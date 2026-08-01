from __future__ import annotations

from html import escape
from typing import Any

from .contracts import now

REPORT_NOTICE = (
    "이 보고서는 공개 통계 제약과 명시된 구조 가정으로 만든 합성 연구 산출물입니다. "
    "실제 개인, 실제 여론조사, 인과효과를 나타내지 않습니다."
)

RESPONSE_LABELS = {"support": "긍정", "conditional": "조건부", "low_change": "변화 낮음", "decline": "거부"}
RESPONSE_ORDER = ("support", "conditional", "low_change", "decline")
# 색약 안전을 위해 색 + 라벨 + 수치를 항상 병행 표기한다.
RESPONSE_COLORS = {"support": "#2f6f4f", "conditional": "#c07f2d", "low_change": "#8a8a83", "decline": "#a34d4d"}


def _text(value: object) -> str:
    return "" if value is None else str(value)


def _probability(value: object) -> str:
    try:
        return f"{float(value):.4f}"
    except (TypeError, ValueError):
        return "—"


def _latest_plan(run: dict[str, Any]) -> dict[str, Any]:
    for event in reversed(run.get("events", [])):
        if event.get("type") == "policy.plan_created":
            return event.get("payload", {}).get("plan") or {}
    return {}


def _persona_name(segment: dict[str, Any]) -> str:
    return _text(segment.get("display_name") or segment.get("id"))


def _inline_md(text: str) -> str:
    html = escape(text)
    import re

    html = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", html)
    return html


def _md_to_html(markdown: str) -> str:
    """Small markdown renderer for the insight section: headings, bullets, tables, bold."""
    lines = _text(markdown).split("\n")
    html: list[str] = []
    list_open = False
    table: list[str] = []

    def close_list() -> None:
        nonlocal list_open
        if list_open:
            html.append("</ul>")
            list_open = False

    def flush_table() -> None:
        nonlocal table
        if not table:
            return
        rows = [
            [cell.strip() for cell in line.strip().strip("|").split("|")]
            for line in table
        ]
        rows = [cells for cells in rows if not all(set(cell) <= set(":- ") and cell for cell in cells)]
        if rows:
            head, *body = rows
            html.append('<div class="table-wrap"><table>')
            html.append("<tr>" + "".join(f"<th>{_inline_md(cell)}</th>" for cell in head) + "</tr>")
            for cells in body:
                html.append("<tr>" + "".join(f"<td>{_inline_md(cell)}</td>" for cell in cells) + "</tr>")
            html.append("</table></div>")
        table = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("|"):
            close_list()
            table.append(stripped)
            continue
        flush_table()
        if stripped.startswith("### "):
            close_list()
            html.append(f"<h3>{_inline_md(stripped[4:])}</h3>")
        elif stripped.startswith("## "):
            close_list()
            html.append(f"<h3>{_inline_md(stripped[3:])}</h3>")
        elif stripped.startswith("- "):
            if not list_open:
                html.append("<ul>")
                list_open = True
            html.append(f"<li>{_inline_md(stripped[2:])}</li>")
        elif stripped:
            close_list()
            html.append(f"<p>{_inline_md(stripped)}</p>")
    flush_table()
    close_list()
    return "".join(html)


def _tiles_html(panel: list, approved: list, alternatives: list, evidence_status: str) -> str:
    status_label = "실측 제약 반영" if evidence_status == "feasible" else "균등 시나리오"
    tiles = (
        ("합성 표본", f"{len(panel)}명", "결합분포 비례 표집"),
        ("승인된 실측 제약", f"{len(approved)}건", status_label),
        ("비교 정책안", f"{len(alternatives)}개", "동일 패널에서 비교"),
    )
    return '<div class="tiles">' + "".join(
        f'<div class="tile"><small>{escape(label)}</small><strong>{escape(value)}</strong><span>{escape(hint)}</span></div>'
        for label, value, hint in tiles
    ) + "</div>"


