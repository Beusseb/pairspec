"""Tests for deterministic IPOG combinatorial generation, exclusions, stability, and AST safety."""

from __future__ import annotations

import pytest

from pairspec.ipog import compute_case_id, generate_matrix, verify_coverage
from pairspec.schema import ExpressionError, SpecDocument, evaluate_expr, parse_spec_text

SAMPLE_TOML = """
[spec]
name = "checkout"
version = "1.0.0"
strength = 2

[default_expect]
allowed = true

[[parameters]]
name = "country"
type = "enum"
values = ["FR", "US", "JP", "DE"]

[[parameters]]
name = "method"
type = "enum"
values = ["card", "sepa", "wallet"]

[[parameters]]
name = "tier"
type = "enum"
values = ["guest", "member", "vip"]

[[parameters]]
name = "amount"
type = "int"
min = 1
max = 100
step = 1
include_invalid = true

[[exclusions]]
expr = "method == 'sepa' and country in ['US', 'JP']"
reason = "SEPA is Europe only"

[[exclusions]]
expr = "tier == 'guest' and method == 'sepa'"
reason = "Guests cannot pay by SEPA"

[[oracles]]
name = "invalid_amount"
priority = 10
when = "amount <= 0 or amount > 100"
expect = { allowed = false, error = "invalid_amount" }
"""


def test_ipog_pairwise_100pct_coverage_and_zero_violations() -> None:
    spec = parse_spec_text(SAMPLE_TOML, fmt="toml")
    res = generate_matrix(spec)

    assert res.coverage.coverage_pct == 100.0
    assert res.coverage.uncovered_tuples == 0
    assert res.coverage.exclusion_violations == 0
    assert len(res.cases) < res.cartesian_size
    assert res.reduction_pct > 50.0


def test_ipog_is_strictly_deterministic() -> None:
    spec = parse_spec_text(SAMPLE_TOML, fmt="toml")
    r1 = generate_matrix(spec)
    r2 = generate_matrix(spec)
    assert [c.case_id for c in r1.cases] == [c.case_id for c in r2.cases]
    assert [c.inputs for c in r1.cases] == [c.inputs for c in r2.cases]
    assert [c.expected for c in r1.cases] == [c.expected for c in r2.cases]


def test_ipog_3wise_full_coverage() -> None:
    spec = SpecDocument.from_dict(
        {
            "name": "tri_test",
            "strength": 3,
            "parameters": [
                {"name": "a", "type": "enum", "values": [1, 2, 3]},
                {"name": "b", "type": "enum", "values": ["x", "y"]},
                {"name": "c", "type": "bool"},
                {"name": "d", "type": "enum", "values": ["p", "q"]},
            ],
            "exclusions": [{"expr": "a == 3 and b == 'y' and c == True"}],
        }
    )
    res = generate_matrix(spec, strength=3)
    check = verify_coverage(spec, res.cases, strength=3)
    assert check.coverage_pct == 100.0
    assert check.exclusion_violations == 0
    assert len(res.cases) < res.cartesian_size


def test_stable_case_id_is_key_order_independent() -> None:
    id1 = compute_case_id("demo", {"a": 1, "b": "x", "c": True})
    id2 = compute_case_id("demo", {"c": True, "a": 1, "b": "x"})
    assert id1 == id2
    assert id1.startswith("PS-")
    assert len(id1) == 11


def test_markdown_fenced_spec_parsing() -> None:
    md = "# My Spec\n\nSome prose.\n\n```pairspec-toml\n" + SAMPLE_TOML + "\n```\n"
    spec = parse_spec_text(md, fmt="md")
    assert spec.name == "checkout"
    assert len(spec.parameters) == 4


def test_safe_expression_evaluator_blocks_code_execution() -> None:
    with pytest.raises(ExpressionError):
        evaluate_expr("__import__('os').system('echo pwned')", {})
    with pytest.raises(ExpressionError):
        evaluate_expr("open('/etc/passwd')", {})
    with pytest.raises(ExpressionError):
        evaluate_expr("(lambda x: x)(1)", {})
