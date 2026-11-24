"""Hand-written adapter: turns a PairSpec case into a real `email_validator.validate_email` call.

Written after the spec and the emitted suite, as the only place that knows the library's API.
Run: uvx --with email-validator==2.4.0 --with pytest python -m pytest examples/email_validator -q
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from addresses import build_address, options  # noqa: E402


@pytest.fixture(scope="module")
def invoke_validate_email() -> Callable[[dict[str, Any]], dict[str, Any]]:
    email_validator = pytest.importorskip("email_validator")

    def _call(inputs: dict[str, Any]) -> dict[str, Any]:
        address = build_address(inputs)
        try:
            info = email_validator.validate_email(address, check_deliverability=False, **options(inputs))
        except email_validator.EmailNotValidError as exc:
            return {"valid": False, "smtputf8": "n/a", "address": address, "error": str(exc)}
        return {"valid": True, "smtputf8": info.smtputf8, "address": address}

    return _call
