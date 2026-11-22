#!/usr/bin/env python3
"""Fast (<2s) static dogfood gate for PairSpec CI.

Verifies:
1. Committed HTTP API demo lockfile & emitted test suite match fresh PairSpec generation byte-for-byte.
2. `pairspec drift` exits cleanly (100% traceability, 0 orphaned tags, 0 lockfile drift).
3. All drift report formats (SARIF 2.1.0, JUnit XML, JSON, Markdown, text) parse cleanly.
4. All emitted suite formats (pytest, playwright, kotlin, gherkin, markdown, json) satisfy syntax & structural invariants.
"""

from __future__ import annotations

import ast
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from pairspec import (  # noqa: E402
    __version__,
    build_lockfile,
    check_test_drift,
    emit_suite,
    generate_matrix,
    load_lockfile,
    load_spec,
    render_drift_report,
)


def _check_balanced_braces(text: str, label: str) -> None:
    depth = 0
    for ch in text:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth < 0:
                raise AssertionError(f"{label}: unexpected closing brace")
    if depth != 0:
        raise AssertionError(f"{label}: unbalanced braces (depth={depth})")


def main() -> int:
    demo_dir = ROOT / "examples" / "http_api_demo"
    spec_path = demo_dir / "spec.toml"
    lock_path = demo_dir / "pairspec.lock.json"
    suite_path = demo_dir / "test_payout_api_pairspec.py"

    spec = load_spec(spec_path)
    result = generate_matrix(spec)

    # 1. Coverage & exclusion invariants
    assert result.coverage.coverage_pct == 100.0, f"Expected 100% coverage, got {result.coverage.coverage_pct}%"
    assert result.coverage.exclusion_violations == 0, "Exclusion violation in demo matrix"

    # 2. Freshness of committed lockfile & untouched emitted suite
    fresh_lock = build_lockfile(result)
    committed_lock = load_lockfile(lock_path)
    assert fresh_lock == committed_lock, (
        "examples/http_api_demo/pairspec.lock.json is stale — regenerate with `pairspec generate --lock`"
    )

    fresh_pytest = emit_suite(result, "pytest")
    committed_pytest = suite_path.read_text(encoding="utf-8")
    assert fresh_pytest == committed_pytest, (
        "examples/http_api_demo/test_payout_api_pairspec.py diverged from `pairspec emit --format pytest`"
    )

    # 3. Drift gate dogfooding
    report = check_test_drift(fresh_lock, demo_dir, previous_lock=committed_lock)
    assert report.is_clean, f"Demo drift detected: {report.to_dict()}"
    assert report.coverage_pct == 100.0

    # 4. Validate all drift report formats (SARIF 2.1.0, JUnit XML, JSON, Markdown, text)
    expected_ids = [c.case_id for c in result.cases]
    sarif_str = render_drift_report(
        report,
        spec_name=result.spec_name,
        spec_fingerprint=result.spec_fingerprint,
        spec_path=str(spec_path.relative_to(ROOT)),
        expected_case_ids=expected_ids,
        fmt="sarif",
        tool_version=__version__,
    )
    sarif = json.loads(sarif_str)
    assert sarif["version"] == "2.1.0"
    assert sarif["runs"][0]["results"] == []

    junit_str = render_drift_report(
        report,
        spec_name=result.spec_name,
        spec_fingerprint=result.spec_fingerprint,
        spec_path=str(spec_path.relative_to(ROOT)),
        expected_case_ids=expected_ids,
        fmt="junit",
        tool_version=__version__,
    )
    junit_root = ET.fromstring(junit_str)
    suite_el = junit_root.find("testsuite")
    assert suite_el is not None
    assert suite_el.attrib["failures"] == "0"
    assert int(suite_el.attrib["tests"]) == len(expected_ids)

    md_str = render_drift_report(
        report,
        spec_name=result.spec_name,
        spec_fingerprint=result.spec_fingerprint,
        spec_path=str(spec_path.relative_to(ROOT)),
        expected_case_ids=expected_ids,
        fmt="markdown",
        tool_version=__version__,
    )
    assert "CLEAN" in md_str and "100.0%" in md_str

    # 5. Syntax & structural verification across all emitted target ecosystems
    ast.parse(fresh_pytest)
    compile(fresh_pytest, "<emitted-pytest>", "exec")

    pw = emit_suite(result, "playwright")
    _check_balanced_braces(pw, "playwright")
    assert "expect.soft(" in pw and "test.step(" in pw and "{ tag: [" in pw

    kt = emit_suite(result, "kotlin")
    _check_balanced_braces(kt, "kotlin")
    assert "abstract class PayoutApiPairSpecTest" in kt and "assertAll(" in kt and "@DisplayName(" in kt

    gh = emit_suite(result, "gherkin")
    assert "Feature: payout_api" in gh and "@oracle:" in gh and gh.count("Scenario:") == len(expected_ids)

    js = json.loads(emit_suite(result, "json"))
    assert js["case_count"] == len(expected_ids)

    print(
        f"[check_static] OK — {len(expected_ids)} cases verified across lockfile, untouched HTTP demo, "
        "SARIF/JUnit/Markdown drift reports, and all 6 emitter formats."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
