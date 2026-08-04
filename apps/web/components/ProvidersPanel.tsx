"use client";

import { useCallback, useEffect, useState } from "react";
import { Check, ChevronLeft, Loader2, Plus, RefreshCw, Server, Trash2, TriangleAlert } from "lucide-react";
import { api, ApiError } from "@/lib/api";

interface Provider {
  slug: string;
  name: string;
  base_url: string;
  models: string[];
  enabled: boolean;
  has_key: boolean;
}
interface Preset { name: string; base_url: string; models?: string[] }

function Toggle({ on, onClick }: { on: boolean; onClick: () => void }) {
  return (
    <button onClick={onClick} className={`relative h-6 w-11 shrink-0 rounded-full transition-colors ${on ? "bg-accent" : "bg-surface2"}`}>
      <span className={`absolute top-0.5 h-5 w-5 rounded-full bg-white transition-all ${on ? "left-[22px]" : "left-0.5"}`} />
    </button>
  );
}

/** Detalhe "Provedores" (Conexões): HUB ÚNICO dos provedores de LLM — o OpenRouter
 *  (embutido) e os endpoints OpenAI-compatíveis (LiteLLM, personalizados). As chaves
 *  ficam todas aqui; antes a do OpenRouter morava em "APIs", separada das demais.
 *  Os modelos entram em todos os seletores prefixados pelo provedor. */
export default function ProvidersPanel({ onBack, onChanged }: { onBack: () => void; onChanged?: () => void }) {
  const [items, setItems] = useState<Provider[] | null>(null);
  const [presets, setPresets] = useState<Record<string, Preset>>({});
  const [editing, setEditing] = useState<Partial<Provider> & { api_key?: string } | null>(null);
  // escolha do tipo ao adicionar (OpenRouter / LiteLLM / personalizado) e o form do
  // OpenRouter, que é só a chave (não tem base URL nem lista de modelos p/ configurar).
  const [choosing, setChoosing] = useState(false);
  const [editingOpenrouter, setEditingOpenrouter] = useState(false);
  const [orKey, setOrKey] = useState<boolean | null>(null); // chave do OpenRouter salva?

  const load = useCallback(async () => {
    try {
      const r = await api.get<{ items: Provider[]; presets: Record<string, Preset> }>("/integrations/providers");
      setItems(r.items);
      setPresets(r.presets || {});
    } catch {
      setItems([]);
    }
    try {
      const s = await api.get<Record<string, boolean>>("/settings/secrets");
      setOrKey(!!s.openrouter);
    } catch { setOrKey(false); }
  }, []);
  useEffect(() => { load(); }, [load]);

  async function afterSave() {
    setEditing(null);
    setChoosing(false);
    setEditingOpenrouter(false);
    await load();
    onChanged?.(); // atualiza os seletores de modelo sem F5
  }

  // presets ainda não adicionados (p/ o seletor de tipo)
  const addedSlugs = new Set((items ?? []).map((i) => i.slug));
  const availablePresets = Object.entries(presets).filter(([slug]) => !addedSlugs.has(slug));

  return (
    <div className="pt-1">
      <button onClick={onBack} className="mb-3 flex items-center gap-1 text-sm text-muted transition-colors hover:text-ink">
        <ChevronLeft size={16} /> Voltar
      </button>

      <div className="flex items-center gap-2.5">
        <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-surface2 text-accent-hover">
          <Server size={18} />
        </span>
        <div>
          <p className="text-sm font-semibold text-ink">Provedores</p>
          <p className="text-xs text-muted">Provedores de LLMs</p>
        </div>
      </div>

      {items == null ? (
        <div className="flex justify-center py-10"><Loader2 size={18} className="animate-spin text-muted" /></div>
      ) : editingOpenrouter ? (
        <OpenrouterForm configured={!!orKey} onCancel={() => setEditingOpenrouter(false)} onSaved={afterSave} />
      ) : editing ? (
        <ProviderForm draft={editing} onCancel={() => { setEditing(null); setChoosing(false); }} onSaved={afterSave} />
      ) : choosing ? (
        <AddProviderChooser
          presets={availablePresets}
          onCancel={() => setChoosing(false)}
          onOpenrouter={() => { setChoosing(false); setEditingOpenrouter(true); }}
          onPreset={(slug, pr) => setEditing({ slug, name: pr.name, base_url: pr.base_url, models: pr.models ?? [], enabled: true, api_key: "" })}
          onCustom={() => setEditing({ name: "", base_url: "", enabled: true, api_key: "" })}
        />
      ) : (
        <div className="space-y-4 pt-4">
          <ul className="space-y-2">
            {/* OpenRouter é o provedor EMBUTIDO: sem base URL/modelos p/ configurar,
                só a chave. Fica na mesma lista p/ tudo de LLM viver num lugar só. */}
            <li className="flex items-center gap-3 rounded-xl border border-border bg-surface px-3 py-2.5">
              <span className={`h-2 w-2 shrink-0 rounded-full ${orKey ? "bg-green-400" : "bg-surface2"}`} />
              <button onClick={() => setEditingOpenrouter(true)} className="min-w-0 flex-1 text-left">
                <p className="truncate text-sm text-ink">OpenRouter</p>
                <p className="truncate text-xs text-muted">
                  {orKey ? "chave configurada" : "sem chave · necessária para conversar"}
                </p>
              </button>
            </li>
            {items.map((p) => (
              <li key={p.slug} className="flex items-center gap-3 rounded-xl border border-border bg-surface px-3 py-2.5">
                <span className={`h-2 w-2 shrink-0 rounded-full ${p.enabled && p.has_key ? "bg-green-400" : "bg-surface2"}`} />
                <button onClick={() => setEditing({ ...p, api_key: "" })} className="min-w-0 flex-1 text-left">
                  <p className="truncate text-sm text-ink">{p.name}</p>
                  <p className="truncate text-xs text-muted">{p.base_url || "sem URL"}{p.has_key ? "" : " · sem chave"}</p>
                </button>
                <Trash2
                  size={15}
                  className="shrink-0 cursor-pointer text-muted transition-colors hover:text-red-400"
                  onClick={async () => { await api.del(`/integrations/providers/${p.slug}`); await load(); onChanged?.(); }}
                />
              </li>
            ))}
          </ul>

          <button
            onClick={() => setChoosing(true)}
            className="flex items-center gap-1.5 rounded-full bg-accent px-4 py-1.5 text-sm font-medium text-white transition-colors hover:bg-accent-hover"
          >
            <Plus size={15} /> Adicionar provedor
          </button>
        </div>
      )}
    </div>
  );
}

