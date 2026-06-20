// 대화 기록은 클라이언트가 보관. 상태(항목)는 서버 SQLite가 진실의 원천.
const history = [];
let busy = false; // in-flight 가드: 중복 전송/동시 DB 변경 방지

const chatEl = document.getElementById("chat");
const form = document.getElementById("form");
const input = document.getElementById("input");
const sendBtn = document.getElementById("send");
const menuEl = document.getElementById("menu");

// ── 테마(라이트/다크) ──
const saved = localStorage.getItem("gptodo-theme");
if (saved) document.documentElement.dataset.theme = saved;
document.getElementById("theme").addEventListener("click", () => {
  const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  localStorage.setItem("gptodo-theme", next);
});

fetch("/api/today")
  .then((r) => r.json())
  .then((d) => (document.getElementById("today").textContent = d.date_header))
  .catch(() => {});

function renderAssistantView(view) {
  const wrap = el("div", "msg assistant");
  wrap.appendChild(renderView(view));
  chatEl.appendChild(wrap);
}

function showWelcome() {
  const wrap = el("div", "msg assistant");
  const w = el("div", "welcome");
  w.innerHTML = "아무렇게나 적어도 캘린더로 정리해드려요.<br>예) 내일 3시 면접, 저녁에 장보기, 보고서도 써야 해";
  wrap.appendChild(w);
  chatEl.appendChild(wrap);
}

// 초기 로드: 저장된 대화 기록을 복원, 없으면 현재 보드만 렌더
fetch("/api/messages")
  .then((r) => r.json())
  .then((d) => {
    if (d.messages && d.messages.length) {
      for (const m of d.messages) {
        if (m.role === "user") addUser(m.content);
        else if (m.view) renderAssistantView(m.view);
        history.push({ role: m.role, content: m.content });
      }
      chatEl.scrollTop = chatEl.scrollHeight;
    } else {
      fetch("/api/view")
        .then((r) => r.json())
        .then((v) => {
          if (v.view.sections.some((s) => s.items.length)) renderAssistantView(v.view);
          else showWelcome();
        })
        .catch(showWelcome);
    }
  })
  .catch(() => {});

// 구글 캘린더 양방향 동기화
document.getElementById("sync").addEventListener("click", async () => {
  const btn = document.getElementById("sync");
  const st = await fetch("/api/sync/status").then((r) => r.json()).catch(() => null);
  if (!st) return;
  if (!st.configured) {
    alert(
      "구글 양방향 연동 설정이 필요해요:\n\n" +
      "1. Google Cloud Console에서 OAuth 클라이언트(데스크톱/웹) 생성\n" +
      "2. 승인된 리디렉션 URI에 " + location.origin + "/oauth/google/callback 추가\n" +
      "3. 서버 실행 시 환경변수 GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET 설정\n\n" +
      "그 다음 다시 🔄 를 누르면 구글 로그인으로 연결됩니다."
    );
    return;
  }
  if (!st.authed) {
    location.href = "/oauth/google/start"; // 구글 로그인으로
    return;
  }
  btn.classList.add("spin");
  try {
    const res = await fetch("/api/sync", { method: "POST" });
    const d = await res.json();
    if (!res.ok) { alert("동기화 실패: " + (d.detail || "")); return; }
    const wrap = el("div", "msg assistant");
    wrap.appendChild(renderView(d.view));
    chatEl.appendChild(wrap);
    chatEl.scrollTop = chatEl.scrollHeight;
    const c = d.counts;
    alert(`동기화 완료\n올림 ${c.pushed || 0} · 받음 ${(c.created || 0) + (c.updated || 0)} · 삭제 ${c.deleted || 0}`);
  } catch (e) {
    alert("동기화 오류: " + e.message);
  } finally {
    btn.classList.remove("spin");
  }
});

// 캘린더 구독 — 피드 URL 복사 + 안내
document.getElementById("subscribe").addEventListener("click", async () => {
  const url = location.origin + "/calendar.ics";
  try { await navigator.clipboard.writeText(url); } catch {}
  alert(
    "캘린더 구독 URL을 복사했어요:\n" + url + "\n\n" +
    "• 애플 캘린더: 파일 → 새로운 캘린더 구독 → 붙여넣기\n" +
    "• 구글 캘린더: 다른 캘린더 + → URL로 추가 → 붙여넣기\n\n" +
    "구독하면 일정·마감이 자동으로 동기화됩니다(단방향)."
  );
});

// 대화 비우기(항목/프로필은 유지)
document.getElementById("clear").addEventListener("click", async () => {
  if (!confirm("대화 기록을 비울까요? (할 일/일정 항목은 그대로 유지됩니다)")) return;
  await fetch("/api/messages/clear", { method: "POST" }).catch(() => {});
  history.length = 0;
  chatEl.innerHTML = "";
  showWelcome();
});

