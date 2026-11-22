"""PairSpec — Blind combinatorial QA engine (IPOG Pairwise + BVA + Spec Drift + MCP)."""

from pairspec.bva import expand_parameter_values
from pairspec.emitters import emit_suite
from pairspec.ipog import GeneratedCase, GenerationResult, generate_matrix, verify_coverage
from pairspec.lockfile import (
    DriftReport,
    LockDiff,
    build_lockfile,
    check_test_drift,
    diff_lockfiles,
    load_lockfile,
    save_lockfile,
)
from pairspec.reporters import render_drift_report
from pairspec.schema import (
    ExclusionRule,
    OracleRule,
    ParameterSpec,
    SpecDocument,
    evaluate_expr,
    load_spec,
)

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "DriftReport",
    "ExclusionRule",
    "GeneratedCase",
    "GenerationResult",
    "LockDiff",
    "OracleRule",
    "ParameterSpec",
    "SpecDocument",
    "build_lockfile",
    "check_test_drift",
    "diff_lockfiles",
    "emit_suite",
    "evaluate_expr",
    "expand_parameter_values",
    "generate_matrix",
    "load_lockfile",
    "load_spec",
    "render_drift_report",
    "save_lockfile",
    "verify_coverage",
]
