"use client";

import { useEffect, useState } from "react";
import { Check, ChevronLeft, Loader2, TriangleAlert, Trash2, Wifi } from "lucide-react";
import { SiSpotify } from "react-icons/si";
import { api } from "@/lib/api";
import { tr } from "@/lib/i18n";

interface SpotifyStatus { connected: boolean }

/** Detalhe "Spotify" (card em Integrações): conecta via app do usuário (Client ID +
 *  Secret, fluxo Client Credentials) para buscar no catálogo. */
export default function SpotifyPanel({ onBack }: { onBack: () => void }) {
  const [st, setSt] = useState<SpotifyStatus | null>(null);
  const [clientId, setClientId] = useState("");
  const [clientSecret, setClientSecret] = useState("");
  const [busy, setBusy] = useState(false);
  const [test, setTest] = useState<"idle" | "loading" | "ok" | "fail">("idle");
  const [msg, setMsg] = useState("");

  const load = () => api.get<SpotifyStatus>("/integrations/spotify").then(setSt).catch(() => setSt({ connected: false }));
  useEffect(() => { load(); }, []);

  async function save() {
    setBusy(true);
    try {
      await api.put("/integrations/spotify", { client_id: clientId.trim(), client_secret: clientSecret.trim() });
      setClientId(""); setClientSecret("");
      setMsg(tr("Credenciais salvas."));
      await load();
    } catch (e) {
      setMsg(e instanceof Error ? e.message : tr("Falha ao salvar."));
    } finally { setBusy(false); }
  }
  async function runTest() {
    setTest("loading"); setMsg("");
    try {
      const r = await api.post<{ ok: boolean; error?: string }>("/integrations/spotify/test");
      if (r.ok) { setTest("ok"); setMsg(tr("Credenciais válidas.")); }
      else { setTest("fail"); setMsg(r.error || tr("Não foi possível conectar.")); }
    } catch (e) {
      setTest("fail"); setMsg(e instanceof Error ? e.message : tr("Falha no teste."));
    }
  }
  async function disconnect() {
    setBusy(true);
    try { await api.del("/integrations/spotify"); setMsg(tr("Desconectado.")); await load(); }
    finally { setBusy(false); }
  }

  const connected = st?.connected;
  return (
    <div className="pt-1">
      <button onClick={onBack} className="mb-3 flex items-center gap-1 text-sm text-muted transition-colors hover:text-ink">
        <ChevronLeft size={16} />  {tr("Voltar")}
      </button>

      <div className="flex items-center gap-2.5">
        <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-surface2 text-accent-hover">
          <SiSpotify size={17} />
        </span>
        <div>
          <p className="text-sm font-semibold text-ink">{tr("Spotify")}</p>
          <p className="text-xs text-muted">{tr("Música: buscar e tocar")}</p>
        </div>
        {connected && <span className="ml-auto flex items-center gap-1 text-xs text-green-400"><Check size={13} />  {tr("Conectado")}</span>}
      </div>

      <div className="mt-4 space-y-3 rounded-xl border border-border bg-surface p-4">
        <p className="text-[11px] text-muted">{tr("Crie um app em developer.spotify.com → Dashboard e cole o Client ID e Secret. Dá acesso ao catálogo (buscar faixas/artistas/álbuns).")}</p>
        <label className="block">
          <span className="mb-1 block text-xs font-medium text-muted">{tr("Client ID")}</span>
          <input
            value={clientId}
            onChange={(e) => setClientId(e.target.value)}
            placeholder={connected ? "•••••••• (configurado)" : "ex.: 1a2b3c..."}
            className="w-full rounded-lg border border-border bg-bg px-3 py-2 font-mono text-xs text-ink outline-none focus:border-accent"
          />
        </label>
        <label className="block">
          <span className="mb-1 block text-xs font-medium text-muted">{tr("Client Secret")}</span>
          <input
            type="password"
            value={clientSecret}
            onChange={(e) => setClientSecret(e.target.value)}
            placeholder={connected ? tr("•••••••• (cole para trocar)") : ""}
            className="w-full rounded-lg border border-border bg-bg px-3 py-2 font-mono text-xs text-ink outline-none focus:border-accent"
          />
          <span className="mt-1 block text-[11px] text-muted">{tr("Guardado cifrado. Controle de playback exige login do usuário (não incluído).")}</span>
        </label>
        <div className="flex flex-wrap items-center gap-2">
          <button onClick={save} disabled={busy} className="flex items-center gap-1.5 rounded-lg bg-accent px-4 py-1.5 text-sm font-medium text-white transition-colors hover:bg-accent-hover disabled:opacity-50">
            {busy ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />}  {tr("Salvar")}
          </button>
          {connected && (
            <>
              <button onClick={runTest} className="flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-sm text-ink-soft transition-colors hover:bg-hover">
                {test === "loading" ? <Loader2 size={14} className="animate-spin" /> : <Wifi size={14} />}  {tr("Testar")}
              </button>
              <button onClick={disconnect} disabled={busy} className="flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-sm text-muted transition-colors hover:border-red-400/40 hover:text-red-400 disabled:opacity-50">
                <Trash2 size={14} />  {tr("Desconectar")}
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
