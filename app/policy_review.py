from __future__ import annotations

import hashlib
from collections import defaultdict
from typing import Any

from .avatars import notionists_avatar


def _clean(value: str) -> str:
    return " ".join(value.split())


def _contains_any(question: str, terms: tuple[str, ...]) -> bool:
    return any(term in question for term in terms)


THEMES: tuple[dict[str, Any], ...] = (
    {
        "id": "democratic_rights",
        "terms": ("투표권", "선거권", "참정권", "투표 자격", "선거 연령"),
        "label": "선거권·참정권",
        "variables": [
            {"id": "age_eligibility_band", "label": "연령 자격 구간", "categories": ["18_27", "28_plus"]},
            {"id": "registration_access", "label": "선거 등록·정보 접근", "categories": ["constrained", "adequate"]},
            {"id": "civic_participation", "label": "시민 참여", "categories": ["active", "limited"]},
        ],
        "alternatives": [
            (
                "동일 선거연령 유지·시민정보 지원",
                "거주지나 학생 신분에 따른 권리 차등 없이 정보·접근 장벽만 낮춥니다.",
                "권리 제한을 정책수단으로 쓰지 않으며 별도 참여 효과 측정이 필요합니다.",
            ),
            (
                "보편적 참여지원 시범사업",
                "모든 같은 연령대에 동일하게 적용되는 비강압적 참여지원의 효과를 검증합니다.",
                "참여지원이 특정 정치적 선택 유도로 변질되지 않도록 독립 감시가 필요합니다.",
            ),
        ],
        "rights_review": {
            "severity": "high",
            "finding": "선거권 연령을 특정 지역·학생 집단에만 다르게 적용하는 원안은 기본권·평등선거·법률유보 검토가 선행되어야 합니다.",
            "issues": [
                "거주지와 학생 신분을 기준으로 한 선거권 차등의 합리적 이유",
                "공직선거법상 선거권 연령과 지방자치단체의 권한 범위",
                "필요성·비례성·최소침해 및 대체수단 존재 여부",
                "영향받는 18–27세 성인의 권리구제와 참여 절차",
            ],
        },
    },
    {
        "id": "culture",
        "terms": ("영화", "문화", "공연", "전시", "관람"),
        "label": "문화생활 참여",
        "variables": [
            {
                "id": "existing_participation",
                "label": "기존 문화 참여",
                "categories": ["regular", "occasional", "none"],
            },
            {"id": "price_barrier", "label": "가격 장벽", "categories": ["high", "manageable"]},
            {"id": "access_barrier", "label": "시간·거리 접근 장벽", "categories": ["high", "manageable"]},
        ],
        "alternatives": [
            (
                "첫 이용 정액 쿠폰",
                "기존 이용자 보조보다 신규 참여를 우선 검증합니다.",
                "반복 이용 지원이 약할 수 있습니다.",
            ),
            (
                "선택형 문화 크레딧",
                "영화·공연·전시 선택권으로 선호 불일치를 줄입니다.",
                "운영과 정산이 복잡해질 수 있습니다.",
            ),
        ],
    },
    {
        "id": "housing",
        "terms": ("주거", "월세", "임대", "전세", "집", "청년주택"),
        "label": "주거 안정",
        "variables": [
            {"id": "housing_cost_pressure", "label": "주거비 부담", "categories": ["high", "manageable"]},
            {"id": "housing_stability", "label": "주거 안정성", "categories": ["unstable", "stable"]},
            {"id": "support_access", "label": "지원 접근성", "categories": ["constrained", "adequate"]},
        ],
        "alternatives": [
            ("초기비용 정액 지원", "계약·이사 초기의 현금 장벽을 직접 다룹니다.", "단기 보조에 그칠 수 있습니다."),
            (
                "정보·신청 동행 지원",
                "자격·신청 절차 장벽을 낮추는지 검증합니다.",
                "공급 부족 자체를 해소하지는 못합니다.",
            ),
        ],
    },
    {
        "id": "mobility",
        "terms": ("교통", "버스", "지하철", "통근", "이동"),
        "label": "이동 접근성",
        "variables": [
            {"id": "transport_cost_pressure", "label": "교통비 부담", "categories": ["high", "manageable"]},
            {"id": "service_access", "label": "서비스 접근성", "categories": ["constrained", "adequate"]},
            {"id": "travel_time_pressure", "label": "이동 시간 부담", "categories": ["high", "manageable"]},
        ],
        "alternatives": [
            (
                "저소득 우선 교통 크레딧",
                "가격 장벽이 큰 집단에 자원을 집중합니다.",
                "시간·노선 장벽은 남을 수 있습니다.",
            ),
            (
                "시간대·연계 이동 지원",
                "통근·통학 시간 장벽을 별도로 줄이는지 검증합니다.",
                "운영 복잡도가 높아질 수 있습니다.",
            ),
        ],
    },
    {
        "id": "employment",
        "terms": ("일자리", "취업", "고용", "구직", "청년수당"),
        "label": "고용·구직 지원",
        "variables": [
            {"id": "employment_status", "label": "고용 상태", "categories": ["employed", "seeking"]},
            {"id": "income_stability", "label": "소득 안정성", "categories": ["unstable", "stable"]},
            {"id": "service_access", "label": "지원 서비스 접근성", "categories": ["constrained", "adequate"]},
        ],
        "alternatives": [
            ("단계형 구직 지원", "초기 탐색·면접·정착 비용을 단계별로 검증합니다.", "행정 절차가 길어질 수 있습니다."),
            (
                "지역 일경험 연계",
                "정보 제공을 실제 지역 기회와 연결하는지 검증합니다.",
                "참여 가능한 사업장 편중 위험이 있습니다.",
            ),
        ],
    },
)

