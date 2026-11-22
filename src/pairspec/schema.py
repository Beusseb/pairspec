"""Specification schema, safe AST expression evaluator, and spec loaders (TOML/JSON/Markdown/YAML)."""

from __future__ import annotations

import ast
import hashlib
import json
import re
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Safe AST evaluation (zero eval/exec)
_BIN_OPS: dict[type[ast.operator], Callable[[Any, Any], Any]] = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
    ast.FloorDiv: lambda a, b: a // b,
    ast.Mod: lambda a, b: a % b,
}

_UNARY_OPS: dict[type[ast.unaryop], Callable[[Any], Any]] = {
    ast.Not: lambda a: not a,
    ast.UAdd: lambda a: +a,
    ast.USub: lambda a: -a,
}

_CMP_OPS: dict[type[ast.cmpop], Callable[[Any, Any], Any]] = {
    ast.Eq: lambda a, b: a == b,
    ast.NotEq: lambda a, b: a != b,
    ast.Lt: lambda a, b: a < b,
    ast.LtE: lambda a, b: a <= b,
    ast.Gt: lambda a, b: a > b,
    ast.GtE: lambda a, b: a >= b,
    ast.In: lambda a, b: a in b,
    ast.NotIn: lambda a, b: a not in b,
}

_SAFE_FUNCS: dict[str, Callable[..., Any]] = {
    "len": len,
    "abs": abs,
    "min": min,
    "max": max,
    "round": round,
}


class ExpressionError(ValueError):
    """Raised when a spec constraint or oracle expression is invalid or unsafe."""


class UnresolvedVariableError(KeyError):
    """Raised when an expression references a parameter missing from a partial assignment."""


def _eval_node(node: ast.AST, env: dict[str, Any]) -> Any:
    if isinstance(node, ast.Expression):
        return _eval_node(node.body, env)
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id in env:
            return env[node.id]
        if node.id in ("true", "True"):
            return True
        if node.id in ("false", "False"):
            return False
        if node.id in ("null", "none", "None"):
            return None
        raise UnresolvedVariableError(node.id)
    if isinstance(node, ast.List):
        return [_eval_node(elt, env) for elt in node.elts]
    if isinstance(node, ast.Tuple):
        return tuple(_eval_node(elt, env) for elt in node.elts)
    if isinstance(node, ast.Set):
        return {_eval_node(elt, env) for elt in node.elts}
    if isinstance(node, ast.UnaryOp):
        unary_op = _UNARY_OPS.get(type(node.op))
        if unary_op is None:
            raise ExpressionError(f"Unsupported unary operator: {type(node.op).__name__}")
        return unary_op(_eval_node(node.operand, env))
    if isinstance(node, ast.BinOp):
        bin_op = _BIN_OPS.get(type(node.op))
        if bin_op is None:
            raise ExpressionError(f"Unsupported binary operator: {type(node.op).__name__}")
        left_val = _eval_node(node.left, env)
        right_val = _eval_node(node.right, env)
        both_text = isinstance(left_val, str) and isinstance(right_val, str) and isinstance(node.op, ast.Add)
        if not both_text and not (_is_number(left_val) and _is_number(right_val)):
            # Numbers only (plus str + str): rules out `'a' * 10**9`-style memory blow-ups.
            raise TypeError(f"Arithmetic needs numeric operands, got {type(left_val).__name__} and {type(right_val).__name__}")
        return bin_op(left_val, right_val)
    if isinstance(node, ast.BoolOp):
        if isinstance(node.op, ast.And):
            result: Any = True
            for value_node in node.values:
                result = _eval_node(value_node, env)
                if not result:
                    return False
            return bool(result)
        if isinstance(node.op, ast.Or):
            for value_node in node.values:
                val = _eval_node(value_node, env)
                if val:
                    return True
            return False
        raise ExpressionError(f"Unsupported boolean operator: {type(node.op).__name__}")
    if isinstance(node, ast.Compare):
        left = _eval_node(node.left, env)
        for op, comparator in zip(node.ops, node.comparators, strict=True):
            cmp_op = _CMP_OPS.get(type(op))
            if cmp_op is None:
                raise ExpressionError(f"Unsupported comparison operator: {type(op).__name__}")
            right = _eval_node(comparator, env)
            if not cmp_op(left, right):
                return False
            left = right
        return True
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _SAFE_FUNCS:
            raise ExpressionError("Only whitelisted functions (len, abs, min, max, round) are allowed")
        if node.keywords:
            raise ExpressionError("Keyword arguments are not supported in spec expressions")
        args = [_eval_node(arg, env) for arg in node.args]
        return _SAFE_FUNCS[node.func.id](*args)
    raise ExpressionError(f"Disallowed syntax in expression: {type(node).__name__}")


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _compile_expr(expr: str) -> tuple[ast.Expression, frozenset[str]]:
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise ExpressionError(f"Invalid expression syntax '{expr}': {exc}") from exc
    variables = frozenset(
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name)
        and node.id not in _SAFE_FUNCS
        and node.id not in ("true", "True", "false", "False", "null", "none", "None")
    )
    return tree, variables


