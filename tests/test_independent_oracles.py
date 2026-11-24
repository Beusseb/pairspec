"""Tests whose expected values do not come from the engine itself.

Written after a mutmut run on ipog.py, bva.py and schema.py (see docs/mutation-testing.md).
The older tests checked exclusions and coverage through `verify_coverage`, which shares its
exclusion predicate with the generator: with exclusions disabled, the generator emitted 25
forbidden cases and the report still said 0 violations. Here every expected value is computed
in the test, from the spec text or the BVA rule, with plain Python.
"""

from __future__ import annotations

import itertools
from pathlib import Path
from typing import Any

import pytest

from pairspec.bva import expand_parameter_values
from pairspec.ipog import GeneratedCase, compute_case_id, generate_matrix, verify_coverage
from pairspec.schema import (
    ExclusionRule,
    OracleRule,
    ParameterSpec,
    SpecDocument,
    UnresolvedVariableError,
    evaluate_expr,
    load_spec,
)

FINTECH = Path(__file__).resolve().parent.parent / "examples" / "fintech_payout" / "spec.toml"


# --- The fintech spec, re-stated by hand from spec.toml -------------------------------------

def _excluded(a: dict[str, Any]) -> bool:
    return (a["rail"] == "SEPA" and a["currency"] == "JPY") or (a["rail"] == "INSTANT" and a["currency"] != "EUR")


ALL_KEYS = {"status", "reason", "requires_2fa", "fee_bps"}
# (priority, variables, predicate, keys it sets)
RULES = {
    "invalid_amount_bounds": (10, ("amount",), lambda a: a["amount"] <= 0 or a["amount"] > 10000, ALL_KEYS),
    "sepa_eur_only": (15, ("rail", "currency"), lambda a: a["rail"] == "SEPA" and a["currency"] != "EUR", ALL_KEYS),
    "kyc_unverified_cap": (20, ("kyc_tier", "amount"), lambda a: a["kyc_tier"] == "unverified" and a["amount"] > 500, ALL_KEYS),
    "mobile_high_value_2fa": (40, ("channel", "amount"), lambda a: a["channel"] == "mobile" and a["amount"] >= 5000, {"requires_2fa"}),
    "swift_fee": (50, ("rail",), lambda a: a["rail"] == "SWIFT", {"fee_bps"}),
}


@pytest.fixture(scope="module")
def fintech() -> Any:
    spec = load_spec(FINTECH)
    domains = generate_matrix(spec).parameter_domains
    names = list(domains)
    every = [dict(zip(names, combo, strict=True)) for combo in itertools.product(*domains.values())]
    return spec, domains, [a for a in every if not _excluded(a)], len(every)


def _tuples(assignments: list[dict[str, Any]], names: list[str], t: int) -> set[tuple[tuple[str, Any], ...]]:
    return {tuple((n, a[n]) for n in comb) for a in assignments for comb in itertools.combinations(names, t)}


@pytest.mark.parametrize(("t", "unmask", "size"), [(1, True, None), (2, True, 53), (2, False, 38), (3, True, 137)])
def test_matrix_respects_exclusions_and_covers_every_feasible_tuple(fintech: Any, t: int, unmask: bool, size: int | None) -> None:
    spec, domains, feasible, _ = fintech
    result = generate_matrix(spec, strength=t, unmask_oracles=unmask)
    inputs = [c.inputs for c in result.cases]
    names = list(domains)

    assert not [a for a in inputs if _excluded(a)]
    expected = _tuples(feasible, names, t)
    assert expected <= _tuples(inputs, names, t)
    assert result.coverage.total_feasible_tuples == len(expected)
    assert result.coverage.covered_tuples == len(expected)
    assert result.coverage.uncovered_tuples == 0
    assert result.coverage.exclusion_violations == 0
    assert result.coverage.strength == t
    if size is not None:  # sizes quoted in the README and the benchmark
        assert len(result.cases) == size


def test_full_strength_is_the_filtered_cartesian_product(fintech: Any) -> None:
    spec, _, feasible, cartesian = fintech
    result = generate_matrix(spec, strength=99)
    assert cartesian == 972 == result.cartesian_size
    assert sorted(map(repr, (c.inputs for c in result.cases))) == sorted(map(repr, feasible))
    assert result.strength == 5


def test_strength_below_one_is_rejected(fintech: Any) -> None:
    with pytest.raises(ValueError):
        generate_matrix(fintech[0], strength=0)


def test_result_metadata_comes_from_the_spec(fintech: Any) -> None:
    spec = fintech[0]
    result = generate_matrix(spec)
    assert (result.spec_name, result.spec_version, result.strength) == ("fintech_payout_rules", "1.0.0", 2)
    assert result.spec_fingerprint == spec.fingerprint() and len(result.spec_fingerprint) == 16
    data = result.to_dict()
    assert data["case_count"] == 53 and 0 < data["oracle_unmask_cases"] < 53
    assert generate_matrix(spec, unmask_oracles=False).to_dict()["oracle_unmask_cases"] == 0
    assert data["cartesian_size"] == 972 and data["strength"] == 2
    assert data["coverage"]["covered_tuples"] == data["coverage"]["total_feasible_tuples"]
    assert [c["id"] for c in data["cases"]] == [c.case_id for c in result.cases]
    assert all(c.case_id == compute_case_id("fintech_payout_rules", c.inputs) for c in result.cases)
    assert data["cases"][0]["expected"] == result.cases[0].expected


