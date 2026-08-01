from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from typing import Any

from .avatars import notionists_avatar


def _clean(value: str) -> str:
    return " ".join(value.split())


def _contains_any(question: str, terms: tuple[str, ...]) -> bool:
    return any(term in question for term in terms)


def _compact_safety_text(value: str) -> str:
    """Normalize whitespace and punctuation so a safety check cannot be trivially spaced apart."""
    return re.sub(r"[\W_]+", "", value.casefold())


HIGH_IMPACT_DOMAINS = ("채용", "고용", "구직", "신용", "대출", "보험", "의료", "진료", "교육", "입학", "수사", "범죄", "지원")
INDIVIDUAL_DECISIONS = ("자동판정", "자동결정", "판정", "심사", "선별", "골라", "고르", "배제", "제한", "거절", "탈락", "등급")
SENSITIVE_ATTRIBUTES = ("우울", "정신질환", "질병", "성격", "정치성향")


def _contains_unsafe_plan_content(value: str) -> bool:
    compact = _compact_safety_text(value)
    return any(term in compact for term in SENSITIVE_ATTRIBUTES) or (
        any(term in compact for term in HIGH_IMPACT_DOMAINS)
        and (
            any(term in compact for term in INDIVIDUAL_DECISIONS)
            or ("대상자" in compact and "선정" in compact)
        )
    )


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
            "finding": "선거권 연령을 특정 지역·학생 집단에만 다르게 적용하는 정책안은 기본권·평등선거·법률유보 검토가 선행되어야 합니다.",
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


# 키워드 템플릿 변수용 공용 범주 라벨 — 폴백 플랜에서도 화면에 영어 코드가 새지 않게 한다.
COMMON_CATEGORY_LABELS = {
    "high": "높음",
    "low": "낮음",
    "manageable": "감당 가능",
    "constrained": "제한적",
    "adequate": "충분함",
    "unstable": "불안정",
    "stable": "안정적",
    "active": "활발",
    "limited": "제한적",
    "regular": "정기적",
    "occasional": "가끔",
    "none": "없음",
    "employed": "재직 중",
    "seeking": "구직 중",
    "18_27": "18~27세",
    "28_plus": "28세 이상",
}

AUDIENCE_TERMS = ("페르소나", "알고 싶", "이해하", "누구인지", "특성을 파악")
# "반응·수용"을 물으면 페르소나 언급이 있어도 모의 인터뷰가 필요한 검토 요청이다.
PLAN_REVIEW_TERMS = ("검토", "정책안", "기획안", "기획하", "평가", "개선안", "반응", "수용")


def _request_type(question: str) -> str:
    """Route audience understanding to a panel-only flow; default to plan review."""
    if _contains_any(question, PLAN_REVIEW_TERMS):
        return "plan_review"
    if _contains_any(question, AUDIENCE_TERMS):
        return "audience_understanding"
    return "plan_review"


def _is_unsafe(question: str) -> str | None:
    compact = _compact_safety_text(question)
    political = _contains_any(compact, ("보수", "진보", "지지자", "투표", "선거")) and _contains_any(
        compact, ("설득", "조작", "공략", "골라", "고르")
    )
    coercion = _contains_any(compact, ("강제로", "반드시", "유인해서", "불이익", "배제"))
    sensitive_inference = _contains_any(compact, SENSITIVE_ATTRIBUTES) and _contains_any(
        compact, ("예측", "추론", "골라", "고르", "선별")
    )
    high_impact_decision = any(term in compact for term in HIGH_IMPACT_DOMAINS) and (
        any(term in compact for term in INDIVIDUAL_DECISIONS) or ("대상자" in compact and "선정" in compact)
    )
    if political:
        return (
            "정치적 설득·조작을 위한 집단 선별은 지원하지 않습니다. 보편적이고 비강압적인 정책 접근성 검토로 바꾸세요."
        )
    if coercion:
        return "강압·배제·불이익을 전제로 한 정책 최적화는 지원하지 않습니다. 자발적 접근성과 형평성 검토로 바꾸세요."
    if sensitive_inference:
        return "인구통계로 민감 특성을 추론하거나 표적화하는 요청은 지원하지 않습니다. 집계 수준의 비식별 서비스 접근성 분석으로 바꾸세요."
    if high_impact_decision:
        return (
            "개인별 고위험 판단·자격 선별은 지원하지 않습니다. 집계 수준에서 접근 장벽과 정보 제공 방식을 검토하세요."
        )
    return None


