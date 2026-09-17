"use client";

import { useEffect, useState } from "react";
import { Loader2, Map as MapIcon } from "lucide-react";
import { api } from "@/lib/api";
import type { ImaginaiMap } from "../types";
import { ImaginaiFeatureStatus } from "../shared";

export function ImaginaiMapPanel({ campaignId }: { campaignId: string }) {
  const [data, setData] = useState<ImaginaiMap | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    api.get<ImaginaiMap>(`/mini-apps/imaginai/campaigns/${campaignId}/map`)
      .then((value) => { if (!cancelled) { setData(value); setSelectedId(value.locations.find((location) => location.current)?.id ?? value.locations[0]?.id ?? null); } })
      .catch((loadError: unknown) => { if (!cancelled) setError(loadError instanceof Error ? loadError.message : "Não foi possível abrir o mapa"); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [campaignId]);
  if (loading) return <ImaginaiFeatureStatus><Loader2 size={17} className="animate-spin" /></ImaginaiFeatureStatus>;
  if (error || !data) return <ImaginaiFeatureStatus error>{error ?? "Mapa indisponível"}</ImaginaiFeatureStatus>;
  if (!data.locations.length) return <ImaginaiFeatureStatus>Nenhum local foi descoberto ainda.</ImaginaiFeatureStatus>;
  const positions = data.locations.map((location, index) => ({ ...location, x: location.x ?? 14 + ((index * 37) % 72), y: location.y ?? 16 + ((index * 29) % 68) }));
  const byId = new Map(positions.map((location) => [location.id, location]));
  const selected = byId.get(selectedId ?? "") ?? positions[0];
  return (
    <div className="imaginai-feature-scroll"><div className="flex items-center justify-between gap-2"><div><h3 className="text-sm font-semibold text-ink">Mapa</h3><p className="mt-0.5 text-[10px] text-muted">Somente locais e caminhos já descobertos.</p></div><span className="text-[10px] text-muted">{positions.length} local{positions.length === 1 ? "" : "is"}</span></div>
      <div className="relative mt-3 h-64 overflow-hidden rounded-xl border border-violet-400/20 bg-[radial-gradient(circle_at_50%_50%,rgba(139,92,246,0.14),transparent_65%),linear-gradient(135deg,rgba(20,20,28,0.9),rgba(12,12,17,0.96))]" aria-label="Mapa dos locais descobertos"><svg className="pointer-events-none absolute inset-0 h-full w-full" aria-hidden="true">{data.routes.map((route) => { const from = byId.get(route.from); const to = byId.get(route.to); return from && to ? <line key={`${route.from}-${route.to}`} x1={`${from.x}%`} y1={`${from.y}%`} x2={`${to.x}%`} y2={`${to.y}%`} stroke="rgba(167,139,250,.48)" strokeWidth="1.5" strokeDasharray="4 3" /> : null; })}</svg>{positions.map((location) => <button key={location.id} type="button" onClick={() => setSelectedId(location.id)} title={location.name} style={{ left: `${location.x}%`, top: `${location.y}%` }} className={`absolute -translate-x-1/2 -translate-y-1/2 rounded-full border p-1.5 shadow-lg transition-transform hover:scale-110 focus-visible:outline focus-visible:outline-2 focus-visible:outline-violet-300 ${location.current ? "border-emerald-300 bg-emerald-400/20 text-emerald-200" : selected.id === location.id ? "border-violet-200 bg-violet-500/35 text-white" : "border-violet-400/40 bg-surface text-violet-200"}`}><MapIcon size={14} /><span className="sr-only">{location.name}</span></button>)}</div>
      <div className="mt-2 rounded-xl border border-border bg-surface2/55 p-2.5"><div className="flex items-center gap-2"><MapIcon size={14} className={selected.current ? "text-emerald-300" : "text-violet-300"} /><h4 className="min-w-0 truncate text-xs font-medium text-ink">{selected.name}</h4>{selected.current ? <span className="ml-auto shrink-0 text-[9px] text-emerald-300">Você está aqui</span> : null}</div><p className="mt-1 line-clamp-3 text-[10px] leading-4 text-muted">{selected.description || "Local conhecido, sem descrição registrada."}</p></div>
    </div>
  );
}
