"use client";

import { useCallback, useEffect, useState } from "react";
import { Check, ChevronLeft, Loader2, RefreshCw, TriangleAlert } from "lucide-react";
import { SiOllama } from "react-icons/si";
import { api, ApiError } from "@/lib/api";

interface OllamaModel { id: string; name: string; parameter_size?: string; family?: string }
interface OllamaStatus {
  configured: boolean;
  base_url: string;
  enabled: boolean;
  models: OllamaModel[];
}

const DEFAULT_BASE = "http://localhost:11434";

function Toggle({ on, onClick }: { on: boolean; onClick: () => void }) {
  return (
    <button onClick={onClick} className={`relative h-6 w-11 shrink-0 rounded-full transition-colors ${on ? "bg-accent" : "bg-surface2"}`}>
      <span className={`absolute top-0.5 h-5 w-5 rounded-full bg-white transition-all ${on ? "left-[22px]" : "left-0.5"}`} />
    </button>
  );
}

/** Detalhe "Ollama" (Conexões): o usuário aponta a URL do seu servidor Ollama e os
 *  modelos locais aparecem em todos os seletores de modelo do sistema. */
export default function OllamaPanel({ onBack, onChanged }: { onBack: () => void; onChanged?: () => void }) {
  const [st, setSt] = useState<OllamaStatus | null>(null);

  const load = useCallback(async () => {
    try {
      setSt(await api.get<OllamaStatus>("/integrations/ollama"));
    } catch {
      setSt({ configured: false, base_url: DEFAULT_BASE, enabled: true, models: [] });
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
          <SiOllama size={18} />
        </span>
        <div>
          <p className="text-sm font-semibold text-ink">Ollama</p>
          <p className="text-xs text-muted">Rode modelos locais e use-os em todo o sistema</p>
        </div>
      </div>

      {st == null ? (
        <div className="flex justify-center py-10"><Loader2 size={18} className="animate-spin text-muted" /></div>
      ) : (
        <OllamaBody st={st} reload={load} onChanged={onChanged} />
      )}
    </div>
  );
}

function OllamaBody({ st, reload, onChanged }: { st: OllamaStatus; reload: () => Promise<void>; onChanged?: () => void }) {
  const [baseUrl, setBaseUrl] = useState(st.base_url || DEFAULT_BASE);
  const [enabled, setEnabled] = useState(st.enabled);
  const [busy, setBusy] = useState<"save" | "test" | null>(null);
  const [test, setTest] = useState<{ ok: boolean; count?: number; error?: string } | null>(null);
  const [saved, setSaved] = useState(false);

  async function save() {
    setBusy("save"); setSaved(false); setTest(null);
    try {
      const res = await api.put<OllamaStatus>("/integrations/ollama", { base_url: baseUrl, enabled });
      setSaved(true);
      setTimeout(() => setSaved(false), 1500);
      // recarrega modelos e avisa o app (atualiza os seletores sem F5)
      await reload();
      onChanged?.();
      // puxa a lista atualizada para exibir aqui
      const fresh = await api.get<OllamaStatus>("/integrations/ollama");
      setTest({ ok: true, count: fresh.models.length });
    } catch (e) {
      setTest({ ok: false, error: e instanceof ApiError ? e.message : "Falha ao salvar" });
    } finally {
      setBusy(null);
    }
  }

  async function runTest() {
    setBusy("test"); setTest(null);
    try {
      const r = await api.post<{ ok: boolean; count?: number; error?: string }>("/integrations/ollama/test", { base_url: baseUrl });
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
        <label className="text-sm text-ink-soft">Endereço do servidor Ollama</label>
        <input
          value={baseUrl}
          onChange={(e) => setBaseUrl(e.target.value)}
          placeholder={DEFAULT_BASE}
          className="w-full rounded-lg border border-border bg-surface2 px-3 py-2 text-sm text-ink outline-none focus:border-accent"
        />
        <p className="text-xs text-muted">
          Ex.: <span className="font-mono">http://localhost:11434</span> ou o IP da máquina na LAN
          (<span className="font-mono">http://192.168.1.199:11434</span>). Rode o Ollama com{" "}
          <span className="font-mono">OLLAMA_HOST=0.0.0.0</span> para acesso pela rede.
        </p>
      </div>

      <div className="flex items-center justify-between rounded-xl border border-border bg-surface px-3 py-2.5">
        <div>
          <p className="text-sm text-ink">Ativado</p>
          <p className="text-xs text-muted">Quando ligado, os modelos locais aparecem nos seletores.</p>
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
            <Check size={15} /> Conectado — {test.count ?? 0} modelo(s) encontrado(s).
          </p>
        ) : (
          <p className="flex items-start gap-1.5 text-sm text-red-400">
            <TriangleAlert size={15} className="mt-0.5 shrink-0" /> {test.error || "Não foi possível conectar."}
          </p>
        )
      )}

      {st.models.length > 0 && (
        <div>
          <p className="mb-1.5 text-xs font-medium uppercase tracking-wider text-muted">Modelos instalados ({st.models.length})</p>
          <div className="grid grid-cols-1 gap-1.5 sm:grid-cols-2">
            {st.models.map((m) => (
              <div key={m.id} className="flex items-center gap-2 rounded-lg border border-border bg-surface px-3 py-2">
                <SiOllama size={14} className="shrink-0 text-accent-hover" />
                <span className="truncate text-sm text-ink">{m.name}</span>
                {m.parameter_size && <span className="ml-auto shrink-0 text-[10px] text-muted">{m.parameter_size}</span>}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
