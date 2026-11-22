"""Hand-written decoupled SUT adapter for the auto-generated Payout API PairSpec suite.

Regenerating `test_payout_api_pairspec.py` with `pairspec emit --format pytest`
never overwrites this file.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

_DEMO_DIR = str(Path(__file__).resolve().parent)
if _DEMO_DIR not in sys.path:
    sys.path.insert(0, _DEMO_DIR)

from server import start_ephemeral_server  # noqa: E402


@pytest.fixture(scope="module")
def api_base_url() -> Iterator[str]:
    server, base_url = start_ephemeral_server()
    try:
        yield base_url
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture(scope="module")
def invoke_payout_api(api_base_url: str) -> Callable[[dict[str, Any]], dict[str, Any]]:
    endpoint = f"{api_base_url}/v1/payouts/evaluate"

    def _call(inputs: dict[str, Any]) -> dict[str, Any]:
        req = urllib.request.Request(
            endpoint,
            data=json.dumps(inputs).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                body = json.loads(resp.read().decode("utf-8"))
                return {"http_status": resp.status, **body}
        except urllib.error.HTTPError as err:
            body = json.loads(err.read().decode("utf-8"))
            return {"http_status": err.code, **body}

    return _call
