"""LLM 없이 결정론적 코어 검증 — 상태/연산/구조화 보기/충돌/메뉴."""

import datetime as dt
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import dateresolve, icalfeed, menu, profile, recurrence, store, timeutil, views
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


# ───────── Codex 리뷰 2차 회귀 ─────────

def test_time_normalized_and_no_conflict_crash():
    # HH:MM:SS 입력도 HH:MM으로 정규화되어 충돌 감지가 깨지지 않음
    assert coerce_item({"title": "x", "time": "09:30:00"}).time == "09:30"
    apply_operations([
        {"op": "add", "item": {"title": "A", "kind": "event", "date": _d(0), "time": "10:00:00"}},
        {"op": "add", "item": {"title": "B", "kind": "event", "date": _d(0), "time": "11:00:00"}},
    ])
    # 10:00~11:00, 11:00~12:00 → 경계 비충돌
    assert views.detect_conflicts(store.all_items()) == []


def test_estimate_unit_parsing():
    assert coerce_item({"title": "x", "estimate_min": "2시간"}).estimate_min == 120
    assert coerce_item({"title": "x", "estimate_min": "1시간 30분"}).estimate_min == 90
    assert coerce_item({"title": "x", "estimate_min": -5}).estimate_min is None  # 음수 거부
    assert coerce_changes({"estimate_min": None}) == {"estimate_min": None}      # 지우기 허용


def test_sort_order_persisted_and_ordered():
    apply_operations([{"op": "add", "item": {"title": "P", "project": "X"}}])
    pid = store.all_items()[0].id
    apply_operations([
        {"op": "add", "item": {"title": "둘째", "project": "X", "parent_id": pid, "sort_order": 2}},
        {"op": "add", "item": {"title": "첫째", "project": "X", "parent_id": pid, "sort_order": 1}},
    ])
    titles = [i["title"] for i in _all_items(views.build_projects(store.all_items()))]
    assert titles == ["P", "첫째", "둘째"]  # sort_order 순서 보존


def test_recurrence_no_month_token_confusion():
    import datetime as _dt
    item = Item(title="x", kind="event", recurrence="매년 6월 1일")
    occ = recurrence.occurrences(item, _dt.date(2026, 1, 1), _dt.date(2026, 12, 31))
    assert {o.date for o in occ} == {"2026-06-01"}  # '6월'의 월이 월요일로 오인되지 않음


def test_recurrence_and_date_no_duplicate():
    # date와 recurrence가 둘 다 있어도 한 날짜에 중복 표시되지 않음
    apply_operations([{"op": "add", "item": {
        "title": "회의", "kind": "event", "date": _d(0), "time": "09:00",
        "recurrence": "매일"}}])
    week = views.build_calendar(store.all_items(), "week")
    for sec in week["sections"]:
        assert sum(1 for i in sec["items"] if i["title"] == "회의") <= 1


def test_parse_rejects_bad_types():
    out = _parse('정리했어요\n===JSON===\n{"operations": "nope", "questions": "hi"}')
    assert out["operations"] == [] and out["questions"] == []


def test_parse_normalizes_local_model_artifacts():
    # gemma의 SentencePiece 공백 '▁'와 ``` 코드펜스가 섞여도 파싱 성공
    raw = ("안녕하세요! 정리할게요.\n```\n===JSON===\n{\n▁▁\"operations\": "
           "[{\"op\": \"add\", \"item\": {\"title\": \"면접\"}}],\n▁▁\"questions\": []\n}\n```")
    out = _parse(raw)
    assert out["operations"] == [{"op": "add", "item": {"title": "면접"}}]
    assert "```" not in out["reply"] and "▁" not in out["reply"]


