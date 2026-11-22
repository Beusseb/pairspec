# PairSpec

[![CI](https://github.com/Beusseb/pairspec/actions/workflows/ci.yml/badge.svg)](https://github.com/Beusseb/pairspec/actions/workflows/ci.yml)
![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)
![Zero Dependencies](https://img.shields.io/badge/dependencies-0-brightgreen)
![License: MIT](https://img.shields.io/badge/license-MIT-green)

**Blind combinatorial QA engine (Pairwise IPOG + BVA + Spec Drift + MCP) — zero dependencies, zero LLM tokens in core.**

> Coding agents that read your implementation to write tests fall into the **Mirror Trap**: they mirror your bugs into your test suite and pass 100% green.
> **PairSpec** derives deterministic combinatorial matrices, boundary values, and test oracles **strictly from specifications** — and tracks Spec Drift in CI in milliseconds.

---

## Why PairSpec?

1. **Anti Mirror Trap (Blind Oracles)** — Expected outcomes are evaluated purely from declarative spec rules, never from implementation code.
2. **Deterministic IPOG + BVA Engine (0 tokens, 0 hallucination)** — Full In-Parameter-Order-General (`t=1..3`, default pairwise `t=2`) + Boundary Value Analysis + CSP exclusion solver in pure Python 3.11+ stdlib.
3. **Stable Case IDs & `<2s` CI Spec Drift** — Every test case gets a content-addressed ID (`@pairspec:PS-XXXXXXXX`) stored in a portable `pairspec.lock.json`. When a spec evolves, `pairspec drift` immediately pinpoints added, removed, or oracle-changed cases across `pytest`, `playwright`, `kotlin` (JUnit 5), and `gherkin`.
4. **Mask-Aware Oracle Coverage** — Pairwise guarantees a value pair appears, not that its effect is visible: a rejection rule matching in the same case hides a bug in a weaker rule. PairSpec appends the few cases needed so every oracle decision is observed at least once without a stronger rule overriding it.
5. **Native MCP Server & Spec-Driven Dev Plugin** — Zero-dependency stdio MCP server (`pairspec mcp`) + drop-in [`SKILL.md`](SKILL.md) for **Claude Code**, **Cursor**, **GitHub Spec Kit**, and **OpenSpec**.
6. **Local-First Sovereign LLM Extraction** — Optional `pairspec extract` speaks OpenAI-compatible `/v1/chat/completions`, defaulting to local gateways (`http://127.0.0.1:11500/v1`, Ollama, or BYOK).

---

## 60-Second Quickstart

```bash
pip install -e .

# 1. Create a starter combinatorial spec
pairspec init pairspec.toml

# 2. Generate deterministic Pairwise + BVA matrix & portable lockfile (0 tokens)
pairspec generate pairspec.toml --lock pairspec.lock.json

# 3. Emit traceable tests (pytest | playwright | kotlin | gherkin | markdown | json)
pairspec emit pairspec.toml --format pytest --out tests/test_payout_pairspec.py

# 4. Run <2s CI Spec Drift check (exit 0 clean, 2 drift, 1 error such as a missing lockfile)
pairspec drift pairspec.toml --tests tests/ --lock pairspec.lock.json
```

---

## Proof: The Mirror Trap Demo

Run the included fintech multi-channel payout benchmark (5 realistic production bugs injected):

```bash
python3 examples/fintech_payout/mirror_trap_demo.py   # runs from a plain clone, no install
```

- **Code-Aware AI Tests (Mirror Trap)**: `5/5 PASSED (GREEN)` → **`0/5` real bugs caught**.
- **PairSpec Blind Oracles**: **`53` cases vs `972` cartesian (`-94.55%`)**, **`100.0%`** verified 2-wise coverage, `0` exclusion violations, `~11 ms` → **`5/5` real bugs caught**.
- `42` of the cases are the pairwise matrix, `11` are oracle-unmasking cases. Without them the pair *mobile × 5000* can land in a case that is rejected for another reason, and the 2FA boundary bug goes unseen.

---

## MCP Setup (Claude Code / Cursor / Cline)

Add PairSpec as a stdio MCP server:

```json
{
  "mcpServers": {
    "pairspec": {
      "command": "pairspec-mcp",
      "args": []
    }
  }
}
```

From a clone without installing: `"command": "python3", "args": ["/abs/path/pairspec/run_mcp.py"]` (Python 3.11+).
Pass absolute paths to the tools: the server resolves relative paths against its own working directory.

Exposed tools:
- `pairspec_generate` — Deterministic IPOG + BVA matrix + optional lockfile.
- `pairspec_emit` — Emit `pytest`, `playwright`, `kotlin`, `gherkin`, `markdown`, or `json` (with `output_path` the file is written and not echoed back).
- `pairspec_check_drift` — Scan `@pairspec:PS-XXXXXXXX` tags and compare against `pairspec.lock.json`.
- `pairspec_diff_locks` — Semantic diff between two lockfiles (`added`, `removed`, `oracle_changed`).
- `pairspec_extract_spec` — Blind spec extraction from prose via local `llm-gateway` / Ollama / BYOK.

---

## Local Sovereign LLM Extraction

```bash
# Check local llm-gateway / Ollama connectivity
pairspec extract --check-gateway --base-url http://127.0.0.1:11500/v1

# Extract structured PairSpec JSON strictly from a Markdown functional spec
pairspec extract docs/payout_spec.md --out payout.pairspec.json
```

Environment variables: `PAIRSPEC_LLM_BASE_URL`, `PAIRSPEC_LLM_MODEL`, `PAIRSPEC_LLM_API_KEY` (or `OPENAI_API_KEY`), `PAIRSPEC_LLM_TIMEOUT` (seconds, default 120).

---

## License

MIT License (Core CLI, IPOG/BVA engine, Drift detector, and MCP server).
