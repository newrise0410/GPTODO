# 🧠 LLM TO-DO

자연어로 적으면 LLM이 구조화된 할 일로 정리해주는 FastAPI 웹앱.
LLM 백엔드는 **Codex / ChatGPT OAuth**(`codex login`)를 우선 사용하고, 없으면 `OPENAI_API_KEY`로 폴백한다.

> 예: "내일까지 보고서 초안 쓰고, 다음주 월요일에 치과 예약" →
> ① 보고서 초안 (due: 내일) ② 치과 예약 (due: 다음주 월요일) 두 개의 할 일로 분리 저장.

## 구조

```
app/
  main.py          FastAPI 라우트 (/, /add, toggle, delete, /api/todos)
  db.py            SQLite 저장소 (stdlib sqlite3)
  llm/
    codex_oauth.py ~/.codex/auth.json 읽기 + 토큰 자동 갱신
    client.py      자연어 → 할 일 JSON 파싱
  templates/       Jinja2 (index.html)
  static/          style.css
data/todos.db      런타임 생성 (gitignore)
```

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

- **Codex OAuth**: `~/.codex/auth.json`의 `access_token`을 읽어 사용하며, JWT `exp`가 임박하면
  `refresh_token`으로 자동 갱신 후 파일에 다시 저장한다.
- ChatGPT OAuth 엔드포인트/모델명은 Codex 버전에 따라 바뀔 수 있어 `LLM_BASE_URL`,
  `LLM_MODEL` 환경변수로 오버라이드 가능하게 분리해 두었다.
- LLM 호출이 실패하면 입력 원문을 그대로 한 개의 할 일로 저장해 앱이 멈추지 않는다.

## TODO (다음 단계)

- [ ] 자연어로 "완료 처리/삭제/수정"까지 (현재는 추가만 LLM)
- [ ] 마감 임박 정렬·알림
- [ ] HTMX로 새로고침 없는 토글
- [ ] Codex OAuth 엔드포인트 실연결 검증
