"""구조화된 보기 빌더 — 프롬프트 §5/§7/§16/§20~22.

문자열이 아니라 섹션/항목 구조(JSON)를 만든다. 그룹 구별·테마는 프런트가 담당.
모든 보기는 LLM 없이 상태에서 직접 생성한다(즉시·무료·일관).
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from typing import Any

from . import timeutil
from .models import Item


def _item_view(it: Item, *, with_date: bool = False, divider: str | None = None,
               note: str | None = None) -> dict[str, Any]:
    return {
        "id": it.id,
        "title": it.title,
        "time": it.time,
        "kind": it.kind,
        "done": it.status == "done",
        "priority": it.priority,
        "date": it.date if with_date else None,
        "recurrence": it.recurrence,
        "location": it.location,
        "divider": divider,        # 카드 안에서 '시간 미정' 같은 소제목 구분
        "note": note,
    }


def _view(title: str, sections: list[dict], *, note: str | None = None,
          questions: list[str] | None = None) -> dict[str, Any]:
    return {
        "date": timeutil.header(),
        "title": title,
        "note": note,
        "sections": sections,
        "questions": questions or [],
    }


def _section(label: str, tone: str, *, items: list[dict] | None = None,
             lines: list[str] | None = None) -> dict[str, Any]:
    return {"label": label, "tone": tone, "items": items or [], "lines": lines or []}


# ---------------------------------------------------------------- 충돌(§16)

def detect_conflicts(items: list[Item]) -> list[tuple[str, str, list[Item]]]:
    groups: dict[tuple[str, str], list[Item]] = defaultdict(list)
    for it in items:
        if it.status == "done" or it.kind != "event" or not it.date or not it.time:
            continue
        groups[(it.date, it.time)].append(it)
    return [(d, t, g) for (d, t), g in sorted(groups.items()) if len(g) >= 2]


def _conflict_section(items: list[Item]) -> dict | None:
    conflicts = detect_conflicts(items)
    if not conflicts:
        return None
    rows = []
    for d, t, g in conflicts:
        for it in g:
            rows.append(_item_view(it, with_date=True, note=f"{t} 겹침"))
    return _section("충돌 가능성", "conflict", items=rows)


# ---------------------------------------------------------------- 캘린더(기본)

def _sort_day(items: list[Item]) -> tuple[list[Item], list[Item]]:
    timed = sorted((i for i in items if i.time), key=lambda i: i.time)
    untimed = sorted((i for i in items if not i.time), key=lambda i: i.rank)
    return timed, untimed


def build_calendar(items: list[Item], scope: str = "all") -> dict[str, Any]:
    ref = timeutil.today()
    open_items = [i for i in items if i.status != "done"]
    dated = [i for i in open_items if i.date_obj and timeutil.in_scope(i.date_obj, scope, ref)]
    undated = [i for i in open_items if not i.date_obj and not i.recurrence]
    recurring = [i for i in open_items if i.recurrence and not i.date_obj]

    scope_label = {"today": "오늘", "week": "이번 주", "month": "이번 달", "all": "전체"}[scope]
    sections: list[dict] = []

    by_date: dict[dt.date, list[Item]] = defaultdict(list)
    for it in dated:
        by_date[it.date_obj].append(it)

    for d in sorted(by_date):
        tag = " · 오늘" if d == ref else (" · 내일" if d == ref + dt.timedelta(days=1) else "")
        tone = "today" if d == ref else "date"
        timed, untimed = _sort_day(by_date[d])
        rows = [_item_view(it) for it in timed]
        for idx, it in enumerate(untimed):
            rows.append(_item_view(it, divider="시간 미정" if idx == 0 and timed else None))
        sections.append(_section(timeutil.header(d) + tag, tone, items=rows))

    if undated:
        rows = [_item_view(it) for it in sorted(undated, key=lambda i: i.rank)]
        sections.append(_section("날짜 미정", "nodate", items=rows))

    if recurring:
        rows = [_item_view(it) for it in recurring]
        sections.append(_section("반복 일정", "recurring", items=rows))

    conflict = _conflict_section(open_items)
    if conflict:
        sections.append(conflict)

    if not sections:
        sections.append(_section("", "empty",
                                 lines=["아직 정리된 항목이 없어요. 자유롭게 적어주시면 캘린더로 정리해드릴게요."]))
    return _view(f"{scope_label} 정리", sections)


# ---------------------------------------------------------------- 분류(§9)

def build_categories(items: list[Item]) -> dict[str, Any]:
    open_items = [i for i in items if i.status != "done"]
    by_cat: dict[str, list[Item]] = defaultdict(list)
    for it in open_items:
        by_cat[it.category or "기타"].append(it)
    sections = []
    for cat in sorted(by_cat, key=lambda c: (-len(by_cat[c]), c)):
        rows = [_item_view(it, with_date=True)
                for it in sorted(by_cat[cat], key=lambda i: (i.rank, i.date or "9999"))]
        sections.append(_section(cat, "category", items=rows))
    if not sections:
        sections.append(_section("", "empty", lines=["정리된 항목이 없어요."]))
    return _view("분류별 정리", sections)


# ---------------------------------------------------------------- 프로젝트(§12)

def build_projects(items: list[Item]) -> dict[str, Any]:
    open_items = [i for i in items if i.status != "done"]
    by_proj: dict[str, list[Item]] = defaultdict(list)
    for it in open_items:
        by_proj[it.project or "(미지정)"].append(it)
    sections = []
    named = {k: v for k, v in by_proj.items() if k != "(미지정)"}
    for proj, group in sorted(named.items()):
        rows = [_item_view(it, with_date=True)
                for it in sorted(group, key=lambda i: (i.date or "9999", i.rank))]
        sections.append(_section(proj, "project", items=rows))
    if by_proj.get("(미지정)"):
        rows = [_item_view(it, with_date=True) for it in by_proj["(미지정)"]]
        sections.append(_section("그 외", "plain", items=rows))
    if not named:
        sections.insert(0, _section("", "empty",
                        lines=["아직 프로젝트로 묶인 항목이 없어요. 큰 작업은 단계로 나눌 수 있어요."]))
    return _view("프로젝트별 정리", sections)


# ---------------------------------------------------------------- 대시보드(§21,§22)

def _due_soon_items(items: list[Item], ref: dt.date, within: int = 2) -> list[Item]:
    res = [i for i in items if (du := timeutil.days_until(i.date_obj, ref)) is not None
           and 0 <= du <= within]
    return sorted(res, key=lambda i: (i.date or "", i.time or "99:99"))


def build_dashboard(items: list[Item]) -> dict[str, Any]:
    ref = timeutil.today()
    open_items = [i for i in items if i.status != "done"]
    done = [i for i in items if i.status == "done"]
    today_n = sum(1 for i in open_items if i.date_obj == ref)
    week_n = sum(1 for i in open_items if timeutil.in_scope(i.date_obj, "week", ref))
    no_date = [i for i in open_items if not i.date_obj]
    # 확인 필요 = needs_review 항목 + 충돌 그룹(둘을 따로 집계해 합산)
    review_n = sum(1 for i in open_items if i.needs_review) + len(detect_conflicts(open_items))

    sections = [_section("요약", "summary", lines=[
        f"오늘 {today_n} · 이번 주 {week_n} · 날짜 미정 {len(no_date)}",
        f"열린 항목 {len(open_items)} · 완료 {len(done)} · 확인 필요 {review_n}",
    ])]
    soon = _due_soon_items(open_items, ref)
    if soon:
        sections.append(_section("마감 임박", "due",
                        items=[_item_view(i, with_date=True) for i in soon]))
    important = sorted((i for i in open_items if i.priority in ("very_high", "high")),
                       key=lambda i: i.rank)
    if important:
        sections.append(_section("중요", "important",
                        items=[_item_view(i, with_date=True) for i in important]))
    return _view("현황 대시보드", sections)


# ---------------------------------------------------------------- 확인 보기

def build_important(items: list[Item]) -> dict[str, Any]:
    open_items = [i for i in items if i.status != "done"]
    imp = sorted((i for i in open_items if i.priority in ("very_high", "high")),
                 key=lambda i: (i.rank, i.date or "9999"))
    if not imp:
        return _view("중요 항목", [_section("", "empty", lines=["중요로 강조된 항목이 없어요."])])
    return _view("중요 항목",
                 [_section("중요", "important", items=[_item_view(i, with_date=True) for i in imp])])


def build_due_soon(items: list[Item]) -> dict[str, Any]:
    ref = timeutil.today()
    soon = _due_soon_items([i for i in items if i.status != "done"], ref, within=3)
    if not soon:
        return _view("마감 임박", [_section("", "empty", lines=["임박한 마감이 없어요."])])
    rows = []
    for it in soon:
        du = timeutil.days_until(it.date_obj, ref)
        tag = {0: "오늘", 1: "내일"}.get(du, f"{du}일 뒤")
        rows.append(_item_view(it, with_date=True, note=tag))
    return _view("마감 임박 (오늘~3일)", [_section("임박", "due", items=rows)])


def build_no_date(items: list[Item]) -> dict[str, Any]:
    nd = sorted((i for i in items if i.status != "done" and not i.date_obj),
                key=lambda i: i.rank)
    if not nd:
        return _view("날짜 미정", [_section("", "empty", lines=["날짜 미정 항목이 없어요."])])
    return _view("날짜 미정",
                 [_section("날짜 미정", "nodate", items=[_item_view(i) for i in nd])])


def build_review(items: list[Item]) -> dict[str, Any]:
    open_items = [i for i in items if i.status != "done"]
    review = [i for i in open_items if i.needs_review]
    sections = []
    if review:
        rows = [_item_view(it, with_date=True, note=it.review_reason) for it in review]
        sections.append(_section("확인 필요", "review", items=rows))
    conflict = _conflict_section(open_items)
    if conflict:
        sections.append(conflict)
    if not sections:
        sections.append(_section("", "empty", lines=["확인이 필요한 항목이 없어요."]))
    return _view("확인 필요", sections)


def build_date_refresh() -> dict[str, Any]:
    n = timeutil.now()
    return _view("날짜 갱신", [_section("기준 날짜", "summary", lines=[
        f"현재(KST) {timeutil.header()} {n:%H:%M}",
        "이제 오늘/내일/이번 주/다음 주/이번 달 계산은 이 날짜를 기준으로 합니다.",
    ])])
