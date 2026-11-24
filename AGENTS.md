# AGENTS.md — rules for agents working on this repository

`SKILL.md` is for people *using* PairSpec. This file is for anyone (human or agent) *changing* it.

## Workflow
- One branch and one pull request per objective. Never push to `main` directly.
- PR description: **What this brings / Why / How it was verified**, with the exact commands run
  and their real output. The maintainer merges.
- Everything written in the repository (code, docs, commits, PRs) is in English.

## Evidence
- No number without the command that reproduces it. If it cannot be measured, remove the claim.
- An assertion only counts once it has been seen failing: for every new test, show it red on a
  mutant (one mutant at a time), then green, and name the mutants in the PR.
- Claim less rather than more. Every README claim points to its proof (test, script or CI job).

## Code
- Minimum code, no dead code, no new dependency in the engine (`src/pairspec/` is stdlib only).
  Dev-only tools go in the `dev` extra.
- Before opening a PR, all four must pass:

```bash
uvx ruff check .
uvx mypy src/pairspec
uv run --no-project python scripts/check_static.py
uvx --with pytest --with pytest-cov python -m pytest tests/ examples/http_api_demo/ -q
```
