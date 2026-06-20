"""Codex / ChatGPT OAuth 자격증명 처리.

Codex CLI(`codex login`)는 ChatGPT 계정 OAuth 토큰을 `~/.codex/auth.json`에 저장한다.
이 모듈은 그 파일을 읽고, access_token이 만료됐으면 refresh_token으로 갱신한다.

auth.json 예시 구조::

    {
      "OPENAI_API_KEY": null,
      "tokens": {
        "id_token": "...",
        "access_token": "...",
        "refresh_token": "...",
        "account_id": "..."
      },
      "last_refresh": "2026-06-20T..."
    }
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

AUTH_PATH = Path.home() / ".codex" / "auth.json"

# Codex CLI가 사용하는 공개 OAuth 클라이언트 ID / 토큰 엔드포인트.
OAUTH_TOKEN_URL = "https://auth.openai.com/oauth/token"
OAUTH_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"

# access_token 만료 여유(초). 이보다 적게 남으면 미리 갱신한다.
REFRESH_MARGIN = 5 * 60


class CodexAuthError(RuntimeError):
    """auth.json이 없거나 토큰 갱신에 실패했을 때."""


@dataclass
class Credentials:
    access_token: str
    account_id: str | None


def _parse_jwt_exp(token: str) -> int | None:
    """JWT access_token에서 exp(만료 epoch)를 best-effort로 추출."""
    import base64

    try:
        payload_b64 = token.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        return int(payload.get("exp")) if "exp" in payload else None
    except Exception:
        return None


def load_raw(path: Path = AUTH_PATH) -> dict:
    if not path.exists():
        raise CodexAuthError(
            f"{path} 가 없습니다. 먼저 `codex login` 으로 ChatGPT 계정 로그인을 해주세요."
        )
    return json.loads(path.read_text())


def _refresh(raw: dict, path: Path) -> dict:
    refresh_token = (raw.get("tokens") or {}).get("refresh_token")
    if not refresh_token:
        raise CodexAuthError("refresh_token이 없어 토큰을 갱신할 수 없습니다. `codex login` 재실행 필요.")

    resp = httpx.post(
        OAUTH_TOKEN_URL,
        json={
            "client_id": OAUTH_CLIENT_ID,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
        timeout=30,
    )
    if resp.status_code != 200:
        raise CodexAuthError(f"토큰 갱신 실패 ({resp.status_code}): {resp.text[:200]}")

    data = resp.json()
    tokens = raw.setdefault("tokens", {})
    tokens["access_token"] = data["access_token"]
    if data.get("refresh_token"):
        tokens["refresh_token"] = data["refresh_token"]
    if data.get("id_token"):
        tokens["id_token"] = data["id_token"]
    raw["last_refresh"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    # 갱신된 토큰을 다시 저장(권한 0600 유지).
    path.write_text(json.dumps(raw, indent=2))
    return raw


def get_credentials(path: Path = AUTH_PATH) -> Credentials:
    """유효한 access_token과 account_id를 반환. 필요 시 자동 갱신."""
    raw = load_raw(path)
    tokens = raw.get("tokens") or {}
    access_token = tokens.get("access_token")

    needs_refresh = not access_token
    if access_token:
        exp = _parse_jwt_exp(access_token)
        if exp is not None and exp - time.time() < REFRESH_MARGIN:
            needs_refresh = True

    if needs_refresh:
        raw = _refresh(raw, path)
        tokens = raw.get("tokens") or {}
        access_token = tokens.get("access_token")

    if not access_token:
        raise CodexAuthError("access_token을 확보하지 못했습니다.")

    return Credentials(access_token=access_token, account_id=tokens.get("account_id"))
