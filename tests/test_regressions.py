"""Regression tests: matrix size and speed, oracle unmasking, drift gate, emitters, MCP protocol edges."""

from __future__ import annotations

import io
import json
import time
from pathlib import Path

import pytest

from pairspec.bva import expand_parameter_values
from pairspec.cli import main as cli_main
from pairspec.emitters import emit_suite
from pairspec.ipog import generate_matrix
from pairspec.lockfile import scan_test_tags
from pairspec.mcp_server import SUPPORTED_PROTOCOL_VERSIONS, handle_rpc_message, serve_stdio
from pairspec.schema import ParameterSpec, SpecDocument, evaluate_expr, load_spec

FINTECH_SPEC = Path(__file__).resolve().parent.parent / "examples" / "fintech_payout" / "spec.toml"


def _regular_spec(n_params: int, n_values: int, exclusions: int = 0) -> SpecDocument:
    return SpecDocument.from_dict(
        {
            "name": "regular",
            "parameters": [
                {"name": f"p{k}", "type": "enum", "values": [f"v{j}" for j in range(n_values)]}
                for k in range(n_params)
            ],
            "exclusions": [{"expr": f"p{k} == 'v0' and p{k + 1} == 'v1'"} for k in range(exclusions)],
        }
    )


def test_regular_domains_do_not_inflate_the_matrix() -> None:
    # 8 parameters x 4 values: first-value tie-breaking alone needs 46 rows.
    res = generate_matrix(_regular_spec(8, 4))
    assert res.coverage.coverage_pct == 100.0
    assert len(res.cases) <= 30


def test_exclusions_stay_fast_on_a_wide_spec() -> None:
    # 20 parameters with exclusions took ~24 s before feasibility was memoised.
    t0 = time.perf_counter()
    res = generate_matrix(_regular_spec(20, 4, exclusions=4))
    assert time.perf_counter() - t0 < 5.0
    assert res.coverage.coverage_pct == 100.0
    assert res.coverage.exclusion_violations == 0


def test_every_oracle_decision_is_observable_in_some_case() -> None:
    spec = load_spec(FINTECH_SPEC)
    res = generate_matrix(spec)
    applied = sorted(spec.oracles, key=lambda r: (-r.priority, r.name))
    by_name = {o.name: o for o in spec.oracles}

    # The boundary a pairwise-only matrix can hide behind a rejection rule: mobile at exactly 5000.
    assert any(
        c.inputs["channel"] == "mobile" and c.inputs["amount"] == 5000 and c.expected["requires_2fa"] is True
        for c in res.cases
    )
    # Each rule that matches somewhere is, in at least one case, not overridden by a stronger rule.
    for pos, rule in enumerate(applied):
        stronger = {o.name for o in applied[pos + 1 :] if set(rule.expect) <= set(o.expect)}
        assert any(
            rule.name in c.matched_oracles and not stronger & set(c.matched_oracles) for c in res.cases
        ), f"{rule.name} is always masked"
    assert by_name["swift_fee"].priority > by_name["invalid_amount_bounds"].priority

    pairwise_only = generate_matrix(spec, unmask_oracles=False)
    assert pairwise_only.oracle_unmask_cases == 0
    assert res.oracle_unmask_cases > 0 and len(res.cases) > len(pairwise_only.cases)


def test_expression_arithmetic_is_numbers_only() -> None:
    assert evaluate_expr("amount * 2 + 1", {"amount": 3}) == 7
    assert evaluate_expr("a + b", {"a": "x", "b": "y"}) == "xy"
    with pytest.raises(TypeError):
        evaluate_expr("'a' * 999999999", {})


def test_missing_required_keys_give_readable_errors() -> None:
    with pytest.raises(ValueError, match="identifier"):
        SpecDocument.from_dict({"parameters": [{"type": "bool"}]})
    with pytest.raises(ValueError, match="when"):
        SpecDocument.from_dict({"parameters": [{"name": "a", "type": "bool"}], "oracles": [{"expect": {"x": 1}}]})


