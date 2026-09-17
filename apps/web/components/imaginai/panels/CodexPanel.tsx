"use client";

import { useCallback, useEffect, useState } from "react";
import { BookOpen, ChevronLeft, ChevronRight, Loader2, LockKeyhole, Map as MapIcon, Package, Search } from "lucide-react";
import { api } from "@/lib/api";
import type { ImaginaiCodexResult } from "../types";
import { ImaginaiEmptyFeature, ImaginaiFeatureStatus } from "../shared";

export function codexDescription(value: ImaginaiCodexResult["description"]): string {
  if (value == null) return "";
  if (typeof value === "string") return value;
  return Object.entries(value).map(([key, item]) => `${key.replaceAll("_", " ")}: ${String(item)}`).join(" · ");
}

export function ImaginaiCodexPanel({ campaignId }: { campaignId: string }) {
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState("all");
  const [results, setResults] = useState<ImaginaiCodexResult[]>([]);
  const [selected, setSelected] = useState<ImaginaiCodexResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const searchCodex = useCallback(async (search = query, resultKind = kind) => {
    setLoading(true);
    setError(null);
    try {
      const response = await api.get<{ results: ImaginaiCodexResult[] }>(`/mini-apps/imaginai/campaigns/${campaignId}/codex?search=${encodeURIComponent(search)}&kind=${encodeURIComponent(resultKind)}`);
      setResults(response.results);
      setSelected(null);
    } catch (searchError) {
      setError(searchError instanceof Error ? searchError.message : "Não foi possível consultar o Codex");
    } finally {
      setLoading(false);
    }
  }, [campaignId, kind, query]);

  useEffect(() => { void searchCodex("", "all"); }, [campaignId]); // eslint-disable-line react-hooks/exhaustive-deps

  if (selected) {
    const hidden = selected.knowledge === "aware";
    return (
      <div className="imaginai-feature-scroll">
        <button type="button" onClick={() => setSelected(null)} className="imaginai-small-button"><ChevronLeft size={14} /> Resultados</button>
        <div className="mt-3 flex items-start justify-between gap-2">
          <div className="min-w-0">
            <p className="text-[10px] uppercase tracking-wider text-violet-300">{selected.kind}</p>
            <h3 className="mt-0.5 text-sm font-semibold text-ink">{selected.name}</h3>
          </div>
          {selected.knowledge === "rumor" ? <span className="rounded-full bg-amber-400/10 px-2 py-1 text-[9px] text-amber-300">Rumor</span> : null}
        </div>
        {selected.subject ? <p className="mt-1 text-[10px] text-muted">Sobre {selected.subject}</p> : null}
        {hidden ? (
          <div className="mt-4 rounded-xl border border-dashed border-border bg-surface2/35 p-3">
            <div className="flex items-center gap-2 text-xs text-muted"><LockKeyhole size={14} /> Informação ainda não descoberta</div>
            <div className="imaginai-redaction mt-3 w-full" /><div className="imaginai-redaction mt-2 w-4/5" /><div className="imaginai-redaction mt-2 w-2/3" />
          </div>
        ) : (
          <p className="mt-4 whitespace-pre-wrap break-words text-xs leading-5 text-ink-soft">{codexDescription(selected.description) || "Nenhum detalhe registrado."}</p>
        )}
        {selected.confidence != null ? <p className="mt-3 text-[10px] text-muted">Confiança da fonte: {Math.round(selected.confidence * 100)}%</p> : null}
      </div>
    );
  }

  const categories = [{ key: "all", label: "Tudo" }, { key: "npc", label: "NPCs" }, { key: "location", label: "Locais" }, { key: "item", label: "Itens" }, { key: "lore", label: "Lore" }];
  return (
    <div className="imaginai-feature-scroll">
      <h3 className="text-sm font-semibold text-ink">Codex</h3>
      <p className="mt-0.5 text-[10px] leading-4 text-muted">Somente conhecimento descoberto pelo personagem.</p>
      <form onSubmit={(event) => { event.preventDefault(); void searchCodex(); }} className="mt-2 flex gap-1.5">
        <label className="relative min-w-0 flex-1">
          <Search size={13} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-muted" />
          <span className="sr-only">Buscar no Codex</span>
          <input aria-label="Buscar no Codex" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Buscar no mundo" className="imaginai-field imaginai-field-icon" />
        </label>
        <button type="submit" className="imaginai-icon-button" aria-label="Buscar no Codex"><Search size={14} /></button>
      </form>
      <div className="mt-2 flex gap-1 overflow-x-auto pb-1" aria-label="Categorias do Codex">
        {categories.map((category) => <button key={category.key} type="button" onClick={() => { setKind(category.key); void searchCodex(query, category.key); }} className={`min-h-11 shrink-0 rounded-lg px-2 text-[10px] transition-colors ${kind === category.key ? "bg-violet-500/20 text-violet-100" : "text-muted hover:bg-hover hover:text-ink"}`}>{category.label}</button>)}
      </div>
      {loading ? <ImaginaiFeatureStatus><Loader2 size={17} className="animate-spin" /></ImaginaiFeatureStatus> : error ? <ImaginaiFeatureStatus error>{error}</ImaginaiFeatureStatus> : results.length === 0 ? <ImaginaiFeatureStatus>Nada conhecido corresponde à busca. Segredos do mundo não aparecem antes de serem descobertos.</ImaginaiFeatureStatus> : (
        <div className="space-y-1">
          {results.map((result) => <button key={`${result.result_type}-${result.id}`} type="button" onClick={() => setSelected(result)} className="flex w-full items-center gap-2 rounded-xl border border-border bg-surface2/55 p-2.5 text-left transition-colors hover:border-violet-400/30 hover:bg-hover">
            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-violet-500/10 text-violet-300">{result.knowledge === "aware" ? <LockKeyhole size={14} /> : result.kind === "item" ? <Package size={14} /> : result.kind === "location" ? <MapIcon size={14} /> : <BookOpen size={14} />}</span>
            <span className="min-w-0 flex-1"><span className="block truncate text-xs font-medium text-ink">{result.name}</span><span className="block truncate text-[10px] capitalize text-muted">{result.knowledge === "aware" ? "Detalhes ocultos" : result.kind}</span></span>
            <ChevronRight size={14} className="shrink-0 text-muted" />
          </button>)}
        </div>
      )}
    </div>
  );
}
