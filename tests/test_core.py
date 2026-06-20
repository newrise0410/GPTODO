"""LLM 없이 결정론적 코어 검증 — 상태/연산/구조화 보기/충돌/메뉴."""

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


def _all_items(view):
    return [it for sec in view["sections"] for it in sec["items"]]


def _tones(view):
    return {sec["tone"] for sec in view["sections"]}


def test_add_and_calendar_grouping():
    apply_operations([
        {"op": "add", "item": {"title": "면접", "kind": "event", "date": _d(0), "time": "15:00"}},
        {"op": "add", "item": {"title": "운동", "kind": "todo", "date": _d(0)}},
        {"op": "add", "item": {"title": "보고서 작성", "kind": "todo"}},
    ])
    view = views.build_calendar(store.all_items(), scope="all")
    titles = [it["title"] for it in _all_items(view)]
    assert {"면접", "운동", "보고서 작성"} <= set(titles)
    assert "today" in _tones(view)      # 오늘 날짜 그룹
    assert "nodate" in _tones(view)     # 보고서(날짜 미정)
    # 운동은 같은 날 시간 미정 divider를 가진다
    untimed = [it for it in _all_items(view) if it["title"] == "운동"][0]
    assert untimed["divider"] == "시간 미정"


def test_today_scope_excludes_future():
    apply_operations([
        {"op": "add", "item": {"title": "오늘일정", "kind": "event", "date": _d(0), "time": "10:00"}},
        {"op": "add", "item": {"title": "다음주일정", "kind": "event", "date": _d(8), "time": "10:00"}},
    ])
    titles = [it["title"] for it in _all_items(views.build_calendar(store.all_items(), "today"))]
    assert "오늘일정" in titles
    assert "다음주일정" not in titles


def test_conflict_detection():
    apply_operations([
        {"op": "add", "item": {"title": "면접", "kind": "event", "date": _d(1), "time": "15:00"}},
        {"op": "add", "item": {"title": "병원", "kind": "event", "date": _d(1), "time": "15:00"}},
    ])
    assert len(views.detect_conflicts(store.all_items())) == 1
    assert "conflict" in _tones(views.build_review(store.all_items()))


def test_complete_and_update():
    apply_operations([{"op": "add", "item": {"title": "장보기", "kind": "todo"}}])
    item_id = store.all_items()[0].id
    apply_operations([{"op": "complete", "id": item_id}])
    assert store.all_items()[0].status == "done"
    apply_operations([{"op": "update", "id": item_id, "changes": {"title": "장보기(대형마트)"}}])
    assert store.all_items()[0].title == "장보기(대형마트)"


def test_invalid_values_coerced():
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
    assert menu.render("📊 대시보드")["title"] == "현황 대시보드"
    assert menu.render("📝 날짜 미정")["title"] == "날짜 미정"
    assert "기준 날짜" in menu.render("🔄 날짜 갱신")["sections"][0]["label"]


def test_priority_surfaced():
    apply_operations([{"op": "add", "item": {
        "title": "계약 체결", "kind": "event", "date": _d(0), "time": "11:00", "priority": "very_high",
    }}])
    it = _all_items(views.build_calendar(store.all_items(), "today"))[0]
    assert it["priority"] == "very_high"
