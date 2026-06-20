"""SQLite 저장소 — 진실의 원천(Source of Truth).

프롬프트 §19: 외부 캘린더/DB 저장은 하지 않지만, 앱 내부 영속화로
새로고침해도 '현재 정리된 항목'이 유지된다.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .models import Item

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "items.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    title        TEXT NOT NULL,
    kind         TEXT NOT NULL DEFAULT 'todo',
    date         TEXT,
    time         TEXT,
    category     TEXT,
    priority     TEXT,
    recurrence   TEXT,
    project      TEXT,
    location     TEXT,
    people       TEXT,
    estimate_min INTEGER,
    status       TEXT NOT NULL DEFAULT 'open',
    needs_review INTEGER NOT NULL DEFAULT 0,
    review_reason TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

_COLS = [
    "title", "kind", "date", "time", "category", "priority", "recurrence",
    "project", "location", "people", "estimate_min", "status",
    "needs_review", "review_reason",
]


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init() -> None:
    with _conn() as conn:
        conn.executescript(SCHEMA)


def _scalar(v):
    """SQLite가 바인딩할 수 있는 스칼라로 강제(리스트/딕셔너리 방어)."""
    if isinstance(v, (str, int, float, bytes)) or v is None:
        return v
    if isinstance(v, list):
        return ", ".join(str(x) for x in v if x is not None) or None
    return str(v)


def add(item: Item) -> int:
    row = item.to_row()
    cols = ", ".join(_COLS)
    ph = ", ".join("?" for _ in _COLS)
    with _conn() as conn:
        cur = conn.execute(
            f"INSERT INTO items ({cols}) VALUES ({ph})",
            [_scalar(row[c]) for c in _COLS],
        )
        return int(cur.lastrowid)


def update(item_id: int, changes: dict) -> None:
    fields = {k: v for k, v in changes.items() if k in _COLS}
    if "needs_review" in fields:
        fields["needs_review"] = int(bool(fields["needs_review"]))
    if not fields:
        return
    sets = ", ".join(f"{k} = ?" for k in fields)
    with _conn() as conn:
        conn.execute(f"UPDATE items SET {sets} WHERE id = ?",
                     [*(_scalar(v) for v in fields.values()), item_id])


def set_status(item_id: int, status: str) -> None:
    with _conn() as conn:
        conn.execute("UPDATE items SET status = ? WHERE id = ?", (status, item_id))


def delete(item_id: int) -> None:
    with _conn() as conn:
        conn.execute("DELETE FROM items WHERE id = ?", (item_id,))


def all_items(include_done: bool = True) -> list[Item]:
    q = "SELECT * FROM items"
    if not include_done:
        q += " WHERE status != 'done'"
    q += " ORDER BY created_at"
    with _conn() as conn:
        rows = conn.execute(q).fetchall()
    return [Item.from_row(dict(r)) for r in rows]


def clear() -> None:
    with _conn() as conn:
        conn.execute("DELETE FROM items")
