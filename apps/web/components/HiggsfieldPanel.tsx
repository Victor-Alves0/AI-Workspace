"use client";

import { useCallback, useEffect, useState } from "react";
import { Check, ChevronLeft, Clapperboard, Loader2, Trash2, TriangleAlert, Wifi } from "lucide-react";
import { api, ApiError } from "@/lib/api";

interface HiggsfieldStatus {
  connected: boolean;
  api_key_masked?: string;
}

/** Tela de detalhe "Higgsfield" (aberta pelo card em Integrações): API key + secret
 *  de cloud.higgsfield.ai. Libera a tool de geração de imagem/vídeo — a ativação
 *  por modelo continua na aba Ferramentas do editor de modelo. */
export default function HiggsfieldPanel({ onBack }: { onBack: () => void }) {
  const [st, setSt] = useState<HiggsfieldStatus | null>(null);

  const load = useCallback(async () => {
    try {
      setSt(await api.get<HiggsfieldStatus>("/integrations/higgsfield"));
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
          <Clapperboard size={17} />
        </span>
        <div>
          <p className="text-sm font-semibold text-ink">Higgsfield</p>
          <p className="text-xs text-muted">Geração de imagem (Soul, Seedream, FLUX) e vídeo (DoP, Kling)</p>
        </div>
      </div>

      {st == null ? (
        <div className="flex justify-center py-10"><Loader2 size={18} className="animate-spin text-muted" /></div>
      ) : (
        <HiggsfieldBody st={st} reload={load} />
      )}
    </div>
  );
}

function HiggsfieldBody({ st, reload }: { st: HiggsfieldStatus; reload: () => Promise<void> }) {
  const [apiKey, setApiKey] = useState("");
  const [secret, setSecret] = useState("");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [test, setTest] = useState<"idle" | "loading" | "ok" | "fail">("idle");
  const [testMsg, setTestMsg] = useState("");

  async function save() {
    setErr(null);
    setSaving(true);
    try {
      await api.put("/integrations/higgsfield", {
        api_key: apiKey.trim(),
        api_secret: secret.trim() || null,
      });
      setApiKey("");
      setSecret("");
      setSaved(true);
      setTimeout(() => setSaved(false), 1500);
      await reload();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Falha ao salvar");
    } finally {
      setSaving(false);
    }
  }

  async function testConn() {
    setTest("loading");
    setTestMsg("");
    try {
      const r = await api.post<{ ok: boolean; error?: string }>("/integrations/higgsfield/test");
      setTest(r.ok ? "ok" : "fail");
      if (!r.ok) setTestMsg(r.error || "");
    } catch (e) {
      setTest("fail");
      setTestMsg(e instanceof ApiError ? e.message : "");
    }
  }

  async function disconnect() {
    setErr(null);
    try {
      await api.del("/integrations/higgsfield");
      setTest("idle");
      await reload();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Falha ao desconectar");
    }
  }

  return (
    <div className="mt-4 space-y-4">
      <div className="space-y-2 rounded-xl border border-border bg-surface p-3">
        <p className="flex items-center gap-1.5 text-xs font-semibold text-ink">
          Conexão
          {st.connected && (
            <span className="inline-flex items-center gap-0.5 text-[10px] font-normal text-green-500">
              <Check size={11} /> conectada {st.api_key_masked && <span className="font-mono text-muted">({st.api_key_masked})</span>}
            </span>
          )}
        </p>
        <p className="text-xs text-muted">
          Crie a chave em <span className="font-mono text-ink-soft">cloud.higgsfield.ai</span> → API Keys.
        </p>
        <label className="block text-sm">
          <span className="text-ink-soft">API Key</span>
          <input value={apiKey} onChange={(e) => setApiKey(e.target.value)}
            placeholder={st.connected ? "•••••••• (preencha p/ trocar)" : "hf_..."}
            className="mt-1 w-full rounded-lg border border-border bg-surface2 px-3 py-1.5 font-mono text-xs text-ink outline-none focus:border-accent" />
        </label>
        <label className="block text-sm">
          <span className="text-ink-soft">API Secret</span>
          <input type="password" value={secret} onChange={(e) => setSecret(e.target.value)}
            placeholder={st.connected ? "•••••••• (deixe em branco p/ manter)" : "secret da chave"}
            className="mt-1 w-full rounded-lg border border-border bg-surface2 px-3 py-1.5 font-mono text-xs text-ink outline-none focus:border-accent" />
        </label>
        <div className="flex flex-wrap items-center gap-2 pt-0.5">
          <button onClick={save} disabled={saving || !apiKey.trim()}
            className="rounded-full bg-accent px-4 py-1.5 text-xs font-medium text-white transition-colors hover:bg-accent-hover disabled:opacity-60">
            {saving ? "…" : saved ? "Salvo ✓" : "Salvar"}
          </button>
          <button onClick={testConn} disabled={test === "loading" || !st.connected}
            title={st.connected ? "Testar conexão" : "Salve as credenciais primeiro"}
            className={`flex items-center gap-1.5 rounded-full border border-border px-3 py-1.5 text-xs transition-colors disabled:opacity-50 ${
              test === "ok" ? "text-green-500" : test === "fail" ? "text-red-400" : "text-ink-soft hover:bg-hover hover:text-ink"
            }`}>
            {test === "loading" ? <Loader2 size={13} className="animate-spin" />
              : test === "ok" ? <Check size={13} />
              : test === "fail" ? <TriangleAlert size={13} /> : <Wifi size={13} />}
            {test === "ok" ? "Conectada" : test === "fail" ? "Falhou" : "Testar"}
          </button>
          {st.connected && (
            <button onClick={disconnect} title="Desconectar (apaga as credenciais)"
              className="ml-auto flex items-center gap-1.5 rounded-full border border-border px-3 py-1.5 text-xs text-muted transition-colors hover:bg-hover hover:text-red-400">
              <Trash2 size={13} /> Desconectar
            </button>
          )}
        </div>
        {test === "fail" && testMsg && <p className="text-[11px] text-red-400/90">{testMsg}</p>}
      </div>

      {err && <p className="flex items-center gap-1.5 text-xs text-red-400"><TriangleAlert size={13} /> {err}</p>}

      <p className="text-[11px] leading-4 text-muted">
        Depois de conectar, ative a ferramenta <span className="text-ink-soft">Higgsfield (Imagem/Vídeo)</span> nos
        modelos que devem gerar mídia (editor do modelo → Ferramentas).
      </p>
    </div>
  );
}
