"""Targeted edge-case tests for CLI, BVA, schema validation, lockfile, and MCP handlers."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from pairspec.bva import expand_parameter_values
from pairspec.cli import main as cli_main
from pairspec.llm_client import LLMConfig
from pairspec.lockfile import DriftReport, scan_test_tags
from pairspec.mcp_server import handle_rpc_message
from pairspec.schema import ExpressionError, ParameterSpec, SpecDocument, evaluate_expr, parse_spec_text


def test_bva_validation_errors_and_explicit_values() -> None:
    assert expand_parameter_values(ParameterSpec.from_dict({"name": "x", "type": "int", "values": [3, 3, 7]})) == [3, 7]
    assert expand_parameter_values(ParameterSpec.from_dict({"name": "r", "type": "float", "values": [1.5, 2.5]})) == [1.5, 2.5]
    assert expand_parameter_values(ParameterSpec.from_dict({"name": "s", "type": "string", "values": ["a", "b"]})) == ["a", "b"]

    with pytest.raises(ValueError, match="non-empty"):
        expand_parameter_values(ParameterSpec(name="e", kind="enum", values=[]))
    with pytest.raises(ValueError, match="min.*max"):
        expand_parameter_values(ParameterSpec(name="i", kind="int"))
    with pytest.raises(ValueError, match="min"):
        expand_parameter_values(ParameterSpec(name="i", kind="int", min_value=10, max_value=5))
    with pytest.raises(ValueError, match="step"):
        expand_parameter_values(ParameterSpec(name="i", kind="int", min_value=1, max_value=5, step=0))
    with pytest.raises(ValueError, match="min.*max"):
        expand_parameter_values(ParameterSpec(name="f", kind="float"))
    with pytest.raises(ValueError, match="min"):
        expand_parameter_values(ParameterSpec(name="f", kind="float", min_value=2.0, max_value=1.0))
    with pytest.raises(ValueError, match="step"):
        expand_parameter_values(ParameterSpec(name="f", kind="float", min_value=0.0, max_value=1.0, step=0.0))
    with pytest.raises(ValueError, match="invalid lengths"):
        expand_parameter_values(ParameterSpec(name="s", kind="string", min_length=10, max_length=2))


def test_schema_expression_and_parsing_edge_cases() -> None:
    assert evaluate_expr("-x + abs(-2)", {"x": 3}) == -1
    assert evaluate_expr("not flag and x in [1, 2, 3]", {"flag": False, "x": 2}) is True
    assert evaluate_expr("x in (1, 2) or x in {3, 4}", {"x": 3}) is True
    assert evaluate_expr("len(name) == 3", {"name": "abc"}) is True

    with pytest.raises(ExpressionError, match="Syntax|Invalid expression"):
        evaluate_expr("1 +", {})
    with pytest.raises(ExpressionError, match="whitelisted"):
        evaluate_expr("eval('1')", {})
    with pytest.raises(ExpressionError, match="Keyword arguments"):
        evaluate_expr("round(1.2, ndigits=1)", {})
    with pytest.raises(ExpressionError, match="Disallowed syntax"):
        evaluate_expr("a.b", {"a": 1})
    with pytest.raises(ExpressionError, match="Unsupported unary"):
        evaluate_expr("~1", {})
    with pytest.raises(ExpressionError, match="Unsupported binary"):
        evaluate_expr("2 ** 3", {})
    with pytest.raises(ExpressionError, match="Unsupported comparison"):
        evaluate_expr("a is None", {"a": None})

    # Markdown JSON block + error cases
    md_json = '```pairspec-json\n{"name":"m","parameters":[{"name":"a","type":"bool"}]}\n```'
    assert parse_spec_text(md_json, "md").name == "m"
    with pytest.raises(ValueError, match="No ```pairspec-toml```"):
        parse_spec_text("# just prose", "md")
    with pytest.raises(ValueError, match="Unsupported spec format"):
        parse_spec_text("{}", "ini")
    with pytest.raises(ValueError, match="mapping/object"):
        parse_spec_text("[1, 2, 3]", "json")

    with pytest.raises(ValueError, match="strength"):
        SpecDocument.from_dict({"name": "s", "strength": 4, "parameters": [{"name": "a", "type": "bool"}]})
    with pytest.raises(ValueError, match="at least one parameter"):
        SpecDocument.from_dict({"name": "s", "parameters": []})
    with pytest.raises(ValueError, match="Duplicate parameter"):
        SpecDocument.from_dict({"name": "s", "parameters": [{"name": "a", "type": "bool"}, {"name": "a", "type": "bool"}]})
    with pytest.raises(ValueError, match="unknown parameters"):
        SpecDocument.from_dict(
            {"name": "s", "parameters": [{"name": "a", "type": "bool"}], "exclusions": ["ghost == True"]}
        )
    with pytest.raises(ValueError, match="unknown parameters"):
        SpecDocument.from_dict(
            {
                "name": "s",
                "parameters": [{"name": "a", "type": "bool"}],
                "oracles": [{"name": "r", "when": "ghost == True", "expect": {"x": 1}}],
            }
        )


def test_cli_init_overwrite_stdout_and_extract_mocked(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    spec_path = tmp_path / "pairspec.toml"
    assert cli_main(["init", str(spec_path)]) == 0
    assert cli_main(["init", str(spec_path)]) == 1  # exists without --force
    assert cli_main(["init", str(spec_path), "--force"]) == 0

    # Generate & emit to stdout
    assert cli_main(["generate", str(spec_path)]) == 0
    assert "# PairSpec Matrix" in capsys.readouterr().out
    assert cli_main(["emit", str(spec_path), "--format", "gherkin"]) == 0
    assert "Feature:" in capsys.readouterr().out

    # Drift with --json alias
    test_file = tmp_path / "test_single.py"
    assert cli_main(["emit", str(spec_path), "--format", "pytest", "--out", str(test_file)]) == 0
    assert cli_main(["drift", str(spec_path), "--tests", str(test_file), "--json"]) == 0
    drift_out = json.loads(capsys.readouterr().out)
    assert drift_out["is_clean"] is True
    assert len(scan_test_tags(test_file)) == drift_out["spec_case_count"]
    assert DriftReport(spec_case_count=0, implemented_ids={}, missing_in_tests=[], orphaned_in_tests=[]).coverage_pct == 100.0

    # Extract --check-gateway & extract from prose (mocked network)
    prose = tmp_path / "spec.md"
    prose.write_text("# Payout rule: web or mobile.", encoding="utf-8")
    extracted = {
        "name": "extracted_spec",
        "parameters": [{"name": "channel", "type": "enum", "values": ["web", "mobile"]}],
        "oracles": [],
    }
    with patch("pairspec.cli.list_models", return_value=["qwen-local"]):
        assert cli_main(["extract", "--check-gateway"]) == 0
        assert "qwen-local" in capsys.readouterr().out

    assert cli_main(["extract"]) == 1  # missing spec_doc

    out_json = tmp_path / "out.json"
    with patch("pairspec.cli.extract_spec_blind", return_value=(SpecDocument.from_dict(extracted), extracted)):
        assert cli_main(["extract", str(prose), "--out", str(out_json)]) == 0
        assert json.loads(out_json.read_text(encoding="utf-8"))["name"] == "extracted_spec"
        assert cli_main(["extract", str(prose)]) == 0
        assert '"extracted_spec"' in capsys.readouterr().out

    # LLMConfig env parsing
    cfg = LLMConfig.from_env(base_url="http://127.0.0.1:11500/v1/", model="m", api_key="k", timeout_s=30)
    assert cfg.base_url == "http://127.0.0.1:11500/v1" and cfg.timeout_s == 30


def test_mcp_additional_tools_and_error_paths(tmp_path: Path) -> None:
    # ping + unknown method
    ping = handle_rpc_message({"jsonrpc": "2.0", "id": 10, "method": "ping"})
    assert ping is not None and ping["result"] == {}
    bad = handle_rpc_message({"jsonrpc": "2.0", "id": 11, "method": "nope/method"})
    assert bad is not None and bad["error"]["code"] == -32601

    spec_path = tmp_path / "s.toml"
    lock_a = tmp_path / "a.lock.json"
    lock_b = tmp_path / "b.lock.json"
    assert cli_main(["init", str(spec_path)]) == 0

    # pairspec_generate with inline spec_text + lock_path
    gen = handle_rpc_message(
        {
            "jsonrpc": "2.0",
            "id": 12,
            "method": "tools/call",
            "params": {
                "name": "pairspec_generate",
                "arguments": {"spec_path": str(spec_path), "lock_path": str(lock_a)},
            },
        }
    )
    assert gen is not None and not gen["result"].get("isError")
    lock_b.write_text(lock_a.read_text(encoding="utf-8"), encoding="utf-8")

    # pairspec_diff_locks
    diff_res = handle_rpc_message(
        {
            "jsonrpc": "2.0",
            "id": 13,
            "method": "tools/call",
            "params": {
                "name": "pairspec_diff_locks",
                "arguments": {"old_lock_path": str(lock_a), "new_lock_path": str(lock_b)},
            },
        }
    )
    assert diff_res is not None
    diff_payload = json.loads(diff_res["result"]["content"][0]["text"])
    assert diff_payload["has_changes"] is False

    # pairspec_extract_spec (mocked)
    extracted = {"name": "mcp_ext", "parameters": [{"name": "a", "type": "bool"}]}
    with patch("pairspec.mcp_server.extract_spec_blind", return_value=(SpecDocument.from_dict(extracted), extracted)):
        ext_res = handle_rpc_message(
            {
                "jsonrpc": "2.0",
                "id": 14,
                "method": "tools/call",
                "params": {"name": "pairspec_extract_spec", "arguments": {"spec_prose": "Rule: a is bool"}},
            }
        )
        assert ext_res is not None
        assert json.loads(ext_res["result"]["content"][0]["text"])["spec"]["name"] == "mcp_ext"
