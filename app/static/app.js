// 대화 기록은 클라이언트가 보관. 상태(항목)는 서버 SQLite가 진실의 원천.
const history = [];

const chatEl = document.getElementById("chat");
const form = document.getElementById("form");
const input = document.getElementById("input");
const sendBtn = document.getElementById("send");

fetch("/api/today")
  .then((r) => r.json())
  .then((d) => (document.getElementById("today").textContent = "📅 " + d.date_header))
  .catch(() => {});

function addBubble(role, text) {
  const wrap = document.createElement("div");
  wrap.className = "msg " + role;
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = text; // pre-wrap CSS가 줄바꿈/공백 보존
  wrap.appendChild(bubble);
  chatEl.appendChild(wrap);
  chatEl.scrollTop = chatEl.scrollHeight;
  return bubble;
}

async function send(text) {
  text = (text || "").trim();
  if (!text) return;

  addBubble("user", text);
  history.push({ role: "user", content: text });
  input.value = "";
  autosize();

  const pending = addBubble("assistant", "정리하는 중…");
  pending.parentElement.classList.add("pending");
  sendBtn.disabled = true;

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages: history }),
    });
    const data = await res.json();
    pending.parentElement.classList.remove("pending");
    if (!res.ok) {
      pending.textContent = "⚠️ " + (data.detail || "오류가 발생했어요.");
      return;
    }
    pending.textContent = data.reply;
    history.push({ role: "assistant", content: data.reply });
  } catch (e) {
    pending.parentElement.classList.remove("pending");
    pending.textContent = "⚠️ 네트워크 오류: " + e.message;
  } finally {
    sendBtn.disabled = false;
    chatEl.scrollTop = chatEl.scrollHeight;
  }
}

form.addEventListener("submit", (e) => {
  e.preventDefault();
  send(input.value);
});

// 빠른 메뉴 클릭 → 라벨 텍스트를 그대로 전송(서버에서 메뉴로 인식)
document.getElementById("menu").addEventListener("click", (e) => {
  if (e.target.tagName === "BUTTON") send(e.target.textContent);
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
