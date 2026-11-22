"""Deterministic Boundary Value Analysis (BVA) value expander — 0 LLM tokens, 0 hallucination."""

from __future__ import annotations

from typing import Any

from pairspec.schema import ParameterSpec


def _dedupe_preserving_order(values: list[Any]) -> list[Any]:
    seen: list[tuple[type, Any]] = []
    out: list[Any] = []
    for v in values:
        # Distinguish bool(True/False) from int(1/0) cleanly
        key = (bool if isinstance(v, bool) else type(v), v)
        if key not in seen:
            seen.append(key)
            out.append(v)
    return out


def expand_parameter_values(param: ParameterSpec) -> list[Any]:
    """Deterministically expand a ParameterSpec into concrete test values including BVA edges."""
    result: list[Any] = []

    if param.kind == "bool":
        result = list(param.values) if param.values else [True, False]

    elif param.kind == "enum":
        if not param.values:
            raise ValueError(f"Enum parameter '{param.name}' must specify non-empty 'values'")
        result = list(param.values)

    elif param.kind == "int":
        if param.values:
            result = [int(v) for v in param.values]
        else:
            if param.min_value is None or param.max_value is None:
                raise ValueError(f"Int parameter '{param.name}' needs 'min' and 'max' (or explicit 'values')")
            lo = int(param.min_value)
            hi = int(param.max_value)
            if lo > hi:
                raise ValueError(f"Int parameter '{param.name}' has min ({lo}) > max ({hi})")
            step = int(param.step) if param.step is not None else 1
            if step <= 0:
                raise ValueError(f"Int parameter '{param.name}' step must be > 0")
            mid = lo + ((hi - lo) // 2)
            # Order: valid boundaries, nominal middle, out-of-bounds if requested
            if param.include_invalid:
                result.append(lo - step)
            result.append(lo)
            if lo + step < hi:
                result.append(lo + step)
            if lo < mid < hi:
                result.append(mid)
            if hi - step > lo:
                result.append(hi - step)
            result.append(hi)
            if param.include_invalid:
                result.append(hi + step)

    elif param.kind == "float":
        if param.values:
            result = [float(v) for v in param.values]
        else:
            if param.min_value is None or param.max_value is None:
                raise ValueError(f"Float parameter '{param.name}' needs 'min' and 'max' (or explicit 'values')")
            lo_f = float(param.min_value)
            hi_f = float(param.max_value)
            if lo_f > hi_f:
                raise ValueError(f"Float parameter '{param.name}' has min ({lo_f}) > max ({hi_f})")
            step_f = float(param.step) if param.step is not None else 0.01
            if step_f <= 0:
                raise ValueError(f"Float parameter '{param.name}' step must be > 0")
            decimals = max(_decimals_for_step(step_f), _decimals_for_step(lo_f), _decimals_for_step(hi_f))
            mid_f = round(lo_f + (hi_f - lo_f) / 2.0, decimals)
            if param.include_invalid:
                result.append(round(lo_f - step_f, decimals))
            result.append(round(lo_f, decimals))
            if lo_f + step_f < hi_f:
                result.append(round(lo_f + step_f, decimals))
            if lo_f < mid_f < hi_f:
                result.append(mid_f)
            if hi_f - step_f > lo_f:
                result.append(round(hi_f - step_f, decimals))
            result.append(round(hi_f, decimals))
            if param.include_invalid:
                result.append(round(hi_f + step_f, decimals))

    elif param.kind == "string":
        if param.values:
            result = [str(v) for v in param.values]
        else:
            min_len = int(param.min_length if param.min_length is not None else 1)
            max_len = int(param.max_length if param.max_length is not None else max(min_len, 16))
            if min_len < 0 or min_len > max_len:
                raise ValueError(f"String parameter '{param.name}' has invalid lengths [{min_len}, {max_len}]")
            if param.include_invalid and min_len > 0:
                result.append("a" * (min_len - 1))
            result.append("a" * min_len)
            mid_len = min_len + (max_len - min_len) // 2
            if min_len < mid_len < max_len:
                result.append("a" * mid_len)
            result.append("a" * max_len)
            if param.include_invalid:
                result.append("a" * (max_len + 1))

    if param.extra_values:
        result.extend(param.extra_values)
    if param.nullable:
        result.append(None)

    return _dedupe_preserving_order(result)


def _decimals_for_step(step: float) -> int:
    """Number of decimal places needed to print `step` (also used for the bounds)."""
    text = f"{step:.10f}".rstrip("0")
    if "." not in text:
        return 0
    return len(text.split(".", 1)[1])
