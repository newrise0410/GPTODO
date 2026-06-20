"""LLM 없이 결정론적 코어 검증 — 상태/연산/구조화 보기/충돌/메뉴."""

import datetime as dt
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import menu, profile, recurrence, store, timeutil, views
from app.llm.extract import _parse, apply_operations
from app.main import app
from app.models import Item, coerce_changes, coerce_item, fmt_estimate


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


def test_recurrence_weekly_only_correct_weekday():
    # '월요일'의 '요일' 속 '일'이 일요일로 오인되면 안 됨(파서 회귀)
    item = Item(title="회의", kind="event", time="09:00", recurrence="매주 월요일")
    mon, sun = dt.date(2026, 6, 15), dt.date(2026, 6, 21)
    dates = {o.date for o in recurrence.occurrences(item, mon, sun)}
    assert dates == {"2026-06-15"}  # 그 주의 월요일 하나만


def test_recurrence_materialized_in_week_view():
    apply_operations([{"op": "add", "item": {
        "title": "스트레칭", "kind": "event", "time": "07:00", "recurrence": "매일"}}])
    week = views.build_calendar(store.all_items(), "week")
    # 이번 주 7일 모두에 인스턴스가 생기고, 가상 인스턴스라 id=None(토글 불가)
    insts = [i for i in _all_items(week) if i["title"] == "스트레칭"]
    assert len(insts) == 7
    assert all(i["id"] is None for i in insts)
    # 'all' 보기에서는 펼치지 않고 '반복 일정' 규칙으로만 남는다
    all_view = views.build_calendar(store.all_items(), "all")
    assert "recurring" in _tones(all_view)


def test_parse_sentinel_format():
    full = '내일 일정 정리했어요.\n===JSON===\n{"operations": [], "questions": ["시간도 정할까요?"]}'
    out = _parse(full)
    assert out["reply"] == "내일 일정 정리했어요."
    assert out["questions"] == ["시간도 정할까요?"]


def test_parse_fallback_without_sentinel():
    # 센티넬 없이 옛 형식(JSON 안 reply)도 호환
    out = _parse('{"reply": "정리했어요", "operations": []}')
    assert out["reply"] == "정리했어요"


def test_chat_stream_endpoint(client):
    # 메뉴 경로는 LLM 없이 SSE로 즉시 view 이벤트를 보낸다
    apply_operations([{"op": "add", "item": {"title": "운동"}}])
    r = client.post("/api/chat/stream", json={"messages": [{"role": "user", "content": "📂 전체"}]})
    assert r.status_code == 200
    assert 'data:' in r.text and '"type": "view"' in r.text
    assert "운동" in r.text


def _zero():
    return {"added": 0, "completed": 0, "reopened": 0, "updated": 0, "deleted": 0}


# ───────── 신규 스펙 항목 ─────────

def test_estimate_displayed():
    assert fmt_estimate(30) == "~30분"
    assert fmt_estimate(90) == "~1시간 30분"
    assert fmt_estimate(120) == "~2시간"
    apply_operations([{"op": "add", "item": {"title": "보고서", "estimate_min": 90}}])
    it = _all_items(views.build_calendar(store.all_items(), "all"))[0]
    assert it["estimate"] == "~1시간 30분"


def test_memo_idea_section():
    apply_operations([
        {"op": "add", "item": {"title": "책 추천 메모", "kind": "memo"}},
        {"op": "add", "item": {"title": "앱 아이디어", "kind": "idea"}},
    ])
    v = views.build_calendar(store.all_items(), "all")
    assert "memo" in _tones(v)
    assert "nodate" not in _tones(v)  # 메모는 '날짜 미정'으로 새지 않음


def test_deadline_drives_due_soon():
    apply_operations([{"op": "add", "item": {
        "title": "세금 신고", "kind": "todo", "deadline": _d(1)}}])  # 일정 날짜 없음, 마감만 내일
    v = views.build_due_soon(store.all_items())
    assert "세금 신고" in [i["title"] for i in _all_items(v)]
    assert coerce_item({"title": "x", "deadline": _d(2)}).deadline == _d(2)


