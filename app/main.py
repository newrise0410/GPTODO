"""FastAPI 엔트리포인트 — GPTODO (하이브리드).

흐름:
  1) 입력이 빠른 메뉴 라벨이면 → LLM 없이 보기 렌더(즉시·무료·일관)
  2) 그 외 자유 문장이면 → LLM 추출 → 상태에 연산 적용 → 캘린더 렌더
상태(SoT)는 SQLite. 충돌·날짜·정렬은 전부 코드가 결정론적으로 처리.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from . import menu, store, timeutil, views
from .llm import CodexAuthError, apply_operations, extract

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
    history = [{"role": m.role, "content": m.content}
               for m in req.messages if m.role in ("user", "assistant")]
    if not history or history[-1]["role"] != "user":
        raise HTTPException(status_code=400, detail="마지막 메시지는 user여야 합니다.")
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
    view = views.build_calendar(store.all_items(), scope="all")
    view["note"] = result["reply"] or None
    view["questions"] = result["questions"]
    return {"view": view, "source": "llm", "counts": counts}


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
