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


def _safe_frame(columns: list[str], rows: list[dict[str, object]]) -> pd.DataFrame:
    return pd.DataFrame(
        [{column: escape(_text(row.get(column))) for column in columns} for row in rows], columns=columns
    )


def _great_table(title: str, columns: list[str], rows: list[dict[str, object]]) -> str:
    """One rendering harness for all display tables, including predictable empty tables."""
    return GT(_safe_frame(columns, rows)).tab_header(title=title).as_raw_html()


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

    return {
        "summary": (summary_columns, summary_rows),
        "sources": (source_columns, source_rows),
        "constraints": (constraint_columns, constraint_rows),
        "estimate": (estimate_columns, estimate_rows),
        "policy": (policy_columns, policy_rows),
        "personas": (persona_columns, persona_rows),
        "panel": (panel_columns, panel_rows),
    }


RESPONSE_META = {
    "support": ("지지", "#0E7A56"),
    "conditional": ("조건부", "#B07818"),
    "low_change": ("변화 낮음", "#3E6FC4"),
    "decline": ("거절", "#B23A2C"),
}


def _brief_html(brief: str) -> str:
    """Render the policy brief's small markdown dialect (##, -, >) to HTML."""
    parts: list[str] = []
    list_open = False

    def close_list() -> None:
        nonlocal list_open
        if list_open:
            parts.append("</ul>")
            list_open = False

    for line in str(brief or "").splitlines():
        line = line.strip()
        if not line or line.startswith("# "):
            continue
        if line.startswith("## "):
            close_list()
            parts.append(f"<h3>{escape(line[3:])}</h3>")
        elif line.startswith("- "):
            if not list_open:
                parts.append("<ul>")
                list_open = True
            parts.append(f"<li>{escape(line[2:])}</li>")
        elif line.startswith("> "):
            close_list()
            parts.append(f"<blockquote>{escape(line[2:])}</blockquote>")
        else:
            close_list()
            parts.append(f"<p>{escape(line)}</p>")
    close_list()
    return "".join(parts)


def _response_bars(alternatives: list[dict[str, Any]], responses: dict[str, Any]) -> str:
    """One labelled 100%-stacked bar per policy alternative; identity also carried by legend and the policy table."""
    if not responses:
        return ""
    rows: list[str] = []
    used: list[str] = []
    for option in alternatives:
        summary = responses.get(option.get("id")) or {}
        total = sum(float(value) for value in summary.values())
        if not total:
            continue
        segments = []
        for key, (label, color) in RESPONSE_META.items():
            share = float(summary.get(key, 0.0)) / total
            if share <= 0:
                continue
            if key not in used:
                used.append(key)
            pct = f"{share * 100:.0f}%"
            text = f'<span class="seg-label">{pct}</span>' if share >= 0.12 else ""
            segments.append(
                f'<span class="seg" style="width:{share * 100:.2f}%;background:{color}" '
                f'title="{escape(label)} {pct}" aria-label="{escape(label)} {pct}">{text}</span>'
            )
        rows.append(
            f'<div class="bar-row"><span class="bar-name">{escape(_text(option.get("label")))}</span>'
            f'<span class="bar" role="img" aria-label="{escape(_text(option.get("label")))} 반응 분포">{"".join(segments)}</span></div>'
        )
    if not rows:
        return ""
    legend = "".join(
        f'<span class="legend-item"><span class="legend-dot" style="background:{RESPONSE_META[key][1]}"></span>{RESPONSE_META[key][0]}</span>'
        for key in used
    )
    return (
        '<section><h2>정책안별 모의 반응</h2><div class="bars">'
        + "".join(rows)
        + f'</div><div class="legend">{legend}</div>'
        + '<p class="fine">가중 합성 패널의 모델 모의 응답 비중입니다. 실제 시민 여론이 아니며, 수치 표는 아래 "정책안과 모의 반응"에 있습니다.</p></section>'
    )


