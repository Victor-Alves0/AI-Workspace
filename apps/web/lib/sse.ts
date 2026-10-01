import { API_URL, ApiError, refreshSession } from "./api";
import { trServer } from "./i18n";
import { beacon } from "./trace";
import type { ChatEvent } from "./types";

// fuso IANA do navegador (ex.: "America/Sao_Paulo") — o backend usa p/ dar ao
// modelo a hora local e p/ criar lembretes/eventos de agenda no fuso certo.
function browserTz(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "";
  } catch {
    return "";
  }
}

// fetch com refresh-on-401: se a sessão expirou, renova e repete uma vez.
async function authedFetch(path: string, init: RequestInit): Promise<Response> {
  const headers = new Headers(init.headers);
  const tz = browserTz();
  if (tz) headers.set("X-Timezone", tz);
  const opts: RequestInit = { ...init, headers, credentials: "include" };
  let res = await fetch(`${API_URL}${path}`, opts);
  if (res.status === 401) {
    if (await refreshSession()) {
      res = await fetch(`${API_URL}${path}`, opts);
    }
  }
  return res;
}

/** Normaliza o `detail` de um erro do FastAPI para texto legível. Um 422 devolve uma
 *  LISTA de objetos {loc,msg,type} — jogá-la crua num Error vira "[object Object]". */
function errDetail(d: unknown, fallback: string): string {
  if (typeof d === "string") return d || fallback;
  if (Array.isArray(d)) {
    const parts = d.map((e) => (typeof e === "string" ? e : (e as { msg?: string })?.msg || JSON.stringify(e)));
    return parts.join("; ") || fallback;
  }
  if (d && typeof d === "object") return (d as { msg?: string }).msg || JSON.stringify(d);
  return fallback;
}

async function readSSE(res: Response, onEvent: (e: ChatEvent) => void): Promise<void> {
  if (!res.ok || !res.body) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = errDetail(body?.detail ?? body?.message, res.statusText);
    } catch {
      /* ignore */
    }
    // Um 413/4xx acontece ANTES de o stream começar. Além de informar a UI,
    // propague-o para quem enviou a mensagem: o composer precisa restaurar o
    // rascunho que tinha sido removido de forma otimista.
    onEvent({ type: "error", message: trServer(detail) });
    throw new ApiError(res.status, detail);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  const dispatch = (raw: string) => {
    // SSE permite LF, CRLF ou CR e também permite que um evento tenha várias
    // linhas `data:`. O parser anterior procurava apenas "\n\n": com um proxy
    // normalizando a resposta para CRLF, nenhum evento era entregue e a UI
    // parecia ficar eternamente travada no streaming.
    const data = raw
      .split(/\r\n|\r|\n/)
      .filter((line) => line.startsWith("data:"))
      .map((line) => line.slice(5).replace(/^ /, ""))
      .join("\n");
    if (!data) return;
    try {
      onEvent(JSON.parse(data) as ChatEvent);
    } catch {
      /* ignora payloads incompletos/malformados sem derrubar todo o stream */
    }
  };

  const drain = (flushTail = false) => {
    // Linha vazia encerra um evento. O tamanho do match importa porque CRLF usa
    // quatro bytes; avançar sempre dois deixava "\r\n" no início do próximo.
    const boundary = /\r\n\r\n|\n\n|\r\r/;
    let match = boundary.exec(buffer);
    while (match) {
      dispatch(buffer.slice(0, match.index));
      buffer = buffer.slice(match.index + match[0].length);
      match = boundary.exec(buffer);
    }
    // Alguns servidores/proxies fecham a conexão logo após a última linha sem
    // uma linha vazia final. No EOF, esse tail já não pode receber mais bytes.
    if (flushTail && buffer.trim()) dispatch(buffer);
    if (flushTail) buffer = "";
  };

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    drain();
  }
  buffer += decoder.decode();
  drain(true);
}

// Envia uma mensagem num chat persistido e lê o stream SSE.
export async function streamMessage(
  chatId: string,
  content: string,
  onEvent: (e: ChatEvent) => void,
  signal?: AbortSignal,
  skillIds?: string[],
  attachments?: unknown[],
  agentModelConfigId?: string | null,
  refDocIds?: string[],
  refChatIds?: string[],
  miniApp?: "imaginai" | "cyberlab" | null,
): Promise<{ queued: boolean }> {
  const t0 = performance.now();
  const res = await authedFetch(`/chats/${chatId}/messages`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      content, skill_ids: skillIds ?? [], attachments: attachments ?? [],
      ...(agentModelConfigId ? { agent_model_config_id: agentModelConfigId } : {}),
      ...(refDocIds && refDocIds.length ? { ref_doc_ids: refDocIds } : {}),
      ...(refChatIds && refChatIds.length ? { ref_chat_ids: refChatIds } : {}),
      ...(miniApp ? { mini_app: miniApp } : {}),
    }),
    signal,
  });
  // "do clique ao 'oi'": mede o tempo até o PRIMEIRO token e o correlaciona ao
  // trace do servidor (X-Trace-Id do próprio POST). Emite uma vez, no 1º token.
  const traceId = res.headers.get("X-Trace-Id");
  // o chat já estava gerando (ex.: continuação no servidor): o back guardou a mensagem
  // na fila dele e respondeu JSON em vez de abrir outro stream
  if (res.ok && (res.headers.get("Content-Type") || "").includes("application/json")) {
    const body = await res.json().catch(() => null);
    return { queued: !!body?.queued };
  }
  let firstTokenSent = false;
  await readSSE(res, (e) => {
    if (!firstTokenSent && e.type === "token") {
      firstTokenSent = true;
      beacon("chat-first-token", performance.now() - t0, { traceId, ttfbMs: performance.now() - t0 });
    }
    onEvent(e);
  });
  return { queued: false };
}

