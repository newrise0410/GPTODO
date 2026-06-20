"""LLM 클라이언트 — Codex OAuth 우선, OPENAI_API_KEY 폴백.

`organizer.md` 시스템 프롬프트로 동작하는 '지능형 정리사' 대화 엔진.
KST 현재 날짜는 서버에서 계산해 시스템 메시지 상단에 주입한다
(LLM이 날짜를 추측하지 않게 — 프롬프트 24번 금지사항 충족).
"""

from __future__ import annotations

import datetime as dt
import os
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from openai import OpenAI

from . import codex_oauth

PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "organizer.md"

# Codex(ChatGPT OAuth)가 사용하는 Responses 기반 엔드포인트.
CHATGPT_BASE_URL = "https://chatgpt.com/backend-api/codex"
DEFAULT_MODEL = os.environ.get("LLM_MODEL", "gpt-5-codex")

KST = ZoneInfo("Asia/Seoul")
_WEEKDAYS_KO = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]


def now_kst() -> dt.datetime:
    return dt.datetime.now(KST)


def date_header() -> str:
    """프롬프트 1번 규칙용 한국식 날짜 문자열."""
    n = now_kst()
    return f"{n.year}년 {n.month}월 {n.day}일 {_WEEKDAYS_KO[n.weekday()]}"


def _system_prompt() -> str:
    spec = PROMPT_PATH.read_text(encoding="utf-8")
    n = now_kst()
    date_ctx = (
        "[시스템 제공 현재 시각] 한국 기준(Asia/Seoul, KST) 현재 날짜와 시각은 "
        f"{date_header()}, {n:%H:%M} 이다. 이 값을 권위 있는 현재 시각으로 사용하고, "
        "별도의 웹 검색 없이 모든 상대 날짜(오늘/내일/이번 주/다음 주/이번 달 등)를 "
        "이 기준으로 실제 날짜와 요일로 환산하라."
    )
    return date_ctx + "\n\n---\n\n" + spec


def _build_client() -> tuple[OpenAI, dict[str, str]]:
    """(client, extra_headers) 반환. Codex OAuth가 있으면 우선 사용."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if api_key:
        return OpenAI(api_key=api_key), {}

    creds = codex_oauth.get_credentials()
    headers: dict[str, str] = {}
    if creds.account_id:
        headers["chatgpt-account-id"] = creds.account_id
    base_url = os.environ.get("LLM_BASE_URL", CHATGPT_BASE_URL)
    return OpenAI(api_key=creds.access_token, base_url=base_url), headers


def chat(history: list[dict[str, Any]]) -> str:
    """대화 기록(history: [{role, content}, ...])을 받아 정리사 응답을 반환."""
    client, headers = _build_client()
    messages = [{"role": "system", "content": _system_prompt()}, *history]
    resp = client.chat.completions.create(
        model=DEFAULT_MODEL,
        messages=messages,
        extra_headers=headers or None,
    )
    return resp.choices[0].message.content or ""
