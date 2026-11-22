#!/usr/bin/env python3
"""
PairSpec Mirror Trap Demo (Fintech / Multi-Channel Payout Engine).

Proves why reading implementation code to generate tests creates the "Mirror Trap":
- The buggy payout engine below contains 5 realistic production bugs.
- A typical code-aware test suite (derived by reading the implementation) approves all 5 bugs (0/5 caught).
- PairSpec derives combinatorial Pairwise+BVA oracles strictly from `spec.toml` and catches 5/5 bugs in milliseconds.

Run (works from a plain clone, no install needed):
  python3 pairspec/examples/fintech_payout/mirror_trap_demo.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

try:
    from pairspec import generate_matrix, load_spec
except ModuleNotFoundError:  # not installed: fall back to the sources next to this example
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
    from pairspec import generate_matrix, load_spec

SPEC_PATH = Path(__file__).resolve().parent / "spec.toml"


def buggy_payout_engine(inputs: dict[str, Any]) -> dict[str, Any]:
    """Simulated production payout service containing 5 subtle real-world bugs."""
    channel = inputs["channel"]
    rail = inputs["rail"]
    currency = inputs["currency"]  # noqa: F841 (BUG 2: intentionally unused on SEPA rail)
    kyc_tier = inputs["kyc_tier"]
    amount = inputs["amount"]

    # BUG 1 (Off-by-one lower bound): forgot `amount == 0` (checks `< 0` instead of `<= 0`)
    if amount < 0 or amount > 10000:
        return {"status": "rejected", "reason": "invalid_amount", "requires_2fa": False, "fee_bps": 0}

    # BUG 2 (Missing rail/currency guard): forgot to block USD/GBP on SEPA
    # (Spec requires rail == 'SEPA' and currency != 'EUR' -> rejected/unsupported_currency)

    # BUG 3 (Off-by-one KYC threshold): checks `>= 500` instead of `> 500`, rejecting valid 500 EUR payouts
    if kyc_tier == "unverified" and amount >= 500:
        return {"status": "rejected", "reason": "kyc_limit_exceeded", "requires_2fa": False, "fee_bps": 0}

    # BUG 4 (Boundary 2FA bypass): checks `> 5000` instead of `>= 5000`, skipping 2FA at exactly 5000 on mobile
    requires_2fa = channel == "mobile" and amount > 5000

    # BUG 5 (Fee misconfiguration): charges 100 bps instead of 150 bps on SWIFT transfers
    fee_bps = 100 if rail == "SWIFT" else 0

    return {
        "status": "approved",
        "requires_2fa": requires_2fa,
        "fee_bps": fee_bps,
    }


def run_code_aware_mirror_trap_tests() -> tuple[int, int]:
    """
    Tests typically written by an AI agent that READ `buggy_payout_engine()` first:
    it faithfully mirrors the implementation's buggy branches (`amount == 0` approved,
    `unverified` at 500 rejected, `mobile` at 5000 without 2FA, SWIFT fee == 100).
    """
    mirror_cases = [
        ({"channel": "web", "rail": "SEPA", "currency": "EUR", "kyc_tier": "verified", "amount": 0}, {"status": "approved"}),
        ({"channel": "api", "rail": "SEPA", "currency": "USD", "kyc_tier": "verified", "amount": 100}, {"status": "approved"}),
        ({"channel": "web", "rail": "INSTANT", "currency": "EUR", "kyc_tier": "unverified", "amount": 500}, {"status": "rejected"}),
        ({"channel": "mobile", "rail": "INSTANT", "currency": "EUR", "kyc_tier": "verified", "amount": 5000}, {"requires_2fa": False}),
        ({"channel": "web", "rail": "SWIFT", "currency": "USD", "kyc_tier": "verified", "amount": 200}, {"fee_bps": 100}),
    ]
    passed = 0
    for inputs, mirrored_expected in mirror_cases:
        actual = buggy_payout_engine(inputs)
        if all(actual.get(k) == v for k, v in mirrored_expected.items()):
            passed += 1
    return passed, len(mirror_cases)


def classify_bug(inputs: dict[str, Any], expected: dict[str, Any], actual: dict[str, Any]) -> str:
    if inputs["amount"] == 0 and (
        actual.get("status") != expected.get("status") or actual.get("reason") != expected.get("reason")
    ):
        return "BUG-1: amount==0 accepted (off-by-one lower bound)"
    if inputs["rail"] == "SEPA" and inputs["currency"] != "EUR" and (
        actual.get("status") != expected.get("status") or actual.get("reason") != expected.get("reason")
    ):
        return "BUG-2: non-EUR accepted on SEPA rail"
    if inputs["kyc_tier"] == "unverified" and inputs["amount"] == 500 and actual.get("status") != expected.get("status"):
        return "BUG-3: unverified KYC cap off-by-one at amount==500"
    if inputs["channel"] == "mobile" and inputs["amount"] == 5000 and actual.get("requires_2fa") != expected.get("requires_2fa"):
        return "BUG-4: mobile 2FA bypassed at boundary amount==5000"
    if inputs["rail"] == "SWIFT" and actual.get("fee_bps") != expected.get("fee_bps"):
        return "BUG-5: wrong SWIFT fee (100 bps vs spec 150 bps)"
    return "OTHER"


def run_demo() -> dict[str, Any]:
    t0 = time.perf_counter()
    spec = load_spec(SPEC_PATH)
    result = generate_matrix(spec)
    gen_ms = (time.perf_counter() - t0) * 1000.0

    mirror_passed, mirror_total = run_code_aware_mirror_trap_tests()

    failed_cases: list[dict[str, Any]] = []
    caught_bugs: set[str] = set()
    for case in result.cases:
        actual = buggy_payout_engine(case.inputs)
        mismatches = {
            k: {"expected": v, "actual": actual.get(k)}
            for k, v in case.expected.items()
            if actual.get(k) != v
        }
        if mismatches:
            bug_label = classify_bug(case.inputs, case.expected, actual)
            caught_bugs.add(bug_label)
            failed_cases.append({"id": case.case_id, "bug": bug_label, "inputs": case.inputs, "mismatches": mismatches})

    return {
        "cartesian_size": result.cartesian_size,
        "pairspec_cases": len(result.cases),
        "reduction_pct": result.reduction_pct,
        "coverage_pct": result.coverage.coverage_pct,
        "gen_ms": round(gen_ms, 2),
        "mirror_trap_passed": mirror_passed,
        "mirror_trap_total": mirror_total,
        "mirror_trap_bugs_caught": mirror_total - mirror_passed,
        "oracle_unmask_cases": result.oracle_unmask_cases,
        "pairspec_failed_cases": len(failed_cases),
        "pairspec_unique_bugs_caught": sorted(caught_bugs),
    }


def main() -> int:
    summary = run_demo()
    print("=" * 72)
    print("PairSpec — Mirror Trap Benchmark (Fintech / Multi-Channel Payouts)")
    print("=" * 72)
    print(
        f"Combinatorial reduction : {summary['pairspec_cases']} cases vs "
        f"{summary['cartesian_size']} cartesian (-{summary['reduction_pct']}%) | "
        f"2-wise coverage: {summary['coverage_pct']}% | time: {summary['gen_ms']} ms"
    )
    print("-" * 72)
    print(
        f"[1] Code-Aware AI Tests (Mirror Trap) : {summary['mirror_trap_passed']}/{summary['mirror_trap_total']} PASSED (GREEN) "
        f"-> {summary['mirror_trap_bugs_caught']}/5 real bugs caught"
    )
    print(
        f"[2] PairSpec Blind Spec Oracles       : {summary['pairspec_failed_cases']} failing cases "
        f"-> {len(summary['pairspec_unique_bugs_caught'])}/5 real bugs caught:"
    )
    for bug in summary["pairspec_unique_bugs_caught"]:
        print(f"    ✓ {bug}")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
