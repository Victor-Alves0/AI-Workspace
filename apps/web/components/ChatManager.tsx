"use client";

import { useMemo, useState } from "react";
import {
  CheckSquare, ChevronDown, FolderInput, Loader2, MessagesSquare, Search, Square, Tag, Trash2, X,
} from "lucide-react";
import type { Chat, Folder } from "@/lib/types";
import { useClickOutside } from "./ui";

function fmtDate(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit", year: "2-digit" });
}

/** Menu "Conversas": busca nos chats + multi-seleção p/ excluir, atribuir a um
 *  projeto (pasta) ou tirar da pasta. Pastas SÃO os projetos (memória de projeto). */
export default function ChatManager({
  chats, folders, onSelect, onMove, onDelete, onClose,
}: {
  chats: Chat[];
  folders: Folder[];
  onSelect: (id: string) => void;
  /** move um LOTE de chats para a pasta/projeto (null = tirar da pasta) */
  onMove: (ids: string[], folderId: string | null) => Promise<void> | void;
  /** exclui um LOTE de chats (com confirmação no chamador) */
  onDelete: (ids: string[]) => Promise<void> | void;
  onClose: () => void;
}) {
  const [q, setQ] = useState("");
  const [sel, setSel] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [moveOpen, setMoveOpen] = useState(false);
  const [tagFilter, setTagFilter] = useState<Set<string>>(new Set());
  const moveRef = useClickOutside<HTMLDivElement>(() => setMoveOpen(false));

  const allTags = useMemo(() => {
    const s = new Set<string>();
    for (const c of chats) for (const t of c.tags || []) s.add(t);
    return [...s].sort();
  }, [chats]);
  const toggleTag = (t: string) =>
    setTagFilter((s) => { const n = new Set(s); n.has(t) ? n.delete(t) : n.add(t); return n; });

  const folderName = useMemo(() => {
    const m: Record<string, string> = {};
    for (const f of folders) m[f.id] = f.name;
    return m;
  }, [folders]);

  const filtered = useMemo(() => {
    const f = q.trim().toLowerCase();
    let list = f ? chats.filter((c) => c.title.toLowerCase().includes(f)) : chats;
    if (tagFilter.size > 0) list = list.filter((c) => (c.tags || []).some((t) => tagFilter.has(t)));
    return [...list].sort((a, b) => +new Date(b.updated_at) - +new Date(a.updated_at));
  }, [chats, q, tagFilter]);

  const allSelected = filtered.length > 0 && filtered.every((c) => sel.has(c.id));
  const toggle = (id: string) =>
    setSel((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });
  const toggleAll = () =>
    setSel(allSelected ? new Set() : new Set(filtered.map((c) => c.id)));

  const ids = [...sel];
  async function move(folderId: string | null) {
    if (!ids.length) return;
    setMoveOpen(false); setBusy(true);
    try { await onMove(ids, folderId); setSel(new Set()); } finally { setBusy(false); }
  }
  async function del() {
    if (!ids.length) return;
    setBusy(true);
    try { await onDelete(ids); setSel(new Set()); } finally { setBusy(false); }
  }

  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/60 p-4" onClick={onClose}>
      <div
        onClick={(e) => e.stopPropagation()}
        className="flex max-h-[82vh] w-full max-w-2xl flex-col overflow-hidden rounded-2xl border border-border bg-surface shadow-2xl"
      >
        {/* cabeçalho */}
        <div className="flex items-center gap-2 border-b border-border px-4 py-3">
          <MessagesSquare size={18} className="text-accent-hover" />
          <span className="text-sm font-semibold text-ink">Conversas</span>
          <span className="text-xs text-muted">{chats.length}</span>
          <button onClick={onClose} className="ml-auto rounded-lg p-1 text-muted hover:bg-hover hover:text-ink"><X size={16} /></button>
        </div>

        {/* busca */}
        <div className="flex items-center gap-2 border-b border-border px-4 py-2.5">
          <Search size={15} className="text-muted" />
          <input
            autoFocus
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Buscar conversas…"
            className="w-full bg-transparent text-sm text-ink outline-none placeholder:text-muted"
          />
          {q && <button onClick={() => setQ("")} className="rounded-md p-0.5 text-muted hover:text-ink"><X size={14} /></button>}
        </div>

        {/* filtro por etiqueta */}
        {allTags.length > 0 && (
          <div className="flex flex-wrap items-center gap-1.5 border-b border-border px-4 py-2">
            <Tag size={13} className="text-muted" />
            {allTags.map((t) => {
              const on = tagFilter.has(t);
              return (
                <button
                  key={t}
                  onClick={() => toggleTag(t)}
                  className={`rounded-full px-2.5 py-0.5 text-xs transition-colors ${on ? "bg-accent text-white" : "bg-surface2 text-muted hover:text-ink"}`}
                >
                  {t}
                </button>
              );
            })}
            {tagFilter.size > 0 && (
              <button onClick={() => setTagFilter(new Set())} className="ml-1 text-xs text-muted hover:text-ink">limpar</button>
            )}
          </div>
        )}

        {/* barra de ações em lote */}
        <div className="flex flex-wrap items-center gap-2 border-b border-border px-4 py-2">
          <button onClick={toggleAll} className="flex items-center gap-1.5 text-sm text-muted transition-colors hover:text-ink">
            {allSelected ? <CheckSquare size={16} className="text-accent-hover" /> : <Square size={16} />}
            {sel.size > 0 ? `${sel.size} selecionada${sel.size === 1 ? "" : "s"}` : "Selecionar tudo"}
          </button>
          {sel.size > 0 && (
            <div className="ml-auto flex items-center gap-1.5">
              {/* mover para pasta/projeto */}
              <div className="relative" ref={moveRef}>
                <button
                  onClick={() => setMoveOpen((v) => !v)}
                  disabled={busy}
                  className="flex items-center gap-1.5 rounded-full border border-border px-3 py-1.5 text-sm text-ink-soft transition-colors hover:bg-hover hover:text-ink disabled:opacity-50"
                >
                  <FolderInput size={14} /> Mover para <ChevronDown size={13} />
                </button>
                {moveOpen && (
                  <div className="absolute right-0 top-10 z-50 max-h-64 w-60 overflow-y-auto rounded-xl border border-border bg-surface p-1 shadow-menu animate-pop">
                    <p className="px-2 py-1 text-[10px] font-semibold uppercase tracking-wider text-muted">Projetos (pastas)</p>
                    {folders.length === 0 && <p className="px-2 py-2 text-xs text-muted">Nenhuma pasta criada.</p>}
                    {folders.map((f) => (
                      <button
                        key={f.id}
                        onClick={() => move(f.id)}
                        className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-sm text-ink transition-colors hover:bg-hover"
                      >
                        <FolderInput size={14} className="shrink-0 text-muted" />
                        <span className="truncate">{f.name}</span>
                      </button>
                    ))}
                    <div className="my-1 border-t border-border" />
                    <button
                      onClick={() => move(null)}
                      className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-sm text-muted transition-colors hover:bg-hover hover:text-ink"
                    >
                      Tirar da pasta (raiz)
                    </button>
                  </div>
                )}
              </div>
              <button
                onClick={del}
                disabled={busy}
                className="flex items-center gap-1.5 rounded-full border border-border px-3 py-1.5 text-sm text-muted transition-colors hover:border-red-400/40 hover:text-red-400 disabled:opacity-50"
              >
                {busy ? <Loader2 size={14} className="animate-spin" /> : <Trash2 size={14} />} Excluir
              </button>
            </div>
          )}
        </div>

        {/* lista */}
        <div className="min-h-0 flex-1 overflow-y-auto p-2">
          {filtered.length === 0 ? (
            <p className="py-14 text-center text-sm text-muted">
              {q ? `Nenhuma conversa para “${q}”.` : "Nenhuma conversa ainda."}
            </p>
          ) : (
            <ul className="space-y-0.5">
              {filtered.map((c) => {
                const picked = sel.has(c.id);
                return (
                  <li
                    key={c.id}
                    className={`group flex items-center gap-2.5 rounded-xl px-2.5 py-2 transition-colors ${picked ? "bg-accent/10" : "hover:bg-hover"}`}
                  >
                    <button onClick={() => toggle(c.id)} className="shrink-0 text-muted transition-colors hover:text-ink" title="Selecionar">
                      {picked ? <CheckSquare size={17} className="text-accent-hover" /> : <Square size={17} />}
                    </button>
                    <button
                      onClick={() => { onSelect(c.id); onClose(); }}
                      className="flex min-w-0 flex-1 items-center gap-2 text-left"
                    >
                      <span className="min-w-0 flex-1 truncate text-sm text-ink">{c.title}</span>
                      {(c.tags || []).slice(0, 3).map((t) => (
                        <span key={t} className="hidden shrink-0 rounded-full bg-surface2 px-1.5 py-0.5 text-[10px] text-muted sm:inline">{t}</span>
                      ))}
                      {c.folder_id && folderName[c.folder_id] && (
                        <span className="hidden shrink-0 items-center gap-1 rounded-full bg-surface2 px-2 py-0.5 text-[10px] text-muted sm:flex">
                          <FolderInput size={10} /> {folderName[c.folder_id]}
                        </span>
                      )}
                      <span className="shrink-0 text-[11px] tabular-nums text-muted">{fmtDate(c.updated_at)}</span>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}
