"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { LayoutGrid, Loader2, MapPin, Maximize2, Minus, Plus, X } from "lucide-react";
import { api } from "@/lib/api";
import type { ImaginaiMap } from "../types";
import { EntityImage, ImaginaiFeatureStatus, ImaginaiToolbar } from "../shared";

type Node = ImaginaiMap["locations"][number] & { px: number; py: number };
type XY = { x: number; y: number };
type View = { x: number; y: number; k: number };

/** Tamanho do "mundo" do canvas (px). O layout trabalha em 0–100 e é escalado. */
const WORLD = 640;
const clamp = (value: number, low: number, high: number) => Math.min(high, Math.max(low, value));

/** Layout por forças (determinístico): locais ligados se aproximam, todos se repelem.
 * Quem já tem coordenada gravada (x/y do mundo) fica onde está. */
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
      force[i].x += (50 - node.px) * 0.01;
      force[i].y += (50 - node.py) * 0.01;
      node.px = Math.min(95, Math.max(5, node.px + Math.max(-4, Math.min(4, force[i].x)) * cool));
      node.py = Math.min(95, Math.max(5, node.py + Math.max(-4, Math.min(4, force[i].y)) * cool));
    });
  }
  return nodes;
}

/** Mapa como canvas (mesmos gestos do grafo do second brain, estilo Obsidian):
 * arrastar o fundo move a vista, a roda aproxima/afasta no ponteiro, cada local se
 * arrasta, e o clique (sem arrastar) abre a janela do local. */
