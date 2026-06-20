"""LLM 추출 레이어 — 자유 문장 → 구조화된 연산(operations).

LLM은 '무엇을 할지'(추가/완료/수정/삭제)와 항목 속성만 뽑는다.
날짜 환산·충돌 감지·보기 렌더링은 전부 앱 코드가 결정론적으로 처리한다.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from .. import profile, store, timeutil
from ..models import CATEGORIES, Item, coerce_changes, coerce_item
from .client import complete, complete_stream

SENTINEL = "===JSON==="

_RULES = f"""너는 사용자의 한국어/영어 문장에서 일정·할 일·메모를 뽑아 구조화하는 추출기다.

현재 한국(KST) 날짜: {{today}}. 상대 표현(오늘/내일/모레/이번 주/다음 주/이번 달 등)은
이 날짜 기준으로 실제 YYYY-MM-DD로 환산하라.

출력은 정확히 두 부분으로, 이 순서를 지켜라:
1) 사용자에게 보여줄 1~2줄 친근한 수신 확인(평문). 캘린더 표·메뉴·JSON 금지.
2) 다음 줄에 `{SENTINEL}` 한 줄. 그 아래에 JSON 객체 하나.

JSON 형식:
{{{{
  "operations": [
    {{{{"op": "add", "item": {{{{...속성...}}}}}}}},
    {{{{"op": "complete", "id": 3}}}},
    {{{{"op": "reopen", "id": 3}}}},
    {{{{"op": "update", "id": 5, "changes": {{{{...바뀐 속성만...}}}}}}}},
    {{{{"op": "delete", "id": 5}}}},
    {{{{"op": "set_profile", "profile": {{{{"name": "...", "role": "...", "categories": ["업무"], "preference": "캘린더"}}}}}}}}
  ],
  "questions": ["최대 2개. 정말 불명확할 때만."]
}}}}

item/changes 속성:
- title (필수, 문자열)
- kind: "event"(특정 날짜·시간) / "todo"(날짜·시간 불명확) / "memo"(메모) / "idea"(아이디어)
- date: "YYYY-MM-DD" 또는 null(날짜 미정)
- date_expr: 날짜를 가리키는 원문 표현 그대로(예:"내일","금요일","다음 주 화요일","이번 달 말","6월 25일").
  상대·요일 표현이면 반드시 이 필드를 채워라 — 실제 날짜 계산은 앱이 정확히 한다(date는 비워도 됨).
- time: "HH:MM" 24시간제 또는 null(시간 미정). 오후 3시→"15:00", 정오→"12:00".
- deadline: 마감일 "YYYY-MM-DD" 또는 null(일정 날짜와 별개. "~까지" 표현은 deadline).
  deadline_expr: 마감의 원문 표현(예:"금요일까지"의 "금요일"). 상대·요일이면 반드시 채워라.
- category: {CATEGORIES} 중 하나 또는 null
- priority: "very_high"|"high"|"medium"|"low"|null  (오늘마감/제출/시험/면접/계약=very_high, 내일·이번주마감/예약/병원=high)
- recurrence: "매주 월요일"·"격주 수요일"·"매월 1일"·"마지막 금요일" 같은 반복 주기 또는 null
- project: 큰 작업명 또는 null.  parent_id: 기존 상위 항목 id(분해 단계일 때).  sort_order: 단계 순서 정수
- (분해) 같은 요청에서 상위+하위를 함께 만들 땐 상위 add에 "ref":"임의이름", 하위 add에 "parent_ref":"같은이름"·sort_order로 연결.
  상위·하위 모두 같은 project 값을 둔다.
- location, people: 있으면, 없으면 null
- estimate_min: 예상 소요(분) 정수 또는 null
- needs_review: 날짜/시간/대상이 모호하면 true
- review_reason: needs_review가 true면 짧은 이유

