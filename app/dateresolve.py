"""한국어 상대·요일 날짜 리졸버 — KST 기준으로 정확히 환산.

LLM(특히 소형 로컬 모델)은 "금요일", "다음 주 화요일" 같은 요일→날짜 계산을
자주 틀린다. 그래서 모델은 원문 표현만 뽑고, 실제 날짜 계산은 이 코드가 한다.
해석 불가하면 None을 반환해 모델이 준 값을 유지한다.
"""

from __future__ import annotations

import datetime as dt
import re

_WD = {"월": 0, "화": 1, "수": 2, "목": 3, "금": 4, "토": 5, "일": 6}
_OFFSET_DAYS = {"오늘": 0, "금일": 0, "내일": 1, "익일": 1, "모레": 2, "글피": 3, "어제": -1}


def _add_months_first(d: dt.date, off: int) -> dt.date:
    total = (d.month - 1) + off
    return dt.date(d.year + total // 12, total % 12 + 1, 1)


def _month_end(d: dt.date, off: int) -> dt.date:
    return _add_months_first(d, off + 1) - dt.timedelta(days=1)


def resolve(expr: str | None, today: dt.date) -> dt.date | None:
    """한국어 날짜 표현 → 실제 date. 못 풀면 None."""
    if not expr:
        return None
    s = str(expr).strip()

    # 명시적 'M월 D일' (지났으면 내년)
    m = re.search(r"(\d{1,2})\s*월\s*(\d{1,2})\s*일", s)
    if m:
        mo, day = int(m.group(1)), int(m.group(2))
        for year in (today.year, today.year + 1):
            try:
                cand = dt.date(year, mo, day)
            except ValueError:
                return None
            if cand >= today:
                return cand
        return cand

    # 오늘/내일/모레/글피/어제
    for k, off in _OFFSET_DAYS.items():
        if k in s:
            return today + dt.timedelta(days=off)

    # N일/주 후·뒤
    m = re.search(r"(\d+)\s*일\s*(뒤|후|있다|지나)", s)
    if m:
        return today + dt.timedelta(days=int(m.group(1)))
    m = re.search(r"(\d+)\s*주\s*(뒤|후)", s)
    if m:
        return today + dt.timedelta(weeks=int(m.group(1)))

    # 달 말/초
    if "말" in s and ("달" in s or "월" in s):
        off = 2 if "다다음" in s else (1 if ("다음" in s or "담" in s) else 0)
        return _month_end(today, off)
    if "초" in s and ("달" in s or "월" in s):
        off = 1 if ("다음" in s or "담" in s) else 0
        return _add_months_first(today, off)

    # 요일 (이번/다음/다다음 주 + 요일, 또는 단독)
    mm = re.search(r"([월화수목금토일])\s*요일", s)
    if mm:
        wd = _WD[mm.group(1)]
        if "다다음" in s:
            weekoff = 2
        elif "다음" in s or "담주" in s or "담 주" in s:
            weekoff = 1
        elif "이번" in s:
            weekoff = 0
        else:
            weekoff = None  # 단독 → 다가오는 그 요일
        if weekoff is None:
            return today + dt.timedelta(days=(wd - today.weekday()) % 7)
        monday = today - dt.timedelta(days=today.weekday())
        return monday + dt.timedelta(days=weekoff * 7 + wd)

    return None
