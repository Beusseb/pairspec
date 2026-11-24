#!/usr/bin/env python3
"""Run every combination of the spec (8,960) against the real library, not only the 38 pairwise cases.

It answers one question: did pairwise miss a disagreement between the docs and the library?
Run: uvx --with email-validator==2.4.0 python examples/email_validator/exhaustive_check.py
"""

from __future__ import annotations

import itertools
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "src"))
sys.path.insert(0, str(HERE))

import email_validator  # noqa: E402
from addresses import build_address, options  # noqa: E402

from pairspec import generate_matrix, load_spec  # noqa: E402


def main() -> int:
    spec = load_spec(HERE / "spec.toml")
    domains = generate_matrix(spec).parameter_domains
    mismatches: Counter[str] = Counter()
    total = 0
    for combo in itertools.product(*domains.values()):
        inputs = dict(zip(domains, combo, strict=True))
        expected, rules = spec.evaluate_oracle(inputs)
        try:
            info = email_validator.validate_email(
                build_address(inputs), check_deliverability=False, **options(inputs)
            )
            actual = {"valid": True, "smtputf8": info.smtputf8}
        except email_validator.EmailNotValidError:
            actual = {"valid": False, "smtputf8": "n/a"}
        total += 1
        if actual != expected:
            mismatches[f"{inputs['local']}@{inputs['domain']} rules={rules or ['default']}"] += 1
    print(f"email-validator {email_validator.__version__}: {total} combinations, {sum(mismatches.values())} mismatches")
    for key, count in mismatches.most_common():
        print(f"  {count:5d}  {key}")
    return 1 if mismatches else 0


if __name__ == "__main__":
    raise SystemExit(main())
