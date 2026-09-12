"use client";

import { useCallback, useEffect, useState } from "react";
import { Check, ChevronLeft, Copy, Crown, ExternalLink, Loader2, Trash2, TriangleAlert } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { copyText } from "@/lib/clipboard";
import { openExternal } from "@/lib/desktop";

interface ChatgptStatus {
  connected: boolean;
  email?: string;
  plan?: string;
  models?: string[];
}

interface DeviceAuthStart {
  url: string;
  user_code: string;
  interval: number;
}

/** Conexões → Assinaturas: usar o plano ChatGPT pelo login da conta (sem chave
 *  de API). ChatGPT Plus/Pro → modelos codex/* nos seletores. */
export default function SubscriptionsPanel({ onBack }: { onBack: () => void }) {
  const [st, setSt] = useState<ChatgptStatus | null>(null);

  const load = useCallback(async () => {
    try {
      setSt(await api.get<ChatgptStatus>("/integrations/subscriptions/chatgpt"));
    } catch {
      setSt({ connected: false });
    }
  }, []);
  useEffect(() => { load(); }, [load]);

  return (
    <div className="pt-1">
      <button onClick={onBack} className="mb-3 flex items-center gap-1 text-sm text-muted transition-colors hover:text-ink">
        <ChevronLeft size={16} /> Voltar
      </button>

      <div className="flex items-center gap-2.5">
        <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-surface2 text-accent-hover">
          <Crown size={17} />
        </span>
        <div>
          <p className="text-sm font-semibold text-ink">Assinaturas</p>
          <p className="text-xs text-muted">Use planos de IA pelo login da conta, sem chave de API</p>
        </div>
      </div>

      {st == null ? (
        <div className="flex justify-center py-10"><Loader2 size={18} className="animate-spin text-muted" /></div>
      ) : (
        <div className="mt-4 space-y-4">
          <ChatgptBlock st={st} reload={load} />
        </div>
      )}
    </div>
  );
}