def evaluate_expr(expr: str, env: dict[str, Any]) -> Any:
    """Safely evaluate a declarative spec expression against parameter values."""
    tree, _ = _compile_expr(expr)
    return _eval_node(tree, env)


@dataclass(frozen=True)
class ParameterSpec:
    name: str
    kind: str  # enum | bool | int | float | string
    values: list[Any] = field(default_factory=list)
    min_value: int | float | None = None
    max_value: int | float | None = None
    step: int | float | None = None
    include_invalid: bool = True
    extra_values: list[Any] = field(default_factory=list)
    min_length: int | None = None
    max_length: int | None = None
    nullable: bool = False
    description: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ParameterSpec:
        name = str(data.get("name") or "").strip()
        if not name or not name.isidentifier():
            raise ValueError(f"Parameter name must be a valid identifier, got: {name!r}")
        kind = str(data.get("type") or data.get("kind") or "enum").strip().lower()
        if kind not in {"enum", "bool", "int", "float", "string"}:
            raise ValueError(f"Unsupported parameter type {kind!r} for '{name}'")
        return cls(
            name=name,
            kind=kind,
            values=list(data.get("values") or []),
            min_value=data.get("min", data.get("min_value")),
            max_value=data.get("max", data.get("max_value")),
            step=data.get("step"),
            include_invalid=bool(data.get("include_invalid", True)),
            extra_values=list(data.get("extra_values") or []),
            min_length=data.get("min_length"),
            max_length=data.get("max_length"),
            nullable=bool(data.get("nullable", False)),
            description=str(data.get("description") or ""),
        )


@dataclass(frozen=True)
class ExclusionRule:
    expr: str
    reason: str = ""
    _tree: ast.Expression = field(init=False, repr=False, compare=False)
    variables: frozenset[str] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        tree, variables = _compile_expr(self.expr)
        object.__setattr__(self, "_tree", tree)
        object.__setattr__(self, "variables", variables)

    def is_violated(self, assignment: dict[str, Any]) -> bool:
        """Return True iff every variable needed is assigned AND the exclusion matches."""
        if not self.variables.issubset(assignment.keys()):
            return False
        try:
            return bool(_eval_node(self._tree, assignment))
        except (TypeError, ZeroDivisionError):
            return False


@dataclass(frozen=True)
class OracleRule:
    name: str
    when: str
    expect: dict[str, Any]
    priority: int = 100
    description: str = ""
    _tree: ast.Expression = field(init=False, repr=False, compare=False)
    variables: frozenset[str] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        tree, variables = _compile_expr(self.when)
        object.__setattr__(self, "_tree", tree)
        object.__setattr__(self, "variables", variables)

    def matches(self, assignment: dict[str, Any]) -> bool:
        if not self.variables.issubset(assignment.keys()):
            return False
        try:
            return bool(_eval_node(self._tree, assignment))
        except (TypeError, ZeroDivisionError):
            return False


