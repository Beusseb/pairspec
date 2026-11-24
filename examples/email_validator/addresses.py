"""How a PairSpec case becomes an email address and validate_email options (shared by the suite and the exhaustive check)."""

from __future__ import annotations

from typing import Any

LOCAL_PARTS = {"plain": "me", "quoted": '"my name"', "unicode": "méo", "empty": "", "long": "a" * 65}
DOMAINS = {
    "public": "example.org",
    "idn": "exämple.org",
    "test_tld": "myname.test",
    "localhost": "localhost",
    "no_dot": "example",
    "literal_ipv4": "[192.0.2.1]",
    "literal_ipv6": "[IPv6:2001:db8::1]",
}
OPTIONS = ("allow_smtputf8", "allow_quoted_local", "allow_domain_literal", "allow_display_name",
           "allow_empty_local", "test_environment", "strict")


def build_address(inputs: dict[str, Any]) -> str:
    address = f"{LOCAL_PARTS[inputs['local']]}@{DOMAINS[inputs['domain']]}"
    return f"My Name <{address}>" if inputs["display_name"] else address


def options(inputs: dict[str, Any]) -> dict[str, Any]:
    return {name: inputs[name] for name in OPTIONS}
