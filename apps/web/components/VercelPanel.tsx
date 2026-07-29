"use client";

import { useEffect, useState } from "react";
import { Check, ChevronLeft, Loader2, TriangleAlert, Trash2, Wifi } from "lucide-react";
import { SiVercel } from "react-icons/si";
import { api } from "@/lib/api";

interface VercelStatus { connected: boolean }

/** Detalhe "Vercel" (card em Integrações): conecta via Personal Access Token para
 *  consultar projetos e deployments. */
export default function VercelPanel({ onBack }: { onBack: () => void }) {
  const [st, setSt] = useState<VercelStatus | null>(null);
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [test, setTest] = useState<"idle" | "loading" | "ok" | "fail">("idle");
  const [msg, setMsg] = useState("");

  const load = () => api.get<VercelStatus>("/integrations/vercel").then(setSt).catch(() => setSt({ connected: false }));
  useEffect(() => { load(); }, []);

  async function save() {
    if (!token.trim()) return;
    setBusy(true);
    try {
      await api.put("/integrations/vercel", { token: token.trim() });
      setToken("");
      setMsg("Token salvo.");
      await load();
    } catch (e) {
      setMsg(e instanceof Error ? e.message : "Falha ao salvar.");
    } finally { setBusy(false); }
  }
  async function runTest() {
    setTest("loading"); setMsg("");
    try {
      const r = await api.post<{ ok: boolean; user?: string; error?: string }>("/integrations/vercel/test");
      if (r.ok) { setTest("ok"); setMsg(`Conectado como ${r.user ?? "usuário"}.`); }
      else { setTest("fail"); setMsg(r.error || "Não foi possível conectar."); }
    } catch (e) {
      setTest("fail"); setMsg(e instanceof Error ? e.message : "Falha no teste.");
    }
  }
  async function disconnect() {
    setBusy(true);
    try { await api.del("/integrations/vercel"); setMsg("Desconectado."); await load(); }
    finally { setBusy(false); }
  }

  const connected = st?.connected;
  return (
    <div className="pt-1">
      <button onClick={onBack} className="mb-3 flex items-center gap-1 text-sm text-muted transition-colors hover:text-ink">
        <ChevronLeft size={16} /> Voltar
      </button>

      <div className="flex items-center gap-2.5">
        <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-surface2 text-accent-hover">
          <SiVercel size={17} />
        </span>
        <div>
          <p className="text-sm font-semibold text-ink">Vercel</p>
          <p className="text-xs text-muted">Projetos e deployments</p>
        </div>
        {connected && <span className="ml-auto flex items-center gap-1 text-xs text-green-400"><Check size={13} /> Conectado</span>}
      </div>

      <div className="mt-4 space-y-3 rounded-xl border border-border bg-surface p-4">
        <label className="block">
          <span className="mb-1 block text-xs font-medium text-muted">Personal Access Token</span>
          <input
            type="password"
            value={token}
            onChange={(e) => setToken(e.target.value)}
            placeholder={connected ? "•••••••• (já configurado — cole para trocar)" : "vercel_..."}
            className="w-full rounded-lg border border-border bg-bg px-3 py-2 font-mono text-xs text-ink outline-none focus:border-accent"
          />
          <span className="mt-1 block text-[11px] text-muted">Crie em vercel.com → Settings → Tokens. Guardado cifrado.</span>
        </label>
        <div className="flex flex-wrap items-center gap-2">
          <button onClick={save} disabled={busy || !token.trim()} className="flex items-center gap-1.5 rounded-lg bg-accent px-4 py-1.5 text-sm font-medium text-white transition-colors hover:bg-accent-hover disabled:opacity-50">
            {busy ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />} Salvar
          </button>
          {connected && (
            <>
              <button onClick={runTest} className="flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-sm text-ink-soft transition-colors hover:bg-hover">
                {test === "loading" ? <Loader2 size={14} className="animate-spin" /> : <Wifi size={14} />} Testar
              </button>
              <button onClick={disconnect} disabled={busy} className="flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-sm text-muted transition-colors hover:border-red-400/40 hover:text-red-400 disabled:opacity-50">
                <Trash2 size={14} /> Desconectar
              </button>
            </>
          )}
        </div>
        {msg && (
          <p className={`flex items-center gap-1.5 text-xs ${test === "ok" ? "text-green-400" : test === "fail" ? "text-red-400" : "text-muted"}`}>
            {test === "fail" && <TriangleAlert size={13} />}{msg}
          </p>
        )}
      </div>
    </div>
  );
}
