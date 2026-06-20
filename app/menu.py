"""빠른 메뉴(§20/§21) 라우팅 — 메뉴 입력은 LLM 없이 즉시 보기 전환."""

from __future__ import annotations

from typing import Callable

from . import store, views

# 라벨(이모지 포함) → 렌더 함수. 프런트가 보내는 버튼 텍스트와 정확히 일치.
_SCOPE_VIEWS: dict[str, str] = {
    "☀️ 오늘": "today",
    "📅 이번 주": "week",
    "🗓 이번 달": "month",
    "📂 전체": "all",
    "📆 캘린더": "all",
}

_OTHER_VIEWS: dict[str, Callable[[list], str]] = {
    "🗂 분류하기": views.render_categories,
    "📁 프로젝트": views.render_projects,
    "📊 대시보드": views.render_dashboard,
    "⭐ 중요": views.render_important,
    "⏰ 마감 임박": views.render_due_soon,
    "📝 날짜 미정": views.render_no_date,
    "⚠️ 확인 필요": views.render_review,
}

MENU_LABELS = set(_SCOPE_VIEWS) | set(_OTHER_VIEWS) | {"🔄 날짜 갱신"}


def is_menu(text: str) -> bool:
    return text.strip() in MENU_LABELS


def render(text: str) -> str:
    """메뉴 라벨에 해당하는 보기를 상태에서 렌더링."""
    text = text.strip()
    if text == "🔄 날짜 갱신":
        return views.render_date_refresh()
    items = store.all_items()
    if text in _SCOPE_VIEWS:
        return views.render_calendar(items, scope=_SCOPE_VIEWS[text])
    if text in _OTHER_VIEWS:
        return _OTHER_VIEWS[text](items)
    return views.render_calendar(items, scope="all")
