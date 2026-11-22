"""Stdlib-only CI drift reporters: text, JSON, SARIF 2.1.0, JUnit XML, and GitHub Markdown Summary."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from typing import Any

from pairspec.lockfile import DriftReport

SARIF_SCHEMA = "https://json.schemastore.org/sarif-2.1.0.json"
SUPPORTED_DRIFT_FORMATS = ("text", "json", "sarif", "junit", "markdown")

_SARIF_RULES: list[dict[str, Any]] = [
    {
        "id": "PS001",
        "name": "MissingTestCase",
        "shortDescription": {"text": "Combinatorial spec case is missing from the test suite"},
        "defaultConfiguration": {"level": "error"},
    },
    {
        "id": "PS002",
        "name": "OrphanedTestTag",
        "shortDescription": {"text": "Test file references a @pairspec ID not present in the active spec"},
        "defaultConfiguration": {"level": "warning"},
    },
    {
        "id": "PS003",
        "name": "OracleChanged",
        "shortDescription": {"text": "Blind spec oracle changed compared to committed pairspec.lock.json"},
        "defaultConfiguration": {"level": "error"},
    },
    {
        "id": "PS004",
        "name": "LockfileDrift",
        "shortDescription": {"text": "Committed pairspec.lock.json does not match the current spec matrix"},
        "defaultConfiguration": {"level": "warning"},
    },
]


def _normalize_format(fmt: str) -> str:
    key = fmt.strip().lower()
    if key == "md":
        return "markdown"
    if key not in SUPPORTED_DRIFT_FORMATS:
        expected = ", ".join(SUPPORTED_DRIFT_FORMATS)
        raise ValueError(f"Unsupported drift report format: {fmt!r} (expected: {expected})")
    return key


def _parse_location(raw: str) -> tuple[str, int]:
    """Parse 'path/to/file.py:42' (preserving Windows drive letters if present)."""
    if ":" in raw:
        path_part, line_part = raw.rsplit(":", 1)
        if line_part.isdigit():
            return path_part, max(1, int(line_part))
    return raw, 1


def _sarif_location(uri: str, start_line: int = 1) -> dict[str, Any]:
    return {
        "physicalLocation": {
            "artifactLocation": {"uri": uri.replace("\\", "/")},
            "region": {"startLine": max(1, start_line)},
        }
    }


def render_drift_text(
    report: DriftReport,
    spec_name: str,
    spec_fingerprint: str,
    elapsed_ms: float = 0.0,
) -> str:
    status = "CLEAN" if report.is_clean else "DRIFT DETECTED"
    covered = report.spec_case_count - len(report.missing_in_tests)
    lines = [
        f"[PairSpec Drift] {status} ({elapsed_ms:.1f}ms) | "
        f"spec={spec_name} ({spec_fingerprint}) | "
        f"traceability={report.coverage_pct}% ({covered}/{report.spec_case_count})"
    ]
    if report.missing_in_tests:
        lines.append(f"  Missing in tests ({len(report.missing_in_tests)}): {', '.join(report.missing_in_tests)}")
    if report.orphaned_in_tests:
        lines.append(f"  Orphaned test tags ({len(report.orphaned_in_tests)}): {', '.join(report.orphaned_in_tests)}")
    if report.lock_diff and report.lock_diff.has_changes:
        ld = report.lock_diff
        lines.append(
            f"  Lock diff ({ld.old_fingerprint} -> {ld.new_fingerprint}): "
            f"+{len(ld.added)} added, -{len(ld.removed)} removed, ~{len(ld.oracle_changed)} oracle changed"
        )
        for oc in ld.oracle_changed:
            lines.append(f"    ~ {oc.case_id}: {oc.old_expected} -> {oc.new_expected}")
    return "\n".join(lines) + "\n"


def render_drift_json(report: DriftReport, elapsed_ms: float = 0.0) -> str:
    payload = report.to_dict()
    payload["elapsed_ms"] = round(elapsed_ms, 2)
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def render_drift_sarif(
    report: DriftReport,
    spec_path: str,
    tool_version: str,
) -> str:
    """Render a SARIF 2.1.0 document for GitHub Code Scanning / PR annotations."""
    results: list[dict[str, Any]] = []

    for cid in report.missing_in_tests:
        results.append(
            {
                "ruleId": "PS001",
                "level": "error",
                "message": {"text": f"Missing test case for @pairspec:{cid} defined by {spec_path}."},
                "locations": [_sarif_location(spec_path, 1)],
            }
        )

    for cid in report.orphaned_in_tests:
        raw_locs = report.implemented_ids.get(cid) or [spec_path]
        for raw_loc in raw_locs:
            uri, line = _parse_location(raw_loc)
            results.append(
                {
                    "ruleId": "PS002",
                    "level": "warning",
                    "message": {"text": f"Orphaned test tag @pairspec:{cid} is no longer in {spec_path}."},
                    "locations": [_sarif_location(uri, line)],
                }
            )

    if report.lock_diff and report.lock_diff.has_changes:
        ld = report.lock_diff
        for oc in ld.oracle_changed:
            raw_locs = report.implemented_ids.get(oc.case_id) or [spec_path]
            uri, line = _parse_location(raw_locs[0])
            results.append(
                {
                    "ruleId": "PS003",
                    "level": "error",
                    "message": {
                        "text": (
                            f"Blind oracle changed for @pairspec:{oc.case_id}: "
                            f"{oc.old_expected} -> {oc.new_expected}."
                        )
                    },
                    "locations": [_sarif_location(uri, line)],
                }
            )
        if ld.added or ld.removed:
            results.append(
                {
                    "ruleId": "PS004",
                    "level": "warning",
                    "message": {
                        "text": (
                            f"Lockfile drift ({ld.old_fingerprint} -> {ld.new_fingerprint}): "
                            f"+{len(ld.added)} added, -{len(ld.removed)} removed."
                        )
                    },
                    "locations": [_sarif_location(spec_path, 1)],
                }
            )

    sarif: dict[str, Any] = {
        "$schema": SARIF_SCHEMA,
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "PairSpec",
                        "version": tool_version,
                        "informationUri": "https://github.com/Beusseb/pairspec",
                        "rules": _SARIF_RULES,
                    }
                },
                "results": results,
            }
        ],
    }
    return json.dumps(sarif, indent=2, ensure_ascii=False) + "\n"


def render_drift_junit(
    report: DriftReport,
    spec_name: str,
    expected_case_ids: list[str],
    elapsed_ms: float = 0.0,
) -> str:
    """Render JUnit XML compatible with Allure 3, GitLab CI, Jenkins, and GitHub Actions."""
    missing_set = set(report.missing_in_tests)
    oracle_map = (
        {oc.case_id: oc for oc in report.lock_diff.oracle_changed}
        if report.lock_diff is not None
        else {}
    )

    testsuites = ET.Element("testsuites")
    total_tests = len(expected_case_ids) + len(report.orphaned_in_tests)
    lock_has_structural_drift = bool(
        report.lock_diff and (report.lock_diff.added or report.lock_diff.removed)
    )
    if lock_has_structural_drift:
        total_tests += 1

    failures = 0
    suite = ET.SubElement(
        testsuites,
        "testsuite",
        attrib={
            "name": f"pairspec.drift.{spec_name}",
            "tests": str(total_tests),
            "failures": "0",
            "errors": "0",
            "time": f"{elapsed_ms / 1000.0:.4f}",
        },
    )

    for cid in expected_case_ids:
        tc = ET.SubElement(suite, "testcase", attrib={"classname": f"pairspec.{spec_name}", "name": f"@pairspec:{cid}"})
        if cid in missing_set:
            failures += 1
            fail = ET.SubElement(tc, "failure", attrib={"type": "MissingTestCase", "message": f"Missing test for @pairspec:{cid}"})
            fail.text = f"Case @pairspec:{cid} exists in spec '{spec_name}' but has no tagged test."
        elif cid in oracle_map:
            failures += 1
            oc = oracle_map[cid]
            fail = ET.SubElement(tc, "failure", attrib={"type": "OracleChanged", "message": f"Oracle changed for @pairspec:{cid}"})
            fail.text = f"Expected oracle changed: {oc.old_expected} -> {oc.new_expected}"

    for cid in report.orphaned_in_tests:
        failures += 1
        locs = ", ".join(report.implemented_ids.get(cid) or [])
        tc = ET.SubElement(suite, "testcase", attrib={"classname": f"pairspec.{spec_name}.orphaned", "name": f"@pairspec:{cid}"})
        fail = ET.SubElement(tc, "failure", attrib={"type": "OrphanedTestTag", "message": f"Orphaned tag @pairspec:{cid}"})
        fail.text = f"Tag @pairspec:{cid} ({locs}) is not part of the active spec '{spec_name}'."

    if lock_has_structural_drift and report.lock_diff is not None:
        failures += 1
        ld = report.lock_diff
        tc = ET.SubElement(suite, "testcase", attrib={"classname": f"pairspec.{spec_name}.lockfile", "name": "lockfile_sync"})
        fail = ET.SubElement(tc, "failure", attrib={"type": "LockfileDrift", "message": "Lockfile out of sync with spec"})
        fail.text = f"+{len(ld.added)} added, -{len(ld.removed)} removed ({ld.old_fingerprint} -> {ld.new_fingerprint})"

    suite.set("failures", str(failures))
    ET.indent(testsuites, space="  ")
    return '<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(testsuites, encoding="unicode") + "\n"


def render_drift_markdown(
    report: DriftReport,
    spec_name: str,
    spec_fingerprint: str,
    elapsed_ms: float = 0.0,
) -> str:
    """Render a compact Markdown summary table suited for $GITHUB_STEP_SUMMARY."""
    covered = report.spec_case_count - len(report.missing_in_tests)
    status_badge = "✅ CLEAN" if report.is_clean else "❌ DRIFT DETECTED"
    lines = [
        f"## PairSpec Drift Gate — `{spec_name}`",
        "",
        "| Metric | Value |",
        "| :--- | :--- |",
        f"| **Status** | {status_badge} |",
        f"| **Spec fingerprint** | `{spec_fingerprint}` |",
        f"| **Traceability** | `{report.coverage_pct}%` ({covered}/{report.spec_case_count}) |",
        f"| **Missing in tests** | `{len(report.missing_in_tests)}` |",
        f"| **Orphaned tags** | `{len(report.orphaned_in_tests)}` |",
        f"| **Elapsed** | `{elapsed_ms:.1f} ms` |",
    ]
    if report.lock_diff is not None:
        ld = report.lock_diff
        lines.append(
            f"| **Lockfile diff** | `+{len(ld.added)}` added / `-{len(ld.removed)}` removed / `~{len(ld.oracle_changed)}` oracle changed |"
        )
    if report.missing_in_tests:
        lines.extend(["", "### Missing Cases", ", ".join(f"`@pairspec:{c}`" for c in report.missing_in_tests)])
    if report.orphaned_in_tests:
        lines.extend(["", "### Orphaned Tags", ", ".join(f"`@pairspec:{c}`" for c in report.orphaned_in_tests)])
    if report.lock_diff and report.lock_diff.oracle_changed:
        lines.extend(["", "### Oracle Changes", "", "| Case ID | Previous Oracle | New Oracle |", "| :--- | :--- | :--- |"])
        for oc in report.lock_diff.oracle_changed:
            lines.append(f"| `@pairspec:{oc.case_id}` | `{oc.old_expected}` | `{oc.new_expected}` |")
    lines.append("")
    return "\n".join(lines)


def render_drift_report(
    report: DriftReport,
    *,
    spec_name: str,
    spec_fingerprint: str,
    spec_path: str,
    expected_case_ids: list[str],
    fmt: str = "text",
    elapsed_ms: float = 0.0,
    tool_version: str = "0.1.0",
) -> str:
    """Render a DriftReport in text, json, sarif, junit, or markdown format."""
    key = _normalize_format(fmt)
    if key == "text":
        return render_drift_text(report, spec_name, spec_fingerprint, elapsed_ms=elapsed_ms)
    if key == "json":
        return render_drift_json(report, elapsed_ms=elapsed_ms)
    if key == "sarif":
        return render_drift_sarif(report, spec_path=spec_path, tool_version=tool_version)
    if key == "junit":
        return render_drift_junit(report, spec_name=spec_name, expected_case_ids=expected_case_ids, elapsed_ms=elapsed_ms)
    return render_drift_markdown(report, spec_name=spec_name, spec_fingerprint=spec_fingerprint, elapsed_ms=elapsed_ms)