def test_decomposition_intra_batch_refs():
    # 같은 배치에서 상위+하위를 ref/parent_ref로 한 번에 분해(§12)
    apply_operations([
        {"op": "add", "item": {"title": "면접 준비", "project": "면접", "ref": "p"}},
        {"op": "add", "item": {"title": "기업 조사", "project": "면접",
                               "parent_ref": "p", "sort_order": 1}},
        {"op": "add", "item": {"title": "예상 질문", "project": "면접",
                               "parent_ref": "p", "sort_order": 2}},
    ])
    by_title = {i.title: i for i in store.all_items()}
    pid = by_title["면접 준비"].id
    assert by_title["기업 조사"].parent_id == pid
    assert by_title["예상 질문"].parent_id == pid
    depths = {r["title"]: r["depth"] for r in _all_items(views.build_projects(store.all_items()))}
    assert depths["기업 조사"] == 1 and depths["예상 질문"] == 1


def test_dashboard_counts_recurrence_occurrences():
    apply_operations([{"op": "add", "item": {
        "title": "스탠드업", "kind": "event", "time": "09:00", "recurrence": "매일"}}])
    summary = views.build_dashboard(store.all_items())["sections"][0]["lines"][0]
    assert "오늘 1" in summary       # 반복 항목이 오늘 1건으로 집계
    assert "이번 주 7" in summary    # 이번 주 7건
    assert "날짜 미정 0" in summary  # 반복은 '날짜 미정'에 포함되지 않음


def test_message_transcript_persistence():
    # 사용자/어시스턴트 턴 저장 후 그대로 복원, view 스냅샷 포함
    store.add_message("user", "내일 회의")
    store.add_message("assistant", "정리했어요", view={"title": "전체 정리", "sections": []})
    msgs = store.all_messages()
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[0]["content"] == "내일 회의" and msgs[0]["view"] is None
    assert msgs[1]["view"]["title"] == "전체 정리"
    store.clear_messages()
    assert store.all_messages() == []


def test_messages_endpoints(client):
    store.add_message("user", "테스트")
    r = client.get("/api/messages")
    assert r.status_code == 200 and r.json()["messages"][0]["content"] == "테스트"
    assert client.post("/api/messages/clear").status_code == 200
    assert client.get("/api/messages").json()["messages"] == []


def test_dateresolver():
    import datetime as _dt
    sat = _dt.date(2026, 6, 20)  # 토요일
    R = lambda e: dateresolve.resolve(e, sat)  # noqa: E731
    assert R("오늘") == _dt.date(2026, 6, 20)
    assert R("내일") == _dt.date(2026, 6, 21)
    assert R("모레") == _dt.date(2026, 6, 22)
    assert R("금요일") == _dt.date(2026, 6, 26)        # 다가오는 금요일(모델들이 틀리던 케이스)
    assert R("다음 주 화요일") == _dt.date(2026, 6, 23)
    assert R("이번 달 말") == _dt.date(2026, 6, 30)
    assert R("다음 달 초") == _dt.date(2026, 7, 1)
    assert R("3일 후") == _dt.date(2026, 6, 23)
    assert R("6월 25일") == _dt.date(2026, 6, 25)
    assert R("횡설수설") is None                        # 못 풀면 None


def test_date_expr_overrides_model_date():
    # 코드가 푼 '금요일'(실행일 기준 다가오는 금요일)은 실행 날짜와 무관하게 같아야 한다.
    friday = dateresolve.resolve("금요일", timeutil.today()).isoformat()
    # 모델이 금요일을 엉뚱하게 틀려도, deadline_expr로 코드가 교정
    it = coerce_item({"title": "세금신고", "deadline": "1999-01-01", "deadline_expr": "금요일"})
    assert it.deadline == friday
    # date_expr도 동일
    assert coerce_item({"title": "x", "date": "1999-01-01", "date_expr": "금요일"}).date == friday
    # expr가 없으면 모델 값 유지
    assert coerce_item({"title": "x", "date": "2026-07-01"}).date == "2026-07-01"
    # update 경로
    assert coerce_changes({"date_expr": "내일"})["date"] == (
        timeutil.today() + __import__("datetime").timedelta(days=1)).isoformat()