// Mesa-redonda: os participantes trabalham em equipe no pedido do usuário (`content`,
// opcional se já houver um). `steps` "one" = uma fala, "auto" = até concluir/pausar/teto.
// Com a mesa já rodando, `content` vira instrução p/ a próxima fala ({queued:true}).
export async function streamRoundtable(
  chatId: string,
  body: { content?: string; steps?: "one" | "auto"; next?: string | null; attachments?: unknown[]; skill_ids?: string[] },
  onEvent: (e: ChatEvent) => void,
  signal?: AbortSignal,
): Promise<{ queued: boolean }> {
  const res = await authedFetch(`/chats/${chatId}/roundtable/run`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      content: body.content ?? "", steps: body.steps ?? "auto", next: body.next ?? null,
      attachments: body.attachments ?? [], skill_ids: body.skill_ids ?? [],
    }),
    signal,
  });
  if (res.ok && (res.headers.get("content-type") || "").includes("application/json")) {
    const body = await res.json().catch(() => null);
    return { queued: !!body?.queued };
  }
  await readSSE(res, onEvent);
  return { queued: false };
}

// Re-assina uma geração em andamento (ex.: usuário deu F5 no meio da resposta).
// Se nada estiver gerando, o servidor emite {type:"idle"} e encerra.
export async function streamResume(
  chatId: string,
  onEvent: (e: ChatEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await authedFetch(`/chats/${chatId}/stream`, {
    method: "GET",
    credentials: "include",
    signal,
  });
  await readSSE(res, onEvent);
}

// Refaz a resposta do assistant (descarta a atual e as posteriores).
export async function streamRegenerate(
  chatId: string,
  messageId: string,
  onEvent: (e: ChatEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await authedFetch(`/chats/${chatId}/messages/${messageId}/regenerate`, {
    method: "POST",
    credentials: "include",
    signal,
  });
  await readSSE(res, onEvent);
}

// Continua a última resposta do assistant (anexa ao conteúdo existente).
export async function streamContinue(
  chatId: string,
  messageId: string,
  onEvent: (e: ChatEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await authedFetch(`/chats/${chatId}/messages/${messageId}/continue`, {
    method: "POST",
    credentials: "include",
    signal,
  });
  await readSSE(res, onEvent);
}

// Playground — Comparações: mesmo prompt em N modelos, streaming lado a lado.
// Eventos: {type:"cols",cols}, {col,type:"delta",text}, {col,type:"done",...}, {col,type:"error",message}, {type:"all_done"}.
export async function streamCompare(
  body: { models: { model: string; model_config_id?: string | null; label?: string }[]; prompt: string; system?: string },
  onEvent: (e: unknown) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await authedFetch(`/playground/compare`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  await readSSE(res, onEvent as (e: ChatEvent) => void);
}

// Playground — Debug de Tools (Trace): roda um turno com ferramentas e emite os
// eventos do orquestrador (tool_call / tool_result / token / done / error / notice).
export async function streamToolTrace(
  body: { model: string; model_config_id?: string | null; prompt: string },
  onEvent: (e: unknown) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await authedFetch(`/playground/tool/trace`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  await readSSE(res, onEvent as (e: ChatEvent) => void);
}

/** Caixa de texto do painel de um agente. Trabalhando → a mensagem entra no loop dele
 *  e volta `{mode: "steer"}`; já terminou → abre uma continuação, cujos eventos
 *  (`progress` / `done` / `error`) chegam em `onEvent`. */
export async function messageAgent(
  chatId: string,
  ref: string,
  body: { content: string; think?: boolean },
  onEvent: (e: Record<string, unknown>) => void,
  signal?: AbortSignal,
): Promise<"steer" | "followup"> {
  const res = await authedFetch(`/chats/${chatId}/agents/${encodeURIComponent(ref)}/message`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (res.ok && (res.headers.get("Content-Type") || "").includes("application/json")) {
    await res.json().catch(() => null);
    return "steer";
  }
  await readSSE(res, onEvent as unknown as (e: ChatEvent) => void);
  return "followup";
}

// Chat temporário: streama um turno sem persistir nada.
export async function streamEphemeral(
  body: {
    model: string;
    content: string;
    history?: { role: string; content: string }[];
    system_prompt?: string | null;
    params?: Record<string, unknown>;
    model_config_id?: string | null;
    skill_ids?: string[];
    attachments?: unknown[];
  },
  onEvent: (e: ChatEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await authedFetch(`/chats/ephemeral`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  await readSSE(res, onEvent);
}
