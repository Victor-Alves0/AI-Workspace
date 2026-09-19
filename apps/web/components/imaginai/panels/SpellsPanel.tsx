"use client";

import { useEffect, useState } from "react";
import { ChevronLeft, ChevronRight, Loader2, Search, Sparkles } from "lucide-react";
import { api } from "@/lib/api";
import type { ImaginaiSpell, ImaginaiSpells } from "../types";
import { ImaginaiFeatureStatus } from "../shared";

export function spellComponents(value: unknown): string {
  if (Array.isArray(value)) return value.map((item) => String(item)).filter(Boolean).join(", ");
  return typeof value === "string" ? value : "";
}

export function ImaginaiSpellsPanel({ campaignId }: { campaignId: string }) {
  const [data, setData] = useState<ImaginaiSpells | null>(null);
  const [selected, setSelected] = useState<ImaginaiSpell | null>(null);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    api.get<ImaginaiSpells>(`/mini-apps/imaginai/campaigns/${campaignId}/spells`)
      .then((value) => { if (!cancelled) setData(value); })
      .catch((loadError: unknown) => { if (!cancelled) setError(loadError instanceof Error ? loadError.message : "Não foi possível abrir as magias"); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [campaignId]);
  if (loading) return <ImaginaiFeatureStatus><Loader2 size={17} className="animate-spin" /></ImaginaiFeatureStatus>;
  if (error || !data) return <ImaginaiFeatureStatus error>{error ?? "Magias indisponíveis"}</ImaginaiFeatureStatus>;
  if (selected) return (
    <div className="imaginai-feature-scroll">
      <button type="button" onClick={() => setSelected(null)} className="imaginai-small-button"><ChevronLeft size={14} /> Grimório</button>
      <div className="mt-3 flex items-start justify-between gap-2"><div className="min-w-0"><p className="text-[10px] uppercase tracking-wider text-violet-300">{selected.level === 0 ? "Truque" : `${selected.level}º nível`}{selected.school ? ` · ${selected.school}` : ""}</p><h3 className="mt-0.5 text-sm font-semibold text-ink">{selected.name}</h3></div><span className={`rounded-full px-2 py-1 text-[9px] ${selected.prepared ? "bg-emerald-400/10 text-emerald-300" : "bg-amber-400/10 text-amber-300"}`}>{selected.prepared ? "Preparada" : "Não preparada"}</span></div>
      <div className="mt-3 grid grid-cols-2 gap-1.5 text-[10px]">{[["Conjuração", selected.casting_time], ["Alcance", selected.range], ["Duração", selected.duration], ["Componentes", spellComponents(selected.components)]].map(([label, value]) => <div key={label} className="rounded-lg border border-border bg-surface2/55 px-2 py-1.5"><span className="block text-[8px] uppercase tracking-wide text-muted">{label}</span><span className="mt-0.5 block truncate text-ink-soft" title={value}>{value || "—"}</span></div>)}</div>
      <div className="mt-2 flex flex-wrap gap-1">{selected.concentration ? <span className="rounded-full bg-violet-500/15 px-2 py-1 text-[9px] text-violet-200">Concentração</span> : null}{selected.ritual ? <span className="rounded-full bg-sky-400/10 px-2 py-1 text-[9px] text-sky-200">Ritual</span> : null}</div>
      <p className="mt-4 whitespace-pre-wrap break-words text-xs leading-5 text-ink-soft">{selected.description || "Os detalhes desta magia ainda não foram registrados na ficha."}</p>
    </div>
  );
  const filtered = data.spells.filter((spell) => spell.name.toLocaleLowerCase("pt-BR").includes(query.trim().toLocaleLowerCase("pt-BR")));
  const groups = new Map<number, ImaginaiSpell[]>();
  for (const spell of filtered) groups.set(spell.level, [...(groups.get(spell.level) ?? []), spell]);
  return (
    <div className="imaginai-feature-scroll">
      <div className="flex items-center justify-between gap-2"><h3 className="text-sm font-semibold text-ink">Magias</h3>{data.save_dc > 0 ? <span className="rounded-lg border border-border bg-surface2/55 px-2 py-1 text-[10px] text-ink-soft">CD {data.save_dc}</span> : null}</div>
      {Object.keys(data.slots).length ? <div className="mt-2 grid grid-cols-4 gap-1">{Object.entries(data.slots).map(([level, slot]) => <div key={level} className="rounded-lg border border-border bg-surface2/55 px-1.5 py-1.5 text-center"><span className="block text-[8px] uppercase tracking-wide text-muted">{level}º nível</span><span className="mt-0.5 block font-mono text-[11px] text-ink">{slot.current}/{slot.max}</span></div>)}</div> : null}
      <label className="relative mt-2 block"><Search size={13} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-muted" /><span className="sr-only">Buscar magia</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Buscar magia" className="imaginai-field imaginai-field-icon" /></label>
      {filtered.length === 0 ? <ImaginaiFeatureStatus>Nenhuma magia.</ImaginaiFeatureStatus> : <div className="mt-2 space-y-3">{[...groups.entries()].map(([level, spells]) => <section key={level}><p className="mb-1 text-[9px] font-semibold uppercase tracking-[0.12em] text-violet-300">{level === 0 ? "Truques" : `${level}º nível`}</p><div className="space-y-1">{spells.map((spell) => <button key={spell.key} type="button" onClick={() => setSelected(spell)} className="flex w-full items-center gap-2 rounded-xl border border-border bg-surface2/55 p-2.5 text-left transition-colors hover:border-violet-400/30 hover:bg-hover"><span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-violet-500/10 text-violet-300"><Sparkles size={14} /></span><span className="min-w-0 flex-1"><span className="block truncate text-xs font-medium text-ink">{spell.name}</span><span className="block truncate text-[10px] text-muted">{spell.school || "Magia"}{spell.concentration ? " · concentração" : ""}</span></span>{!spell.prepared ? <span title="Não preparada" className="h-2 w-2 shrink-0 rounded-full bg-amber-300" /> : null}<ChevronRight size={14} className="shrink-0 text-muted" /></button>)}</div></section>)}</div>}
    </div>
  );
}
