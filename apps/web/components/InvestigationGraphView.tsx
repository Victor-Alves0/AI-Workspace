"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Maximize2, Minus, Plus, RotateCcw, Search, X } from "lucide-react";
import { api } from "@/lib/api";
import type { InvestigationViz, InvestigationVizNode } from "@/lib/types";

type XY = { x: number; y: number };
type View = { x: number; y: number; k: number };

const PAD = 90;
const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));

// paleta categórica fixa (mesma do grafo de código) — cor por TIPO de nó, atribuída
// na ordem em que os tipos aparecem (nunca ciclada por dado).
const PALETTE = [
  "#3b82f6", "#f97316", "#14b8a6", "#ef4444", "#a855f7", "#ec4899",
  "#84cc16", "#06b6d4", "#eab308", "#6366f1", "#f43f5e", "#22c55e",
];
// tracejado da aresta por confiança (igual ao grafo de código investigativo)
const DASH: Record<string, string> = { certain: "", inferred: "5 4", possible: "2 4" };

function hash(s: string): number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); }
  return (h >>> 0) / 4294967295;
}

/** Layout force-directed determinístico (mesmo método do [[BrainGraph]]): repulsão
 *  O(n²) + molas nas arestas + gravidade. Os grafos de investigação são pequenos
 *  (capados em 600 nós no serviço), então O(n²) é de sobra. */
function forceLayout(g: { nodes: { id: string }[]; edges: { source: string; target: string }[] }) {
  const n = g.nodes.length;
  const pos = new Map<string, XY>();
  if (!n) return { pos, bounds: { w: 400, h: 300 } };
  const R = 90 + Math.sqrt(n) * 60;
  const p: Record<string, XY> = {};
  for (const node of g.nodes) {
    const a = hash(node.id) * Math.PI * 2;
    const r = 40 + hash(node.id + "r") * R;
    p[node.id] = { x: Math.cos(a) * r, y: Math.sin(a) * r };
  }
  const L = 150, REP = 5200;
  const ids = g.nodes.map((x) => x.id);
  for (let it = 0; it < 250; it++) {
    const t = 1 - it / 250;
    const disp: Record<string, XY> = {};
    for (const id of ids) disp[id] = { x: 0, y: 0 };
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        const a = p[ids[i]], b = p[ids[j]];
        let dx = a.x - b.x, dy = a.y - b.y;
        let d2 = dx * dx + dy * dy;
        if (d2 < 1) { dx = hash(ids[i] + it) - 0.5; dy = hash(ids[j] + it) - 0.5; d2 = dx * dx + dy * dy + 0.01; }
        const f = REP / d2, d = Math.sqrt(d2);
        disp[ids[i]].x += (dx / d) * f; disp[ids[i]].y += (dy / d) * f;
        disp[ids[j]].x -= (dx / d) * f; disp[ids[j]].y -= (dy / d) * f;
      }
    }
    for (const e of g.edges) {
      const a = p[e.source], b = p[e.target];
      if (!a || !b) continue;
      const dx = b.x - a.x, dy = b.y - a.y;
      const d = Math.sqrt(dx * dx + dy * dy) || 0.01;
      const f = (d - L) * 0.06;
      disp[e.source].x += (dx / d) * f; disp[e.source].y += (dy / d) * f;
      disp[e.target].x -= (dx / d) * f; disp[e.target].y -= (dy / d) * f;
    }
    for (const id of ids) {
      disp[id].x -= p[id].x * 0.03; disp[id].y -= p[id].y * 0.03;
      const d = Math.sqrt(disp[id].x ** 2 + disp[id].y ** 2) || 0.01;
      const step = Math.min(d, 22 * t + 2);
      p[id].x += (disp[id].x / d) * step;
      p[id].y += (disp[id].y / d) * step;
    }
  }
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (const id of ids) {
    minX = Math.min(minX, p[id].x); minY = Math.min(minY, p[id].y);
    maxX = Math.max(maxX, p[id].x); maxY = Math.max(maxY, p[id].y);
  }
  for (const id of ids) pos.set(id, { x: p[id].x - minX + PAD, y: p[id].y - minY + PAD });
  return { pos, bounds: { w: maxX - minX + PAD * 2, h: maxY - minY + PAD * 2 } };
}

const EMPTY: InvestigationViz = {
  graph: { id: "", name: "", kind: "", target: "", description: "" },
  nodes: [], edges: [], truncated: false,
};