def test_clarification_answer_merges_not_duplicates():
    # 1턴: 시간 미정으로 면접 등록(확인 필요)
    apply_operations([{"op": "add", "item": {
        "title": "면접", "kind": "event", "date": _d(1),
        "needs_review": True, "review_reason": "시간 미정"}}])
    # 2턴: 시간을 답하면 LLM이 (잘못) 다시 add 해도 → 중복 대신 기존 항목에 병합
    apply_operations([{"op": "add", "item": {
        "title": "면접", "kind": "event", "date": _d(1), "time": "15:00"}}])
    items = [i for i in store.all_items() if i.title == "면접"]
    assert len(items) == 1                      # 중복 안 생김
    assert items[0].time == "15:00"             # 시간 채워짐
    assert items[0].needs_review is False       # 모호함 해소


def test_merge_skips_exact_duplicate():
    apply_operations([{"op": "add", "item": {"title": "운동", "kind": "todo"}}])
    apply_operations([{"op": "add", "item": {"title": "운동", "kind": "todo"}}])
    assert len([i for i in store.all_items() if i.title == "운동"]) == 1


def test_merge_keeps_different_dates_separate():
    apply_operations([{"op": "add", "item": {"title": "회의", "kind": "event", "date": _d(0)}}])
    apply_operations([{"op": "add", "item": {"title": "회의", "kind": "event", "date": _d(3)}}])
    assert len([i for i in store.all_items() if i.title == "회의"]) == 2  # 날짜 다르면 별개


def test_done_items_shown_for_reopen():
    apply_operations([{"op": "add", "item": {"title": "운동", "kind": "todo"}}])
    iid = store.all_items()[0].id
    apply_operations([{"op": "complete", "id": iid}])
    v = views.build_calendar(store.all_items(), "all")
    assert "done" in _tones(v)  # 완료 섹션이 보여야 되살릴 수 있음
    done_titles = [i["title"] for s in v["sections"] if s["tone"] == "done" for i in s["items"]]
    assert "운동" in done_titles


def test_delete_and_update_endpoints(client):
    apply_operations([{"op": "add", "item": {"title": "초안", "kind": "todo"}}])
    iid = store.all_items()[0].id
    # 수정
    r = client.post(f"/api/items/{iid}/update", json={"changes": {"title": "최종본"}})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert store.get(iid).title == "최종본"
    # 삭제
    assert client.post(f"/api/items/{iid}/delete").status_code == 200
    assert store.get(iid) is None
    assert client.post(f"/api/items/{iid}/delete").status_code == 404  # 이미 없음


def test_reopen_via_toggle(client):
    apply_operations([{"op": "add", "item": {"title": "장보기"}}])
    iid = store.all_items()[0].id
    apply_operations([{"op": "complete", "id": iid}])
    assert store.get(iid).status == "done"
    client.post(f"/api/items/{iid}/toggle")            # 되살리기
    assert store.get(iid).status == "open"


def test_ical_feed_timed_and_allday():
    apply_operations([
        {"op": "add", "item": {"title": "면접", "kind": "event", "date": _d(1),
                               "time": "15:00", "estimate_min": 90}},
        {"op": "add", "item": {"title": "세금신고", "kind": "todo", "deadline": _d(2)}},
        {"op": "add", "item": {"title": "그냥 할일", "kind": "todo"}},  # 캘린더 대상 아님
    ])
    ics = icalfeed.build(store.all_items())
    assert "BEGIN:VCALENDAR" in ics and "END:VCALENDAR" in ics
    assert ics.count("BEGIN:VEVENT") == 2          # 면접 + 세금신고(마감)만
    assert "SUMMARY:면접" in ics
    assert "SUMMARY:[마감] 세금신고" in ics
    assert "그냥 할일" not in ics                   # 날짜·마감 없는 할일 제외
    assert "DTSTART:" in ics and "DTSTART;VALUE=DATE:" in ics  # 시간있음/종일 둘 다


def test_ical_feed_recurrence_expanded():
    apply_operations([{"op": "add", "item": {
        "title": "스탠드업", "kind": "event", "time": "09:00", "recurrence": "매일"}}])
    ics = icalfeed.build(store.all_items())
    assert ics.count("BEGIN:VEVENT") > 30          # 90일 구간으로 펼쳐짐


