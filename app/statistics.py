from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from typing import Any

import numpy as np
from scipy.optimize import LinearConstraint, linprog, minimize

from .contracts import Constraint, Variable, matches, states_for
from .errors import DomainError

TOLERANCE = 1e-8


@dataclass
class Problem:
    states: list[dict[str, str]]
    a_eq: np.ndarray
    b_eq: np.ndarray
    a_ub: np.ndarray
    b_ub: np.ndarray
    labels: list[str]


def _row(states: list[dict[str, str]], where: dict[str, str]) -> np.ndarray:
    return np.array([1.0 if matches(state, where) else 0.0 for state in states])


def build_problem(variables: list[Variable], constraints: list[Constraint]) -> Problem:
    states = states_for(variables)
    a_eq: list[np.ndarray] = [np.ones(len(states))]
    b_eq: list[float] = [1.0]
    a_ub: list[np.ndarray] = []
    b_ub: list[float] = []
    labels: list[str] = ["probability_normalization"]
    for constraint in constraints:
        row = _row(states, constraint.where)
        labels.append(constraint.id)
        if constraint.relation == "eq":
            a_eq.append(row)
            b_eq.append(constraint.value)
        elif constraint.relation == "lte":
            a_ub.append(row)
            b_ub.append(constraint.value)
        else:
            a_ub.append(-row)
            b_ub.append(-constraint.value)
    return Problem(states, np.array(a_eq), np.array(b_eq), np.array(a_ub), np.array(b_ub), labels)


def feasible(problem: Problem) -> tuple[bool, np.ndarray | None, str]:
    result = linprog(
        c=np.zeros(len(problem.states)),
        A_ub=problem.a_ub if len(problem.a_ub) else None,
        b_ub=problem.b_ub if len(problem.b_ub) else None,
        A_eq=problem.a_eq,
        b_eq=problem.b_eq,
        bounds=(0, None),
        method="highs",
    )
    return bool(result.success), result.x if result.success else None, str(result.message)


def deletion_minimal_conflict(variables: list[Variable], constraints: list[Constraint]) -> list[str]:
    core = list(constraints)
    if feasible(build_problem(variables, core))[0]:
        return []
    index = 0
    while index < len(core):
        candidate = core[:index] + core[index + 1 :]
        if candidate and not feasible(build_problem(variables, candidate))[0]:
            core = candidate
        else:
            index += 1
    return [item.id for item in core]


def _simple_ipf(
    problem: Problem, constraints: list[Constraint], max_iterations: int = 5000
) -> tuple[np.ndarray | None, int, float]:
    if any(item.relation != "eq" for item in constraints):
        return None, 0, float("inf")
    p = np.full(len(problem.states), 1.0 / len(problem.states))
    for iteration in range(1, max_iterations + 1):
        for constraint in constraints:
            mask = _row(problem.states, constraint.where).astype(bool)
            current = float(p[mask].sum())
            target = constraint.value
            # current가 1e-100 같은 극소 양수면 target/current 곱이 오버플로한다 — 허용오차 미만은 실패 처리.
            if (current < TOLERANCE and target > 0) or (current > 1 - TOLERANCE and target < 1):
                return None, iteration, float("inf")
            if current > 0:
                p[mask] *= target / current
            if current < 1:
                p[~mask] *= (1 - target) / (1 - current)
        residual = (
            max(abs(float(row @ p) - value) for row, value in zip(problem.a_eq[1:], problem.b_eq[1:], strict=True))
            if len(problem.a_eq) > 1
            else 0.0
        )
        if residual < TOLERANCE:
            return p / p.sum(), iteration, residual
    return None, max_iterations, residual


def maximum_entropy(problem: Problem, constraints: list[Constraint], initial: np.ndarray) -> dict[str, Any]:
    p, iterations, residual = _simple_ipf(problem, constraints)
    if p is not None:
        return {
            "distribution": p,
            "algorithm": "iterative_proportional_fitting",
            "iterations": iterations,
            "residual": residual,
        }

    def objective(x: np.ndarray) -> float:
        safe = np.clip(x, 1e-15, None)
        return float(np.sum(safe * np.log(safe)))

    def jacobian(x: np.ndarray) -> np.ndarray:
        return np.log(np.clip(x, 1e-15, None)) + 1

    constraints_for_solver: list[LinearConstraint] = [LinearConstraint(problem.a_eq, problem.b_eq, problem.b_eq)]
    if len(problem.a_ub):
        constraints_for_solver.append(LinearConstraint(problem.a_ub, -np.inf, problem.b_ub))
    result = minimize(
        objective,
        initial,
        jac=jacobian,
        bounds=[(0, 1)] * len(initial),
        constraints=constraints_for_solver,
        method="SLSQP",
        options={"maxiter": 5000, "ftol": 1e-12},
    )
    if not result.success:
        raise DomainError(
            "MAX_ENTROPY_FAILED",
            "최대엔트로피 최적화가 수렴하지 않았습니다.",
            details={"solver_message": result.message},
        )
    return {
        "distribution": result.x,
        "algorithm": "constrained_maximum_entropy",
        "iterations": result.nit,
        "residual": float(max(np.abs(problem.a_eq @ result.x - problem.b_eq))),
    }


