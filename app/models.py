"""도메인 모델 — 프롬프트 §4/§5/§9/§10 추출 항목을 구조화."""

from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass
from typing import Any

from . import timeutil

# 프롬프트 §9 카테고리
CATEGORIES = ["업무", "학업", "건강", "개인", "가족", "재무", "취미", "여행", "인간관계", "기타"]

# 프롬프트 §10 우선순위 (정렬용 가중치 + 표시 이모지)
PRIORITY_RANK = {"very_high": 0, "high": 1, "medium": 2, "low": 3}
PRIORITY_EMOJI = {"very_high": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢"}
PRIORITY_LABEL = {"very_high": "매우 높음", "high": "높음", "medium": "보통", "low": "낮음"}


@dataclass
class Item:
    title: str
    kind: str = "todo"  # 'event'(일정) | 'todo'(할 일) — §5
    date: str | None = None  # YYYY-MM-DD, None=날짜 미정
    time: str | None = None  # HH:MM, None=시간 미정
    category: str | None = None
    priority: str | None = None  # very_high|high|medium|low
    recurrence: str | None = None  # §15 반복 주기 (예: '매주 월요일')
    project: str | None = None
    location: str | None = None
    people: str | None = None
    estimate_min: int | None = None  # §11 예상 소요시간(분)
    status: str = "open"  # open | done
    needs_review: bool = False  # §14 확인 필요
    review_reason: str | None = None
    id: int | None = None
    created_at: str | None = None

    # ---- 파생 ----
    @property
    def date_obj(self) -> dt.date | None:
        return timeutil.parse_date(self.date)

    @property
    def rank(self) -> int:
        return PRIORITY_RANK.get(self.priority or "medium", 2)

    def to_row(self) -> dict[str, Any]:
        d = asdict(self)
        d["needs_review"] = int(self.needs_review)
        return d

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "Item":
        data = dict(row)
        data["needs_review"] = bool(data.get("needs_review"))
        valid = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in valid})


def coerce_item(raw: dict[str, Any]) -> Item:
    """LLM이 준 dict를 안전하게 Item으로 변환(잘못된 값은 버리고 None 처리)."""
    def pick(key: str, allowed: list[str] | None = None) -> Any:
        v = _as_str(raw.get(key))           # 리스트/숫자 등도 문자열로 정규화
        if allowed and v not in allowed:
            return None
        return v

    title = _as_str(raw.get("title")) or ""
    kind = pick("kind", ["event", "todo"]) or "todo"
    date = pick("date")
    if date and not timeutil.parse_date(date):  # 형식 틀리면 날짜 미정
        date = None
    time = pick("time")
    est = raw.get("estimate_min")
    return Item(
        title=title,
        kind=kind,
        date=date,
        time=time if _valid_time(time) else None,
        category=pick("category", CATEGORIES),
        priority=pick("priority", list(PRIORITY_RANK)),
        recurrence=pick("recurrence"),
        project=pick("project"),
        location=pick("location"),
        people=pick("people"),
        estimate_min=int(est) if isinstance(est, (int, float)) else None,
        needs_review=bool(raw.get("needs_review")),
        review_reason=pick("review_reason"),
    )


def _as_str(v: Any) -> str | None:
    """문자열로 정규화. 리스트는 ', '로 합치고, None/빈값은 None."""
    if v is None:
        return None
    if isinstance(v, list):
        v = ", ".join(str(x) for x in v if x is not None)
    elif not isinstance(v, str):
        v = str(v)
    v = v.strip()
    return v or None


def _valid_time(t: Any) -> bool:
    if not isinstance(t, str):
        return False
    try:
        dt.time.fromisoformat(t)
        return True
    except ValueError:
        return False
