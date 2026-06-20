"""FastAPI 엔트리포인트 — 지능형 정리사 채팅 앱.

대화는 클라이언트가 보관하고 매 요청마다 history를 보낸다(서버 무상태).
프롬프트 19번 규칙대로 외부 저장은 하지 않으며 '현재 대화 기준'으로 동작한다.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from .llm import CodexAuthError, chat, date_header

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="LLM 정리사")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


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
    history = [{"role": m.role, "content": m.content} for m in req.messages]
    # 사용자/어시스턴트 메시지만 전달(시스템 프롬프트는 서버에서 주입).
    history = [m for m in history if m["role"] in ("user", "assistant")]
    if not history or history[-1]["role"] != "user":
        raise HTTPException(status_code=400, detail="마지막 메시지는 user여야 합니다.")
    try:
        reply = chat(history)
    except CodexAuthError as e:
        raise HTTPException(status_code=401, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001 — 프런트에 원인 전달
        raise HTTPException(status_code=502, detail=f"LLM 호출 실패: {e}") from e
    return {"reply": reply}


@app.get("/api/today")
def api_today():
    return {"date_header": date_header()}


@app.get("/health")
def health():
    return {"ok": True}
