"""결정론적 보기 렌더러 — 프롬프트 §5/§7/§16/§20~22.

모든 보기는 LLM 없이 상태에서 직접 생성한다(즉시·무료·일관).
빠른 메뉴는 UI가 고정 출력하므로 텍스트에는 넣지 않는다.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict

from . import timeutil
from .models import PRIORITY_EMOJI, Item


def _top() -> str:
    return f"[오늘은 {timeutil.header()}입니다]"


def _prio_prefix(item: Item) -> str:
    # §10: 부드럽게 — 매우 높음/높음만 표시
    if item.priority in ("very_high", "high"):
        return PRIORITY_EMOJI[item.priority] + " "
    return ""


def _line(item: Item, *, with_date: bool = False) -> str:
    parts = []
    if item.status == "done":
        parts.append("✅")
    elif item.kind == "event" and item.time:
        parts.append(item.time)
    else:
        parts.append("□")
    prefix = _prio_prefix(item)
    title = f"{prefix}{item.title}"
    if with_date and item.date:
        title = f"{item.date} {title}"
    parts.append(title)
    if item.recurrence:
        parts.append(f"({item.recurrence})")
    if item.location:
        parts.append(f"@{item.location}")
    return "  " + " ".join(parts)


# ---------------------------------------------------------------- 충돌(§16)

def detect_conflicts(items: list[Item]) -> list[tuple[str, str, list[Item]]]:
    """같은 날짜+시간에 일정(event) 2건 이상 → (date, time, [items])."""
    groups: dict[tuple[str, str], list[Item]] = defaultdict(list)
    for it in items:
        if it.status == "done" or it.kind != "event" or not it.date or not it.time:
            continue
        groups[(it.date, it.time)].append(it)
    return [(d, t, g) for (d, t), g in sorted(groups.items()) if len(g) >= 2]


def _conflict_block(items: list[Item]) -> str:
    conflicts = detect_conflicts(items)
    if not conflicts:
        return ""
    lines = ["", "⚠️ 일정 충돌 가능성이 있어요."]
    for d, t, g in conflicts:
        names = " / ".join(i.title for i in g)
        lines.append(f"  {d} {t} — {names}")
    return "\n".join(lines)


# ---------------------------------------------------------------- 캘린더(기본)

def _sort_in_day(items: list[Item]) -> list[Item]:
    # 시간 있는 일정 먼저(시간순), 그다음 시간 미정/할 일(우선순위순)
    timed = sorted((i for i in items if i.time), key=lambda i: i.time)
    untimed = sorted((i for i in items if not i.time), key=lambda i: i.rank)
    return timed + untimed


def render_calendar(items: list[Item], scope: str = "all") -> str:
    ref = timeutil.today()
    open_items = [i for i in items if i.status != "done"]
    dated = [i for i in open_items if i.date_obj and timeutil.in_scope(i.date_obj, scope, ref)]
    undated = [i for i in open_items if not i.date_obj]
    recurring = [i for i in open_items if i.recurrence and not i.date_obj]

    out = [_top(), ""]
    scope_label = {"today": "오늘", "week": "이번 주", "month": "이번 달", "all": "전체"}[scope]
    out.append(f"📆 {scope_label} 정리")

    by_date: dict[dt.date, list[Item]] = defaultdict(list)
    for it in dated:
        by_date[it.date_obj].append(it)

    if not by_date and not undated:
        out.append("\n아직 정리된 항목이 없어요. 자유롭게 적어주시면 캘린더로 정리해드릴게요.")
        return "\n".join(out)

    for d in sorted(by_date):
        tag = " (오늘)" if d == ref else (" (내일)" if d == ref + dt.timedelta(days=1) else "")
        out.append(f"\n📅 {timeutil.header(d)}{tag}")
        day_items = _sort_in_day(by_date[d])
        timed = [i for i in day_items if i.time]
        untimed = [i for i in day_items if not i.time]
        for it in timed:
            out.append(_line(it))
        if untimed:
            out.append("  🕒 시간 미정")
            for it in untimed:
                out.append(_line(it))

    # 날짜 미정 할 일은 잃어버리지 않게 항상 노출(§25)
    plain_undated = [i for i in undated if not i.recurrence]
    if plain_undated:
        out.append("\n📝 날짜 미정")
        for it in sorted(plain_undated, key=lambda i: i.rank):
            out.append(_line(it))

    if recurring:
        out.append("\n🔁 반복 일정")
        for it in recurring:
            out.append(_line(it))

    block = _conflict_block(open_items)
    if block:
        out.append(block)
    return "\n".join(out)


# ---------------------------------------------------------------- 분류(§9)

def render_categories(items: list[Item]) -> str:
    open_items = [i for i in items if i.status != "done"]
    by_cat: dict[str, list[Item]] = defaultdict(list)
    for it in open_items:
        by_cat[it.category or "기타"].append(it)
    out = [_top(), "", "🗂 분류별 정리"]
    if not open_items:
        out.append("\n정리된 항목이 없어요.")
        return "\n".join(out)
    for cat in sorted(by_cat, key=lambda c: (-len(by_cat[c]), c)):
        out.append(f"\n[{cat}]")
        for it in sorted(by_cat[cat], key=lambda i: (i.rank, i.date or "9999")):
            out.append(_line(it, with_date=bool(it.date)))
    return "\n".join(out)


# ---------------------------------------------------------------- 프로젝트(§12)

def render_projects(items: list[Item]) -> str:
    open_items = [i for i in items if i.status != "done"]
    by_proj: dict[str, list[Item]] = defaultdict(list)
    for it in open_items:
        by_proj[it.project or "(프로젝트 미지정)"].append(it)
    out = [_top(), "", "📁 프로젝트별 정리"]
    named = {k: v for k, v in by_proj.items() if k != "(프로젝트 미지정)"}
    if not named:
        out.append("\n아직 프로젝트로 묶인 항목이 없어요. 큰 작업은 단계로 나눠 정리할 수 있어요.")
    for proj, group in sorted(named.items()):
        out.append(f"\n📂 {proj}")
        for it in sorted(group, key=lambda i: (i.date or "9999", i.rank)):
            out.append(_line(it, with_date=bool(it.date)))
    if by_proj.get("(프로젝트 미지정)"):
        out.append("\n그 외")
        for it in by_proj["(프로젝트 미지정)"]:
            out.append(_line(it, with_date=bool(it.date)))
    return "\n".join(out)


# ---------------------------------------------------------------- 대시보드(§21,§22)

def render_dashboard(items: list[Item]) -> str:
    ref = timeutil.today()
    open_items = [i for i in items if i.status != "done"]
    done = [i for i in items if i.status == "done"]
    today_n = sum(1 for i in open_items if i.date_obj == ref)
    week_n = sum(1 for i in open_items if timeutil.in_scope(i.date_obj, "week", ref))
    no_date = [i for i in open_items if not i.date_obj]
    review = [i for i in open_items if i.needs_review] or detect_conflicts(open_items)
    soon = _due_soon_items(open_items, ref)

    out = [_top(), "", "📊 현황 대시보드"]
    out.append(f"\n• 오늘 {today_n}건 · 이번 주 {week_n}건 · 날짜 미정 {len(no_date)}건")
    out.append(f"• 열린 항목 {len(open_items)}건 · 완료 {len(done)}건 · 확인 필요 {len(review)}건")
    if soon:
        out.append("\n⏰ 마감 임박")
        for it in soon:
            out.append(_line(it, with_date=True))
    important = [i for i in open_items if i.priority in ("very_high", "high")]
    if important:
        out.append("\n⭐ 중요")
        for it in sorted(important, key=lambda i: i.rank):
            out.append(_line(it, with_date=bool(it.date)))
    return "\n".join(out)


# ---------------------------------------------------------------- 확인 보기

def _due_soon_items(items: list[Item], ref: dt.date, within: int = 2) -> list[Item]:
    res = []
    for it in items:
        du = timeutil.days_until(it.date_obj, ref)
        if du is not None and 0 <= du <= within:
            res.append(it)
    return sorted(res, key=lambda i: (i.date or "", i.time or "99:99"))


def render_important(items: list[Item]) -> str:
    open_items = [i for i in items if i.status != "done"]
    imp = [i for i in open_items if i.priority in ("very_high", "high")]
    out = [_top(), "", "⭐ 중요 항목"]
    if not imp:
        out.append("\n중요로 강조된 항목이 없어요.")
    for it in sorted(imp, key=lambda i: (i.rank, i.date or "9999")):
        out.append(_line(it, with_date=bool(it.date)))
    return "\n".join(out)


def render_due_soon(items: list[Item]) -> str:
    ref = timeutil.today()
    soon = _due_soon_items([i for i in items if i.status != "done"], ref, within=3)
    out = [_top(), "", "⏰ 마감 임박 (오늘~3일)"]
    if not soon:
        out.append("\n임박한 마감이 없어요.")
    for it in soon:
        du = timeutil.days_until(it.date_obj, ref)
        tag = {0: "오늘", 1: "내일"}.get(du, f"{du}일 뒤")
        out.append(_line(it, with_date=True) + f"  — {tag}")
    return "\n".join(out)


def render_no_date(items: list[Item]) -> str:
    nd = [i for i in items if i.status != "done" and not i.date_obj]
    out = [_top(), "", "📝 날짜 미정"]
    if not nd:
        out.append("\n날짜 미정 항목이 없어요.")
    for it in sorted(nd, key=lambda i: i.rank):
        out.append(_line(it))
    return "\n".join(out)


def render_review(items: list[Item]) -> str:
    open_items = [i for i in items if i.status != "done"]
    review = [i for i in open_items if i.needs_review]
    out = [_top(), "", "⚠️ 확인 필요"]
    if not review and not detect_conflicts(open_items):
        out.append("\n확인이 필요한 항목이 없어요.")
    for it in review:
        reason = f" — {it.review_reason}" if it.review_reason else ""
        out.append(_line(it, with_date=bool(it.date)) + reason)
    block = _conflict_block(open_items)
    if block:
        out.append(block)
    return "\n".join(out)


def render_date_refresh() -> str:
    n = timeutil.now()
    return (
        f"[오늘은 {timeutil.header()}입니다]\n\n"
        f"🔄 기준 날짜를 갱신했어요.\n"
        f"• 현재(KST): {timeutil.header()} {n:%H:%M}\n"
        f"• 이제 오늘/내일/이번 주/다음 주/이번 달 계산은 이 날짜를 기준으로 합니다."
    )