def test_float_bounds_keep_their_own_precision() -> None:
    values = expand_parameter_values(
        ParameterSpec.from_dict({"name": "rate", "type": "float", "min": 0.005, "max": 0.125, "step": 0.01})
    )
    assert 0.005 in values and 0.125 in values


def test_tag_scan_ignores_skip_dirs_only_below_the_root(tmp_path: Path) -> None:
    root = tmp_path / "build" / "project" / "tests"  # the checkout itself lives under `build/`
    (root / "node_modules").mkdir(parents=True)
    (root / "test_a.py").write_text("# @pairspec:PS-0000AAAA\n", encoding="utf-8")
    (root / "node_modules" / "test_b.py").write_text("# @pairspec:PS-0000BBBB\n", encoding="utf-8")
    assert set(scan_test_tags(root)) == {"PS-0000AAAA"}


def test_kotlin_emitter_escapes_templates_and_slug_stays_an_identifier() -> None:
    spec = SpecDocument.from_dict(
        {
            "name": "3ds checkout",
            "parameters": [
                {"name": "label", "type": "enum", "values": ["$name", "plain"]},
                {"name": "vip", "type": "bool"},
            ],
        }
    )
    res = generate_matrix(spec)
    assert '"\\$name"' in emit_suite(res, "kotlin")
    pytest_code = emit_suite(res, "pytest")
    assert "def invoke_spec_3ds_checkout(" in pytest_code
    compile(pytest_code, "<emitted>", "exec")


def test_drift_with_a_missing_lockfile_is_an_error_not_a_pass(tmp_path: Path) -> None:
    spec_path = tmp_path / "pairspec.toml"
    test_file = tmp_path / "tests" / "test_suite.py"
    assert cli_main(["init", str(spec_path)]) == 0
    assert cli_main(["emit", str(spec_path), "--format", "pytest", "--out", str(test_file)]) == 0
    assert cli_main(["drift", str(spec_path), "--tests", str(test_file.parent)]) == 0
    assert cli_main(["drift", str(spec_path), "--tests", str(test_file.parent), "--lock", str(tmp_path / "typo.json")]) == 1
    assert cli_main(["drift", str(spec_path), "--tests", str(tmp_path / "nowhere")]) == 1


def test_mcp_protocol_edges(tmp_path: Path) -> None:
    newest = SUPPORTED_PROTOCOL_VERSIONS[0]
    for requested, answered in [("2024-11-05", "2024-11-05"), (newest, newest), ("1999-01-01", newest), (None, newest)]:
        params = {"protocolVersion": requested} if requested else {}
        reply = handle_rpc_message({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": params})
        assert reply is not None and reply["result"]["protocolVersion"] == answered

    # Notifications (no id) are never answered, whatever the method.
    assert handle_rpc_message({"jsonrpc": "2.0", "method": "notifications/cancelled"}) is None
    assert handle_rpc_message({"jsonrpc": "2.0", "method": "tools/list"}) is None

    unknown = handle_rpc_message(
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "nope", "arguments": {}}}
    )
    assert unknown is not None and unknown["error"]["code"] == -32602

    # A non-object message or a failing tool must not stop the server.
    spec_path = tmp_path / "spec.toml"
    assert cli_main(["init", str(spec_path)]) == 0
    out_file = tmp_path / "out" / "test_gen.py"
    lines = [
        "[1, 2]",
        json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "pairspec_emit", "arguments": {"spec_path": "missing.toml", "format": "pytest"}}}),
        json.dumps({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "pairspec_emit", "arguments": {"spec_path": str(spec_path), "format": "pytest", "output_path": str(out_file)}}}),
    ]
    stdout = io.StringIO()
    assert serve_stdio(stdin=io.StringIO("\n".join(lines) + "\n"), stdout=stdout) == 0
    replies = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert replies[0]["error"]["code"] == -32600
    assert replies[1]["result"]["isError"] is True
    written = json.loads(replies[2]["result"]["content"][0]["text"])
    assert written["written_to"] == str(out_file) and "content" not in written
    assert out_file.is_file()
