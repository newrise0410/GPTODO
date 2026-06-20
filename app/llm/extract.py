"""LLM 추출 레이어 — 자유 문장 → 구조화된 연산(operations).

LLM은 '무엇을 할지'(추가/완료/수정/삭제)와 항목 속성만 뽑는다.
날짜 환산·충돌 감지·보기 렌더링은 전부 앱 코드가 결정론적으로 처리한다.
"""

from __future__ import annotations

import json
from typing import Any

from .. import store, timeutil
from ..models import CATEGORIES, Item, coerce_changes, coerce_item
from .client import complete

_RULES = f"""너는 사용자의 한국어/영어 문장에서 일정·할 일·메모를 뽑아 구조화하는 추출기다.
출력은 반드시 아래 JSON 한 개만. 설명 문장 금지.

현재 한국(KST) 날짜: {{today}}. 상대 표현(오늘/내일/모레/이번 주/다음 주/이번 달 등)은
이 날짜 기준으로 실제 YYYY-MM-DD로 환산하라.

JSON 형식:
{{{{
  "reply": "1~3줄 친근한 수신 확인. 캘린더 표나 메뉴는 절대 넣지 마라(앱이 그린다).",
  "operations": [
    {{{{"op": "add", "item": {{{{...속성...}}}}}}}},
    {{{{"op": "complete", "id": 3}}}},
    {{{{"op": "reopen", "id": 3}}}},
    {{{{"op": "update", "id": 5, "changes": {{{{...바뀐 속성만...}}}}}}}},
    {{{{"op": "delete", "id": 5}}}}
  ],
  "questions": ["최대 2개. 정말 불명확할 때만."]
}}}}

item/changes 속성:
- title (필수, 문자열)
- kind: "event"(특정 날짜·시간) 또는 "todo"(날짜/시간 불명확)
- date: "YYYY-MM-DD" 또는 null(날짜 미정)
- time: "HH:MM" 24시간제 또는 null(시간 미정). 오후 3시→"15:00", 정오→"12:00".
- category: {CATEGORIES} 중 하나 또는 null
- priority: "very_high"|"high"|"medium"|"low"|null  (오늘마감/제출/시험/면접/계약=very_high, 내일·이번주마감/예약/병원=high)
- recurrence: "매주 월요일" 같은 반복 주기 또는 null
- project: 큰 작업명 또는 null
- location, people: 있으면, 없으면 null
- estimate_min: 예상 소요(분) 정수 또는 null
- needs_review: 날짜/시간/대상이 모호하면 true
- review_reason: needs_review가 true면 짧은 이유

핵심 규칙:
1) 사용자가 말하지 않은 항목을 만들지 마라. 단, 큰 작업은 단계로 분해 제안 가능(이때 reply로 분해임을 알림).
2) 한 문장에 여러 항목이 있으면 각각 분리해 add 한다.
3) 완료/끝냈어/했어 → complete. 삭제/취소 → delete. 시간·내용 변경 → update.
   대상이 여러 개라 모호하면 연산하지 말고 questions로 물어라.
4) 보기 전환(오늘/이번 주/대시보드 등)은 네가 처리하지 않는다 — 그런 요청이면 operations는 비우고 reply만.
5) 저장했다고 단정하지 마라("정리했어요" 정도).
"""


def _loads(content: str) -> dict[str, Any] | None:
    """프로즈/코드펜스로 감싸인 경우까지 첫 JSON 객체를 파싱."""
    content = content.strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass
    start = content.find("{")
    end = content.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(content[start:end + 1])
        except json.JSONDecodeError:
            return None
    return None


def _items_context(items: list[Item]) -> str:
    if not items:
        return "현재 정리된 항목: 없음"
    rows = []
    for it in items:
        rows.append(
            f"#{it.id} [{it.status}] {it.kind} | {it.title} | "
            f"date={it.date} time={it.time} cat={it.category} prio={it.priority}"
        )
    return "현재 정리된 항목(연산 시 id 참조):\n" + "\n".join(rows)


def _system_prompt() -> str:
    return _RULES.format(today=timeutil.header())


def extract(history: list[dict[str, Any]]) -> dict[str, Any]:
    """대화 기록을 받아 {reply, operations, questions} 반환."""
    instructions = _system_prompt() + "\n\n" + _items_context(store.all_items())
    content = complete(instructions, history, json_mode=True)
    data = _loads(content)
    if data is None:
        return {"reply": "조금 더 구체적으로 적어주시겠어요?", "operations": [], "questions": []}
    return {
        "reply": str(data.get("reply", "")).strip(),
        "operations": data.get("operations") or [],
        "questions": (data.get("questions") or [])[:2],
    }


def _as_id(v: Any) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def apply_operations(operations: Any) -> dict[str, int]:
    """LLM 연산을 검증·정규화한 뒤 단일 트랜잭션으로 적용. rowcount 기반 카운트 반환.

    잘못된 op(문자열/누락 필드/없는 id 등)는 조용히 건너뛰며, 절대 예외를 던지지 않는다.
    실제로 변경된 항목만 카운트된다(없는 id 완료/삭제는 0).
    """
    norm: list[tuple] = []
    if isinstance(operations, list):
        for op in operations:
            if not isinstance(op, dict):
                continue
            kind = op.get("op")
            if kind == "add":
                item = op.get("item")
                if isinstance(item, dict):
                    it = coerce_item(item)
                    if it.title:
                        norm.append(("add", it))
            elif kind in ("complete", "reopen", "delete", "update"):
                iid = _as_id(op.get("id"))
                if iid is None:
                    continue
                if kind == "complete":
                    norm.append(("status", iid, "done"))
                elif kind == "reopen":
                    norm.append(("status", iid, "open"))
                elif kind == "delete":
                    norm.append(("delete", iid))
                else:  # update
                    changes = coerce_changes(op.get("changes") or {})
                    if changes:
                        norm.append(("update", iid, changes))
    return store.apply_batch(norm)