def test_cal_info_in_item_view():
    apply_operations([{"op": "add", "item": {
        "title": "회의", "kind": "event", "date": _d(1), "time": "10:00"}}])
    it = _all_items(views.build_calendar(store.all_items(), "all"))[0]
    assert it["cal"] and it["cal"]["gcal"].startswith("https://calendar.google.com")
    assert it["cal"]["ics"].endswith("/ics")


def test_calendar_blank_surfaced_in_review():
    # 날짜 없는 일정 → '캘린더 정보 필요'로 질문 대상
    apply_operations([{"op": "add", "item": {"title": "면접", "kind": "event"}}])
    v = views.build_review(store.all_items())
    labels = [s["label"] for s in v["sections"]]
    assert "캘린더 정보 필요" in labels


def test_feed_and_ics_endpoints(client):
    apply_operations([{"op": "add", "item": {
        "title": "치과", "kind": "event", "date": _d(1), "time": "14:00"}}])
    iid = store.all_items()[0].id
    r = client.get("/calendar.ics")
    assert r.status_code == 200 and "text/calendar" in r.headers["content-type"]
    assert "SUMMARY:치과" in r.text
    r2 = client.get(f"/api/items/{iid}/ics")
    assert r2.status_code == 200 and "BEGIN:VEVENT" in r2.text
    # 캘린더 대상 아닌 항목은 404
    apply_operations([{"op": "add", "item": {"title": "메모", "kind": "todo"}}])
    mid = [i for i in store.all_items() if i.title == "메모"][0].id
    assert client.get(f"/api/items/{mid}/ics").status_code == 404


class FakeGCal:
    """gcal.GoogleCalendar 대역 — 외부 호출 없이 sync 로직 검증."""
    def __init__(self, changes=None):
        self.events = {}
        self._n = 0
        self._changes = changes or []
        self.saved_token = None

    def insert_event(self, body):
        self._n += 1
        eid = f"g{self._n}"
        self.events[eid] = body
        return eid

    def update_event(self, eid, body):
        if eid not in self.events:
            raise KeyError(eid)
        self.events[eid] = body

    def delete_event(self, eid):
        self.events.pop(eid, None)

    def list_changes(self):
        return self._changes, "synctok-1"

    def save_sync_token(self, token):
        self.saved_token = token


def test_sync_push_insert_update_delete():
    from app import sync
    apply_operations([{"op": "add", "item": {
        "title": "면접", "kind": "event", "date": _d(1), "time": "15:00"}}])
    iid = store.all_items()[0].id
    fake = FakeGCal()
    c1 = sync.push(fake)
    assert c1["pushed"] == 1 and len(fake.events) == 1
    assert store.get(iid).google_event_id is not None      # 매핑 저장됨
    # 변경 없으면 재 push 스킵(churn 방지)
    assert sync.push(fake)["pushed"] == 0 and len(fake.events) == 1
    # 내용 바꾸면 다시 push(update)
    apply_operations([{"op": "update", "id": iid, "changes": {"time": "16:00"}}])
    assert sync.push(fake)["pushed"] == 1 and len(fake.events) == 1
    # 완료 → 원격에서 제거
    apply_operations([{"op": "complete", "id": iid}])
    assert sync.push(fake)["deleted"] == 1 and len(fake.events) == 0
    assert store.get(iid).google_event_id is None


def test_sync_push_delete_tombstone():
    from app import sync
    apply_operations([{"op": "add", "item": {
        "title": "회의", "kind": "event", "date": _d(1), "time": "10:00"}}])
    iid = store.all_items()[0].id
    fake = FakeGCal()
    sync.push(fake)                                         # 매핑 생성
    eid = store.get(iid).google_event_id
    apply_operations([{"op": "delete", "id": iid}])         # 삭제 → tombstone
    assert eid in store.all_tombstones()
    assert sync.push(fake)["deleted"] == 1 and eid not in fake.events
    assert store.all_tombstones() == []