/** Escolha do TIPO ao adicionar: OpenRouter, os presets (LiteLLM) e o personalizado.
 *  Em vez de largar o usuário num formulário vazio, mostra as opções reais e leva
 *  direto ao que cada uma precisa — no OpenRouter, só a chave. */
function AddProviderChooser({
  presets, onCancel, onOpenrouter, onPreset, onCustom,
}: {
  presets: [string, Preset][];
  onCancel: () => void;
  onOpenrouter: () => void;
  onPreset: (slug: string, pr: Preset) => void;
  onCustom: () => void;
}) {
  const Card = ({ title, desc, onClick }: { title: string; desc: string; onClick: () => void }) => (
    <button
      onClick={onClick}
      className="w-full rounded-xl border border-border bg-surface px-3 py-3 text-left transition-colors hover:bg-surface2"
    >
      <p className="text-sm text-ink">{title}</p>
      <p className="text-xs text-muted">{desc}</p>
    </button>
  );
  return (
    <div className="space-y-2 pt-4">
      <button onClick={onCancel} className="mb-1 flex items-center gap-1 text-sm text-muted transition-colors hover:text-ink">
        <ChevronLeft size={16} /> Adicionar provedor
      </button>
      <Card title="OpenRouter" desc="Centenas de modelos com uma só chave. Só cole a chave." onClick={onOpenrouter} />
      {presets.map(([slug, pr]) => (
        <Card
          key={slug}
          title={pr.name}
          desc={`Endpoint compatível com OpenAI · ${pr.base_url}`}
          onClick={() => onPreset(slug, pr)}
        />
      ))}
      <Card title="Personalizado" desc="Qualquer endpoint compatível com OpenAI: nome, URL e chave." onClick={onCustom} />
    </div>
  );
}

