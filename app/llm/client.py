"""LLM 클라이언트 — Codex OAuth 우선, OPENAI_API_KEY 폴백.

자연어 입력을 구조화된 할 일(JSON)로 바꾸는 역할만 한다.
백엔드 교체가 쉽도록 호출부는 이 모듈만 의존한다.
"""

from __future__ import annotations

import json
import os
from typing import Any

from openai import OpenAI

from . import codex_oauth

# Codex(ChatGPT OAuth)가 사용하는 Responses 기반 엔드포인트.
# 일반 OPENAI_API_KEY를 쓸 때는 기본 base_url을 그대로 사용한다.
CHATGPT_BASE_URL = "https://chatgpt.com/backend-api/codex"

DEFAULT_MODEL = os.environ.get("LLM_MODEL", "gpt-5-codex")

SYSTEM_PROMPT = """\
너는 할 일(todo) 관리 비서다. 사용자의 한국어/영어 자연어 입력을 분석해서
할 일 목록을 구조화된 JSON으로만 반환한다. 설명 문장은 쓰지 마라.

반환 형식:
{
  "todos": [
    {"title": "...", "priority": "low|medium|high", "due": "YYYY-MM-DD 또는 null", "tags": ["..."]}
  ]
}

규칙:
- 하나의 입력에 여러 할 일이 있으면 모두 분리한다.
- 마감/우선순위가 명시되지 않으면 priority="medium", due=null.
- 오늘 날짜 기준으로 "내일", "다음주" 같은 상대 표현을 절대 날짜로 환산한다.
"""


def _build_client() -> tuple[OpenAI, dict[str, str]]:
    """(client, extra_headers) 반환. Codex OAuth가 있으면 그걸 우선 사용."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if api_key:
        return OpenAI(api_key=api_key), {}

    creds = codex_oauth.get_credentials()
    headers: dict[str, str] = {}
    if creds.account_id:
        headers["chatgpt-account-id"] = creds.account_id
    base_url = os.environ.get("LLM_BASE_URL", CHATGPT_BASE_URL)
    return OpenAI(api_key=creds.access_token, base_url=base_url), headers


def parse_todos(text: str, today: str) -> list[dict[str, Any]]:
    """자연어 → 할 일 리스트. 실패 시 단일 할 일로 폴백."""
    client, headers = _build_client()
    user_msg = f"오늘 날짜: {today}\n\n입력:\n{text}"

    resp = client.chat.completions.create(
        model=DEFAULT_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        response_format={"type": "json_object"},
        extra_headers=headers or None,
    )
    content = resp.choices[0].message.content or "{}"
    try:
        data = json.loads(content)
        todos = data.get("todos", [])
        return todos if isinstance(todos, list) else []
    except json.JSONDecodeError:
        return [{"title": text.strip(), "priority": "medium", "due": None, "tags": []}]
