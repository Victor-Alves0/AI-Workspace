"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Check, GitBranch, Maximize2, Pencil, Pin, Plus, RotateCcw, Minus, Search, Trash2, X } from "lucide-react";
import { api } from "@/lib/api";
import type { Compaction } from "@/lib/types";

function fmtWhen(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}

const NODE_W = 172;
const NODE_H = 60;
const X_GAP = 44;
const Y_GAP = 104;
const PAD = 80;

type XY = { x: number; y: number };
type View = { x: number; y: number; k: number };

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));

/** Grafo de contexto em canvas (estilo Obsidian): cada checkpoint é um nó; o pai
 *  (parent_id) forma ramos. Arraste o fundo p/ mover, roda do mouse p/ zoom,
 *  arraste um nó p/ reposicionar. Hover mostra os detalhes; a busca destaca nós;
 *  clicar seleciona e abre as ações (restaurar / renomear / excluir). */
export default function CompactionHistory({
  chatId,
  onClose,
  onPinned,
}: {
  chatId: string;
  onClose: () => void;
  onPinned: () => void;
}) {
  const [items, setItems] = useState<Compaction[] | null>(null);
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [sel, setSel] = useState<string | null>(null);
  const [hover, setHover] = useState<string | null>(null);
  const [confirmRestore, setConfirmRestore] = useState(false);
  const [confirmDel, setConfirmDel] = useState(false);
  const [editing, setEditing] = useState(false);
  const [editName, setEditName] = useState("");

  const [view, setView] = useState<View>({ x: 0, y: 0, k: 1 });
  // deslocamentos manuais dos nós (arrastar) sobre o layout automático
  const [offsets, setOffsets] = useState<Record<string, XY>>({});

  const viewportRef = useRef<HTMLDivElement>(null);
  const gesture = useRef<{ type: "pan" | "node"; id?: string; sx: number; sy: number; ox: number; oy: number } | null>(null);
  const didFit = useRef(false);
  const moved = useRef(false);

  const load = useCallback(
    () => api.get<Compaction[]>(`/chats/${chatId}/compactions`).then(setItems).catch(() => setItems([])),
    [chatId],
  );
  useEffect(() => { load(); }, [load]);

  const byId = useMemo(() => new Map((items ?? []).map((c) => [c.id, c])), [items]);
  const label = (c: Compaction) => c.name || fmtWhen(c.created_at);

  // ---- layout tidy-tree (posições base, deslocadas p/ o quadrante positivo) ----
  const { pos, bounds } = useMemo(() => {
    const list = items ?? [];
    const ids = new Set(list.map((c) => c.id));
    const kids = new Map<string, Compaction[]>();
    const roots: Compaction[] = [];
    for (const c of list) {
      if (c.parent_id && ids.has(c.parent_id)) {
        (kids.get(c.parent_id) ?? kids.set(c.parent_id, []).get(c.parent_id)!).push(c);
      } else roots.push(c);
    }
    const raw = new Map<string, XY>();
    let leaf = 0;
    const place = (c: Compaction, depth: number): number => {
      const ch = kids.get(c.id) ?? [];
      const y = depth * Y_GAP;
      if (!ch.length) {
        const x = leaf * (NODE_W + X_GAP);
        leaf += 1;
        raw.set(c.id, { x, y });
        return x;
      }
      const xs = ch.map((k) => place(k, depth + 1));
      const x = (xs[0] + xs[xs.length - 1]) / 2;
      raw.set(c.id, { x, y });
      return x;
    };
    roots.forEach((r) => place(r, 0));
    // desloca p/ ficar tudo positivo, com padding
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    raw.forEach((p) => {
      minX = Math.min(minX, p.x); minY = Math.min(minY, p.y);
      maxX = Math.max(maxX, p.x); maxY = Math.max(maxY, p.y);
    });
    if (!Number.isFinite(minX)) { minX = minY = 0; maxX = maxY = 0; }
    const pos = new Map<string, XY>();
    raw.forEach((p, id) => pos.set(id, { x: p.x - minX + PAD + NODE_W / 2, y: p.y - minY + PAD + NODE_H / 2 }));
    return { pos, bounds: { w: maxX - minX + PAD * 2 + NODE_W, h: maxY - minY + PAD * 2 + NODE_H } };
  }, [items]);

  const at = useCallback(
    (id: string): XY => {
      const p = pos.get(id) ?? { x: 0, y: 0 };
      const o = offsets[id];
      return o ? { x: p.x + o.x, y: p.y + o.y } : p;
    },
    [pos, offsets],
  );

  const fit = useCallback(() => {
    const vp = viewportRef.current;
    if (!vp || !pos.size) return;
    const k = clamp(Math.min(vp.clientWidth / bounds.w, vp.clientHeight / bounds.h, 1), 0.3, 1);
    setView({ k, x: (vp.clientWidth - bounds.w * k) / 2, y: (vp.clientHeight - bounds.h * k) / 2 });
  }, [pos, bounds]);

  useEffect(() => {
    if (!didFit.current && pos.size) { didFit.current = true; fit(); }
  }, [pos, fit]);

  // ---- gestos: pan (fundo), arrastar nó, zoom (roda) ----
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
    if (g.type === "pan") {
      setView((v) => ({ ...v, x: g.ox + dx, y: g.oy + dy }));
    } else if (g.id) {
      setOffsets((m) => ({ ...m, [g.id!]: { x: g.ox + dx / view.k, y: g.oy + dy / view.k } }));
    }
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
      const k2 = clamp(v.k * (e.deltaY < 0 ? 1.12 : 1 / 1.12), 0.3, 2.5);
      const wx = (mx - v.x) / v.k, wy = (my - v.y) / v.k;
      return { k: k2, x: mx - wx * k2, y: my - wy * k2 };
    });
  }
  const zoom = (dir: number) => {
    const vp = viewportRef.current;
    const cx = (vp?.clientWidth ?? 0) / 2, cy = (vp?.clientHeight ?? 0) / 2;
    setView((v) => {
      const k2 = clamp(v.k * (dir > 0 ? 1.2 : 1 / 1.2), 0.3, 2.5);
      const wx = (cx - v.x) / v.k, wy = (cy - v.y) / v.k;
      return { k: k2, x: cx - wx * k2, y: cy - wy * k2 };
    });
  };

  // ---- busca: conjunto de ids que casam ----
  const match = useMemo(() => {
    const f = q.trim().toLowerCase();
    if (!f) return null;
    const set = new Set<string>();
    for (const c of items ?? []) {
      if ([c.name, c.last_message, c.summary, fmtWhen(c.created_at)].some((s) => (s || "").toLowerCase().includes(f)))
        set.add(c.id);
    }
    return set;
  }, [items, q]);

  // ---- ações ----
  const selected = sel ? byId.get(sel) : undefined;
  function pickNode(id: string) {
    if (moved.current) return; // foi arraste, não clique
    setSel(id); setConfirmRestore(false); setConfirmDel(false); setEditing(false);
  }
  async function restore() {
    if (!selected) return;
    if (!confirmRestore) { setConfirmRestore(true); setTimeout(() => setConfirmRestore(false), 3500); return; }
    setConfirmRestore(false); setBusy(selected.id);
    try {
      await api.post(`/chats/${chatId}/compactions/${selected.id}/pin`);
      onPinned();
      await load();
    } finally { setBusy(null); }
  }
  async function saveName() {
    if (!selected) return;
    setBusy(selected.id);
    try {
      await api.patch(`/chats/${chatId}/compactions/${selected.id}`, { name: editName.trim() || null });
      setEditing(false);
      await load();
    } finally { setBusy(null); }
  }
  async function remove() {
    if (!selected) return;
    if (!confirmDel) { setConfirmDel(true); setTimeout(() => setConfirmDel(false), 3500); return; }
    const wasPinned = selected.pinned;
    setConfirmDel(false); setBusy(selected.id);
    try {
      await api.del(`/chats/${chatId}/compactions/${selected.id}`);
      setSel(null);
      // apagar o checkpoint ATIVO descompacta a conversa → recarrega as mensagens
      if (wasPinned) onPinned();
      await load();
    } finally { setBusy(null); }
  }

  // tooltip (hover) em coordenadas de tela
  const hovered = hover ? byId.get(hover) : undefined;
  const hoverPt = hover ? at(hover) : null;

  return (
    <div onClick={onClose} className="fixed inset-0 z-[60] flex items-center justify-center bg-black/60 px-4 backdrop-blur-sm">
      <div onClick={(e) => e.stopPropagation()} className="animate-pop flex h-[84vh] w-full max-w-3xl flex-col overflow-hidden rounded-2xl border border-border bg-bg shadow-2xl">
        {/* cabeçalho + busca */}
        <div className="flex items-center gap-3 border-b border-border px-5 py-3">
          <h2 className="flex shrink-0 items-center gap-2 text-base font-semibold text-ink">
            <GitBranch size={17} className="text-muted" /> Grafo de contexto
          </h2>
          <div className="ml-auto flex items-center gap-2 rounded-lg bg-surface px-3 py-1.5">
            <Search size={14} className="text-muted" />
            <input
              value={q} onChange={(e) => setQ(e.target.value)} placeholder="Filtrar por nome, data ou mensagem…"
              className="w-56 bg-transparent text-sm text-ink outline-none placeholder:text-muted"
            />
          </div>
          <button onClick={onClose} className="rounded-lg p-1 text-muted hover:bg-hover hover:text-ink"><X size={18} /></button>
        </div>

        {/* canvas */}
        <div className="relative min-h-0 flex-1 overflow-hidden bg-[radial-gradient(circle,rgb(var(--c-border))_1px,transparent_1px)] [background-size:22px_22px]">
          {items === null ? (
            <p className="grid h-full place-items-center text-sm text-muted">Carregando…</p>
          ) : items.length === 0 ? (
            <p className="grid h-full place-items-center px-8 text-center text-sm text-muted">Nenhum checkpoint ainda. Use &quot;Compactar agora&quot; para criar o primeiro ponto.</p>
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
                {/* arestas */}
                <svg width={bounds.w} height={bounds.h} className="absolute left-0 top-0 overflow-visible" style={{ pointerEvents: "none" }}>
                  {(items ?? []).map((c) => {
                    if (!c.parent_id || !byId.has(c.parent_id)) return null;
                    const p = at(c.parent_id), n = at(c.id);
                    const dim = match && (!match.has(c.id) || !match.has(c.parent_id));
                    const my = (p.y + n.y) / 2;
                    return (
                      <path key={c.id} d={`M ${p.x} ${p.y} C ${p.x} ${my} ${n.x} ${my} ${n.x} ${n.y}`}
                        fill="none" stroke="rgb(var(--c-border))" strokeWidth={2} opacity={dim ? 0.2 : 0.9} />
                    );
                  })}
                </svg>
                {/* nós */}
                {(items ?? []).map((c) => {
                  const p = at(c.id);
                  const dim = !!match && !match.has(c.id);
                  const isSel = sel === c.id;
                  const isChild = !!c.parent_id && byId.has(c.parent_id);
                  return (
                    <div
                      key={c.id}
                      onPointerDown={(e) => onPointerDownNode(e, c.id)}
                      onPointerUp={() => pickNode(c.id)}
                      onMouseEnter={() => setHover(c.id)}
                      onMouseLeave={() => setHover((h) => (h === c.id ? null : h))}
                      className={`absolute flex cursor-pointer flex-col justify-center rounded-xl border px-3 py-2 shadow-menu transition-[opacity,border-color] ${
                        c.pinned ? "border-accent/60 bg-accent/10" : isSel ? "border-accent/50 bg-surface2" : "border-border bg-surface"
                      } ${dim ? "opacity-25" : "opacity-100"} ${isSel ? "ring-2 ring-accent/40" : ""}`}
                      style={{ left: p.x - NODE_W / 2, top: p.y - NODE_H / 2, width: NODE_W, height: NODE_H }}
                    >
                      <div className="flex items-center gap-1.5">
                        <span className={`flex h-5 w-5 shrink-0 items-center justify-center rounded-full ${c.pinned ? "bg-accent/20 text-accent-hover" : "bg-surface2 text-muted"}`}>
                          {isChild ? <GitBranch size={11} /> : <Pin size={11} className={c.pinned ? "fill-accent-hover" : ""} />}
                        </span>
                        <span className="truncate text-sm font-medium text-ink">{label(c)}</span>
                        {c.pinned && <span className="ml-auto shrink-0 rounded-full bg-accent/20 px-1.5 py-0.5 text-[9px] font-medium text-accent-hover">Atual</span>}
                      </div>
                      <p className="mt-0.5 truncate text-[11px] text-muted">{c.message_count} msgs · {fmtWhen(c.created_at)}</p>
                    </div>
                  );
                })}
              </div>

              {/* tooltip de hover (coordenadas de tela) */}
              {hovered && hoverPt && (
                <div
                  className="pointer-events-none absolute z-10 w-64 rounded-xl border border-border bg-bg/95 p-3 shadow-2xl backdrop-blur"
                  style={{
                    left: clamp(hoverPt.x * view.k + view.x + NODE_W / 2 * view.k + 10, 8, (viewportRef.current?.clientWidth ?? 400) - 268),
                    top: clamp(hoverPt.y * view.k + view.y - 20, 8, (viewportRef.current?.clientHeight ?? 400) - 140),
                  }}
                >
                  <p className="flex items-center gap-1.5 text-sm font-medium text-ink">
                    {hovered.name || "Checkpoint"}
                    {hovered.pinned && <span className="rounded-full bg-accent/20 px-1.5 py-0.5 text-[9px] text-accent-hover">Atual</span>}
                  </p>
                  <p className="mt-0.5 text-[11px] text-muted">{fmtWhen(hovered.created_at)} · {hovered.message_count} mensagens</p>
                  {hovered.last_message && <p className="mt-1.5 line-clamp-3 text-xs text-ink-soft/80">{hovered.last_message}</p>}
                </div>
              )}
            </div>
          )}

          {/* controles de zoom */}
          {items && items.length > 0 && (
            <div className="absolute bottom-3 right-3 flex flex-col gap-1 rounded-xl border border-border bg-surface/90 p-1 shadow-menu backdrop-blur">
              <button onClick={() => zoom(1)} title="Aproximar" className="rounded-lg p-1.5 text-muted hover:bg-hover hover:text-ink"><Plus size={15} /></button>
              <button onClick={() => zoom(-1)} title="Afastar" className="rounded-lg p-1.5 text-muted hover:bg-hover hover:text-ink"><Minus size={15} /></button>
              <button onClick={() => { setOffsets({}); fit(); }} title="Ajustar à tela" className="rounded-lg p-1.5 text-muted hover:bg-hover hover:text-ink"><Maximize2 size={15} /></button>
            </div>
          )}

          {/* painel de ações do nó selecionado */}
          {selected && (
            <div className="absolute bottom-3 left-3 w-72 rounded-xl border border-border bg-surface/95 p-3 shadow-2xl backdrop-blur">
              <div className="flex items-start gap-2">
                <span className={`mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full ${selected.pinned ? "bg-accent/20 text-accent-hover" : "bg-surface2 text-muted"}`}>
                  {selected.parent_id && byId.has(selected.parent_id) ? <GitBranch size={12} /> : <Pin size={12} className={selected.pinned ? "fill-accent-hover" : ""} />}
                </span>
                <div className="min-w-0 flex-1">
                  {editing ? (
                    <div className="flex items-center gap-1">
                      <input
                        autoFocus value={editName} onChange={(e) => setEditName(e.target.value)}
                        onKeyDown={(e) => { if (e.key === "Enter") saveName(); if (e.key === "Escape") setEditing(false); }}
                        placeholder="Nome do checkpoint"
                        className="w-full rounded-md border border-border bg-bg px-2 py-1 text-sm text-ink outline-none focus:border-accent"
                      />
                      <button onClick={saveName} className="rounded p-1 text-green-400 hover:bg-hover"><Check size={14} /></button>
                    </div>
                  ) : (
                    <p className="truncate text-sm font-medium text-ink">{label(selected)}</p>
                  )}
                  <p className="text-[11px] text-muted">{fmtWhen(selected.created_at)} · {selected.message_count} msgs</p>
                </div>
                <button onClick={() => setSel(null)} className="rounded p-1 text-muted hover:bg-hover hover:text-ink"><X size={14} /></button>
              </div>
              {/* resumo gerado nesta compactação (o "resultado" que não aparece no chat) */}
              {selected.summary && !editing && (
                <div className="mt-2 max-h-40 overflow-y-auto rounded-lg border border-border bg-bg/60 p-2 text-xs leading-relaxed text-ink-soft/80 whitespace-pre-wrap">
                  {selected.summary}
                </div>
              )}
              {!selected.summary && selected.last_message && !editing && <p className="mt-1.5 line-clamp-2 text-xs text-ink-soft/70">{selected.last_message}</p>}
              {!editing && (
                <div className="mt-2.5 flex items-center gap-1.5">
                  {!selected.pinned && (
                    <button
                      onClick={restore} disabled={busy !== null}
                      className={`flex flex-1 items-center justify-center gap-1 rounded-lg border px-2 py-1.5 text-xs transition-colors disabled:opacity-60 ${
                        confirmRestore ? "border-amber-500/50 bg-amber-500/15 text-amber-300" : "border-border text-ink-soft hover:bg-hover"
                      }`}
                    >
                      <RotateCcw size={12} /> {busy === selected.id ? "…" : confirmRestore ? "Confirmar" : "Restaurar"}
                    </button>
                  )}
                  <button onClick={() => { setEditing(true); setEditName(selected.name ?? ""); }} title="Renomear" className="rounded-lg border border-border p-1.5 text-muted hover:bg-hover hover:text-ink"><Pencil size={13} /></button>
                  {selected.pinned ? (
                    // checkpoint ATIVO: excluir = DESCOMPACTAR (desfazer esta compactação)
                    <button
                      onClick={remove} disabled={busy !== null}
                      title="Descompactar: as mensagens voltam ao contexto da IA"
                      className={`flex flex-1 items-center justify-center gap-1 rounded-lg border px-2 py-1.5 text-xs transition-colors disabled:opacity-60 ${
                        confirmDel ? "border-red-500/50 bg-red-500/15 text-red-300" : "border-border text-ink-soft hover:bg-hover hover:text-red-300"
                      }`}
                    >
                      <Trash2 size={12} /> {busy === selected.id ? "…" : confirmDel ? "Confirmar" : "Descompactar"}
                    </button>
                  ) : (
                    <button onClick={remove} disabled={busy !== null} title="Excluir"
                      className={`rounded-lg border p-1.5 transition-colors ${confirmDel ? "border-red-500/50 bg-red-500/15 text-red-300" : "border-border text-muted hover:bg-hover hover:text-red-300"}`}>
                      <Trash2 size={13} />
                    </button>
                  )}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
