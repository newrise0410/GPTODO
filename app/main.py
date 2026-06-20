"""FastAPI 엔트리포인트 — GPTODO (하이브리드).

흐름:
  1) 입력이 빠른 메뉴 라벨이면 → LLM 없이 보기 렌더(즉시·무료·일관)
  2) 그 외 자유 문장이면 → LLM 추출 → 상태에 연산 적용 → 캘린더 렌더
상태(SoT)는 SQLite. 충돌·날짜·정렬은 전부 코드가 결정론적으로 처리.
"""

from __future__ import annotations

from pathlib import Path

import json

import os

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import (HTMLResponse, PlainTextResponse, RedirectResponse,
                               Response, StreamingResponse)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from . import gcal, icalfeed, menu, store, sync, timeutil, views
from .llm import CodexAuthError, apply_operations, extract, stream
from .models import coerce_changes

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="GPTODO")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

store.init()

HISTORY_LIMIT = 8  # 추출에 넘길 최근 대화 수(토큰 절약)


class Message(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: list[Message]


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {})


@app.post("/api/chat")
def api_chat(req: ChatRequest):
    history = _validate(req)
    text = history[-1]["content"].strip()

    # 1) 빠른 메뉴 → 보기 전환 (LLM 미사용)
    if menu.is_menu(text):
        return {"view": menu.render(text), "source": "menu"}

    # 2) 자유 문장 → LLM 추출 → 연산 적용 → 갱신된 캘린더
    try:
        result = extract(history[-HISTORY_LIMIT:])
    except CodexAuthError as e:
        raise HTTPException(status_code=401, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"LLM 호출 실패: {e}") from e

    counts = apply_operations(result["operations"])
    view = _reply_view(result)
    _save_turn(text, view)
    return {"view": view, "source": "llm", "counts": counts}


def _reply_view(result: dict) -> dict:
    """추출 결과를 적용한 뒤의 전체 캘린더 + 수신확인/질문 부착."""
    view = views.build_calendar(store.all_items(), scope="all")
    view["note"] = result["reply"] or None
    view["questions"] = result["questions"]
    return view


def _assistant_context(view: dict) -> str:
    """LLM 후속 맥락용 요약 텍스트(프런트 assistantContext와 동일 규칙)."""
    parts = [view.get("note") or view.get("title", "")]
    if view.get("questions"):
        parts.append("질문: " + " / ".join(view["questions"]))
    return " ".join(p for p in parts if p)


def _save_turn(user_text: str, view: dict) -> None:
    """LLM 대화 한 턴(사용자+어시스턴트)을 영속화. 메뉴 내비게이션은 저장 안 함."""
    store.add_message("user", user_text)
    store.add_message("assistant", _assistant_context(view), view=view)


def _validate(req: ChatRequest) -> list[dict]:
    history = [{"role": m.role, "content": m.content}
               for m in req.messages if m.role in ("user", "assistant")]
    if not history or history[-1]["role"] != "user":
        raise HTTPException(status_code=400, detail="마지막 메시지는 user여야 합니다.")
    return history


def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


@app.post("/api/chat/stream")
def api_chat_stream(req: ChatRequest):
    """SSE 스트리밍: 수신 확인 문장을 실시간(note 이벤트)으로, 완료 시 view 이벤트로."""
    history = _validate(req)
    text = history[-1]["content"].strip()

    def gen():
        if menu.is_menu(text):
            yield _sse({"type": "view", "view": menu.render(text)})
            return
        result = {"reply": "", "operations": [], "questions": []}
        try:
            for kind, payload in stream(history[-HISTORY_LIMIT:]):
                if kind == "note":
                    yield _sse({"type": "note", "text": payload})
                else:
                    result = payload
        except CodexAuthError as e:
            yield _sse({"type": "error", "detail": str(e)})
            return
        except Exception as e:  # noqa: BLE001
            yield _sse({"type": "error", "detail": f"LLM 호출 실패: {e}"})
            return
        counts = apply_operations(result["operations"])
        view = _reply_view(result)
        _save_turn(text, view)
        yield _sse({"type": "view", "view": view, "counts": counts})

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/messages")
def api_messages():
    """저장된 대화 기록(새로고침 복원용)."""
    return {"messages": store.all_messages()}


