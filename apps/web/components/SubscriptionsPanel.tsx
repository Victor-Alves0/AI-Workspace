"use client";

import { useCallback, useEffect, useState } from "react";
import { Check, ChevronLeft, Crown, ExternalLink, Loader2, Trash2, TriangleAlert } from "lucide-react";
import { api, ApiError } from "@/lib/api";

interface ChatgptStatus {
  connected: boolean;
  email?: string;
  plan?: string;
  models?: string[];
}

/** Conexões → Assinaturas: usar planos de IA pelo LOGIN da conta (sem chave de
 *  API). ChatGPT Plus/Pro → modelos codex/* nos seletores. O card do Claude é
 *  informativo: a Anthropic proibiu OAuth de assinatura em apps de terceiros
 *  (fev/2026) e a enforcement baniu contas — não implementamos de propósito. */
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

          {/* Claude — honesto: proibido, não "em breve" */}
          <div className="space-y-1.5 rounded-xl border border-border bg-surface p-3 opacity-80">
            <p className="text-xs font-semibold text-ink">Claude (Pro/Max)</p>
            <p className="text-xs leading-4 text-muted">
              Indisponível: a Anthropic proíbe usar o login da assinatura fora dos apps oficiais
              (termos de fev/2026) e a fiscalização suspendeu contas. Para usar Claude aqui,
              use a API (OpenRouter) — cobrada por uso.
            </p>
          </div>

          {/* Gemini — tecnicamente possível, não implementado */}
          <div className="space-y-1.5 rounded-xl border border-border bg-surface p-3 opacity-80">
            <p className="text-xs font-semibold text-ink">Google Gemini</p>
            <p className="text-xs leading-4 text-muted">
              Em avaliação: o login do Gemini CLI exige callback local do OAuth do Google;
              o encaixe no servidor ainda está sendo estudado.
            </p>
          </div>
        </div>
      )}
    </div>
  );
}

function ChatgptBlock({ st, reload }: { st: ChatgptStatus; reload: () => Promise<void> }) {
  const [authUrl, setAuthUrl] = useState<string | null>(null);
  const [pasted, setPasted] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [modelsDraft, setModelsDraft] = useState<string | null>(null);

  async function begin() {
    setErr(null);
    setBusy(true);
    try {
      const r = await api.post<{ url: string }>("/integrations/subscriptions/chatgpt/begin");
      setAuthUrl(r.url);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Falha ao iniciar o login");
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
          <button onClick={begin} disabled={busy}
            className="rounded-full bg-accent px-4 py-1.5 text-xs font-medium text-white transition-colors hover:bg-accent-hover disabled:opacity-60">
            {busy ? "…" : "Conectar"}
          </button>
        </>
      )}

      {!st.connected && authUrl && (
        <div className="space-y-2">
          <p className="text-xs leading-4 text-muted">
            1. Abra o link e faça login. 2. Ao final o navegador tenta abrir
            <span className="font-mono"> localhost:1455</span> e falha — é esperado.
            3. Copie a URL inteira da barra de endereço e cole aqui.
          </p>
          <a href={authUrl} target="_blank" rel="noreferrer noopener"
            className="inline-flex items-center gap-1.5 rounded-full border border-border px-3 py-1.5 text-xs text-ink transition-colors hover:bg-hover">
            <ExternalLink size={13} /> Abrir login da OpenAI
          </a>
          <textarea rows={2} value={pasted} onChange={(e) => setPasted(e.target.value)}
            placeholder="http://localhost:1455/auth/callback?code=…&state=…"
            className="w-full resize-y rounded-lg border border-border bg-surface2 px-3 py-2 font-mono text-[11px] text-ink outline-none focus:border-accent" />
          <div className="flex items-center gap-2">
            <button onClick={finish} disabled={busy || !pasted.trim()}
              className="rounded-full bg-accent px-4 py-1.5 text-xs font-medium text-white transition-colors hover:bg-accent-hover disabled:opacity-60">
              {busy ? "…" : "Concluir"}
            </button>
            <button onClick={() => { setAuthUrl(null); setPasted(""); }}
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
