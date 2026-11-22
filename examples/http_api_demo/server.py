"""Minimal zero-dependency stdlib HTTP JSON server implementing the Payout Evaluation API."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


def evaluate_payout(payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """Reference HTTP API handler matching examples/http_api_demo/spec.toml."""
    channel = payload["channel"]
    rail = payload["rail"]
    currency = payload["currency"]
    kyc_tier = payload["kyc_tier"]
    amount = payload["amount"]

    if amount <= 0 or amount > 10000:
        return 422, {"status": "rejected", "reason": "invalid_amount", "requires_2fa": False, "fee_bps": 0}
    if rail == "SEPA" and currency != "EUR":
        return 422, {"status": "rejected", "reason": "unsupported_currency", "requires_2fa": False, "fee_bps": 0}
    if kyc_tier == "unverified" and amount > 500:
        return 403, {"status": "rejected", "reason": "kyc_limit_exceeded", "requires_2fa": False, "fee_bps": 0}

    return 200, {
        "status": "approved",
        "requires_2fa": channel == "mobile" and amount >= 5000,
        "fee_bps": 150 if rail == "SWIFT" else 0,
    }


class PayoutAPIHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/v1/payouts/evaluate":
            self._send_json(404, {"error": "not_found"})
            return
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length).decode("utf-8"))
        status_code, response = evaluate_payout(body)
        self._send_json(status_code, response)

    def _send_json(self, status_code: int, payload: dict[str, Any]) -> None:
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        # Silence per-request stdout noise during pytest runs.
        return


def start_ephemeral_server() -> tuple[ThreadingHTTPServer, str]:
    """Start the HTTP server on 127.0.0.1 with an OS-assigned free port."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), PayoutAPIHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{port}"