def _validate_dag(variables: list[Variable], parents: dict[str, list[str]]) -> dict[str, list[str]]:
    ids = {item.id for item in variables}
    normalized = {item.id: [str(parent) for parent in parents.get(item.id, [])] for item in variables}
    if any(parent not in ids or parent == child for child, entries in normalized.items() for parent in entries):
        raise DomainError("INVALID_DAG", "DAG parent는 알려진 다른 변수여야 합니다.")
    remaining = {key: set(value) for key, value in normalized.items()}
    resolved: set[str] = set()
    while remaining:
        ready = [key for key, value in remaining.items() if value <= resolved]
        if not ready:
            raise DomainError("CYCLIC_DAG", "DAG에는 순환 edge를 둘 수 없습니다.")
        for key in ready:
            resolved.add(key)
            del remaining[key]
    return normalized


def fit_dag(problem: Problem, variables: list[Variable], parents_input: dict[str, list[str]]) -> dict[str, Any]:
    """Fit a discrete Bayesian-network point model subject to approved constraints.

    This is deliberately a point-estimator only. LP bounds remain assumption-free.
    """
    parents = _validate_dag(variables, parents_input)
    by_id = {item.id: item for item in variables}
    layout: dict[tuple[str, tuple[str, ...]], tuple[int, int]] = {}
    cursor = 0
    for variable in variables:
        configurations = product(*(by_id[parent].categories for parent in parents[variable.id]))
        for configuration in configurations:
            width = len(variable.categories) - 1
            layout[(variable.id, tuple(configuration))] = (cursor, cursor + width)
            cursor += width

    def distribution(theta: np.ndarray) -> np.ndarray:
        values: list[float] = []
        for state in problem.states:
            probability = 1.0
            for variable in variables:
                configuration = tuple(state[parent] for parent in parents[variable.id])
                start, end = layout[(variable.id, configuration)]
                logits = np.concatenate([theta[start:end], np.array([0.0])])
                weights = np.exp(logits - logits.max())
                probability *= float(weights[variable.categories.index(state[variable.id])] / weights.sum())
            values.append(probability)
        return np.array(values)

    def objective(theta: np.ndarray) -> float:
        values = np.clip(distribution(theta), 1e-15, None)
        return float(np.sum(values * np.log(values)))

    # The softmax CPT parameterization already guarantees sum(p)=1. Passing
    # that row again makes SLSQP's equality Jacobian singular, so only source
    # constraints (all rows after normalization) are supplied here.
    constraints: list[dict[str, Any]] = []
    if len(problem.a_eq) > 1:
        constraints.append(
            {"type": "eq", "fun": lambda theta: problem.a_eq[1:] @ distribution(theta) - problem.b_eq[1:]}
        )
    if len(problem.a_ub):
        constraints.append({"type": "ineq", "fun": lambda theta: problem.b_ub - problem.a_ub @ distribution(theta)})
    result = minimize(
        objective, np.zeros(cursor), method="SLSQP", constraints=constraints, options={"maxiter": 3000, "ftol": 1e-12}
    )
    values = distribution(result.x)
    equality_residual = np.max(np.abs(problem.a_eq[1:] @ values - problem.b_eq[1:])) if len(problem.a_eq) > 1 else 0.0
    residual = float(max(equality_residual, np.max(problem.a_ub @ values - problem.b_ub) if len(problem.a_ub) else 0.0))
    if not result.success or residual > 1e-6:
        return {"status": "not_fitted", "solver_message": str(result.message), "residual": residual, "parents": parents}
    return {
        "status": "fitted",
        "distribution": values,
        "parents": parents,
        "iterations": result.nit,
        "residual": residual,
    }


def _linear_bound(problem: Problem, target: np.ndarray, direction: str) -> float:
    result = linprog(
        c=target if direction == "min" else -target,
        A_ub=problem.a_ub if len(problem.a_ub) else None,
        b_ub=problem.b_ub if len(problem.b_ub) else None,
        A_eq=problem.a_eq,
        b_eq=problem.b_eq,
        bounds=(0, None),
        method="highs",
    )
    if not result.success:
        raise DomainError(
            "BOUND_FAILED", "식별구간 LP 계산에 실패했습니다.", details={"solver_message": result.message}
        )
    return float(result.fun if direction == "min" else -result.fun)


def _merge_conditions(numerator: dict[str, str], denominator: dict[str, str]) -> dict[str, str] | None:
    merged = dict(denominator)
    for key, value in numerator.items():
        if key in merged and merged[key] != value:
            return None
        merged[key] = value
    return merged


