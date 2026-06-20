"""LLM 호출 — Codex(ChatGPT OAuth) Responses SSE 우선, OPENAI_API_KEY 폴백.

ChatGPT 백엔드(`/backend-api/codex/responses`)는 표준 chat/completions가 아니라
스트리밍 Responses API만 받는다. 실측으로 확인한 요구사항:
  - 헤더 `originator: codex_cli_rs` (없으면 모든 모델이 'not supported'로 거부됨)
  - `chatgpt-account-id` 헤더
  - body: instructions(시스템), store=false, stream=true
  - input 항목 content type: user→input_text, assistant→output_text
  - SSE에서 `response.output_text.delta`를 모아 본문을 구성
"""

from __future__ import annotations

import json
import os

import httpx

from . import codex_oauth

CODEX_RESPONSES_URL = "https://chatgpt.com/backend-api/codex/responses"
ORIGINATOR = "codex_cli_rs"
USER_AGENT = "codex_cli_rs/0.141.0"

# Codex(ChatGPT 계정)에서 지원되는 모델. 환경변수로 override 가능.
CODEX_MODEL = os.environ.get("LLM_MODEL", "gpt-5.4-mini")
# OPENAI_API_KEY 경로 기본 모델.
APIKEY_MODEL = os.environ.get("LLM_MODEL", "gpt-4o-mini")

_TIMEOUT = httpx.Timeout(120.0, connect=15.0)


def _codex_complete(instructions: str, history: list[dict]) -> str:
    creds = codex_oauth.get_credentials()
    headers = {
        "Authorization": f"Bearer {creds.access_token}",
        "chatgpt-account-id": creds.account_id or "",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "originator": ORIGINATOR,
        "User-Agent": USER_AGENT,
    }
    input_items = [
        {
            "role": m["role"],
            "content": [{
                "type": "output_text" if m["role"] == "assistant" else "input_text",
                "text": m["content"],
            }],
        }
        for m in history
    ]
    body = {
        "model": CODEX_MODEL,
        "instructions": instructions,
        "store": False,
        "stream": True,
        "input": input_items,
    }

    text = ""
    with httpx.stream("POST", CODEX_RESPONSES_URL, headers=headers, json=body, timeout=_TIMEOUT) as r:
        if r.status_code != 200:
            raise RuntimeError(f"Codex 응답 {r.status_code}: {r.read()[:200]!r}")
        for line in r.iter_lines():
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                ev = json.loads(data)
            except json.JSONDecodeError:
                continue
            if ev.get("type") == "response.output_text.delta":
                text += ev.get("delta", "")
    return text


def _apikey_complete(instructions: str, history: list[dict], json_mode: bool) -> str:
    from openai import OpenAI

    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    messages = [{"role": "system", "content": instructions}, *history]
    kwargs = {"model": APIKEY_MODEL, "messages": messages}
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    resp = client.chat.completions.create(**kwargs)
    return resp.choices[0].message.content or ""


def complete(instructions: str, history: list[dict], *, json_mode: bool = True) -> str:
    """system instructions + 대화(history) → 모델 텍스트 응답.

    history: [{"role": "user"|"assistant", "content": str}, ...]
    """
    if os.environ.get("OPENAI_API_KEY"):
        return _apikey_complete(instructions, history, json_mode)
    return _codex_complete(instructions, history)