DEFAULT_INTERVIEW_QUESTIONS = [
    "이 정책안을 이용할 의향이 있나요?",
    "이용 또는 미이용의 가장 큰 장벽은 무엇인가요?",
    "어떤 조건이 바뀌면 이용 가능성이 달라질까요?",
    "이 정책에서 빠질 가능성이 큰 집단은 누구인가요?",
    "정책을 더 공정하고 접근 가능하게 바꾸려면 무엇이 필요할까요?",
]


def _normalized_llm_plan(raw: object) -> dict[str, Any] | None:
    """Validate a model-designed plan; any violation falls back to the keyword templates."""
    if not isinstance(raw, dict):
        return None
    if _contains_unsafe_plan_content(str(raw)):
        return None
    request_type = raw.get("request_type")
    if request_type not in ("plan_review", "audience_understanding"):
        request_type = None  # 무효 값은 키워드 폴백에 맡긴다
    variables_raw = raw.get("variables")
    if not isinstance(variables_raw, list) or not 2 <= len(variables_raw) <= 4:
        return None
    variables: list[dict[str, Any]] = []
    for item in variables_raw:
        if not isinstance(item, dict):
            return None
        var_id = re.sub(r"\s+", "_", str(item.get("id", "")).strip())[:60]
        label = str(item.get("label", "")).strip()
        categories_raw = item.get("categories")
        if not var_id or not label:
            return None
        if not isinstance(categories_raw, list) or not 2 <= len(categories_raw) <= 4:
            return None
        categories = []
        category_labels: dict[str, str] = {}
        for category in categories_raw:
            if isinstance(category, dict):
                # 플랜 모델이 {code,label} 객체로 주는 경우 — code는 계산용, label은 표시용으로 분리 보존한다.
                code = str(category.get("code") or category.get("id") or category.get("label") or "").strip()
                category_label = str(category.get("label") or code).strip()
            else:
                code = str(category).strip()
                category_label = code
            categories.append(code)
            category_labels[code] = category_label
        if not all(categories) or len(set(categories)) != len(categories):
            return None
        variables.append(
            {"id": var_id, "label": label, "categories": categories, "category_labels": category_labels}
        )
    if len({item["id"] for item in variables}) != len(variables):
        return None
    alternatives_raw = raw.get("alternatives")
    if not isinstance(alternatives_raw, list) or not 1 <= len(alternatives_raw) <= 3:
        return None
    alternatives: list[tuple[str, str, str]] = []
    for item in alternatives_raw:
        if not isinstance(item, dict) or not str(item.get("label", "")).strip():
            return None
        alternatives.append(
            (
                str(item["label"]).strip(),
                str(item.get("hypothesis", "")).strip() or "가상 패널에서 수혜 분포와 접근 장벽을 비교합니다.",
                str(item.get("risk", "")).strip() or "실제 시민 반응이나 인과효과를 뜻하지 않습니다.",
            )
        )
    queries = [str(query).strip() for query in raw.get("evidence_queries") or [] if str(query).strip()][:6]
    kosis_terms = [
        str(term).strip()
        for term in raw.get("kosis_search_terms") or []
        if isinstance(term, str) and 2 <= len(str(term).strip()) <= 30
    ][:4]
    questions = [str(question).strip() for question in raw.get("interview_questions") or [] if str(question).strip()][:6]
    assumptions = [
        {"field": str(item.get("field", "")), "value": str(item.get("value", "")), "reason": str(item.get("reason", ""))}
        for item in (raw.get("assumptions") or [])
        if isinstance(item, dict) and str(item.get("field", "")).strip()
    ]
    rights_raw = raw.get("rights_review")
    rights_review = None
    if isinstance(rights_raw, dict) and str(rights_raw.get("finding", "")).strip():
        rights_review = {
            "severity": str(rights_raw.get("severity", "medium")),
            "finding": str(rights_raw["finding"]).strip(),
            "issues": [str(issue).strip() for issue in rights_raw.get("issues") or [] if str(issue).strip()][:6],
        }
    focus = str(raw.get("policy_focus", "")).strip()
    if not focus or not queries:
        return None
    return {
        "request_type": request_type,
        "policy_focus": focus,
        "target_population": str(raw.get("target_population", "")).strip(),
        "variables": variables,
        "alternatives": alternatives,
        "evidence_queries": queries,
        "kosis_search_terms": kosis_terms,
        "interview_questions": questions,
        "assumptions": assumptions,
        "rights_review": rights_review,
    }


