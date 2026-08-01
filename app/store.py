from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .contracts import now
from .errors import DomainError


class ProjectStore:
    """SQLite state and immutable run artifacts, always rooted inside project/."""

    def __init__(self, root: Path):
        self.root = root
        self.data_dir = root / "data"
        self.run_dir = self.data_dir / "runs"
        self.source_dir = self.data_dir / "source-cache"
        self.db_path = self.data_dir / "persona_restorer.sqlite"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.source_dir.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        # Streaming status reads run alongside background writes. Let SQLite wait for
        # the short write transaction instead of failing the agent stream on a lock.
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                PRAGMA foreign_keys = ON;
                CREATE TABLE IF NOT EXISTS runs (
                  id TEXT PRIMARY KEY, session_key TEXT NOT NULL, question TEXT NOT NULL,
                  target_population TEXT NOT NULL, status TEXT NOT NULL, variables_json TEXT,
                  estimand_json TEXT, result_json TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sources (
                  id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id), payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS constraints (
                  id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id), payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(id),
                  event_type TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS inbound_dedupe (
                  event_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id)
                );
                """
            )

    def create_run(self, run: dict[str, Any], client_event_id: str | None = None) -> dict[str, Any]:
        with self._connect() as connection:
            if client_event_id:
                existing = connection.execute(
                    "SELECT run_id FROM inbound_dedupe WHERE event_id = ?", (client_event_id,)
                ).fetchone()
                if existing:
                    return self.get_run(existing["run_id"])
            connection.execute(
                "INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run["id"],
                    run["session_key"],
                    run["question"],
                    run["target_population"],
                    run["status"],
                    None,
                    None,
                    None,
                    run["created_at"],
                    run["updated_at"],
                ),
            )
            if client_event_id:
                connection.execute("INSERT INTO inbound_dedupe VALUES (?, ?)", (client_event_id, run["id"]))
            self._append_event_in_connection(connection, run["id"], "run.accepted", {"question": run["question"]})
        return self.get_run(run["id"])

    def _row_to_run(self, row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        for field in ("variables_json", "estimand_json", "result_json"):
            raw = result.pop(field)
            result[field.removesuffix("_json")] = json.loads(raw) if raw else None
        result["sources"] = self.list_sources(result["id"])
        result["constraints"] = self.list_constraints(result["id"])
        result["events"] = self.list_events(result["id"])
        return result

    def get_run(self, run_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if not row:
            raise DomainError("RUN_NOT_FOUND", "해당 실행을 찾을 수 없습니다.", status=404)
        return self._row_to_run(row)

    def update_run(self, run_id: str, **fields: Any) -> dict[str, Any]:
        allowed = {"status", "variables", "estimand", "result"}
        if not fields.keys() <= allowed:
            raise ValueError("unsupported run update")
        columns: list[str] = []
        values: list[Any] = []
        for key, value in fields.items():
            column = f"{key}_json" if key in {"variables", "estimand", "result"} else key
            columns.append(f"{column} = ?")
            values.append(
                json.dumps(value, ensure_ascii=False) if key in {"variables", "estimand", "result"} else value
            )
        columns.append("updated_at = ?")
        values.append(now())
        values.append(run_id)
        with self._connect() as connection:
            connection.execute(f"UPDATE runs SET {', '.join(columns)} WHERE id = ?", values)
        return self.get_run(run_id)

    def append_event(self, run_id: str, event_type: str, payload: dict[str, Any]) -> None:
        with self._connect() as connection:
            self._append_event_in_connection(connection, run_id, event_type, payload)

    def _append_event_in_connection(
        self, connection: sqlite3.Connection, run_id: str, event_type: str, payload: dict[str, Any]
    ) -> None:
        connection.execute(
            "INSERT INTO events (run_id, event_type, payload_json, created_at) VALUES (?, ?, ?, ?)",
            (run_id, event_type, json.dumps(payload, ensure_ascii=False), now()),
        )

    def list_events(self, run_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT event_type, payload_json, created_at FROM events WHERE run_id = ? ORDER BY id", (run_id,)
            ).fetchall()
        return [
            {"type": row["event_type"], "payload": json.loads(row["payload_json"]), "created_at": row["created_at"]}
            for row in rows
        ]

    def add_source(self, run_id: str, source: dict[str, Any]) -> None:
        self.get_run(run_id)
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO sources VALUES (?, ?, ?)", (source["id"], run_id, json.dumps(source, ensure_ascii=False))
            )
        self.append_event(
            run_id, "source.stored", {"source_id": source["id"], "snapshot_hash": source["snapshot_hash"]}
        )

    def list_sources(self, run_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM sources WHERE run_id = ? ORDER BY id", (run_id,)
            ).fetchall()
        return [json.loads(row["payload_json"]) for row in rows]

    def add_constraint(self, run_id: str, constraint: dict[str, Any]) -> None:
        self.get_run(run_id)
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO constraints VALUES (?, ?, ?)",
                (constraint["id"], run_id, json.dumps(constraint, ensure_ascii=False)),
            )
        self.append_event(run_id, "constraint.candidate", {"constraint_id": constraint["id"]})

    def list_constraints(self, run_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM constraints WHERE run_id = ? ORDER BY id", (run_id,)
            ).fetchall()
        return [json.loads(row["payload_json"]) for row in rows]

    def replace_constraint(self, run_id: str, constraint: dict[str, Any]) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE constraints SET payload_json = ? WHERE id = ? AND run_id = ?",
                (json.dumps(constraint, ensure_ascii=False), constraint["id"], run_id),
            )

    def write_artifact(self, run_id: str, filename: str, content: str) -> str:
        path = self.run_dir / run_id
        path.mkdir(exist_ok=True)
        destination = path / filename
        destination.write_text(content, encoding="utf-8")
        return str(destination.relative_to(self.root))

    def read_artifact(self, run_id: str, filename: str) -> bytes:
        if filename not in {
            "report.md",
            "report.html",
            "run.json",
            "policy_brief.md",
            "panel.jsonl",
            "interviews.jsonl",
            "evidence.json",
        }:
            raise DomainError("ARTIFACT_NOT_FOUND", "허용되지 않은 artifact입니다.", status=404)
        destination = (self.run_dir / run_id / filename).resolve()
        run_root = (self.run_dir / run_id).resolve()
        if run_root not in destination.parents or not destination.is_file():
            raise DomainError("ARTIFACT_NOT_FOUND", "artifact를 찾을 수 없습니다.", status=404)
        return destination.read_bytes()
