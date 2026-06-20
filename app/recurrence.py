"""반복 일정 파서 — 자유 텍스트 recurrence를 날짜 occurrence로 펼친다(§15).

지원: 매일 / 평일마다 / 주말마다 / 매주 <요일들> / 매월 N일.
해석 불가한 규칙(격주·첫째 주·마지막 금요일 등)은 빈 리스트를 반환하고,
호출부(views)가 '반복 일정' 섹션에 규칙 그대로 남겨 잃어버리지 않게 한다.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import replace

from .models import Item

_WEEKDAY_TOKENS = {"월": 0, "화": 1, "수": 2, "목": 3, "금": 4, "토": 5, "일": 6}


def _weekdays_in(text: str) -> set[int]:
    """'매주 월요일', '월/수/금', '화목' 등에서 요일 추출.

    주의: '월요일'의 '요일' 속 '일'(일요일)이나 '매월'의 '월'(월요일)이 오인되지 않게,
    'X요일' 형태를 먼저 잡고 키워드/접미사를 제거한 뒤 압축형을 본다.
    """
    days: set[int] = set()
    for m in re.finditer(r"([월화수목금토일])요일", text):
        days.add(_WEEKDAY_TOKENS[m.group(1)])
    rest = re.sub(r"[월화수목금토일]요일", " ", text)
    for kw in ("매월", "매주", "매일", "마다", "평일", "주말", "매년"):
        rest = rest.replace(kw, " ")
    rest = re.sub(r"\d+\s*일", " ", rest)  # '1일' 같은 날짜의 '일' 제외
    for ch in rest:
        if ch in _WEEKDAY_TOKENS:
            days.add(_WEEKDAY_TOKENS[ch])
    return days


def _matches(text: str, d: dt.date) -> bool:
    if "매일" in text:
        return True
    if "평일" in text:
        return d.weekday() < 5
    if "주말" in text:
        return d.weekday() >= 5
    if "매월" in text:
        m = re.search(r"(\d{1,2})\s*일", text)
        return bool(m) and d.day == int(m.group(1))
    days = _weekdays_in(text)
    if ("매주" in text or "마다" in text) and days:
        return d.weekday() in days
    return False


def is_parseable(text: str | None) -> bool:
    if not text:
        return False
    if any(k in text for k in ("매일", "평일", "주말", "매월")):
        return True
    return bool(_weekdays_in(text)) and ("매주" in text or "마다" in text)


def occurrences(item: Item, lo: dt.date, hi: dt.date) -> list[Item]:
    """[lo, hi] 범위 안에서 반복 규칙이 맞는 날짜마다 가상 Item(인스턴스)을 생성.

    인스턴스는 id=None(영속 항목 아님 → 완료 토글 비활성)이며 date가 채워진다.
    """
    text = item.recurrence or ""
    if not is_parseable(text):
        return []
    out: list[Item] = []
    cur = lo
    while cur <= hi:
        if _matches(text, cur):
            out.append(replace(item, id=None, date=cur.isoformat()))
        cur += dt.timedelta(days=1)
    return out
