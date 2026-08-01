from __future__ import annotations

from html import escape
from typing import Any

import pandas as pd
from great_tables import GT

REPORT_NOTICE = (
    "이 보고서는 공개 통계 제약과 명시된 구조 가정으로 만든 합성 연구 산출물입니다. "
    "실제 개인, 실제 여론조사, 인과효과를 나타내지 않습니다."
)


def _text(value: object) -> str:
    if value is None or value == "":
        return "—"
    return str(value)


def _probability(value: object) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value):.4f} ({float(value) * 100:.2f}%)"
    except (TypeError, ValueError):
        return _text(value)


def _markdown_table(columns: list[str], rows: list[dict[str, object]]) -> str:
    def cell(value: object) -> str:
        return _text(value).replace("|", "\\|").replace("\n", "<br>")

    header = "| " + " | ".join(columns) + " |"
    divider = "| " + " | ".join("---" for _ in columns) + " |"
    body = ["| " + " | ".join(cell(row.get(column)) for column in columns) + " |" for row in rows]
    return "\n".join([header, divider, *body])


def _safe_frame(columns: list[str], rows: list[dict[str, object]]) -> pd.DataFrame:
    return pd.DataFrame(
        [{column: escape(_text(row.get(column))) for column in columns} for row in rows], columns=columns
    )


def _great_table(title: str, subtitle: str, columns: list[str], rows: list[dict[str, object]]) -> str:
    """One rendering harness for all display tables, including predictable empty tables."""
    return GT(_safe_frame(columns, rows)).tab_header(title=title, subtitle=subtitle).as_raw_html()


def report_tables(run: dict[str, Any]) -> dict[str, tuple[list[str], list[dict[str, object]]]]:
    result = run.get("result") or {}
    constraints = run.get("constraints") or []
    sources = run.get("sources") or []
    identification = result.get("identification") or {}
    plan = (
        next(
            (
                event.get("payload", {}).get("plan")
                for event in reversed(run.get("events", []))
                if event.get("type") == "policy.plan_created"
            ),
            {},
        )
        or {}
    )
    policy_review = result.get("policy_review") or {}

    summary_columns = ["항목", "값"]
    summary_rows = [
        {"항목": "질문", "값": run.get("question")},
        {"항목": "대상 힌트", "값": run.get("target_population")},
        {"항목": "실행 상태", "값": run.get("status")},
        {"항목": "승인된 제약", "값": sum(item.get("review_status") == "approved" for item in constraints)},
        {"항목": "고정 출처", "값": len(sources)},
        {"항목": "선택 구조", "값": result.get("selected_model")},
    ]

    source_columns = ["출처", "기관", "관측 시점", "모집단", "등급", "스냅샷"]
    source_rows = [
        {
            "출처": source.get("title"),
            "기관": source.get("organization"),
            "관측 시점": source.get("observed_period"),
            "모집단": source.get("population"),
            "등급": source.get("trust_tier"),
            "스냅샷": (source.get("snapshot_hash") or "")[:12],
        }
        for source in sources
    ]

    constraint_columns = ["제약", "표준 조건", "관계", "값", "모집단 정합", "검토", "출처"]
    constraint_rows = [
        {
            "제약": item.get("label"),
            "표준 조건": ", ".join(f"{key}={value}" for key, value in (item.get("where") or {}).items()),
            "관계": item.get("relation"),
            "값": _probability(item.get("value")),
            "모집단 정합": item.get("population_compatibility"),
            "검토": item.get("review_status"),
            "출처": item.get("source_id"),
        }
        for item in constraints
    ]

    estimate_columns = ["지표", "값", "해석"]
    estimate_rows = [
        {
            "지표": "Feasibility",
            "값": result.get("status"),
            "해석": "제약을 동시에 만족하는 분포의 존재 여부",
        },
        {
            "지표": "최대엔트로피 점추정",
            "값": _probability((result.get("maximum_entropy") or {}).get("point_estimate")),
            "해석": "고차 상호작용을 0으로 두는 구조 가정의 결과",
        },
        {
            "지표": "가정 없는 식별구간",
            "값": f"{_probability(identification.get('lower'))} – {_probability(identification.get('upper'))}",
            "해석": identification.get("status") or "승인된 제약만으로 가능한 범위",
        },
    ]

    policy_columns = ["정책안", "가설", "주요 위험", "합성 패널 반응"]
    response_summary = policy_review.get("responses") or {}
    policy_rows = [
        {
            "정책안": option.get("label"),
            "가설": option.get("hypothesis"),
            "주요 위험": option.get("risk"),
            "합성 패널 반응": ", ".join(
                f"{key}={float(value) * 100:.1f}%"
                for key, value in (response_summary.get(option.get("id")) or {}).items()
            )
            or "모의 인터뷰 미실행",
        }
        for option in plan.get("alternatives", [])
    ]

    persona_columns = ["합성 페르소나", "표집된 속성", "태그"]
    persona_rows = [
        {
            "합성 페르소나": item.get("id"),
            "표집된 속성": ", ".join(
                f"{attribute.get('variable')}={attribute.get('value')}" for attribute in item.get("attributes", [])
            ),
            "태그": "sampled · 완전 합성",
        }
        for item in (result.get("personas") or {}).get("items", [])[:20]
    ]
    panel_columns = ["가중 세그먼트", "속성", "가중치", "근거 수준"]
    panel_rows = [
        {
            "가중 세그먼트": item.get("id"),
            "속성": ", ".join(
                f"{attribute.get('variable')}={attribute.get('value')}" for attribute in item.get("attributes", [])
            ),
            "가중치": item.get("weight_display"),
            "근거 수준": item.get("evidence_level"),
        }
        for item in policy_review.get("panel", [])
    ]

    holdout = result.get("holdout") or {}
    evaluation = holdout.get("evaluation") or {}
    holdout_columns = ["항목", "값"]
    holdout_rows = [
        {"항목": "예측 봉인 hash", "값": holdout.get("prediction_hash")},
        {"항목": "Total variation distance", "값": evaluation.get("tv_distance")},
        {"항목": "실제 관심량", "값": _probability(evaluation.get("actual_estimand"))},
        {"항목": "식별구간 포함 여부", "값": evaluation.get("interval_covered")},
    ]

    activity_columns = ["도구/이벤트", "상태", "기록 시각"]
    activity_rows = [
        {
            "도구/이벤트": event.get("payload", {}).get("tool") or event.get("type"),
            "상태": event.get("type"),
            "기록 시각": event.get("created_at"),
        }
        for event in run.get("events", [])
    ]
    return {
        "summary": (summary_columns, summary_rows),
        "sources": (source_columns, source_rows),
        "constraints": (constraint_columns, constraint_rows),
        "estimate": (estimate_columns, estimate_rows),
        "policy": (policy_columns, policy_rows),
        "personas": (persona_columns, persona_rows),
        "panel": (panel_columns, panel_rows),
        "holdout": (holdout_columns, holdout_rows),
        "activity": (activity_columns, activity_rows),
    }


