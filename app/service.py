from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from .contracts import Constraint, Variable, new_id, now, parse_variables, validate_where
from .errors import DomainError
from .personas import (
    classify_chat_intent,
    converse_with_memory,
    converse_with_memory_stream,
    decide_next_evidence_action,
    extract_constraint_candidates,
    generate_narratives,
    llm_policy_plan,
    narrate_panel_segments,
    sample_personas,
    simulate_policy_interviews,
    synthesize_policy_insights,
)
from .policy_review import (
    _is_unsafe,
    build_policy_plan,
    labeled_attributes,
    policy_brief,
    summarize_panel_interviews,
    weighted_segments,
)
from .reporting import render_html_report
from .sources import (
    fetch_data_go_api,
    fetch_kosis_statistics,
    fetch_source,
    search_kosis_tables,
    search_public_web,
    source_excerpt,
)
from .statistics import estimate
from .store import ProjectStore


class ResearchAgent:
    """Host-owned workflow. The language model never gets direct DB, solver or network access."""

    def __init__(self, root: Path):
        self.root = root
        self.store = ProjectStore(root)
        # Session-scoped conversational memory: survives across turns of one web session,
        # intentionally in-process only (not part of the auditable run provenance).
        self.chat_memory: dict[str, list[dict[str, str]]] = {}
        self.session_last_run: dict[str, str] = {}
        # ponytail: 세션 dict 전체를 하나의 락으로 보호 — 세션별 락은 경합이 실측되면.
        self._chat_lock = threading.Lock()

    def _chat_path(self, session_id: str) -> Path:
        safe = re.sub(r"[^0-9A-Za-z_-]", "_", session_id)[:80]
        return self.store.data_dir / "chats" / f"{safe}.json"

    def _load_chat(self, session_id: str) -> None:
        if not session_id or session_id in self.chat_memory:
            return
        path = self._chat_path(session_id)
        if not path.is_file():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        self.chat_memory[session_id] = list(data.get("turns") or [])[-24:]
        if data.get("last_run_id"):
            self.session_last_run.setdefault(session_id, str(data["last_run_id"]))

    def _persist_chat(self, session_id: str) -> None:
        path = self._chat_path(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "session_id": session_id,
            "updated_at": now(),
            "turns": self.chat_memory.get(session_id, []),
            "last_run_id": self.session_last_run.get(session_id),
        }
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)

    def list_chats(self, limit: int = 8) -> list[dict[str, Any]]:
        directory = self.store.data_dir / "chats"
        if not directory.is_dir():
            return []
        chats: list[dict[str, Any]] = []
        for path in sorted(directory.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True)[:limit]:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            turns = list(data.get("turns") or [])
            title = next((turn.get("text", "") for turn in turns if turn.get("role") == "user"), "대화")
            chats.append(
                {
                    "session_id": str(data.get("session_id") or path.stem),
                    "title": title[:80],
                    "updated_at": data.get("updated_at"),
                    "turns": turns,
                    "last_run_id": data.get("last_run_id"),
                }
            )
        return chats

    def get_chat(self, session_id: str) -> dict[str, Any]:
        self._load_chat(session_id)
        return {
            "session_id": session_id,
            "turns": self.chat_memory.get(session_id, []),
            "last_run_id": self.session_last_run.get(session_id),
        }

    def delete_chat(self, session_id: str) -> None:
        self.chat_memory.pop(session_id, None)
        self.session_last_run.pop(session_id, None)
        path = self._chat_path(session_id)
        if path.is_file():
            path.unlink()

    def classify_intent(self, session_id: str, text: str) -> str:
        if not session_id or os.getenv("PERSONA_RESTORER_DEMO_MODEL", "0") == "1":
            return "policy_review"
        self._load_chat(session_id)
        try:
            return classify_chat_intent(text, bool(self.chat_memory.get(session_id)))
        except DomainError:
            return "policy_review"

    @staticmethod
    def safety_block_reason(text: str) -> str | None:
        """Apply the policy-input safety boundary before selecting a conversational lane."""
        return _is_unsafe(" ".join(text.split()))

    def remember_turn(self, session_id: str, role: str, text: str) -> None:
        if not session_id or not text:
            return
        with self._chat_lock:
            self._load_chat(session_id)
            memory = self.chat_memory.setdefault(session_id, [])
            memory.append({"role": role, "text": text[:2000]})
            del memory[:-24]
            self._persist_chat(session_id)

    def bind_session_run(self, session_id: str, run_id: str) -> None:
        if session_id:
            self.session_last_run[session_id] = run_id

    def _session_run_context(self, session_id: str) -> str:
        run_id = self.session_last_run.get(session_id)
        if not run_id:
            return ""
        try:
            run = self.store.get_run(run_id)
        except DomainError:
            return ""
        result = run.get("result") or {}
        review = result.get("policy_review") or {}
        parts = [
            f"질문: {run.get('question')}",
            f"상태: {run.get('status')} / 근거 수준: {result.get('status')}",
            f"정책안 반응: {json.dumps(review.get('responses') or {}, ensure_ascii=False)}",
        ]
        if review.get("insights"):
            parts.append(f"인사이트: {str(review['insights'])[:1200]}")
        if review.get("panel"):
            parts.append(
                "패널(가상 인물 — 답변에서는 이름으로 지칭): "
                + "; ".join(
                    f"{segment.get('display_name') or segment.get('id')}({segment.get('weight_display')}) "
                    + ",".join(f"{attr.get('variable')}={attr.get('value')}" for attr in segment.get("attributes", []))
                    for segment in review["panel"][:8]
                )
            )
        return "\n".join(parts)

    def converse(self, session_id: str, text: str) -> str:
        if blocked_reason := self.safety_block_reason(text):
            return blocked_reason
        self._load_chat(session_id)
        memory = list(self.chat_memory.get(session_id) or [])[-12:]
        reply = converse_with_memory(text, memory, self._session_run_context(session_id))
        self.remember_turn(session_id, "user", text)
        self.remember_turn(session_id, "agent", reply)
        return reply

    def converse_stream(self, session_id: str, text: str, mode: str = "chat"):
        """Yield the conversational reply token-by-token, persisting the exchange at the end."""
        if blocked_reason := self.safety_block_reason(text):
            yield blocked_reason
            return
        self._load_chat(session_id)
        memory = list(self.chat_memory.get(session_id) or [])[-12:]
        context = self._session_run_context(session_id)
        pieces: list[str] = []
        completed = False
        try:
            for delta in converse_with_memory_stream(text, memory, context, mode=mode):
                pieces.append(delta)
                yield delta
            completed = True
        finally:
            reply = "".join(pieces).strip()
            # 스트림이 중간에 실패한 답변은 오류로 표시되므로 세션 기록에 남기지 않는다.
            if completed and reply:
                self.remember_turn(session_id, "user", text)
                self.remember_turn(session_id, "agent", reply)

    def chat(self, text: str, client_event_id: str | None = None) -> dict[str, Any]:
        question = " ".join(text.split())
        if len(question) < 4:
            raise DomainError("QUESTION_TOO_SHORT", "대상 집단과 알고 싶은 정책/질문을 조금 더 구체적으로 입력하세요.")
        target = self._target_hint(question)
        run = {
            "id": new_id("run"),
            "session_key": f"agent:persona-restorer:webchat:{new_id('session')}",
            "question": question,
            "target_population": target,
            "status": "waiting_for_review",
            "created_at": now(),
            "updated_at": now(),
        }
        stored = self.store.create_run(run, client_event_id)
        self.store.append_event(stored["id"], "tool.completed", {"tool": "agent.intake_question"})
        llm_raw = None
        if os.getenv("PERSONA_RESTORER_DEMO_MODEL", "0") != "1":
            try:
                llm_raw = llm_policy_plan(question)
            except DomainError:
                llm_raw = None
        plan = build_policy_plan(question, target, llm_raw=llm_raw)
        self.store.append_event(
            stored["id"], "policy.plan_designed", {"plan_source": plan.get("plan_source", "keyword_template")}
        )
        self.store.append_event(stored["id"], "policy.plan_created", {"plan": plan})
        if plan["status"] == "SAFETY_BLOCKED":
            self.store.update_run(stored["id"], status="safety_blocked")
            self.store.append_event(stored["id"], "policy.blocked", {"reason": plan["blocked_reason"]})
            message = plan["blocked_reason"]
        else:
            proposed = parse_variables(plan["proposed_variables"])
            labels_by_id = {
                str(item.get("id")): item.get("category_labels") or {} for item in plan["proposed_variables"]
            }
            self.store.update_run(
                stored["id"],
                status="planning",
                variables=[
                    {
                        "id": item.id,
                        "label": item.label,
                        "categories": list(item.categories),
                        "category_labels": labels_by_id.get(item.id, {}),
                    }
                    for item in proposed
                ],
            )
            self.store.append_event(
                stored["id"], "schema.proposed", {"variable_ids": [item.id for item in proposed], "plan_id": plan["id"]}
            )
            self.store.append_event(
                stored["id"],
                "evidence.gate_scheduled",
                {"owner": "policy_review_agent", "reason": "sources_constraints_before_pgm"},
            )
            message = "정책 목표를 해석하고 가정·대안·조사 변수를 계획했습니다. 이제 한국 신뢰 출처를 병렬 탐색합니다."
        return {
            "run": self.store.get_run(stored["id"]),
            "message": message,
        }

    def autonomous_review(self, text: str, client_event_id: str | None = None) -> dict[str, Any]:
        """Execute the user-facing policy workflow from one chat message.

        Missing quantitative evidence is a completed, explicit scenario-only result;
        it never becomes a fabricated public statistic or a silent approval request.
        """
        created = self.chat(text, client_event_id)
        return self.continue_autonomous_review(created["run"]["id"], created["message"])

    def continue_autonomous_review(self, run_id: str, intake_message: str | None = None) -> dict[str, Any]:
        """Continue a run already created by ``chat`` for HTTP streaming clients."""
        initial_run = self.store.get_run(run_id)
        plan = self._latest_policy_plan(initial_run)
        if plan.get("status") == "SAFETY_BLOCKED":
            return {
                "run": initial_run,
                "message": intake_message or plan.get("blocked_reason", "정책 검토를 진행할 수 없습니다."),
                "research": {"results": [], "failed_queries": []},
                "artifacts": {},
            }

        self.store.append_event(run_id, "tool.started", {"tool": "policy.plan_request"})
        self.store.append_event(run_id, "tool.completed", {"tool": "policy.plan_request"})

        self.store.append_event(run_id, "tool.started", {"tool": "web.parallel_korean_policy_research"})
        try:
            research = self.research_policy_sources(run_id)
            self.store.append_event(
                run_id,
                "tool.completed",
                {"tool": "web.parallel_korean_policy_research", "count": len(research["results"])},
            )
        except DomainError as error:
            research = {
                "plan": plan,
                "results": [],
                "failed_queries": [{"query": "policy research", "code": error.code}],
                "warning": "검색 공급자 실패를 기록하고 근거 공백 상태로 계속 진행했습니다.",
            }
            self.store.append_event(
                run_id,
                "tool.failed",
                {"tool": "web.parallel_korean_policy_research", "code": error.code},
            )

        stored_sources, excluded = self._autonomous_source_snapshots(run_id, research["results"])
        stored_sources = stored_sources + self._autonomous_kosis_evidence(run_id, plan)
        llm_ready = all(os.getenv(name) for name in ("LLM_API_URL", "LLM_API_KEY", "LLM_MODEL"))
        self._autonomous_constraint_extraction(run_id, stored_sources, llm_ready)
        approved_ids = self._autonomous_evidence_gate(run_id)
        # 부분 근거(일부 변수만 제약)도 루프 대상이다 — 미커버 변수가 남아 있으면 추가 수집을 시도한다(#35).
        if llm_ready and self._uncovered_variables(run_id):
            approved_ids = self._evidence_recovery_loop(run_id, plan) or approved_ids

        self.store.append_event(run_id, "tool.started", {"tool": "statistics.identification_bounds"})
        if approved_ids:
            computed, evidence_mode = self._compute_with_conflict_fallback(run_id, approved_ids)
        else:
            computed = self._scenario_only_model(run_id)
            evidence_mode = "scenario_only"
        self.store.append_event(
            run_id,
            "tool.completed",
            {"tool": "statistics.identification_bounds", "evidence_mode": evidence_mode},
        )

        result = computed["result"]
        result["research"] = {
            "candidates": research["results"],
            "failed_queries": research["failed_queries"],
            "stored_source_ids": [item["id"] for item in stored_sources],
            "excluded_sources": excluded,
        }
        self.store.update_run(run_id, result=result)

        adult_scope = self._adult_scope(self.store.get_run(run_id))
        if adult_scope:
            self.store.append_event(run_id, "tool.started", {"tool": "personas.sample_joint_distribution"})
            sampled = self.create_personas(
                run_id,
                {"adult_population_confirmed": True, "count": 24, "seed": 20260801},
            )
            self.store.append_event(
                run_id,
                "tool.completed",
                {"tool": "personas.sample_joint_distribution", "count": len(sampled["result"]["personas"]["items"])},
            )
            if llm_ready:
                self.store.append_event(run_id, "tool.started", {"tool": "llm.narrate_personas"})
                try:
                    self.narratives(run_id)
                    self.store.append_event(run_id, "tool.completed", {"tool": "llm.narrate_personas"})
                except DomainError as error:
                    self.store.append_event(run_id, "tool.failed", {"tool": "llm.narrate_personas", "code": error.code})

        self.store.append_event(run_id, "tool.started", {"tool": "policy.weighted_panel_interviews"})
        if plan.get("request_type") == "audience_understanding":
            self._policy_review_without_llm(
                run_id,
                review_status="COMPLETED_AUDIENCE_PANEL",
                warning="대상 이해 요청으로 판정되어 모의 인터뷰 없이 합성 패널·근거·현장조사 질문만 생성했습니다.",
            )
            self.store.append_event(
                run_id,
                "tool.completed",
                {"tool": "policy.weighted_panel_interviews", "outcome": "audience_panel_only"},
            )
            return self._finalize_autonomous_review(run_id, research)
        try:
            self.policy_panel_review(run_id)
            panel_outcome = "simulated_interviews"
        except DomainError as error:
            if error.code != "LLM_NOT_CONFIGURED":
                self.store.append_event(
                    run_id, "tool.failed", {"tool": "policy.weighted_panel_interviews", "code": error.code}
                )
            failure_detail = error.code + (
                f" · {error.details['reason']}" if error.details.get("reason") else ""
            )
            self._policy_review_without_llm(
                run_id,
                warning=(
                    "LLM 모의 인터뷰 호출이 완료되지 않아 응답률을 만들지 않았습니다. "
                    f"합성 프로필과 통계·시나리오 상태만 표시합니다. (사유: {failure_detail})"
                    if error.code != "LLM_NOT_CONFIGURED"
                    else None
                ),
            )
            panel_outcome = (
                "profiles_only_no_llm" if error.code == "LLM_NOT_CONFIGURED" else "profiles_only_llm_failure"
            )
        except Exception as error:  # 패널 단계의 예기치 못한 실패가 전체 실행을 죽이면 안 된다.
            self.store.append_event(
                run_id, "tool.failed", {"tool": "policy.weighted_panel_interviews", "code": type(error).__name__}
            )
            self._policy_review_without_llm(
                run_id,
                warning=(
                    "모의 인터뷰 처리 중 내부 오류가 발생해 응답률을 만들지 않았습니다. "
                    f"합성 프로필과 통계·시나리오 상태만 표시합니다. (사유: {type(error).__name__})"
                ),
            )
            panel_outcome = "profiles_only_internal_error"
        self.store.append_event(
            run_id,
            "tool.completed",
            {"tool": "policy.weighted_panel_interviews", "outcome": panel_outcome},
        )
        return self._finalize_autonomous_review(run_id, research)

    def _finalize_autonomous_review(self, run_id: str, research: dict[str, Any]) -> dict[str, Any]:
        self.store.append_event(run_id, "tool.started", {"tool": "report.write_provenance"})
        report = self.report(run_id)
        self.store.append_event(run_id, "tool.completed", {"tool": "report.write_provenance"})
        final_run = self.store.get_run(run_id)
        final_result = final_run.get("result") or {}
        scenario_only = final_result.get("status") == "scenario_only"
        if (final_result.get("policy_review") or {}).get("status") == "COMPLETED_AUDIENCE_PANEL":
            message = "대상 이해 요청으로 합성 페르소나 패널과 근거·현장조사 질문을 생성했습니다." + (
                " 검증된 정량 제약이 없어 가중치는 균등 시나리오이며 모집단 추정이 아닙니다."
                if scenario_only
                else ""
            )
        elif scenario_only:
            message = "정책 검토를 끝까지 완료했습니다. 검증된 정량 제약이 없어 PGM은 모집단 추정이 아닌 균등 시나리오로 표시했고, 권리·법률 검토와 필요한 실제 조사 계획을 보고서에 남겼습니다."
        else:
            message = "공개 근거 스냅샷과 승인 가능한 정량 제약으로 PGM·합성 패널·정책 보고서까지 완료했습니다."
        return {
            "run": final_run,
            "message": message,
            "research": research,
            "artifacts": {"html_report": report["report_url"], **report["downloads"]},
        }

    def _compute_with_conflict_fallback(self, run_id: str, approved_ids: list[str]) -> tuple[dict[str, Any], str]:
        """자동 승인 제약이 서로 모순이면 최소 충돌 집합만 강등하고 재계산한다.

        같은 변수를 서로 다른 분할로 공표한 두 표가 동시에 승인되면 합이 1을
        넘어 infeasible이 된다. 사람 검토가 없는 자율 실행에서는 충돌 core를
        'conflicted'로 강등해 나머지 근거로 계산하고, 그래도 안 되면 시나리오로
        정직하게 내려간다. 강등 내역은 이벤트로 남는다.
        """
        computed = self.compute(run_id, self._default_estimand(self.store.get_run(run_id)))
        if computed["result"]["status"] != "infeasible":
            return computed, "approved_public_constraints"
        core = sorted(set(computed["result"].get("conflict_core") or []))
        # 충돌 core에서 근거가 가장 좋은 제약 하나만 남기고 강등:
        # ① population_compatibility == "exact" 우선(broader보다) ② raw_statement의 PRD_DE 연도 최신 우선
        # ③ 동률이면 id 사전순. (연도 파싱은 _autonomous_evidence_gate의 latest_period와 동일 로직)
        by_id = {item["id"]: item for item in self.store.list_constraints(run_id)}

        def keep_rank(constraint_id: str) -> tuple[bool, int, str]:
            item = by_id.get(constraint_id, {})
            match = re.search(r"PRD_DE\D{0,4}(\d{4})", str(item.get("raw_statement", "")))
            year = int(match.group(1)) if match else 0
            return (item.get("population_compatibility") != "exact", -year, constraint_id)

        keep = min(core, key=keep_rank) if len(core) > 1 else None
        drop = {identifier for identifier in core if identifier != keep}
        self.store.append_event(
            run_id, "statistics.conflict_demoted", {"dropped_constraint_ids": sorted(drop), "conflict_core": core}
        )
        for item in self.store.list_constraints(run_id):
            if item["id"] in drop:
                item["review_status"] = "conflicted"
                item["override_note"] = ((item.get("override_note") or "") + " [자동] 상호 모순 최소 집합으로 강등됨").strip()
                self.store.replace_constraint(run_id, item)
        remaining = [identifier for identifier in approved_ids if identifier not in drop]
        if remaining:
            computed = self.compute(run_id, self._default_estimand(self.store.get_run(run_id)))
            if computed["result"]["status"] != "infeasible":
                return computed, "approved_public_constraints_after_conflict"
        return self._scenario_only_model(run_id), "scenario_only_after_conflict"

    def _uncovered_variables(self, run_id: str) -> list[str]:
        """승인 제약이 하나도 조건으로 삼지 않은 변수 id 목록."""
        run = self.store.get_run(run_id)
        covered = {
            key
            for item in run["constraints"]
            if item.get("review_status") == "approved"
            for key in (item.get("where") or {})
        }
        return [variable["id"] for variable in run["variables"] if variable["id"] not in covered]

    def _evidence_recovery_loop(self, run_id: str, plan: dict[str, Any]) -> list[str]:
        """Observe the empty evidence gate and let the decision tool pick the next action.

        Hard budgets live in code: at most two extra rounds, three new queries per
        round, and no query is ever repeated. The model only chooses WHAT to look at.

        계약(#32): 'stop' 결정은 근거 **수집**의 중단이다 — 이 루프를 벗어난 뒤에는
        수집 계열 도구(검색·스냅샷·KOSIS·추출·게이트)를 다시 호출하지 않는다.
        파이프라인 자체는 scenario_only로 정직하게 완주해 패널·보고서를 산출한다.
        """
        tried_web = [str(query) for query in plan.get("evidence_queries") or []]
        tried_kosis = [str(term) for term in plan.get("kosis_search_terms") or []]
        approved: list[str] = []
        run = self.store.get_run(run_id)
        variable_ids = [variable["id"] for variable in run["variables"]]
        for round_number in (1, 2):
            constraints = self.store.list_constraints(run_id)
            uncovered = self._uncovered_variables(run_id)
            if not uncovered:
                break
            observation = {
                "round": round_number,
                "rounds_left": 2 - round_number,
                "approved_count": sum(1 for item in constraints if item.get("review_status") == "approved"),
                "covered_variables": [identifier for identifier in variable_ids if identifier not in uncovered],
                "uncovered_variables": uncovered,
                "candidate_count": len(constraints),
                "broader_candidates": sum(
                    1
                    for item in constraints
                    if item.get("population_compatibility") == "broader"
                    and item.get("review_status") == "candidate"
                    and str(item.get("raw_statement", "")).strip()
                ),
                "tried_kosis_queries": tried_kosis,
                "tried_web_queries": tried_web,
                "kosis_available": bool(os.getenv("KOSIS_API_KEY")),
                "policy_focus": plan.get("policy_focus"),
                "target_population": plan.get("target_population"),
            }
            self.store.append_event(run_id, "agent.evidence_round", {"round": round_number, "observation": observation})
            decision = decide_next_evidence_action(observation)
            self.store.append_event(run_id, "agent.decision", {"round": round_number, **decision})
            action = decision["action"]
            if action == "stop":
                break
            if action == "approve_broader":
                approved = self._autonomous_evidence_gate(run_id, allow_broader=True)
                if not self._uncovered_variables(run_id):
                    break
                continue
            fresh = [query for query in decision["queries"] if query not in tried_web and query not in tried_kosis][:3]
            if action == "kosis":
                if not observation["kosis_available"]:
                    continue
                if fresh:
                    tried_kosis += fresh
                    new_sources = self._autonomous_kosis_evidence(run_id, plan, queries_override=fresh)
                elif not tried_kosis:
                    # KOSIS 자체가 미시도면 기본(플랜·포커스) 검색어로 1회 시도하고 재사용을 막는다.
                    tried_kosis.append("(기본 검색어)")
                    new_sources = self._autonomous_kosis_evidence(run_id, plan)
                else:
                    # 새 검색어 없이 기시도 검색어를 반복하는 낭비 라운드는 건너뛴다.
                    continue
            else:  # search
                if not fresh:
                    break
                tried_web += fresh
                candidates: list[dict[str, Any]] = []
                for query in fresh:
                    try:
                        candidates.extend(search_public_web(query, True))
                    except DomainError:
                        continue
                new_sources, _ = self._autonomous_source_snapshots(run_id, candidates)
            if new_sources:
                self._autonomous_constraint_extraction(run_id, new_sources, True)
            approved = self._autonomous_evidence_gate(run_id)
            if not self._uncovered_variables(run_id):
                break
        return approved

    def _autonomous_kosis_evidence(
        self, run_id: str, plan: dict[str, Any], queries_override: list[str] | None = None
    ) -> list[dict[str, Any]]:
        """Pull real published proportions straight from KOSIS OpenAPI for the plan variables."""
        if not os.getenv("KOSIS_API_KEY"):
            return []

        # KOSIS 통합검색은 짧은 주제 키워드에만 반응한다 — 플랜 모델이 설계한 검색어를 우선 사용한다.
        stopwords = {
            "수원", "수원시", "서울", "부산", "대구", "인천", "광주", "대전", "울산", "세종", "경기",
            "전국", "정책", "검토", "도입", "확대", "프로그램", "여부", "구간", "형태",
        }
        queries = [str(term).strip() for term in queries_override or plan.get("kosis_search_terms") or [] if str(term).strip()][:4]
        if not queries:
            tokens = [
                token
                for token in re.split(r"[^0-9A-Za-z가-힣]+", str(plan.get("policy_focus") or ""))
                if len(token) >= 2 and token not in stopwords
            ]
            queries = list(dict.fromkeys(tokens))[:3]
        self.store.append_event(run_id, "tool.started", {"tool": "kosis.statistics_openapi", "queries": queries})
        stored: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        def rate_table(table: dict[str, str]) -> bool:
            return any(token in table["table_name"] for token in ("율", "률", "비율", "비중", "분포"))

        collected: list[dict[str, str]] = []
        for query in queries:
            try:
                collected.extend(search_kosis_tables(query, 5))
            except DomainError:
                continue
        # 플랜 변수를 실제로 공표하는 표가 3개 캡 안에 들어오도록, 표 제목과
        # 변수 label·정책 초점의 토큰 겹침을 1순위로, 비율 표 여부를 2순위로 정렬한다.
        plan_tokens = {
            token
            for text in [str(plan.get("policy_focus") or "")]
            + [str(item.get("label") or "") for item in plan.get("proposed_variables") or []]
            for token in re.split(r"[^0-9A-Za-z가-힣]+", text)
            if len(token) >= 2 and token not in stopwords
        }

        def relevance(table: dict[str, str]) -> int:
            return sum(1 for token in plan_tokens if token in table["table_name"])

        for table in sorted(collected, key=lambda item: (-relevance(item), not rate_table(item))):
            key = (table["org_id"], table["table_id"])
            if key in seen or len(stored) >= 3:
                continue
            seen.add(key)
            payload = {
                "org_id": table["org_id"],
                "table_id": table["table_id"],
                "item_id": "ALL",
                "classification_1": "ALL",
                "period_type": "Y",
                "newest_period_count": "2",
                "title": table["table_name"],
                "survey_name": table["survey_name"],
                "population": table["path"] or "통계표 원문에서 검토 필요",
            }
            source = None
            for extra in (
                {},
                {"classification_2": "ALL"},
                {"classification_2": "ALL", "classification_3": "ALL"},
                {"classification_2": "ALL", "classification_3": "ALL", "classification_4": "ALL"},
            ):
                try:
                    candidate = fetch_kosis_statistics(self.root, {**payload, **extra})
                except DomainError:
                    continue
                head = source_excerpt(self.root, candidate.as_dict(), 4000)
                # excerpt는 압축 형식('항목 = 값 %')이다 — raw JSON 마커가 아니라 단위 기호로 거른다.
                if "%" not in head:
                    continue
                source = candidate
                break
            if source is None:
                continue
            self.store.add_source(run_id, source.as_dict())
            stored.append(source.as_dict())
        self.store.append_event(
            run_id, "tool.completed", {"tool": "kosis.statistics_openapi", "stored": len(stored)}
        )
        return stored

    @staticmethod
    def _looks_like_content_url(url: str) -> bool:
        """Navigation, login and portal home pages never carry the published numbers."""
        lowered = url.lower()
        if any(token in lowered for token in ("login", "signin", "sso.", "/member", "/join", "/search?")):
            return False
        # KOSIS 포털은 JS 렌더링이라 웹 스냅샷에 수치가 없다 — KOSIS 수치는 OpenAPI 경로가 담당한다.
        if "kosis.kr" in lowered:
            return False
        from urllib.parse import urlparse

        parsed = urlparse(lowered)
        path = parsed.path.strip("/")
        if not path and not parsed.query:
            return False
        if path in {"index.do", "index", "main.do", "main", "eng", "kor"} and not parsed.query:
            return False
        return True

    @staticmethod
    def _adult_scope(run: dict[str, Any]) -> bool:
        text = f"{run.get('question', '')} {run.get('target_population', '')}"
        return any(term in text for term in ("성인", "20대", "청년", "대학", "투표권", "선거권"))

    @staticmethod
    def _default_estimand(run: dict[str, Any]) -> dict[str, Any]:
        variable = Variable.parse(run["variables"][0])
        return {
            "estimand": {"numerator": {variable.id: variable.categories[0]}},
            "dag_candidates": [],
            "selected_model": "maximum_entropy",
        }

    def _autonomous_source_snapshots(
        self, run_id: str, candidates: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
        trusted = [
            item
            for item in candidates
            if item.get("trust_tier") in {"korean_official", "korean_research"}
            and item.get("url")
            and self._looks_like_content_url(str(item["url"]))
        ][:5]
        self.store.append_event(run_id, "tool.started", {"tool": "source.fetch_snapshot", "count": len(trusted)})
        run = self.store.get_run(run_id)
        stored: list[dict[str, Any]] = []
        excluded: list[dict[str, str]] = []

        def download(candidate: dict[str, Any]) -> Any:
            return fetch_source(
                self.root,
                str(candidate["url"]),
                {
                    "title": candidate.get("title"),
                    "organization": candidate.get("domain") or "기관 확인 필요",
                    "survey_name": candidate.get("title") or "웹 원문",
                    "published_at": "원문 메타데이터 확인 필요",
                    "observed_period": "원문 메타데이터 확인 필요",
                    "population": run["target_population"],
                },
            )

        with ThreadPoolExecutor(max_workers=max(1, len(trusted))) as executor:
            futures = {executor.submit(download, candidate): candidate for candidate in trusted}
            for future in as_completed(futures):
                candidate = futures[future]
                try:
                    source = future.result().as_dict()
                    self.store.add_source(run_id, source)
                    stored.append(source)
                except DomainError as error:
                    excluded.append({"url": str(candidate["url"]), "code": error.code})
        self.store.append_event(
            run_id,
            "tool.completed",
            {"tool": "source.fetch_snapshot", "stored": len(stored), "excluded": len(excluded)},
        )
        return stored, excluded

    def _autonomous_constraint_extraction(self, run_id: str, sources: list[dict[str, Any]], llm_ready: bool) -> None:
        self.store.append_event(run_id, "tool.started", {"tool": "llm.extract_constraint_candidates"})
        if not llm_ready:
            self.store.append_event(
                run_id,
                "tool.completed",
                {"tool": "llm.extract_constraint_candidates", "outcome": "skipped_no_configured_model"},
            )
            return
        accepted = 0
        rejected = 0
        with ThreadPoolExecutor(max_workers=min(3, max(1, len(sources)))) as executor:
            futures = {executor.submit(self.extract_candidates, run_id, source["id"]): source for source in sources}
            for future in as_completed(futures):
                source = futures[future]
                try:
                    result = future.result()
                    accepted += len(result["candidates"])
                    rejected += int(result.get("rejected_count", 0))
                except DomainError as error:
                    self.store.append_event(
                        run_id,
                        "constraint.extraction_skipped",
                        {"source_id": source["id"], "code": error.code},
                    )
        self.store.append_event(
            run_id,
            "tool.completed",
            {
                "tool": "llm.extract_constraint_candidates",
                "accepted_candidates": accepted,
                "rejected_candidates": rejected,
                "outcome": "no_candidates_from_model" if accepted == 0 and rejected == 0 else "completed",
            },
        )

    def _autonomous_evidence_gate(self, run_id: str, allow_broader: bool = False) -> list[str]:
        allowed_compat = {"exact", "broader"} if allow_broader else {"exact"}
        self.store.append_event(run_id, "tool.started", {"tool": "review.auto_approve_exact_constraints"})
        run = self.store.get_run(run_id)
        trusted_sources = {
            item["id"] for item in run["sources"] if item.get("trust_tier") in {"korean_official", "korean_research"}
        }
        eligible = [
            item
            for item in run["constraints"]
            if item.get("source_id") in trusted_sources
            and item.get("population_compatibility") in allowed_compat
            and str(item.get("raw_statement", "")).strip()
            # 0%·100% 셀(eq value가 0.005 미만 또는 0.995 초과)은 분포 정보가 없어 승인해도 근거가 되지 않으므로 제외한다.
            and not (item.get("relation") == "eq" and not 0.005 <= float(item.get("value") or 0.0) <= 0.995)
        ]

        def latest_period(item: dict[str, Any]) -> int:
            match = re.search(r"PRD_DE\D{0,4}(\d{4})", str(item.get("raw_statement", "")))
            return int(match.group(1)) if match else 0

        # 서로 다른 범주·교차표의 eq 제약은 모순이 아니므로 모두 승인 대상이다.
        # 완전히 같은 셀(where 일치)의 중복만 걸러내고, exact를 broader보다 우선하며 최신 연도를 고른다.
        selected_by_cell: dict[tuple, dict[str, Any]] = {}
        for item in sorted(
            eligible, key=lambda entry: (entry.get("population_compatibility") != "exact", -latest_period(entry))
        ):
            where = item.get("where") or {}
            if not where:
                continue
            cell = tuple(sorted((str(key), str(value)) for key, value in where.items()))
            selected_by_cell.setdefault(cell, item)
        chosen = list(selected_by_cell.values())
        selected = [item["id"] for item in chosen]
        if selected:
            broader_note = "전국(광의) 모집단 통계를 대상 집단의 근사로 사용한다는 명시적 가정 아래 자동 승인됨"
            self.approve_constraints(
                run_id,
                {
                    "constraint_ids": selected,
                    "override_notes": {
                        item["id"]: broader_note
                        for item in chosen
                        if item.get("population_compatibility") == "broader"
                    },
                },
            )
        self.store.append_event(
            run_id,
            "tool.completed",
            {
                "tool": "review.auto_approve_exact_constraints",
                "approved": len(selected),
                "gate": "exact_plus_broader_assumption" if allow_broader else "exact_population_only",
            },
        )
        return selected

    def _scenario_only_model(self, run_id: str) -> dict[str, Any]:
        run = self.store.get_run(run_id)
        variables = [Variable.parse(item) for item in run["variables"]]
        estimand = self._default_estimand(run)["estimand"]
        result = estimate(variables, [], estimand["numerator"], None, [], "maximum_entropy")
        result["solver_status"] = result["status"]
        result["status"] = "scenario_only"
        result["selected_model"] = "uninformed_maximum_entropy_scenario"
        result["estimand"] = estimand
        result["assumption"] = (
            "승인된 정량 제약이 없어 모든 결합 셀을 동일하게 둔 설계 시나리오입니다. 모집단 점추정이 아닙니다."
        )
        result["evidence_gap"] = (
            "모집단·시점·분모·범주가 일치하는 공개 교차표를 자동 검증하지 못해 식별구간은 [0,1]입니다."
        )
        stop_decision = next(
            (
                event.get("payload", {})
                for event in reversed(run.get("events", []))
                if event.get("type") == "agent.decision" and event.get("payload", {}).get("action") == "stop"
            ),
            None,
        )
        if stop_decision:
            # stop run도 누락 이유를 보존한다(#32) — 보고서·매니페스트에서 그대로 추적 가능.
            result["evidence_gap"] += f" 에이전트 수집 중단 사유: {stop_decision.get('reason', '')}"
        self.store.update_run(run_id, result=result, estimand=estimand, status="running")
        self.store.append_event(run_id, "statistics.scenario_only", {"reason": "no_approved_constraints"})
        self._persist_manifest(run_id)
        return self.store.get_run(run_id)

    def _policy_review_without_llm(
        self, run_id: str, warning: str | None = None, review_status: str = "COMPLETED_WITHOUT_LLM_INTERVIEWS"
    ) -> dict[str, Any]:
        run = self.store.get_run(run_id)
        plan = self._latest_policy_plan(run)
        result = run["result"] or {}
        evidence_level = "scenario_only" if result.get("status") == "scenario_only" else "partial_estimate"
        variable_labels = {item["id"]: item.get("label") or item["id"] for item in run["variables"]}
        category_labels = {item["id"]: item.get("category_labels") or {} for item in run["variables"]}
        panel = weighted_segments(
            result["states"],
            result["distribution"],
            limit=12,
            evidence_level=evidence_level,
            variable_labels=variable_labels,
            category_labels=category_labels,
        )
        review = {
            "status": review_status,
            "plan_id": plan["id"],
            "alternatives": plan["alternatives"],
            "panel": panel,
            "panel_coverage": sum(item["weight"] for item in panel),
            "interviews": [],
            "responses": {},
            "fieldwork_questions": plan.get("interview_questions", []),
            "brief": policy_brief(plan, panel, []),
            "warning": warning
            or "LLM이 설정되지 않아 모의 인터뷰와 반응률을 만들지 않았습니다. 프로필은 통계 또는 시나리오 상태를 보여주는 완전 합성 카드입니다.",
        }
        result["policy_review"] = review
        self.store.update_run(run_id, result=result, status="reviewed")
        self.store.append_event(run_id, "policy.panel_profiles_completed", {"segment_count": len(panel)})
        self._persist_manifest(run_id)
        return self.store.get_run(run_id)

    @staticmethod
    def _target_hint(question: str) -> str:
        parts = []
        for region in ("수원", "대전", "서울", "부산", "대구", "인천", "광주", "울산", "세종"):
            if region in question:
                parts.append(f"대한민국 {region}")
                break
        if "대학생" in question or "대학" in question:
            parts.append("대학 재학생")
        if "1인 가구" in question or "1인가구" in question:
            parts.append("1인 가구")
        return ", ".join(parts) if parts else "사용자 질의에서 추출할 대상 집단 — 검토 필요"

    def set_variables(self, run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        run = self.store.get_run(run_id)
        if run["result"]:
            raise DomainError(
                "RUN_IMMUTABLE", "계산 결과가 있는 run의 변수 스키마는 바꿀 수 없습니다. 새 run을 만드세요."
            )
        variables = parse_variables(payload.get("variables", []))
        self.store.update_run(
            run_id,
            variables=[{"id": item.id, "label": item.label, "categories": list(item.categories)} for item in variables],
        )
        self.store.append_event(run_id, "schema.accepted", {"variable_ids": [item.id for item in variables]})
        return self.store.get_run(run_id)

    @staticmethod
    def _latest_policy_plan(run: dict[str, Any]) -> dict[str, Any]:
        for event in reversed(run.get("events", [])):
            if event.get("type") == "policy.plan_created":
                plan = event.get("payload", {}).get("plan")
                if isinstance(plan, dict):
                    return plan
        raise DomainError("POLICY_PLAN_REQUIRED", "먼저 정책 질문으로 실행을 시작해야 합니다.")

    def policy_plan(self, run_id: str) -> dict[str, Any]:
        run = self.store.get_run(run_id)
        plan = self._latest_policy_plan(run)
        return {"plan": plan, "run": run}

    def research_policy_sources(self, run_id: str) -> dict[str, Any]:
        """Run independent, read-only Korean evidence searches concurrently.

        Candidate URLs remain untrusted until the source snapshot and constraint review
        stages. A failed query only makes the research plan partial.
        """
        run = self.store.get_run(run_id)
        plan = self._latest_policy_plan(run)
        if plan.get("status") == "SAFETY_BLOCKED":
            raise DomainError("SAFETY_BLOCKED", str(plan.get("blocked_reason")))
        queries = list(plan.get("evidence_queries", []))[:4]
        candidates: list[dict[str, Any]] = []
        failures: list[dict[str, str]] = []
        with ThreadPoolExecutor(max_workers=max(1, len(queries))) as executor:
            futures = {executor.submit(search_public_web, query, True): query for query in queries}
            for future in as_completed(futures):
                query = futures[future]
                try:
                    for candidate in future.result():
                        candidates.append({**candidate, "query": query})
                except DomainError as error:
                    failures.append({"query": query, "code": error.code})
        unique: list[dict[str, Any]] = []
        seen: set[str] = set()
        for candidate in candidates:
            url = str(candidate.get("url", ""))
            if url and url not in seen:
                seen.add(url)
                unique.append(candidate)
        self.store.append_event(
            run_id,
            "policy.evidence_candidates",
            {
                "plan_id": plan["id"],
                "query_count": len(queries),
                "candidate_count": len(unique),
                "failed_queries": failures,
            },
        )
        self.store.update_run(run_id, status="researching")
        return {
            "plan": plan,
            "results": unique,
            "failed_queries": failures,
            "warning": "후보는 신뢰 도메인 우선 검색 결과일 뿐입니다. 원문 스냅샷·모집단·시점·분모를 검토하고 승인한 제약만 PGM에 사용합니다.",
        }

    def fetch_kosis(self, run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.store.get_run(run_id)
        source = fetch_kosis_statistics(self.root, payload)
        self.store.add_source(run_id, source.as_dict())
        return source.as_dict()

    def fetch_data_go(self, run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.store.get_run(run_id)
        source = fetch_data_go_api(self.root, payload)
        self.store.add_source(run_id, source.as_dict())
        return source.as_dict()

    def extract_candidates(self, run_id: str, source_id: str) -> dict[str, Any]:
        run = self.store.get_run(run_id)
        if not run["variables"]:
            raise DomainError("VARIABLES_REQUIRED", "추출 전에 변수 스키마를 정해야 합니다.")
        source = next((item for item in run["sources"] if item["id"] == source_id), None)
        if not source:
            raise DomainError("MISSING_SOURCE", "이 run에 저장된 source_id만 추출할 수 있습니다.")
        candidates = extract_constraint_candidates(source, run["variables"], source_excerpt(self.root, source))
        accepted: list[dict[str, Any]] = []
        rejected: list[dict[str, str]] = []
        variables = [Variable.parse(item) for item in run["variables"]]
        for candidate in candidates:
            if not isinstance(candidate, dict):
                rejected.append({"label": str(candidate)[:60], "code": "NOT_AN_OBJECT"})
                continue
            candidate["source_id"] = source_id
            candidate["review_status"] = "candidate"
            try:
                constraint = Constraint.parse(candidate, variables)
            except DomainError as error:
                rejected.append(
                    {
                        "label": str(candidate.get("label", ""))[:60],
                        "code": error.code,
                        "where": json.dumps(candidate.get("where"), ensure_ascii=False)[:120],
                    }
                )
                continue
            self.store.add_constraint(run_id, constraint.as_dict())
            accepted.append(constraint.as_dict())
        if rejected:
            # 어디서 죽는지 트레이스에 남긴다 — 후보 0건의 원인 조사가 이 이벤트 하나로 끝나야 한다.
            self.store.append_event(
                run_id, "constraint.candidate_rejected", {"source_id": source_id, "rejected": rejected[:10]}
            )
        self.store.append_event(
            run_id,
            "constraint.extraction_completed",
            {
                "source_id": source_id,
                "accepted_candidate_count": len(accepted),
                "rejected_candidate_count": len(rejected),
                "model_candidate_count": len(candidates),
            },
        )
        return {
            "candidates": accepted,
            "rejected_count": len(rejected),
            "warning": "모델이 제안한 후보입니다. 수치·범주 매핑·모집단을 사람이 검토하고 승인해야만 계산에 사용됩니다.",
        }

    def add_constraint(self, run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        run = self.store.get_run(run_id)
        if not run["variables"]:
            raise DomainError("VARIABLES_REQUIRED", "제약을 넣기 전에 변수 스키마를 정해야 합니다.")
        source_ids = {item["id"] for item in run["sources"]}
        if payload.get("source_id") not in source_ids:
            raise DomainError("MISSING_SOURCE", "이 run에 저장된 source_id만 제약에 사용할 수 있습니다.")
        constraint = Constraint.parse(payload, [Variable.parse(item) for item in run["variables"]])
        self.store.add_constraint(run_id, constraint.as_dict())
        return constraint.as_dict()

    def approve_constraints(self, run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        selected = set(payload.get("constraint_ids", []))
        override_notes = payload.get("override_notes", {})
        if not selected:
            raise DomainError("NO_CONSTRAINTS_SELECTED", "승인할 제약을 하나 이상 선택하세요.")
        constraints = self.store.list_constraints(run_id)
        available = {item["id"] for item in constraints}
        if not selected <= available:
            raise DomainError("UNKNOWN_CONSTRAINT", "승인 대상에 없는 constraint_id가 있습니다.")
        for item in constraints:
            if item["id"] not in selected:
                continue
            compatibility = item["population_compatibility"]
            note = override_notes.get(item["id"])
            if compatibility in {"overlap_unknown", "incompatible"} and not note:
                raise DomainError(
                    "POPULATION_REVIEW_REQUIRED",
                    "overlap_unknown/incompatible 제약은 명시적 override 사유가 필요합니다.",
                    details={"constraint_id": item["id"]},
                )
            item["review_status"] = "approved"
            item["reviewed_at"] = now()
            item["override_note"] = note
            self.store.replace_constraint(run_id, item)
        self.store.update_run(run_id, status="running")
        self.store.append_event(run_id, "constraints.approved", {"constraint_ids": sorted(selected)})
        return self.store.get_run(run_id)

    def compute(self, run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        run = self.store.get_run(run_id)
        if not run["variables"]:
            raise DomainError("VARIABLES_REQUIRED", "변수 스키마가 없습니다.")
        variables = [Variable.parse(item) for item in run["variables"]]
        approved = [
            Constraint.parse(item, variables) for item in run["constraints"] if item["review_status"] == "approved"
        ]
        if not approved:
            raise DomainError("APPROVAL_REQUIRED", "통계 계산 전에는 승인된 제약이 하나 이상 필요합니다.")
        estimand = payload.get("estimand", {})
        numerator = validate_where(estimand.get("numerator", {}), variables)
        denominator_raw = estimand.get("denominator")
        denominator = validate_where(denominator_raw, variables) if denominator_raw else None
        dag_candidates = payload.get("dag_candidates", [])
        if not isinstance(dag_candidates, list):
            raise DomainError("INVALID_DAG", "dag_candidates는 배열이어야 합니다.")
        result = estimate(
            variables,
            approved,
            numerator,
            denominator,
            dag_candidates,
            str(payload.get("selected_model", "maximum_entropy")),
        )
        result["estimand"] = {"numerator": numerator, "denominator": denominator}
        result["assumption"] = "maximum entropy: 관측하지 않은 고차 상호작용을 0으로 두는 명시적 구조 가정" + (
            ""
            if result.get("cross_constraint_count")
            else " · 승인 제약이 모두 단일 변수 조건이라 변수 간 상관은 관측되지 않았고 독립으로 처리됩니다 — 조합 비중은 주변분포의 곱입니다."
        )
        self.store.update_run(run_id, result=result, estimand=result["estimand"], status="running")
        self.store.append_event(
            run_id, "statistics.completed", {"status": result["status"], "constraint_count": len(approved)}
        )
        self._persist_manifest(run_id)
        return self.store.get_run(run_id)

    def create_personas(self, run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        run = self.store.get_run(run_id)
        if not payload.get("adult_population_confirmed"):
            raise DomainError(
                "ADULT_CONFIRMATION_REQUIRED", "1인칭 합성 페르소나는 성인 대상임을 확인해야 생성할 수 있습니다."
            )
        result = run["result"] or {}
        if result.get("status") not in {"feasible", "scenario_only"}:
            raise DomainError(
                "FEASIBLE_MODEL_REQUIRED", "feasible한 통계 모델 또는 명시된 scenario-only 모델이 필요합니다."
            )
        seed = int(payload.get("seed", 20260801))
        personas = sample_personas(result["states"], result["distribution"], int(payload.get("count", 20)), seed)
        result["personas"] = {
            "seed": seed,
            "items": personas,
            "evidence_level": "scenario_only" if result.get("status") == "scenario_only" else "partial_estimate",
            "warning": (
                "승인된 정량 제약 없이 균등 시나리오에서 표집한 완전 합성 성인 프로필입니다. 모집단이나 실제 개인을 나타내지 않습니다."
                if result.get("status") == "scenario_only"
                else "완전 합성 성인 페르소나입니다. 실제 개인이나 대표 표본이 아닙니다."
            ),
        }
        self.store.update_run(run_id, result=result)
        self.store.append_event(run_id, "personas.sampled", {"count": len(personas), "seed": seed})
        self._persist_manifest(run_id)
        return self.store.get_run(run_id)

    def narratives(self, run_id: str) -> dict[str, Any]:
        run = self.store.get_run(run_id)
        result = run["result"] or {}
        personas = result.get("personas", {})
        items = personas.get("items", [])
        if not items:
            raise DomainError("PERSONAS_REQUIRED", "서술을 만들기 전에 페르소나를 생성해야 합니다.")
        personas["narratives"] = generate_narratives(items, int(personas["seed"]))
        result["personas"] = personas
        self.store.update_run(run_id, result=result)
        self.store.append_event(run_id, "personas.narrated", {"count": len(items)})
        self._persist_manifest(run_id)
        return self.store.get_run(run_id)

    def policy_panel_review(self, run_id: str) -> dict[str, Any]:
        run = self.store.get_run(run_id)
        plan = self._latest_policy_plan(run)
        result = run["result"] or {}
        if result.get("status") not in {"feasible", "scenario_only"}:
            raise DomainError(
                "FEASIBLE_MODEL_REQUIRED", "가중 가상 시민 패널은 PGM 또는 scenario-only 결과 뒤에만 만들 수 있습니다."
            )
        evidence_level = "scenario_only" if result.get("status") == "scenario_only" else "partial_estimate"
        variable_labels = {item["id"]: item.get("label") or item["id"] for item in run["variables"]}
        category_labels = {item["id"]: item.get("category_labels") or {} for item in run["variables"]}
        panel = weighted_segments(
            result["states"],
            result["distribution"],
            limit=12,
            evidence_level=evidence_level,
            variable_labels=variable_labels,
            category_labels=category_labels,
        )
        demo_mode = os.getenv("PERSONA_RESTORER_DEMO_MODEL", "0") == "1"

        # 내부 계산용 code로 셀을 식별하고, 표시·프롬프트에는 한국어 label을 전달한다.
        def cell_signature(segment: dict[str, Any]) -> tuple:
            return tuple(
                (item.get("variable_code", item["variable"]), item.get("code", item["value"]))
                for item in segment["attributes"]
            )

        # 유니크 셀 패널의 상한 밖에 남은 세그먼트는 침묵하는 사각지대가 되므로 명시적으로 공개한다.
        total_weight = sum(float(weight) for weight in result["distribution"]) or 1.0
        sampled_cell_keys = {cell_signature(segment) for segment in panel}
        omitted_cells = sorted(
            (
                {
                    "attributes": labeled_attributes(state, variable_labels, category_labels),
                    "share": float(weight) / total_weight,
                }
                for state, weight in zip(result["states"], result["distribution"])
                if float(weight) > 0 and tuple(state.items()) not in sampled_cell_keys
            ),
            key=lambda item: item["share"],
            reverse=True,
        )[:8]

        narrate_pool = ThreadPoolExecutor(max_workers=1)
        narrate_future = (
            narrate_pool.submit(narrate_panel_segments, panel, plan.get("policy_focus"))
            if not demo_mode
            else None
        )
        try:
            interviews = simulate_policy_interviews(
                panel, plan, int((result.get("personas") or {}).get("seed", 20260801))
            )
            if narrate_future is not None:
                try:
                    profiles = narrate_future.result()
                    for segment in panel:
                        segment["narrative"] = profiles[segment["id"]]
                except DomainError:
                    pass
        finally:
            narrate_pool.shutdown(wait=False)
        insights = None
        if not demo_mode:
            source_titles = {
                item["id"]: f"{item.get('organization', '')} · {item.get('title', '')}" for item in run["sources"]
            }
            approved_constraints = [
                {
                    "where": item.get("where"),
                    "value": item.get("value"),
                    "source": source_titles.get(item.get("source_id"), item.get("source_id")),
                }
                for item in run.get("constraints", [])
                if item.get("review_status") == "approved"
            ]
            self.store.append_event(run_id, "tool.started", {"tool": "llm.synthesize_insights"})
            insight_buffer: list[str] = []

            def flush_insight_deltas() -> None:
                if insight_buffer:
                    self.store.append_event(run_id, "insight.delta", {"delta": "".join(insight_buffer)})
                    insight_buffer.clear()

            def on_insight_delta(chunk: str) -> None:
                insight_buffer.append(chunk)
                if sum(len(piece) for piece in insight_buffer) >= 120:
                    flush_insight_deltas()

            try:
                insights = synthesize_policy_insights(
                    plan,
                    panel,
                    interviews,
                    str(result.get("status")),
                    approved_constraints=approved_constraints,
                    responses=summarize_panel_interviews(panel, interviews),
                    omitted_cells=omitted_cells,
                    on_delta=on_insight_delta,
                )
                flush_insight_deltas()
                self.store.append_event(run_id, "tool.completed", {"tool": "llm.synthesize_insights"})
            except DomainError as error:
                flush_insight_deltas()
                self.store.append_event(
                    run_id, "tool.failed", {"tool": "llm.synthesize_insights", "code": error.code}
                )
        review = {
            "status": "COMPLETED_WITH_ASSUMPTIONS" if plan.get("assumptions") else "COMPLETED",
            "plan_id": plan["id"],
            "alternatives": plan["alternatives"],
            "panel": panel,
            "panel_coverage": sum(item["weight"] for item in panel),
            "interviews": interviews,
            "responses": summarize_panel_interviews(panel, interviews),
            "fieldwork_questions": plan.get("interview_questions", []),
            "omitted_cells": omitted_cells,
            "insights": insights,
            "brief": policy_brief(plan, panel, interviews, insights=insights),
            "warning": "인터뷰는 PGM으로 가중된 완전 합성 패널에 대한 모델 모의 응답입니다. 실제 시민 응답·찬성률·행동·인과효과가 아닙니다.",
        }
        result["policy_review"] = review
        self.store.update_run(run_id, result=result, status="reviewed")
        self.store.append_event(
            run_id,
            "policy.panel_interview_completed",
            {"plan_id": plan["id"], "segment_count": len(panel), "interview_count": len(interviews)},
        )
        self._persist_manifest(run_id)
        return self.store.get_run(run_id)

    def report(self, run_id: str) -> dict[str, Any]:
        run = self.store.get_run(run_id)
        result = run["result"]
        if not result:
            raise DomainError("RESULT_REQUIRED", "보고서 전에 통계 계산을 실행하세요.")
        self.store.update_run(run_id, status="completed")
        report_run = self.store.get_run(run_id)
        html_path = self.store.write_artifact(run_id, "report.html", render_html_report(report_run))
        policy_review = (report_run.get("result") or {}).get("policy_review")
        downloads: dict[str, str] = {}
        if policy_review:
            alternative_labels = {
                item.get("id"): item.get("label") for item in policy_review.get("alternatives", [])
            }
            answers_by_persona: dict[str, list[dict[str, Any]]] = {}
            for item in policy_review.get("interviews", []):
                answers_by_persona.setdefault(str(item.get("segment_id")), []).append(
                    {
                        "policy_id": item.get("policy_id"),
                        "policy": alternative_labels.get(item.get("policy_id"), item.get("policy_id")),
                        "response": item.get("response"),
                        "reason": item.get("reason"),
                        "barrier": item.get("barrier"),
                        "suggestion": item.get("suggested_change"),
                    }
                )
            panel_records = [
                {
                    "id": segment.get("id"),
                    "name": segment.get("display_name"),
                    "attributes": {
                        attr.get("variable"): attr.get("value") for attr in segment.get("attributes", [])
                    },
                    "share": segment.get("weight_display"),
                    "narrative": segment.get("narrative"),
                    "answers": answers_by_persona.get(str(segment.get("id")), []),
                }
                for segment in policy_review.get("panel", [])
            ]
            evidence_payload = {
                "sources": [
                    {
                        key: source.get(key)
                        for key in (
                            "id",
                            "title",
                            "organization",
                            "url",
                            "observed_period",
                            "trust_tier",
                            "snapshot_hash",
                        )
                    }
                    for source in report_run["sources"]
                ],
                "constraints": report_run["constraints"],
                "excluded_sources": ((report_run.get("result") or {}).get("research") or {}).get(
                    "excluded_sources", []
                ),
            }
            downloads = {
                "panel": self.store.write_artifact(
                    run_id,
                    "panel.jsonl",
                    "\n".join(json.dumps(item, ensure_ascii=False) for item in panel_records) + "\n",
                ),
                "interviews": self.store.write_artifact(
                    run_id,
                    "interviews.jsonl",
                    "\n".join(
                        json.dumps(item, ensure_ascii=False) for item in policy_review.get("interviews", [])
                    )
                    + "\n",
                ),
                "evidence": self.store.write_artifact(
                    run_id, "evidence.json", json.dumps(evidence_payload, ensure_ascii=False, indent=2)
                ),
            }
        for stale in ("report.md", "policy_brief.md"):
            (self.store.run_dir / run_id / stale).unlink(missing_ok=True)
        self.store.append_event(run_id, "report.completed", {"artifact": html_path})
        self._persist_manifest(run_id)
        return {
            "artifact": html_path,
            "html_artifact": html_path,
            "report_url": f"/api/runs/{run_id}/artifacts/report.html",
            "downloads": {key: f"/api/runs/{run_id}/artifacts/{Path(value).name}" for key, value in downloads.items()},
            "run": self.store.get_run(run_id),
        }

    def _persist_manifest(self, run_id: str) -> None:
        run = self.store.get_run(run_id)
        stable = json.dumps(run, ensure_ascii=False, sort_keys=True)
        self.store.write_artifact(run_id, "run.json", stable)
        self.store.append_event(run_id, "artifact.persisted", {"sha256": hashlib.sha256(stable.encode()).hexdigest()})
