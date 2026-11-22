"""Tests for OpenAI-compatible llm-gateway client using an ephemeral local mock HTTP server."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from pairspec.llm_client import LLMConfig, extract_spec_blind, list_models

MOCK_SPEC_JSON = {
    "name": "kyc_withdrawal",
    "version": "1.0.0",
    "strength": 2,
    "parameters": [
        {"name": "tier", "type": "enum", "values": ["unverified", "verified"]},
        {"name": "amount", "type": "int", "min": 1, "max": 500, "step": 1, "include_invalid": True},
    ],
    "exclusions": [],
    "default_expect": {"status": "approved"},
    "oracles": [
        {
            "name": "reject_bad_amount",
            "priority": 10,
            "when": "amount <= 0 or amount > 500",
            "expect": {"status": "rejected"},
        }
    ],
}


class _MockGatewayHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        return

    def do_GET(self) -> None:
        if self.path == "/v1/models":
            body = json.dumps({"object": "list", "data": [{"id": "qa-mtp-64k:latest"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self) -> None:
        if self.path == "/v1/chat/completions":
            length = int(self.headers.get("Content-Length", "0"))
            _ = self.rfile.read(length)
            # Simulate a reasoning model emitting <think>...</think> + fenced JSON
            content = (
                "<think>Analyzing spec strictly without reading source code.</think>\n"
                "```json\n"
                + json.dumps(MOCK_SPEC_JSON)
                + "\n```"
            )
            body = json.dumps({"choices": [{"message": {"role": "assistant", "content": content}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()


def test_llm_gateway_list_models_and_blind_extraction() -> None:
    server = HTTPServer(("127.0.0.1", 0), _MockGatewayHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        cfg = LLMConfig(base_url=f"http://127.0.0.1:{port}/v1", model="qa-mtp-64k:latest")
        models = list_models(cfg)
        assert models == ["qa-mtp-64k:latest"]

        spec_doc, raw = extract_spec_blind("Withdrawals must be between 1 and 500 EUR.", config=cfg)
        assert spec_doc.name == "kyc_withdrawal"
        assert len(spec_doc.parameters) == 2
        assert raw["oracles"][0]["name"] == "reject_bad_amount"
    finally:
        server.shutdown()
        server.server_close()
