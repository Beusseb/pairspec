#!/usr/bin/env python3
"""Deterministic PreToolUse hook enforcing the PairSpec Anti-Mirror-Trap rule.

Reduces the chance that a coding agent (Claude Code / compatible PreToolUse runners) reads
implementation files under SUT source directories before blind test oracles have been
locked into `pairspec.lock.json`.

What it sees: `Read`/`Grep`/`Glob` paths (a `Grep` without a path searches the SUT too), and
existing SUT paths written literally in a `Bash` command (`cat src/x.py`,
`python -c "open('src/x.py')"`, `<src/x.py`).
What it does not see: paths built at run time (`cat $(printf sr)c/x.py`, variables, globs such
as `s*/x.py`), `grep -r` over `.`, code executed without being printed (running the tests), and
tools of other agents under other names. It is a guard rail against accidents, not a sandbox.

Protocol (Claude Code PreToolUse):
- Reads tool invocation JSON from stdin (`tool_name`, `tool_input`, optional `cwd`).
- Exits `0` to allow the tool call.
- Exits `2` with a clear explanation on stderr to block the tool call.

Environment overrides:
- PAIRSPEC_LOCK_PATH : path to lockfile (default: `pairspec.lock.json`, relative to cwd)
- PAIRSPEC_SUT_DIRS  : comma-separated implementation directories to guard (default: `src,app,lib`)
- PAIRSPEC_BYPASS    : set to `1` to temporarily disable the guard
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
from pathlib import Path
from typing import Any

GUARDED_TOOLS = {"Read", "Grep", "Glob", "Bash", "read_file", "view_file", "grep_search"}
SEARCH_TOOLS = {"Grep", "grep_search"}  # without a path, they search the whole cwd
DEFAULT_SUT_DIRS = ("src", "app", "lib")
DEFAULT_LOCK_PATH = "pairspec.lock.json"


def _extract_target_path(tool_input: dict[str, Any]) -> str:
    for key in ("file_path", "path", "AbsolutePath", "target_path", "directory"):
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _bash_paths(command: str, cwd: Path) -> list[str]:
    """Existing paths written literally in a shell command (split on quotes, `=`, `(`, redirections...)."""
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()
    parts = (part for token in tokens for part in re.split(r"[=,;()'\"`|<>&]", token))
    return [part for part in parts if part and (cwd / part).exists()]


def _is_under_sut_dirs(target: str, cwd: Path, sut_dirs: tuple[str, ...]) -> bool:
    if not target:
        return False
    target_path = Path(target)
    if not target_path.is_absolute():
        target_path = (cwd / target_path).resolve()
    else:
        target_path = target_path.resolve()

    try:
        rel_parts = target_path.relative_to(cwd.resolve()).parts
    except ValueError:
        rel_parts = target_path.parts

    return bool(rel_parts and rel_parts[0] in sut_dirs)


def evaluate_hook_event(
    event: dict[str, Any],
    *,
    cwd: Path | None = None,
    lock_path: str | None = None,
    sut_dirs: tuple[str, ...] | None = None,
    bypass: bool = False,
) -> tuple[int, str]:
    """Return `(exit_code, message)` for a PreToolUse JSON event."""
    if bypass:
        return 0, ""

    tool_name = str(event.get("tool_name") or "")
    if tool_name not in GUARDED_TOOLS:
        return 0, ""

    base_dir = Path(event.get("cwd") or cwd or Path.cwd())
    effective_lock = Path(lock_path or os.environ.get("PAIRSPEC_LOCK_PATH") or DEFAULT_LOCK_PATH)
    if not effective_lock.is_absolute():
        effective_lock = base_dir / effective_lock

    if effective_lock.is_file():
        return 0, ""

    raw_dirs = os.environ.get("PAIRSPEC_SUT_DIRS")
    effective_dirs = (
        sut_dirs
        if sut_dirs is not None
        else tuple(d.strip().strip("/") for d in raw_dirs.split(",") if d.strip())
        if raw_dirs
        else DEFAULT_SUT_DIRS
    )

    tool_input = event.get("tool_input")
    if not isinstance(tool_input, dict):
        return 0, ""

    if tool_name == "Bash":
        command = tool_input.get("command")
        targets = _bash_paths(command, base_dir) if isinstance(command, str) else []
    else:
        targets = [_extract_target_path(tool_input)]
        if tool_name in SEARCH_TOOLS and not targets[0]:
            targets = [d for d in effective_dirs if (base_dir / d).exists()]
    target = next((t for t in targets if _is_under_sut_dirs(t, base_dir, effective_dirs)), "")
    if not target:
        return 0, ""

    reason = (
        f"[PairSpec Anti-Mirror-Trap] Blocked `{tool_name}` on `{target}`: "
        f"`{effective_lock.name}` does not exist yet. "
        "Derive your blind combinatorial matrix and oracles strictly from the functional spec FIRST "
        "(`pairspec generate <spec.toml> --lock pairspec.lock.json`) before reading SUT implementation code."
    )
    return 2, reason


def main() -> int:
    raw = sys.stdin.read().strip()
    if not raw:
        return 0
    try:
        event = json.loads(raw)
    except json.JSONDecodeError:
        return 0
    if not isinstance(event, dict):
        return 0

    code, message = evaluate_hook_event(
        event,
        bypass=os.environ.get("PAIRSPEC_BYPASS") == "1",
    )
    if message:
        print(message, file=sys.stderr)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
