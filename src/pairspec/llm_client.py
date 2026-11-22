"""OpenAI-compatible client (zero external deps) for strict blind spec extraction via llm-gateway / Ollama / BYOK."""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from pairspec.schema import SpecDocument, parse_spec_text

DEFAULT_BASE_URL = "http://127.0.0.1:11500/v1"
DEFAULT_MODEL = "qa-mtp-64k:latest"

BLIND_EXTRACTION_SYSTEM_PROMPT = """You are a strict Blind Specification Analyst for PairSpec.
Your ONLY input is a functional specification document. You must NEVER assume or invent implementation details.

Convert the specification into a valid PairSpec JSON object with this EXACT schema:
{
  "name": "short_snake_case_name",
  "version": "1.0.0",
  "description": "One-sentence summary of the rule under test",
  "strength": 2,
  "parameters": [
    {
      "name": "valid_python_identifier",
      "type": "enum | bool | int | float | string",
      "values": ["for enum/bool or explicit list"],
      "min": 0,
      "max": 100,
      "step": 1,
      "include_invalid": true,
      "extra_values": []
    }
  ],
  "exclusions": [
    {"expr": "currency == 'JPY' and rail == 'SEPA'", "reason": "Impossible combination per spec"}
  ],
  "default_expect": {"status": "approved"},
  "oracles": [
    {
      "name": "rule_identifier",
      "priority": 10,
      "when": "amount <= 0",
      "expect": {"status": "rejected", "reason": "invalid_amount"}
    }
  ]
}

Rules:
- Use only Python-like safe comparison/boolean expressions (`==`, `!=`, `<`, `<=`, `>`, `>=`, `in`, `not in`, `and`, `or`, `not`).
- Lower `priority` number overrides higher `priority` number (e.g. priority 10 overrides priority 50).
- Every variable in `exclusions` and `oracles` MUST match a parameter `name`.
- Output ONLY raw JSON (no prose before or after).
"""

_FENCE_RE = re.compile(r"```(?:json)?\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)


@dataclass(frozen=True)
class LLMConfig:
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    api_key: str = "local-gateway"
    timeout_s: int = 120
    temperature: float = 0.0

    @classmethod
    def from_env(
        cls,
        base_url: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        timeout_s: int | None = None,
    ) -> LLMConfig:
        if timeout_s is None:
            timeout_s = int(os.environ.get("PAIRSPEC_LLM_TIMEOUT") or 120)
        return cls(
            base_url=(base_url or os.environ.get("PAIRSPEC_LLM_BASE_URL") or DEFAULT_BASE_URL).rstrip("/"),
            model=model or os.environ.get("PAIRSPEC_LLM_MODEL") or DEFAULT_MODEL,
            api_key=api_key
            or os.environ.get("PAIRSPEC_LLM_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
            or "local-gateway",
            timeout_s=timeout_s,
        )


def list_models(config: LLMConfig) -> list[str]:
    """Query GET /models on llm-gateway / Ollama / OpenAI-compatible server."""
    url = f"{config.base_url}/models"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {config.api_key}",
            "Accept": "application/json",
        },
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=min(config.timeout_s, 15)) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return [str(m["id"]) for m in data.get("data", []) if "id" in m]


def chat_completion(config: LLMConfig, system_prompt: str, user_prompt: str) -> str:
    """Call POST /chat/completions on any OpenAI-compatible endpoint (llm-gateway, Ollama, BYOK)."""
    url = f"{config.base_url}/chat/completions"
    payload: dict[str, Any] = {
        "model": config.model,
        "temperature": config.temperature,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {config.api_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=config.timeout_s) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="ignore")[:400]
        raise RuntimeError(f"LLM endpoint HTTP {exc.code} at {url}: {err_body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Cannot reach LLM endpoint {url}: {exc.reason}") from exc

    choices = raw.get("choices") or []
    if not choices:
        raise RuntimeError(f"Empty choices returned by LLM endpoint {url}")
    content = choices[0].get("message", {}).get("content", "")
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("LLM returned empty completion content")
    return content


def _extract_json_blob(text: str) -> str:
    # Strip <think>...</think> blocks emitted by reasoning models
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE).strip()
    fence = _FENCE_RE.search(cleaned)
    if fence:
        return fence.group(1).strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        return cleaned[start : end + 1]
    return cleaned


def extract_spec_blind(
    spec_prose: str,
    config: LLMConfig | None = None,
) -> tuple[SpecDocument, dict[str, Any]]:
    """Extract and validate a structured SpecDocument strictly from functional spec prose."""
    cfg = config or LLMConfig.from_env()
    completion = chat_completion(
        cfg,
        system_prompt=BLIND_EXTRACTION_SYSTEM_PROMPT,
        user_prompt=f"Functional Specification:\n\n{spec_prose}",
    )
    json_blob = _extract_json_blob(completion)
    raw_dict = json.loads(json_blob)
    spec_doc = parse_spec_text(json.dumps(raw_dict), fmt="json")
    return spec_doc, raw_dict
