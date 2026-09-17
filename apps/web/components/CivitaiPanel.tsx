"use client";

import { useCallback, useEffect, useState } from "react";
import { Check, ChevronLeft, Loader2, Sparkles, Trash2, TriangleAlert, Wifi } from "lucide-react";
import { api, ApiError } from "@/lib/api";

interface CivitaiStatus {
  connected: boolean;
}

/** Token único para catálogo e Orchestration API. O token fica cifrado no backend;
 * a UI recebe apenas o booleano de conexão. */
export default function CivitaiPanel({ onBack }: { onBack: () => void }) {
  const [status, setStatus] = useState<CivitaiStatus | null>(null);
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [test, setTest] = useState<"idle" | "loading" | "ok" | "fail">("idle");
  const [validating, setValidating] = useState(false);
  const [message, setMessage] = useState("");

  const load = useCallback(async () => {
    try {
      setStatus(await api.get<CivitaiStatus>("/integrations/civitai"));
    } catch {
      setStatus({ connected: false });
    }
  }, []);
  useEffect(() => { load(); }, [load]);

  async function save() {
    if (!token.trim()) return;
    setBusy(true);
    setMessage("");
    try {
      await api.put("/integrations/civitai", { api_key: token.trim() });
      setToken("");
      setMessage("Token salvo com segurança.");
      await load();
    } catch (error) {
      setMessage(error instanceof ApiError ? error.message : "Falha ao salvar o token.");
    } finally {
      setBusy(false);
    }
  }

  async function testConnection() {
    setTest("loading");
    setMessage("");
    try {
      const result = await api.post<{ ok: boolean; user?: string; tier?: string; error?: string }>("/integrations/civitai/test");
      setTest(result.ok ? "ok" : "fail");
      setMessage(result.ok
        ? `Conectado como ${result.user ?? "usuário"}${result.tier ? ` · ${result.tier}` : ""}.`
        : result.error || "Não foi possível conectar.");
    } catch (error) {
      setTest("fail");
      setMessage(error instanceof ApiError ? error.message : "Falha no teste.");
    }
  }

  async function disconnect() {
    setBusy(true);
    setMessage("");
    try {
      await api.del("/integrations/civitai");
      setTest("idle");
      setMessage("Civitai desconectado.");
      await load();
    } catch (error) {
      setMessage(error instanceof ApiError ? error.message : "Falha ao desconectar.");
    } finally {
      setBusy(false);
    }
  }

  async function validateGeneration() {
    setValidating(true);
    setMessage("");
    try {
      const result = await api.post<{ ok: boolean; cost?: unknown; error?: string }>("/integrations/civitai/validate-generation");
      setTest(result.ok ? "ok" : "fail");
      setMessage(result.ok
        ? "Contrato de geração validado sem criar imagem nem gastar Buzz."
        : result.error || "Não foi possível validar a geração.");
    } catch (error) {
      setTest("fail");
      setMessage(error instanceof ApiError ? error.message : "Falha na validação da geração.");
    } finally {
      setValidating(false);
    }
  }

  const connected = status?.connected;
  return (
    <div className="pt-1">
      <button onClick={onBack} className="mb-3 flex items-center gap-1 text-sm text-muted transition-colors hover:text-ink">
        <ChevronLeft size={16} /> Voltar
      </button>

      <div className="flex items-center gap-2.5">
        <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-surface2 text-accent-hover">
          <Sparkles size={17} />
        </span>
        <div>
          <p className="text-sm font-semibold text-ink">Civitai</p>
          <p className="text-xs text-muted">Catálogo de modelos e geração de imagens</p>
        </div>
        {connected && <span className="ml-auto flex items-center gap-1 text-xs text-green-400"><Check size={13} /> Conectado</span>}
      </div>

      <div className="mt-4 space-y-3 rounded-xl border border-border bg-surface p-4">
        <label className="block">
          <span className="mb-1 block text-xs font-medium text-muted">API token</span>
          <input
            type="password"
            value={token}
            onChange={(event) => setToken(event.target.value)}
            placeholder={connected ? "•••••••• (cole para trocar)" : "Token do Civitai"}
            className="w-full rounded-lg border border-border bg-bg px-3 py-2 font-mono text-xs text-ink outline-none focus:border-accent"
          />
          <span className="mt-1 block text-[11px] leading-4 text-muted">
            Crie em civitai.com → Account Settings → API Keys. O token é guardado cifrado e nunca volta ao navegador.
          </span>
        </label>
        <div className="flex flex-wrap items-center gap-2">
          <button onClick={save} disabled={busy || !token.trim()} className="flex items-center gap-1.5 rounded-lg bg-accent px-4 py-1.5 text-sm font-medium text-white transition-colors hover:bg-accent-hover disabled:opacity-50">
            {busy ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />} Salvar
          </button>
          {connected && (
            <>
              <button onClick={testConnection} disabled={test === "loading"} className="flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-sm text-ink-soft transition-colors hover:bg-hover disabled:opacity-50">
                {test === "loading" ? <Loader2 size={14} className="animate-spin" /> : <Wifi size={14} />} Testar
              </button>
              <button onClick={validateGeneration} disabled={validating} className="flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-sm text-ink-soft transition-colors hover:bg-hover disabled:opacity-50">
                {validating ? <Loader2 size={14} className="animate-spin" /> : <Sparkles size={14} />} Validar geração
              </button>
              <button onClick={disconnect} disabled={busy} className="flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-sm text-muted transition-colors hover:border-red-400/40 hover:text-red-400 disabled:opacity-50">
                <Trash2 size={14} /> Desconectar
              </button>
            </>
          )}
        </div>
        {message && (
          <p className={`flex items-center gap-1.5 text-xs ${test === "ok" ? "text-green-400" : test === "fail" ? "text-red-400" : "text-muted"}`}>
            {test === "fail" && <TriangleAlert size={13} />}{message}
          </p>
        )}
      </div>

      <p className="mt-4 text-[11px] leading-4 text-muted">
        Depois de conectar, ative <span className="text-ink-soft">Civitai (Modelos/Mídia)</span> em Ferramentas no editor do modelo.
        Buscar o catálogo é gratuito; gerar imagens usa o saldo Buzz da conta. <span className="text-ink-soft">Validar geração</span> usa uma simulação e não cria mídia.
      </p>
    </div>
  );
}
