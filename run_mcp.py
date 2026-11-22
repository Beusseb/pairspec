#!/usr/bin/env python3
"""Start the PairSpec MCP server straight from a clone, without installing the package.

Used by the repo-level `.mcp.json` (Claude Code) and by `scripts/sync_agents.py sync-mcp`
(Cline, Antigravity), which need one script path they can make absolute.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from pairspec.mcp_server import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
