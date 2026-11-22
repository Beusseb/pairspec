"""Tests for deterministic Boundary Value Analysis (BVA) expansion."""

from __future__ import annotations

import pytest

from pairspec.bva import expand_parameter_values
from pairspec.schema import ParameterSpec


def test_int_bva_with_invalid_and_extras() -> None:
    p = ParameterSpec.from_dict(
        {"name": "amount", "type": "int", "min": 1, "max": 100, "step": 1, "include_invalid": True, "extra_values": [50]}
    )
    vals = expand_parameter_values(p)
    assert vals == [0, 1, 2, 50, 99, 100, 101]


def test_int_bva_without_invalid() -> None:
    p = ParameterSpec.from_dict(
        {"name": "qty", "type": "int", "min": 10, "max": 20, "step": 5, "include_invalid": False}
    )
    vals = expand_parameter_values(p)
    assert vals == [10, 15, 20]


def test_float_bva_rounding() -> None:
    p = ParameterSpec.from_dict(
        {"name": "rate", "type": "float", "min": 0.0, "max": 1.0, "step": 0.1, "include_invalid": True}
    )
    vals = expand_parameter_values(p)
    assert -0.1 in vals
    assert 0.0 in vals
    assert 0.1 in vals
    assert 0.5 in vals
    assert 0.9 in vals
    assert 1.0 in vals
    assert 1.1 in vals


def test_string_bva_lengths_and_nullable() -> None:
    p = ParameterSpec.from_dict(
        {"name": "code", "type": "string", "min_length": 2, "max_length": 6, "include_invalid": True, "nullable": True}
    )
    vals = expand_parameter_values(p)
    assert vals == ["a", "aa", "aaaa", "aaaaaa", "aaaaaaa", None]


def test_bool_and_int_deduplication_keeps_distinct_types() -> None:
    p = ParameterSpec.from_dict({"name": "flag", "type": "enum", "values": [True, 1, False, 0, True]})
    vals = expand_parameter_values(p)
    assert len(vals) == 4
    assert any(isinstance(v, bool) and v is True for v in vals)
    assert any(type(v) is int and v == 1 for v in vals)


def test_invalid_bounds_raise() -> None:
    p = ParameterSpec.from_dict({"name": "bad", "type": "int", "min": 10, "max": 5})
    with pytest.raises(ValueError, match="min .* > max"):
        expand_parameter_values(p)
