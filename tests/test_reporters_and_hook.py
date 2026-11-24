"""Tests for stdlib CI drift reporters (SARIF 2.1.0, JUnit XML, Markdown, JSON, text) and the PreToolUse hook."""

from __future__ import annotations

import importlib.util
import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from pairspec.cli import main as cli_main
from pairspec.ipog import generate_matrix
from pairspec.lockfile import build_lockfile, check_test_drift, save_lockfile
from pairspec.reporters import SARIF_SCHEMA, render_drift_report
from pairspec.schema import SpecDocument


def _load_hook_module():
    hook_path = Path(__file__).resolve().parent.parent / "hooks" / "anti_mirror_trap_hook.py"
    spec = importlib.util.spec_from_file_location("anti_mirror_trap_hook", hook_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_drift_scenario(tmp_path: Path):
    spec_v1 = SpecDocument.from_dict(
        {
            "name": "checkout",
            "parameters": [
                {"name": "country", "type": "enum", "values": ["FR", "US"]},
                {"name": "vip", "type": "bool"},
            ],
            "default_expect": {"fee": 5},
            "oracles": [{"name": "vip_free", "when": "vip == True", "expect": {"fee": 0}}],
        }
    )
    spec_v2 = SpecDocument.from_dict(
        {
            "name": "checkout",
            "parameters": [
                {"name": "country", "type": "enum", "values": ["FR", "US", "UK"]},
                {"name": "vip", "type": "bool"},
            ],
            "default_expect": {"fee": 5},
            "oracles": [{"name": "vip_free", "when": "vip == True", "expect": {"fee": 1}}],
        }
    )
    res_v1 = generate_matrix(spec_v1)
    res_v2 = generate_matrix(spec_v2)
    lock_v1 = build_lockfile(res_v1)
    lock_v2 = build_lockfile(res_v2)

    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    # Keep first case from v2, drop the rest, and plant an orphaned tag
    kept_id = res_v2.cases[0].case_id
    (tests_dir / "test_checkout.py").write_text(
        f"# @pairspec:{kept_id}\n# @pairspec:PS-DEADBEEF\n",
        encoding="utf-8",
    )
    report = check_test_drift(lock_v2, tests_dir, previous_lock=lock_v1)
    return res_v2, report, tests_dir


def test_reporters_sarif_junit_markdown_json_text_on_drift(tmp_path: Path) -> None:
    res, report, _ = _make_drift_scenario(tmp_path)
    assert not report.is_clean
    expected_ids = [c.case_id for c in res.cases]

    sarif = json.loads(
        render_drift_report(
            report,
            spec_name=res.spec_name,
            spec_fingerprint=res.spec_fingerprint,
            spec_path="specs/checkout.toml",
            expected_case_ids=expected_ids,
            fmt="sarif",
            elapsed_ms=4.2,
            tool_version="0.1.0",
        )
    )
    assert sarif["$schema"] == SARIF_SCHEMA
    assert sarif["version"] == "2.1.0"
    rule_ids = {r["ruleId"] for r in sarif["runs"][0]["results"]}
    assert {"PS001", "PS002", "PS003", "PS004"} <= rule_ids

    junit_xml = render_drift_report(
        report,
        spec_name=res.spec_name,
        spec_fingerprint=res.spec_fingerprint,
        spec_path="specs/checkout.toml",
        expected_case_ids=expected_ids,
        fmt="junit",
        elapsed_ms=4.2,
    )
    root = ET.fromstring(junit_xml)
    suite = root.find("testsuite")
    assert suite is not None
    assert int(suite.attrib["failures"]) > 0
    failure_types = {el.attrib["type"] for el in root.findall(".//failure")}
    assert {"MissingTestCase", "OrphanedTestTag", "LockfileDrift"} <= failure_types

    md = render_drift_report(
        report,
        spec_name=res.spec_name,
        spec_fingerprint=res.spec_fingerprint,
        spec_path="specs/checkout.toml",
        expected_case_ids=expected_ids,
        fmt="md",
        elapsed_ms=4.2,
    )
    assert "DRIFT DETECTED" in md
    assert "Missing Cases" in md
    assert "Orphaned Tags" in md
    assert "Oracle Changes" in md

    js = json.loads(
        render_drift_report(
            report,
            spec_name=res.spec_name,
            spec_fingerprint=res.spec_fingerprint,
            spec_path="specs/checkout.toml",
            expected_case_ids=expected_ids,
            fmt="json",
            elapsed_ms=4.2,
        )
    )
    assert js["is_clean"] is False
    assert js["elapsed_ms"] == 4.2

    txt = render_drift_report(
        report,
        spec_name=res.spec_name,
        spec_fingerprint=res.spec_fingerprint,
        spec_path="specs/checkout.toml",
        expected_case_ids=expected_ids,
        fmt="text",
        elapsed_ms=4.2,
    )
    assert "DRIFT DETECTED" in txt
    assert "Orphaned test tags" in txt
    assert "Lock diff" in txt

    with pytest.raises(ValueError, match="Unsupported drift report format"):
        render_drift_report(
            report,
            spec_name=res.spec_name,
            spec_fingerprint=res.spec_fingerprint,
            spec_path="specs/checkout.toml",
            expected_case_ids=expected_ids,
            fmt="xml",
        )


def test_cli_drift_format_out_and_summary_file(tmp_path: Path) -> None:
    spec_path = tmp_path / "spec.toml"
    lock_path = tmp_path / "pairspec.lock.json"
    tests_file = tmp_path / "tests" / "test_gen.py"
    sarif_out = tmp_path / "reports" / "drift.sarif"
    junit_out = tmp_path / "reports" / "drift.xml"
    summary_out = tmp_path / "step_summary.md"

    assert cli_main(["init", str(spec_path)]) == 0
    assert cli_main(["generate", str(spec_path), "--lock", str(lock_path), "--out", str(tmp_path / "matrix.md")]) == 0
    assert cli_main(["emit", str(spec_path), "--format", "pytest", "--out", str(tests_file)]) == 0

    rc = cli_main(
        [
            "drift",
            str(spec_path),
            "--tests",
            str(tests_file.parent),
            "--lock",
            str(lock_path),
            "--format",
            "sarif",
            "--out",
            str(sarif_out),
            "--summary-file",
            str(summary_out),
        ]
    )
    assert rc == 0
    assert json.loads(sarif_out.read_text(encoding="utf-8"))["runs"][0]["results"] == []
    assert "CLEAN" in summary_out.read_text(encoding="utf-8")

    rc_junit = cli_main(
        [
            "drift",
            str(spec_path),
            "--tests",
            str(tests_file.parent),
            "--lock",
            str(lock_path),
            "--format",
            "junit",
            "--out",
            str(junit_out),
        ]
    )
    assert rc_junit == 0
    assert ET.fromstring(junit_out.read_text(encoding="utf-8")).find("testsuite").attrib["failures"] == "0"


def test_anti_mirror_trap_hook_blocks_sut_before_lock_and_allows_after(tmp_path: Path) -> None:
    hook = _load_hook_module()
    (tmp_path / "src").mkdir()
    (tmp_path / "specs").mkdir()
    sut_file = tmp_path / "src" / "payout.py"
    spec_file = tmp_path / "specs" / "payout.toml"
    sut_file.write_text("def pay(): pass\n", encoding="utf-8")
    spec_file.write_text("[spec]\nname = 'p'\n", encoding="utf-8")

    # Reading the functional spec before lockfile is ALWAYS allowed
    code, msg = hook.evaluate_hook_event(
        {"tool_name": "Read", "tool_input": {"file_path": str(spec_file)}, "cwd": str(tmp_path)}
    )
    assert code == 0 and msg == ""

    # Reading SUT implementation before pairspec.lock.json exists is BLOCKED (exit 2)
    code, msg = hook.evaluate_hook_event(
        {"tool_name": "Read", "tool_input": {"file_path": str(sut_file)}, "cwd": str(tmp_path)}
    )
    assert code == 2
    assert "Anti-Mirror-Trap" in msg

    # Non-guarded tools pass through
    code, _ = hook.evaluate_hook_event(
        {"tool_name": "WebFetch", "tool_input": {"url": "https://example.com"}, "cwd": str(tmp_path)}
    )
    assert code == 0

    # Bypass flag allows reading explicitly
    code, _ = hook.evaluate_hook_event(
        {"tool_name": "Read", "tool_input": {"file_path": str(sut_file)}, "cwd": str(tmp_path)},
        bypass=True,
    )
    assert code == 0

    # Once blind oracles are locked into pairspec.lock.json, reading SUT is ALLOWED
    save_lockfile(
        generate_matrix(
            SpecDocument.from_dict(
                {"name": "p", "parameters": [{"name": "a", "type": "bool"}, {"name": "b", "type": "bool"}]}
            )
        ),
        tmp_path / "pairspec.lock.json",
    )
    code, msg = hook.evaluate_hook_event(
        {"tool_name": "Read", "tool_input": {"file_path": str(sut_file)}, "cwd": str(tmp_path)}
    )
    assert code == 0 and msg == ""


def _hook_code(hook, tmp_path: Path, tool_name: str, tool_input: dict) -> int:
    code, _ = hook.evaluate_hook_event({"tool_name": tool_name, "tool_input": tool_input, "cwd": str(tmp_path)})
    return code


@pytest.mark.parametrize(
    "command",
    [
        "cat src/payout.py",
        "head -n 50 ./src/payout.py",
        "grep -rn amount src",
        "python3 -c \"print(open('src/payout.py').read())\"",
        "wc -l <src/payout.py",
    ],
)
def test_hook_blocks_bash_reading_sut_before_lock(tmp_path: Path, command: str) -> None:
    hook = _load_hook_module()
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "payout.py").write_text("def pay(): pass\n", encoding="utf-8")
    assert _hook_code(hook, tmp_path, "Bash", {"command": command}) == 2


