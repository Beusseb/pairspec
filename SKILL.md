---
name: pairspec-blind-qa
description: "Blind combinatorial QA workflow for Claude Code, Cursor, GitHub Spec Kit, and OpenSpec. Derives deterministic Pairwise (IPOG) + Boundary Value Analysis (BVA) test matrices and Spec Drift checks strictly from specifications without reading implementation code (eliminates the AI Mirror Trap)."
---

# PairSpec — Blind Combinatorial QA Skill

Use this skill whenever a specification (`spec.md`, PRD, `spec-kit` feature spec, `OpenSpec` change spec, or `pairspec.toml`) is created or updated.

## Hard Rule — Prevent the Mirror Trap
**NEVER read the implementation source code before generating the PairSpec matrix and blind oracles.**
Read ONLY the functional specification, acceptance criteria, or contract.

## Workflow (Spec Kit / OpenSpec / Claude Code)

1. **Create or update the combinatorial spec** (`pairspec.toml` or fenced ```` ```pairspec-toml ```` block inside `spec.md`):
   - Declare all input parameters (`enum`, `bool`, `int`, `float`, `string`) with exact bounds (`min`, `max`, `step`, `extra_values`).
   - Declare impossible combinations under `[[exclusions]]`.
   - Declare expected business outcomes under `[default_expect]` and `[[oracles]]`.
   - If starting from prose Markdown and a local gateway (`http://127.0.0.1:11500/v1`) or BYOK model is configured, run:
     ```bash
     pairspec extract specs/feature.md --out specs/feature.pairspec.json
     ```

2. **Generate deterministic matrix & lockfile (0 LLM tokens)**:
   ```bash
   pairspec generate specs/feature.pairspec.toml --lock pairspec.lock.json
   ```
   Or invoke MCP tool `pairspec_generate`.

3. **Emit traceable tests** (`pytest`, `playwright`, `kotlin`, or `gherkin`):
   ```bash
   pairspec emit specs/feature.pairspec.toml --format pytest --out tests/test_feature_pairspec.py
   ```
   Keep every `@pairspec:PS-XXXXXXXX` tag intact when wiring the SUT adapter.
   The matrix includes a few oracle-unmasking cases beyond pairwise: do not prune them, they are the ones that expose a rule hidden behind a stronger one.

4. **Enforce Spec Drift gate in CI (< 2 seconds)**:
   ```bash
   pairspec drift specs/feature.pairspec.toml --tests tests/ --lock pairspec.lock.json
   ```
   - Exit code `0`: 100% traceability, zero drift.
   - Exit code `2`: missing cases, orphaned tags, or oracle changes → update only the impacted test IDs.
   - Exit code `1`: the check could not run (spec error, missing lockfile or tests path).

When calling the MCP tools, pass absolute paths.
