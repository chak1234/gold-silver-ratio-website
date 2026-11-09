import { fetchJson } from "./api.js";

const byId = (id) => document.getElementById(id);

/** Create an element whose text is always inserted as text, never as HTML. */
function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function addMessage(kind, children) {
  const log = byId("chat-log");
  const message = element("div", undefined, `chat__message chat__message--${kind}`);
  message.append(...children);
  log.append(message);
  log.scrollTop = log.scrollHeight;
}

function addReply(reply) {
  const parts = [element("h3", reply.title)];
  if (reply.rows.length) {
    const rows = element("dl", undefined, "chat__rows");
    for (const [label, value] of reply.rows) {
      const row = element("div");
      row.append(element("dt", label), element("dd", value));
      rows.append(row);
    }
    parts.push(rows);
  }
  parts.push(...reply.paragraphs.map((text) => element("p", text)));
  if (reply.note) parts.push(element("p", reply.note, "chat__note"));
  addMessage("bot", parts);
}

export function initChat() {
  const panel = byId("chat-panel");
  const toggle = byId("chat-toggle");
  const input = byId("chat-input");
  const form = byId("chat-form");
  const send = form.querySelector("button");
  let pending = false;

  function setOpen(open) {
    panel.hidden = !open;
    toggle.hidden = open;
    toggle.setAttribute("aria-expanded", String(open));
    (open ? input : toggle).focus();
  }

  async function ask(question) {
    if (pending) return;
    pending = true;
    send.disabled = true;
    addMessage("user", [element("p", question)]);
    try {
      addReply(
        await fetchJson("/api/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ question }),
        }),
      );
    } catch {
      addMessage("bot", [element("p", "Sorry, something went wrong. Please try again.")]);
    } finally {
      pending = false;
      send.disabled = false;
    }
  }

  addMessage("bot", [
    element("p", "Ask about prices, the ratio, currency conversions, or what an amount buys."),
  ]);

  toggle.addEventListener("click", () => setOpen(true));
  byId("chat-close").addEventListener("click", () => setOpen(false));
  panel.addEventListener("keydown", (event) => {
    if (event.key === "Escape") setOpen(false);
  });
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const question = input.value.trim();
    if (question && !pending) {
      input.value = "";
      ask(question);
    }
  });
  for (const button of document.querySelectorAll("[data-question]")) {
    button.addEventListener("click", () => ask(button.dataset.question));
  }
}
