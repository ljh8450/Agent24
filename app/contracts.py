from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from itertools import product
from typing import Any
from uuid import uuid4

from .errors import DomainError


def now() -> str:
    return datetime.now(UTC).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:16]}"


@dataclass(frozen=True)
class Variable:
    id: str
    label: str
    categories: tuple[str, ...]

    @staticmethod
    def parse(value: dict[str, Any]) -> Variable:
        variable_id = str(value.get("id", "")).strip()

        def category_code(item: Any) -> str:
            # 플랜 모델이 categories를 [{code,label}] 객체로 돌려주는 경우가 있다 —
            # dict를 그대로 str()하면 추출·패널·보고서 전부가 깨진 카테고리로 오염된다.
            if isinstance(item, dict):
                item = item.get("code") or item.get("id") or item.get("label") or ""
            return str(item).strip()

        categories = tuple(category_code(item) for item in value.get("categories", []))
        if not variable_id or len(categories) < 2 or len(set(categories)) != len(categories):
            raise DomainError("INVALID_VARIABLE", "변수는 고유 ID와 중복 없는 두 개 이상의 범주가 필요합니다.")
        return Variable(variable_id, str(value.get("label") or variable_id), categories)


def parse_variables(value: list[dict[str, Any]]) -> list[Variable]:
    variables = [Variable.parse(item) for item in value]
    if not 1 <= len(variables) <= 7 or len({item.id for item in variables}) != len(variables):
        raise DomainError("INVALID_VARIABLE_SCHEMA", "변수는 고유한 1–7개 이산 변수여야 합니다.")
    if len(list(product(*(item.categories for item in variables)))) > 4096:
        raise DomainError("STATE_SPACE_TOO_LARGE", "결합 셀은 4096개를 넘을 수 없습니다. 범주 또는 변수를 줄이세요.")
    return variables


def validate_where(where: dict[str, Any], variables: list[Variable]) -> dict[str, str]:
    known = {item.id: set(item.categories) for item in variables}
    if not isinstance(where, dict) or not where:
        raise DomainError("INVALID_PREDICATE", "제약과 관심량에는 비어 있지 않은 범주 조건이 필요합니다.")
    normalized = {str(key): str(value) for key, value in where.items()}
    for key, value in normalized.items():
        if key not in known or value not in known[key]:
            raise DomainError("INVALID_PREDICATE", f"알 수 없는 변수 또는 범주입니다: {key}={value}")
    return normalized


def states_for(variables: list[Variable]) -> list[dict[str, str]]:
    return [
        dict(zip((item.id for item in variables), values, strict=True))
        for values in product(*(item.categories for item in variables))
    ]


def matches(state: dict[str, str], where: dict[str, str]) -> bool:
    return all(state.get(key) == value for key, value in where.items())


@dataclass
class Constraint:
    id: str
    label: str
    source_id: str
    where: dict[str, str]
    relation: str
    value: float
    population_compatibility: str
    raw_statement: str
    review_status: str = "candidate"
    reviewed_at: str | None = None
    override_note: str | None = None
    source_categories: dict[str, str] = field(default_factory=dict)
    mapping_note: str = ""

    @staticmethod
    def parse(value: dict[str, Any], variables: list[Variable]) -> Constraint:
        relation = str(value.get("relation", "eq"))
        number = value.get("value")
        try:
            number = float(number)
        except (TypeError, ValueError) as error:
            raise DomainError("INVALID_CONSTRAINT", "제약 value는 0과 1 사이의 수여야 합니다.") from error
        compatibility = str(value.get("population_compatibility", "overlap_unknown"))
        if relation not in {"eq", "gte", "lte"} or not 0 <= number <= 1:
            raise DomainError("INVALID_CONSTRAINT", "relation은 eq/gte/lte이고 value는 0–1이어야 합니다.")
        if compatibility not in {"exact", "restricted", "broader", "overlap_unknown", "incompatible"}:
            raise DomainError("INVALID_COMPATIBILITY", "모집단 호환성 값이 올바르지 않습니다.")
        source_id = str(value.get("source_id", "")).strip()
        if not source_id:
            raise DomainError("MISSING_SOURCE", "제약에는 저장된 source_id가 필요합니다.")
        return Constraint(
            id=str(value.get("id") or new_id("con")),
            label=str(value.get("label") or "unnamed constraint"),
            source_id=source_id,
            where=validate_where(value.get("where", {}), variables),
            relation=relation,
            value=number,
            population_compatibility=compatibility,
            raw_statement=str(value.get("raw_statement") or ""),
            review_status=str(value.get("review_status") or "candidate"),
            reviewed_at=value.get("reviewed_at"),
            override_note=value.get("override_note"),
            source_categories={str(key): str(item) for key, item in value.get("source_categories", {}).items()},
            mapping_note=str(value.get("mapping_note") or ""),
        )

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Source:
    id: str
    url: str
    title: str
    organization: str
    survey_name: str
    published_at: str
    observed_period: str
    population: str
    sample_size: int | None
    snapshot_hash: str
    snapshot_path: str
    fetched_at: str = field(default_factory=now)
    trust_tier: str = "unreviewed_web"
    source_kind: str = "web_page"
    source_domain: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