def test_recurrence_advanced():
    import datetime as _dt
    item = lambda r: Item(title="x", kind="event", recurrence=r)  # noqa: E731
    jun1, jun30 = _dt.date(2026, 6, 1), _dt.date(2026, 6, 30)
    first_mon = {o.date for o in recurrence.occurrences(item("첫째 주 월요일"), jun1, jun30)}
    assert first_mon == {"2026-06-01"}                  # 6/1이 월요일
    last_fri = {o.date for o in recurrence.occurrences(item("마지막 금요일"), jun1, jun30)}
    assert last_fri == {"2026-06-26"}                   # 6월 마지막 금요일
    biweekly = recurrence.occurrences(item("격주 수요일"), jun1, jun30)
    assert biweekly and all(_dt.date.fromisoformat(o.date).weekday() == 2 for o in biweekly)


def test_overlap_conflict_by_duration():
    apply_operations([
        {"op": "add", "item": {"title": "회의", "kind": "event", "date": _d(0),
                               "time": "10:00", "estimate_min": 90}},   # 10:00~11:30
        {"op": "add", "item": {"title": "통화", "kind": "event", "date": _d(0),
                               "time": "11:00", "estimate_min": 30}},   # 11:00~11:30 겹침
    ])
    conflicts = views.detect_conflicts(store.all_items())
    assert len(conflicts) == 1 and len(conflicts[0][1]) == 2


def test_format_views_and_aliases():
    apply_operations([{"op": "add", "item": {"title": "운동", "kind": "todo"}}])
    assert menu.is_menu("표로") and menu.is_menu("체크리스트") and menu.is_menu("요약")
    assert menu.render("표로")["title"] == "표 보기"
    assert menu.render("체크리스트")["title"] == "체크리스트"
    assert menu.render("요약")["title"] == "요약"


def test_project_nesting_depth():
    apply_operations([{"op": "add", "item": {"title": "면접 준비", "project": "면접"}}])
    parent_id = store.all_items()[0].id
    apply_operations([
        {"op": "add", "item": {"title": "기업 조사", "project": "면접",
                               "parent_id": parent_id, "sort_order": 1}},
        {"op": "add", "item": {"title": "예상 질문", "project": "면접",
                               "parent_id": parent_id, "sort_order": 2}},
    ])
    v = views.build_projects(store.all_items())
    rows = _all_items(v)
    depths = {r["title"]: r["depth"] for r in rows}
    assert depths["면접 준비"] == 0
    assert depths["기업 조사"] == 1 and depths["예상 질문"] == 1


def test_onboarding_trigger():
    assert menu.is_menu("정보 템플릿 요청")
    assert menu.render("정보 템플릿 요청")["title"] == "정보 템플릿"


def test_set_profile(tmp_path, monkeypatch):
    monkeypatch.setattr(profile, "PROFILE_PATH", tmp_path / "profile.json")
    apply_operations([{"op": "set_profile", "profile": {
        "name": "홍길동", "role": "개발자", "categories": "업무/건강"}}])
    prof = profile.load()
    assert prof["name"] == "홍길동" and prof["role"] == "개발자"
    assert prof["categories"] == ["업무", "건강"]
    assert "홍길동" in profile.as_context()


def test_migration_adds_columns(tmp_path, monkeypatch):
    import sqlite3
    db = tmp_path / "old.db"
    # 구버전 스키마: deadline/parent_id/sort_order 만 빠진 상태
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE items (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, "
        "kind TEXT NOT NULL DEFAULT 'todo', date TEXT, time TEXT, category TEXT, "
        "priority TEXT, recurrence TEXT, project TEXT, location TEXT, people TEXT, "
        "estimate_min INTEGER, status TEXT NOT NULL DEFAULT 'open', "
        "needs_review INTEGER NOT NULL DEFAULT 0, review_reason TEXT, "
        "created_at TEXT NOT NULL DEFAULT (datetime('now')))")
    con.commit()
    con.close()
    monkeypatch.setattr(store, "DB_PATH", db)
    store.init()  # 마이그레이션 수행
    store.add(Item(title="t", deadline=_d(1), parent_id=None, sort_order=3))
    assert store.all_items()[0].deadline == _d(1)


@pytest.fixture
def client():
    return TestClient(app)