def _visible(a: dict[str, Any], target: dict[str, Any], maskers: list[Any]) -> bool:
    return all(a[v] == x for v, x in target.items()) and not any(m(a) for m in maskers)


def test_every_rule_is_observed_without_a_stronger_rule_hiding_it(fintech: Any) -> None:
    """The unmasking claim, checked by brute force over every feasible assignment."""
    spec, domains, feasible, _ = fintech

    def hidden_tuples(cases: list[dict[str, Any]]) -> list[tuple[str, tuple[Any, ...]]]:
        missing = []
        for name, (prio, variables, _, keys) in RULES.items():
            maskers = [r[2] for r in RULES.values() if r[0] < prio and keys <= r[3]]
            for combo in itertools.product(*(domains[v] for v in variables)):
                target = dict(zip(variables, combo, strict=True))
                if any(_visible(a, target, maskers) for a in feasible) and not any(
                    _visible(a, target, maskers) for a in cases
                ):
                    missing.append((name, combo))
        return missing

    assert hidden_tuples([c.inputs for c in generate_matrix(spec).cases]) == []
    assert hidden_tuples([c.inputs for c in generate_matrix(spec, unmask_oracles=False).cases]) != []


def test_unmasking_continues_after_a_rule_that_nothing_masks() -> None:
    """`w` (a == 1) is hidden by `s` unless b == c == 0, a 3-way tuple pairwise does not promise.
    `n` comes first in evaluation order and has no masker; it must not stop the search."""
    spec = SpecDocument.from_dict(
        {
            "name": "u",
            "parameters": [{"name": n, "type": "enum", "values": [0, 1, 2]} for n in "abc"]
            + [{"name": "d", "type": "enum", "values": [0, 1]}],
            "default_expect": {"x": 0, "z": 0},
            "oracles": [
                {"name": "n", "priority": 60, "when": "d == 1", "expect": {"z": 1}},
                {"name": "w", "priority": 50, "when": "a == 1", "expect": {"x": 1}},
                {"name": "s", "priority": 10, "when": "b != 0 or c != 0", "expect": {"x": 2}},
            ],
        }
    )

    def w_visible(result: Any) -> bool:
        return any((c.inputs["a"], c.inputs["b"], c.inputs["c"]) == (1, 0, 0) for c in result.cases)

    assert w_visible(generate_matrix(spec))
    assert not w_visible(generate_matrix(spec, unmask_oracles=False))


def test_expected_outcomes_follow_rule_priorities(fintech: Any) -> None:
    spec, _, feasible, _ = fintech
    for a in feasible:
        expected: dict[str, Any] = {"status": "approved", "requires_2fa": False, "fee_bps": 0}
        if RULES["swift_fee"][2](a):
            expected["fee_bps"] = 150
        if RULES["mobile_high_value_2fa"][2](a):
            expected["requires_2fa"] = True
        for name, reason in (("kyc_unverified_cap", "kyc_limit_exceeded"), ("sepa_eur_only", "unsupported_currency"),
                             ("invalid_amount_bounds", "invalid_amount")):
            if RULES[name][2](a):
                expected = {"status": "rejected", "reason": reason, "requires_2fa": False, "fee_bps": 0}
        assert spec.evaluate_oracle(a)[0] == expected, a


def test_verify_coverage_counts_violations_and_ignores_infeasible_tuples(fintech: Any) -> None:
    spec, domains, feasible, _ = fintech
    base = feasible[0]
    bad = [dict(base, rail="SEPA", currency="JPY"), dict(base, rail="INSTANT", currency="USD")]
    cases = [GeneratedCase(case_id=str(i), inputs=a, expected={}) for i, a in enumerate([base, *bad])]
    report = verify_coverage(spec, cases, strength=2, domains=domains)

    names = list(domains)
    feasible_pairs = _tuples(feasible, names, 2)
    assert report.exclusion_violations == 2
    assert report.total_feasible_tuples == len(feasible_pairs)
    assert report.covered_tuples == len(_tuples([base, *bad], names, 2) & feasible_pairs)
    assert report.uncovered_tuples == len(feasible_pairs) - report.covered_tuples
    assert verify_coverage(spec, cases).strength == 2  # falls back to the spec strength


# --- Stable IDs: a contract, every committed lockfile depends on it -------------------------

def test_case_ids_are_frozen() -> None:
    # Computed with hashlib alone from the documented canonical form (sorted keys, compact
    # separators, ASCII escapes): sha256('{"inputs":{...},"spec":"p"}')[:8], upper case.
    assert compute_case_id("p", {"b": "x", "a": 1}) == "PS-6E4A6A58"
    assert compute_case_id("p", {"city": "Orléans", "n": 2}) == "PS-33327A28"


