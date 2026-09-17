"use client";

import { useCallback, useEffect, useState } from "react";
import { Check, ChevronLeft, Loader2, Search, SlidersHorizontal, Sparkles, Trash2, TriangleAlert, Wifi } from "lucide-react";
import { api, ApiError } from "@/lib/api";

interface CivitaiRecipe {
  width?: number;
  height?: number;
  steps?: number;
  cfg_scale?: number;
  sampler?: string;
}

interface CivitaiVersion {
  id: number;
  name: string;
  base_model?: string;
  air?: string;
  can_generate?: boolean;
  recipe?: CivitaiRecipe;
}

interface CivitaiModel {
  id: number;
  name: string;
  creator?: string;
  versions: CivitaiVersion[];
}

interface CivitaiGenerationConfig extends CivitaiRecipe {
  model?: string;
  model_name?: string;
  version_name?: string;
  base_model?: string;
  model_id?: number;
  version_id?: number;
}

interface CivitaiStatus {
  connected: boolean;
  generation?: CivitaiGenerationConfig;
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
  const [generation, setGeneration] = useState<CivitaiGenerationConfig>({ width: 1024, height: 1024 });
  const [modelQuery, setModelQuery] = useState("");
  const [models, setModels] = useState<CivitaiModel[]>([]);
  const [searchingModels, setSearchingModels] = useState(false);
  const [savingGeneration, setSavingGeneration] = useState(false);

