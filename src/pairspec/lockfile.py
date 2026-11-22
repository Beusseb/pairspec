"""Portable `pairspec.lock.json` lockfile and <2s CI Spec Drift detector (`@pairspec:PS-XXXXXXXX`)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pairspec.ipog import GenerationResult

LOCKFILE_SCHEMA_VERSION = "1"

# Matches `@pairspec:PS-A1B2C3D4` inside Python/TS/Kotlin/Gherkin comments, decorators, tags, or strings
TAG_PATTERN = re.compile(r"@pairspec:(PS-[0-9A-Fa-f]{8})\b")

DEFAULT_TEST_EXTENSIONS = {
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".kt",
    ".kts",
    ".java",
    ".feature",
    ".spec",
}

SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    "build",
    "dist",
    ".gradle",
    "target",
}


def build_lockfile(result: GenerationResult) -> dict[str, Any]:
    """Build a deterministic, Git-friendly lockfile (no volatile timestamps)."""
    return {
        "schema_version": LOCKFILE_SCHEMA_VERSION,
        "spec_name": result.spec_name,
        "spec_version": result.spec_version,
        "spec_fingerprint": result.spec_fingerprint,
        "strength": result.strength,
        "cartesian_size": result.cartesian_size,
        "case_count": len(result.cases),
        "reduction_pct": result.reduction_pct,
        "coverage_pct": result.coverage.coverage_pct,
        "cases": {
            c.case_id: {
                "inputs": c.inputs,
                "expected": c.expected,
                "matched_oracles": c.matched_oracles,
            }
            for c in result.cases
        },
    }


def save_lockfile(result: GenerationResult, path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = build_lockfile(result)
    target.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return target


def load_lockfile(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


@dataclass(frozen=True)
class OracleChange:
    case_id: str
    old_expected: dict[str, Any]
    new_expected: dict[str, Any]


@dataclass(frozen=True)
class LockDiff:
    old_fingerprint: str
    new_fingerprint: str
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    oracle_changed: list[OracleChange] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return bool(self.added or self.removed or self.oracle_changed)

    def to_dict(self) -> dict[str, Any]:
        return {
            "old_fingerprint": self.old_fingerprint,
            "new_fingerprint": self.new_fingerprint,
            "has_changes": self.has_changes,
            "added": self.added,
            "removed": self.removed,
            "oracle_changed": [
                {
                    "id": c.case_id,
                    "old_expected": c.old_expected,
                    "new_expected": c.new_expected,
                }
                for c in self.oracle_changed
            ],
            "unchanged_count": len(self.unchanged),
        }


def diff_lockfiles(old_lock: dict[str, Any], new_lock: dict[str, Any]) -> LockDiff:
    """Compute semantic diff between two lockfiles (added/removed cases + oracle changes)."""
    old_cases: dict[str, Any] = old_lock.get("cases") or {}
    new_cases: dict[str, Any] = new_lock.get("cases") or {}

    old_ids = set(old_cases.keys())
    new_ids = set(new_cases.keys())

    added = sorted(new_ids - old_ids)
    removed = sorted(old_ids - new_ids)
    oracle_changed: list[OracleChange] = []
    unchanged: list[str] = []

    for cid in sorted(old_ids & new_ids):
        old_exp = old_cases[cid].get("expected") or {}
        new_exp = new_cases[cid].get("expected") or {}
        if old_exp != new_exp:
            oracle_changed.append(OracleChange(case_id=cid, old_expected=old_exp, new_expected=new_exp))
        else:
            unchanged.append(cid)

    return LockDiff(
        old_fingerprint=str(old_lock.get("spec_fingerprint") or ""),
        new_fingerprint=str(new_lock.get("spec_fingerprint") or ""),
        added=added,
        removed=removed,
        oracle_changed=oracle_changed,
        unchanged=unchanged,
    )


@dataclass(frozen=True)
class DriftReport:
    spec_case_count: int
    implemented_ids: dict[str, list[str]]  # case_id -> list of "file:line"
    missing_in_tests: list[str]
    orphaned_in_tests: list[str]
    lock_diff: LockDiff | None = None

    @property
    def is_clean(self) -> bool:
        if self.missing_in_tests or self.orphaned_in_tests:
            return False
        if self.lock_diff is not None and self.lock_diff.has_changes:
            return False
        return True

    @property
    def coverage_pct(self) -> float:
        if self.spec_case_count == 0:
            return 100.0
        covered = self.spec_case_count - len(self.missing_in_tests)
        return round((covered / self.spec_case_count) * 100.0, 2)

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_clean": self.is_clean,
            "spec_case_count": self.spec_case_count,
            "implemented_count": len(self.implemented_ids),
            "traceability_pct": self.coverage_pct,
            "missing_in_tests": self.missing_in_tests,
            "orphaned_in_tests": self.orphaned_in_tests,
            "implemented_locations": self.implemented_ids,
            "lock_diff": self.lock_diff.to_dict() if self.lock_diff else None,
        }


def scan_test_tags(tests_root: str | Path, extensions: set[str] | None = None) -> dict[str, list[str]]:
    """Scan test files for `@pairspec:PS-XXXXXXXX` traceability tags."""
    root = Path(tests_root)
    exts = extensions or DEFAULT_TEST_EXTENSIONS
    found: dict[str, list[str]] = {}

    files: list[Path] = []
    if root.is_file():
        files = [root]
    elif root.is_dir():
        for path in sorted(root.rglob("*")):
            # Relative parts only: a checkout living under e.g. `~/build/` must still be scanned.
            if any(part in SKIP_DIRS for part in path.relative_to(root).parts):
                continue
            if path.is_file() and path.suffix.lower() in exts:
                files.append(path)

    for file_path in files:
        try:
            text = file_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            for match in TAG_PATTERN.finditer(line):
                cid = match.group(1).upper()
                loc = f"{file_path}:{lineno}"
                found.setdefault(cid, []).append(loc)
    return found


def check_test_drift(
    current_lock: dict[str, Any],
    tests_root: str | Path,
    previous_lock: dict[str, Any] | None = None,
) -> DriftReport:
    """Detect missing test cases, orphaned test tags, and spec lockfile drift."""
    expected_ids = set((current_lock.get("cases") or {}).keys())
    found = scan_test_tags(tests_root)
    found_ids = set(found.keys())

    missing = sorted(expected_ids - found_ids)
    orphaned = sorted(found_ids - expected_ids)
    lock_diff = diff_lockfiles(previous_lock, current_lock) if previous_lock is not None else None

    return DriftReport(
        spec_case_count=len(expected_ids),
        implemented_ids=found,
        missing_in_tests=missing,
        orphaned_in_tests=orphaned,
        lock_diff=lock_diff,
    )
