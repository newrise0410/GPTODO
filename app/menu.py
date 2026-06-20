"""빠른 메뉴(§20/§21) 라우팅 — 메뉴 입력은 LLM 없이 즉시 보기 전환."""

from __future__ import annotations

from typing import Any, Callable

from . import store, views

# 라벨(이모지 포함) → 보기. 프런트가 보내는 data-cmd와 정확히 일치.
_SCOPE_VIEWS: dict[str, str] = {
    "☀️ 오늘": "today",
    "📅 이번 주": "week",
    "🗓 이번 달": "month",
    "📂 전체": "all",
    "📆 캘린더": "all",
}

_OTHER_VIEWS: dict[str, Callable[[list], dict]] = {
    "🗂 분류하기": views.build_categories,
    "📁 프로젝트": views.build_projects,
    "📊 대시보드": views.build_dashboard,
    "⭐ 중요": views.build_important,
    "⏰ 마감 임박": views.build_due_soon,
    "📝 날짜 미정": views.build_no_date,
    "⚠️ 확인 필요": views.build_review,
}

# §7 임의 포맷 — 사용자가 자연어로 치는 짧은 형식 전환어(별칭)
_ALIASES: dict[str, Callable[[list], dict]] = {
    "표": views.build_table, "표로": views.build_table, "테이블": views.build_table,
    "체크리스트": views.build_checklist, "체크리스트로": views.build_checklist,
    "요약": views.build_summary, "요약해서": views.build_summary, "요약 보기": views.build_summary,
    "분류": views.build_categories, "분류해서": views.build_categories,
    "분류해줘": views.build_categories, "카테고리별로": views.build_categories,
    "프로젝트별로": views.build_projects, "프로젝트별": views.build_projects,
    "캘린더로": lambda items: views.build_calendar(items, "all"),
    "대시보드로": views.build_dashboard,
}

MENU_LABELS = set(_SCOPE_VIEWS) | set(_OTHER_VIEWS) | {"🔄 날짜 갱신"}


def is_menu(text: str) -> bool:
    t = text.strip()
    return t in MENU_LABELS or t in _ALIASES or t in _ONBOARDING


def render(text: str) -> dict[str, Any]:
    """메뉴 라벨/별칭에 해당하는 구조화된 보기(view dict)를 상태에서 생성."""
    text = text.strip()
    if text == "🔄 날짜 갱신":
        return views.build_date_refresh()
    items = store.all_items()
    if text in _SCOPE_VIEWS:
        return views.build_calendar(items, scope=_SCOPE_VIEWS[text])
    if text in _OTHER_VIEWS:
        return _OTHER_VIEWS[text](items)
    if text in _ALIASES:
        return _ALIASES[text](items)
    if text in _ONBOARDING:
        return views.build_onboarding()
    return views.build_calendar(items, scope="all")


# §23 정보 템플릿 트리거
_ONBOARDING = {"정보 템플릿 요청", "정보 템플릿", "프로필 설정"}
