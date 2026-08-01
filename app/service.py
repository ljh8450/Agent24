from __future__ import annotations

import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from .contracts import Constraint, Variable, new_id, now, parse_variables, validate_where
from .errors import DomainError
from .personas import (
    answer_persona_question,
    extract_constraint_candidates,
    generate_narratives,
    sample_personas,
    simulate_policy_interviews,
    simulate_survey,
)
from .policy_review import build_policy_plan, policy_brief, summarize_panel_interviews, weighted_segments
from .reporting import render_html_report
from .sources import (
    fetch_data_go_api,
    fetch_kosis_statistics,
    fetch_source,
    korean_source_catalog,
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

    def chat(self, text: str, client_event_id: str | None = None) -> dict[str, Any]:
        question = " ".join(text.split())
        if len(question) < 4:
            raise DomainError("QUESTION_TOO_SHORT", "대상 집단과 알고 싶은 정책/질문을 조금 더 구체적으로 입력하세요.")
        target = self._target_hint(question)
        run = {
            "id": new_id("run"),
            "session_key": f"agent:e2p-agent:webchat:{new_id('session')}",
            "question": question,
            "target_population": target,
            "status": "waiting_for_review",
            "created_at": now(),
            "updated_at": now(),
        }
        stored = self.store.create_run(run, client_event_id)
        self.store.append_event(stored["id"], "tool.completed", {"tool": "agent.intake_question"})
        plan = build_policy_plan(question, target)
        self.store.append_event(stored["id"], "policy.plan_created", {"plan": plan})
        if plan["status"] == "SAFETY_BLOCKED":
            self.store.update_run(stored["id"], status="safety_blocked")
            self.store.append_event(stored["id"], "policy.blocked", {"reason": plan["blocked_reason"]})
            message = plan["blocked_reason"]
        else:
            proposed = parse_variables(plan["proposed_variables"])
            self.store.update_run(
                stored["id"],
                status="planning",
                variables=[
                    {"id": item.id, "label": item.label, "categories": list(item.categories)} for item in proposed
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
        llm_ready = all(os.getenv(name) for name in ("LLM_API_URL", "LLM_API_KEY", "LLM_MODEL"))
        self._autonomous_constraint_extraction(run_id, stored_sources, llm_ready)
        approved_ids = self._autonomous_evidence_gate(run_id)

        self.store.append_event(run_id, "tool.started", {"tool": "statistics.identification_bounds"})
        if approved_ids:
            computed = self.compute(run_id, self._default_estimand(self.store.get_run(run_id)))
            evidence_mode = "approved_public_constraints"
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
                warning="대상 이해 요청으로 판정되어 모의 인터뷰 없이 가중 패널·근거·현장조사 질문만 생성했습니다.",
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
            self._policy_review_without_llm(
                run_id,
                warning=(
                    "LLM 모의 인터뷰 호출이 완료되지 않아 응답률을 만들지 않았습니다. "
                    f"합성 프로필과 통계·시나리오 상태만 표시합니다. (사유: {error.code})"
                    if error.code != "LLM_NOT_CONFIGURED"
                    else None
                ),
            )
            panel_outcome = (
                "profiles_only_no_llm" if error.code == "LLM_NOT_CONFIGURED" else "profiles_only_llm_failure"
            )
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
            message = "대상 이해 요청으로 가중 페르소나 패널과 근거·현장조사 질문을 생성했습니다." + (
                " 검증된 정량 제약이 없어 가중치는 균등 시나리오이며 모집단 추정이 아닙니다." if scenario_only else ""
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
            if item.get("trust_tier") in {"korean_official", "korean_research"} and item.get("url")
        ][:4]
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
        for source in sources:
            try:
                accepted += len(self.extract_candidates(run_id, source["id"])["candidates"])
            except DomainError as error:
                self.store.append_event(
                    run_id,
                    "constraint.extraction_skipped",
                    {"source_id": source["id"], "code": error.code},
                )
        self.store.append_event(
            run_id,
            "tool.completed",
            {"tool": "llm.extract_constraint_candidates", "accepted_candidates": accepted},
        )

    def _autonomous_evidence_gate(self, run_id: str) -> list[str]:
        # 자율 실행에서는 사람이 없으므로 exact 모집단 일치 제약만 코드가 자동 승인한다. 사람 검토가 아니다.
        self.store.append_event(run_id, "tool.started", {"tool": "review.auto_approve_exact_constraints"})
        run = self.store.get_run(run_id)
        trusted_sources = {
            item["id"] for item in run["sources"] if item.get("trust_tier") in {"korean_official", "korean_research"}
        }
        selected = [
            item["id"]
            for item in run["constraints"]
            if item.get("source_id") in trusted_sources
            and item.get("population_compatibility") == "exact"
            and str(item.get("raw_statement", "")).strip()
        ]
        if selected:
            self.approve_constraints(run_id, {"constraint_ids": selected, "override_notes": {}})
        self.store.append_event(
            run_id,
            "tool.completed",
            {
                "tool": "review.auto_approve_exact_constraints",
                "approved": len(selected),
                "gate": "exact_population_only",
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
        panel = weighted_segments(result["states"], result["distribution"], evidence_level=evidence_level)
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

    def source_catalog(self) -> dict[str, Any]:
        return {
            "sources": korean_source_catalog(),
            "data_go_services": ["welfare_list", "nps_subscription"],
            "warning": "등급은 기관·도메인 기반 시작점입니다. 통계표의 조사설계·모집단·시점·범주를 반드시 검토하세요.",
        }

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

    def search_sources(self, run_id: str, query: str, trusted_korean_only: bool = True) -> dict[str, Any]:
        self.store.get_run(run_id)
        results = search_public_web(query, trusted_korean_only)
        self.store.append_event(
            run_id,
            "source.search_completed",
            {"query": query, "count": len(results), "trusted_korean_only": trusted_korean_only},
        )
        return {
            "results": results,
            "trusted_korean_only": trusted_korean_only,
            "warning": "검색 결과는 미검증 외부 증거입니다. 원문을 스냅샷으로 저장한 뒤 사람이 조사설계·모집단·범주 매핑을 검토해야 합니다.",
        }

    def fetch_source(self, run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.store.get_run(run_id)
        metadata = payload.get("metadata", {})
        if not isinstance(metadata, dict):
            raise DomainError("INVALID_SOURCE_METADATA", "출처 메타데이터는 JSON 객체여야 합니다.")
        source = fetch_source(self.root, str(payload.get("url", "")), metadata)
        self.store.add_source(run_id, source.as_dict())
        return source.as_dict()

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
        variables = [Variable.parse(item) for item in run["variables"]]
        for candidate in candidates:
            candidate["source_id"] = source_id
            candidate["review_status"] = "candidate"
            try:
                constraint = Constraint.parse(candidate, variables)
            except DomainError:
                continue
            self.store.add_constraint(run_id, constraint.as_dict())
            accepted.append(constraint.as_dict())
        self.store.append_event(
            run_id,
            "constraint.extraction_completed",
            {"source_id": source_id, "accepted_candidate_count": len(accepted)},
        )
        return {
            "candidates": accepted,
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
        result["assumption"] = "maximum entropy: 관측하지 않은 고차 상호작용을 0으로 두는 명시적 구조 가정"
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

    def survey(self, run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        run = self.store.get_run(run_id)
        result = run["result"] or {}
        items = result.get("personas", {}).get("items", [])
        if not items:
            raise DomainError("PERSONAS_REQUIRED", "합성 설문 전에는 페르소나를 생성해야 합니다.")
        policy_question = " ".join(str(payload.get("policy_question", "")).split())
        if len(policy_question) < 4:
            raise DomainError("POLICY_QUESTION_REQUIRED", "조사할 정책 문항을 입력하세요.")
        survey = simulate_survey(items, policy_question, int(result["personas"]["seed"]))
        result["survey"] = survey
        self.store.update_run(run_id, result=result)
        self.store.append_event(run_id, "survey.completed", {"n": survey["n"], "mode": survey["mode"]})
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
        panel = weighted_segments(result["states"], result["distribution"], evidence_level=evidence_level)
        interviews = simulate_policy_interviews(panel, plan, int((result.get("personas") or {}).get("seed", 20260801)))
        review = {
            "status": "COMPLETED_WITH_ASSUMPTIONS" if plan.get("assumptions") else "COMPLETED",
            "plan_id": plan["id"],
            "alternatives": plan["alternatives"],
            "panel": panel,
            "panel_coverage": sum(item["weight"] for item in panel),
            "interviews": interviews,
            "responses": summarize_panel_interviews(panel, interviews),
            "fieldwork_questions": plan.get("interview_questions", []),
            "brief": policy_brief(plan, panel, interviews),
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

    def persona_chat(self, run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        run = self.store.get_run(run_id)
        items = (run["result"] or {}).get("personas", {}).get("items", [])
        persona = next((item for item in items if item["id"] == payload.get("persona_id")), None)
        if not persona:
            raise DomainError("PERSONA_NOT_FOUND", "해당 합성 페르소나를 찾을 수 없습니다.")
        answer = answer_persona_question(persona, str(payload.get("question", "")), payload.get("allowed_variable"))
        self.store.append_event(
            run_id, "persona.question_answered", {"persona_id": persona["id"], "status": answer["status"]}
        )
        return answer

    def seal_holdout(self, run_id: str) -> dict[str, Any]:
        run = self.store.get_run(run_id)
        result = run["result"] or {}
        if result.get("status") != "feasible":
            raise DomainError("FEASIBLE_MODEL_REQUIRED", "봉인 전에 feasible한 통계 모델이 필요합니다.")
        if result.get("holdout"):
            raise DomainError(
                "HOLDOUT_ALREADY_SEALED", "이 run은 이미 홀드아웃을 봉인했습니다. 새 run으로 다시 시작하세요."
            )
        payload = {
            "distribution": result["distribution"],
            "estimand": result["estimand"],
            "selected_model": result["selected_model"],
            "maximum_entropy": result["maximum_entropy"],
        }
        result["holdout"] = {
            "sealed_at": now(),
            "prediction_hash": hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest(),
            "prediction": payload,
        }
        self.store.update_run(run_id, result=result)
        self.store.append_event(run_id, "holdout.sealed", {"prediction_hash": result["holdout"]["prediction_hash"]})
        self._persist_manifest(run_id)
        return self.store.get_run(run_id)

    def evaluate_holdout(self, run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        run = self.store.get_run(run_id)
        result = run["result"] or {}
        holdout = result.get("holdout")
        if not holdout or holdout.get("evaluation"):
            raise DomainError(
                "HOLDOUT_NOT_READY", "먼저 예측을 봉인해야 하며, 봉인한 홀드아웃은 한 번만 채점할 수 있습니다."
            )
        actual = payload.get("actual_distribution")
        if not isinstance(actual, list) or len(actual) != len(result["distribution"]):
            raise DomainError("INVALID_HOLDOUT", "actual_distribution의 셀 수가 추정 분포와 일치해야 합니다.")
        try:
            actual = [float(item) for item in actual]
        except (TypeError, ValueError) as error:
            raise DomainError("INVALID_HOLDOUT", "홀드아웃 분포는 숫자 배열이어야 합니다.") from error
        if any(item < 0 for item in actual) or abs(sum(actual) - 1) > 1e-8:
            raise DomainError("INVALID_HOLDOUT", "홀드아웃 분포는 음수가 아니고 합이 1이어야 합니다.")
        prediction = holdout["prediction"]["distribution"]
        tv_distance = 0.5 * sum(abs(left - right) for left, right in zip(prediction, actual, strict=True))
        estimand = holdout["prediction"]["estimand"]
        states = result["states"]
        numerator = [all(state.get(key) == value for key, value in estimand["numerator"].items()) for state in states]
        denominator_where = estimand.get("denominator")
        denominator = (
            [all(state.get(key) == value for key, value in denominator_where.items()) for state in states]
            if denominator_where
            else None
        )
        if denominator:
            joined = [left and right for left, right in zip(numerator, denominator, strict=True)]
            denominator_probability = sum(value for value, mask in zip(actual, denominator, strict=True) if mask)
            actual_estimand = (
                sum(value for value, mask in zip(actual, joined, strict=True) if mask) / denominator_probability
                if denominator_probability
                else None
            )
        else:
            actual_estimand = sum(value for value, mask in zip(actual, numerator, strict=True) if mask)
        interval = result["identification"]
        coverage = (
            interval.get("lower") is not None
            and actual_estimand is not None
            and interval["lower"] - 1e-8 <= actual_estimand <= interval["upper"] + 1e-8
        )
        holdout["evaluation"] = {
            "evaluated_at": now(),
            "tv_distance": tv_distance,
            "actual_estimand": actual_estimand,
            "interval_covered": coverage,
            "actual_distribution_hash": hashlib.sha256(json.dumps(actual).encode()).hexdigest(),
        }
        result["holdout"] = holdout
        self.store.update_run(run_id, result=result)
        self.store.append_event(run_id, "holdout.evaluated", {"tv_distance": tv_distance, "interval_covered": coverage})
        self._persist_manifest(run_id)
        return self.store.get_run(run_id)

    def report(self, run_id: str) -> dict[str, Any]:
        run = self.store.get_run(run_id)
        result = run["result"]
        if not result:
            raise DomainError("RESULT_REQUIRED", "보고서 전에 통계 계산을 실행하세요.")
        self.store.update_run(run_id, status="completed")
        report_run = self.store.get_run(run_id)
        html_report = render_html_report(report_run)
        html_path = self.store.write_artifact(run_id, "report.html", html_report)
        policy_review = (report_run.get("result") or {}).get("policy_review")
        downloads: dict[str, str] = {}
        if policy_review:
            downloads = {
                "panel": self.store.write_artifact(
                    run_id,
                    "panel.jsonl",
                    "\n".join(json.dumps(item, ensure_ascii=False) for item in policy_review["panel"]) + "\n",
                ),
                "interviews": self.store.write_artifact(
                    run_id,
                    "interviews.jsonl",
                    "\n".join(json.dumps(item, ensure_ascii=False) for item in policy_review["interviews"]) + "\n",
                ),
                "evidence": self.store.write_artifact(
                    run_id,
                    "evidence.json",
                    json.dumps(
                        {
                            "sources": report_run["sources"],
                            "constraints": report_run["constraints"],
                            "excluded_sources": ((report_run.get("result") or {}).get("research") or {}).get(
                                "excluded_sources", []
                            ),
                            "search_candidates": ((report_run.get("result") or {}).get("research") or {}).get(
                                "candidates", []
                            ),
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                ),
            }
        self.store.append_event(run_id, "report.completed", {"artifact": html_path})
        self._persist_manifest(run_id)
        return {
            "html": html_report,
            "artifact": html_path,
            "report_url": f"/api/runs/{run_id}/artifacts/report.html",
            "downloads": {key: f"/api/runs/{run_id}/artifacts/{Path(value).name}" for key, value in downloads.items()},
            "run": self.store.get_run(run_id),
        }

    def _persist_manifest(self, run_id: str) -> None:
        run = self.store.get_run(run_id)
        stable = json.dumps(run, ensure_ascii=False, sort_keys=True)
        self.store.write_artifact(run_id, "run.json", stable)
        self.store.append_event(run_id, "artifact.persisted", {"sha256": hashlib.sha256(stable.encode()).hexdigest()})
