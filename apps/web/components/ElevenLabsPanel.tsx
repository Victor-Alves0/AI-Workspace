"use client";

import { useCallback, useEffect, useState } from "react";
import { AudioLines, Check, ChevronLeft, Loader2, TriangleAlert, Trash2, Wifi } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { useConfirm } from "./ConfirmDialog";
import { Select } from "@/components/ui";
import { toast } from "@/components/Toaster";
import { tr } from "@/lib/i18n";

interface Voice { id: string; name: string }
interface ElevenLabsStatus {
  connected: boolean;
  enabled: boolean;
  model: string;
  default_voice: string;
  voices: Voice[];
}

/** Conexão ElevenLabs (Conexões): voz premium do sistema (o TTS passa a usar as vozes
 *  ElevenLabs, escolhidas por-modelo com o prefixo "el:") + libera a ferramenta de
 *  áudio (elevenlabs.audio.generate: TTS e efeitos sonoros no chat). Só API key. */
export default function ElevenLabsPanel({ onBack, onChanged }: { onBack: () => void; onChanged?: () => void }) {
  const confirm = useConfirm();
  const [st, setSt] = useState<ElevenLabsStatus | null>(null);
  const [apiKey, setApiKey] = useState("");
  const [model, setModel] = useState("");
  const [defVoice, setDefVoice] = useState("");
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [err, setErr] = useState("");
  const [test, setTest] = useState<"idle" | "loading" | "ok" | "fail">("idle");

  const load = useCallback(async () => {
    try {
      const r = await api.get<ElevenLabsStatus>("/integrations/elevenlabs");
      setSt(r);
      setModel(r.model || "");
      setDefVoice(r.default_voice || "");
    } catch {
      setSt({ connected: false, enabled: false, model: "", default_voice: "", voices: [] });
    }
  }, []);
  useEffect(() => { load(); }, [load]);

  async function save(patch: Partial<{ enabled: boolean }> = {}) {
    setBusy(true); setErr("");
    try {
      const body: Record<string, unknown> = { model: model.trim(), default_voice: defVoice.trim(), ...patch };
      if (apiKey.trim()) body.api_key = apiKey.trim();
      const r = await api.put<ElevenLabsStatus>("/integrations/elevenlabs", body);
      setSt(r); setApiKey("");
      setSaved(true); setTimeout(() => setSaved(false), 1500);
      onChanged?.();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : tr("Falha ao salvar"));
    } finally {
      setBusy(false);
    }
  }

  async function runTest() {
    setTest("loading");
    try {
      const r = await api.post<{ ok: boolean }>("/integrations/elevenlabs/test");
      setTest(r.ok ? "ok" : "fail");
    } catch { setTest("fail"); }
  }

  async function disconnect() {
    if (!(await confirm({ title: tr("Desconectar a ElevenLabs?"), body: tr("A voz premium e a ferramenta de áudio deixarão de funcionar."), confirmLabel: tr("Desconectar"), danger: true }))) return;
    try { await api.del("/integrations/elevenlabs"); await load(); onChanged?.(); }
    catch (e) { toast(e instanceof ApiError ? e.message : tr("Falha ao desconectar")); }
  }

  return (
    <div className="pt-1">
      <button onClick={onBack} className="mb-3 flex items-center gap-1 text-sm text-muted transition-colors hover:text-ink">
        <ChevronLeft size={16} />  {tr("Voltar")}
      </button>

      <div className="flex items-center gap-2.5">
        <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-surface2 text-accent-hover">
          <AudioLines size={17} />
        </span>
        <div>
          <p className="text-sm font-semibold text-ink">ElevenLabs</p>
          <p className="text-xs text-muted">{tr("Voz premium do sistema + geração de áudio no chat")}</p>
        </div>
      </div>

      {st == null ? (
        <div className="flex justify-center py-10"><Loader2 size={18} className="animate-spin text-muted" /></div>
      ) : (
        <>
          <div className="mt-4 space-y-2 rounded-xl border border-border bg-surface p-3">
            <label className="block text-sm">
              <span className="text-ink-soft">{tr("API key")}</span>
              <input
                type="password" value={apiKey} onChange={(e) => setApiKey(e.target.value)}
                placeholder={st.connected ? tr("•••••••• (deixe em branco p/ manter)") : tr("sua API key da ElevenLabs")}
                className="mt-1 w-full rounded-lg border border-border bg-surface2 px-3 py-1.5 text-sm text-ink outline-none focus:border-accent"
              />
            </label>
            <label className="block text-sm">
              <span className="text-ink-soft">{tr("Modelo")}</span>
              <input
                value={model} onChange={(e) => setModel(e.target.value)}
                placeholder="eleven_multilingual_v2"
                className="mt-1 w-full rounded-lg border border-border bg-surface2 px-3 py-1.5 text-sm text-ink outline-none focus:border-accent"
              />
            </label>
            <label className="block text-sm">
              <span className="text-ink-soft">{tr("Voz padrão")}</span>
              {st.voices.length ? (
                <Select
                  value={defVoice} onChange={(e) => setDefVoice(e.target.value)}
                  className="mt-1 w-full rounded-lg border border-border bg-surface2 px-3 py-1.5 text-sm text-ink outline-none focus:border-accent"
                >
                  <option value="">{tr("(padrão da ElevenLabs)")}</option>
                  {st.voices.map((v) => <option key={v.id} value={v.id}>{v.name}</option>)}
                </Select>
              ) : (
                <input
                  value={defVoice} onChange={(e) => setDefVoice(e.target.value)}
                  placeholder={tr("voice id (opcional)")}
                  className="mt-1 w-full rounded-lg border border-border bg-surface2 px-3 py-1.5 text-sm text-ink outline-none focus:border-accent"
                />
              )}
            </label>
            <div className="flex items-center justify-between gap-2 pt-0.5">
              {err ? <p className="min-w-0 flex-1 truncate text-[11px] text-red-400">{err}</p>
                   : <p className="min-w-0 flex-1 text-[11px] text-muted">{tr("Pegue a chave em elevenlabs.io → perfil → API Keys.")}</p>}
              <button onClick={() => save()} disabled={busy || (!st.connected && !apiKey.trim())}
                className="shrink-0 rounded-full bg-accent px-4 py-1.5 text-xs font-medium text-white transition-colors hover:bg-accent-hover disabled:opacity-60">
                {busy ? "…" : saved ? tr("Salvo ✓") : tr("Salvar")}
              </button>
            </div>
          </div>

          {st.connected && (
            <>
              <div className="mt-3 flex items-center justify-between rounded-xl border border-border bg-surface px-3 py-2.5">
                <div className="min-w-0">
                  <p className="text-sm text-ink">{tr("Voz do sistema")}</p>
                  <p className="text-[11px] text-muted">{tr("Usar vozes ElevenLabs nas respostas faladas (escolha por-modelo, prefixo “el:”).")}</p>
                </div>
                <button
                  onClick={() => save({ enabled: !st.enabled })}
                  className={`relative h-5 w-9 shrink-0 rounded-full transition-colors ${st.enabled ? "bg-accent" : "bg-surface2"}`}
                >
                  <span className={`absolute top-0.5 h-4 w-4 rounded-full bg-white transition-all ${st.enabled ? "left-[18px]" : "left-0.5"}`} />
                </button>
              </div>

              <div className="mt-3 flex items-center gap-2">
                <button onClick={runTest} disabled={test === "loading"}
                  className={`flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs transition-colors ${
                    test === "ok" ? "text-green-500" : test === "fail" ? "text-red-400" : "text-muted hover:bg-hover hover:text-ink"
                  }`}>
                  {test === "loading" ? <Loader2 size={13} className="animate-spin" />
                    : test === "ok" ? <Check size={13} />
                    : test === "fail" ? <TriangleAlert size={13} /> : <Wifi size={13} />}
                  {test === "ok" ? tr("Conectado") : test === "fail" ? tr("Falhou") : tr("Testar conexão")}
                </button>
                <button onClick={disconnect}
                  className="flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs text-muted transition-colors hover:bg-hover hover:text-red-400">
                  <Trash2 size={13} />  {tr("Desconectar")}
                </button>
              </div>
            </>
          )}
        </>
      )}
    </div>
  );
}
