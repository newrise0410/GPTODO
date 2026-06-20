"""구글 캘린더 양방향 동기화 엔진 — pull(원격→로컬) 후 push(로컬→원격).

client는 gcal.GoogleCalendar(또는 테스트용 fake). 외부 호출을 주입받아 테스트 가능.
매핑은 item.google_event_id ↔ Google eventId, 우리가 만든 이벤트는
extendedProperties.private.gptodo_id로 식별한다.

견고화:
- 변경분만 push(gcal_sig 서명 비교) → 무의미한 원격 수정/동기화 churn 방지
- 로컬 삭제(tombstone)는 pull에서 부활하지 않게 우선 적용
- 마감 할 일은 gptodo_field=deadline 로 표시 → pull 때 deadline 필드를 갱신
- 수정은 PATCH(gcal.py) → 원격 전용 필드 보존
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from zoneinfo import ZoneInfo

from . import icalfeed, store
from .models import Item

KST = ZoneInfo("Asia/Seoul")


def build_event_body(item: Item) -> dict:
    start, end, all_day = icalfeed.event_times(item)
    is_deadline = item.kind != "event" and item.deadline_obj
    body: dict = {
        "summary": ("[마감] " if is_deadline else "") + item.title,
        "extendedProperties": {"private": {
            "gptodo_id": str(item.id),
            "gptodo_field": "deadline" if is_deadline else "date",
        }},
    }
    if all_day:
        body["start"] = {"date": start.strftime("%Y-%m-%d")}
        body["end"] = {"date": end.strftime("%Y-%m-%d")}
    else:
        body["start"] = {"dateTime": start.strftime("%Y-%m-%dT%H:%M:%SZ"), "timeZone": "UTC"}
        body["end"] = {"dateTime": end.strftime("%Y-%m-%dT%H:%M:%SZ"), "timeZone": "UTC"}
    body["location"] = item.location or ""
    return body


def _sig(body: dict) -> str:
    return hashlib.sha1(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


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
    tombs = set(store.all_tombstones())  # 로컬 삭제 예정 → pull에서 부활 금지
    items = store.all_items()
    by_gid = {i.google_event_id: i for i in items if i.google_event_id}
    for ev in events:
        eid = ev.get("id")
        if not eid or eid in tombs:
            continue
        if ev.get("status") == "cancelled":
            local = by_gid.get(eid)
            if local:
                store.set_gcal_id(local.id, None)  # tombstone 재생성 방지
                store.delete(local.id)
                counts["removed"] += 1
            continue
        title, date, time = _parse_remote(ev)
        priv = (ev.get("extendedProperties", {}).get("private", {}) or {})
        gptodo_id = priv.get("gptodo_id")
        field = priv.get("gptodo_field", "date")
        local = by_gid.get(eid)
        if local is None and gptodo_id:
            local = next((i for i in items if str(i.id) == str(gptodo_id)), None)
        if local is not None:
            changes = {"title": title, "google_event_id": eid}
            if field == "deadline":   # 마감 항목은 deadline을 갱신(date 덮어쓰지 않음)
                changes["deadline"] = date
            else:
                changes["date"] = date
                changes["time"] = time
            store.update(local.id, changes)
            counts["updated"] += 1
        else:  # 구글에서 직접 만든 일정 → 로컬 생성
            store.add(Item(title=title, kind="event", date=date, time=time,
                           google_event_id=eid))
            counts["created"] += 1
    client.save_sync_token(next_token)
    return counts


def push(client) -> dict[str, int]:
    """로컬 상태를 원격에 반영. 삭제(tombstone)·완료 제거·변경분 업로드."""
    counts = {"pushed": 0, "deleted": 0}
    for eid in store.all_tombstones():       # 로컬에서 지운 항목 → 원격 삭제
        client.delete_event(eid)
        store.clear_tombstone(eid)
        counts["deleted"] += 1
    for it in store.all_items():
        if it.status == "done":
            if it.google_event_id:           # 완료된 일정은 캘린더에서 제거
                client.delete_event(it.google_event_id)
                store.set_gcal_id(it.id, None)
                counts["deleted"] += 1
            continue
        if not icalfeed.is_eligible(it):
            continue
        body = build_event_body(it)
        sig = _sig(body)
        if it.google_event_id and it.gcal_sig == sig:
            continue                          # 변경 없음 → push 스킵(churn 방지)
        if it.google_event_id:
            try:
                client.update_event(it.google_event_id, body)
                store.set_gcal_id(it.id, it.google_event_id, sig)
            except KeyError:                  # 원격에서 사라짐 → 재생성
                store.set_gcal_id(it.id, client.insert_event(body), sig)
        else:
            store.set_gcal_id(it.id, client.insert_event(body), sig)
        counts["pushed"] += 1
    return counts


def sync(client) -> dict[str, int]:
    """전체 동기화: 원격 변경 먼저 반영(pull) 후 로컬 업로드(push)."""
    pulled = pull(client)
    pushed = push(client)
    return {**pulled, **pushed}
