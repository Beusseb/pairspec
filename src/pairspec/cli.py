"""Command-line interface for PairSpec."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from pairspec import __version__
from pairspec.emitters import emit_suite
from pairspec.ipog import GenerationResult, generate_matrix
from pairspec.llm_client import LLMConfig, extract_spec_blind, list_models
from pairspec.lockfile import build_lockfile, check_test_drift, load_lockfile, save_lockfile
from pairspec.mcp_server import serve_stdio
from pairspec.reporters import render_drift_report
from pairspec.schema import load_spec

STARTER_SPEC_TOML = """[spec]
name = "fintech_payout"
version = "1.0.0"
description = "Multi-channel payout validation rules (BVA + Pairwise + Blind Oracles)"
strength = 2

[default_expect]
status = "approved"
requires_2fa = false
fee_bps = 0

[[parameters]]
name = "channel"
type = "enum"
values = ["web", "mobile", "api"]

[[parameters]]
name = "rail"
type = "enum"
values = ["SEPA", "INSTANT", "SWIFT"]

[[parameters]]
name = "currency"
type = "enum"
values = ["EUR", "USD", "JPY"]

[[parameters]]
name = "kyc_tier"
type = "enum"
values = ["unverified", "basic", "verified"]

[[parameters]]
name = "amount"
type = "int"
min = 1
max = 10000
step = 1
include_invalid = true
extra_values = [500, 5000]

[[exclusions]]
expr = "rail == 'SEPA' and currency == 'JPY'"
reason = "JPY cannot be routed over SEPA"

[[exclusions]]
expr = "rail == 'INSTANT' and currency != 'EUR'"
reason = "SEPA Instant supports EUR only"

[[oracles]]
name = "invalid_amount_bounds"
priority = 10
when = "amount <= 0 or amount > 10000"
expect = { status = "rejected", reason = "invalid_amount", requires_2fa = false, fee_bps = 0 }

[[oracles]]
name = "sepa_eur_only"
priority = 15
when = "rail == 'SEPA' and currency != 'EUR'"
expect = { status = "rejected", reason = "unsupported_currency", requires_2fa = false, fee_bps = 0 }

[[oracles]]
name = "kyc_unverified_cap"
priority = 20
when = "kyc_tier == 'unverified' and amount > 500"
expect = { status = "rejected", reason = "kyc_limit_exceeded", requires_2fa = false, fee_bps = 0 }

[[oracles]]
name = "mobile_high_value_2fa"
priority = 40
when = "channel == 'mobile' and amount >= 5000"
expect = { requires_2fa = true }

