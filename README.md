# PairSpec

[![CI](https://github.com/Beusseb/pairspec/actions/workflows/ci.yml/badge.svg)](https://github.com/Beusseb/pairspec/actions/workflows/ci.yml)
![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)
![Zero Dependencies](https://img.shields.io/badge/dependencies-0-brightgreen)
![Typed: PEP 561](https://img.shields.io/badge/typing-py.typed-blue)
![License: MIT](https://img.shields.io/badge/license-MIT-green)

**Blind combinatorial QA engine (Pairwise IPOG + BVA + Spec Drift + MCP + SARIF/JUnit CI Gate) — zero dependencies, zero LLM tokens in core.**

> Coding agents that read your implementation to write tests fall into the **Mirror Trap**: they mirror your bugs into your test suite and pass 100% green.
> **PairSpec** derives deterministic combinatorial matrices, boundary values, and test oracles **strictly from specifications** — emits production-grade suites with decoupled SUT adapters — and enforces `<2s` Spec Drift in CI.

---

## Why PairSpec?

1. **Anti Mirror Trap (Blind Oracles + PreToolUse Hook)** — Expected outcomes are evaluated purely from declarative spec rules, never from implementation code. Ships with a deterministic `PreToolUse` hook ([`hooks/anti_mirror_trap_hook.py`](hooks/anti_mirror_trap_hook.py)) that reduces the chance of a coding agent reading `src/` before `pairspec.lock.json` is locked (guard rail, not a sandbox: see its limits below).
2. **Deterministic IPOG + BVA Engine (0 tokens, 0 hallucination)** — Full In-Parameter-Order-General (`t=1..3`, default pairwise `t=2`) + Boundary Value Analysis + CSP exclusion solver in pure Python 3.11+ stdlib (`py.typed` PEP 561).
3. **Mask-Aware Oracle Coverage** — Pairwise guarantees a value pair appears, not that its effect is visible: a rejection rule matching in the same case hides a bug in a weaker rule. PairSpec appends the minimal unmasking cases so every oracle decision is observable.
4. **Decoupled Production-Grade Emitters** — Non-destructive regeneration across **Pytest** (injectable `conftest.py` fixture + multi-field diagnostics), **Playwright TypeScript** (decoupled `./<slug>.adapter`, native `{ tag: [...] }`, `{ request }`, `test.step`, `expect.soft`), **Kotlin JUnit 5** (`abstract class`, `@Tag`, `@DisplayName`, `assertAll`), and **Gherkin** (`@pairspec:` + `@oracle:` tags).
5. **Universal Stdlib CI Reports & Reusable GitHub Action** — `pairspec drift` natively outputs **SARIF 2.1.0** (GitHub Code Scanning PR annotations), **JUnit XML** (Allure 3 / GitLab CI / Jenkins), **Markdown** (`$GITHUB_STEP_SUMMARY`), JSON, and text — packaged as a drop-in Composite Action ([`action.yml`](action.yml)).
6. **Native MCP Server & Sovereign Local LLM Extraction** — Zero-dependency stdio MCP server (`pairspec mcp`) + drop-in [`SKILL.md`](SKILL.md) for **Claude Code**, **Cursor**, **Cline**, **GitHub Spec Kit**, and **OpenSpec**.

---

## 60-Second Quickstart

```bash
pip install -e .

# 1. Create a starter combinatorial spec
pairspec init pairspec.toml

# 2. Generate deterministic Pairwise + BVA matrix & portable lockfile (0 tokens)
pairspec generate pairspec.toml --lock pairspec.lock.json

# 3. Emit decoupled traceable tests (pytest | playwright | kotlin | gherkin | markdown | json)
pairspec emit pairspec.toml --format pytest --out tests/test_payout_pairspec.py

# 4. Run <2s CI Spec Drift gate (text | json | sarif | junit | markdown)
pairspec drift pairspec.toml --tests tests/ --lock pairspec.lock.json \
  --format sarif --out drift.sarif --summary-file "$GITHUB_STEP_SUMMARY"
```

---

## Reusable GitHub Action (`action.yml`)

Drop PairSpec straight into any pull request pipeline:

```yaml
- name: PairSpec Drift Gate
  uses: Beusseb/pairspec@v0.1.0
  with:
    spec: examples/http_api_demo/spec.toml
    tests: examples/http_api_demo
    lock: examples/http_api_demo/pairspec.lock.json
    format: sarif          # text | json | sarif | junit | markdown
    output-file: drift.sarif
    step-summary: "true"   # publishes Markdown traceability table to $GITHUB_STEP_SUMMARY
    fail-on-drift: "true"
```

---

## Proofs Included & Executed in CI

### 1. The Mirror Trap Benchmark (`examples/fintech_payout/`)

```bash
python3 examples/fintech_payout/mirror_trap_demo.py   # runs from a plain clone, no install
```

- **Code-Aware AI Tests (Mirror Trap)**: `5/5 PASSED (GREEN)` → **`0/5` real bugs caught**.
- **PairSpec Blind Oracles**: **`53` cases vs `972` cartesian (`-94.55%`)**, **`100.0%`** verified 2-wise coverage, `0` exclusion violations → **`5/5` real bugs caught**.

### 2. Live Stdlib HTTP JSON API Demo (`examples/http_api_demo/`)

[`test_payout_api_pairspec.py`](examples/http_api_demo/test_payout_api_pairspec.py) is **emitted untouched** by `pairspec emit --format pytest` (`36` cases vs `648` cartesian, `-94.44%`), wired through a decoupled [`conftest.py`](examples/http_api_demo/conftest.py) fixture against a live stdlib HTTP JSON server ([`server.py`](examples/http_api_demo/server.py)), and verified byte-for-byte in CI by [`scripts/check_static.py`](scripts/check_static.py):

```bash
pytest examples/http_api_demo/ -v
python3 scripts/check_static.py
```

---

## Anti-Mirror-Trap Agent Hook (`PreToolUse`)

Prevent coding agents from peeking at `src/` before blind spec oracles are locked. Copy [`hooks/claude_settings.example.json`](hooks/claude_settings.example.json) into `.claude/settings.json`:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Read|Grep|Glob|Bash",
        "hooks": [
          {
            "type": "command",
            "command": "python3 hooks/anti_mirror_trap_hook.py"
          }
        ]
      }
    ]
  }
}
```

- Before `pairspec.lock.json` exists: reading functional specs is allowed; reading SUT implementation paths (`PAIRSPEC_SUT_DIRS=src,app,lib`) exits `2` with actionable guidance.
- Once `pairspec generate <spec> --lock pairspec.lock.json` runs: full codebase reads unlock automatically.
- `Bash` commands are checked for existing SUT paths written literally (`cat src/x.py`, `python -c "open('src/x.py')"`); a `Grep` without a path counts as a search of the SUT.
- Not seen: paths built at run time (`$(...)`, variables, globs), `grep -r` over `.`, code executed without being printed, and tools of other agents under other names. Tested in [`tests/test_reporters_and_hook.py`](tests/test_reporters_and_hook.py), including one known bypass.

---

## MCP Setup (Claude Code / Cursor / Cline)

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

Exposed tools:
- `pairspec_generate` — Deterministic IPOG + BVA matrix + optional lockfile.
- `pairspec_emit` — Emit `pytest`, `playwright`, `kotlin`, `gherkin`, `markdown`, or `json`.
- `pairspec_check_drift` — Scan `@pairspec:PS-XXXXXXXX` tags and compare against `pairspec.lock.json`.
- `pairspec_diff_locks` — Semantic diff between two lockfiles (`added`, `removed`, `oracle_changed`).
- `pairspec_extract_spec` — Blind spec extraction from prose via local `llm-gateway` / Ollama / BYOK.

---

## License

MIT License (Core CLI, IPOG/BVA engine, Emitters, SARIF/JUnit/Markdown Reporters, Composite Action, PreToolUse Hook, and MCP server).
