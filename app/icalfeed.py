"""iCalendar(.ics) 피드 — 애플/구글 캘린더에 'URL 구독'으로 단방향 동기화.

대상(§캘린더 연동): 일정(event, date 있음) + 마감 있는 할 일(deadline).
반복 일정은 향후 구간으로 펼쳐 개별 VEVENT로 넣는다(RRULE 매핑 오류 회피).
시각은 Asia/Seoul→UTC로 변환해 'Z' 표기.
"""

from __future__ import annotations

import datetime as dt
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from . import recurrence, timeutil
from .models import Item

KST = ZoneInfo("Asia/Seoul")
UTC = dt.timezone.utc
_WINDOW_BACK = 7
_WINDOW_FWD = 90  # 반복 일정 펼침 구간(일)


def event_times(item: Item) -> tuple[object, object, bool] | None:
    """캘린더에 넣을 (start, end, all_day) 반환. 대상 아니면 None.

    all_day=True면 start/end는 date(끝은 배타적), False면 UTC datetime.
    """
    if item.kind == "event" and item.date_obj:
        if item.time:
            try:
                h, m = (int(x) for x in item.time.split(":"))
            except ValueError:
                return None
            start = dt.datetime(item.date_obj.year, item.date_obj.month, item.date_obj.day,
                                h, m, tzinfo=KST).astimezone(UTC)
            end = start + dt.timedelta(minutes=item.estimate_min or 60)
            return start, end, False
        return item.date_obj, item.date_obj + dt.timedelta(days=1), True
    if item.deadline_obj:  # 마감 있는 할 일 → 마감일 종일 일정
        return item.deadline_obj, item.deadline_obj + dt.timedelta(days=1), True
    return None


def is_eligible(item: Item) -> bool:
    return item.status != "done" and event_times(item) is not None


def google_url(item: Item) -> str | None:
    """구글 캘린더 '일정 추가' 미리채움 링크(원클릭)."""
    t = event_times(item)
    if not t:
        return None
    start, end, all_day = t
    if all_day:
        dates = f"{start.strftime('%Y%m%d')}/{end.strftime('%Y%m%d')}"
    else:
        dates = f"{start.strftime('%Y%m%dT%H%M%SZ')}/{end.strftime('%Y%m%dT%H%M%SZ')}"
    params = {"action": "TEMPLATE", "text": item.title, "dates": dates}
    if item.location:
        params["location"] = item.location
    return "https://calendar.google.com/calendar/render?" + urlencode(params)


def _esc(text: str) -> str:
    text = (text.replace("\\", "\\\\").replace("\r\n", "\n").replace("\r", "\n"))
    return text.replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _fold(line: str) -> str:
    """RFC5545 라인 폴딩 — UTF-8 73바이트 넘으면 CRLF+space로 접는다."""
    if len(line.encode("utf-8")) <= 73:
        return line
    out, cur = [], ""
    for ch in line:
        if len((cur + ch).encode("utf-8")) > 73:
            out.append(cur)
            cur = ch
        else:
            cur += ch
    out.append(cur)
    return "\r\n ".join(out)


def _vevent(item: Item, stamp: str, uid_suffix: str = "") -> list[str] | None:
    t = event_times(item)
    if not t:
        return None
    start, end, all_day = t
    uid = f"{item.id if item.id is not None else 'r'}{uid_suffix}@gptodo"
    lines = ["BEGIN:VEVENT", f"UID:{uid}", f"DTSTAMP:{stamp}"]
    if all_day:
        lines.append(f"DTSTART;VALUE=DATE:{start.strftime('%Y%m%d')}")
        lines.append(f"DTEND;VALUE=DATE:{end.strftime('%Y%m%d')}")
    else:
        lines.append(f"DTSTART:{start.strftime('%Y%m%dT%H%M%SZ')}")
        lines.append(f"DTEND:{end.strftime('%Y%m%dT%H%M%SZ')}")
    prefix = "[마감] " if (item.kind != "event" and item.deadline_obj) else ""
    lines.append(f"SUMMARY:{_esc(prefix + item.title)}")
    if item.location:
        lines.append(f"LOCATION:{_esc(item.location)}")
    desc = " · ".join(x for x in (item.category, item.people) if x)
    if desc:
        lines.append(f"DESCRIPTION:{_esc(desc)}")
    lines.append("END:VEVENT")
    return lines


def _wrap(lines: list[str]) -> str:
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"


def single_ics(item: Item) -> str:
    stamp = timeutil.now().astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    body = _vevent(item, stamp) or []
    return _wrap(["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//GPTODO//KR",
                  "CALSCALE:GREGORIAN", *body, "END:VCALENDAR"])


def build(items: list[Item]) -> str:
    """전체 구독 피드. 반복 일정은 [today-7, today+90] 구간으로 펼친다."""
    stamp = timeutil.now().astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    lo = timeutil.today() - dt.timedelta(days=_WINDOW_BACK)
    hi = timeutil.today() + dt.timedelta(days=_WINDOW_FWD)
    out = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//GPTODO//KR",
           "CALSCALE:GREGORIAN", "METHOD:PUBLISH", "X-WR-CALNAME:GPTODO",
           "X-WR-TIMEZONE:Asia/Seoul"]
    for it in items:
        if it.status == "done":
            continue
        if it.recurrence:
            for occ in recurrence.occurrences(it, lo, hi):
                # UID에 원본 item id 포함 → 다른 반복 항목이 같은 날 충돌하지 않게
                block = _vevent(occ, stamp, uid_suffix=f"-{it.id}-{occ.date}")
                if block:
                    out.extend(block)
        else:
            block = _vevent(it, stamp)
            if block:
                out.extend(block)
    out.append("END:VCALENDAR")
    return _wrap(out)
