"""SQLite 저장소 — 진실의 원천(Source of Truth).

프롬프트 §19: 외부 캘린더/DB 저장은 하지 않지만, 앱 내부 영속화로
새로고침해도 '현재 정리된 항목'이 유지된다.

쓰기는 single-connection 트랜잭션(`apply_batch`)으로 묶어 부분 적용/경합을 막는다.
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
    deadline     TEXT,
    parent_id    INTEGER,
    sort_order   INTEGER NOT NULL DEFAULT 0,
    status       TEXT NOT NULL DEFAULT 'open',
    needs_review INTEGER NOT NULL DEFAULT 0,
    review_reason TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

_COLS = [
    "title", "kind", "date", "time", "category", "priority", "recurrence",
    "project", "location", "people", "estimate_min", "deadline", "parent_id",
    "sort_order", "status", "needs_review", "review_reason",
]

# 기존 DB에 없을 수 있는 컬럼 → ALTER TABLE로 보강(가벼운 마이그레이션).
_MIGRATIONS = [
    ("deadline", "deadline TEXT"),
    ("parent_id", "parent_id INTEGER"),
    ("sort_order", "sort_order INTEGER NOT NULL DEFAULT 0"),
]


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init() -> None:
    with _conn() as conn:
        conn.executescript(SCHEMA)
        existing = {r["name"] for r in conn.execute("PRAGMA table_info(items)")}
        for col, ddl in _MIGRATIONS:
            if col not in existing:
                conn.execute(f"ALTER TABLE items ADD COLUMN {ddl}")


def _scalar(v):
    """SQLite가 바인딩할 수 있는 스칼라로 강제(리스트/딕셔너리 방어)."""
    if isinstance(v, (str, int, float, bytes)) or v is None:
        return v
    if isinstance(v, list):
        return ", ".join(str(x) for x in v if x is not None) or None
    return str(v)


# ---- 단일 연산(내부, conn 공유) — rowcount 반환 ----

def _add(conn: sqlite3.Connection, item: Item) -> int:
    row = item.to_row()
    cols = ", ".join(_COLS)
    ph = ", ".join("?" for _ in _COLS)
    cur = conn.execute(f"INSERT INTO items ({cols}) VALUES ({ph})",
                       [_scalar(row[c]) for c in _COLS])
    return int(cur.lastrowid)


def _set_status(conn: sqlite3.Connection, item_id: int, status: str) -> int:
    cur = conn.execute("UPDATE items SET status = ? WHERE id = ?", (status, item_id))
    return cur.rowcount


def _update(conn: sqlite3.Connection, item_id: int, changes: dict) -> int:
    fields = {k: v for k, v in changes.items() if k in _COLS}
    if "needs_review" in fields:
        fields["needs_review"] = int(bool(fields["needs_review"]))
    if not fields:
        return 0
    sets = ", ".join(f"{k} = ?" for k in fields)
    cur = conn.execute(f"UPDATE items SET {sets} WHERE id = ?",
                       [*(_scalar(v) for v in fields.values()), item_id])
    return cur.rowcount


def _delete(conn: sqlite3.Connection, item_id: int) -> int:
    cur = conn.execute("DELETE FROM items WHERE id = ?", (item_id,))
    return cur.rowcount


# ---- 배치(트랜잭션) ----

def apply_batch(ops: list[tuple]) -> dict[str, int]:
    """정규화된 연산들을 단일 트랜잭션으로 적용. rowcount 기반 카운트 반환.

    ops 항목 형식:
      ("add", Item)
      ("status", id, "done"|"open")
      ("update", id, changes_dict)
      ("delete", id)
    개별 연산 실패는 건너뛰되(앱이 멈추지 않게), 전체는 한 번에 커밋한다.
    """
    counts = {"added": 0, "completed": 0, "reopened": 0, "updated": 0, "deleted": 0}
    conn = _conn()
    try:
        with conn:  # 트랜잭션: 블록 정상 종료 시 commit, 예외 시 rollback
            for op in ops:
                try:
                    kind = op[0]
                    if kind == "add":
                        _add(conn, op[1])
                        counts["added"] += 1
                    elif kind == "status":
                        if _set_status(conn, op[1], op[2]):
                            counts["completed" if op[2] == "done" else "reopened"] += 1
                    elif kind == "update":
                        if _update(conn, op[1], op[2]):
                            counts["updated"] += 1
                    elif kind == "delete":
                        if _delete(conn, op[1]):
                            counts["deleted"] += 1
                except (ValueError, TypeError, sqlite3.Error):
                    continue
    finally:
        conn.close()
    return counts


# ---- 단건 공개 API(편의) ----

def add(item: Item) -> int:
    with _conn() as conn:
        return _add(conn, item)


def set_status(item_id: int, status: str) -> int:
    with _conn() as conn:
        return _set_status(conn, item_id, status)


def update(item_id: int, changes: dict) -> int:
    with _conn() as conn:
        return _update(conn, item_id, changes)


def delete(item_id: int) -> int:
    with _conn() as conn:
        return _delete(conn, item_id)


# ---- 조회 ----

def all_items(include_done: bool = True) -> list[Item]:
    q = "SELECT * FROM items"
    if not include_done:
        q += " WHERE status != 'done'"
    q += " ORDER BY created_at, id"
    with _conn() as conn:
        rows = conn.execute(q).fetchall()
    return [Item.from_row(dict(r)) for r in rows]


def get(item_id: int) -> Item | None:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    return Item.from_row(dict(row)) if row else None


def clear() -> None:
    with _conn() as conn:
        conn.execute("DELETE FROM items")
