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

/** Lê uma CSS var (ex.: "--c-border" = "34 40 49") como "r,g,b" p/ o canvas, que
 *  não resolve var(); com fallback caso não exista. */
function cssRGB(el: HTMLElement | null, name: string, fallback: string): string {
  if (!el) return fallback;
  const v = getComputedStyle(el).getPropertyValue(name).trim();
  return v ? v.replace(/\s+/g, ",") : fallback;
}

/** Layout force-directed mínimo e determinístico (sem lib) — mesmo método do
 *  [[BrainGraph]] do Cérebro. Barnes-Hut (quadtree) na repulsão → O(n log n) em vez
 *  de O(n²), para escalar a projetos ainda maiores sem travar o cálculo. */
function forceLayout(g: CodespaceViz): { pos: Map<number, XY>; bounds: { w: number; h: number } } {
  const n = g.nodes.length;
  const pos = new Map<number, XY>();
  if (!n) return { pos, bounds: { w: 400, h: 300 } };
  const R = 90 + Math.sqrt(n) * 65;
  const px = new Float64Array(n);
  const py = new Float64Array(n);
  const idx = new Map<number, number>();
  g.nodes.forEach((node, i) => {
    idx.set(node.id, i);
    const a = hash(node.id) * Math.PI * 2;
    const r = 40 + hash(node.id + 7919) * R;
    px[i] = Math.cos(a) * r;
    py[i] = Math.sin(a) * r;
  });
  // arestas por índice (uma vez), ignorando as que referenciam nós ausentes
  const es: number[] = [];
  const et: number[] = [];
  for (const e of g.links) {
    const s = idx.get(e.source), t = idx.get(e.target);
    if (s !== undefined && t !== undefined) { es.push(s); et.push(t); }
  }
  const L = 210;      // comprimento de repouso das arestas (mais = nós mais espaçados)
  const REP = 16000;  // repulsão forte, estilo Obsidian (afasta bem os nós)
  const GRAV = 0.018; // gravidade ao centro mais fraca → menos aglomeração
  const THETA2 = 0.81; // (0.9)² — critério de Barnes-Hut (maior = mais aproximado)
  const iters = n > 400 ? 160 : 220; // menos passos p/ grafos grandes (já convergem)
  // raio de cada nó (mesma fórmula do render) p/ a resolução de colisão anti-sobreposição
  const rad = g.nodes.map((nd) => 4 + Math.min(16, Math.sqrt(nd.n || 1) * 2.6));

  // Quadtree Barnes-Hut: acumula massa/centro por célula e aplica repulsão agregada.
  type QNode = { x0: number; y0: number; x1: number; y1: number; cx: number; cy: number; mass: number; pt: number; children: QNode[] | null };
  const buildTree = (): QNode | null => {
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (let i = 0; i < n; i++) {
      if (px[i] < minX) minX = px[i]; if (px[i] > maxX) maxX = px[i];
      if (py[i] < minY) minY = py[i]; if (py[i] > maxY) maxY = py[i];
    }
    const size = Math.max(maxX - minX, maxY - minY) + 1;
    const root: QNode = { x0: minX, y0: minY, x1: minX + size, y1: minY + size, cx: 0, cy: 0, mass: 0, pt: -1, children: null };
    const insert = (q: QNode, i: number) => {
      // desce até uma folha vazia, subdividindo quando há colisão
      while (true) {
        if (q.children) {
          q.cx += px[i]; q.cy += py[i]; q.mass++;
          const mx = (q.x0 + q.x1) / 2, my = (q.y0 + q.y1) / 2;
          q = q.children[(px[i] >= mx ? 1 : 0) + (py[i] >= my ? 2 : 0)];
          continue;
        }
        if (q.mass === 0) { q.pt = i; q.cx = px[i]; q.cy = py[i]; q.mass = 1; return; }
        // folha ocupada: subdivide e reinsere o ponto existente
        const mx = (q.x0 + q.x1) / 2, my = (q.y0 + q.y1) / 2;
        q.children = [
          { x0: q.x0, y0: q.y0, x1: mx, y1: my, cx: 0, cy: 0, mass: 0, pt: -1, children: null },
          { x0: mx, y0: q.y0, x1: q.x1, y1: my, cx: 0, cy: 0, mass: 0, pt: -1, children: null },
          { x0: q.x0, y0: my, x1: mx, y1: q.y1, cx: 0, cy: 0, mass: 0, pt: -1, children: null },
          { x0: mx, y0: my, x1: q.x1, y1: q.y1, cx: 0, cy: 0, mass: 0, pt: -1, children: null },
        ];
        const old = q.pt;
        q.pt = -1; q.cx = 0; q.cy = 0; q.mass = 0;
        insert(q, old);
        // continua o laço p/ inserir `i` na subárvore correta
      }
    };
    for (let i = 0; i < n; i++) insert(root, i);
    return root;
  };
  const applyRepulsion = (root: QNode, i: number, dispX: Float64Array, dispY: Float64Array) => {
    const stack: QNode[] = [root];
    while (stack.length) {
      const q = stack.pop()!;
      if (q.mass === 0) continue;
      const ccx = q.mass > 0 ? q.cx / q.mass : q.cx;
      const ccy = q.mass > 0 ? q.cy / q.mass : q.cy;
      let dx = px[i] - ccx, dy = py[i] - ccy;
      let d2 = dx * dx + dy * dy;
      const w = q.x1 - q.x0;
      const isLeaf = !q.children;
      if (isLeaf && q.pt === i) continue;
      if (isLeaf || (w * w) < THETA2 * d2) {
        if (d2 < 1) { dx = hash(i + q.pt + 1) - 0.5; dy = hash(i * 3 + 7) - 0.5; d2 = dx * dx + dy * dy + 0.01; }
        const f = (REP * q.mass) / d2;
        const d = Math.sqrt(d2);
        dispX[i] += (dx / d) * f; dispY[i] += (dy / d) * f;
      } else if (q.children) {
        for (const c of q.children) stack.push(c);
      }
    }
  };

  const dispX = new Float64Array(n);
  const dispY = new Float64Array(n);
  for (let it = 0; it < iters; it++) {
    const t = 1 - it / iters;
    dispX.fill(0); dispY.fill(0);
    const root = buildTree();
    if (root) for (let i = 0; i < n; i++) applyRepulsion(root, i, dispX, dispY);
    // atração pelas arestas
    for (let k = 0; k < es.length; k++) {
      const a = es[k], b = et[k];
      const dx = px[b] - px[a], dy = py[b] - py[a];
      const d = Math.sqrt(dx * dx + dy * dy) || 0.01;
      const f = (d - L) * 0.05;
      dispX[a] += (dx / d) * f; dispY[a] += (dy / d) * f;
      dispX[b] -= (dx / d) * f; dispY[b] -= (dy / d) * f;
    }
    // gravidade ao centro + passo com resfriamento
    for (let i = 0; i < n; i++) {
      dispX[i] -= px[i] * GRAV; dispY[i] -= py[i] * GRAV;
      const d = Math.sqrt(dispX[i] * dispX[i] + dispY[i] * dispY[i]) || 0.01;
      const step = Math.min(d, 20 * t + 2);
      px[i] += (dispX[i] / d) * step;
      py[i] += (dispY[i] / d) * step;
    }
  }
  // resolução de colisão: separa pares que ficaram sobrepostos (r_i + r_j + folga),
  // garantindo que nenhum nó fique "por cima" do outro — o espaçamento do Obsidian.
  // O(n²) por passo, mas n é capado em 250 (top=250) → barato.
  const GAP = 20;
  for (let pass = 0; pass < 14; pass++) {
    let moved2 = false;
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        let dx = px[j] - px[i], dy = py[j] - py[i];
        let d2 = dx * dx + dy * dy;
        const min = rad[i] + rad[j] + GAP;
        if (d2 < min * min) {
          let d = Math.sqrt(d2);
          if (d < 0.01) { dx = hash(i * 31 + j) - 0.5; dy = hash(j * 31 + i) - 0.5; d = Math.sqrt(dx * dx + dy * dy) || 0.01; }
          const push = (min - d) / 2;
          const ux = dx / d, uy = dy / d;
          px[i] -= ux * push; py[i] -= uy * push;
          px[j] += ux * push; py[j] += uy * push;
          moved2 = true;
        }
      }
    }
    if (!moved2) break; // já não há sobreposição
  }
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (let i = 0; i < n; i++) {
    if (px[i] < minX) minX = px[i]; if (px[i] > maxX) maxX = px[i];
    if (py[i] < minY) minY = py[i]; if (py[i] > maxY) maxY = py[i];
  }
  g.nodes.forEach((node, i) => pos.set(node.id, { x: px[i] - minX + PAD, y: py[i] - minY + PAD }));
  return { pos, bounds: { w: maxX - minX + PAD * 2, h: maxY - minY + PAD * 2 } };
}