@pytest.mark.parametrize(
    "command",
    ["ls", "pytest tests/", "cat specs/payout.toml", "pip install lib", "echo 'unbalanced"],
)
def test_hook_allows_bash_without_sut_path(tmp_path: Path, command: str) -> None:
    hook = _load_hook_module()
    (tmp_path / "src").mkdir()
    (tmp_path / "specs").mkdir()
    (tmp_path / "specs" / "payout.toml").write_text("[spec]\n", encoding="utf-8")
    assert _hook_code(hook, tmp_path, "Bash", {"command": command}) == 0


def test_hook_treats_grep_without_path_as_sut_search(tmp_path: Path) -> None:
    hook = _load_hook_module()
    assert _hook_code(hook, tmp_path, "Grep", {"pattern": "def "}) == 0  # no SUT dir in this project
    (tmp_path / "src").mkdir()
    assert _hook_code(hook, tmp_path, "Grep", {"pattern": "def "}) == 2
    assert _hook_code(hook, tmp_path, "Grep", {"pattern": "def ", "path": "specs"}) == 0


def test_hook_known_bypass_path_built_at_run_time(tmp_path: Path) -> None:
    """Documented limit: a path assembled by the shell is invisible to the hook."""
    hook = _load_hook_module()
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "payout.py").write_text("def pay(): pass\n", encoding="utf-8")
    assert _hook_code(hook, tmp_path, "Bash", {"command": "cat $(printf sr)c/payout.py"}) == 0
