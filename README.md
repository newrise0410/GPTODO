# GPTODO

아무렇게나 적어도 LLM이 **캘린더 중심**으로 정리해주는 채팅 웹앱.
일정·할 일·메모·아이디어를 구조화하고, 빠른 메뉴로 다양한 보기(오늘/이번 주/분류/프로젝트/대시보드)로 재구성한다.

LLM 백엔드 선택 우선순위는 코드 기준으로 **`OPENAI_API_KEY`가 설정돼 있으면 그것**, 없으면
**Codex / ChatGPT OAuth**(`codex login`)다. 즉 평소엔 `codex login`만 해두면 OAuth로 동작하고,
API 키를 쓰고 싶을 때 env로 켜면 그쪽이 우선한다. (`app/llm/client.py:complete`)

> - **추출 프롬프트(실사용)**: `app/llm/extract.py`의 `_RULES` — 문장→연산 JSON 변환 규칙.
> - **제품 동작 명세(참조)**: `app/prompts/organizer.md` — 원본 GPT 사양. 보기/메뉴/우선순위 규칙은
>   대부분 코드(`views.py`/`menu.py`)로 구현돼 있어 런타임에 프롬프트로 주입되지 않는다.
> - KST 현재 날짜는 **서버에서 계산해 추출 프롬프트에 주입**하므로 LLM이 날짜를 추측하지 않는다.

## 아키텍처 (하이브리드)

| 레이어 | 담당 | 누가 |
|---|---|---|
| 추출 | 자유 문장 → 구조화된 연산(add/complete/update/delete) + 항목 속성 | LLM (`llm/extract.py`) |
| 상태(SoT) | 항목 저장, 날짜 환산, 충돌 감지, 정렬, 보기 필터 | 코드 (`store.py`, `views.py`, `timeutil.py`) |
| 보기/메뉴 | 캘린더·분류·프로젝트·대시보드·확인 보기, 고정 빠른 메뉴 | 코드 (`views.py`, `menu.py`) — **LLM 미사용** |

→ 메뉴/보기 전환은 LLM을 안 거치므로 **즉시·무료·일관**. 날짜·충돌은 코드라 **정확**. 항목은 SQLite로 **영속**.

```
app/
  main.py        /api/chat(JSON) + /api/chat/stream(SSE) + /api/view + /api/items/{id}/toggle
  timeutil.py    KST 날짜 유틸 (단일 출처)
  models.py      Item 모델 + LLM dict→Item 안전 변환(coerce_item/coerce_changes)
  store.py       SQLite 저장소 (진실의 원천) — 단일 트랜잭션 apply_batch, WAL
  views.py       결정론적 보기 렌더러 + 충돌 감지(§16) + 반복 일정 펼침
  recurrence.py  반복 규칙 파서(매일/평일/주말/매주 요일/매월 N일) → occurrence 생성
  menu.py        빠른 메뉴 라벨 → 보기 함수 라우팅
  llm/
    codex_oauth.py  ~/.codex/auth.json 읽기 + access_token 자동 갱신
    client.py       Codex Responses(SSE) complete/complete_stream / OPENAI_API_KEY 폴백
    extract.py      문장→연산 추출(stream: note 실시간) + 스토어 적용
  prompts/organizer.md  제품 동작 명세(참조용)
  templates/index.html, static/{app.js,style.css}  채팅 + 고정 빠른 메뉴
```

- **스트리밍**: 추출 프롬프트는 `친근한 수신확인 + ===JSON=== + JSON`을 출력한다. 서버는 모델 델타를
  스트리밍하며 수신확인 부분만 `note` 이벤트로 흘려보내고, JSON을 파싱·적용한 뒤 `view` 이벤트로 보드를 보낸다.

- 빠른 메뉴 버튼(☀️ 오늘 / 📊 대시보드 / 🔄 날짜 갱신 …)을 누르면 그 라벨이 사용자 입력으로 전송되고, 서버가 LLM 없이 해당 보기를 렌더한다.
- KST 현재 날짜는 서버에서 계산해 LLM 지시문에 주입한다(날짜 추측 방지).
- **UI**: 서버는 구조화된 view(JSON: 섹션+항목)를 내려주고, 프런트가 그룹을 **톤별 카드**로 렌더한다
  (날짜/날짜미정/반복/충돌/중요/마감 등 색상 구분). 헤더의 ◐ 버튼으로 **라이트/다크 토글**(localStorage 저장).

## 실행

```bash
cd ~/projects/LLM_TO_DO
uv sync

# LLM 인증 — 둘 중 하나
codex login                     # 권장: ChatGPT OAuth → ~/.codex/auth.json
# 또는
export OPENAI_API_KEY=sk-...

uv run uvicorn app.main:app --reload
# http://127.0.0.1:8000
```

## Codex OAuth 실연결 메모 (실측)

ChatGPT 계정 백엔드(`https://chatgpt.com/backend-api/codex/responses`)는 표준
`chat/completions`가 아니라 **스트리밍 Responses API**만 받는다. 동작에 필요했던 조건:

- 헤더 `originator: codex_cli_rs` — **없으면 모든 모델이 "not supported"로 거부됨** (핵심)
- 헤더 `chatgpt-account-id`
- body: `instructions`(시스템), `store=false`, `stream=true`
- `input` content type: user→`input_text`, assistant→`output_text`
- SSE의 `response.output_text.delta`를 모아 본문 구성
- 지원 모델: `gpt-5.4-mini`(기본) / `gpt-5.4` / `gpt-5.5` (계정 플랜에 따라 다를 수 있음)

`~/.codex/auth.json`의 `access_token`을 읽어 쓰며, JWT `exp`가 임박하면 `refresh_token`으로
자동 갱신 후 파일에 다시 저장한다. (`app/llm/client.py`, `codex_oauth.py`)

## TODO (다음 단계)

- [x] `codex login` 후 실제 LLM 호출 검증 — 완료(라이브 동작 확인)
- [x] 응답 스트리밍을 프런트까지 전달 — `/api/chat/stream`(SSE), 수신 확인 문장 실시간 표시
- [x] 반복 일정 인스턴스화(§15) — 매일/평일/주말/매주 요일/매월 N일을 기간 보기에서 펼침(`recurrence.py`)
- [ ] 빠른 메뉴 응답의 마크다운(표/구분선) 렌더링
- [ ] 프로젝트 분해(§12) — `parent_id`/`sort_order` 모델
- [ ] 격주·첫째 주·마지막 N요일 등 고급 반복 규칙
- [ ] 대화 세션 분리(현재 단일 전역 상태)