핵심 규칙:
1) 사용자가 말하지 않은 항목을 만들지 마라. 단, 큰 작업은 단계로 분해 제안 가능(이때 같은 project 값으로 여러 add, 수신 확인에 분해임을 알림).
2) 한 문장에 여러 항목이 있으면 각각 분리해 add 한다. 단순 적어두기/생각은 kind="memo"/"idea".
3) 완료/끝냈어/했어 → complete. 삭제/취소 → delete. 시간·내용 변경 → update.
   대상이 여러 개라 모호하면 연산하지 말고 questions로 물어라.
3-1) 사용자가 이름/직업/선호 카테고리/정리 방식을 알려주면 set_profile로 저장한다.
4) 보기 전환(오늘/이번 주/대시보드/표/체크리스트/요약 등)은 네가 처리하지 않는다 — 그런 요청이면 operations는 비우고 수신 확인만.
5) 저장했다고 단정하지 마라("정리했어요" 정도).
"""


def _normalize(text: str) -> str:
    """로컬 모델 아티팩트 정규화 — 예: gemma의 SentencePiece 공백 '▁'(U+2581)→공백."""
    return text.replace("▁", " ")


def _loads(content: str) -> dict[str, Any] | None:
    """프로즈/코드펜스로 감싸인 경우까지 첫 JSON 객체를 파싱."""
    content = _normalize(content).strip()
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


def _note_prefix(buf: str) -> str:
    """누적 버퍼에서 사용자에게 보여줄 수신 확인 부분(센티넬/JSON 이전)만 추출."""
    buf = _normalize(buf)
    idx = buf.find(SENTINEL)
    head = buf if idx == -1 else buf[:idx]
    # 센티넬이 아직 안 왔지만 JSON이 시작되면 거기서 끊는다.
    brace = head.find("{")
    if brace != -1:
        head = head[:brace]
    # 코드펜스(```), 잔여 백틱 정리
    head = head.replace("```json", "").replace("```", "")
    return head.strip()


def _parse(full: str) -> dict[str, Any]:
    """전체 응답 → {reply, operations, questions}. 센티넬 우선, 실패 시 폴백.

    operations/questions 타입을 방어적으로 검증한다(모델이 dict 대신 문자열 등을 줘도 안전).
    """
    reply = _note_prefix(full)
    json_part = full.split(SENTINEL, 1)[1] if SENTINEL in full else full
    data = _loads(json_part)
    if not isinstance(data, dict):
        data = {}
    if not reply:  # 폴백: JSON 안에 reply가 있던 옛 형식 호환
        reply = str(data.get("reply", "")).strip()
    ops = data.get("operations")
    questions = data.get("questions")
    return {
        "reply": reply,
        "operations": ops if isinstance(ops, list) else [],
        "questions": [str(q) for q in questions[:2]] if isinstance(questions, list) else [],
    }


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


def _instructions() -> str:
    parts = [_system_prompt(), _items_context(store.all_items())]
    prof = profile.as_context()
    if prof:
        parts.append(prof)
    return "\n\n".join(parts)


def extract(history: list[dict[str, Any]]) -> dict[str, Any]:
    """대화 기록을 받아 {reply, operations, questions} 반환(비스트리밍)."""
    return _parse(complete(_instructions(), history))


def stream(history: list[dict[str, Any]]) -> Iterator[tuple[str, Any]]:
    """스트리밍 추출.

    ("note", 누적_수신확인_텍스트) 이벤트를 진행 중 yield하고,
    마지막에 ("result", {reply, operations, questions})를 한 번 yield한다.
    """
    instructions = _instructions()
    buf = ""
    last_note = ""
    for delta in complete_stream(instructions, history):
        buf += delta
        note = _note_prefix(buf)
        if note != last_note:
            last_note = note
            yield ("note", note)
    yield ("result", _parse(buf))


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
                        ref = (str(item.get("ref")).strip() or None) if item.get("ref") else None
                        parent_ref = (str(item.get("parent_ref")).strip() or None
                                      if item.get("parent_ref") else None)
                        norm.append(("add", it, ref, parent_ref))
            elif kind == "set_profile":
                if isinstance(op.get("profile"), dict):
                    profile.update(op["profile"])
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