@app.post("/api/messages/clear")
def api_clear_messages():
    """대화 기록만 비움(항목/프로필은 유지)."""
    store.clear_messages()
    return {"ok": True}


@app.get("/api/view")
def api_view():
    """초기 로드/새로고침용 — 저장된 항목의 전체 캘린더."""
    return {"view": views.build_calendar(store.all_items(), scope="all")}


@app.post("/api/items/{item_id}/toggle")
def api_toggle(item_id: int):
    """항목 완료/미완료 토글 후 갱신된 캘린더를 반환."""
    it = store.get(item_id)
    if it is None:
        raise HTTPException(status_code=404, detail="항목을 찾을 수 없어요.")
    store.set_status(item_id, "open" if it.status == "done" else "done")
    return {"view": views.build_calendar(store.all_items(), scope="all")}


@app.post("/api/items/{item_id}/delete")
def api_delete(item_id: int):
    """항목 삭제."""
    if not store.delete(item_id):
        raise HTTPException(status_code=404, detail="항목을 찾을 수 없어요.")
    return {"ok": True}


class ItemChanges(BaseModel):
    changes: dict


@app.post("/api/items/{item_id}/update")
def api_update(item_id: int, body: ItemChanges):
    """항목 직접 수정(제목/시간/날짜 등). 검증 후 적용."""
    if store.get(item_id) is None:
        raise HTTPException(status_code=404, detail="항목을 찾을 수 없어요.")
    n = store.update(item_id, coerce_changes(body.changes))
    return {"ok": n > 0}


@app.get("/calendar.ics")
def calendar_feed():
    """애플/구글 캘린더 'URL 구독'용 iCal 피드(일정 + 마감 있는 할 일)."""
    body = icalfeed.build(store.all_items())
    return PlainTextResponse(body, media_type="text/calendar; charset=utf-8",
                             headers={"Content-Disposition": 'inline; filename="gptodo.ics"'})


@app.get("/api/items/{item_id}/ics")
def item_ics(item_id: int):
    """단일 일정 .ics 다운로드(애플/아웃룩 등에서 열기)."""
    it = store.get(item_id)
    if it is None or not icalfeed.is_eligible(it):
        raise HTTPException(status_code=404, detail="캘린더에 넣을 수 있는 일정이 아니에요.")
    return Response(icalfeed.single_ics(it), media_type="text/calendar; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="item-{item_id}.ics"'})


def _redirect_uri(request: Request) -> str:
    return os.environ.get("GOOGLE_REDIRECT_URI") or str(request.base_url) + "oauth/google/callback"


@app.get("/oauth/google/start")
def google_start(request: Request):
    if not gcal.is_configured():
        raise HTTPException(status_code=400,
                            detail="GOOGLE_CLIENT_ID/SECRET 환경변수를 먼저 설정해주세요.")
    return RedirectResponse(gcal.auth_url(_redirect_uri(request)))


@app.get("/oauth/google/callback", response_class=HTMLResponse)
def google_callback(request: Request, code: str | None = None, error: str | None = None):
    if error or not code:
        return HTMLResponse(f"<p>연결 실패: {error or '코드 없음'}</p><a href='/'>돌아가기</a>")
    try:
        gcal.exchange_code(code, _redirect_uri(request))
    except gcal.GCalError as e:
        return HTMLResponse(f"<p>토큰 교환 실패: {e}</p><a href='/'>돌아가기</a>")
    return HTMLResponse("<p>구글 캘린더 연결 완료! 창을 닫고 동기화를 눌러주세요.</p>"
                        "<script>setTimeout(()=>{location.href='/'},1200)</script>")


@app.get("/api/sync/status")
def sync_status():
    return gcal.status()


@app.post("/api/sync")
def api_sync():
    try:
        client = gcal.GoogleCalendar()
        counts = sync.sync(client)
    except gcal.NotAuthed as e:
        raise HTTPException(status_code=401, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"동기화 실패: {e}") from e
    return {"ok": True, "counts": counts, "view": views.build_calendar(store.all_items(), "all")}


@app.post("/api/google/disconnect")
def google_disconnect():
    gcal.disconnect()
    return {"ok": True}


@app.get("/api/today")
def api_today():
    return {"date_header": timeutil.header()}


@app.post("/api/reset")
def api_reset():
    store.clear()
    return {"ok": True}


@app.get("/health")
def health():
    return {"ok": True}
