"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { FileCode2, Maximize2, Minus, Plus, RotateCcw, Search } from "lucide-react";
import { api } from "@/lib/api";
import type { CodespaceViz, CodespaceVizNode } from "@/lib/types";

type XY = { x: number; y: number };
type View = { x: number; y: number; k: number };

const PAD = 90;
const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));

// paleta categórica fixa (ordem nunca ciclada por dado — só por índice de domínio)
const PALETTE = [
  "#3b82f6", "#f97316", "#14b8a6", "#ef4444", "#a855f7", "#ec4899",
  "#84cc16", "#06b6d4", "#eab308", "#6366f1", "#f43f5e", "#22c55e",
];
function domainColor(domain: number | null): string {
  if (domain == null) return "#8a93a3";
  return PALETTE[domain % PALETTE.length];
}

function hash(n: number): number {
  let h = 2166136261 ^ n;
  h = Math.imul(h ^ (h >>> 16), 2246822507);
  h = Math.imul(h ^ (h >>> 13), 3266489909);
  return ((h ^ (h >>> 16)) >>> 0) / 4294967295;
}

/** Layout force-directed mínimo e determinístico (sem lib) — mesmo método do
 *  [[BrainGraph]] do Cérebro, adaptado pra ids numéricos + tamanho por nº de
 *  símbolos do arquivo (`n`) em vez de links_in/out. */
