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

/** Detalhe "Provedores" (Conexões): endpoints OpenAI-compatíveis (kie.ai, LiteLLM, …).
 *  Cada provedor tem nome + base URL + chave; os modelos entram em todos os seletores
 *  do sistema prefixados pelo provedor. Só provedores com chave listam modelos. */
export default function ProvidersPanel({ onBack, onChanged }: { onBack: () => void; onChanged?: () => void }) {
  const [items, setItems] = useState<Provider[] | null>(null);
  const [presets, setPresets] = useState<Record<string, Preset>>({});
  const [editing, setEditing] = useState<Partial<Provider> & { api_key?: string } | null>(null);

  const load = useCallback(async () => {
    try {
      const r = await api.get<{ items: Provider[]; presets: Record<string, Preset> }>("/integrations/providers");
      setItems(r.items);
      setPresets(r.presets || {});
    } catch {
      setItems([]);
    }
  }, []);
  useEffect(() => { load(); }, [load]);

  async function afterSave() {
    setEditing(null);
    await load();
    onChanged?.(); // atualiza os seletores de modelo sem F5
  }

  // presets ainda não adicionados (p/ o botão de atalho)
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
          <p className="text-xs text-muted">Endpoints compatíveis com OpenAI (kie.ai, LiteLLM, …)</p>
        </div>
      </div>

      {items == null ? (
        <div className="flex justify-center py-10"><Loader2 size={18} className="animate-spin text-muted" /></div>
      ) : editing ? (
        <ProviderForm draft={editing} onCancel={() => setEditing(null)} onSaved={afterSave} />
      ) : (
        <div className="space-y-4 pt-4">
          {items.length === 0 ? (
            <p className="rounded-xl border border-dashed border-border px-3 py-6 text-center text-sm text-muted">
              Nenhum provedor. Adicione um endpoint compatível com OpenAI para usar seus modelos.
            </p>
          ) : (
            <ul className="space-y-2">
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
          )}

          <div className="flex flex-wrap items-center gap-2">
            <button
              onClick={() => setEditing({ name: "", base_url: "", enabled: true, api_key: "" })}
              className="flex items-center gap-1.5 rounded-full bg-accent px-4 py-1.5 text-sm font-medium text-white transition-colors hover:bg-accent-hover"
            >
              <Plus size={15} /> Adicionar provedor
            </button>
            {availablePresets.map(([slug, pr]) => (
              <button
                key={slug}
                onClick={() => setEditing({ slug, name: pr.name, base_url: pr.base_url, models: pr.models ?? [], enabled: true, api_key: "" })}
                className="flex items-center gap-1.5 rounded-full border border-border bg-surface px-3 py-1.5 text-sm text-ink-soft transition-colors hover:bg-surface2"
              >
                <Plus size={14} /> {pr.name}
              </button>
            ))}
          </div>
        </div>
      )}
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