def render_html_report(run: dict[str, Any]) -> str:
    tables = report_tables(run)
    result = run.get("result") or {}
    identification = result.get("identification") or {}
    policy_review = result.get("policy_review") or {}
    question = escape(_text(run.get("question")))

    status = result.get("status")
    evidence_stat = {
        "feasible": "승인 제약 기반 추정",
        "scenario_only": "균등 시나리오 (추정 아님)",
        "infeasible": "제약 충돌",
    }.get(status, _text(status))
    interval = f"{_probability(identification.get('lower'))} – {_probability(identification.get('upper'))}"
    coverage = policy_review.get("panel_coverage")
    coverage_stat = f"{float(coverage) * 100:.1f}%" if coverage is not None else "—"
    stats = f"""<section class="stats">
      <div class="stat"><small>근거 상태</small><strong>{escape(evidence_stat)}</strong></div>
      <div class="stat"><small>가정 없는 식별구간</small><strong>{escape(interval)}</strong></div>
      <div class="stat"><small>패널 커버리지 · 인터뷰</small><strong>{escape(coverage_stat)} · {len(policy_review.get("interviews", []))}건</strong></div>
    </section>"""

    brief_section = (
        f'<section class="brief"><h2>정책 사전검증 브리프</h2>{_brief_html(policy_review.get("brief", ""))}'
        + (f'<p class="warning">{escape(_text(policy_review.get("warning")))}</p>' if policy_review.get("warning") else "")
        + "</section>"
        if policy_review
        else ""
    )
    bars_section = _response_bars(policy_review.get("alternatives", []), policy_review.get("responses") or {})

    table_html = "\n".join(
        _great_table(title, *tables[key])
        for key, title in (
            ("summary", "실행 요약"),
            ("sources", "출처 원장"),
            ("constraints", "검토 제약"),
            ("estimate", "결합분포와 식별성"),
            ("policy", "정책안과 모의 반응"),
            ("personas", "합성 페르소나 표집"),
            ("panel", "가중 가상 시민 패널"),
        )
    )

    return f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>E2P Agent 연구 보고서</title><style>
  :root {{ color-scheme: light; --paper:#FAF9F5; --panel:#F0EEE6; --ink:#1F1E1B; --muted:#6B675D; --line:#E4E0D5; --accent:#C15F3C; }}
  * {{ box-sizing:border-box; }}
  body {{ max-width:960px; margin:0 auto; padding:56px 32px 88px; color:var(--ink); background:var(--paper);
         font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Apple SD Gothic Neo","Malgun Gothic",sans-serif; line-height:1.6; }}
  h1,h2,.question {{ font-family:"Noto Serif KR","Apple SD Gothic Neo",Georgia,serif; }}
  header {{ margin-bottom:34px; padding-bottom:26px; border-bottom:2px solid var(--ink); }}
  .eyebrow {{ color:var(--accent); font-size:11px; font-weight:700; letter-spacing:.14em; }}
  h1 {{ margin:10px 0 6px; font-size:34px; font-weight:600; letter-spacing:-.02em; }}
  .question {{ color:var(--muted); font-size:17px; margin:0 0 14px; }}
  .notice {{ border-left:3px solid var(--accent); padding:10px 14px; color:#5d5147; background:#F5EDE4; font-size:13px; margin:0; }}
  h2 {{ margin:44px 0 14px; font-size:21px; font-weight:600; letter-spacing:-.01em; }}
  .stats {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(200px,1fr)); gap:10px; margin-top:28px; }}
  .stat {{ background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:14px 16px; }}
  .stat small {{ display:block; color:var(--muted); font-size:12px; margin-bottom:4px; }}
  .stat strong {{ font-size:16px; letter-spacing:-.01em; }}
  .brief {{ background:#fff; border:1px solid var(--line); border-radius:10px; padding:6px 24px 18px; margin-top:28px; }}
  .brief h2 {{ margin-top:18px; }}
  .brief h3 {{ margin:18px 0 6px; font-size:14px; color:var(--accent); letter-spacing:.02em; }}
  .brief p, .brief li {{ font-size:14px; margin:4px 0; }}
  .brief ul {{ margin:4px 0; padding-left:20px; }}
  .brief blockquote {{ margin:14px 0 4px; padding:8px 12px; border-left:3px solid var(--line); color:var(--muted); font-size:13px; }}
  .warning {{ color:#8a4f3d; font-size:13px; }}
  .bars {{ display:flex; flex-direction:column; gap:12px; }}
  .bar-row {{ display:grid; grid-template-columns:180px 1fr; gap:12px; align-items:center; }}
  .bar-name {{ font-size:13px; color:var(--ink); }}
  .bar {{ display:flex; gap:2px; height:26px; border-radius:4px; overflow:hidden; background:var(--panel); }}
  .seg {{ display:flex; align-items:center; justify-content:center; min-width:2px; }}
  .seg-label {{ color:#fff; font-size:11px; font-weight:600; }}
  .legend {{ display:flex; gap:16px; margin-top:10px; flex-wrap:wrap; }}
  .legend-item {{ display:inline-flex; align-items:center; gap:6px; font-size:12px; color:var(--muted); }}
  .legend-dot {{ width:9px; height:9px; border-radius:50%; display:inline-block; }}
  .fine {{ color:var(--muted); font-size:12px; margin-top:10px; }}
  table.gt_table {{ width:100% !important; margin:0 0 22px !important; background:#fff; border-collapse:collapse; font-size:12.5px; }}
  table.gt_table th {{ color:#55524a; background:var(--panel); font-weight:600; }}
  table.gt_table th, table.gt_table td {{ border-bottom:1px solid var(--line); padding:8px 10px; text-align:left; vertical-align:top; }}
  footer {{ margin-top:52px; padding-top:16px; border-top:1px solid var(--line); color:var(--muted); font-size:12px;
            font-family:ui-monospace,SFMono-Regular,Menlo,monospace; }}
  @media (max-width:640px) {{ .bar-row {{ grid-template-columns:1fr; gap:4px; }} body {{ padding:28px 18px 56px; }} }}
  @media print {{ body {{ padding:22px; }} }}
</style></head><body>
<header><span class="eyebrow">E2P AGENT · PROVENANCE-AWARE SYNTHETIC POLICY RESEARCH</span>
<h1>정책 검토 보고서</h1><p class="question">{question}</p>
<p class="notice">{escape(REPORT_NOTICE)}</p></header>
{stats}
{brief_section}
{bars_section}
<h2>근거와 계산</h2>
{table_html}
<h2>해석 한계</h2><ul>
<li>승인된 제약만 계산에 사용했습니다. 모집단·시점·범주 매핑은 출처 원장과 함께 재검토해야 합니다.</li>
<li>점추정은 구조 가정에 의존하며, 식별구간과 구별해야 합니다.</li>
<li>합성 페르소나와 모의 인터뷰는 실제 개인·실제 응답·대표 표본이 아닙니다.</li>
</ul>
<footer>run id: {escape(_text(run.get("id")))} · status: {escape(_text(run.get("status")))}</footer>
</body></html>"""
