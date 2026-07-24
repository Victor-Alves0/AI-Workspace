// Cliente HTTP fino. Sempre envia cookies (credentials: include) p/ a auth via cookie httpOnly.

/** Base da API. Prioridade:
 *  1. NEXT_PUBLIC_API_URL, se definida no build (setups avançados / domínio próprio).
 *  2. Mesmo host da página + porta 8000 — assim, abrir o app pelo IP da máquina
 *     (ex.: pelo celular na LAN) faz as chamadas irem para esse IP, não para o
 *     localhost do dispositivo. Local-first: a API mora no mesmo host do front.
 *  3. Fallback SSR: localhost:8000. */
function resolveApiUrl(): string {
  const env = (process.env.NEXT_PUBLIC_API_URL || "").trim();
  if (env) return env.replace(/\/$/, "");
  if (typeof window !== "undefined") {
    return `${window.location.protocol}//${window.location.hostname}:8000`;
  }
  return "http://localhost:8000";
}

export const API_URL = resolveApiUrl();

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

// Refresh-on-401: o access token expira em ~30min, mas o refresh token dura dias.
// Quando uma chamada volta 401, tentamos renovar a sessão UMA vez (deduplicado
// entre chamadas concorrentes) e repetimos a requisição. Só se o refresh também
// falhar é que o erro sobe (e o app manda pro login). Sem isto, o usuário era
// deslogado após ficar ocioso.
let refreshInFlight: Promise<boolean> | null = null;

async function tryRefresh(): Promise<boolean> {
  if (!refreshInFlight) {
    refreshInFlight = fetch(`${API_URL}/auth/refresh`, {
      method: "POST",
      credentials: "include",
    })
      .then((r) => r.ok)
      .catch(() => false)
      .finally(() => {
        // libera a próxima tentativa só no próximo tick
        setTimeout(() => (refreshInFlight = null), 0);
      });
  }
  return refreshInFlight;
}

// Último X-Trace-Id visto numa resposta — o tracer do cliente (lib/trace.ts) usa
// para correlacionar os tempos medidos no navegador com o trace do servidor.
let lastTraceId: string | null = null;

export function getLastTraceId(): string | null {
  return lastTraceId;
}

async function rawFetch(path: string, init: RequestInit): Promise<Response> {
  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    credentials: "include",
    headers: { "Content-Type": "application/json", ...(init.headers ?? {}) },
  });
  const tid = res.headers.get("X-Trace-Id");
  if (tid) lastTraceId = tid;
  return res;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let res = await rawFetch(path, init);

  // sessão expirada: renova e repete uma vez (exceto nas próprias rotas de auth)
  if (res.status === 401 && !path.startsWith("/auth/")) {
    if (await tryRefresh()) {
      res = await rawFetch(path, init);
    }
  }

  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

/** Renova a sessão sob demanda (usado pelo streaming SSE antes/depois de 401). */
export async function refreshSession(): Promise<boolean> {
  return tryRefresh();
}

export const api = {
  get: <T>(p: string) => request<T>(p),
  post: <T>(p: string, body?: unknown) =>
    request<T>(p, { method: "POST", body: body ? JSON.stringify(body) : undefined }),
  put: <T>(p: string, body?: unknown) =>
    request<T>(p, { method: "PUT", body: body ? JSON.stringify(body) : undefined }),
  patch: <T>(p: string, body?: unknown) =>
    request<T>(p, { method: "PATCH", body: body ? JSON.stringify(body) : undefined }),
  del: <T>(p: string) => request<T>(p, { method: "DELETE" }),
};