def build_policy_plan(question: str, fallback_target: str, llm_raw: object = None) -> dict[str, Any]:
    question = _clean(question)
    blocked_reason = _is_unsafe(question)
    theme = _theme(question)
    target, assumptions = _target(question, fallback_target)
    plan_id = f"plan_{hashlib.sha256(question.encode()).hexdigest()[:12]}"
    llm_plan = _normalized_llm_plan(llm_raw) if not blocked_reason else None
    if llm_plan:
        plan_source = "llm_designed"
        policy_domain = "llm_designed"
        policy_focus = llm_plan["policy_focus"]
        variables = llm_plan["variables"]
        alternative_tuples = llm_plan["alternatives"]
        queries = llm_plan["evidence_queries"]
        kosis_search_terms = llm_plan["kosis_search_terms"]
        interview_questions = llm_plan["interview_questions"] or DEFAULT_INTERVIEW_QUESTIONS
        target = llm_plan["target_population"] or target
        assumptions = assumptions + llm_plan["assumptions"]
        rights_review = llm_plan["rights_review"] or theme.get("rights_review")
    else:
        plan_source = "keyword_template"
        policy_domain = theme["id"]
        policy_focus = theme["label"]
        kosis_search_terms = [theme["label"]]
        variables = [
            {
                **item,
                "category_labels": {code: COMMON_CATEGORY_LABELS.get(code, code) for code in item["categories"]},
            }
            for item in theme["variables"]
        ]
        alternative_tuples = theme["alternatives"]
        interview_questions = DEFAULT_INTERVIEW_QUESTIONS
        rights_review = theme.get("rights_review")
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
    alternatives = [
        {
            "id": "original",
            "label": (question if len(question) <= 26 else question[:26].rstrip() + "…") + " (검토 요청안)",
            "description": question,
            "hypothesis": "검토를 요청받은 정책의 수혜 분포와 접근 장벽을 가상 패널에서 비교합니다.",
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
                "origin": "planner_llm" if llm_plan else "planner_narrative",
            }
            for index, (label, hypothesis, risk) in enumerate(alternative_tuples, start=1)
        ],
    ]
    return {
        "id": plan_id,
        "status": "SAFETY_BLOCKED" if blocked_reason else "COMPLETED_WITH_ASSUMPTIONS" if assumptions else "PLANNED",
        "request_type": (llm_plan or {}).get("request_type") or _request_type(question),
        "plan_source": plan_source,
        "policy_domain": policy_domain,
        "policy_focus": policy_focus,
        "target_population": target,
        "proposed_variables": variables,
        "assumptions": assumptions,
        "blocked_reason": blocked_reason,
        "alternatives": alternatives,
        "interview_questions": interview_questions,
        "evidence_queries": queries,
        "kosis_search_terms": kosis_search_terms,
        "review_gates": [
            "통계표의 모집단·시점·범주가 대상 정의와 호환되는지 검토",
            "수치·분모·가중 여부가 확인된 후보만 제약으로 승인",
            "가상 패널의 응답은 실제 찬성률·정책 효과로 해석하지 않음",
        ],
        "rights_review": rights_review,
    }


SYNTHETIC_SEGMENT_NAMES = (
    "가온", "나래", "다온", "라온", "마루", "보라", "새론", "아람", "이든", "재이", "하늘", "온유",
)


def labeled_attributes(
    state: dict[str, str],
    variable_labels: dict[str, str] | None = None,
    category_labels: dict[str, dict[str, str]] | None = None,
) -> list[dict[str, str]]:
    variable_labels = variable_labels or {}
    category_labels = category_labels or {}
    return [
        {
            "variable": variable_labels.get(key, key),
            "variable_code": key,
            "value": category_labels.get(key, {}).get(value, value),
            "code": value,
            "tag": "model_weighted",
        }
        for key, value in state.items()
    ]