/** OpenRouter: provedor embutido — não tem base URL nem lista de modelos para
 *  configurar, então o formulário é só a chave. Guardada como o segredo `openrouter`
 *  (mesmo lugar de sempre), agora editável aqui em vez de na aba "APIs". */
function OpenrouterForm({ configured, onCancel, onSaved }: {
  configured: boolean; onCancel: () => void; onSaved: () => void;
}) {
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  async function save() {
    if (!key.trim()) { setErr("Informe a chave."); return; }
    setBusy(true); setErr("");
    try {
      await api.put("/settings/secrets/openrouter", { api_key: key.trim() });
      onSaved();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Falha ao salvar.");
    } finally { setBusy(false); }
  }

  return (
    <div className="space-y-4 pt-4">
      <button onClick={onCancel} className="flex items-center gap-1 text-sm text-muted transition-colors hover:text-ink">
        <ChevronLeft size={16} /> OpenRouter
      </button>
      <div className="space-y-1.5">
        <label className="text-sm text-ink-soft">Chave de API</label>
        <input
          type="password" value={key} onChange={(e) => setKey(e.target.value)}
          placeholder={configured ? "•••••••• (guardada — cole outra para trocar)" : "cole a chave do OpenRouter"}
          className="w-full rounded-lg border border-border bg-surface2 px-3 py-2 text-sm text-ink outline-none focus:border-accent"
        />
        <p className="text-xs text-muted">Pegue em openrouter.ai/keys. É a chave usada para conversar.</p>
      </div>
      <button onClick={save} disabled={busy} className="rounded-full bg-accent px-5 py-1.5 text-sm font-medium text-white transition-colors hover:bg-accent-hover disabled:opacity-50">
        {busy ? "Salvando…" : "Salvar"}
      </button>
      {err && <p className="flex items-start gap-1.5 text-sm text-red-400"><TriangleAlert size={15} className="mt-0.5 shrink-0" /> {err}</p>}
    </div>
  );
}