DEFAULT_THEME: dict[str, Any] = {
    "id": "civic",
    "label": "공공서비스 접근",
    "variables": [
        {"id": "service_need", "label": "서비스 필요도", "categories": ["high", "low"]},
        {"id": "access_barrier", "label": "접근 장벽", "categories": ["high", "manageable"]},
        {"id": "information_access", "label": "정보 접근성", "categories": ["constrained", "adequate"]},
    ],
    "alternatives": [
        (
            "우선 대상 집중 지원",
            "필요도가 높은 집단에 자원이 도달하는지 검증합니다.",
            "누락된 경계 집단이 생길 수 있습니다.",
        ),
        ("신청·정보 접근 개선", "자격과 절차 장벽을 줄이는지 검증합니다.", "핵심 공급 부족은 남을 수 있습니다."),
    ],
}


def _theme(question: str) -> dict[str, Any]:
    return next((item for item in THEMES if _contains_any(question, item["terms"])), DEFAULT_THEME)


def _target(question: str, fallback: str) -> tuple[str, list[dict[str, str]]]:
    assumptions: list[dict[str, str]] = []
    labels: list[str] = []
    region = next(
        (
            name
            for name in (
                "서울",
                "수원",
                "부산",
                "대구",
                "인천",
                "광주",
                "대전",
                "울산",
                "세종",
                "경기",
                "강원",
                "충북",
                "충남",
                "전북",
                "전남",
                "경북",
                "경남",
                "제주",
            )
            if name in question
        ),
        None,
    )
    if region:
        labels.append(region)
    else:
        labels.append("대한민국")
        assumptions.append(
            {"field": "지역", "value": "대한민국 전체", "reason": "입력에 지역이 없어 전국 범위를 임시로 사용"}
        )
    if "20대" in question:
        labels.append("20대")
    elif "청년" in question:
        labels.append("청년")
    elif "대학생" in question or "대학" in question:
        labels.append("대학 재학생")
    else:
        labels.append("성인")
        assumptions.append({"field": "연령", "value": "성인", "reason": "대화형 합성 패널의 기본 안전 범위"})
    if "1인 가구" in question or "1인가구" in question:
        labels.append("1인 가구")
    if "남성" in question:
        labels.append("남성")
    elif "여성" in question:
        labels.append("여성")
    if not any(year in question for year in ("2024", "2025", "2026", "2027")):
        assumptions.append(
            {
                "field": "기준 시점",
                "value": "가장 최근 공개 통계",
                "reason": "입력에 기준연도가 없어 최신값을 우선 탐색",
            }
        )
    return " · ".join(labels) if labels else fallback, assumptions


AUDIENCE_TERMS = ("페르소나", "알고 싶", "이해하", "누구인지", "특성을 파악")


