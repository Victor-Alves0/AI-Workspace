"use client";

import { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { Loader2, MapPin, X } from "lucide-react";
import { api } from "@/lib/api";
import type { ImaginaiMap } from "../types";
import { ImaginaiFeatureStatus } from "../shared";

type Node = ImaginaiMap["locations"][number] & { px: number; py: number };

/** Layout por forças (determinístico): locais ligados se aproximam, todos se repelem.
 * Quem já tem coordenada gravada (x/y do mundo) fica onde está. Com ≤ 12 locais, 300
 * iterações custam nada e dão um grafo legível — "de onde se chega aonde". */
export function layoutMap(data: ImaginaiMap): Node[] {
  const n = data.locations.length;
  const nodes: Node[] = data.locations.map((location, index) => {
    const angle = (index / Math.max(1, n)) * Math.PI * 2;
    return {
      ...location,
      px: location.x ?? 50 + 30 * Math.cos(angle),
      py: location.y ?? 50 + 30 * Math.sin(angle),
    };
  });
  const fixed = (node: Node) => node.x != null && node.y != null;
  const index = new Map(nodes.map((node, i) => [node.id, i]));
  const edges = data.routes
    .map((route) => [index.get(route.from), index.get(route.to)] as const)
    .filter((pair): pair is readonly [number, number] => pair[0] != null && pair[1] != null);
  for (let step = 0; step < 300; step += 1) {
    const cool = 1 - step / 300;
    const force = nodes.map(() => ({ x: 0, y: 0 }));
    for (let a = 0; a < n; a += 1) {
      for (let b = a + 1; b < n; b += 1) {
        const dx = nodes[a].px - nodes[b].px;
        const dy = nodes[a].py - nodes[b].py;
        const dist2 = Math.max(dx * dx + dy * dy, 4);
        const push = 420 / dist2;
        const d = Math.sqrt(dist2);
        force[a].x += (dx / d) * push; force[a].y += (dy / d) * push;
        force[b].x -= (dx / d) * push; force[b].y -= (dy / d) * push;
      }
    }
    for (const [a, b] of edges) {
      const dx = nodes[b].px - nodes[a].px;
      const dy = nodes[b].py - nodes[a].py;
      const d = Math.max(Math.sqrt(dx * dx + dy * dy), 0.01);
      const pull = (d - 26) * 0.06;
      force[a].x += (dx / d) * pull; force[a].y += (dy / d) * pull;
      force[b].x -= (dx / d) * pull; force[b].y -= (dy / d) * pull;
    }
    nodes.forEach((node, i) => {
      if (fixed(node)) return;
      // gravidade leve p/ o centro: ilhas sem rota não fogem da moldura
      force[i].x += (50 - node.px) * 0.01;
      force[i].y += (50 - node.py) * 0.01;
      node.px = Math.min(90, Math.max(10, node.px + Math.max(-4, Math.min(4, force[i].x)) * cool));
      node.py = Math.min(88, Math.max(10, node.py + Math.max(-4, Math.min(4, force[i].y)) * cool));
    });
  }
  return nodes;
}

export function ImaginaiMapPanel({ campaignId }: { campaignId: string }) {
  const [data, setData] = useState<ImaginaiMap | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    api.get<ImaginaiMap>(`/mini-apps/imaginai/campaigns/${campaignId}/map`)
      .then((value) => { if (!cancelled) setData(value); })
      .catch((loadError: unknown) => { if (!cancelled) setError(loadError instanceof Error ? loadError.message : "Não foi possível abrir o mapa"); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [campaignId]);
  const nodes = useMemo(() => (data ? layoutMap(data) : []), [data]);

  if (loading) return <ImaginaiFeatureStatus><Loader2 size={17} className="animate-spin" /></ImaginaiFeatureStatus>;
  if (error || !data) return <ImaginaiFeatureStatus error>{error ?? "Mapa indisponível"}</ImaginaiFeatureStatus>;
  if (!nodes.length) return <ImaginaiFeatureStatus>Nenhum local descoberto.</ImaginaiFeatureStatus>;

  const byId = new Map(nodes.map((node) => [node.id, node]));
  const open = openId ? byId.get(openId) ?? null : null;
  const neighbours = (id: string) => data.routes
    .filter((route) => route.from === id || route.to === id)
    .map((route) => byId.get(route.from === id ? route.to : route.from))
    .filter((node): node is Node => Boolean(node));

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div
        className="relative min-h-0 flex-1 overflow-hidden rounded-xl border border-violet-400/20 bg-[radial-gradient(circle_at_50%_50%,rgba(139,92,246,0.14),transparent_65%),linear-gradient(135deg,rgba(20,20,28,0.9),rgba(12,12,17,0.96))]"
        aria-label="Mapa dos locais descobertos"
      >
        <svg className="pointer-events-none absolute inset-0 h-full w-full" aria-hidden="true">
          {data.routes.map((route) => {
            const from = byId.get(route.from);
            const to = byId.get(route.to);
            return from && to ? <line key={`${route.from}-${route.to}`} x1={`${from.px}%`} y1={`${from.py}%`} x2={`${to.px}%`} y2={`${to.py}%`} stroke="rgba(167,139,250,.5)" strokeWidth="1.5" strokeDasharray="4 3" /> : null;
          })}
        </svg>
        {nodes.map((node) => (
          <button
            key={node.id}
            type="button"
            onClick={() => setOpenId(node.id)}
            style={{ left: `${node.px}%`, top: `${node.py}%` }}
            className="group absolute flex -translate-x-1/2 -translate-y-1/2 flex-col items-center gap-1 focus-visible:outline-none"
          >
            <span className={`flex h-7 w-7 items-center justify-center rounded-full border shadow-lg transition-transform group-hover:scale-110 group-focus-visible:ring-2 group-focus-visible:ring-violet-300 ${node.current ? "border-emerald-300 bg-emerald-400/25 text-emerald-100" : "border-violet-400/50 bg-surface text-violet-200"}`}>
              <MapPin size={13} />
            </span>
            <span className={`max-w-[6.5rem] truncate rounded-md bg-black/55 px-1.5 py-0.5 text-[9px] leading-3 ${node.current ? "text-emerald-100" : "text-ink-soft"}`}>{node.name}</span>
          </button>
        ))}
      </div>
      {open ? <LocationDialog node={open} paths={neighbours(open.id)} onGo={(id) => setOpenId(id)} onClose={() => setOpenId(null)} /> : null}
    </div>
  );
}

function LocationDialog({ node, paths, onGo, onClose }: { node: Node; paths: Node[]; onGo: (id: string) => void; onClose: () => void }) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  // portal: o card do dock tem overflow escondido; a janela é da tela inteira
  return createPortal(
    <div className="fixed inset-0 z-[80] flex items-center justify-center bg-black/55 p-4 backdrop-blur-sm" onMouseDown={onClose}>
      <div role="dialog" aria-modal="true" aria-labelledby="imaginai-location-title" onMouseDown={(event) => event.stopPropagation()} className="w-full max-w-md rounded-2xl border border-border bg-surface p-4 shadow-menu animate-pop">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            {node.current ? <p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-emerald-300">Você está aqui</p> : null}
            <h2 id="imaginai-location-title" className="mt-0.5 text-base font-semibold text-ink">{node.name}</h2>
          </div>
          <button type="button" onClick={onClose} aria-label="Fechar" className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-muted transition-colors hover:bg-hover hover:text-ink"><X size={16} /></button>
        </div>
        <p className="mt-3 max-h-[50vh] overflow-y-auto whitespace-pre-wrap text-sm leading-6 text-ink-soft">{node.description || "Sem descrição registrada."}</p>
        {paths.length ? (
          <div className="mt-4">
            <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted">Caminhos</p>
            <div className="imaginai-chips mt-1.5 flex-wrap">
              {paths.map((path) => <button key={path.id} type="button" onClick={() => onGo(path.id)} className="imaginai-chip"><MapPin size={11} className="mr-1" />{path.name}</button>)}
            </div>
          </div>
        ) : null}
      </div>
    </div>,
    document.body,
  );
}