/** Grafo de investigação (recon/RE/comportamento): nós = achados tipados, arestas =
 *  relações (tracejadas pela confiança). Pan/zoom/arrastar; clique abre o detalhe. */
export default function InvestigationGraphView({ graphId }: { graphId: string }) {
  const [graph, setGraph] = useState<InvestigationViz | null>(null);
  const [q, setQ] = useState("");
  const [selected, setSelected] = useState<InvestigationVizNode | null>(null);
  const [view, setView] = useState<View>({ x: 0, y: 0, k: 1 });
  const [offsets, setOffsets] = useState<Record<string, XY>>({});
  const viewportRef = useRef<HTMLDivElement>(null);
  const gesture = useRef<{ type: "pan" | "node"; id?: string; sx: number; sy: number; ox: number; oy: number } | null>(null);
  const didFit = useRef(false);
  const moved = useRef(false);

  const load = useCallback(
    () => api.get<InvestigationViz>(`/investigation/graphs/${graphId}/visualize`)
      .then((g) => setGraph(g.error ? EMPTY : g)).catch(() => setGraph(EMPTY)),
    [graphId],
  );
  useEffect(() => { didFit.current = false; setOffsets({}); setSelected(null); load(); }, [load]);

  // cor por tipo: mapeia cada tipo distinto (na ordem de aparição) a um índice fixo
  const typeColor = useMemo(() => {
    const m = new Map<string, string>();
    for (const nd of graph?.nodes ?? []) {
      if (!m.has(nd.type)) m.set(nd.type, PALETTE[m.size % PALETTE.length]);
    }
    return m;
  }, [graph]);

  const { pos, bounds } = useMemo(() => forceLayout(graph ?? EMPTY), [graph]);
  const at = useCallback((id: string): XY => {
    const p = pos.get(id) ?? { x: 0, y: 0 };
    const o = offsets[id];
    return o ? { x: p.x + o.x, y: p.y + o.y } : p;
  }, [pos, offsets]);

  const fit = useCallback(() => {
    const vp = viewportRef.current;
    if (!vp || !pos.size) return;
    const k = clamp(Math.min(vp.clientWidth / bounds.w, vp.clientHeight / bounds.h, 1.2), 0.25, 1.2);
    setView({ k, x: (vp.clientWidth - bounds.w * k) / 2, y: (vp.clientHeight - bounds.h * k) / 2 });
  }, [pos, bounds]);
  useEffect(() => { if (!didFit.current && pos.size) { didFit.current = true; fit(); } }, [pos, fit]);

  function onPointerDownBg(e: React.PointerEvent) {
    if (e.button !== 0) return;
    gesture.current = { type: "pan", sx: e.clientX, sy: e.clientY, ox: view.x, oy: view.y };
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    moved.current = false;
  }
  function onPointerDownNode(e: React.PointerEvent, id: string) {
    if (e.button !== 0) return;
    e.stopPropagation();
    const o = offsets[id] ?? { x: 0, y: 0 };
    gesture.current = { type: "node", id, sx: e.clientX, sy: e.clientY, ox: o.x, oy: o.y };
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    moved.current = false;
  }
  function onPointerMove(e: React.PointerEvent) {
    const g = gesture.current;
    if (!g) return;
    const dx = e.clientX - g.sx, dy = e.clientY - g.sy;
    if (Math.abs(dx) > 3 || Math.abs(dy) > 3) moved.current = true;
    if (g.type === "pan") setView((v) => ({ ...v, x: g.ox + dx, y: g.oy + dy }));
    else if (g.id) setOffsets((m) => ({ ...m, [g.id!]: { x: g.ox + dx / view.k, y: g.oy + dy / view.k } }));
  }
  function onPointerUp(e: React.PointerEvent) {
    try { (e.currentTarget as HTMLElement).releasePointerCapture(e.pointerId); } catch { /* noop */ }
    gesture.current = null;
  }
  function onWheel(e: React.WheelEvent) {
    const vp = viewportRef.current;
    if (!vp) return;
    const rect = vp.getBoundingClientRect();
    const mx = e.clientX - rect.left, my = e.clientY - rect.top;
    setView((v) => {
      const k2 = clamp(v.k * (e.deltaY < 0 ? 1.12 : 1 / 1.12), 0.25, 2.5);
      const wx = (mx - v.x) / v.k, wy = (my - v.y) / v.k;
      return { k: k2, x: mx - wx * k2, y: my - wy * k2 };
    });
  }
  const zoom = (dir: number) => {
    const vp = viewportRef.current;
    const cx = (vp?.clientWidth ?? 0) / 2, cy = (vp?.clientHeight ?? 0) / 2;
    setView((v) => {
      const k2 = clamp(v.k * (dir > 0 ? 1.2 : 1 / 1.2), 0.25, 2.5);
      const wx = (cx - v.x) / v.k, wy = (cy - v.y) / v.k;
      return { k: k2, x: cx - wx * k2, y: cy - wy * k2 };
    });
  };

  const match = useMemo(() => {
    const f = q.trim().toLowerCase();
    if (!f || !graph) return null;
    return new Set(graph.nodes.filter(
      (nd) => nd.label.toLowerCase().includes(f) || nd.type.toLowerCase().includes(f),
    ).map((nd) => nd.id));
  }, [graph, q]);

  // vizinhança do selecionado (realça nó + arestas ligadas)
  const neighbors = useMemo(() => {
    if (!selected || !graph) return null;
    const s = new Set<string>([selected.id]);
    for (const e of graph.edges) {
      if (e.source === selected.id) s.add(e.target);
      if (e.target === selected.id) s.add(e.source);
    }
    return s;
  }, [selected, graph]);

  const radius = (nd: InvestigationVizNode) => (nd.confidence === "certain" ? 9 : 7);

  return (
    <div className="relative h-[62vh] min-h-[340px] overflow-hidden rounded-2xl border border-border bg-[radial-gradient(circle,rgb(var(--c-border))_1px,transparent_1px)] [background-size:22px_22px]">
      {/* busca */}
      <div className="absolute left-3 top-3 z-10 flex items-center gap-2 rounded-lg border border-border bg-surface/90 px-2.5 py-1.5 backdrop-blur">
        <Search size={13} className="text-muted" />
        <input
          value={q} onChange={(e) => setQ(e.target.value)} placeholder="Filtrar por rótulo/tipo…"
          className="w-44 bg-transparent text-xs text-ink outline-none placeholder:text-muted"
        />
      </div>

      {/* legenda de tipos */}
      {typeColor.size > 0 && (
        <div className="absolute right-3 top-3 z-10 flex max-w-[45%] flex-wrap justify-end gap-x-2 gap-y-1 rounded-lg border border-border bg-surface/90 px-2 py-1.5 backdrop-blur">
          {Array.from(typeColor.entries()).map(([t, c]) => (
            <span key={t} className="flex items-center gap-1 text-[10px] text-ink-soft">
              <span className="inline-block h-2 w-2 rounded-full" style={{ background: c }} />{t}
            </span>
          ))}
        </div>
      )}

      {graph === null ? (
        <p className="grid h-full place-items-center text-sm text-muted">Carregando…</p>
      ) : graph.nodes.length === 0 ? (
        <p className="grid h-full place-items-center px-8 text-center text-sm text-muted">
          Grafo vazio. Peça à IA para investigar um alvo/binário — ela registra os achados aqui.
        </p>
      ) : (
        <div
          ref={viewportRef}
          onPointerDown={onPointerDownBg}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onWheel={onWheel}
          className="h-full w-full cursor-grab touch-none select-none active:cursor-grabbing"
        >
          <div className="absolute left-0 top-0 origin-top-left" style={{ transform: `translate(${view.x}px, ${view.y}px) scale(${view.k})` }}>
            {/* arestas (tracejado por confiança) */}
            <svg width={bounds.w} height={bounds.h} className="absolute left-0 top-0 overflow-visible" style={{ pointerEvents: "none" }}>
              {graph.edges.map((e) => {
                const a = at(e.source), b = at(e.target);
                const dim = (!!match && (!match.has(e.source) || !match.has(e.target)))
                  || (!!neighbors && (!neighbors.has(e.source) || !neighbors.has(e.target)));
                return (
                  <line key={e.id} x1={a.x} y1={a.y} x2={b.x} y2={b.y}
                    stroke="rgb(var(--c-border))" strokeWidth={1.5}
                    strokeDasharray={DASH[e.confidence] || undefined}
                    opacity={dim ? 0.12 : 0.75} />
                );
              })}
            </svg>
            {/* nós (círculo colorido por tipo + rótulo) */}
            {graph.nodes.map((nd) => {
              const p = at(nd.id);
              const r = radius(nd);
              const dim = (!!match && !match.has(nd.id)) || (!!neighbors && !neighbors.has(nd.id));
              const isSel = selected?.id === nd.id;
              return (
                <div
                  key={nd.id}
                  onPointerDown={(e) => onPointerDownNode(e, nd.id)}
                  onPointerUp={() => { if (!moved.current) setSelected((c) => (c?.id === nd.id ? null : nd)); }}
                  title={`${nd.type}: ${nd.label} (${nd.confidence})`}
                  className={`absolute flex cursor-pointer flex-col items-center transition-opacity ${dim ? "opacity-20" : "opacity-100"}`}
                  style={{ left: p.x - 60, top: p.y - r, width: 120 }}
                >
                  <span
                    className="rounded-full transition-transform hover:scale-110"
                    style={{
                      width: r * 2, height: r * 2,
                      background: typeColor.get(nd.type) ?? "#8a93a3",
                      outline: isSel ? "2px solid rgb(var(--c-ink))" : "none",
                      outlineOffset: 2,
                      opacity: nd.confidence === "possible" ? 0.6 : 1,
                    }}
                  />
                  <span className="mt-1 max-w-[120px] truncate text-center text-[11px] leading-tight text-ink-soft">
                    {nd.label}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* detalhe do nó selecionado */}
      {selected && (
        <div className="absolute bottom-3 left-3 z-10 max-w-[min(420px,72%)] rounded-lg border border-border bg-surface/95 p-2.5 backdrop-blur">
          <div className="mb-1 flex items-start gap-2">
            <span className="mt-1 inline-block h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: typeColor.get(selected.type) ?? "#8a93a3" }} />
            <div className="min-w-0 flex-1">
              <p className="break-words font-mono text-[12px] text-ink">{selected.label}</p>
              <p className="text-[10px] text-muted">{selected.type} · confiança: {selected.confidence}</p>
            </div>
            <button onClick={() => setSelected(null)} className="shrink-0 rounded p-0.5 text-muted hover:text-ink"><X size={13} /></button>
          </div>
          {(() => {
            const entries = Object.entries(selected.props || {}).filter(([k]) => k !== "notes");
            const notes = (selected.props?.notes as string[] | undefined) || [];
            return (
              <>
                {entries.length > 0 && (
                  <dl className="mt-1 grid grid-cols-[auto_1fr] gap-x-2 gap-y-0.5 text-[11px]">
                    {entries.map(([k, v]) => (
                      <div key={k} className="contents">
                        <dt className="text-muted">{k}</dt>
                        <dd className="min-w-0 break-words font-mono text-ink-soft">{typeof v === "object" ? JSON.stringify(v) : String(v)}</dd>
                      </div>
                    ))}
                  </dl>
                )}
                {notes.length > 0 && (
                  <ul className="mt-1.5 space-y-1 border-t border-border pt-1.5 text-[11px] text-ink-soft">
                    {notes.map((t, i) => <li key={i} className="break-words">• {t}</li>)}
                  </ul>
                )}
              </>
            );
          })()}
        </div>
      )}

      {graph?.truncated && (
        <p className="absolute bottom-3 left-1/2 z-10 -translate-x-1/2 rounded-full border border-border bg-surface/90 px-2.5 py-1 text-[10px] text-muted backdrop-blur">
          grafo grande — mostrando os primeiros nós
        </p>
      )}

      {/* controles */}
      <div className="absolute bottom-3 right-3 flex flex-col gap-1 rounded-xl border border-border bg-surface/90 p-1 shadow-menu backdrop-blur">
        <button onClick={() => zoom(1)} title="Aproximar" className="rounded-lg p-1.5 text-muted hover:bg-hover hover:text-ink"><Plus size={15} /></button>
        <button onClick={() => zoom(-1)} title="Afastar" className="rounded-lg p-1.5 text-muted hover:bg-hover hover:text-ink"><Minus size={15} /></button>
        <button onClick={() => { setOffsets({}); setSelected(null); fit(); }} title="Ajustar à tela" className="rounded-lg p-1.5 text-muted hover:bg-hover hover:text-ink"><Maximize2 size={15} /></button>
        <button onClick={() => { didFit.current = false; load(); }} title="Recarregar" className="rounded-lg p-1.5 text-muted hover:bg-hover hover:text-ink"><RotateCcw size={15} /></button>
      </div>
    </div>
  );
}