def _request_type(question: str) -> str:
    """대상 이해(패널만) vs 기획안 검토(패널+모의 인터뷰). 기본값은 검토."""
    return "audience_understanding" if _contains_any(question, AUDIENCE_TERMS) else "plan_review"


def _is_unsafe(question: str) -> str | None:
    lowered = question.lower()
    political = _contains_any(lowered, ("보수", "진보", "지지자", "투표", "선거")) and _contains_any(
        lowered, ("설득", "조작", "공략", "골라")
    )
    coercion = _contains_any(lowered, ("강제로", "반드시", "유인해서", "불이익", "배제"))
    sensitive_inference = _contains_any(lowered, ("우울", "정신질환", "질병", "성격", "정치성향")) and _contains_any(
        lowered, ("예측", "추론", "골라")
    )
    if political:
        return (
            "정치적 설득·조작을 위한 집단 선별은 지원하지 않습니다. 보편적이고 비강압적인 정책 접근성 검토로 바꾸세요."
        )
    if coercion:
        return "강압·배제·불이익을 전제로 한 정책 최적화는 지원하지 않습니다. 자발적 접근성과 형평성 검토로 바꾸세요."
    if sensitive_inference:
        return "인구통계로 민감 특성을 추론하거나 표적화하는 요청은 지원하지 않습니다. 집계 수준의 비식별 서비스 접근성 분석으로 바꾸세요."
    return None


def build_policy_plan(question: str, fallback_target: str) -> dict[str, Any]:
    question = _clean(question)
    blocked_reason = _is_unsafe(question)
    theme = _theme(question)
    target, assumptions = _target(question, fallback_target)
    plan_id = f"plan_{hashlib.sha256(question.encode()).hexdigest()[:12]}"
    alternatives = [
        {
            "id": "original",
            "label": "원안",
            "description": question,
            "hypothesis": "원안의 수혜 분포와 접근 장벽을 가상 패널에서 비교합니다.",
            "risk": "실제 시민 반응이나 인과효과를 뜻하지 않습니다.",
            "origin": "user_input",
        },
        *[
            {
                "id": f"alternative_{index}",
                "label": label,
                "description": label,
                "hypothesis": hypothesis,
                "risk": risk,
                "origin": "planner_narrative",
            }
            for index, (label, hypothesis, risk) in enumerate(theme["alternatives"], start=1)
        ],
    ]
    queries = [
        f"KOSIS {target} {theme['label']} 통계",
        f"공공데이터포털 {target} {theme['label']} 정책",
        f"국회입법조사처 {theme['label']} 정책 보고서",
        f"ScienceON {target} {theme['label']} 연구",
    ]
    if theme["id"] == "democratic_rights":
        queries = [
            "국가법령정보센터 공직선거법 선거권 연령",
            "중앙선거관리위원회 선거권 연령 대학생",
            "국회입법조사처 선거권 연령 평등선거",
            f"KOSIS {target} 연령별 인구 대학생",
        ]
    return {
        "id": plan_id,
        "status": "SAFETY_BLOCKED" if blocked_reason else "COMPLETED_WITH_ASSUMPTIONS" if assumptions else "PLANNED",
        "request_type": _request_type(question),
        "policy_domain": theme["id"],
        "policy_focus": theme["label"],
        "target_population": target,
        "proposed_variables": theme["variables"],
        "assumptions": assumptions,
        "blocked_reason": blocked_reason,
        "alternatives": alternatives,
        "interview_questions": [
            "이 정책안을 이용할 의향이 있나요?",
            "이용 또는 미이용의 가장 큰 장벽은 무엇인가요?",
            "어떤 조건이 바뀌면 이용 가능성이 달라질까요?",
            "이 정책에서 빠질 가능성이 큰 집단은 누구인가요?",
            "정책을 더 공정하고 접근 가능하게 바꾸려면 무엇이 필요할까요?",
        ],
        "evidence_queries": queries,
        "review_gates": [
            "통계표의 모집단·시점·범주가 대상 정의와 호환되는지 검토",
            "수치·분모·가중 여부가 확인된 후보만 제약으로 승인",
            "가상 패널의 응답은 실제 찬성률·정책 효과로 해석하지 않음",
        ],
        "rights_review": theme.get("rights_review"),
    }


