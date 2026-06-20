# 🧠 지능형 정리사 (LLM TO-DO)

아무렇게나 적어도 LLM이 **캘린더 중심**으로 정리해주는 채팅 웹앱.
일정·할 일·메모·아이디어를 구조화하고, 빠른 메뉴로 다양한 보기(오늘/이번 주/분류/프로젝트/대시보드)로 재구성한다.

LLM 백엔드는 **Codex / ChatGPT OAuth**(`codex login`)를 우선 사용하고, 없으면 `OPENAI_API_KEY`로 폴백한다.

> 핵심 동작 규칙은 전부 `app/prompts/organizer.md` 시스템 프롬프트에 들어 있다.
> KST 현재 날짜는 **서버에서 계산해 주입**하므로 LLM이 날짜를 추측하지 않는다.

## 구조

```
app/
  main.py              FastAPI — / (채팅 UI), POST /api/chat, /api/today, /health
  prompts/organizer.md '지능형 정리사' 시스템 프롬프트 (캘린더식 출력 + 빠른 메뉴 규칙)
  llm/
    codex_oauth.py     ~/.codex/auth.json 읽기 + access_token 자동 갱신
    client.py          KST 날짜 주입 + 대화 호출 (chat)
  templates/index.html 채팅 + 고정 빠른 메뉴
  static/app.js,style.css
```

- **무상태 서버**: 대화 기록은 브라우저가 보관하고 매 요청마다 `messages`로 전송한다
  (프롬프트 §19 "현재 대화 기준" 원칙 — 외부 저장 없음).
- 빠른 메뉴 버튼(☀️ 오늘 / 📊 대시보드 / 🔄 날짜 갱신 …)을 누르면 그 라벨이 그대로 사용자 입력으로 전송된다.

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

## 동작 메모

- **KST 날짜 주입**: `client._system_prompt()`가 `Asia/Seoul` 현재 날짜·요일을 계산해
  시스템 메시지 상단에 넣는다. 상대 날짜(내일/이번 주 등)는 이 값을 기준으로 환산된다.
- **Codex OAuth**: `~/.codex/auth.json`의 `access_token`을 읽어 쓰며, JWT `exp`가 임박하면
  `refresh_token`으로 자동 갱신 후 파일에 다시 저장한다.
- ChatGPT OAuth 엔드포인트/모델명은 Codex 버전에 따라 바뀔 수 있어
  `LLM_BASE_URL`, `LLM_MODEL` 환경변수로 오버라이드 가능하게 분리해 두었다.

## TODO (다음 단계)

- [ ] `codex login` 후 실제 LLM 호출 검증 (OAuth 엔드포인트/모델명 실연결)
- [ ] 응답 스트리밍(SSE) — 긴 정리도 끊김 없이
- [ ] 빠른 메뉴 응답의 마크다운(표/구분선) 렌더링
- [ ] 선택적 대화 영속화 (현재는 새로고침 시 초기화)