def weighted_segments(
    states: list[dict[str, str]],
    distribution: list[float],
    limit: int = 12,
    evidence_level: str = "partial_estimate",
    variable_labels: dict[str, str] | None = None,
    category_labels: dict[str, dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    # 같은 가중치 셀만 상위에 몰리면 차등 분포가 있어도 균일해 보인다 —
    # 가중치 티어별 라운드로빈으로 뽑아 패널이 분포의 실제 모양을 드러내게 한다.
    tiers: dict[float, list[tuple[dict[str, str], float]]] = {}
    for state, weight in sorted(zip(states, distribution, strict=True), key=lambda item: item[1], reverse=True):
        tiers.setdefault(round(float(weight), 6), []).append((state, float(weight)))
    ranked: list[tuple[dict[str, str], float]] = []
    while len(ranked) < limit and any(tiers.values()):
        for key in sorted(tiers, reverse=True):
            if tiers[key]:
                ranked.append(tiers[key].pop(0))
                if len(ranked) >= limit:
                    break
    return [
        {
            "id": f"P{index:02d}",
            "display_name": SYNTHETIC_SEGMENT_NAMES[(index - 1) % len(SYNTHETIC_SEGMENT_NAMES)],
            "avatar": notionists_avatar(f"P{index:02d}"),
            "attributes": labeled_attributes(state, variable_labels, category_labels),
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


RESPONSE_LABELS = {"support": "긍정", "conditional": "조건부", "low_change": "변화 낮음", "decline": "거부"}


def _panel_voice_lines(
    plan: dict[str, Any], panel: list[dict[str, Any]], interviews: list[dict[str, Any]]
) -> list[str]:
    weights = {item["id"]: float(item.get("cell_share", item.get("weight", 0.0))) for item in panel}
    names = {item["id"]: item.get("display_name") or item["id"] for item in panel}
    lines: list[str] = []
    for option in plan.get("alternatives", []):
        answers = []
        seen_reasons: set[str] = set()
        for item in sorted(
            (entry for entry in interviews if entry.get("policy_id") == option["id"]),
            key=lambda entry: weights.get(entry.get("segment_id"), 0.0),
            reverse=True,
        ):
            reason = str(item.get("reason", ""))
            if reason in seen_reasons:
                continue
            seen_reasons.add(reason)
            answers.append(item)
            if len(answers) >= 2:
                break
        for item in answers:
            label = RESPONSE_LABELS.get(item.get("response"), item.get("response"))
            speaker = names.get(item.get("segment_id"), item.get("segment_id"))
            lines.append(
                f"- **{option['label']} · 가상 인물 {speaker} ({label})** — “{item.get('reason', '')}”"
                + (f" / 장벽: {item.get('barrier')}" if item.get("barrier") else "")
            )
    return lines


def policy_brief(
    plan: dict[str, Any],
    panel: list[dict[str, Any]],
    interviews: list[dict[str, Any]],
    insights: str | None = None,
) -> str:
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
        if any(
            attr.get("code", attr["value"]) in {"high", "constrained", "unstable", "none", "seeking"}
            for attr in item["attributes"]
        )
    ]
    return (
        "\n".join(
            [
                "# 정책 사전검증 브리프",
                "",
                f"## 대상\n{plan['target_population']}",
                "",
                "## 판정\n가상 패널 모의 인터뷰 기준으로는 요청받은 정책안 단독 확정보다, 상위 대안을 포함한 소규모 시범사업 검증을 권장합니다.",
                "",
                *([insights.replace("### ", "## "), ""] if insights else []),
                "## 우선 검토안\n"
                + (
                    f"{preferred['label']} — {preferred['hypothesis']}"
                    if preferred
                    else "검증된 인터뷰 응답이 없어 정책안 순위를 만들지 않았습니다. 권리·법률 검토와 실제 시범조사를 우선하세요."
                ),
                "",
                "## 패널 목소리 (합성 모의 인터뷰)\n"
                + (
                    "\n".join(_panel_voice_lines(plan, panel, interviews))
                    + "\n\n모든 인용은 통계 가중 합성 세그먼트의 모델 모의 응답이며 실제 시민 발화가 아닙니다."
                    if _panel_voice_lines(plan, panel, interviews)
                    else "모의 인터뷰가 실행되지 않아 인용할 패널 목소리가 없습니다."
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
                "## 현실 검증 계획\n- 기간: 4주 소규모 시범\n- 비교: 검토 요청안 / 상위 대안 / 지원 없음 또는 기존 서비스\n- 지표: 신규 참여, 실제 사용, 기존 이용자 집중도, 지역·정보 접근 격차, 재이용\n- 수집: 사전 등록한 동의 기반 실제 설문·행동 집계·이해관계자 인터뷰",
                "",
                "> 이 문서는 통계 기반 가상 패널의 모의 인터뷰입니다. 실제 시민의 찬성률·행동·정책 효과가 아닙니다.",
            ]
        )
        + "\n"
    )
