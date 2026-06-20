"""구글 캘린더 양방향 동기화 엔진 — pull(원격→로컬) 후 push(로컬→원격).

client는 gcal.GoogleCalendar(또는 테스트용 fake). 외부 호출을 주입받아 테스트 가능.
매핑은 item.google_event_id ↔ Google eventId, 그리고 우리가 만든 이벤트는
extendedProperties.private.gptodo_id로 식별한다.
"""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from . import icalfeed, store
from .models import Item

KST = ZoneInfo("Asia/Seoul")


def build_event_body(item: Item) -> dict:
    start, end, all_day = icalfeed.event_times(item)
    prefix = "[마감] " if (item.kind != "event" and item.deadline_obj) else ""
    body: dict = {
        "summary": prefix + item.title,
        "extendedProperties": {"private": {"gptodo_id": str(item.id)}},
    }
    if all_day:
        body["start"] = {"date": start.strftime("%Y-%m-%d")}
        body["end"] = {"date": end.strftime("%Y-%m-%d")}
    else:
        body["start"] = {"dateTime": start.strftime("%Y-%m-%dT%H:%M:%SZ"), "timeZone": "UTC"}
        body["end"] = {"dateTime": end.strftime("%Y-%m-%dT%H:%M:%SZ"), "timeZone": "UTC"}
    if item.location:
        body["location"] = item.location
    return body


def _parse_remote(ev: dict) -> tuple[str, str | None, str | None]:
    title = (ev.get("summary") or "").removeprefix("[마감] ").strip() or "(제목 없음)"
    start = ev.get("start") or {}
    if "date" in start:
        return title, start["date"], None
    if "dateTime" in start:
        kst = dt.datetime.fromisoformat(start["dateTime"]).astimezone(KST)
        return title, kst.strftime("%Y-%m-%d"), kst.strftime("%H:%M")
    return title, None, None


def pull(client) -> dict[str, int]:
    """원격 변경을 로컬에 반영. 새 이벤트 생성, 수정 반영, 취소 삭제."""
    counts = {"created": 0, "updated": 0, "removed": 0}
    events, next_token = client.list_changes()
    items = store.all_items()
    by_gid = {i.google_event_id: i for i in items if i.google_event_id}
    for ev in events:
        eid = ev.get("id")
        if not eid:
            continue
        if ev.get("status") == "cancelled":
            local = by_gid.get(eid)
            if local:
                store.set_gcal_id(local.id, None)  # tombstone 재생성 방지
                store.delete(local.id)
                counts["removed"] += 1
            continue
        title, date, time = _parse_remote(ev)
        gptodo_id = (ev.get("extendedProperties", {}).get("private", {}) or {}).get("gptodo_id")
        local = by_gid.get(eid)
        if local is None and gptodo_id:
            local = next((i for i in items if str(i.id) == str(gptodo_id)), None)
        if local is not None:
            store.update(local.id, {"title": title, "date": date, "time": time,
                                    "google_event_id": eid})
            counts["updated"] += 1
        else:  # 구글에서 직접 만든 일정 → 로컬 생성
            store.add(Item(title=title, kind="event", date=date, time=time,
                           google_event_id=eid))
            counts["created"] += 1
    client.save_sync_token(next_token)
    return counts


def push(client) -> dict[str, int]:
    """로컬 상태를 원격에 반영. 삭제(tombstone)·완료 제거·신규/수정 업로드."""
    counts = {"pushed": 0, "deleted": 0}
    # 로컬에서 지운 항목 → 원격 삭제
    for eid in store.all_tombstones():
        client.delete_event(eid)
        store.clear_tombstone(eid)
        counts["deleted"] += 1
    for it in store.all_items():
        if it.status == "done":
            if it.google_event_id:  # 완료된 일정은 캘린더에서 제거
                client.delete_event(it.google_event_id)
                store.set_gcal_id(it.id, None)
                counts["deleted"] += 1
            continue
        if not icalfeed.is_eligible(it):
            continue
        body = build_event_body(it)
        if it.google_event_id:
            try:
                client.update_event(it.google_event_id, body)
            except KeyError:  # 원격에서 사라짐 → 재생성
                store.set_gcal_id(it.id, client.insert_event(body))
        else:
            store.set_gcal_id(it.id, client.insert_event(body))
        counts["pushed"] += 1
    return counts


def sync(client) -> dict[str, int]:
    """전체 동기화: 원격 변경 먼저 반영(pull) 후 로컬 업로드(push)."""
    pulled = pull(client)
    pushed = push(client)
    return {**pulled, **pushed}
