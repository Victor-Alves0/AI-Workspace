"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Check, FileText, Film, Image as ImageIcon, Loader2, Sparkles, TriangleAlert, X } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import type { Model, ModelConfig } from "@/lib/types";
import ModelField from "./ModelField";

interface Enrichment {
  id: string;
  doc_id: string;
  filename: string;
  mime: string;
  folder: string | null;
  status: "pending" | "ready" | "error";
  title: string;
  description: string;
  tags: string[];
  error: string | null;
}

function TypeIcon({ mime }: { mime: string }) {
  if (mime.startsWith("image/")) return <ImageIcon size={14} className="text-muted" />;
  if (mime.startsWith("video/") || mime.startsWith("audio/")) return <Film size={14} className="text-muted" />;
  return <FileText size={14} className="text-muted" />;
}

/** Enriquecedor com IA: gera título/descrição/tags para os arquivos (proposta) e o
 *  usuário aprova ou descarta cada um. Escopo = pasta atual ou base inteira. */
export default function EnrichModal({
  baseId, folderId, folderName, onClose, onApplied,
}: {
  baseId: string;
  folderId: string | null;
  folderName?: string;
  onClose: () => void;
  onApplied?: () => void;
}) {
  const [extModels, setExtModels] = useState<Model[]>([]);
  const [custom, setCustom] = useState<ModelConfig[]>([]);
  const [modelValue, setModelValue] = useState("");
  const [scope, setScope] = useState<"folder" | "base">(folderId ? "folder" : "base");
  const [extra, setExtra] = useState("");
  const [items, setItems] = useState<Enrichment[]>([]);
  const [busy, setBusy] = useState(false);
  const [started, setStarted] = useState(false);
  const [msg, setMsg] = useState("");

  useEffect(() => {
    api.get<ModelConfig[]>("/models").then(setCustom).catch(() => {});
    Promise.all([
      api.get<Model[]>("/settings/models").catch(() => [] as Model[]),
      api.get<Model[]>("/integrations/ollama/models").catch(() => [] as Model[]),
      api.get<Model[]>("/integrations/subscriptions/chatgpt/models").catch(() => [] as Model[]),
    ]).then(([e, l, s]) => setExtModels([...e, ...l, ...s])).catch(() => {});
  }, []);

  // id de modelo em texto puro (o endpoint espera o id externo, não "custom:<id>")
  function resolvedModel(): string {
    if (modelValue.startsWith("custom:")) {
      const mc = custom.find((m) => m.id === modelValue.slice(7));
      return mc?.base_model ?? "";
    }
    return modelValue;
  }

  const load = useCallback(async () => {
    try {
      setItems(await api.get<Enrichment[]>(`/knowledge/bases/${baseId}/enrichments`));
    } catch { /* silencioso */ }
  }, [baseId]);

  useEffect(() => { load(); }, [load]);

  // enquanto houver algo gerando, refaz o poll (a lista se preenche sozinha)
  const anyPending = items.some((i) => i.status === "pending");
  useEffect(() => {
    if (!started && !anyPending) return;
    const t = setInterval(load, 2500);
    return () => clearInterval(t);
  }, [started, anyPending, load]);

  async function generate() {
    const model = resolvedModel();
    if (!model) { setMsg("Escolha um modelo."); return; }
    setBusy(true); setMsg("");
    try {
      const body: Record<string, unknown> = { model, extra_prompt: extra.trim() };
      if (scope === "folder" && folderId) body.folder_id = folderId;
      else body.all = true;
      const r = await api.post<{ queued: number }>(`/knowledge/bases/${baseId}/enrich`, body);
      setStarted(true);
      setMsg(r.queued ? `${r.queued} arquivo(s) na fila — gerando…` : "Nada novo para enriquecer (já em aberto ou vazio).");
      await load();
    } catch (e) {
      setMsg(e instanceof ApiError ? e.message : "Falha ao iniciar.");
    } finally { setBusy(false); }
  }

  async function approve(id: string) {
    setItems((xs) => xs.filter((x) => x.id !== id));
    try { await api.post(`/knowledge/enrichments/${id}/approve`); onApplied?.(); } catch { load(); }
  }
  async function dismiss(id: string) {
    setItems((xs) => xs.filter((x) => x.id !== id));
    try { await api.del(`/knowledge/enrichments/${id}`); } catch { load(); }
  }
  async function approveAll() {
    const ready = items.filter((i) => i.status === "ready").map((i) => i.id);
    if (!ready.length) return;
    setItems((xs) => xs.filter((x) => x.status !== "ready"));
    try { await api.post(`/knowledge/bases/${baseId}/enrichments/approve-all`); onApplied?.(); } finally { load(); }
  }
  async function dismissAll() {
    setItems([]); setStarted(false);
    try { await api.del(`/knowledge/bases/${baseId}/enrichments`); } finally { load(); }
  }

  const readyCount = items.filter((i) => i.status === "ready").length;

  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/60 p-4" onClick={onClose}>
      <div onClick={(e) => e.stopPropagation()} className="flex max-h-[86vh] w-full max-w-2xl flex-col overflow-hidden rounded-2xl border border-border bg-surface shadow-2xl">
        <div className="flex items-center justify-between border-b border-border px-4 py-3">
          <span className="flex items-center gap-2 text-sm font-semibold text-ink">
            <Sparkles size={16} className="text-accent-hover" /> Gerar tags e descrições com IA
          </span>
          <button onClick={onClose} className="rounded-lg p-1 text-muted hover:bg-hover hover:text-ink"><X size={16} /></button>
        </div>

        {/* configuração do enriquecimento */}
        <div className="space-y-3 border-b border-border p-4">
          <div className="flex flex-col gap-3 sm:flex-row">
            <div className="flex-1 space-y-1">
              <p className="text-xs text-muted">Modelo que vai gerar</p>
              <ModelField models={extModels} custom={custom} includeCustom value={modelValue} onChange={setModelValue} placeholder="Selecionar modelo (visão p/ imagens)" />
            </div>
            {folderId && (
              <div className="space-y-1">
                <p className="text-xs text-muted">Escopo</p>
                <div className="flex rounded-lg border border-border p-0.5 text-sm">
                  <button onClick={() => setScope("folder")} className={`rounded-md px-3 py-1.5 transition-colors ${scope === "folder" ? "bg-accent text-white" : "text-muted hover:text-ink"}`}>{folderName || "Esta pasta"}</button>
                  <button onClick={() => setScope("base")} className={`rounded-md px-3 py-1.5 transition-colors ${scope === "base" ? "bg-accent text-white" : "text-muted hover:text-ink"}`}>Base inteira</button>
                </div>
              </div>
            )}
          </div>
          <div className="space-y-1">
            <p className="text-xs text-muted">Instrução extra (opcional)</p>
            <textarea
              value={extra} onChange={(e) => setExtra(e.target.value)} rows={2}
              placeholder='Ex.: "coloque a tag amarelo nos itens que têm carro"'
              className="w-full resize-none rounded-lg border border-border bg-bg px-3 py-2 text-sm text-ink outline-none placeholder:text-muted focus:border-accent/50"
            />
          </div>
          <div className="flex items-center gap-3">
            <button onClick={generate} disabled={busy} className="flex items-center gap-1.5 rounded-lg bg-accent px-4 py-1.5 text-sm font-medium text-white transition-colors hover:bg-accent-hover disabled:opacity-50">
              {busy ? <Loader2 size={14} className="animate-spin" /> : <Sparkles size={14} />} Gerar
            </button>
            {msg && <span className="text-xs text-muted">{msg}</span>}
          </div>
        </div>

        {/* lista de propostas */}
        <div className="min-h-0 flex-1 overflow-y-auto p-3">
          {items.length === 0 ? (
            <p className="py-10 text-center text-sm text-muted">
              Escolha um modelo e clique em <span className="font-medium text-ink">Gerar</span>. As propostas aparecem aqui para você aprovar ou descartar.
            </p>
          ) : (
            <>
              <div className="mb-2 flex items-center justify-between px-1">
                <span className="text-xs text-muted">{items.length} item(ns){readyCount ? ` · ${readyCount} pronto(s)` : ""}</span>
                <div className="flex items-center gap-2">
                  <button onClick={approveAll} disabled={!readyCount} className="rounded-lg border border-border px-2.5 py-1 text-xs text-ink transition-colors hover:bg-hover disabled:opacity-40">Aprovar todos</button>
                  <button onClick={dismissAll} className="rounded-lg border border-border px-2.5 py-1 text-xs text-muted transition-colors hover:text-ink">Cancelar todos</button>
                </div>
              </div>
              <ul className="space-y-2">
                {items.map((it) => (
                  <li key={it.id} className="rounded-xl border border-border bg-bg p-3">
                    <div className="mb-1.5 flex items-center gap-1.5 text-xs text-muted">
                      <TypeIcon mime={it.mime} />
                      <span className="truncate">{it.folder ? `${it.folder}/` : ""}{it.filename}</span>
                    </div>
                    {it.status === "pending" ? (
                      <p className="flex items-center gap-1.5 py-1 text-xs text-amber-500"><Loader2 size={12} className="animate-spin" /> gerando…</p>
                    ) : it.status === "error" ? (
                      <div className="flex items-center justify-between gap-2">
                        <p className="flex items-center gap-1.5 text-xs text-rose-400"><TriangleAlert size={12} /> {it.error || "falhou"}</p>
                        <button onClick={() => dismiss(it.id)} className="rounded-lg px-2 py-1 text-xs text-muted hover:text-ink">Remover</button>
                      </div>
                    ) : (
                      <div className="space-y-1.5">
                        {it.title && <p className="text-sm font-semibold text-ink">{it.title}</p>}
                        {it.description && <p className="text-xs leading-5 text-ink-soft">{it.description}</p>}
                        {it.tags.length > 0 && (
                          <div className="flex flex-wrap gap-1">
                            {it.tags.map((t) => <span key={t} className="rounded-full bg-surface2 px-2 py-0.5 text-[11px] text-ink-soft">{t}</span>)}
                          </div>
                        )}
                        <div className="flex items-center gap-2 pt-1">
                          <button onClick={() => approve(it.id)} className="flex items-center gap-1 rounded-lg bg-accent px-3 py-1 text-xs font-medium text-white transition-colors hover:bg-accent-hover"><Check size={12} /> Aprovar</button>
                          <button onClick={() => dismiss(it.id)} className="rounded-lg border border-border px-3 py-1 text-xs text-muted transition-colors hover:text-ink">Cancelar</button>
                        </div>
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