// ── 헬퍼 ──
function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
}

function addUser(text) {
  const wrap = el("div", "msg user");
  wrap.appendChild(el("div", "chip", text));
  chatEl.appendChild(wrap);
  chatEl.scrollTop = chatEl.scrollHeight;
}

function priorityDot(p) {
  if (p !== "very_high" && p !== "high") return null;
  return el("span", "dot " + (p === "very_high" ? "p-vhigh" : "p-high"));
}

function itemRow(it) {
  const row = el("div", "item" + (it.done ? " done" : ""));
  if (it.depth) row.style.marginLeft = it.depth * 1.1 + "rem"; // §12 하위 단계 들여쓰기
  // 완료 토글 체크(클릭 → 완료/되살리기)
  const check = el("span", "check", it.done ? "✓" : "○");
  if (it.id != null) {
    check.dataset.id = it.id;
    check.dataset.done = it.done ? "1" : "0";
    check.title = it.done ? "미완료로 되돌리기" : "완료 처리";
  }
  row.appendChild(check);
  if (it.time) row.appendChild(el("span", "time", it.time));
  if (it.date) row.appendChild(el("span", "date-label", it.date));
  const dot = priorityDot(it.priority);
  if (dot) row.appendChild(dot);
  // 제목(클릭 시 인라인 수정)
  const title = el("span", "t", it.title);
  if (it.id != null) {
    title.dataset.id = it.id;
    title.title = "클릭해서 제목 수정";
  }
  row.appendChild(title);
  if (it.recurrence) row.appendChild(el("span", "badge", "🔁 " + it.recurrence));
  if (it.deadline) row.appendChild(el("span", "badge", "📌 " + it.deadline));
  if (it.estimate) row.appendChild(el("span", "badge", "⏱ " + it.estimate));
  if (it.location) row.appendChild(el("span", "badge", "@" + it.location));
  if (it.note) row.appendChild(el("span", "note", it.note));
  // 캘린더 연동: 구글 추가 링크 + .ics 다운로드
  if (it.cal) {
    const g = el("a", "cal-btn", "G");
    g.href = it.cal.gcal; g.target = "_blank"; g.rel = "noopener"; g.title = "구글 캘린더에 추가";
    const ics = el("a", "cal-btn", "↓ics");
    ics.href = it.cal.ics; ics.title = "애플/아웃룩 등 .ics 다운로드";
    row.appendChild(g);
    row.appendChild(ics);
  }
  // 삭제 버튼
  if (it.id != null) {
    const del = el("span", "del", "✕");
    del.dataset.id = it.id;
    del.title = "삭제";
    row.appendChild(del);
  }
  return row;
}

function section(sec) {
  const card = el("div", "card tone-" + sec.tone);
  if (sec.label) card.appendChild(el("div", "card-h", sec.label));
  for (const line of sec.lines) card.appendChild(el("div", "line", line));
  for (const it of sec.items) {
    if (it.divider) card.appendChild(el("div", "divider", it.divider));
    card.appendChild(itemRow(it));
  }
  return card;
}

function renderView(view) {
  const box = el("div", "view");
  const head = el("div", "view-h");
  head.appendChild(el("span", "v-title", view.title));
  head.appendChild(el("span", "v-date", view.date));
  box.appendChild(head);
  if (view.note) box.appendChild(el("div", "note-line", view.note));
  for (const sec of view.sections) box.appendChild(section(sec));
  if (view.questions && view.questions.length) {
    const q = el("div", "card tone-ask");
    q.appendChild(el("div", "card-h", "확인이 필요해요"));
    view.questions.forEach((text, i) => q.appendChild(el("div", "line", `${i + 1}. ${text}`)));
    box.appendChild(q);
  }
  return box;
}

function setBusy(v) {
  busy = v;
  sendBtn.disabled = v;
  menuEl.classList.toggle("disabled", v);
}

