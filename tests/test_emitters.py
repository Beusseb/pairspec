"""Tests for multi-framework emitters (Pytest, Playwright, Kotlin, Gherkin, Markdown, JSON)."""

from __future__ import annotations

import ast
import json

from pairspec.emitters import emit_suite
from pairspec.ipog import generate_matrix
from pairspec.schema import SpecDocument


def _sample_result():
    spec = SpecDocument.from_dict(
        {
            "name": "transfer_rule",
            "parameters": [
                {"name": "currency", "type": "enum", "values": ["EUR", "USD"]},
                {"name": "instant", "type": "bool"},
            ],
            "default_expect": {"status": "ok"},
            "oracles": [
                {"name": "usd_no_instant", "when": "currency == 'USD' and instant == True", "expect": {"status": "rejected"}}
            ],
        }
    )
    return generate_matrix(spec)


def test_emit_pytest_is_valid_python_and_tagged() -> None:
    res = _sample_result()
    code = emit_suite(res, "pytest")
    ast.parse(code)
    for c in res.cases:
        assert f"@pairspec:{c.case_id}" in code


def test_emit_playwright_kotlin_gherkin_markdown_json() -> None:
    res = _sample_result()
    pw = emit_suite(res, "playwright")
    kt = emit_suite(res, "kotlin")
    gh = emit_suite(res, "gherkin")
    md = emit_suite(res, "markdown")
    js = json.loads(emit_suite(res, "json"))

    assert "import { test, expect } from '@playwright/test';" in pw
    assert "class TransferRulePairSpecTest" in kt
    assert "Feature: transfer_rule" in gh
    assert "| ID |" in md
    assert js["case_count"] == len(res.cases)

    for c in res.cases:
        tag = f"@pairspec:{c.case_id}"
        assert tag in pw
        assert tag in kt
        assert tag in gh
        assert tag in md
