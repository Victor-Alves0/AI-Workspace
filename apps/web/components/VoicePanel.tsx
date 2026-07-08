"use client";

import { useCallback, useEffect, useState } from "react";
import { AudioLines, Check, ChevronLeft, Loader2, RefreshCw, TriangleAlert } from "lucide-react";
import { api, ApiError } from "@/lib/api";

interface VoiceConfig {
  configured: boolean;
  base_url: string;
  tts_model: string;
  has_key: boolean;
  enabled: boolean;
  voices: string[];
}

const DEFAULT_BASE = "http://localhost:8880/v1";

function Toggle({ on, onClick }: { on: boolean; onClick: () => void }) {
  return (
    <button onClick={onClick} className={`relative h-6 w-11 shrink-0 rounded-full transition-colors ${on ? "bg-accent" : "bg-surface2"}`}>
      <span className={`absolute top-0.5 h-5 w-5 rounded-full bg-white transition-all ${on ? "left-[22px]" : "left-0.5"}`} />
    </button>
  );
}

/** Conexão "Voz Local": aponta um servidor de voz OpenAI-compatível (Kokoro-FastAPI
 *  ou um servidor de clonagem) e as vozes ficam disponíveis por-modelo. */
export default function VoicePanel({ onBack, onChanged }: { onBack: () => void; onChanged?: () => void }) {
  const [st, setSt] = useState<VoiceConfig | null>(null);

  const load = useCallback(async () => {
    try {
      setSt(await api.get<VoiceConfig>("/voice/config"));
    } catch {
      setSt({ configured: false, base_url: DEFAULT_BASE, tts_model: "kokoro", has_key: false, enabled: true, voices: [] });
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
          <AudioLines size={18} />
        </span>
        <div>
          <p className="text-sm font-semibold text-ink">Voz Local</p>
          <p className="text-xs text-muted">Kokoro ou servidor de clonagem (voz da IA local)</p>
        </div>
      </div>

      {st == null ? (
        <div className="flex justify-center py-10"><Loader2 size={18} className="animate-spin text-muted" /></div>
      ) : (
        <VoiceBody st={st} reload={load} onChanged={onChanged} />
      )}
    </div>
  );
}

function VoiceBody({ st, reload, onChanged }: { st: VoiceConfig; reload: () => Promise<void>; onChanged?: () => void }) {
  const [baseUrl, setBaseUrl] = useState(st.base_url || DEFAULT_BASE);
  const [ttsModel, setTtsModel] = useState(st.tts_model || "kokoro");
  const [apiKey, setApiKey] = useState("");
  const [enabled, setEnabled] = useState(st.enabled);
  const [busy, setBusy] = useState<"save" | "test" | null>(null);
  const [test, setTest] = useState<{ ok: boolean; count?: number; error?: string } | null>(null);
  const [saved, setSaved] = useState(false);

  async function save() {
    setBusy("save"); setSaved(false); setTest(null);
    try {
      await api.put<VoiceConfig>("/voice/config", {
        base_url: baseUrl, tts_model: ttsModel, enabled,
        ...(apiKey ? { api_key: apiKey } : {}),
      });
      setSaved(true);
      setTimeout(() => setSaved(false), 1500);
      await reload();
      onChanged?.();
      const fresh = await api.get<VoiceConfig>("/voice/config");
      setTest({ ok: true, count: fresh.voices.length });
    } catch (e) {
      setTest({ ok: false, error: e instanceof ApiError ? e.message : "Falha ao salvar" });
    } finally {
      setBusy(null);
    }
  }

  async function runTest() {
    setBusy("test"); setTest(null);
    try {
      const r = await api.post<{ ok: boolean; count?: number; error?: string }>("/voice/test", {
        base_url: baseUrl, ...(apiKey ? { api_key: apiKey } : {}),
      });
      setTest(r);
    } catch (e) {
      setTest({ ok: false, error: e instanceof ApiError ? e.message : "Falha na conexão" });
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="space-y-4 pt-4">
      <div className="space-y-1.5">
        <label className="text-sm text-ink-soft">Endereço do servidor de voz (OpenAI-compatível)</label>
        <input
          value={baseUrl}
          onChange={(e) => setBaseUrl(e.target.value)}
          placeholder={DEFAULT_BASE}
          className="w-full rounded-lg border border-border bg-surface2 px-3 py-2 text-sm text-ink outline-none focus:border-accent"
        />
        <p className="text-xs text-muted">
          O endereço é acessado pelo <b>servidor</b> (não pelo navegador) e deve terminar em{" "}
          <span className="font-mono">/v1</span>. Serviço embutido:{" "}
          <span className="font-mono">http://kokoro:8880/v1</span>. Servidor no seu PC (host):{" "}
          <span className="font-mono">http://host.docker.internal:8880/v1</span> ou o IP da LAN.
        </p>
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <div className="space-y-1.5">
          <label className="text-sm text-ink-soft">Modelo TTS</label>
          <input
            value={ttsModel}
            onChange={(e) => setTtsModel(e.target.value)}
            placeholder="kokoro"
            className="w-full rounded-lg border border-border bg-surface2 px-3 py-2 text-sm text-ink outline-none focus:border-accent"
          />
        </div>
        <div className="space-y-1.5">
          <label className="text-sm text-ink-soft">Chave (opcional)</label>
          <input
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            placeholder={st.has_key ? "•••••• (salva)" : "muitos servidores locais ignoram"}
            className="w-full rounded-lg border border-border bg-surface2 px-3 py-2 text-sm text-ink outline-none focus:border-accent"
          />
        </div>
      </div>

      <div className="flex items-center justify-between rounded-xl border border-border bg-surface px-3 py-2.5">
        <div>
          <p className="text-sm text-ink">Ativado</p>
          <p className="text-xs text-muted">Quando ligado, o TTS usa este servidor no lugar do provedor global.</p>
        </div>
        <Toggle on={enabled} onClick={() => setEnabled((v) => !v)} />
      </div>

      <div className="flex items-center gap-2">
        <button onClick={runTest} disabled={!!busy} className="flex items-center gap-1.5 rounded-full border border-border bg-surface px-4 py-1.5 text-sm text-ink-soft transition-colors hover:bg-surface2 disabled:opacity-50">
          {busy === "test" ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />} Testar conexão
        </button>
        <button onClick={save} disabled={!!busy} className="rounded-full bg-accent px-5 py-1.5 text-sm font-medium text-white transition-colors hover:bg-accent-hover disabled:opacity-50">
          {busy === "save" ? "Salvando…" : saved ? "Salvo ✓" : "Salvar"}
        </button>
      </div>

      {test && (
        test.ok ? (
          <p className="flex items-center gap-1.5 text-sm text-green-400">
            <Check size={15} /> Conectado — {test.count ?? 0} voz(es) disponível(is).
          </p>
        ) : (
          <p className="flex items-start gap-1.5 text-sm text-red-400">
            <TriangleAlert size={15} className="mt-0.5 shrink-0" /> {test.error || "Não foi possível conectar."}
          </p>
        )
      )}

      {st.voices.length > 0 && (
        <div>
          <p className="mb-1.5 text-xs font-medium uppercase tracking-wider text-muted">Vozes disponíveis ({st.voices.length})</p>
          <div className="flex flex-wrap gap-1.5">
            {st.voices.slice(0, 60).map((v) => (
              <span key={v} className="rounded-full border border-border bg-surface px-2.5 py-0.5 font-mono text-xs text-ink-soft">{v}</span>
            ))}
          </div>
          <p className="mt-2 text-xs text-muted">
            Escolha a voz por modelo em Espaço → Modelos. Dá para misturar vozes com pesos, ex.:{" "}
            <span className="font-mono">af_bella(2)+af_sky(1)</span>.
          </p>
        </div>
      )}
    </div>
  );
}