# --- Boundary values, expected lists written from the BVA rule -------------------------------
# Rule: lo, lo+step, middle, hi-step, hi (each kept only if strictly inside), plus lo-step and
# hi+step when include_invalid (default true); floats rounded to the decimals of step and bounds.

@pytest.mark.parametrize(
    ("param", "expected"),
    [
        ({"type": "int", "min": 0, "max": 10}, [-1, 0, 1, 5, 9, 10, 11]),
        ({"type": "int", "min": 0, "max": 10, "step": 2, "include_invalid": False}, [0, 2, 5, 8, 10]),
        ({"type": "int", "min": 5, "max": 5, "include_invalid": False}, [5]),
        ({"type": "int", "min": 0, "max": 5, "step": 10, "include_invalid": False}, [0, 2, 5]),
        ({"type": "float", "min": 0, "max": 1, "include_invalid": False}, [0.0, 0.01, 0.5, 0.99, 1.0]),
        ({"type": "float", "min": 0, "max": 0.25, "step": 0.1, "include_invalid": False}, [0.0, 0.1, 0.12, 0.15, 0.25]),
        ({"type": "float", "min": 0.25, "max": 2.5, "step": 1}, [-0.75, 0.25, 1.25, 1.38, 1.5, 2.5, 3.5]),
        ({"type": "float", "min": 0, "max": 2.25, "step": 1, "include_invalid": False}, [0.0, 1.0, 1.12, 1.25, 2.25]),
        ({"type": "float", "min": 1, "max": 1}, [0.99, 1.0, 1.01]),
        ({"type": "float", "min": 0, "max": 0.5, "step": 1, "include_invalid": False}, [0.0, 0.2, 0.5]),
        ({"type": "string", "include_invalid": False}, ["a", "a" * 8, "a" * 16]),
        ({"type": "string", "min_length": 1, "max_length": 3}, ["", "a", "aa", "aaa", "aaaa"]),
        ({"type": "string", "min_length": 0, "max_length": 2}, ["", "a", "aa", "aaa"]),
        ({"type": "string", "min_length": 3, "max_length": 3, "include_invalid": False}, ["aaa"]),
        ({"type": "bool"}, [True, False]),
        ({"type": "bool", "values": [True]}, [True]),
    ],
)
def test_bva_values_follow_the_rule(param: dict[str, Any], expected: list[Any]) -> None:
    assert expand_parameter_values(ParameterSpec.from_dict({"name": "p", **param})) == expected


@pytest.mark.parametrize(
    "param",
    [
        {"type": "int", "min": 0},
        {"type": "int", "max": 0},
        {"type": "float", "min": 0},
        {"type": "float", "min": 2, "max": 1},
        {"type": "int", "min": 0, "max": 3, "step": 0},
        {"type": "string", "min_length": 4, "max_length": 2},
    ],
)
def test_bva_rejects_incomplete_or_inverted_ranges(param: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        expand_parameter_values(ParameterSpec.from_dict({"name": "p", **param}))


# --- Expression evaluator ---------------------------------------------------------------------

@pytest.mark.parametrize(
    ("expr", "env", "expected"),
    [
        ("true", {}, True),
        ("True", {}, True),
        ("false", {}, False),
        ("False", {}, False),
        ("null", {}, None),
        ("none", {}, None),
        ("None", {}, None),
        ("a in [b, 3]", {"a": 2, "b": 2}, True),
        ("(a, b) == (1, 2)", {"a": 1, "b": 2}, True),
        ("a in {b}", {"a": 1, "b": 1}, True),
        ("1 < a < 3", {"a": 2}, True),
        ("1 < a < 3", {"a": 3}, False),
        ("a <= b <= 2", {"a": 1, "b": 2}, True),
        ("s + t == 'ab'", {"s": "a", "t": "b"}, True),
        ("a and b", {"a": 1, "b": 2}, True),
        ("a or b", {"a": 0, "b": 0}, False),
    ],
)
def test_expression_values(expr: str, env: dict[str, Any], expected: Any) -> None:
    assert evaluate_expr(expr, env) == expected
    assert type(evaluate_expr(expr, env)) is type(expected)


@pytest.mark.parametrize("expr", ["[1] + [2]", "s * 3", "flag + 1"])
def test_arithmetic_is_numbers_only(expr: str) -> None:
    with pytest.raises(TypeError):
        evaluate_expr(expr, {"s": "ab", "flag": True})


def test_unresolved_variable_names_the_variable() -> None:
    with pytest.raises(UnresolvedVariableError, match="ghost"):
        evaluate_expr("ghost == 1", {})


@pytest.mark.parametrize("rule", [OracleRule(name="r", when="a > 1", expect={"x": 1}), ExclusionRule(expr="a > 1")])
def test_rules_do_not_match_on_missing_variables_or_type_errors(rule: Any) -> None:
    check = rule.matches if isinstance(rule, OracleRule) else rule.is_violated
    assert check({"a": 2}) is True
    assert check({}) is False
    assert check({"a": "text"}) is False