export function ImaginaiMapPanel({ campaignId }: { campaignId: string }) {
  const [data, setData] = useState<ImaginaiMap | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [view, setView] = useState<View>({ x: 0, y: 0, k: 1 });
  const [offsets, setOffsets] = useState<Record<string, XY>>({});
  const viewportRef = useRef<HTMLDivElement>(null);
  const gesture = useRef<{ type: "pan" | "node"; id?: string; sx: number; sy: number; ox: number; oy: number } | null>(null);
  const moved = useRef(false);
  const didFit = useRef(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    didFit.current = false;
    setOffsets({});
    api.get<ImaginaiMap>(`/mini-apps/imaginai/campaigns/${campaignId}/map`)
      .then((value) => { if (!cancelled) setData(value); })
      .catch((loadError: unknown) => { if (!cancelled) setError(loadError instanceof Error ? loadError.message : "Não foi possível abrir o mapa"); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [campaignId]);

  const nodes = useMemo(() => (data ? layoutMap(data) : []), [data]);
  const at = useCallback((node: Node): XY => {
    const o = offsets[node.id];
    return { x: (node.px / 100) * WORLD + (o?.x ?? 0), y: (node.py / 100) * WORLD + (o?.y ?? 0) };
  }, [offsets]);

  const fit = useCallback(() => {
    const vp = viewportRef.current;
    if (!vp || !nodes.length) return;
    const pts = nodes.map((node) => at(node));
    const minX = Math.min(...pts.map((p) => p.x)) - 60, maxX = Math.max(...pts.map((p) => p.x)) + 60;
    const minY = Math.min(...pts.map((p) => p.y)) - 50, maxY = Math.max(...pts.map((p) => p.y)) + 60;
    const k = clamp(Math.min(vp.clientWidth / (maxX - minX), vp.clientHeight / (maxY - minY)), 0.3, 1.6);
    setView({ k, x: (vp.clientWidth - (maxX + minX) * k) / 2, y: (vp.clientHeight - (maxY + minY) * k) / 2 });
  }, [nodes, at]);
  useEffect(() => {
    if (!didFit.current && nodes.length && viewportRef.current) { didFit.current = true; fit(); }
  }, [nodes, fit]);

  const match = useMemo(() => {
    const wanted = query.trim().toLocaleLowerCase("pt-BR");
    if (!wanted) return null;
    return new Set(nodes.filter((node) => node.name.toLocaleLowerCase("pt-BR").includes(wanted)).map((node) => node.id));
  }, [nodes, query]);

  /** Enter na busca: centraliza no primeiro local encontrado. */
  function focusFirstMatch() {
    const vp = viewportRef.current;
    const first = nodes.find((node) => match?.has(node.id));
    if (!vp || !first) return;
    const p = at(first);
    setView((v) => ({ ...v, x: vp.clientWidth / 2 - p.x * v.k, y: vp.clientHeight / 2 - p.y * v.k }));
  }

  function onPointerDownBg(event: React.PointerEvent) {
    if (event.button !== 0) return;
    gesture.current = { type: "pan", sx: event.clientX, sy: event.clientY, ox: view.x, oy: view.y };
    (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
    moved.current = false;
  }
  function onPointerDownNode(event: React.PointerEvent, id: string) {
    if (event.button !== 0) return;
    event.stopPropagation();
    const o = offsets[id] ?? { x: 0, y: 0 };
    gesture.current = { type: "node", id, sx: event.clientX, sy: event.clientY, ox: o.x, oy: o.y };
    (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
    moved.current = false;
  }
  function onPointerMove(event: React.PointerEvent) {
    const g = gesture.current;
    if (!g) return;
    const dx = event.clientX - g.sx, dy = event.clientY - g.sy;
    if (Math.abs(dx) > 3 || Math.abs(dy) > 3) moved.current = true;
    if (g.type === "pan") setView((v) => ({ ...v, x: g.ox + dx, y: g.oy + dy }));
    else if (g.id) setOffsets((m) => ({ ...m, [g.id!]: { x: g.ox + dx / view.k, y: g.oy + dy / view.k } }));
  }
  function onPointerUp(event: React.PointerEvent) {
    try { (event.currentTarget as HTMLElement).releasePointerCapture(event.pointerId); } catch { /* noop */ }
    const soltouLocal = gesture.current?.type === "node" && moved.current;
    gesture.current = null;
    if (soltouLocal) void persistLayout();
  }

  /** Grava o mapa INTEIRO como está na tela (não só o local arrastado): com uma posição
   * fixa nova o layout automático recalcularia os outros e eles "pulariam". */
  async function persistLayout() {
    const positions = Object.fromEntries(nodes.map((node) => {
      const p = at(node);
      return [node.id, { x: clamp((p.x / WORLD) * 100, 0, 100), y: clamp((p.y / WORLD) * 100, 0, 100) }];
    }));
    try {
      const atualizado = await api.patch<ImaginaiMap>(`/mini-apps/imaginai/campaigns/${campaignId}/map/positions`, { positions });
      setData(atualizado);
      setOffsets({});
    } catch { /* posição só não fica gravada; o arraste continua valendo na tela */ }
  }

  async function resetLayout() {
    const positions = Object.fromEntries(nodes.map((node) => [node.id, null]));
    try {
      const atualizado = await api.patch<ImaginaiMap>(`/mini-apps/imaginai/campaigns/${campaignId}/map/positions`, { positions });
      didFit.current = false;
      setOffsets({});
      setData(atualizado);
    } catch { /* noop */ }
  }
  useEffect(() => {
    const vp = viewportRef.current;
    if (!vp) return;
    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      event.stopPropagation();
      const rect = vp.getBoundingClientRect();
      const mx = event.clientX - rect.left, my = event.clientY - rect.top;
      setView((v) => {
        const k2 = clamp(v.k * (event.deltaY < 0 ? 1.12 : 1 / 1.12), 0.3, 2.5);
        const wx = (mx - v.x) / v.k, wy = (my - v.y) / v.k;
        return { k: k2, x: mx - wx * k2, y: my - wy * k2 };
      });
    };
    vp.addEventListener("wheel", onWheel, { passive: false });
    return () => vp.removeEventListener("wheel", onWheel);
  }, [nodes.length, loading]);
  const zoom = (dir: number) => {
    const vp = viewportRef.current;
    const cx = (vp?.clientWidth ?? 0) / 2, cy = (vp?.clientHeight ?? 0) / 2;
    setView((v) => {
      const k2 = clamp(v.k * (dir > 0 ? 1.2 : 1 / 1.2), 0.3, 2.5);
      const wx = (cx - v.x) / v.k, wy = (cy - v.y) / v.k;
      return { k: k2, x: cx - wx * k2, y: cy - wy * k2 };
    });
  };

  if (loading) return <ImaginaiFeatureStatus><Loader2 size={17} className="animate-spin" /></ImaginaiFeatureStatus>;
  if (error || !data) return <ImaginaiFeatureStatus error>{error ?? "Mapa indisponível"}</ImaginaiFeatureStatus>;

  const byId = new Map(nodes.map((node) => [node.id, node]));
  const open = openId ? byId.get(openId) ?? null : null;
  const neighbours = (id: string) => data.routes
    .filter((route) => route.from === id || route.to === id)
    .map((route) => byId.get(route.from === id ? route.to : route.from))
    .filter((node): node is Node => Boolean(node));

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <form onSubmit={(event) => { event.preventDefault(); focusFirstMatch(); }}>
        <ImaginaiToolbar value={query} onChange={setQuery} placeholder="Buscar local" />
      </form>
      <div className="relative mt-2 min-h-0 flex-1 overflow-hidden rounded-xl bg-[radial-gradient(circle,rgb(var(--c-border))_1px,transparent_1px)] [background-size:20px_20px]">
        {nodes.length === 0 ? <ImaginaiFeatureStatus>Nenhum local descoberto.</ImaginaiFeatureStatus> : (
          <div
            ref={viewportRef}
            onPointerDown={onPointerDownBg}
            onPointerMove={onPointerMove}
            onPointerUp={onPointerUp}
            aria-label="Mapa dos locais descobertos"
            className="absolute inset-0 cursor-grab touch-none select-none active:cursor-grabbing"
          >
            <div className="absolute left-0 top-0 origin-top-left" style={{ transform: `translate(${view.x}px, ${view.y}px) scale(${view.k})` }}>
              <svg width={WORLD} height={WORLD} className="pointer-events-none absolute left-0 top-0 overflow-visible" aria-hidden="true">
                {data.routes.map((route) => {
                  const from = byId.get(route.from);
                  const to = byId.get(route.to);
                  if (!from || !to) return null;
                  const a = at(from), b = at(to);
                  const dim = match && (!match.has(from.id) && !match.has(to.id));
                  return <line key={`${route.from}-${route.to}`} x1={a.x} y1={a.y} x2={b.x} y2={b.y} stroke="rgba(167,139,250,.55)" strokeWidth={1.5} strokeDasharray="5 4" opacity={dim ? 0.2 : 1} />;
                })}
              </svg>
              {nodes.map((node) => {
                const p = at(node);
                const dim = Boolean(match) && !match!.has(node.id);
                return (
                  <div
                    key={node.id}
                    role="button"
                    tabIndex={0}
                    onPointerDown={(event) => onPointerDownNode(event, node.id)}
                    onPointerUp={() => { if (!moved.current) setOpenId(node.id); }}
                    onKeyDown={(event) => { if (event.key === "Enter") setOpenId(node.id); }}
                    title={node.name}
                    className={`group absolute flex w-[120px] cursor-pointer flex-col items-center transition-opacity focus-visible:outline-none ${dim ? "opacity-25" : "opacity-100"}`}
                    style={{ left: p.x - 60, top: p.y - 14 }}
                  >
                    <span className={`flex h-7 w-7 items-center justify-center rounded-full border transition-transform group-hover:scale-110 group-focus-visible:ring-2 group-focus-visible:ring-violet-300 ${node.current ? "border-emerald-300 bg-emerald-400/25 text-emerald-100" : "border-violet-400/50 bg-surface text-violet-200"}`}>
                      <MapPin size={13} />
                    </span>
                    <span className={`mt-1 max-w-[120px] truncate rounded-md bg-black/55 px-1.5 py-0.5 text-center text-[10px] leading-3 ${node.current ? "text-emerald-100" : "text-ink-soft"}`}>{node.name}</span>
                  </div>
                );
              })}
            </div>
          </div>
        )}
        <div className="absolute bottom-2 right-2 flex flex-col gap-0.5 rounded-xl border border-border bg-surface/90 p-0.5">
          <button type="button" onClick={() => zoom(1)} title="Aproximar" aria-label="Aproximar" className="rounded-lg p-1.5 text-muted hover:bg-hover hover:text-ink"><Plus size={14} /></button>
          <button type="button" onClick={() => zoom(-1)} title="Afastar" aria-label="Afastar" className="rounded-lg p-1.5 text-muted hover:bg-hover hover:text-ink"><Minus size={14} /></button>
          <button type="button" onClick={() => fit()} title="Ajustar à tela" aria-label="Ajustar à tela" className="rounded-lg p-1.5 text-muted hover:bg-hover hover:text-ink"><Maximize2 size={14} /></button>
          <button type="button" onClick={() => void resetLayout()} title="Voltar ao layout automático" aria-label="Voltar ao layout automático" className="rounded-lg p-1.5 text-muted hover:bg-hover hover:text-ink"><LayoutGrid size={14} /></button>
        </div>
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
  // portal: o card do dock recorta o conteúdo; a janela é da tela inteira
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
        {node.image_url ? <EntityImage url={node.image_url} alt={node.name} className="mt-3 aspect-video w-full" /> : null}
        <p className="mt-3 max-h-[40vh] overflow-y-auto whitespace-pre-wrap text-sm leading-6 text-ink-soft">{node.description || "Sem descrição registrada."}</p>
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
