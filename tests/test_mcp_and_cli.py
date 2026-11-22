"""End-to-end tests for MCP stdio server, CLI commands, and the Mirror Trap demo."""

from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path

from pairspec.cli import main as cli_main
from pairspec.mcp_server import serve_stdio


def test_mcp_stdio_protocol_lifecycle(tmp_path: Path) -> None:
    spec_path = tmp_path / "spec.toml"
    assert cli_main(["init", str(spec_path)]) == 0
    lock_path = tmp_path / "pairspec.lock.json"
    out_test = tmp_path / "tests" / "test_gen.py"

    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "pairspec_generate",
                "arguments": {"spec_path": str(spec_path), "lock_path": str(lock_path)},
            },
        },
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "pairspec_emit",
                "arguments": {"spec_path": str(spec_path), "format": "pytest", "output_path": str(out_test)},
            },
        },
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {
                "name": "pairspec_check_drift",
                "arguments": {
                    "spec_path": str(spec_path),
                    "tests_dir": str(out_test.parent),
                    "lock_path": str(lock_path),
                },
            },
        },
    ]

    stdin = io.StringIO("\n".join(json.dumps(r) for r in requests) + "\n")
    stdout = io.StringIO()
    assert serve_stdio(stdin=stdin, stdout=stdout) == 0

    responses = [json.loads(line) for line in stdout.getvalue().splitlines() if line.strip()]
    assert [r["id"] for r in responses] == [1, 2, 3, 4, 5]
    assert responses[0]["result"]["serverInfo"]["name"] == "pairspec"
    tool_names = {t["name"] for t in responses[1]["result"]["tools"]}
    assert {"pairspec_generate", "pairspec_emit", "pairspec_check_drift", "pairspec_diff_locks", "pairspec_extract_spec"} <= tool_names

    gen_payload = json.loads(responses[2]["result"]["content"][0]["text"])
    assert gen_payload["coverage"]["coverage_pct"] == 100.0
    assert lock_path.exists()

    drift_payload = json.loads(responses[4]["result"]["content"][0]["text"])
    assert drift_payload["is_clean"] is True
    assert drift_payload["traceability_pct"] == 100.0


def test_cli_drift_exit_codes(tmp_path: Path) -> None:
    spec_path = tmp_path / "pairspec.toml"
    lock_path = tmp_path / "pairspec.lock.json"
    tests_dir = tmp_path / "tests"
    test_file = tests_dir / "test_suite.py"

    assert cli_main(["init", str(spec_path)]) == 0
    assert cli_main(["generate", str(spec_path), "--lock", str(lock_path), "--out", str(tmp_path / "matrix.md")]) == 0
    assert cli_main(["emit", str(spec_path), "--format", "pytest", "--out", str(test_file)]) == 0
    assert cli_main(["drift", str(spec_path), "--tests", str(tests_dir), "--lock", str(lock_path)]) == 0

    # Delete test file -> drift must exit with code 2
    test_file.unlink()
    assert cli_main(["drift", str(spec_path), "--tests", str(tests_dir), "--lock", str(lock_path)]) == 2


def test_mirror_trap_demo_catches_all_five_bugs() -> None:
    demo_path = Path(__file__).resolve().parent.parent / "examples" / "fintech_payout" / "mirror_trap_demo.py"
    spec = importlib.util.spec_from_file_location("mirror_trap_demo", demo_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    summary = mod.run_demo()
    assert summary["coverage_pct"] == 100.0
    assert summary["mirror_trap_passed"] == summary["mirror_trap_total"]
    assert summary["mirror_trap_bugs_caught"] == 0
    assert len(summary["pairspec_unique_bugs_caught"]) == 5