def _fractional_bound(problem: Problem, numerator: np.ndarray, denominator: np.ndarray, direction: str) -> float:
    n = len(problem.states)
    c = np.concatenate([numerator if direction == "min" else -numerator, np.zeros(1)])
    a_eq = np.hstack([problem.a_eq, -problem.b_eq.reshape(-1, 1)])
    a_eq = np.vstack([a_eq, np.concatenate([denominator, [0]])])
    b_eq = np.concatenate([np.zeros(len(problem.b_eq)), [1.0]])
    a_ub = np.hstack([problem.a_ub, -problem.b_ub.reshape(-1, 1)]) if len(problem.a_ub) else None
    result = linprog(
        c=c,
        A_ub=a_ub,
        b_ub=np.zeros(len(problem.b_ub)) if len(problem.a_ub) else None,
        A_eq=a_eq,
        b_eq=b_eq,
        bounds=[(0, None)] * (n + 1),
        method="highs",
    )
    if not result.success:
        raise DomainError(
            "CONDITIONAL_BOUND_FAILED",
            "조건부 식별구간 계산에 실패했습니다.",
            details={"solver_message": result.message},
        )
    return float(result.fun if direction == "min" else -result.fun)


def identification_bounds(
    problem: Problem, numerator_where: dict[str, str], denominator_where: dict[str, str] | None = None
) -> dict[str, Any]:
    numerator = _row(problem.states, numerator_where)
    if not denominator_where:
        return {
            "kind": "probability",
            "lower": _linear_bound(problem, numerator, "min"),
            "upper": _linear_bound(problem, numerator, "max"),
        }
    merged = _merge_conditions(numerator_where, denominator_where)
    if merged is None:
        return {"kind": "conditional_probability", "status": "defined", "lower": 0.0, "upper": 0.0}
    numerator = _row(problem.states, merged)
    denominator = _row(problem.states, denominator_where)
    denominator_min = _linear_bound(problem, denominator, "min")
    if denominator_min <= TOLERANCE:
        return {
            "kind": "conditional_probability",
            "status": "undefined_due_to_zero_denominator",
            "denominator_lower": denominator_min,
        }
    return {
        "kind": "conditional_probability",
        "status": "defined",
        "lower": _fractional_bound(problem, numerator, denominator, "min"),
        "upper": _fractional_bound(problem, numerator, denominator, "max"),
        "denominator_lower": denominator_min,
    }


def _point_estimate(
    states: list[dict[str, str]],
    distribution: np.ndarray,
    numerator_where: dict[str, str],
    denominator_where: dict[str, str] | None,
) -> float | None:
    numerator = _row(states, numerator_where)
    if not denominator_where:
        return float(numerator @ distribution)
    merged = _merge_conditions(numerator_where, denominator_where)
    numerator = _row(states, merged or {"__impossible__": "true"})
    denominator = _row(states, denominator_where)
    denominator_value = float(denominator @ distribution)
    return float(numerator @ distribution / denominator_value) if denominator_value > TOLERANCE else None


def estimate(
    variables: list[Variable],
    constraints: list[Constraint],
    numerator_where: dict[str, str],
    denominator_where: dict[str, str] | None,
    dag_candidates: list[dict[str, Any]] | None = None,
    selected_model: str = "maximum_entropy",
) -> dict[str, Any]:
    problem = build_problem(variables, constraints)
    is_feasible, initial, message = feasible(problem)
    if not is_feasible or initial is None:
        return {
            "status": "infeasible",
            "solver_message": message,
            "conflict_core": deletion_minimal_conflict(variables, constraints),
        }
    max_entropy = maximum_entropy(problem, constraints, initial)
    bounds = identification_bounds(problem, numerator_where, denominator_where)
    point = _point_estimate(problem.states, max_entropy["distribution"], numerator_where, denominator_where)
    selected_distribution = max_entropy["distribution"]
    sensitivity: list[dict[str, Any]] = []
    for candidate in dag_candidates or []:
        identifier = str(candidate.get("id", ""))
        if not identifier:
            raise DomainError("INVALID_DAG", "각 DAG 후보에는 id가 필요합니다.")
        fitted = fit_dag(problem, variables, candidate.get("parents", {}))
        summary = {key: value for key, value in fitted.items() if key != "distribution"} | {"id": identifier}
        if fitted["status"] == "fitted":
            summary["point_estimate"] = _point_estimate(
                problem.states, fitted["distribution"], numerator_where, denominator_where
            )
            if identifier == selected_model:
                selected_distribution = fitted["distribution"]
        sensitivity.append(summary)
    if selected_model != "maximum_entropy" and all(
        item["id"] != selected_model or item["status"] != "fitted" for item in sensitivity
    ):
        raise DomainError("SELECTED_DAG_NOT_FITTED", "선택한 DAG 후보가 적합되지 않았습니다.")
    return {
        "status": "feasible",
        "states": problem.states,
        "distribution": selected_distribution.tolist(),
        "selected_model": selected_model,
        "maximum_entropy": {key: value for key, value in max_entropy.items() if key != "distribution"}
        | {"point_estimate": point},
        "identification": bounds,
        "structure_sensitivity": sensitivity,
        "constraint_count": len(constraints),
        "cross_constraint_count": sum(1 for item in constraints if len(item.where) > 1),
    }
