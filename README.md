# GPTODO

아무렇게나 적어도 LLM이 **캘린더 중심**으로 정리해주는 채팅 웹앱.
일정·할 일·메모·아이디어를 구조화하고, 빠른 메뉴로 다양한 보기(오늘/이번 주/분류/프로젝트/대시보드)로 재구성한다.

LLM 백엔드는 **Codex / ChatGPT OAuth**(`codex login`)를 우선 사용하고, 없으면 `OPENAI_API_KEY`로 폴백한다.

> 핵심 동작 규칙은 전부 `app/prompts/organizer.md` 시스템 프롬프트에 들어 있다.
> KST 현재 날짜는 **서버에서 계산해 주입**하므로 LLM이 날짜를 추측하지 않는다.

## 아키텍처 (하이브리드)

| 레이어 | 담당 | 누가 |
|---|---|---|
| 추출 | 자유 문장 → 구조화된 연산(add/complete/update/delete) + 항목 속성 | LLM (`llm/extract.py`) |
| 상태(SoT) | 항목 저장, 날짜 환산, 충돌 감지, 정렬, 보기 필터 | 코드 (`store.py`, `views.py`, `timeutil.py`) |
| 보기/메뉴 | 캘린더·분류·프로젝트·대시보드·확인 보기, 고정 빠른 메뉴 | 코드 (`views.py`, `menu.py`) — **LLM 미사용** |

→ 메뉴/보기 전환은 LLM을 안 거치므로 **즉시·무료·일관**. 날짜·충돌은 코드라 **정확**. 항목은 SQLite로 **영속**.

```
app/
  main.py        메뉴면 즉시 렌더, 자유 문장이면 LLM 추출→연산 적용→캘린더 렌더
  timeutil.py    KST 날짜 유틸 (단일 출처)
  models.py      Item 모델 + LLM dict→Item 안전 변환
  store.py       SQLite 저장소 (진실의 원천)
  views.py       결정론적 보기 렌더러 + 충돌 감지(§16)
  menu.py        빠른 메뉴 라벨 → 보기 함수 라우팅
  llm/
    codex_oauth.py  ~/.codex/auth.json 읽기 + access_token 자동 갱신
    client.py       Codex Responses(SSE) 호출 / OPENAI_API_KEY 폴백
    extract.py      문장→연산 추출 + 스토어 적용
  prompts/organizer.md  제품 동작 명세(참조용)
  templates/index.html, static/{app.js,style.css}  채팅 + 고정 빠른 메뉴
```

- 빠른 메뉴 버튼(☀️ 오늘 / 📊 대시보드 / 🔄 날짜 갱신 …)을 누르면 그 라벨이 사용자 입력으로 전송되고, 서버가 LLM 없이 해당 보기를 렌더한다.
- KST 현재 날짜는 서버에서 계산해 LLM 지시문에 주입한다(날짜 추측 방지).

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
- [ ] 응답 스트리밍을 프런트까지 전달(SSE 프록시) — 긴 정리도 끊김 없이
- [ ] 빠른 메뉴 응답의 마크다운(표/구분선) 렌더링
- [ ] 프로젝트 분해(§12)·반복 일정 인스턴스화(§15) 보강
- [ ] 대화 세션 분리(현재 단일 전역 상태)
