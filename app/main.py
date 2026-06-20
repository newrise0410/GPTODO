"""FastAPI 엔트리포인트 — LLM 할 일 웹앱."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import db
from .llm import CodexAuthError, parse_todos

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="LLM TO-DO")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# 임포트 시점에 스키마 보장 — uvicorn/TestClient 양쪽 모두에서 안전.
db.init()


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    todos = db.list_todos()
    return templates.TemplateResponse(
        request, "index.html", {"todos": todos}
    )


@app.post("/add")
def add(text: str = Form(...)):
    """자연어 입력을 LLM으로 파싱해 할 일들을 저장."""
    today = dt.date.today().isoformat()
    try:
        parsed = parse_todos(text, today=today)
    except CodexAuthError as e:
        raise HTTPException(status_code=401, detail=str(e)) from e
    except Exception:
        # LLM 실패 시 입력 원문을 그대로 저장(앱이 멈추지 않게).
        parsed = [{"title": text.strip(), "priority": "medium", "due": None, "tags": []}]

    for t in parsed:
        db.add_todo(
            title=t.get("title", "").strip() or text.strip(),
            priority=t.get("priority", "medium"),
            due=t.get("due"),
            tags=t.get("tags", []),
        )
    return RedirectResponse("/", status_code=303)


@app.post("/todos/{todo_id}/toggle")
def toggle(todo_id: int, done: bool = Form(...)):
    db.set_done(todo_id, done)
    return RedirectResponse("/", status_code=303)


@app.post("/todos/{todo_id}/delete")
def remove(todo_id: int):
    db.delete_todo(todo_id)
    return RedirectResponse("/", status_code=303)


@app.get("/api/todos")
def api_todos():
    return JSONResponse(db.list_todos())


@app.get("/health")
def health():
    return {"ok": True}
