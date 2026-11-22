"""Tests for pairspec.lock.json, semantic diffing, and CI Spec Drift detection."""

from __future__ import annotations

from pathlib import Path

from pairspec.emitters import emit_suite
from pairspec.ipog import generate_matrix
from pairspec.lockfile import build_lockfile, check_test_drift, diff_lockfiles, load_lockfile, save_lockfile
from pairspec.schema import SpecDocument


def _make_spec(fee: int = 150, extra_channel: bool = False) -> SpecDocument:
    channels = ["web", "mobile", "api"] if extra_channel else ["web", "mobile"]
    return SpecDocument.from_dict(
        {
            "name": "payout",
            "version": "1.0.0",
            "parameters": [
                {"name": "channel", "type": "enum", "values": channels},
                {"name": "rail", "type": "enum", "values": ["SEPA", "SWIFT"]},
                {"name": "vip", "type": "bool"},
            ],
            "default_expect": {"fee_bps": 0},
            "oracles": [
                {"name": "swift_fee", "priority": 10, "when": "rail == 'SWIFT'", "expect": {"fee_bps": fee}}
            ],
        }
    )


def test_lockfile_roundtrip_and_clean_drift(tmp_path: Path) -> None:
    spec = _make_spec()
    res = generate_matrix(spec)
    lock_path = save_lockfile(res, tmp_path / "pairspec.lock.json")
    loaded = load_lockfile(lock_path)

    test_file = tmp_path / "tests" / "test_payout.py"
    test_file.parent.mkdir()
    test_file.write_text(emit_suite(res, "pytest"), encoding="utf-8")

    report = check_test_drift(loaded, test_file.parent, previous_lock=loaded)
    assert report.is_clean is True
    assert report.coverage_pct == 100.0
    assert report.missing_in_tests == []
    assert report.orphaned_in_tests == []


def test_drift_detects_missing_orphaned_and_oracle_changes(tmp_path: Path) -> None:
    spec_v1 = _make_spec(fee=150)
    res_v1 = generate_matrix(spec_v1)
    lock_v1 = build_lockfile(res_v1)

    # Write tests for v1 PLUS one orphaned tag, and omit one real case
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    kept_ids = [c.case_id for c in res_v1.cases[:-1]]
    body = "\n".join(f"# @pairspec:{cid}" for cid in kept_ids) + "\n# @pairspec:PS-DEADBEEF\n"
    (tests_dir / "test_partial.kt").write_text(body, encoding="utf-8")

    # Evolve spec: change SWIFT fee oracle 150 -> 200
    spec_v2 = _make_spec(fee=200)
    res_v2 = generate_matrix(spec_v2)
    lock_v2 = build_lockfile(res_v2)

    diff = diff_lockfiles(lock_v1, lock_v2)
    assert diff.has_changes is True
    assert len(diff.oracle_changed) > 0
    assert diff.added == []
    assert diff.removed == []

    report = check_test_drift(lock_v2, tests_dir, previous_lock=lock_v1)
    assert report.is_clean is False
    assert res_v1.cases[-1].case_id in report.missing_in_tests
    assert "PS-DEADBEEF" in report.orphaned_in_tests
    assert report.lock_diff is not None and report.lock_diff.has_changes is True
