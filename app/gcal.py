"""Google Calendar 양방향 연동 — OAuth + REST(httpx).

전제: 사용자가 Google Cloud Console에서 OAuth 클라이언트를 만들어
GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET 환경변수로 제공해야 한다.
토큰·동기화 상태는 data/gcal_token.json(.gitignore)에 보관한다.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

import httpx

TOKEN_PATH = Path(__file__).resolve().parent.parent / "data" / "gcal_token.json"
SCOPE = "https://www.googleapis.com/auth/calendar"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://www.googleapis.com/calendar/v3"
CAL_NAME = "GPTODO"
_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


class GCalError(RuntimeError):
    pass


class NotAuthed(GCalError):
    pass


# ---- 설정/토큰 ----

def client_id() -> str | None:
    return os.environ.get("GOOGLE_CLIENT_ID") or _load().get("client_id")


def client_secret() -> str | None:
    return os.environ.get("GOOGLE_CLIENT_SECRET") or _load().get("client_secret")


def is_configured() -> bool:
    return bool(client_id() and client_secret())


def save_credentials(cid: str, secret: str) -> None:
    """앱 UI에서 받은 OAuth 클라이언트 자격증명을 토큰 파일에 저장(env 없이 사용)."""
    tok = _load()
    tok["client_id"] = cid
    tok["client_secret"] = secret
    _save(tok)


def _load() -> dict:
    try:
        return json.loads(TOKEN_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save(data: dict) -> None:
    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_PATH.write_text(json.dumps(data, indent=2))
    try:
        os.chmod(TOKEN_PATH, 0o600)  # client_secret/토큰 평문 → 소유자만 읽기
    except OSError:
        pass


def is_authed() -> bool:
    return bool(_load().get("refresh_token"))


def status() -> dict:
    tok = _load()
    return {"configured": is_configured(), "authed": bool(tok.get("refresh_token")),
            "calendar_id": tok.get("calendar_id")}


# ---- OAuth ----

def auth_url(redirect_uri: str) -> str:
    from urllib.parse import urlencode
    params = {
        "client_id": client_id(), "redirect_uri": redirect_uri, "response_type": "code",
        "scope": SCOPE, "access_type": "offline", "prompt": "consent",
    }
    return f"{AUTH_URL}?{urlencode(params)}"


def exchange_code(code: str, redirect_uri: str) -> None:
    r = httpx.post(TOKEN_URL, timeout=_TIMEOUT, data={
        "code": code, "client_id": client_id(), "client_secret": client_secret(),
        "redirect_uri": redirect_uri, "grant_type": "authorization_code",
    })
    if r.status_code != 200:
        raise GCalError(f"토큰 교환 실패: {r.text[:200]}")
    d = r.json()
    tok = _load()
    tok["refresh_token"] = d.get("refresh_token", tok.get("refresh_token"))
    tok["access_token"] = d["access_token"]
    tok["expiry"] = (dt.datetime.now(dt.timezone.utc)
                     + dt.timedelta(seconds=d.get("expires_in", 3600) - 60)).isoformat()
    _save(tok)


def disconnect() -> None:
    """로그인 토큰만 해제(클라이언트 자격증명은 유지해 재연결을 쉽게)."""
    tok = _load()
    for k in ("refresh_token", "access_token", "expiry", "sync_token", "calendar_id"):
        tok.pop(k, None)
    _save(tok)


# ---- 클라이언트 ----

class GoogleCalendar:
    """인증된 호출 래퍼. calendar_id를 확보하고 이벤트 CRUD/증분 동기화 제공."""

    def __init__(self) -> None:
        if not is_configured():
            raise NotAuthed("GOOGLE_CLIENT_ID/SECRET 미설정")
        if not is_authed():
            raise NotAuthed("구글 계정 미연결")

    def _access_token(self) -> str:
        tok = _load()
        exp = tok.get("expiry")
        fresh = exp and dt.datetime.fromisoformat(exp) > dt.datetime.now(dt.timezone.utc)
        if tok.get("access_token") and fresh:
            return tok["access_token"]
        r = httpx.post(TOKEN_URL, timeout=_TIMEOUT, data={
            "refresh_token": tok["refresh_token"], "client_id": client_id(),
            "client_secret": client_secret(), "grant_type": "refresh_token",
        })
        if r.status_code != 200:
            raise NotAuthed(f"토큰 갱신 실패: {r.text[:160]}")
        d = r.json()
        tok["access_token"] = d["access_token"]
        tok["expiry"] = (dt.datetime.now(dt.timezone.utc)
                         + dt.timedelta(seconds=d.get("expires_in", 3600) - 60)).isoformat()
        _save(tok)
        return tok["access_token"]

    def _req(self, method: str, path: str, **kw) -> httpx.Response:
        headers = {"Authorization": f"Bearer {self._access_token()}"}
        r = httpx.request(method, API + path, headers=headers, timeout=_TIMEOUT, **kw)
        return r

    def calendar_id(self) -> str:
        tok = _load()
        if tok.get("calendar_id"):
            return tok["calendar_id"]
        # 기존 GPTODO 캘린더 찾기
        r = self._req("GET", "/users/me/calendarList")
        for c in r.json().get("items", []) if r.status_code == 200 else []:
            if c.get("summary") == CAL_NAME:
                tok["calendar_id"] = c["id"]
                _save(tok)
                return c["id"]
        # 없으면 생성
        r = self._req("POST", "/calendars", json={"summary": CAL_NAME,
                                                  "timeZone": "Asia/Seoul"})
        if r.status_code not in (200, 201):
            raise GCalError(f"캘린더 생성 실패: {r.text[:160]}")
        cid = r.json()["id"]
        tok["calendar_id"] = cid
        _save(tok)
        return cid

    def insert_event(self, body: dict) -> str:
        r = self._req("POST", f"/calendars/{self.calendar_id()}/events", json=body)
        if r.status_code not in (200, 201):
            raise GCalError(f"이벤트 생성 실패: {r.text[:160]}")
        return r.json()["id"]

    def update_event(self, event_id: str, body: dict) -> None:
        # PATCH: 우리가 보내는 필드만 갱신 → 구글에서 추가한 알림/색/설명 등 보존
        r = self._req("PATCH", f"/calendars/{self.calendar_id()}/events/{event_id}", json=body)
        if r.status_code == 404:
            raise KeyError(event_id)  # 원격에서 사라짐 → 재생성 유도
        if r.status_code != 200:
            raise GCalError(f"이벤트 수정 실패: {r.text[:160]}")

    def delete_event(self, event_id: str) -> None:
        r = self._req("DELETE", f"/calendars/{self.calendar_id()}/events/{event_id}")
        if r.status_code not in (200, 204, 404, 410):
            raise GCalError(f"이벤트 삭제 실패: {r.text[:160]}")

    def list_changes(self) -> tuple[list[dict], str | None]:
        """증분 동기화. (events, next_sync_token). syncToken 만료 시 전체 재조회."""
        tok = _load()
        cid = self.calendar_id()
        params = {"singleEvents": "true", "showDeleted": "true", "maxResults": "250"}
        if tok.get("sync_token"):
            params["syncToken"] = tok["sync_token"]
        else:
            params["timeMin"] = (dt.datetime.now(dt.timezone.utc)
                                 - dt.timedelta(days=30)).isoformat()
        events: list[dict] = []
        page = None
        while True:
            if page:
                params["pageToken"] = page
            r = self._req("GET", f"/calendars/{cid}/events", params=params)
            if r.status_code == 410:  # syncToken 만료
                tok.pop("sync_token", None)
                _save(tok)
                params.pop("syncToken", None)
                params["timeMin"] = (dt.datetime.now(dt.timezone.utc)
                                     - dt.timedelta(days=30)).isoformat()
                continue
            if r.status_code != 200:
                raise GCalError(f"이벤트 조회 실패: {r.text[:160]}")
            data = r.json()
            events.extend(data.get("items", []))
            page = data.get("nextPageToken")
            if not page:
                next_token = data.get("nextSyncToken")
                return events, next_token

    def save_sync_token(self, token: str | None) -> None:
        if token:
            tok = _load()
            tok["sync_token"] = token
            _save(tok)
