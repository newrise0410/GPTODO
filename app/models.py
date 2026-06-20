"""도메인 모델 — 프롬프트 §4/§5/§9/§10 추출 항목을 구조화."""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import asdict, dataclass
from typing import Any

from . import dateresolve, timeutil

# 프롬프트 §9 카테고리
CATEGORIES = ["업무", "학업", "건강", "개인", "가족", "재무", "취미", "여행", "인간관계", "기타"]

# §4/§5 항목 종류: 일정/할 일/메모/아이디어
KINDS = ["event", "todo", "memo", "idea"]
KIND_LABEL = {"event": "일정", "todo": "할 일", "memo": "메모", "idea": "아이디어"}

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
    deadline: str | None = None  # §4 마감일 YYYY-MM-DD (일정 날짜와 별개)
    parent_id: int | None = None  # §12 프로젝트 하위 단계의 상위 항목
    sort_order: int = 0  # §12 하위 단계 순서
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

    @property
    def deadline_obj(self) -> dt.date | None:
        return timeutil.parse_date(self.deadline)

    @property
    def due_obj(self) -> dt.date | None:
        """마감 임박 계산용 — deadline 우선, 없으면 일정 date."""
        return self.deadline_obj or self.date_obj

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
    kind = pick("kind", KINDS) or "todo"
    # 날짜: 원문 표현(date_expr)이 있으면 코드가 환산한 값을 우선(모델 계산 오류 보정)
    date = _resolve_or_keep(pick("date_expr"), pick("date"))
    deadline = _resolve_or_keep(pick("deadline_expr"), pick("deadline"))
    return Item(
        title=title,
        kind=kind,
        date=date,
        time=_norm_time(pick("time")),
        category=pick("category", CATEGORIES),
        priority=pick("priority", list(PRIORITY_RANK)),
        recurrence=pick("recurrence"),
        project=pick("project"),
        location=pick("location"),
        people=pick("people"),
        estimate_min=_as_minutes(raw.get("estimate_min")),
        deadline=deadline,
        parent_id=_as_int(raw.get("parent_id")),
        sort_order=_as_int(raw.get("sort_order")) or 0,
        needs_review=_as_bool(raw.get("needs_review")),
        review_reason=pick("review_reason"),
    )


def _resolve_or_keep(expr: str | None, model_date: str | None) -> str | None:
    """원문 표현이 풀리면 그 날짜, 아니면 모델이 준 날짜(형식 검증)."""
    if expr:
        rd = dateresolve.resolve(expr, timeutil.today())
        if rd:
            return rd.isoformat()
    return model_date if (model_date and timeutil.parse_date(model_date)) else None


def fmt_estimate(minutes: int | None) -> str | None:
    """분 단위 예상 소요시간을 사람이 읽기 쉬운 표기로(§11)."""
    if not minutes or minutes <= 0:
        return None
    if minutes < 60:
        return f"~{minutes}분"
    h, m = divmod(minutes, 60)
    return f"~{h}시간" + (f" {m}분" if m else "")


# 부분 업데이트(changes) 검증 — coerce_item과 동일한 필드 규칙을 재사용.
_TEXT_FIELDS = {"title", "recurrence", "project", "location", "people", "review_reason"}


def coerce_changes(changes: dict[str, Any]) -> dict[str, Any]:
    """LLM update changes를 필드별로 검증/정규화. 잘못된 값은 버린다."""
    if not isinstance(changes, dict):
        return {}
    out: dict[str, Any] = {}
    for k, v in changes.items():
        if k == "kind":
            if v in KINDS:
                out[k] = v
        elif k in ("date_expr", "deadline_expr"):  # 원문 표현 → 코드가 환산
            rd = dateresolve.resolve(_as_str(v), timeutil.today())
            if rd:
                out["deadline" if k == "deadline_expr" else "date"] = rd.isoformat()
        elif k in ("date", "deadline"):
            s = _as_str(v)
            if v is None:
                out[k] = None                  # 비우기 허용
            elif s and timeutil.parse_date(s):
                out[k] = s
        elif k == "parent_id":
            out[k] = _as_int(v)
        elif k == "sort_order":
            n = _as_int(v)
            if n is not None:
                out[k] = n
        elif k == "time":
            if v is None:
                out[k] = None
            elif (t := _norm_time(_as_str(v))):
                out[k] = t
        elif k == "category":
            if v in CATEGORIES or v is None:
                out[k] = v
        elif k == "priority":
            if v in PRIORITY_RANK:
                out[k] = v
        elif k == "estimate_min":
            if v is None:
                out[k] = None                  # 소요시간 지우기 허용
            elif (n := _as_minutes(v)) is not None:
                out[k] = n
        elif k == "needs_review":
            out[k] = _as_bool(v)
        elif k == "title":
            s = _as_str(v)
            if s:                              # 제목은 빈 값으로 덮어쓰지 않음
                out[k] = s
        elif k in _TEXT_FIELDS:
            out[k] = _as_str(v)                # None이면 해당 필드 비우기
    return out


def _as_int(v: Any) -> int | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return int(v)
    if isinstance(v, str):
        m = re.search(r"-?\d+", v)
        return int(m.group()) if m else None
    return None


def _as_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    if isinstance(v, str):
        return v.strip().lower() in ("true", "1", "yes", "y", "t", "참", "응", "네")
    return bool(v)


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


def _norm_time(t: Any) -> str | None:
    """시간을 정확히 'HH:MM'으로 정규화(초 단위 절삭). 잘못된 값은 None."""
    if not isinstance(t, str):
        return None
    try:
        parsed = dt.time.fromisoformat(t.strip())  # HH:MM, HH:MM:SS 모두 허용
    except ValueError:
        return None
    return f"{parsed.hour:02d}:{parsed.minute:02d}"


def _as_minutes(v: Any) -> int | None:
    """예상 소요시간을 분(양수)으로. 'N시간'/'N시간 M분'/'N분'/숫자 지원. 비현실값은 버림."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        n = int(v)
        return n if 0 < n <= 7 * 24 * 60 else None
    if isinstance(v, str):
        h = re.search(r"(\d+)\s*시간", v)
        m = re.search(r"(\d+)\s*분", v)
        if h or m:
            total = (int(h.group(1)) * 60 if h else 0) + (int(m.group(1)) if m else 0)
            return total if 0 < total <= 7 * 24 * 60 else None
        digits = re.search(r"\d+", v)
        if digits:
            n = int(digits.group())
            return n if 0 < n <= 7 * 24 * 60 else None
    return None
