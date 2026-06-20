"""SQLite 기반 할 일 저장소 — 의존성 없이 stdlib만 사용."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "todos.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS todos (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    title     TEXT NOT NULL,
    priority  TEXT NOT NULL DEFAULT 'medium',
    due       TEXT,
    tags      TEXT DEFAULT '',
    done      INTEGER NOT NULL DEFAULT 0,
    created   TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init() -> None:
    with _conn() as conn:
        conn.executescript(SCHEMA)


def add_todo(title: str, priority: str = "medium", due: str | None = None,
             tags: list[str] | None = None) -> int:
    with _conn() as conn:
        cur = conn.execute(
            "INSERT INTO todos (title, priority, due, tags) VALUES (?, ?, ?, ?)",
            (title, priority, due, ",".join(tags or [])),
        )
        return int(cur.lastrowid)


def list_todos(include_done: bool = True) -> list[dict[str, Any]]:
    query = "SELECT * FROM todos"
    if not include_done:
        query += " WHERE done = 0"
    query += " ORDER BY done, CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, due IS NULL, due"
    with _conn() as conn:
        rows = conn.execute(query).fetchall()
    return [_row_to_dict(r) for r in rows]


def set_done(todo_id: int, done: bool) -> None:
    with _conn() as conn:
        conn.execute("UPDATE todos SET done = ? WHERE id = ?", (1 if done else 0, todo_id))


def delete_todo(todo_id: int) -> None:
    with _conn() as conn:
        conn.execute("DELETE FROM todos WHERE id = ?", (todo_id,))


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["tags"] = [t for t in (d.get("tags") or "").split(",") if t]
    d["done"] = bool(d["done"])
    return d