def _response_counts(interviews: list, policy_id: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in interviews:
        if item.get("policy_id") == policy_id:
            counts[item.get("response")] = counts.get(item.get("response"), 0) + 1
    return counts


def _stacked_bars_html(review: dict[str, Any]) -> str:
    interviews = review.get("interviews") or []
    alternatives = review.get("alternatives") or []
    if not interviews or not alternatives:
        return ""
    legend = "".join(
        f'<span><i style="background:{RESPONSE_COLORS[key]}"></i>{RESPONSE_LABELS[key]}</span>' for key in RESPONSE_ORDER
    )
    rows = []
    for alt in alternatives:
        counts = _response_counts(interviews, alt.get("id"))
        total = sum(counts.values()) or 1
        segments = "".join(
            f'<i style="width:{counts[key] / total * 100:.1f}%;background:{RESPONSE_COLORS[key]}"></i>'
            for key in RESPONSE_ORDER
            if counts.get(key)
        )
        numbers = " · ".join(f"{RESPONSE_LABELS[key]} {counts[key]}명" for key in RESPONSE_ORDER if counts.get(key))
        rows.append(
            f'<div class="bar-row"><span class="bar-label">{escape(_text(alt.get("label")))}</span>'
            f'<span class="bar">{segments}</span><small>{escape(numbers)}</small></div>'
        )
    return f'<div class="bars"><div class="legend">{legend}</div>{"".join(rows)}</div>'


def _constraints_table_html(run: dict[str, Any]) -> str:
    sources = {item.get("id"): item for item in run.get("sources", [])}
    approved = [item for item in run.get("constraints", []) if item.get("review_status") == "approved"]
    if not approved:
        return (
            '<p class="callout warn">승인된 실측 통계 제약이 없어 표본은 균등 시나리오에서 표집되었습니다. '
            "아래 구성비는 모집단 추정치가 아닙니다.</p>"
        )
    rows = "".join(
        "<tr><td>"
        + escape(", ".join(f"{key} = {value}" for key, value in (item.get("where") or {}).items()))
        + f"</td><td>{float(item.get('value', 0)) * 100:.1f}%</td><td>"
        + ("대상 일치" if item.get("population_compatibility") == "exact" else "광의 대리값<sup>†</sup>")
        + "</td><td>"
        + escape(_text((sources.get(item.get("source_id")) or {}).get("organization") or item.get("source_id")))
        + "</td><td>"
        + escape(_text(item.get("raw_statement"))[:90])
        + "</td></tr>"
        for item in approved
    )
    proxy_note = (
        '<p class="footnote">† 광의 대리값: 전국·상위 모집단 통계를 대상 집단의 대리값으로 가정해 반영한 제약입니다. '
        "대상 모집단의 실측치가 아닙니다.</p>"
        if any(item.get("population_compatibility") != "exact" for item in approved)
        else ""
    )
    return (
        '<div class="table-wrap"><table><tr><th>조건</th><th>공표값</th><th>모집단</th><th>출처</th><th>원문 인용</th></tr>'
        + rows
        + "</table></div>"
        + proxy_note
    )


def _panel_table_html(panel: list) -> str:
    if not panel:
        return ""
    rows = "".join(
        f"<tr><td>{escape(_persona_name(segment))}</td><td>"
        + escape(" · ".join(_text(attr.get("value")) for attr in segment.get("attributes", [])))
        + f"</td><td>{escape(_text(segment.get('weight_display')))}</td></tr>"
        for segment in panel
    )
    return (
        '<div class="table-wrap"><table><tr><th>가상 인물</th><th>표집된 속성</th><th>세그먼트 비중</th></tr>'
        + rows
        + "</table></div>"
    )


def _matrix_table_html(review: dict[str, Any]) -> str:
    panel = review.get("panel") or []
    interviews = review.get("interviews") or []
    alternatives = review.get("alternatives") or []
    if not panel or not interviews or not alternatives:
        return ""
    by_key = {(item.get("segment_id"), item.get("policy_id")): item for item in interviews}
    head = "<tr><th>가상 인물</th>" + "".join(f"<th>{escape(_text(alt.get('label')))}</th>" for alt in alternatives) + "</tr>"
    body = []
    for segment in panel:
        cells = []
        for alt in alternatives:
            item = by_key.get((segment.get("id"), alt.get("id")))
            if item:
                key = item.get("response")
                cells.append(
                    f'<td><span class="chip" style="background:{RESPONSE_COLORS.get(key, "#777")}1f;'
                    f'color:{RESPONSE_COLORS.get(key, "#555")}">{RESPONSE_LABELS.get(key, escape(_text(key)))}</span></td>'
                )
            else:
                cells.append("<td>—</td>")
        body.append(f"<tr><td>{escape(_persona_name(segment))}</td>" + "".join(cells) + "</tr>")
    return '<div class="table-wrap"><table>' + head + "".join(body) + "</table></div>"


def _voices_html(review: dict[str, Any]) -> str:
    panel = review.get("panel") or []
    interviews = review.get("interviews") or []
    alternatives = review.get("alternatives") or []
    names = {segment.get("id"): _persona_name(segment) for segment in panel}
    blocks = []
    for alt in alternatives:
        seen: set[str] = set()
        quotes = []
        for item in interviews:
            if item.get("policy_id") != alt.get("id"):
                continue
            reason = _text(item.get("reason"))
            if not reason or reason in seen:
                continue
            seen.add(reason)
            label = RESPONSE_LABELS.get(item.get("response"), _text(item.get("response")))
            quotes.append(
                f'<blockquote><p>“{escape(reason)}”</p><footer>가상 인물 {escape(names.get(item.get("segment_id"), ""))}'
                f" · {escape(label)}"
                + (f" · 장벽: {escape(_text(item.get('barrier')))}" if item.get("barrier") else "")
                + "</footer></blockquote>"
            )
            if len(quotes) >= 2:
                break
        if quotes:
            blocks.append(f"<h3>{escape(_text(alt.get('label')))}</h3>" + "".join(quotes))
    return "".join(blocks)


def _sources_table_html(run: dict[str, Any]) -> str:
    sources = run.get("sources") or []
    if not sources:
        return "<p>저장된 출처 스냅샷이 없습니다.</p>"
    rows = "".join(
        f"<tr><td>{escape(_text(source.get('title'))[:70])}</td><td>{escape(_text(source.get('organization')))}</td>"
        f"<td>{escape(_text(source.get('observed_period')))}</td><td><code>{escape(_text(source.get('snapshot_hash'))[:12])}</code></td></tr>"
        for source in sources
    )
    return (
        '<div class="table-wrap"><table><tr><th>출처</th><th>기관</th><th>관측 시점</th><th>스냅샷 해시</th></tr>'
        + rows
        + "</table></div>"
    )


def render_html_report(run: dict[str, Any]) -> str:
    result = run.get("result") or {}
    review = result.get("policy_review") or {}
    plan = _latest_plan(run)
    panel = review.get("panel") or []
    alternatives = review.get("alternatives") or []
    approved = [item for item in run.get("constraints", []) if item.get("review_status") == "approved"]
    evidence_status = _text(result.get("status"))
    identification = result.get("identification") or {}
    rights = plan.get("rights_review")

    insights_html = _md_to_html(review.get("insights") or "") or "<p>모델 인사이트가 생성되지 않은 실행입니다.</p>"
    verdict = "가상 패널 모의 인터뷰 기준으로는 원안 단독 확정보다, 상위 대안을 포함한 소규모 시범사업 검증을 권장합니다."
    has_proxy = any(item.get("population_compatibility") != "exact" for item in approved)
    status_badge = (
        ("실측 제약 반영" + (" · 광의 대리값 포함" if has_proxy else "") + " (feasible)")
        if evidence_status == "feasible"
        else "균등 시나리오 (scenario_only)"
    )

    omitted = review.get("omitted_cells") or []
    omitted_html = ""
    if omitted:
        omitted_rows = "".join(
            "<tr><td>"
            + escape(" · ".join(_text(attr.get("value")) for attr in cell.get("attributes", [])))
            + f"</td><td>{float(cell.get('share', 0)) * 100:.1f}%</td></tr>"
            for cell in omitted
        )
        omitted_html = (
            "<h3>표본 밖 세그먼트</h3>"
            '<p class="footnote">모집단 비중이 있으나 비례 표집에서 좌석을 받지 못해 이번 표본에서 발화하지 못한 집단입니다. '
            "사각지대 해석 시 반드시 고려하세요.</p>"
            '<div class="table-wrap"><table><tr><th>속성 조합</th><th>모집단 비중</th></tr>' + omitted_rows + "</table></div>"
        )

    holdout = result.get("holdout") or {}
    evaluation = holdout.get("evaluation") or {}
    survey = result.get("survey") or {}
    sensitivity = result.get("structure_sensitivity") or []
    extra_results = ""
    if holdout:
        extra_results += (
            "<h3>봉인 홀드아웃 채점</h3><ul>"
            f"<li>예측 봉인 hash: <code>{escape(_text(holdout.get('prediction_hash'))[:16])}</code></li>"
            + (
                f"<li>Total variation distance: {_probability(evaluation.get('tv_distance'))}</li>"
                f"<li>실제 관심량: {_probability(evaluation.get('actual_estimand'))}</li>"
                f"<li>식별구간 포함 여부: {escape(_text(evaluation.get('interval_covered')))}</li>"
                if evaluation
                else "<li>아직 채점되지 않은 봉인 예측입니다.</li>"
            )
            + "</ul>"
        )
    if survey:
        extra_results += (
            "<h3>합성 설문</h3><ul>"
            f"<li>모드: {escape(_text(survey.get('mode')))} · 응답 {escape(_text(survey.get('n')))}건</li>"
            f"<li>집계: {escape(_text(survey.get('counts')))}</li></ul>"
        )
    if sensitivity:
        extra_results += "<h3>구조 민감도</h3><ul>" + "".join(
            f"<li><code>{escape(_text(item.get('id')))}</code>: {escape(_text(item.get('status')))} · 점추정 {_probability(item.get('point_estimate'))}</li>"
            for item in sensitivity
        ) + "</ul>"

    rights_html = (
        f"<p>{escape(_text(rights.get('finding')))}</p><ul>"
        + "".join(f"<li>{escape(_text(issue))}</li>" for issue in rights.get("issues", []))
        + "</ul>"
        if rights
        else "<p>별도의 고위험 기본권 경고가 탐지되지 않았습니다.</p>"
    )

    sections = f"""
<section><h2><span>1</span>핵심 요약</h2>
{_tiles_html(panel, approved, alternatives, evidence_status)}
<p class="verdict">{escape(verdict)}</p>
{_stacked_bars_html(review)}
</section>
<section><h2><span>2</span>종합 인사이트</h2>{insights_html}</section>
<section><h2><span>3</span>표본 구성과 근거</h2>
{_constraints_table_html(run)}
{_panel_table_html(panel)}
<p class="footnote">가상 인물은 결합분포에서 비례 표집한 완전 합성 세그먼트이며, 같은 속성 조합은 대표 응답을 공유합니다.</p>
{omitted_html}
</section>
<section><h2><span>4</span>가상 인물 × 정책안 반응</h2>{_matrix_table_html(review)}</section>
<section><h2><span>5</span>패널 목소리 (합성 모의 인터뷰)</h2>{_voices_html(review) or "<p>모의 인터뷰가 실행되지 않았습니다.</p>"}
<p class="footnote">모든 인용은 통계 가중 합성 세그먼트의 모델 모의 응답이며 실제 시민 발화가 아닙니다.</p>
</section>
<section><h2><span>6</span>권리·법률 사전검토</h2>{rights_html}</section>
<section><h2><span>7</span>현실 검증 계획</h2>
<ul>
<li><strong>기간</strong> 4주 소규모 시범</li>
<li><strong>비교</strong> 원안 / 상위 대안 / 지원 없음 또는 기존 서비스</li>
<li><strong>지표</strong> 신규 참여, 실제 사용, 기존 이용자 집중도, 지역·정보 접근 격차, 재이용</li>
<li><strong>수집</strong> 사전 등록한 동의 기반 실제 설문·행동 집계·이해관계자 인터뷰</li>
</ul></section>
<section><h2><span>8</span>출처 원장</h2>{_sources_table_html(run)}</section>
<section><h2><span>9</span>방법과 한계</h2>
{extra_results}
<ul>
<li>선택 구조: <code>{escape(_text(result.get("selected_model")))}</code> · 가정 없는 식별구간 {_probability(identification.get("lower"))} – {_probability(identification.get("upper"))}</li>
<li>승인된 제약만 결합분포 계산에 사용했습니다. 범주 매핑과 모집단 정합은 출처 원장과 함께 재검토해야 합니다.</li>
<li>점추정은 최대엔트로피 구조 가정에 의존하며 식별구간과 동일시할 수 없습니다.</li>
<li>합성 인물의 발화·반응은 모델 생성이며 실제 응답자·대표 표본이 아닙니다.</li>
</ul></section>"""

    question = escape(_text(run.get("question")))
    return f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>정책 검토 보고서 · E2P Agent</title><style>
  :root {{ color-scheme: light; --ink:#292925; --muted:#6e6e66; --line:#e2e1d8; --accent:#b0562f; --paper:#faf9f4; }}
  * {{ box-sizing:border-box; }}
  body {{ max-width:880px; margin:0 auto; padding:56px 34px 90px; color:var(--ink); background:var(--paper); font-family:"Apple SD Gothic Neo","Pretendard",-apple-system,sans-serif; font-size:15px; line-height:1.7; }}
  header {{ margin-bottom:40px; padding-bottom:26px; border-bottom:2px solid var(--ink); }}
  .eyebrow {{ color:var(--accent); font-size:11px; font-weight:750; letter-spacing:.14em; }}
  h1 {{ margin:10px 0 6px; font-family:Georgia,"Times New Roman","Apple SD Gothic Neo",serif; font-size:34px; letter-spacing:-.02em; }}
  .question {{ margin:0 0 14px; color:var(--muted); font-size:16px; }}
  .meta {{ display:flex; flex-wrap:wrap; gap:8px 18px; color:var(--muted); font-size:12px; }}
  .meta b {{ color:var(--ink); font-weight:600; }}
  .badge {{ display:inline-block; padding:2px 9px; border:1px solid var(--line); border-radius:999px; background:#fff; font-size:11px; }}
  .notice {{ margin-top:16px; padding:10px 14px; border-left:3px solid var(--accent); background:#fff4ee; color:#5d514b; font-size:13px; }}
  section {{ margin:38px 0; }}
  h2 {{ margin:0 0 14px; font-family:Georgia,serif; font-size:21px; letter-spacing:-.01em; }}
  h2 span {{ display:inline-block; margin-right:10px; color:var(--accent); font-size:15px; }}
  h3 {{ margin:20px 0 8px; font-size:15px; }}
  p {{ margin:0 0 10px; }}
  ul {{ margin:0 0 12px; padding-left:22px; }}
  li {{ margin-bottom:5px; }}
  .tiles {{ display:grid; grid-template-columns:repeat(3,1fr); gap:12px; margin:0 0 18px; }}
  .tile {{ padding:13px 15px; border:1px solid var(--line); border-radius:10px; background:#fff; }}
  .tile small {{ display:block; color:var(--muted); font-size:11px; }}
  .tile strong {{ display:block; margin:3px 0 1px; font-size:22px; font-family:Georgia,serif; }}
  .tile span {{ color:var(--muted); font-size:11px; }}
  .verdict {{ padding:11px 14px; border:1px solid var(--line); border-radius:10px; background:#fff; font-weight:600; }}
  .bars {{ display:grid; gap:9px; margin:14px 0 4px; }}
  .legend {{ display:flex; gap:14px; color:var(--muted); font-size:11px; }}
  .legend i {{ display:inline-block; width:10px; height:10px; margin-right:4px; border-radius:2px; vertical-align:-1px; }}
  .bar-row {{ display:grid; grid-template-columns:minmax(110px,180px) 1fr; gap:12px; align-items:center; }}
  .bar-row small {{ grid-column:2; color:var(--muted); font-size:11px; }}
  .bar-label {{ font-size:12px; }}
  .bar {{ display:flex; height:16px; overflow:hidden; border-radius:4px; background:#eceae0; }}
  .bar i {{ display:block; height:100%; }}
  .table-wrap {{ overflow-x:auto; margin:0 0 14px; }}
  table {{ width:100%; border-collapse:collapse; background:#fff; font-size:12.5px; }}
  th, td {{ padding:8px 11px; border:1px solid var(--line); text-align:left; vertical-align:top; }}
  th {{ background:#f2f1e8; font-weight:600; }}
  .chip {{ display:inline-block; padding:2px 9px; border-radius:999px; font-size:11px; font-weight:600; }}
  blockquote {{ margin:0 0 12px; padding:10px 16px; border-left:3px solid var(--line); background:#fff; }}
  blockquote p {{ margin:0 0 5px; font-style:italic; }}
  blockquote footer {{ color:var(--muted); font-size:11.5px; }}
  .callout.warn {{ padding:10px 14px; border-left:3px solid #c07f2d; background:#fdf6ea; color:#6b5a33; font-size:13px; }}
  .footnote {{ color:var(--muted); font-size:11.5px; }}
  code {{ font-family:ui-monospace,Menlo,monospace; font-size:11.5px; }}
  footer.page {{ margin-top:52px; padding-top:14px; border-top:1px solid var(--line); color:var(--muted); font-size:11.5px; }}
  @media print {{ body {{ padding:20px; }} section {{ break-inside:avoid; }} }}
  @media (max-width:640px) {{ .tiles {{ grid-template-columns:1fr; }} }}
</style></head><body>
<header>
  <span class="eyebrow">EVIDENCE-TO-PERSONA POLICY REVIEW</span>
  <h1>정책 검토 보고서</h1>
  <p class="question">{question}</p>
  <div class="meta">
    <span>생성 <b>{escape(now()[:16].replace("T", " "))} UTC</b></span>
    <span>대상 <b>{escape(_text(plan.get("target_population") or run.get("target_population")))}</b></span>
    <span>근거 수준 <span class="badge">{escape(status_badge)}</span></span>
    <span>run <code>{escape(_text(run.get("id")))}</code></span>
  </div>
  <p class="notice">{escape(REPORT_NOTICE)}</p>
</header>
{sections}
<footer class="page">E2P Agent · 로컬 provenance artifact · 합성 패널 {len(panel)}명 · 이벤트 {len(run.get("events", []))}건</footer>
</body></html>"""
