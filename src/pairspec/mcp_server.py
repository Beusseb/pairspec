"""Zero-dependency MCP (Model Context Protocol) stdio JSON-RPC 2.0 server for PairSpec."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, TextIO

from pairspec import __version__
from pairspec.emitters import emit_suite
from pairspec.ipog import generate_matrix
from pairspec.llm_client import LLMConfig, extract_spec_blind
from pairspec.lockfile import build_lockfile, check_test_drift, diff_lockfiles, load_lockfile, save_lockfile
from pairspec.schema import load_spec, parse_spec_text

# Revisions this server can speak (it only uses the stable `tools` surface), newest first.
SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
PROTOCOL_VERSION = SUPPORTED_PROTOCOL_VERSIONS[0]

TOOLS: list[dict[str, Any]] = [
    {
        "name": "pairspec_generate",
        "annotations": {"title": "Generate test matrix", "idempotentHint": True, "openWorldHint": False},
        "description": (
            "Generate a deterministic blind combinatorial test matrix (IPOG Pairwise/3-wise + BVA + exclusions) "
            "from a PairSpec file (.toml, .json, .md) or inline spec text. Zero LLM tokens, stable @pairspec:IDs."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "spec_path": {"type": "string", "description": "Path to .toml/.json/.md spec file"},
                "spec_text": {"type": "string", "description": "Inline spec content (alternative to spec_path)"},
                "spec_format": {"type": "string", "enum": ["toml", "json", "md"], "default": "toml"},
                "strength": {"type": "integer", "minimum": 1, "maximum": 3, "description": "Override combinatorial strength (default: spec strength / 2)"},
                "lock_path": {"type": "string", "description": "Optional path to write pairspec.lock.json"},
            },
        },
    },
    {
        "name": "pairspec_emit",
        "annotations": {"title": "Emit tagged tests", "idempotentHint": True, "openWorldHint": False},
        "description": (
            "Emit traceable test code or matrix report (pytest, playwright, kotlin, gherkin, markdown, json) "
            "with @pairspec:PS-XXXXXXXX tags and blind spec oracles. With output_path the suite is written "
            "to disk and not echoed back (saves context tokens)."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["spec_path", "format"],
            "properties": {
                "spec_path": {"type": "string"},
                "format": {"type": "string", "enum": ["pytest", "playwright", "kotlin", "gherkin", "markdown", "json"]},
                "strength": {"type": "integer", "minimum": 1, "maximum": 3},
                "output_path": {"type": "string", "description": "Optional path to write emitted test file"},
            },
        },
    },
    {
        "name": "pairspec_check_drift",
        "annotations": {"title": "Check spec drift", "readOnlyHint": True, "openWorldHint": False},
        "description": (
            "Detect Spec Drift in <2s: compares the current spec against existing test files tagged with "
            "@pairspec:PS-XXXXXXXX and an optional previous pairspec.lock.json."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["spec_path", "tests_dir"],
            "properties": {
                "spec_path": {"type": "string"},
                "tests_dir": {"type": "string"},
                "lock_path": {"type": "string", "description": "Optional path to existing pairspec.lock.json for oracle diff"},
                "strength": {"type": "integer", "minimum": 1, "maximum": 3},
            },
        },
    },
    {
        "name": "pairspec_diff_locks",
        "annotations": {"title": "Diff lockfiles", "readOnlyHint": True, "openWorldHint": False},
        "description": "Compute semantic diff (added, removed, oracle_changed) between two pairspec.lock.json files.",
        "inputSchema": {
            "type": "object",
            "required": ["old_lock_path", "new_lock_path"],
            "properties": {
                "old_lock_path": {"type": "string"},
                "new_lock_path": {"type": "string"},
            },
        },
    },
    {
        "name": "pairspec_extract_spec",
        "annotations": {"title": "Extract spec from prose (LLM)", "readOnlyHint": True, "openWorldHint": True},
        "description": (
            "Strictly blind extraction of a structured PairSpec JSON from prose specification text using an "
            "OpenAI-compatible endpoint (defaults to local llm-gateway at http://127.0.0.1:11500/v1)."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["spec_prose"],
            "properties": {
                "spec_prose": {"type": "string", "description": "Functional spec prose (NEVER pass implementation source code)"},
                "base_url": {"type": "string", "description": "OpenAI-compatible base URL (default: http://127.0.0.1:11500/v1)"},
                "model": {"type": "string", "description": "Model ID (default: qa-mtp-64k:latest)"},
            },
        },
    },
]


def _load_spec_from_args(args: dict[str, Any]) -> Any:
    if args.get("spec_path"):
        return load_spec(args["spec_path"])
    if args.get("spec_text"):
        return parse_spec_text(str(args["spec_text"]), fmt=str(args.get("spec_format") or "toml"))
    raise ValueError("Either 'spec_path' or 'spec_text' must be provided")


def execute_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    if name == "pairspec_generate":
        spec = _load_spec_from_args(args)
        result = generate_matrix(spec, strength=args.get("strength"))
        payload = result.to_dict()
        if args.get("lock_path"):
            written = save_lockfile(result, args["lock_path"])
            payload["lockfile_written"] = str(written)
        return payload

    if name == "pairspec_emit":
        spec = load_spec(args["spec_path"])
        result = generate_matrix(spec, strength=args.get("strength"))
        rendered = emit_suite(result, fmt=str(args["format"]))
        out: dict[str, Any] = {
            "spec_name": result.spec_name,
            "format": args["format"],
            "case_count": len(result.cases),
        }
        if args.get("output_path"):
            target = Path(args["output_path"])
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(rendered, encoding="utf-8")
            out["written_to"] = str(target)
        else:
            out["content"] = rendered
        return out

    if name == "pairspec_check_drift":
        spec = load_spec(args["spec_path"])
        result = generate_matrix(spec, strength=args.get("strength"))
        current_lock = build_lockfile(result)
        previous_lock = None
        if args.get("lock_path"):
            if not Path(args["lock_path"]).is_file():
                raise FileNotFoundError(f"Lockfile not found: {args['lock_path']}")
            previous_lock = load_lockfile(args["lock_path"])
        if not Path(args["tests_dir"]).exists():
            raise FileNotFoundError(f"Tests path not found: {args['tests_dir']}")
        report = check_test_drift(current_lock, args["tests_dir"], previous_lock=previous_lock)
        payload = report.to_dict()
        payload.pop("implemented_locations", None)  # one entry per case: not worth the agent's context
        return payload

    if name == "pairspec_diff_locks":
        old_lock = load_lockfile(args["old_lock_path"])
        new_lock = load_lockfile(args["new_lock_path"])
        return diff_lockfiles(old_lock, new_lock).to_dict()

    if name == "pairspec_extract_spec":
        cfg = LLMConfig.from_env(base_url=args.get("base_url"), model=args.get("model"))
        spec_doc, raw_dict = extract_spec_blind(str(args["spec_prose"]), config=cfg)
        return {
            "spec_name": spec_doc.name,
            "spec_fingerprint": spec_doc.fingerprint(),
            "spec": raw_dict,
        }

    raise KeyError(f"Unknown MCP tool: {name}")


_TOOL_NAMES = frozenset(tool["name"] for tool in TOOLS)


def handle_rpc_message(message: Any) -> dict[str, Any] | None:
    """Process one JSON-RPC 2.0 request/notification and return a response dict (or None for notifications)."""
    if not isinstance(message, dict):
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid Request"}}
    method = message.get("method")
    msg_id = message.get("id")
    params = message.get("params")
    if not isinstance(params, dict):
        params = {}

    if "id" not in message:
        return None  # notifications never get a response

    if method == "initialize":
        requested = params.get("protocolVersion")
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                # Echo the client's revision when we speak it, else offer our newest one.
                "protocolVersion": requested if requested in SUPPORTED_PROTOCOL_VERSIONS else PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "pairspec", "version": __version__},
            },
        }

    if method == "ping":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": TOOLS}}

    if method == "tools/call":
        tool_name = params.get("name")
        tool_args = params.get("arguments") or {}
        if tool_name not in _TOOL_NAMES:
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32602, "message": f"Unknown tool: {tool_name}"},
            }
        try:
            data = execute_tool(str(tool_name), dict(tool_args))
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    # Compact JSON: the reader is a model, indentation only costs tokens.
                    "content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False, separators=(",", ":"))}],
                    "isError": False,
                },
            }
        except Exception as exc:
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [{"type": "text", "text": f"Error: {type(exc).__name__}: {exc}"}],
                    "isError": True,
                },
            }

    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


def serve_stdio(stdin: TextIO | None = None, stdout: TextIO | None = None) -> int:
    """Run MCP stdio loop (newline-delimited JSON-RPC 2.0)."""
    inp = stdin or sys.stdin
    out = stdout or sys.stdout
    for raw_line in inp:
        line = raw_line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            err = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": f"Parse error: {exc}"},
            }
            out.write(json.dumps(err, ensure_ascii=False) + "\n")
            out.flush()
            continue

        try:
            response = handle_rpc_message(message)
        except Exception as exc:  # keep serving: one bad request must not kill the session
            msg_id = message.get("id") if isinstance(message, dict) else None
            response = {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32603, "message": f"Internal error: {exc}"}}
        if response is not None:
            out.write(json.dumps(response, ensure_ascii=False) + "\n")
            out.flush()
    return 0


def main() -> int:
    return serve_stdio()


if __name__ == "__main__":
    raise SystemExit(main())