const radius = (nd: CodespaceVizNode) => 4 + Math.min(16, Math.sqrt(nd.n || 1) * 2.6);

export default function CodespaceGraphView({
  projectId, onOpenFile,
}: {
  projectId: string;
  /** duplo-clique num nó: abre o arquivo no explorador */
  onOpenFile: (path: string) => void;
}) {
  const [graph, setGraph] = useState<CodespaceViz | null>(null);
  const [q, setQ] = useState("");
  const [selected, setSelected] = useState<CodespaceVizNode | null>(null);

  const canvasRef = useRef<HTMLCanvasElement>(null);
  const viewportRef = useRef<HTMLDivElement>(null);
  // estado de interação em REFS (não dispara re-render do React por frame — é o que
  // deixava o grafo travado: pan/zoom/arraste re-renderizavam centenas de nós DOM).
  const viewRef = useRef<View>({ x: 0, y: 0, k: 1 });
  const offsetsRef = useRef<Map<number, XY>>(new Map());
  const gesture = useRef<{ type: "pan" | "node"; id?: number; sx: number; sy: number; ox: number; oy: number } | null>(null);
  const moved = useRef(false);
  const didFit = useRef(false);
  const rafRef = useRef<number | null>(null);
  const lastDouble = useRef(0);

  const load = useCallback(
    () => api.get<CodespaceViz>(`/codespace/projects/${projectId}/graph/visualize?level=file&top=250`)
      .then(setGraph).catch(() => setGraph({ level: "file", nodes: [], links: [], domains: [], warnings: [] })),
    [projectId],
  );
  useEffect(() => { didFit.current = false; offsetsRef.current.clear(); load(); }, [load]);

  const { pos, bounds } = useMemo(
    () => forceLayout(graph ?? { level: "file", nodes: [], links: [], domains: [], warnings: [] }),
    [graph],
  );

  const at = useCallback((id: number): XY => {
    const p = pos.get(id) ?? { x: 0, y: 0 };
    const o = offsetsRef.current.get(id);
    return o ? { x: p.x + o.x, y: p.y + o.y } : p;
  }, [pos]);

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

  // --- desenho no canvas (imperativo, agendado por rAF) ------------------------ #
  const draw = useCallback(() => {
    rafRef.current = null;
    const canvas = canvasRef.current, vp = viewportRef.current;
    if (!canvas || !vp || !graph) return;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const cw = vp.clientWidth, ch = vp.clientHeight;
    if (canvas.width !== Math.round(cw * dpr) || canvas.height !== Math.round(ch * dpr)) {
      canvas.width = Math.round(cw * dpr); canvas.height = Math.round(ch * dpr);
      canvas.style.width = `${cw}px`; canvas.style.height = `${ch}px`;
    }
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const border = cssRGB(vp, "--c-border", "60,66,74");
    const ink = cssRGB(vp, "--c-ink", "20,24,30");
    const inkSoft = cssRGB(vp, "--c-ink-soft", "90,100,115");
    const v = viewRef.current;

    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, cw, ch);
    ctx.translate(v.x, v.y);
    ctx.scale(v.k, v.k);

    // links — em dois lotes (normal/apagado) p/ trocar de estilo o mínimo possível
    for (const pass of [0, 1] as const) {
      ctx.beginPath();
      ctx.strokeStyle = `rgba(${border},${pass === 0 ? 0.5 : 0.07})`;
      ctx.lineWidth = 1.2 / v.k;
      for (const e of graph.links) {
        const dim = (match && (!match.has(e.source) || !match.has(e.target)))
          || (neighbors && (!neighbors.has(e.source) || !neighbors.has(e.target)));
        if ((dim ? 1 : 0) !== pass) continue;
        const a = at(e.source), b = at(e.target);
        ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y);
      }
      ctx.stroke();
    }

    // nós (círculos) + rótulos (só com zoom suficiente — LOD)
    const showLabels = v.k >= 0.55 || graph.nodes.length <= 120;
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    ctx.font = `${10 / v.k}px ui-sans-serif, system-ui, sans-serif`;
    for (const nd of graph.nodes) {
      const p = at(nd.id);
      const r = radius(nd);
      const dim = (!!match && !match.has(nd.id)) || (!!neighbors && !neighbors.has(nd.id));
      ctx.globalAlpha = dim ? 0.15 : 1;
      ctx.beginPath();
      ctx.fillStyle = domainColor(nd.domain);
      ctx.arc(p.x, p.y, r, 0, Math.PI * 2);
      ctx.fill();
      if (selected?.id === nd.id) {
        ctx.lineWidth = 2 / v.k;
        ctx.strokeStyle = `rgb(${ink})`;
        ctx.beginPath();
        ctx.arc(p.x, p.y, r + 2 / v.k + 1, 0, Math.PI * 2);
        ctx.stroke();
      }
      if (showLabels && !dim) {
        const label = nd.label.split("/").pop() ?? nd.label;
        ctx.fillStyle = `rgb(${inkSoft})`;
        ctx.fillText(label.length > 22 ? label.slice(0, 21) + "…" : label, p.x, p.y + r + 2);
      }
    }
    ctx.globalAlpha = 1;
  }, [graph, match, neighbors, selected, at]);

  const scheduleDraw = useCallback(() => {
    if (rafRef.current == null) rafRef.current = requestAnimationFrame(draw);
  }, [draw]);

  // redesenha quando muda dado/seleção/busca/layout ou o tamanho do viewport
  useEffect(() => { scheduleDraw(); }, [scheduleDraw]);
  useEffect(() => {
    const vp = viewportRef.current;
    if (!vp) return;
    const ro = new ResizeObserver(() => scheduleDraw());
    ro.observe(vp);
    return () => ro.disconnect();
  }, [scheduleDraw]);

  const fit = useCallback(() => {
    const vp = viewportRef.current;
    if (!vp || !pos.size) return;
    const k = clamp(Math.min(vp.clientWidth / bounds.w, vp.clientHeight / bounds.h, 1.2), 0.2, 1.2);
    viewRef.current = { k, x: (vp.clientWidth - bounds.w * k) / 2, y: (vp.clientHeight - bounds.h * k) / 2 };
    scheduleDraw();
  }, [pos, bounds, scheduleDraw]);
  useEffect(() => { if (!didFit.current && pos.size) { didFit.current = true; fit(); } }, [pos, fit]);

  // --- picking / interação ---------------------------------------------------- #
  const nodeAt = useCallback((mx: number, my: number): CodespaceVizNode | null => {
    if (!graph) return null;
    const v = viewRef.current;
    const wx = (mx - v.x) / v.k, wy = (my - v.y) / v.k;
    // procura o nó cujo círculo contém o ponto (raio + folga p/ toque)
    let best: CodespaceVizNode | null = null, bestD = Infinity;
    for (const nd of graph.nodes) {
      const p = at(nd.id);
      const dx = p.x - wx, dy = p.y - wy;
      const d2 = dx * dx + dy * dy;
      const rr = radius(nd) + 4;
      if (d2 <= rr * rr && d2 < bestD) { bestD = d2; best = nd; }
    }
    return best;
  }, [graph, at]);

  function localXY(e: { clientX: number; clientY: number }): XY {
    const rect = viewportRef.current!.getBoundingClientRect();
    return { x: e.clientX - rect.left, y: e.clientY - rect.top };
  }
  function onPointerDown(e: React.PointerEvent) {
    if (e.button !== 0) return;
    const { x: mx, y: my } = localXY(e);
    const nd = nodeAt(mx, my);
    const v = viewRef.current;
    if (nd) {
      const o = offsetsRef.current.get(nd.id) ?? { x: 0, y: 0 };
      gesture.current = { type: "node", id: nd.id, sx: e.clientX, sy: e.clientY, ox: o.x, oy: o.y };
    } else {
      gesture.current = { type: "pan", sx: e.clientX, sy: e.clientY, ox: v.x, oy: v.y };
    }
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    moved.current = false;
  }
  function onPointerMove(e: React.PointerEvent) {
    const g = gesture.current;
    if (!g) return;
    const dx = e.clientX - g.sx, dy = e.clientY - g.sy;
    if (Math.abs(dx) > 3 || Math.abs(dy) > 3) moved.current = true;
    if (g.type === "pan") {
      viewRef.current = { ...viewRef.current, x: g.ox + dx, y: g.oy + dy };
    } else if (g.id != null) {
      offsetsRef.current.set(g.id, { x: g.ox + dx / viewRef.current.k, y: g.oy + dy / viewRef.current.k });
    }
    scheduleDraw();
  }
  function onPointerUp(e: React.PointerEvent) {
    try { (e.currentTarget as HTMLElement).releasePointerCapture(e.pointerId); } catch { /* noop */ }
    const g = gesture.current;
    gesture.current = null;
    if (g && !moved.current) {
      const { x: mx, y: my } = localXY(e);
      const nd = nodeAt(mx, my);
      if (nd) {
        // duplo-clique (dois ups no mesmo nó em <350ms) abre o arquivo
        const now = Date.now();
        if (selected?.id === nd.id && now - lastDouble.current < 350) { onOpenFile(nd.label); lastDouble.current = 0; return; }
        lastDouble.current = now;
        setSelected((cur) => (cur?.id === nd.id ? cur : nd));
      } else {
        setSelected(null);
      }
    }
  }
  function onWheel(e: React.WheelEvent) {
    const { x: mx, y: my } = localXY(e);
    const v = viewRef.current;
    const k2 = clamp(v.k * (e.deltaY < 0 ? 1.12 : 1 / 1.12), 0.2, 3);
    const wx = (mx - v.x) / v.k, wy = (my - v.y) / v.k;
    viewRef.current = { k: k2, x: mx - wx * k2, y: my - wy * k2 };
    scheduleDraw();
  }
  const zoom = (dir: number) => {
    const vp = viewportRef.current;
    const cx = (vp?.clientWidth ?? 0) / 2, cy = (vp?.clientHeight ?? 0) / 2;
    const v = viewRef.current;
    const k2 = clamp(v.k * (dir > 0 ? 1.2 : 1 / 1.2), 0.2, 3);
    const wx = (cx - v.x) / v.k, wy = (cy - v.y) / v.k;
    viewRef.current = { k: k2, x: cx - wx * k2, y: cy - wy * k2 };
    scheduleDraw();
  };

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
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onWheel={onWheel}
          className="h-full w-full cursor-grab touch-none select-none active:cursor-grabbing"
        >
          <canvas ref={canvasRef} className="absolute left-0 top-0" />
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
        <button onClick={() => { offsetsRef.current.clear(); setSelected(null); fit(); }} title="Ajustar à tela" className="rounded-lg p-1.5 text-muted hover:bg-hover hover:text-ink"><Maximize2 size={15} /></button>
        {/* pós-reindexação os ids dos nós podem mudar — limpa offsets/seleção junto,
            senão o arrasto antigo (e o destaque) cai em cima do nó errado */}
        <button onClick={() => { didFit.current = false; offsetsRef.current.clear(); setSelected(null); load(); }} title="Recarregar" className="rounded-lg p-1.5 text-muted hover:bg-hover hover:text-ink"><RotateCcw size={15} /></button>
      </div>
    </div>
  );
}