function forceLayout(g: CodespaceViz): { pos: Map<number, XY>; bounds: { w: number; h: number } } {
  const n = g.nodes.length;
  const pos = new Map<number, XY>();
  if (!n) return { pos, bounds: { w: 400, h: 300 } };
  const R = 90 + Math.sqrt(n) * 65;
  const p: Record<number, XY> = {};
  for (const node of g.nodes) {
    const a = hash(node.id) * Math.PI * 2;
    const r = 40 + hash(node.id + 7919) * R;
    p[node.id] = { x: Math.cos(a) * r, y: Math.sin(a) * r };
  }
  const L = 130;
  const REP = 4800;
  const ids = g.nodes.map((x) => x.id);
  for (let it = 0; it < 220; it++) {
    const t = 1 - it / 220;
    const disp: Record<number, XY> = {};
    for (const id of ids) disp[id] = { x: 0, y: 0 };
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        const a = p[ids[i]], b = p[ids[j]];
        let dx = a.x - b.x, dy = a.y - b.y;
        let d2 = dx * dx + dy * dy;
        if (d2 < 1) { dx = hash(ids[i] + it) - 0.5; dy = hash(ids[j] + it) - 0.5; d2 = dx * dx + dy * dy + 0.01; }
        const f = REP / d2;
        const d = Math.sqrt(d2);
        disp[ids[i]].x += (dx / d) * f; disp[ids[i]].y += (dy / d) * f;
        disp[ids[j]].x -= (dx / d) * f; disp[ids[j]].y -= (dy / d) * f;
      }
    }
    for (const e of g.links) {
      const a = p[e.source], b = p[e.target];
      if (!a || !b) continue;
      const dx = b.x - a.x, dy = b.y - a.y;
      const d = Math.sqrt(dx * dx + dy * dy) || 0.01;
      const f = (d - L) * 0.05;
      disp[e.source].x += (dx / d) * f; disp[e.source].y += (dy / d) * f;
      disp[e.target].x -= (dx / d) * f; disp[e.target].y -= (dy / d) * f;
    }
    for (const id of ids) {
      disp[id].x -= p[id].x * 0.03; disp[id].y -= p[id].y * 0.03;
      const d = Math.sqrt(disp[id].x ** 2 + disp[id].y ** 2) || 0.01;
      const step = Math.min(d, 20 * t + 2);
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

export default function CodespaceGraphView({
  projectId, onOpenFile,
}: {
  projectId: string;
  /** duplo-clique num nó: abre o arquivo no explorador */
  onOpenFile: (path: string) => void;
}) {
  const [graph, setGraph] = useState<CodespaceViz | null>(null);
  const [q, setQ] = useState("");
  const [view, setView] = useState<View>({ x: 0, y: 0, k: 1 });
  const [offsets, setOffsets] = useState<Record<number, XY>>({});
  const [selected, setSelected] = useState<CodespaceVizNode | null>(null);
  const viewportRef = useRef<HTMLDivElement>(null);
  const gesture = useRef<{ type: "pan" | "node"; id?: number; sx: number; sy: number; ox: number; oy: number } | null>(null);
  const didFit = useRef(false);
  const moved = useRef(false);

  const load = useCallback(
    () => api.get<CodespaceViz>(`/codespace/projects/${projectId}/graph/visualize?level=file&top=250`)
      .then(setGraph).catch(() => setGraph({ level: "file", nodes: [], links: [], domains: [], warnings: [] })),
    [projectId],
  );
  useEffect(() => { didFit.current = false; setOffsets({}); load(); }, [load]);

  const { pos, bounds } = useMemo(() => forceLayout(graph ?? { level: "file", nodes: [], links: [], domains: [], warnings: [] }), [graph]);
  const at = useCallback((id: number): XY => {
    const p = pos.get(id) ?? { x: 0, y: 0 };
    const o = offsets[id];
    return o ? { x: p.x + o.x, y: p.y + o.y } : p;
  }, [pos, offsets]);

  const fit = useCallback(() => {
    const vp = viewportRef.current;
    if (!vp || !pos.size) return;
    const k = clamp(Math.min(vp.clientWidth / bounds.w, vp.clientHeight / bounds.h, 1.2), 0.2, 1.2);
    setView({ k, x: (vp.clientWidth - bounds.w * k) / 2, y: (vp.clientHeight - bounds.h * k) / 2 });
  }, [pos, bounds]);
  useEffect(() => { if (!didFit.current && pos.size) { didFit.current = true; fit(); } }, [pos, fit]);

  function onPointerDownBg(e: React.PointerEvent) {
    if (e.button !== 0) return;
    gesture.current = { type: "pan", sx: e.clientX, sy: e.clientY, ox: view.x, oy: view.y };
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    moved.current = false;
  }
  function onPointerDownNode(e: React.PointerEvent, id: number) {
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
    else if (g.id != null) setOffsets((m) => ({ ...m, [g.id!]: { x: g.ox + dx / view.k, y: g.oy + dy / view.k } }));
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
      const k2 = clamp(v.k * (e.deltaY < 0 ? 1.12 : 1 / 1.12), 0.2, 3);
      const wx = (mx - v.x) / v.k, wy = (my - v.y) / v.k;
      return { k: k2, x: mx - wx * k2, y: my - wy * k2 };
    });
  }
  const zoom = (dir: number) => {
    const vp = viewportRef.current;
    const cx = (vp?.clientWidth ?? 0) / 2, cy = (vp?.clientHeight ?? 0) / 2;
    setView((v) => {
      const k2 = clamp(v.k * (dir > 0 ? 1.2 : 1 / 1.2), 0.2, 3);
      const wx = (cx - v.x) / v.k, wy = (cy - v.y) / v.k;
      return { k: k2, x: cx - wx * k2, y: cy - wy * k2 };
    });
  };

  const neighbors = useMemo(() => {
    if (!selected || !graph) return null;
    const s = new Set<number>([selected.id]);
    for (const e of graph.links) {
      if (e.source === selected.id) s.add(e.target);
      if (e.target === selected.id) s.add(e.source);
    }
    return s;
  }, [selected, graph]);

  const match = useMemo(() => {
    const f = q.trim().toLowerCase();
    if (!f || !graph) return null;
    return new Set(graph.nodes.filter((nd) => nd.label.toLowerCase().includes(f)).map((nd) => nd.id));
  }, [graph, q]);

  function pickNode(nd: CodespaceVizNode) {
    if (moved.current) return;
    setSelected((cur) => (cur?.id === nd.id ? null : nd));
  }

  const radius = (nd: CodespaceVizNode) => 4 + Math.min(16, Math.sqrt(nd.n || 1) * 2.6);

  return (
    <div className="relative h-[58vh] min-h-[360px] overflow-hidden rounded-xl border border-border bg-[radial-gradient(circle,rgb(var(--c-border))_1px,transparent_1px)] [background-size:22px_22px]">
      <div className="absolute left-3 top-3 z-10 flex items-center gap-2 rounded-lg border border-border bg-surface/90 px-2.5 py-1.5 backdrop-blur">
        <Search size={13} className="text-muted" />
        <input
          value={q} onChange={(e) => setQ(e.target.value)} placeholder="Filtrar arquivos…"
          className="w-40 bg-transparent text-xs text-ink outline-none placeholder:text-muted"
        />
      </div>

      {graph === null ? (
        <p className="grid h-full place-items-center text-sm text-muted">Carregando…</p>
      ) : graph.nodes.length === 0 ? (
        <p className="grid h-full place-items-center px-8 text-center text-sm text-muted">Sem dados suficientes ainda.</p>
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
            <svg width={bounds.w} height={bounds.h} className="absolute left-0 top-0 overflow-visible" style={{ pointerEvents: "none" }}>
              {graph.links.map((e, i) => {
                const a = at(e.source), b = at(e.target);
                const dim = (match && (!match.has(e.source) || !match.has(e.target)))
                  || (neighbors && (!neighbors.has(e.source) || !neighbors.has(e.target)));
                return (
                  <line key={i} x1={a.x} y1={a.y} x2={b.x} y2={b.y}
                    stroke="rgb(var(--c-border))" strokeWidth={1.2} opacity={dim ? 0.08 : 0.55} />
                );
              })}
            </svg>
            {graph.nodes.map((nd) => {
              const p = at(nd.id);
              const r = radius(nd);
              const dim = (!!match && !match.has(nd.id)) || (!!neighbors && !neighbors.has(nd.id));
              const isSel = selected?.id === nd.id;
              return (
                <div
                  key={nd.id}
                  onPointerDown={(e) => onPointerDownNode(e, nd.id)}
                  onPointerUp={() => pickNode(nd)}
                  onDoubleClick={() => onOpenFile(nd.label)}
                  title={nd.label}
                  className={`absolute flex cursor-pointer flex-col items-center transition-opacity ${dim ? "opacity-15" : "opacity-100"}`}
                  style={{ left: p.x - 70, top: p.y - r, width: 140 }}
                >
                  <span
                    className="rounded-full transition-transform"
                    style={{
                      width: r * 2, height: r * 2, background: domainColor(nd.domain),
                      outline: isSel ? "2px solid rgb(var(--c-ink))" : "none", outlineOffset: 2,
                    }}
                  />
                  <span className="mt-1 max-w-[140px] truncate text-center text-[10px] leading-tight text-ink-soft">
                    {nd.label.split("/").pop()}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {selected && (
        <div className="absolute bottom-3 left-3 z-10 max-w-[min(420px,70%)] rounded-lg border border-border bg-surface/95 p-2.5 backdrop-blur">
          <div className="flex items-start gap-2">
            <FileCode2 size={14} className="mt-0.5 shrink-0 text-muted" />
            <div className="min-w-0 flex-1">
              <p className="truncate font-mono text-[11px] text-ink">{selected.label}</p>
              <p className="text-[10px] text-muted">{selected.n} símbolo(s)</p>
            </div>
            <button onClick={() => onOpenFile(selected.label)}
              className="shrink-0 rounded-full bg-accent px-2.5 py-1 text-[11px] font-medium text-white hover:bg-accent-hover">
              Abrir
            </button>
          </div>
        </div>
      )}

      <div className="absolute bottom-3 right-3 flex flex-col gap-1 rounded-xl border border-border bg-surface/90 p-1 shadow-menu backdrop-blur">
        <button onClick={() => zoom(1)} title="Aproximar" className="rounded-lg p-1.5 text-muted hover:bg-hover hover:text-ink"><Plus size={15} /></button>
        <button onClick={() => zoom(-1)} title="Afastar" className="rounded-lg p-1.5 text-muted hover:bg-hover hover:text-ink"><Minus size={15} /></button>
        <button onClick={() => { setOffsets({}); setSelected(null); fit(); }} title="Ajustar à tela" className="rounded-lg p-1.5 text-muted hover:bg-hover hover:text-ink"><Maximize2 size={15} /></button>
        {/* pós-reindexação os ids dos nós podem mudar — limpa offsets/seleção junto,
            senão o arrasto antigo (e o destaque) cai em cima do nó errado */}
        <button onClick={() => { didFit.current = false; setOffsets({}); setSelected(null); load(); }} title="Recarregar" className="rounded-lg p-1.5 text-muted hover:bg-hover hover:text-ink"><RotateCcw size={15} /></button>
      </div>
    </div>
  );
}
