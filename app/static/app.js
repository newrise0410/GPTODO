// 대화 기록은 클라이언트가 보관. 상태(항목)는 서버 SQLite가 진실의 원천.
const history = [];

const chatEl = document.getElementById("chat");
const form = document.getElementById("form");
const input = document.getElementById("input");
const sendBtn = document.getElementById("send");

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
  // 좌측: 시간칩 또는 체크
  if (it.time) row.appendChild(el("span", "time", it.time));
  else row.appendChild(el("span", "mark", it.done ? "✓" : "○"));
  const dot = priorityDot(it.priority);
  if (dot) row.appendChild(dot);
  // 제목
  const title = el("span", "t");
  title.textContent = (it.date ? it.date + "  " : "") + it.title;
  row.appendChild(title);
  // 배지
  if (it.recurrence) row.appendChild(el("span", "badge", "🔁 " + it.recurrence));
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

async function send(text) {
  text = (text || "").trim();
  if (!text) return;
  addUser(text);
  history.push({ role: "user", content: text });
  input.value = "";
  autosize();

  const wrap = el("div", "msg assistant pending");
  wrap.appendChild(el("div", "welcome", "정리하는 중…"));
  chatEl.appendChild(wrap);
  chatEl.scrollTop = chatEl.scrollHeight;
  sendBtn.disabled = true;

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages: history }),
    });
    const data = await res.json();
    wrap.innerHTML = "";
    wrap.classList.remove("pending");
    if (!res.ok) {
      wrap.appendChild(el("div", "error", "⚠️ " + (data.detail || "오류가 발생했어요.")));
      return;
    }
    wrap.appendChild(renderView(data.view));
    // 어시스턴트 컨텍스트는 간단한 요약으로 저장(토큰 절약)
    history.push({ role: "assistant", content: assistantContext(data.view) });
  } catch (e) {
    wrap.innerHTML = "";
    wrap.classList.remove("pending");
    wrap.appendChild(el("div", "error", "⚠️ 네트워크 오류: " + e.message));
  } finally {
    sendBtn.disabled = false;
    chatEl.scrollTop = chatEl.scrollHeight;
  }
}

// LLM 후속 맥락용 텍스트(질문 답변 등 해석 도움)
function assistantContext(view) {
  const parts = [view.note || view.title];
  if (view.questions && view.questions.length) parts.push("질문: " + view.questions.join(" / "));
  return parts.join(" ");
}

form.addEventListener("submit", (e) => {
  e.preventDefault();
  send(input.value);
});

document.getElementById("menu").addEventListener("click", (e) => {
  const btn = e.target.closest("button");
  if (btn) send(btn.dataset.cmd || btn.textContent);
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