function ChatgptBlock({ st, reload }: { st: ChatgptStatus; reload: () => Promise<void> }) {
  const [authUrl, setAuthUrl] = useState<string | null>(null);
  const [authKind, setAuthKind] = useState<"device" | "browser" | null>(null);
  const [userCode, setUserCode] = useState("");
  const [pollDelayMs, setPollDelayMs] = useState(5000);
  const [pasted, setPasted] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [modelsDraft, setModelsDraft] = useState<string | null>(null);

  // Device auth é consultado pelo servidor; no fallback de navegador, basta
  // consultar o status porque o callback local é quem conclui o OAuth.
  useEffect(() => {
    if (!authUrl || !authKind || st.connected) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      let nextDelay = authKind === "device" ? pollDelayMs : 1800;
      try {
        if (authKind === "device") {
          const out = await api.post<ChatgptStatus & { pending?: boolean; retry_after?: number }>(
            "/integrations/subscriptions/chatgpt/device/poll",
          );
          if (out.connected) {
            setAuthUrl(null);
            setAuthKind(null);
            setUserCode("");
            await reload();
            return;
          }
          nextDelay = Math.max(1000, (out.retry_after ?? 5) * 1000);
        } else {
          await reload();
        }
      } catch (e) {
        if (authKind === "device") {
          setErr(e instanceof ApiError ? e.message : "Falha ao consultar a autorização");
          setAuthUrl(null);
          setAuthKind(null);
          setUserCode("");
          return;
        }
      }
      if (!cancelled) timer = setTimeout(poll, nextDelay);
    };
    timer = setTimeout(poll, authKind === "device" ? pollDelayMs : 1200);
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [authUrl, authKind, pollDelayMs, st.connected, reload]);

  useEffect(() => {
    if (!st.connected) return;
    setAuthUrl(null);
    setAuthKind(null);
    setUserCode("");
    setPasted("");
  }, [st.connected]);

  async function beginDevice() {
    setErr(null);
    setBusy(true);
    try {
      const r = await api.post<DeviceAuthStart>("/integrations/subscriptions/chatgpt/device/begin");
      setAuthUrl(r.url);
      setAuthKind("device");
      setUserCode(r.user_code);
      setPollDelayMs(Math.max(1000, r.interval * 1000));
      if (!(await openExternal(r.url))) {
        setErr("Não consegui abrir o navegador daqui — use o botão “Abrir OpenAI”.");
      }
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Falha ao iniciar o login");
    } finally {
      setBusy(false);
    }
  }

  async function beginBrowser() {
    setErr(null);
    setBusy(true);
    try {
      const r = await api.post<{ url: string }>("/integrations/subscriptions/chatgpt/begin");
      setAuthUrl(r.url);
      setAuthKind("browser");
      setUserCode("");
      if (!(await openExternal(r.url))) {
        setErr("Não consegui abrir o navegador daqui — use “Copiar link”.");
      }
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Falha ao iniciar o login alternativo");
    } finally {
      setBusy(false);
    }
  }

  async function finish() {
    setErr(null);
    setBusy(true);
    try {
      await api.post("/integrations/subscriptions/chatgpt/finish", { pasted });
      setAuthUrl(null);
      setAuthKind(null);
      setPasted("");
      await reload();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Falha ao concluir o login");
    } finally {
      setBusy(false);
    }
  }

  async function disconnect() {
    setErr(null);
    try {
      await api.del("/integrations/subscriptions/chatgpt");
      setAuthUrl(null);
      setAuthKind(null);
      setUserCode("");
      await reload();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Falha ao desconectar");
    }
  }

  async function saveModels() {
    if (modelsDraft == null) return;
    setErr(null);
    try {
      await api.put("/integrations/subscriptions/chatgpt/models", {
        models: modelsDraft.split(",").map((s) => s.trim()).filter(Boolean),
      });
      setModelsDraft(null);
      await reload();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Falha ao salvar modelos");
    }
  }

  return (
    <div className="space-y-2.5 rounded-xl border border-border bg-surface p-3">
      <p className="flex flex-wrap items-center gap-1.5 text-xs font-semibold text-ink">
        ChatGPT (Plus/Pro) — Codex
        {st.connected && (
          <span className="inline-flex items-center gap-0.5 text-[10px] font-normal text-green-500">
            <Check size={11} /> conectado{st.email ? ` · ${st.email}` : ""}{st.plan ? ` · ${st.plan}` : ""}
          </span>
        )}
      </p>

      {!st.connected && !authUrl && (
        <>
          <p className="text-xs leading-4 text-muted">
            Entra com a sua conta ChatGPT (mesmo login do Codex CLI) e usa os modelos
            <span className="font-mono text-ink-soft"> codex/*</span> pela assinatura, sem chave de API.
            Fluxo não oficial: a OpenAI tolera hoje, mas pode mudar — uso por sua conta e risco.
          </p>
          <button onClick={beginDevice} disabled={busy}
            className="rounded-full bg-accent px-4 py-1.5 text-xs font-medium text-white transition-colors hover:bg-accent-hover disabled:opacity-60">
            {busy ? "…" : "Conectar"}
          </button>
          <button onClick={beginBrowser} disabled={busy}
            className="ml-2 rounded-full border border-border px-3 py-1.5 text-[11px] text-muted transition-colors hover:bg-hover hover:text-ink disabled:opacity-60">
            Login alternativo
          </button>
        </>
      )}

      {!st.connected && authUrl && authKind === "device" && (
        <div className="space-y-2.5">
          <p className="text-xs leading-4 text-muted">
            Entre na OpenAI e informe este código de uso único. A conexão será
            reconhecida automaticamente aqui, sem callback em localhost.
          </p>
          <div className="flex items-center gap-2">
            <code className="rounded-lg border border-accent/40 bg-surface2 px-3 py-2 text-base font-semibold tracking-[0.18em] text-ink">
              {userCode}
            </code>
            <button
              onClick={async () => { setErr(await copyText(userCode) ? null : "Não consegui copiar o código."); }}
              className="inline-flex items-center gap-1.5 rounded-full border border-border px-3 py-1.5 text-xs text-muted transition-colors hover:bg-hover hover:text-ink">
              <Copy size={13} /> Copiar código
            </button>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <button
              onClick={async () => {
                if (!(await openExternal(authUrl))) {
                  setErr("Não consegui abrir o navegador daqui.");
                }
              }}
              className="inline-flex items-center gap-1.5 rounded-full bg-accent px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-accent-hover">
              <ExternalLink size={13} /> Abrir OpenAI
            </button>
            <button onClick={() => { setAuthUrl(null); setAuthKind(null); setUserCode(""); }}
              className="rounded-full border border-border px-3 py-1.5 text-xs text-muted transition-colors hover:bg-hover hover:text-ink">
              Cancelar
            </button>
          </div>
          <p className="text-[11px] leading-4 text-muted">
            O código expira em 15 minutos. Continue somente se você iniciou este login.
          </p>
        </div>
      )}

      {!st.connected && authUrl && authKind === "browser" && (
        <div className="space-y-2">
          <p className="text-xs leading-4 text-muted">
            Este modo usa o callback fixo do Codex em localhost. Se navegador e
            servidor estiverem na mesma máquina, a conexão termina automaticamente.
          </p>
          {/* NÃO usar <a target="_blank">: no webview do app desktop isso não faz nada
              (sem handler de nova janela / plugin de shell) e o clique parece morto.
              openExternal chama o shell; se falhar (ex.: .exe antigo, sem o comando),
              cai no botão de copiar o link, que sempre funciona. */}
          <div className="flex flex-wrap items-center gap-2">
            <button
              onClick={async () => {
                if (!(await openExternal(authUrl))) {
                  setErr("Não consegui abrir o navegador daqui — use “Copiar link” e cole no seu navegador.");
                }
              }}
              className="inline-flex items-center gap-1.5 rounded-full border border-border px-3 py-1.5 text-xs text-ink transition-colors hover:bg-hover">
              <ExternalLink size={13} /> Abrir login da OpenAI
            </button>
            <button
              onClick={async () => { setErr(await copyText(authUrl) ? null : "Não consegui copiar — selecione o link manualmente."); }}
              className="inline-flex items-center gap-1.5 rounded-full border border-border px-3 py-1.5 text-xs text-muted transition-colors hover:bg-hover hover:text-ink">
              <Copy size={13} /> Copiar link
            </button>
          </div>
          <p className="text-[11px] leading-4 text-muted">
            Se o AI Workspace estiver em outra máquina e o retorno automático não abrir,
            copie a URL inteira da barra de endereço e use o campo abaixo.
          </p>
          <textarea rows={2} value={pasted} onChange={(e) => setPasted(e.target.value)}
            placeholder="http://localhost:1455/auth/callback?code=…&state=…"
            className="w-full resize-y rounded-lg border border-border bg-surface2 px-3 py-2 font-mono text-[11px] text-ink outline-none focus:border-accent" />
          <div className="flex items-center gap-2">
            <button onClick={finish} disabled={busy || !pasted.trim()}
              className="rounded-full bg-accent px-4 py-1.5 text-xs font-medium text-white transition-colors hover:bg-accent-hover disabled:opacity-60">
              {busy ? "…" : "Concluir"}
            </button>
            <button onClick={() => { setAuthUrl(null); setAuthKind(null); setPasted(""); }}
              className="rounded-full border border-border px-3 py-1.5 text-xs text-muted transition-colors hover:bg-hover hover:text-ink">
              Cancelar
            </button>
          </div>
        </div>
      )}

      {st.connected && (
        <div className="space-y-2">
          <div>
            <p className="mb-1 text-[11px] font-medium uppercase tracking-wide text-muted">Modelos nos seletores</p>
            {modelsDraft == null ? (
              <div className="flex flex-wrap items-center gap-1.5">
                {(st.models ?? []).map((m) => (
                  <span key={m} className="rounded-full bg-surface2 px-2.5 py-1 font-mono text-[11px] text-ink-soft">{m}</span>
                ))}
                <button
                  onClick={() => setModelsDraft((st.models ?? []).map((m) => m.replace(/^codex\//, "")).join(", "))}
                  className="rounded-full border border-border px-2.5 py-1 text-[11px] text-muted transition-colors hover:bg-hover hover:text-ink">
                  Editar
                </button>
              </div>
            ) : (
              <div className="flex flex-wrap items-center gap-2">
                <input value={modelsDraft} onChange={(e) => setModelsDraft(e.target.value)}
                  placeholder="gpt-5, gpt-5-codex"
                  className="w-full min-w-0 flex-1 basis-52 rounded-lg border border-border bg-surface2 px-3 py-1.5 font-mono text-xs text-ink outline-none focus:border-accent" />
                <button onClick={saveModels} className="shrink-0 rounded-full bg-accent px-3 py-1.5 text-xs font-medium text-white hover:bg-accent-hover">Salvar</button>
                <button onClick={() => setModelsDraft(null)} className="shrink-0 rounded-full border border-border px-3 py-1.5 text-xs text-muted hover:bg-hover hover:text-ink">Cancelar</button>
              </div>
            )}
          </div>
          <button onClick={disconnect}
            className="flex items-center gap-1.5 rounded-full border border-border px-3 py-1.5 text-xs text-muted transition-colors hover:bg-hover hover:text-red-400">
            <Trash2 size={13} /> Desconectar
          </button>
        </div>
      )}

      {err && <p className="flex items-center gap-1.5 text-xs text-red-400"><TriangleAlert size={13} /> {err}</p>}
    </div>
  );
}