def weighted_segments(
    states: list[dict[str, str]],
    distribution: list[float],
    limit: int = 12,
    evidence_level: str = "partial_estimate",
) -> list[dict[str, Any]]:
    ranked = sorted(zip(states, distribution, strict=True), key=lambda item: item[1], reverse=True)[:limit]
    return [
        {
            "id": f"P{index:02d}",
            "avatar": notionists_avatar(f"P{index:02d}"),
            "attributes": [{"variable": key, "value": value, "tag": "model_weighted"} for key, value in state.items()],
            "weight": float(weight),
            "weight_display": f"{weight * 100:.1f}%",
            "evidence_level": evidence_level,
            "boundary": (
                "승인된 정량 제약이 없어 균등 시나리오 가중치를 사용했습니다. 모집단 추정치가 아닙니다."
                if evidence_level == "scenario_only"
                else "가중치는 승인된 제약과 선택한 PGM 점모형의 합성 패널 비중이며 실제 개인의 확률이나 집단의 의견이 아닙니다."
            ),
        }
        for index, (state, weight) in enumerate(ranked, start=1)
    ]


def summarize_panel_interviews(panel: list[dict[str, Any]], interviews: list[dict[str, Any]]) -> dict[str, Any]:
    by_option: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for interview in interviews:
        segment = next((item for item in panel if item["id"] == interview.get("segment_id")), None)
        if not segment:
            continue
        by_option[str(interview.get("policy_id"))][str(interview.get("response"))] += float(segment["weight"])
    return {
        policy_id: {key: round(value, 8) for key, value in counts.items()} for policy_id, counts in by_option.items()
    }


def policy_brief(plan: dict[str, Any], panel: list[dict[str, Any]], interviews: list[dict[str, Any]]) -> str:
    summary = summarize_panel_interviews(panel, interviews)
    alternatives = {item["id"]: item for item in plan["alternatives"]}
    ranked = sorted(
        summary.items(), key=lambda item: item[1].get("support", 0.0) + item[1].get("conditional", 0.0), reverse=True
    )
    preferred_id = ranked[0][0] if ranked else None
    preferred = alternatives.get(preferred_id) if preferred_id else None
    low_access = [
        item
        for item in panel
        if any(attr["value"] in {"high", "constrained", "unstable", "none", "seeking"} for attr in item["attributes"])
    ]
    return (
        "\n".join(
            [
                "# 정책 사전검증 브리프",
                "",
                f"## 대상\n{plan['target_population']}",
                "",
                "## 판정\n가상 패널 모의 인터뷰 기준으로는 원안 단독 확정보다, 상위 대안을 포함한 소규모 시범사업 검증을 권장합니다.",
                "",
                "## 우선 검토안\n"
                + (
                    f"{preferred['label']} — {preferred['hypothesis']}"
                    if preferred
                    else "검증된 인터뷰 응답이 없어 정책안 순위를 만들지 않았습니다. 권리·법률 검토와 실제 시범조사를 우선하세요."
                ),
                "",
                "## 권리·법률 사전검토\n"
                + (
                    f"{plan['rights_review']['finding']}\n- " + "\n- ".join(plan["rights_review"].get("issues", []))
                    if plan.get("rights_review")
                    else "별도의 고위험 기본권 경고가 탐지되지 않았습니다."
                ),
                "",
                "## 사각지대 가설\n"
                + (
                    f"접근·비용·안정성 장벽을 가진 상위 {len(low_access)}개 가중 세그먼트를 별도로 측정하세요."
                    if low_access
                    else "현재 PGM 변수만으로 사각지대를 특정할 수 없습니다. 추가 교차표와 실제 인터뷰가 필요합니다."
                ),
                "",
                "## 현실 검증 계획\n- 기간: 4주 소규모 시범\n- 비교: 원안 / 상위 대안 / 지원 없음 또는 기존 서비스\n- 지표: 신규 참여, 실제 사용, 기존 이용자 집중도, 지역·정보 접근 격차, 재이용\n- 수집: 사전 등록한 동의 기반 실제 설문·행동 집계·이해관계자 인터뷰",
                "",
                "> 이 문서는 통계 기반 가상 패널의 모의 인터뷰입니다. 실제 시민의 찬성률·행동·정책 효과가 아닙니다.",
            ]
        )
        + "\n"
    )