@dataclass(frozen=True)
class SpecDocument:
    name: str
    version: str = "1.0.0"
    description: str = ""
    strength: int = 2
    source_path: str | None = None
    parameters: list[ParameterSpec] = field(default_factory=list)
    exclusions: list[ExclusionRule] = field(default_factory=list)
    default_expect: dict[str, Any] = field(default_factory=dict)
    oracles: list[OracleRule] = field(default_factory=list)

    def canonical_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "strength": self.strength,
            "parameters": [
                {
                    "name": p.name,
                    "type": p.kind,
                    "values": p.values,
                    "min": p.min_value,
                    "max": p.max_value,
                    "step": p.step,
                    "include_invalid": p.include_invalid,
                    "extra_values": p.extra_values,
                    "min_length": p.min_length,
                    "max_length": p.max_length,
                    "nullable": p.nullable,
                }
                for p in self.parameters
            ],
            "exclusions": [{"expr": e.expr, "reason": e.reason} for e in self.exclusions],
            "default_expect": self.default_expect,
            "oracles": [
                {
                    "name": o.name,
                    "when": o.when,
                    "expect": o.expect,
                    "priority": o.priority,
                }
                for o in self.oracles
            ],
        }

    def fingerprint(self) -> str:
        raw = json.dumps(self.canonical_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def evaluate_oracle(self, inputs: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
        """Compute expected outcome strictly from spec rules (highest priority wins per key)."""
        expected: dict[str, Any] = dict(self.default_expect)
        matched_rules: list[str] = []
        # Apply from lowest priority (larger number) to highest priority (smaller number)
        # so higher-priority rules cleanly override overlapping keys.
        ordered = sorted(self.oracles, key=lambda r: (-r.priority, r.name))
        for rule in ordered:
            if rule.matches(inputs):
                expected.update(rule.expect)
                matched_rules.append(rule.name)
        matched_rules.reverse()
        return expected, matched_rules

    @classmethod
    def from_dict(cls, data: dict[str, Any], source_path: str | None = None) -> SpecDocument:
        raw_meta = data.get("spec")
        meta: dict[str, Any] = raw_meta if isinstance(raw_meta, dict) else data
        name = str(meta.get("name") or data.get("name") or "unnamed_spec").strip()
        version = str(meta.get("version") or data.get("version") or "1.0.0")
        description = str(meta.get("description") or data.get("description") or "")
        strength = int(meta.get("strength") or data.get("strength") or 2)
        if strength < 1 or strength > 3:
            raise ValueError(f"Combinatorial strength must be 1, 2, or 3, got {strength}")

        raw_params = data.get("parameters") or []
        if not raw_params:
            raise ValueError("Specification must define at least one parameter under 'parameters'")
        parameters = [ParameterSpec.from_dict(p) for p in raw_params]
        param_names = {p.name for p in parameters}
        if len(param_names) != len(parameters):
            raise ValueError("Duplicate parameter names found in specification")

        exclusions: list[ExclusionRule] = []
        for raw_ex in data.get("exclusions") or []:
            if isinstance(raw_ex, str):
                ex = ExclusionRule(expr=raw_ex)
            else:
                if not raw_ex.get("expr"):
                    raise ValueError("Every exclusion needs an 'expr' expression")
                ex = ExclusionRule(expr=str(raw_ex["expr"]), reason=str(raw_ex.get("reason") or ""))
            unknown = ex.variables - param_names
            if unknown:
                raise ValueError(f"Exclusion '{ex.expr}' references unknown parameters: {sorted(unknown)}")
            exclusions.append(ex)

        oracles: list[OracleRule] = []
        for idx, raw_o in enumerate(data.get("oracles") or []):
            if not raw_o.get("when"):
                raise ValueError(f"Oracle #{idx + 1} needs a 'when' expression")
            rule = OracleRule(
                name=str(raw_o.get("name") or f"rule_{idx + 1}"),
                when=str(raw_o["when"]),
                expect=dict(raw_o.get("expect") or {}),
                priority=int(raw_o.get("priority", 100)),
                description=str(raw_o.get("description") or ""),
            )
            unknown = rule.variables - param_names
            if unknown:
                raise ValueError(f"Oracle '{rule.name}' references unknown parameters: {sorted(unknown)}")
            oracles.append(rule)

        default_expect = dict(data.get("default_expect") or meta.get("default_expect") or {})
        return cls(
            name=name,
            version=version,
            description=description,
            strength=strength,
            source_path=source_path,
            parameters=parameters,
            exclusions=exclusions,
            default_expect=default_expect,
            oracles=oracles,
        )


_MD_BLOCK_RE = re.compile(
    r"```(?P<lang>pairspec-toml|pairspec-json|pairspec-yaml|pairspec)\s*\n(?P<body>.*?)```",
    re.DOTALL | re.IGNORECASE,
)


def parse_spec_text(text: str, fmt: str, source_path: str | None = None) -> SpecDocument:
    """Parse specification text from toml, json, yaml, or fenced Markdown."""
    normalized = fmt.lower().lstrip(".")
    if normalized in ("md", "markdown"):
        match = _MD_BLOCK_RE.search(text)
        if not match:
            raise ValueError(
                "No ```pairspec-toml``` or ```pairspec-json``` block found in Markdown. "
                "Use `pairspec extract` to derive a structured spec from prose Markdown."
            )
        lang = match.group("lang").lower()
        body = match.group("body")
        if lang in ("pairspec-json",):
            return parse_spec_text(body, "json", source_path=source_path)
        if lang in ("pairspec-yaml",):
            return parse_spec_text(body, "yaml", source_path=source_path)
        return parse_spec_text(body, "toml", source_path=source_path)

    if normalized == "toml":
        data = tomllib.loads(text)
    elif normalized == "json":
        data = json.loads(text)
    elif normalized in ("yaml", "yml"):
        try:
            import yaml
        except ImportError as exc:
            raise RuntimeError(
                "YAML support requires PyYAML (`pip install pairspec[yaml]`). "
                "TOML and JSON work out-of-the-box with zero dependencies."
            ) from exc
        data = yaml.safe_load(text)
    else:
        raise ValueError(f"Unsupported spec format: {fmt!r} (supported: toml, json, md, yaml)")

    if not isinstance(data, dict):
        raise ValueError("Specification root must be a mapping/object")
    return SpecDocument.from_dict(data, source_path=source_path)


def load_spec(path: str | Path) -> SpecDocument:
    """Load a SpecDocument from disk (.toml, .json, .md, .yaml/.yml)."""
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    return parse_spec_text(text, p.suffix or "toml", source_path=str(p))