async function send(text) {
  text = (text || "").trim();
  if (!text || busy) return;
  addUser(text);
  history.push({ role: "user", content: text });
  input.value = "";
  autosize();

  const wrap = el("div", "msg assistant pending");
  const live = el("div", "welcome", "정리하는 중…");
  wrap.appendChild(live);
  chatEl.appendChild(wrap);
  chatEl.scrollTop = chatEl.scrollHeight;
  setBusy(true);

  try {
    const res = await fetch("/api/chat/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages: history }),
    });
    if (!res.ok || !res.body) {
      const data = await res.json().catch(() => ({}));
      finishError(wrap, "⚠️ " + (data.detail || "오류가 발생했어요."));
      return;
    }
    await consumeSSE(res.body, (obj) => {
      if (obj.type === "note") {
        if (obj.text) live.textContent = obj.text;
      } else if (obj.type === "error") {
        finishError(wrap, "⚠️ " + (obj.detail || "오류가 발생했어요."));
      } else if (obj.type === "view") {
        wrap.innerHTML = "";
        wrap.classList.remove("pending");
        wrap.appendChild(renderView(obj.view));
        history.push({ role: "assistant", content: assistantContext(obj.view) });
      }
      chatEl.scrollTop = chatEl.scrollHeight;
    });
  } catch (e) {
    finishError(wrap, "⚠️ 네트워크 오류: " + e.message);
  } finally {
    setBusy(false);
    chatEl.scrollTop = chatEl.scrollHeight;
  }
}

function finishError(wrap, msg) {
  wrap.innerHTML = "";
  wrap.classList.remove("pending");
  wrap.appendChild(el("div", "error", msg));
}

// SSE(`data: {...}\n\n`)를 fetch 스트림에서 파싱
async function consumeSSE(body, onEvent) {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buf.indexOf("\n\n")) !== -1) {
      const chunk = buf.slice(0, idx).trim();
      buf = buf.slice(idx + 2);
      if (chunk.startsWith("data:")) {
        try { onEvent(JSON.parse(chunk.slice(5).trim())); } catch {}
      }
    }
  }
}

function assistantContext(view) {
  const parts = [view.note || view.title];
  if (view.questions && view.questions.length) parts.push("질문: " + view.questions.join(" / "));
  return parts.join(" ");
}

// 항목 직접 조작 — 완료/되살리기 토글, 삭제, 제목 수정 (낙관적 갱신)
chatEl.addEventListener("click", async (e) => {
  const check = e.target.closest(".check[data-id]");
  if (check) {
    const id = check.dataset.id;
    const wasDone = check.dataset.done === "1";
    const row = check.closest(".item");
    row.classList.toggle("done", !wasDone);
    check.textContent = wasDone ? "○" : "✓";
    check.dataset.done = wasDone ? "0" : "1";
    try {
      const res = await fetch(`/api/items/${id}/toggle`, { method: "POST" });
      if (!res.ok) throw new Error();
    } catch {
      row.classList.toggle("done", wasDone);
      check.textContent = wasDone ? "✓" : "○";
      check.dataset.done = wasDone ? "1" : "0";
    }
    return;
  }

  const del = e.target.closest(".del[data-id]");
  if (del) {
    const row = del.closest(".item");
    const title = row.querySelector(".t")?.textContent || "이 항목";
    if (!confirm(`'${title}' 삭제할까요?`)) return;
    row.style.opacity = "0.4";
    try {
      const res = await fetch(`/api/items/${del.dataset.id}/delete`, { method: "POST" });
      if (!res.ok) throw new Error();
      row.remove();
    } catch {
      row.style.opacity = "";
    }
    return;
  }
});

// 제목 클릭 → 인라인 수정
chatEl.addEventListener("click", (e) => {
  const t = e.target.closest(".t[data-id]");
  if (!t || t.isContentEditable) return;
  const original = t.textContent;
  t.contentEditable = "true";
  t.classList.add("editing");
  t.focus();
  // 캐럿 끝으로
  const range = document.createRange();
  range.selectNodeContents(t);
  range.collapse(false);
  const sel = getSelection();
  sel.removeAllRanges();
  sel.addRange(range);

  const finish = async (save) => {
    t.contentEditable = "false";
    t.classList.remove("editing");
    const next = t.textContent.trim();
    if (!save || !next || next === original) {
      t.textContent = original;
      return;
    }
    try {
      const res = await fetch(`/api/items/${t.dataset.id}/update`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ changes: { title: next } }),
      });
      if (!res.ok) throw new Error();
    } catch {
      t.textContent = original;
    }
  };
  t.onkeydown = (ev) => {
    if (ev.key === "Enter") { ev.preventDefault(); t.blur(); }
    if (ev.key === "Escape") { t.textContent = original; t.blur(); }
  };
  t.onblur = () => finish(true);
});

form.addEventListener("submit", (e) => {
  e.preventDefault();
  send(input.value);
});

menuEl.addEventListener("click", (e) => {
  const btn = e.target.closest("button");
  if (btn && !busy) send(btn.dataset.cmd || btn.textContent);
});

function autosize() {
  input.style.height = "auto";
  input.style.height = Math.min(input.scrollHeight, 160) + "px";
}
input.addEventListener("input", autosize);
input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    send(input.value);
  }
});
