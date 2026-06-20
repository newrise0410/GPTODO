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
    rest = re.sub(r"\d+\s*월", " ", rest)  # 'N월'의 '월'(월요일 오인) 제외
    rest = re.sub(r"\d+\s*일", " ", rest)  # 'N일' 같은 날짜의 '일' 제외
    for ch in rest:
        if ch in _WEEKDAY_TOKENS:
            days.add(_WEEKDAY_TOKENS[ch])
    return days


# §15 주차 서수: '첫째 주 월요일', '마지막 금요일', '격주 수요일' 등
_ORDINALS = {
    "첫째": 1, "첫번째": 1, "첫 번째": 1, "둘째": 2, "두번째": 2, "두 번째": 2,
    "셋째": 3, "세번째": 3, "세 번째": 3, "넷째": 4, "네번째": 4, "네 번째": 4,
    "다섯째": 5, "다섯번째": 5,
}


def _days_in_month(d: dt.date) -> int:
    nxt = (d.replace(day=1) + dt.timedelta(days=32)).replace(day=1)
    return (nxt - dt.timedelta(days=1)).day


def _matches(text: str, d: dt.date, anchor: dt.date | None = None) -> bool:
    if "매년" in text:
        m = re.search(r"(\d{1,2})\s*월\s*(\d{1,2})\s*일", text)
        return bool(m) and d.month == int(m.group(1)) and d.day == int(m.group(2))
    if "매일" in text:
        return True
    if "평일" in text:
        return d.weekday() < 5
    if "주말" in text:
        return d.weekday() >= 5
    if "매월" in text:
        m = re.search(r"(\d{1,2})\s*일", text)
        if m:
            return d.day == int(m.group(1))
        # 숫자 없는 '매월 첫째 주 월요일' 등은 아래 요일/주차 로직으로 넘어간다.
    days = _weekdays_in(text)
    if not days or d.weekday() not in days:
        return False
    # 여기부터 요일은 일치 — 빈도/주차 조건만 본다.
    if "마지막" in text:
        return d.day + 7 > _days_in_month(d)
    for word, n in _ORDINALS.items():
        if word in text:
            return (n - 1) * 7 < d.day <= n * 7
    if "격주" in text:
        # 시작 기준(anchor)이 있으면 그로부터 2주 주기, 없으면 ISO week parity
        if anchor:
            return ((d - anchor).days // 7) % 2 == 0
        return d.isocalendar()[1] % 2 == 0
    return True  # 매주/마다/요일만 → 매주


def is_parseable(text: str | None) -> bool:
    if not text:
        return False
    if any(k in text for k in ("매일", "평일", "주말", "매월", "매년")):
        return True
    return bool(_weekdays_in(text))


def occurrences(item: Item, lo: dt.date, hi: dt.date) -> list[Item]:
    """[lo, hi] 범위 안에서 반복 규칙이 맞는 날짜마다 가상 Item(인스턴스)을 생성.

    인스턴스는 id=None(영속 항목 아님 → 완료 토글 비활성)이며 date가 채워진다.
    """
    text = item.recurrence or ""
    if not is_parseable(text):
        return []
    anchor = item.date_obj  # 격주 시작 기준(있으면)
    out: list[Item] = []
    cur = lo
    while cur <= hi:
        if _matches(text, cur, anchor):
            out.append(replace(item, id=None, date=cur.isoformat()))
        cur += dt.timedelta(days=1)
    return out