function ProviderForm({
  draft, onCancel, onSaved,
}: {
  draft: Partial<Provider> & { api_key?: string };
  onCancel: () => void;
  onSaved: () => void;
}) {
  const isNew = !draft.slug;
  const [name, setName] = useState(draft.name ?? "");
  const [baseUrl, setBaseUrl] = useState(draft.base_url ?? "");
  const [modelsText, setModelsText] = useState((draft.models ?? []).join("\n"));
  const [key, setKey] = useState("");
  const [enabled, setEnabled] = useState(draft.enabled ?? true);
  const modelList = modelsText.split(/[\n,]+/).map((s) => s.trim()).filter(Boolean);
  const pathModel = baseUrl.includes("{model}"); // provedor põe o modelo no caminho (kie.ai)
  const [busy, setBusy] = useState<"save" | "test" | null>(null);
  const [test, setTest] = useState<{ ok: boolean; count?: number; error?: string } | null>(null);
  const [err, setErr] = useState("");

  // slug: fixo p/ presets/edição; senão derivado do nome (o backend re-slugifica)
  const slug = draft.slug || name.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/(^-|-$)/g, "");

  async function runTest() {
    setBusy("test"); setTest(null); setErr("");
    if (!key.trim() && !draft.has_key) { setErr("Informe a chave para testar."); setBusy(null); return; }
    try {
      const r = await api.post<{ ok: boolean; count?: number; error?: string }>(
        "/integrations/providers/test", { base_url: baseUrl, api_key: key.trim() || "test", models: modelList },
      );
      setTest(r);
    } catch (e) {
      setTest({ ok: false, error: e instanceof ApiError ? e.message : "Falha na conexão" });
    } finally { setBusy(null); }
  }

  async function save() {
    if (!name.trim()) { setErr("Dê um nome ao provedor."); return; }
    if (!baseUrl.trim()) { setErr("Informe a base URL."); return; }
    if (pathModel && modelList.length === 0) { setErr("Provedores com {model} na URL precisam de ao menos um modelo."); return; }
    if (isNew && !key.trim()) { setErr("Informe a chave de API."); return; }
    setBusy("save"); setErr("");
    try {
      await api.put(`/integrations/providers/${slug || "provedor"}`, {
        name: name.trim(),
        base_url: baseUrl.trim(),
        models: modelList,
        enabled,
        ...(key.trim() ? { api_key: key.trim() } : {}),
      });
      onSaved();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Falha ao salvar.");
    } finally { setBusy(null); }
  }

  return (
    <div className="space-y-4 pt-4">
      <button onClick={onCancel} className="flex items-center gap-1 text-sm text-muted transition-colors hover:text-ink">
        <ChevronLeft size={16} /> {isNew ? "Novo provedor" : name || draft.slug}
      </button>

      <div className="space-y-1.5">
        <label className="text-sm text-ink-soft">Nome</label>
        <input
          value={name} onChange={(e) => setName(e.target.value)} placeholder="Ex.: Kie.ai"
          className="w-full rounded-lg border border-border bg-surface2 px-3 py-2 text-sm text-ink outline-none focus:border-accent"
        />
      </div>

      <div className="space-y-1.5">
        <label className="text-sm text-ink-soft">Base URL (compatível com OpenAI)</label>
        <input
          value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} placeholder="http://localhost:4000/v1"
          className="w-full rounded-lg border border-border bg-surface2 px-3 py-2 font-mono text-sm text-ink outline-none focus:border-accent"
        />
        <p className="text-xs text-muted">
          A raiz onde bate <span className="font-mono">/chat/completions</span> (geralmente termina em <span className="font-mono">/v1</span>).
          Use <span className="font-mono">{"{model}"}</span> na URL se o provedor põe o modelo no caminho (em vez do corpo).
        </p>
      </div>

      <div className="space-y-1.5">
        <label className="text-sm text-ink-soft">Modelos {pathModel && <span className="text-muted">(obrigatório)</span>}</label>
        <textarea
          value={modelsText} onChange={(e) => setModelsText(e.target.value)} rows={3}
          placeholder={"gpt-5-2\ngemini-3-pro"}
          className="w-full resize-y rounded-lg border border-border bg-surface2 px-3 py-2 font-mono text-sm text-ink outline-none focus:border-accent"
        />
        <p className="text-xs text-muted">
          Um id por linha (ou separados por vírgula). {pathModel
            ? "Este provedor não tem lista automática — informe os ids que quer usar."
            : "Deixe vazio para descobrir automaticamente via /models."}
        </p>
      </div>

      <div className="space-y-1.5">
        <label className="text-sm text-ink-soft">Chave de API</label>
        <input
          type="password" value={key} onChange={(e) => setKey(e.target.value)}
          placeholder={draft.has_key ? "•••••••• (guardada — deixe em branco para manter)" : "cole a chave do provedor"}
          className="w-full rounded-lg border border-border bg-surface2 px-3 py-2 text-sm text-ink outline-none focus:border-accent"
        />
      </div>

      <div className="flex items-center justify-between rounded-xl border border-border bg-surface px-3 py-2.5">
        <div>
          <p className="text-sm text-ink">Ativado</p>
          <p className="text-xs text-muted">Quando ligado, os modelos deste provedor aparecem nos seletores.</p>
        </div>
        <Toggle on={enabled} onClick={() => setEnabled((v) => !v)} />
      </div>

      <div className="flex items-center gap-2">
        <button onClick={runTest} disabled={!!busy} className="flex items-center gap-1.5 rounded-full border border-border bg-surface px-4 py-1.5 text-sm text-ink-soft transition-colors hover:bg-surface2 disabled:opacity-50">
          {busy === "test" ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />} Testar conexão
        </button>
        <button onClick={save} disabled={!!busy} className="rounded-full bg-accent px-5 py-1.5 text-sm font-medium text-white transition-colors hover:bg-accent-hover disabled:opacity-50">
          {busy === "save" ? "Salvando…" : "Salvar"}
        </button>
      </div>

      {err && <p className="flex items-start gap-1.5 text-sm text-red-400"><TriangleAlert size={15} className="mt-0.5 shrink-0" /> {err}</p>}
      {test && (
        test.ok ? (
          <p className="flex items-center gap-1.5 text-sm text-green-400"><Check size={15} /> Conectado — {test.count ?? 0} modelo(s) disponíveis.</p>
        ) : (
          <p className="flex items-start gap-1.5 text-sm text-red-400"><TriangleAlert size={15} className="mt-0.5 shrink-0" /> {test.error || "Não foi possível conectar."}</p>
        )
      )}
    </div>
  );
}