  const load = useCallback(async () => {
    try {
      const next = await api.get<CivitaiStatus>("/integrations/civitai");
      setStatus(next);
      setGeneration({ width: 1024, height: 1024, ...(next.generation ?? {}) });
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

  async function searchModels() {
    setSearchingModels(true);
    setMessage("");
    try {
      const result = await api.get<{ items: CivitaiModel[] }>(`/integrations/civitai/models?query=${encodeURIComponent(modelQuery.trim())}`);
      setModels(result.items);
      if (!result.items.length) setMessage("Nenhum checkpoint compatível com geração foi encontrado.");
    } catch (error) {
      setMessage(error instanceof ApiError ? error.message : "Não foi possível consultar os modelos.");
    } finally {
      setSearchingModels(false);
    }
  }

  function chooseVersion(model: CivitaiModel, version: CivitaiVersion) {
    if (!version.air || version.can_generate === false) return;
    setGeneration((current) => ({
      ...current,
      ...version.recipe,
      model: version.air,
      model_name: model.name,
      version_name: version.name,
      base_model: version.base_model,
      model_id: model.id,
      version_id: version.id,
    }));
    setModels([]);
    setMessage(`Selecionado: ${model.name} · ${version.name}. Revise a receita e salve o perfil.`);
  }

  async function saveGeneration() {
    if (!generation.model) {
      setMessage("Escolha um checkpoint antes de salvar o perfil.");
      return;
    }
    setSavingGeneration(true);
    setMessage("");
    try {
      const result = await api.put<{ generation: CivitaiGenerationConfig }>("/integrations/civitai/generation", generation);
      setGeneration({ width: 1024, height: 1024, ...result.generation });
      setStatus((current) => current ? { ...current, generation: result.generation } : current);
      setMessage("Perfil de geração salvo. O chat usará este checkpoint, sem fallback oculto.");
    } catch (error) {
      setMessage(error instanceof ApiError ? error.message : "Não foi possível salvar o perfil de geração.");
    } finally {
      setSavingGeneration(false);
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

      {connected && (
        <section className="mt-3 rounded-xl border border-border bg-surface p-4" aria-labelledby="civitai-generation-profile">
          <div className="flex items-start gap-2.5">
            <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-violet-500/10 text-violet-300"><SlidersHorizontal size={16} /></span>
            <div className="min-w-0">
              <h3 id="civitai-generation-profile" className="text-sm font-semibold text-ink">Perfil de geração</h3>
              <p className="mt-0.5 text-[11px] leading-4 text-muted">Escolha o checkpoint que o chat usará quando você não indicar um modelo. Sem perfil, a geração é bloqueada — não há modelo padrão invisível.</p>
            </div>
          </div>

          <div className="mt-3 rounded-xl border border-violet-400/20 bg-violet-500/[.045] p-3">
            <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-violet-300">Checkpoint ativo</p>
            {generation.model ? <div className="mt-1"><p className="truncate text-xs font-medium text-ink" title={`${generation.model_name ?? ""} · ${generation.version_name ?? ""}`}>{generation.model_name || "Checkpoint selecionado"}</p><p className="mt-0.5 truncate text-[10px] text-muted">{generation.version_name || generation.base_model || "Versão selecionada"}{generation.base_model && generation.version_name ? ` · ${generation.base_model}` : ""}</p></div> : <p className="mt-1 text-xs text-amber-300">Nenhum checkpoint selecionado.</p>}
          </div>

          <form onSubmit={(event) => { event.preventDefault(); void searchModels(); }} className="mt-3 flex gap-1.5">
            <label className="relative min-w-0 flex-1"><Search size={14} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-muted" /><span className="sr-only">Buscar checkpoint no Civitai</span><input value={modelQuery} onChange={(event) => setModelQuery(event.target.value)} placeholder="Buscar checkpoint, ex.: photorealistic Flux" className="w-full rounded-lg border border-border bg-bg py-2 pl-8 pr-3 text-xs text-ink outline-none focus:border-accent" /></label>
            <button type="submit" disabled={searchingModels} className="flex min-h-9 items-center gap-1 rounded-lg border border-border px-3 text-xs text-ink-soft transition-colors hover:bg-hover hover:text-ink disabled:opacity-50">{searchingModels ? <Loader2 size={14} className="animate-spin" /> : <Search size={14} />} Buscar</button>
          </form>

          {models.length > 0 && <div className="mt-2 max-h-64 space-y-1 overflow-y-auto rounded-xl border border-border bg-bg/50 p-1.5">
            {models.map((model) => <article key={model.id} className="rounded-lg border border-transparent p-2 hover:border-border hover:bg-hover/60"><p className="truncate text-xs font-medium text-ink" title={model.name}>{model.name}</p><p className="mt-0.5 text-[10px] text-muted">{model.creator ? `por ${model.creator}` : "Checkpoint"}</p><div className="mt-2 flex flex-wrap gap-1">{model.versions.map((version) => <button key={version.id} type="button" disabled={!version.air || version.can_generate === false} onClick={() => chooseVersion(model, version)} title={version.can_generate === false ? "Esta versão não está disponível na API de geração" : `${version.name}${version.base_model ? ` · ${version.base_model}` : ""}`} className="min-h-8 rounded-md border border-violet-400/20 bg-violet-500/10 px-2 text-[10px] text-violet-100 transition-colors hover:bg-violet-500/20 disabled:cursor-not-allowed disabled:opacity-40">{version.name || "Versão"}{version.base_model ? ` · ${version.base_model}` : ""}</button>)}</div></article>)}
          </div>}

          <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-5">
            <label className="text-[10px] font-medium text-muted">Largura<input type="number" min="64" max="4096" step="64" value={generation.width ?? ""} onChange={(event) => setGeneration((current) => ({ ...current, width: event.target.value ? Number(event.target.value) : undefined }))} className="mt-1 w-full rounded-lg border border-border bg-bg px-2 py-1.5 font-mono text-xs text-ink outline-none focus:border-accent" /></label>
            <label className="text-[10px] font-medium text-muted">Altura<input type="number" min="64" max="4096" step="64" value={generation.height ?? ""} onChange={(event) => setGeneration((current) => ({ ...current, height: event.target.value ? Number(event.target.value) : undefined }))} className="mt-1 w-full rounded-lg border border-border bg-bg px-2 py-1.5 font-mono text-xs text-ink outline-none focus:border-accent" /></label>
            <label className="text-[10px] font-medium text-muted">Steps<input type="number" min="1" max="100" value={generation.steps ?? ""} onChange={(event) => setGeneration((current) => ({ ...current, steps: event.target.value ? Number(event.target.value) : undefined }))} className="mt-1 w-full rounded-lg border border-border bg-bg px-2 py-1.5 font-mono text-xs text-ink outline-none focus:border-accent" /></label>
            <label className="text-[10px] font-medium text-muted">CFG<input type="number" min="0.1" max="30" step="0.1" value={generation.cfg_scale ?? ""} onChange={(event) => setGeneration((current) => ({ ...current, cfg_scale: event.target.value ? Number(event.target.value) : undefined }))} className="mt-1 w-full rounded-lg border border-border bg-bg px-2 py-1.5 font-mono text-xs text-ink outline-none focus:border-accent" /></label>
            <label className="col-span-2 text-[10px] font-medium text-muted sm:col-span-1">Sampler<input value={generation.sampler ?? ""} onChange={(event) => setGeneration((current) => ({ ...current, sampler: event.target.value }))} placeholder="Do exemplo" maxLength={120} className="mt-1 w-full rounded-lg border border-border bg-bg px-2 py-1.5 text-xs text-ink outline-none focus:border-accent" /></label>
          </div>
          <p className="mt-2 text-[10px] leading-4 text-muted">Ao selecionar uma versão, os campos recebem a receita compacta de uma imagem-exemplo quando ela estiver disponível. Você pode ajustar antes de salvar.</p>
          <button type="button" onClick={() => void saveGeneration()} disabled={savingGeneration || !generation.model} className="mt-3 flex min-h-10 w-full items-center justify-center gap-1.5 rounded-lg bg-violet-500 px-3 text-xs font-medium text-white transition-colors hover:bg-violet-400 disabled:cursor-not-allowed disabled:opacity-50">{savingGeneration ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />} Salvar perfil de geração</button>
        </section>
      )}

      <p className="mt-4 text-[11px] leading-4 text-muted">
        Depois de conectar, ative <span className="text-ink-soft">Civitai (Modelos/Mídia)</span> em Ferramentas no editor do modelo.
        Buscar o catálogo é gratuito; gerar imagens usa o saldo Buzz da conta. <span className="text-ink-soft">Validar geração</span> usa uma simulação e não cria mídia.
      </p>
    </div>
  );
}