[[oracles]]
name = "swift_fee"
priority = 50
when = "rail == 'SWIFT'"
expect = { fee_bps = 150 }
"""


def cmd_init(args: argparse.Namespace) -> int:
    target = Path(args.path)
    if target.exists() and not args.force:
        print(f"Error: {target} already exists (use --force to overwrite)", file=sys.stderr)
        return 1
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(STARTER_SPEC_TOML, encoding="utf-8")
    print(f"[OK] Starter PairSpec written to {target}")
    print(f"Next: pairspec generate {target} --lock pairspec.lock.json")
    return 0


def _warn_if_incomplete(result: GenerationResult) -> None:
    """A matrix below 100% feasible coverage or breaking an exclusion must never pass silently."""
    cov = result.coverage
    if cov.uncovered_tuples or cov.exclusion_violations:
        print(
            f"[WARN] {result.spec_name}: coverage={cov.coverage_pct}% "
            f"({cov.uncovered_tuples} feasible tuples uncovered), violations={cov.exclusion_violations}",
            file=sys.stderr,
        )


def cmd_generate(args: argparse.Namespace) -> int:
    t0 = time.perf_counter()
    spec = load_spec(args.spec)
    result = generate_matrix(spec, strength=args.strength)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    _warn_if_incomplete(result)

    if args.lock:
        saved = save_lockfile(result, args.lock)
        print(f"[LOCK] Saved {saved}", file=sys.stderr)

    rendered = emit_suite(result, fmt=args.format)
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(rendered, encoding="utf-8")
        print(
            f"[OK] {result.spec_name}: {len(result.cases)} cases "
            f"(vs {result.cartesian_size} cartesian, -{result.reduction_pct}%), "
            f"coverage={result.coverage.coverage_pct}%, violations={result.coverage.exclusion_violations} "
            f"in {elapsed_ms:.1f}ms -> {out_path}",
            file=sys.stderr,
        )
    else:
        print(rendered, end="")
    return 0


def cmd_emit(args: argparse.Namespace) -> int:
    spec = load_spec(args.spec)
    result = generate_matrix(spec, strength=args.strength)
    _warn_if_incomplete(result)
    rendered = emit_suite(result, fmt=args.format)
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(rendered, encoding="utf-8")
        print(f"[OK] Emitted {len(result.cases)} {args.format} cases -> {out_path}", file=sys.stderr)
    else:
        print(rendered, end="")
    return 0


def cmd_drift(args: argparse.Namespace) -> int:
    t0 = time.perf_counter()
    spec = load_spec(args.spec)
    result = generate_matrix(spec, strength=args.strength)
    current_lock = build_lockfile(result)

    previous_lock = None
    if args.lock:
        if not Path(args.lock).is_file():
            # A mistyped lock path must not turn the CI gate green.
            raise FileNotFoundError(f"Lockfile not found: {args.lock} (create it with `pairspec generate --lock`)")
        previous_lock = load_lockfile(args.lock)
    if not Path(args.tests).exists():
        raise FileNotFoundError(f"Tests path not found: {args.tests}")

    report = check_test_drift(current_lock, args.tests, previous_lock=previous_lock)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    expected_ids = [c.case_id for c in result.cases]

    fmt = "json" if args.as_json else args.format
    rendered = render_drift_report(
        report,
        spec_name=result.spec_name,
        spec_fingerprint=result.spec_fingerprint,
        spec_path=str(args.spec),
        expected_case_ids=expected_ids,
        fmt=fmt,
        elapsed_ms=elapsed_ms,
        tool_version=__version__,
    )

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(rendered, encoding="utf-8")
        print(f"[OK] Wrote {fmt} drift report -> {out_path}", file=sys.stderr)
    else:
        print(rendered, end="")

    if args.summary_file:
        summary_md = render_drift_report(
            report,
            spec_name=result.spec_name,
            spec_fingerprint=result.spec_fingerprint,
            spec_path=str(args.spec),
            expected_case_ids=expected_ids,
            fmt="markdown",
            elapsed_ms=elapsed_ms,
            tool_version=__version__,
        )
        summary_path = Path(args.summary_file)
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        with summary_path.open("a", encoding="utf-8") as fh:
            fh.write(summary_md)

    return 0 if report.is_clean else 2


def cmd_extract(args: argparse.Namespace) -> int:
    cfg = LLMConfig.from_env(base_url=args.base_url, model=args.model, api_key=args.api_key)
    if args.check_gateway:
        models = list_models(cfg)
        print(f"[OK] Gateway reachable at {cfg.base_url} ({len(models)} models): {', '.join(models[:8])}")
        return 0

    if not args.spec_doc:
        print("Error: provide a functional spec file (or --check-gateway)", file=sys.stderr)
        return 1

    prose = Path(args.spec_doc).read_text(encoding="utf-8")
    spec_doc, raw_dict = extract_spec_blind(prose, config=cfg)
    rendered = json.dumps(raw_dict, indent=2, ensure_ascii=False) + "\n"
    if args.out:
        Path(args.out).write_text(rendered, encoding="utf-8")
        print(
            f"[OK] Extracted blind spec '{spec_doc.name}' ({len(spec_doc.parameters)} params, "
            f"{len(spec_doc.oracles)} oracles) -> {args.out}",
            file=sys.stderr,
        )
    else:
        print(rendered, end="")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pairspec",
        description="PairSpec — Blind combinatorial QA engine (IPOG Pairwise + BVA + Spec Drift + MCP)",
    )
    parser.add_argument("--version", action="version", version=f"pairspec {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="Create a starter pairspec.toml specification")
    p_init.add_argument("path", nargs="?", default="pairspec.toml", help="Output path (default: pairspec.toml)")
    p_init.add_argument("--force", action="store_true", help="Overwrite if file exists")
    p_init.set_defaults(func=cmd_init)

    p_gen = sub.add_parser("generate", help="Generate deterministic Pairwise+BVA matrix from a spec")
    p_gen.add_argument("spec", help="Path to .toml, .json, .md, or .yaml spec")
    p_gen.add_argument("--strength", type=int, choices=[1, 2, 3], help="Override combinatorial strength t")
    p_gen.add_argument("--lock", help="Write portable pairspec.lock.json to this path")
    p_gen.add_argument("--format", choices=["markdown", "json"], default="markdown", help="Output format")
    p_gen.add_argument("--out", help="Write output to file instead of stdout")
    p_gen.set_defaults(func=cmd_generate)

    p_emit = sub.add_parser("emit", help="Emit traceable test suite with @pairspec:ID tags")
    p_emit.add_argument("spec", help="Path to spec file")
    p_emit.add_argument(
        "--format",
        required=True,
        choices=["pytest", "playwright", "kotlin", "gherkin", "markdown", "json"],
    )
    p_emit.add_argument("--strength", type=int, choices=[1, 2, 3])
    p_emit.add_argument("--out", help="Write suite to file instead of stdout")
    p_emit.set_defaults(func=cmd_emit)

    p_drift = sub.add_parser("drift", help="Check CI Spec Drift (<2s) against tagged test files")
    p_drift.add_argument("spec", help="Path to spec file")
    p_drift.add_argument("--tests", required=True, help="Directory or file containing tagged tests")
    p_drift.add_argument("--lock", help="Committed pairspec.lock.json to diff against (must exist when given)")
    p_drift.add_argument("--strength", type=int, choices=[1, 2, 3])
    p_drift.add_argument(
        "--format",
        choices=["text", "json", "sarif", "junit", "markdown"],
        default="text",
        help="Drift report format (default: text)",
    )
    p_drift.add_argument("--json", dest="as_json", action="store_true", help="Shorthand for --format json")
    p_drift.add_argument("--out", help="Write formatted drift report to file instead of stdout")
    p_drift.add_argument("--summary-file", help="Append Markdown summary table (e.g. $GITHUB_STEP_SUMMARY)")
    p_drift.set_defaults(func=cmd_drift)

    p_ext = sub.add_parser("extract", help="Blind LLM spec extraction from prose (llm-gateway / Ollama / BYOK)")
    p_ext.add_argument("spec_doc", nargs="?", help="Path to Markdown/text functional spec (NEVER source code)")
    p_ext.add_argument("--out", help="Output JSON spec file")
    p_ext.add_argument("--base-url", help="OpenAI-compatible base URL (default: http://127.0.0.1:11500/v1)")
    p_ext.add_argument("--model", help="Model ID (default: qa-mtp-64k:latest)")
    p_ext.add_argument("--api-key", help="API key (optional for local llm-gateway/Ollama)")
    p_ext.add_argument("--check-gateway", action="store_true", help="Verify connectivity and list models")
    p_ext.set_defaults(func=cmd_extract)

    p_mcp = sub.add_parser("mcp", help="Start PairSpec stdio MCP server (Claude Code / Cursor / Cline)")
    p_mcp.set_defaults(func=lambda _a: serve_stdio())

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except Exception as exc:
        print(f"[PairSpec Error] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
