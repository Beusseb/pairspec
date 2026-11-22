"""Tests for decoupled production-grade emitters (Pytest, Playwright, Kotlin JUnit 5, Gherkin, Markdown, JSON)."""

from __future__ import annotations

import ast
import json

import pytest

from pairspec.emitters import emit_suite
from pairspec.ipog import generate_matrix
from pairspec.schema import SpecDocument


def _sample_result(with_oracles: bool = True):
    spec_dict = {
        "name": "transfer_rule",
        "parameters": [
            {"name": "currency", "type": "enum", "values": ["EUR", "USD"]},
            {"name": "instant", "type": "bool"},
        ],
        "default_expect": {"status": "ok"} if with_oracles else {},
        "oracles": (
            [{"name": "usd_no_instant", "when": "currency == 'USD' and instant == True", "expect": {"status": "rejected"}}]
            if with_oracles
            else []
        ),
    }
    return generate_matrix(SpecDocument.from_dict(spec_dict))


def test_emit_pytest_decoupled_fixture_and_valid_python() -> None:
    res = _sample_result()
    code = emit_suite(res, "pytest")
    ast.parse(code)
    compile(code, "<emitted-pytest>", "exec")
    assert "invoke_transfer_rule: SutAdapter" in code
    assert "def invoke_transfer_rule(" not in code  # decoupled: fixture lives in conftest.py
    assert "_assert_oracle(" in code
    for c in res.cases:
        assert f"@pairspec:{c.case_id}" in code


def test_emit_playwright_decoupled_tags_steps_and_soft_assertions() -> None:
    res = _sample_result()
    pw = emit_suite(res, "playwright")
    assert "import { test, expect } from '@playwright/test';" in pw
    assert "import { executeScenario } from './transfer_rule.adapter';" in pw
    assert "{ tag: [" in pw
    assert "@oracle:usd_no_instant" in pw
    assert "async ({ request }) =>" in pw
    assert "test.step(" in pw
    assert "expect.soft(" in pw
    for c in res.cases:
        assert f"@pairspec:{c.case_id}" in pw


def test_emit_kotlin_abstract_base_tags_displayname_and_assert_all() -> None:
    res = _sample_result()
    kt = emit_suite(res, "kotlin")
    assert "abstract class TransferRulePairSpecTest" in kt
    assert "protected abstract fun invokeSut(" in kt
    assert "@Tag(\"oracle:usd_no_instant\")" in kt
    assert "@DisplayName(" in kt
    assert "assertAll(" in kt
    for c in res.cases:
        assert f"@pairspec:{c.case_id}" in kt
        assert f'@Tag("pairspec:{c.case_id}")' in kt


def test_emit_gherkin_markdown_json_and_edge_literals() -> None:
    res = _sample_result()
    gh = emit_suite(res, "gherkin")
    md = emit_suite(res, "markdown")
    js = json.loads(emit_suite(res, "json"))

    assert "Feature: transfer_rule" in gh
    assert "@oracle:usd_no_instant" in gh
    assert "| ID |" in md
    assert js["case_count"] == len(res.cases)

    for c in res.cases:
        tag = f"@pairspec:{c.case_id}"
        assert tag in gh
        assert tag in md

    # Empty expected dict exercises fallback Gherkin branch & emptyMap() in Kotlin
    res_no_exp = _sample_result(with_oracles=False)
    assert "Then the operation completes according to spec" in emit_suite(res_no_exp, "gherkin")
    assert "emptyMap()" in emit_suite(res_no_exp, "kotlin")

    # Numeric/null literals in Kotlin + unsupported format error
    spec_num = SpecDocument.from_dict(
        {
            "name": "num_spec",
            "parameters": [
                {"name": "count", "type": "int", "values": [1, 2]},
                {"name": "flag", "type": "bool"},
            ],
            "default_expect": {"code": 200, "extra": None},
        }
    )
    kt_num = emit_suite(generate_matrix(spec_num), "kotlin")
    assert '"code" to 200' in kt_num
    assert '"extra" to null' in kt_num

    with pytest.raises(ValueError, match="Unsupported emitter format"):
        emit_suite(res, "unknown_fmt")
