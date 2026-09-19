"use client";

import { useEffect, useRef, useState } from "react";
import { BookOpen, ChevronLeft, ChevronRight, Flag, Loader2, LockKeyhole, Map as MapIcon, Package, PawPrint, Search, User } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { api } from "@/lib/api";
import type { ImaginaiCodexResult } from "../types";
import { EntityImage, ImaginaiFeatureStatus, mediaSrc } from "../shared";

export function codexDescription(value: ImaginaiCodexResult["description"]): string {
  if (value == null) return "";
  if (typeof value === "string") return value;
  return Object.entries(value).map(([key, item]) => `${key.replaceAll("_", " ")}: ${String(item)}`).join(" · ");
}

const KINDS: Record<string, { label: string; icon: LucideIcon }> = {
  npc: { label: "NPC", icon: User },
  creature: { label: "Criatura", icon: PawPrint },
  location: { label: "Local", icon: MapIcon },
  faction: { label: "Facção", icon: Flag },
  item: { label: "Item", icon: Package },
  lore: { label: "Lore", icon: BookOpen },
};

const CATEGORIES = [
  { key: "all", label: "Tudo" },
  { key: "npc", label: "NPCs" },
  { key: "location", label: "Locais" },
  { key: "faction", label: "Facções" },
  { key: "item", label: "Itens" },
  { key: "lore", label: "Lore" },
];

export function kindLabel(kind: string): string {
  return KINDS[kind]?.label ?? kind;
}

export function ImaginaiCodexPanel({ campaignId }: { campaignId: string }) {
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState("all");
  const [results, setResults] = useState<ImaginaiCodexResult[]>([]);
  const [selected, setSelected] = useState<ImaginaiCodexResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const first = useRef(true);

  // busca ao digitar (com respiro), sem botão: o campo já é a ação
  useEffect(() => {
    let cancelled = false;
    const timer = window.setTimeout(async () => {
      setLoading(true);
      setError(null);
      try {
        const response = await api.get<{ results: ImaginaiCodexResult[] }>(`/mini-apps/imaginai/campaigns/${campaignId}/codex?search=${encodeURIComponent(query)}&kind=${encodeURIComponent(kind)}`);
        if (!cancelled) setResults(response.results);
      } catch (searchError) {
        if (!cancelled) setError(searchError instanceof Error ? searchError.message : "Não foi possível consultar o Codex");
      } finally {
        if (!cancelled) setLoading(false);
      }
    }, first.current ? 0 : 250);
    first.current = false;
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [campaignId, kind, query]);

  if (selected) {
    const hidden = selected.knowledge === "aware";
    return (
      <div className="imaginai-feature-scroll">
        <button type="button" onClick={() => setSelected(null)} className="imaginai-small-button -ml-1.5"><ChevronLeft size={14} /> Codex</button>
        <div className="mt-2 flex items-start justify-between gap-2">
          <div className="min-w-0">
            <p className="text-[10px] uppercase tracking-wider text-violet-300">{kindLabel(selected.kind)}</p>
            <h3 className="mt-0.5 text-sm font-semibold text-ink">{selected.name}</h3>
          </div>
          {selected.knowledge === "rumor" ? <span className="rounded-full bg-amber-400/10 px-2 py-1 text-[9px] text-amber-300">Rumor</span> : null}
        </div>
        {selected.subject ? <p className="mt-1 text-[10px] text-muted">Sobre {selected.subject}</p> : null}
        {selected.image_url && !hidden ? <EntityImage url={selected.image_url} alt={selected.name} className="mt-3 aspect-[4/3] w-full" /> : null}
        {hidden ? (
          <div className="mt-4 rounded-xl border border-dashed border-border bg-surface2/35 p-3">
            <div className="flex items-center gap-2 text-xs text-muted"><LockKeyhole size={14} /> Ainda não descoberto</div>
            <div className="imaginai-redaction mt-3 w-full" /><div className="imaginai-redaction mt-2 w-4/5" /><div className="imaginai-redaction mt-2 w-2/3" />
          </div>
        ) : (
          <p className="mt-3 whitespace-pre-wrap break-words text-xs leading-5 text-ink-soft">{codexDescription(selected.description) || "Nenhum detalhe registrado."}</p>
        )}
        {selected.confidence != null ? <p className="mt-3 text-[10px] text-muted">Confiança da fonte: {Math.round(selected.confidence * 100)}%</p> : null}
      </div>
    );
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <label className="relative block shrink-0">
        <Search size={13} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-muted" />
        <input aria-label="Buscar no Codex" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Buscar no mundo" className="imaginai-field imaginai-field-icon" />
      </label>
      <div className="imaginai-chips mt-2 shrink-0" role="group" aria-label="Categorias do Codex">
        {CATEGORIES.map((category) => <button key={category.key} type="button" aria-pressed={kind === category.key} onClick={() => setKind(category.key)} className="imaginai-chip">{category.label}</button>)}
      </div>
      <div className="imaginai-feature-scroll mt-2">
        {loading && !results.length ? <ImaginaiFeatureStatus><Loader2 size={17} className="animate-spin" /></ImaginaiFeatureStatus> : error ? <ImaginaiFeatureStatus error>{error}</ImaginaiFeatureStatus> : results.length === 0 ? <ImaginaiFeatureStatus>Nada encontrado.</ImaginaiFeatureStatus> : (
          <div className="space-y-1">
            {results.map((result) => {
              const locked = result.knowledge === "aware";
              const Icon = locked ? LockKeyhole : KINDS[result.kind]?.icon ?? BookOpen;
              return <button key={`${result.result_type}-${result.id}`} type="button" onClick={() => setSelected(result)} className="flex w-full items-center gap-2.5 rounded-xl border border-border bg-surface2/55 px-2.5 py-2 text-left transition-colors hover:border-violet-400/30 hover:bg-hover">
                {result.image_url && !locked
                  // eslint-disable-next-line @next/next/no-img-element
                  ? <img src={mediaSrc(result.image_url)} alt="" loading="lazy" className="h-7 w-7 shrink-0 rounded-lg object-cover" />
                  : <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-violet-500/10 text-violet-300"><Icon size={13} /></span>}
                <span className="min-w-0 flex-1"><span className="block truncate text-xs font-medium text-ink">{result.name}</span><span className="block truncate text-[10px] text-muted">{locked ? "Detalhes ocultos" : kindLabel(result.kind)}</span></span>
                <ChevronRight size={14} className="shrink-0 text-muted" />
              </button>;
            })}
          </div>
        )}
      </div>
    </div>
  );
}