def render_markdown_report(run: dict[str, Any]) -> str:
    tables = report_tables(run)
    result = run.get("result") or {}
    sections = [
        "# 페르소나 복원기 연구 보고서",
        "",
        f"> {REPORT_NOTICE}",
        "",
        "## 실행 요약",
        _markdown_table(*tables["summary"]),
        "",
        "## 출처 원장",
        _markdown_table(*tables["sources"]),
        "",
        "## 검토 제약",
        _markdown_table(*tables["constraints"]),
        "",
        "## 결합분포와 식별성",
        _markdown_table(*tables["estimate"]),
        "",
        "## 정책안과 모의 반응",
        _markdown_table(*tables["policy"]),
        "",
        "## 합성 페르소나 표집",
        _markdown_table(*tables["personas"]),
        "",
        "## 가중 가상 시민 패널",
        _markdown_table(*tables["panel"]),
        "",
        "## 봉인 홀드아웃 채점",
        _markdown_table(*tables["holdout"]),
        "",
        "## 도구 실행 이력",
        _markdown_table(*tables["activity"]),
        "",
        "## 한계와 해석 경계",
        "- 검색 결과와 원문은 도구 정책을 바꾸지 않는 외부 증거입니다.",
        "- `approved` 상태의 제약만 계산에 사용됩니다. 모집단·시점·범주 매핑은 출처 원장과 함께 재검토해야 합니다.",
        "- 점추정은 최대엔트로피 또는 사용자가 택한 구조 가정에 의존합니다. 식별구간과 동일시할 수 없습니다.",
        "- 페르소나는 결합분포에서 표집한 완전 합성 속성 튜플입니다. 실제 응답자나 대표 표본이 아닙니다.",
        "",
        "## 재현 정보",
        f"- run id: `{run.get('id')}`",
        f"- selected model: `{result.get('selected_model')}`",
        f"- generated state: `{run.get('status')}`",
    ]
    return "\n".join(sections) + "\n"

