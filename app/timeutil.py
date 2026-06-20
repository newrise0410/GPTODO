"""KST 기준 날짜 유틸 — 모든 날짜 계산의 단일 출처."""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
_WEEKDAYS_KO = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]


def now() -> dt.datetime:
    return dt.datetime.now(KST)


def today() -> dt.date:
    return now().date()


def weekday_ko(d: dt.date) -> str:
    return _WEEKDAYS_KO[d.weekday()]


def header(d: dt.date | None = None) -> str:
    """'2026년 6월 20일 토요일' 형식."""
    d = d or today()
    return f"{d.year}년 {d.month}월 {d.day}일 {weekday_ko(d)}"


def parse_date(s: str | None) -> dt.date | None:
    if not s:
        return None
    try:
        return dt.date.fromisoformat(s)
    except ValueError:
        return None


def week_bounds(d: dt.date | None = None) -> tuple[dt.date, dt.date]:
    """월요일 시작 ~ 일요일 끝."""
    d = d or today()
    monday = d - dt.timedelta(days=d.weekday())
    return monday, monday + dt.timedelta(days=6)


def in_scope(d: dt.date | None, scope: str, ref: dt.date | None = None) -> bool:
    """scope: today | week | month | all. 날짜 미정(None)은 today/week/month에서 제외."""
    ref = ref or today()
    if scope == "all":
        return True
    if d is None:
        return False
    if scope == "today":
        return d == ref
    if scope == "week":
        lo, hi = week_bounds(ref)
        return lo <= d <= hi
    if scope == "month":
        return d.year == ref.year and d.month == ref.month
    return True


def days_until(d: dt.date | None, ref: dt.date | None = None) -> int | None:
    if d is None:
        return None
    return (d - (ref or today())).days
