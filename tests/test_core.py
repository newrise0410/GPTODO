"""LLM 없이 결정론적 코어 검증 — 상태/연산/보기/충돌/메뉴."""

import datetime as dt
from pathlib import Path

import pytest

from app import menu, store, timeutil, views
from app.llm.extract import apply_operations


@pytest.fixture(autouse=True)
def temp_db(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "t.db")
    store.init()
    yield
    store.clear()


def _d(offset: int) -> str:
    return (timeutil.today() + dt.timedelta(days=offset)).isoformat()


def test_add_and_calendar_grouping():
    apply_operations([
        {"op": "add", "item": {"title": "면접", "kind": "event", "date": _d(0), "time": "15:00"}},
        {"op": "add", "item": {"title": "운동", "kind": "todo", "date": _d(0)}},
        {"op": "add", "item": {"title": "보고서 작성", "kind": "todo"}},
    ])
    items = store.all_items()
    assert len(items) == 3
    out = views.render_calendar(items, scope="all")
    assert "15:00 면접" in out
    assert "🕒 시간 미정" in out      # 운동(날짜 있고 시간 없음)
    assert "📝 날짜 미정" in out       # 보고서(날짜 없음)
    assert "보고서 작성" in out


def test_today_scope_excludes_future():
    apply_operations([
        {"op": "add", "item": {"title": "오늘일정", "kind": "event", "date": _d(0), "time": "10:00"}},
        {"op": "add", "item": {"title": "다음주일정", "kind": "event", "date": _d(8), "time": "10:00"}},
    ])
    out = views.render_calendar(store.all_items(), scope="today")
    assert "오늘일정" in out
    assert "다음주일정" not in out


def test_conflict_detection():
    apply_operations([
        {"op": "add", "item": {"title": "면접", "kind": "event", "date": _d(1), "time": "15:00"}},
        {"op": "add", "item": {"title": "병원", "kind": "event", "date": _d(1), "time": "15:00"}},
    ])
    conflicts = views.detect_conflicts(store.all_items())
    assert len(conflicts) == 1
    assert "충돌" in views.render_review(store.all_items())


def test_complete_and_update():
    apply_operations([{"op": "add", "item": {"title": "장보기", "kind": "todo"}}])
    item_id = store.all_items()[0].id
    apply_operations([{"op": "complete", "id": item_id}])
    assert store.all_items()[0].status == "done"
    apply_operations([{"op": "update", "id": item_id, "changes": {"title": "장보기(대형마트)"}}])
    assert store.all_items()[0].title == "장보기(대형마트)"


def test_invalid_values_coerced():
    # 잘못된 날짜/시간/카테고리는 버려지고 날짜·시간 미정 처리
    apply_operations([{"op": "add", "item": {
        "title": "모호한 일", "date": "내일", "time": "25:99", "category": "없는카테고리",
    }}])
    it = store.all_items()[0]
    assert it.date is None and it.time is None and it.category is None


def test_menu_routing_no_llm():
    apply_operations([{"op": "add", "item": {"title": "할일", "kind": "todo"}}])
    assert menu.is_menu("📊 대시보드")
    assert menu.is_menu("☀️ 오늘")
    assert not menu.is_menu("내일 회의")
    assert "현황 대시보드" in menu.render("📊 대시보드")
    assert "날짜 미정" in menu.render("📝 날짜 미정")
    assert "기준 날짜를 갱신" in menu.render("🔄 날짜 갱신")


def test_priority_emoji_surfaced():
    apply_operations([{"op": "add", "item": {
        "title": "계약 체결", "kind": "event", "date": _d(0), "time": "11:00", "priority": "very_high",
    }}])
    out = views.render_calendar(store.all_items(), scope="today")
    assert "🔴" in out
