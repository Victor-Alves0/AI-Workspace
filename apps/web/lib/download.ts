import type { Chat, Message } from "./types";

function triggerDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

function slug(s: string) {
  return (s || "chat").replace(/[^\w\-]+/g, "_").slice(0, 40);
}

export function downloadJSON(chat: Chat, messages: Message[]) {
  const data = {
    title: chat.title,
    model: chat.model,
    system_prompt: chat.system_prompt,
    messages: messages.map((m) => ({ role: m.role, content: m.content })),
  };
  triggerDownload(
    new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }),
    `${slug(chat.title)}.json`,
  );
}

export function downloadTXT(chat: Chat, messages: Message[]) {
  const lines = messages.map(
    (m) => `${m.role === "user" ? "Você" : m.role === "assistant" ? "IA" : m.role}:\n${m.content}\n`,
  );
  const text = `# ${chat.title}\nModelo: ${chat.model}\n\n${lines.join("\n")}`;
  triggerDownload(new Blob([text], { type: "text/plain;charset=utf-8" }), `${slug(chat.title)}.txt`);
}

export function downloadPDF(chat: Chat, messages: Message[]) {
  // Sem dependência: abre uma janela imprimível e usa "Salvar como PDF".
  const esc = (s: string) =>
    s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const body = messages
    .map(
      (m) =>
        `<div class="msg ${m.role}"><div class="role">${
          m.role === "user" ? "Você" : m.role === "assistant" ? "IA" : m.role
        }</div><div class="content">${esc(m.content)}</div></div>`,
    )
    .join("");
  const html = `<!doctype html><html><head><meta charset="utf-8"><title>${esc(
    chat.title,
  )}</title><style>
    body{font-family:system-ui,sans-serif;max-width:720px;margin:32px auto;color:#111}
    h1{font-size:20px} .meta{color:#666;font-size:12px;margin-bottom:24px}
    .msg{margin:16px 0;padding:12px 16px;border-radius:12px;white-space:pre-wrap}
    .user{background:#eef0ff} .assistant{background:#f4f4f4}
    .role{font-size:11px;text-transform:uppercase;color:#888;margin-bottom:4px}
  </style></head><body><h1>${esc(chat.title)}</h1><div class="meta">Modelo: ${esc(
    chat.model,
  )}</div>${body}<script>window.onload=()=>window.print()</script></body></html>`;
  const w = window.open("", "_blank");
  if (w) {
    w.document.write(html);
    w.document.close();
  }
}
