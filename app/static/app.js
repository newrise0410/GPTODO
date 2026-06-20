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

// 초기 로드: 저장된 항목이 있으면 보드를 바로 렌더
fetch("/api/view")
  .then((r) => r.json())
  .then((d) => {
    const has = d.view.sections.some((s) => s.items.length);
    if (has) {
      const wrap = el("div", "msg assistant");
      wrap.appendChild(renderView(d.view));
      chatEl.appendChild(wrap);
      chatEl.scrollTop = chatEl.scrollHeight;
    }
  })
  .catch(() => {});

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
  // 완료 토글 체크(클릭 가능)
  const check = el("span", "check", it.done ? "✓" : "○");
  if (it.id != null) {
    check.dataset.id = it.id;
    check.dataset.done = it.done ? "1" : "0";
    check.title = it.done ? "미완료로 되돌리기" : "완료 처리";
  }
  row.appendChild(check);
  if (it.time) row.appendChild(el("span", "time", it.time));
  const dot = priorityDot(it.priority);
  if (dot) row.appendChild(dot);
  const title = el("span", "t");
  title.textContent = (it.date ? it.date + "  " : "") + it.title;
  row.appendChild(title);
  if (it.recurrence) row.appendChild(el("span", "badge", "🔁 " + it.recurrence));
  if (it.deadline) row.appendChild(el("span", "badge", "📌 " + it.deadline));
  if (it.estimate) row.appendChild(el("span", "badge", "⏱ " + it.estimate));
  if (it.location) row.appendChild(el("span", "badge", "@" + it.location));
  if (it.note) row.appendChild(el("span", "note", it.note));
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

// 완료 토글 — 클릭 행만 낙관적으로 갱신(서버는 영속화). 실패 시 되돌림.
chatEl.addEventListener("click", async (e) => {
  const check = e.target.closest(".check[data-id]");
  if (!check) return;
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
    row.classList.toggle("done", wasDone); // 되돌림
    check.textContent = wasDone ? "✓" : "○";
    check.dataset.done = wasDone ? "1" : "0";
  }
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
