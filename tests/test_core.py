"""LLM 없이 결정론적 코어 검증 — 상태/연산/구조화 보기/충돌/메뉴."""

import datetime as dt
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import menu, store, timeutil, views
from app.llm.extract import apply_operations
from app.main import app
from app.models import coerce_changes, coerce_item


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


def test_list_valued_fields_coerced():
    # LLM이 people/location 등을 리스트로 줘도 저장이 깨지지 않아야 한다(회귀)
    apply_operations([{"op": "add", "item": {
        "title": "회의", "kind": "event", "date": _d(0), "time": "10:00",
        "people": ["김대리", "박과장"], "location": ["3층", "회의실"],
    }}])
    it = store.all_items()[0]
    assert it.people == "김대리, 박과장"
    assert it.location == "3층, 회의실"
    # update도 리스트 방어
    apply_operations([{"op": "update", "id": it.id, "changes": {"people": ["최부장"]}}])
    assert store.all_items()[0].people == "최부장"


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


# ───────── 회귀: Codex 리뷰 지적 사항 ─────────

def test_malformed_operations_never_crash():
    # 비-리스트 / 문자열 op / item 누락 / 잘못된 changes 모두 예외 없이 무시
    assert apply_operations("not a list") == _zero()
    assert apply_operations(["just a string", 123, None]) == _zero()
    assert apply_operations([{"op": "add"}]) == _zero()              # item 없음
    assert apply_operations([{"op": "add", "item": "x"}]) == _zero()  # item이 dict 아님
    assert apply_operations([{"op": "update", "id": "abc"}]) == _zero()  # id 변환 불가


def test_missing_id_not_counted():
    counts = apply_operations([
        {"op": "complete", "id": 999},
        {"op": "delete", "id": 999},
        {"op": "update", "id": 999, "changes": {"title": "x"}},
    ])
    assert counts == _zero()  # 실제 변경 0 → 카운트 0


def test_real_change_counted():
    apply_operations([{"op": "add", "item": {"title": "장보기"}}])
    iid = store.all_items()[0].id
    assert apply_operations([{"op": "complete", "id": iid}])["completed"] == 1
    assert apply_operations([{"op": "reopen", "id": iid}])["reopened"] == 1
    assert apply_operations([{"op": "delete", "id": iid}])["deleted"] == 1


def test_bool_int_string_parsing():
    it = coerce_item({"title": "x", "needs_review": "false", "estimate_min": "약 30분"})
    assert it.needs_review is False        # "false"가 참으로 처리되던 버그
    assert it.estimate_min == 30           # 문자열 숫자 파싱
    assert coerce_item({"title": "y", "needs_review": "true"}).needs_review is True


def test_update_field_validation():
    changes = coerce_changes({"time": "99:99", "date": "내일", "priority": "high", "bogus": 1})
    assert "time" not in changes and "date" not in changes  # 잘못된 값 제거
    assert changes == {"priority": "high"}                  # 유효한 것만


def test_dashboard_counts_review_and_conflict():
    apply_operations([
        {"op": "add", "item": {"title": "모호", "needs_review": True, "review_reason": "날짜 미정"}},
        {"op": "add", "item": {"title": "면접", "kind": "event", "date": _d(1), "time": "15:00"}},
        {"op": "add", "item": {"title": "병원", "kind": "event", "date": _d(1), "time": "15:00"}},
    ])
    summary = views.build_dashboard(store.all_items())["sections"][0]["lines"][1]
    assert "확인 필요 2" in summary  # needs_review 1 + 충돌 1


def test_api_view_and_toggle(client):
    apply_operations([{"op": "add", "item": {"title": "운동"}}])
    iid = store.all_items()[0].id
    v = client.get("/api/view").json()["view"]
    assert "운동" in [i["title"] for i in _all_items(v)]
    client.post(f"/api/items/{iid}/toggle")
    assert store.get(iid).status == "done"
    client.post(f"/api/items/{iid}/toggle")
    assert store.get(iid).status == "open"
    assert client.post("/api/items/99999/toggle").status_code == 404


def _zero():
    return {"added": 0, "completed": 0, "reopened": 0, "updated": 0, "deleted": 0}


@pytest.fixture
def client():
    return TestClient(app)