def test_sync_pull_create_update_cancel():
    from app import sync
    # 1) 구글에서 직접 만든 일정 → 로컬 생성
    fake = FakeGCal(changes=[{"id": "ext1", "status": "confirmed", "summary": "외부미팅",
                              "start": {"date": _d(2)}}])
    assert sync.pull(fake)["created"] == 1
    created = [i for i in store.all_items() if i.title == "외부미팅"][0]
    assert created.google_event_id == "ext1" and created.date == _d(2)
    assert fake.saved_token == "synctok-1"
    # 2) 같은 이벤트 취소 → 로컬 삭제
    fake2 = FakeGCal(changes=[{"id": "ext1", "status": "cancelled"}])
    assert sync.pull(fake2)["removed"] == 1
    assert not [i for i in store.all_items() if i.title == "외부미팅"]


def test_sync_pull_respects_tombstone():
    from app import sync
    apply_operations([{"op": "add", "item": {
        "title": "면접", "kind": "event", "date": _d(1), "time": "15:00"}}])
    iid = store.all_items()[0].id
    sync.push(FakeGCal())
    eid = store.get(iid).google_event_id
    apply_operations([{"op": "delete", "id": iid}])         # 로컬 삭제 → tombstone
    # 원격에 같은 이벤트가 (수정됨 상태로) 들어와도 부활하면 안 됨
    fake = FakeGCal(changes=[{"id": eid, "status": "confirmed", "summary": "면접",
                              "start": {"date": _d(1)}}])
    assert sync.pull(fake)["created"] == 0
    assert not [i for i in store.all_items() if i.title == "면접"]


def test_sync_pull_updates_deadline_field():
    from app import sync
    apply_operations([{"op": "add", "item": {
        "title": "세금신고", "kind": "todo", "deadline": _d(2)}}])
    iid = store.all_items()[0].id
    sync.push(FakeGCal())
    eid = store.get(iid).google_event_id
    # 구글에서 마감일을 옮김 → deadline이 갱신되어야(다음 push에서 되돌지 않게)
    fake = FakeGCal(changes=[{"id": eid, "status": "confirmed", "summary": "[마감] 세금신고",
                              "start": {"date": _d(5)},
                              "extendedProperties": {"private": {
                                  "gptodo_id": str(iid), "gptodo_field": "deadline"}}}])
    sync.pull(fake)
    it = store.get(iid)
    assert it.deadline == _d(5) and it.date is None


def test_merge_keeps_different_deadlines_separate():
    apply_operations([{"op": "add", "item": {"title": "보고서", "kind": "todo", "deadline": _d(1)}}])
    apply_operations([{"op": "add", "item": {"title": "보고서", "kind": "todo", "deadline": _d(5)}}])
    assert len([i for i in store.all_items() if i.title == "보고서"]) == 2


def test_dateresolver_more():
    import datetime as _dt
    sat = _dt.date(2026, 6, 20)
    R = lambda e: dateresolve.resolve(e, sat)  # noqa: E731
    assert R("2026년 6월 25일") == _dt.date(2026, 6, 25)   # 연도 지정
    assert R("2주 후 화요일") == _dt.date(2026, 6, 30)      # N주 후 + 요일
    assert R("월말") == _dt.date(2026, 6, 30)              # 월말 = 이번 달 말
    assert R("월요일") != _dt.date(2026, 6, 30)            # '월'요일이 월말로 오해석되지 않음


def test_ical_escape_and_recurrence_uid():
    apply_operations([
        {"op": "add", "item": {"title": "A, B; C", "kind": "event", "date": _d(1), "time": "10:00"}},
        {"op": "add", "item": {"title": "스탠드업", "kind": "event", "time": "09:00", "recurrence": "매일"}},
    ])
    ics = icalfeed.build(store.all_items())
    assert "SUMMARY:A\\, B\\; C" in ics                     # 쉼표/세미콜론 이스케이프
    rid = [i.id for i in store.all_items() if i.title == "스탠드업"][0]
    assert f"-{rid}-" in ics                                # 반복 UID에 원본 id 포함


@pytest.fixture
def client():
    return TestClient(app)
