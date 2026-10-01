"use client";

import { useEffect, useRef, useState } from "react";
import { Check, CornerDownRight, GitFork, ListEnd, MoreHorizontal, Pencil, Trash2 } from "lucide-react";
import { AnchoredMenu, MenuItem } from "./ui";
import { tr } from "@/lib/i18n";

/** Item da fila de mensagens do composer. `sent` = já foi injetado no turno em curso
 *  ("Enviar agora" durante a geração) e só espera o turno acabar para ir ao histórico. */
export type QueueItem = { id: string; text: string; sent?: boolean };

/** Bandeja da fila, presa em cima da caixa de mensagem (sai de trás dela): cada
 *  mensagem enfileirada numa linha, com "Enviar agora", apagar e ⋯ (Editar / Fork).
 *  Mostra até 4 linhas; o resto rola dentro dela. */
export default function QueueTray({ items, onSendNow, onDelete, onEdit, onFork }: {
  items: QueueItem[];
  onSendNow: (id: string) => void;
  onDelete: (id: string) => void;
  onEdit: (id: string, text: string) => void;
  onFork: (id: string) => void;
}) {
  const [editing, setEditing] = useState<string | null>(null);
  if (!items.length) return null;
  return (
    <div className="mx-auto max-w-3xl px-3">
      {/* -mb-4/pb-4: a base da bandeja entra por baixo do topo arredondado da caixa */}
      <div className="-mb-4 max-h-[calc(4*2.25rem+1rem+0.5rem)] overflow-y-auto rounded-t-2xl border border-b-0 border-border bg-surface2/70 px-1.5 pb-4 pt-1 backdrop-blur-sm">
        {items.map((q) =>
          editing === q.id ? (
            <EditRow key={q.id} text={q.text}
              onSave={(t) => { onEdit(q.id, t); setEditing(null); }}
              onCancel={() => setEditing(null)} />
          ) : (
            <Row key={q.id} item={q}
              onSendNow={() => onSendNow(q.id)}
              onDelete={() => onDelete(q.id)}
              onEdit={() => setEditing(q.id)}
              onFork={() => onFork(q.id)} />
          ),
        )}
      </div>
    </div>
  );
}

function Row({ item, onSendNow, onDelete, onEdit, onFork }: {
  item: QueueItem; onSendNow: () => void; onDelete: () => void; onEdit: () => void; onFork: () => void;
}) {
  const moreRef = useRef<HTMLButtonElement>(null);
  const [menu, setMenu] = useState(false);
  const icon = "flex h-7 w-7 flex-none items-center justify-center rounded-lg text-muted transition-colors hover:bg-hover hover:text-ink";
  return (
    <div className={`group flex h-9 items-center gap-2 rounded-lg px-1.5 ${item.sent ? "opacity-60" : ""}`}>
      {item.sent
        ? <Check size={14} className="flex-none text-accent-hover" aria-label={tr("Enviada")} />
        : <ListEnd size={14} className="flex-none text-muted" />}
      <span className="min-w-0 flex-1 truncate text-sm text-ink-soft" title={item.text}>{item.text}</span>
      {item.sent ? (
        <span className="flex-none pr-1 text-xs text-muted">{tr("Enviada")}</span>
      ) : (
        <>
          <button type="button" onClick={onSendNow} title={tr("Enviar agora")}
            className="flex h-7 flex-none items-center gap-1 rounded-lg px-2 text-xs text-ink-soft transition-colors hover:bg-hover hover:text-ink">
            <CornerDownRight size={13} />  {tr("Enviar agora")}
          </button>
          <button type="button" onClick={onDelete} title={tr("Remover da fila")} aria-label={tr("Remover da fila")} className={icon}>
            <Trash2 size={14} />
          </button>
          <button ref={moreRef} type="button" onClick={() => setMenu((v) => !v)} title={tr("Mais opções")} aria-label={tr("Mais opções")} className={icon}>
            <MoreHorizontal size={15} />
          </button>
          {menu && (
            <AnchoredMenu anchorRef={moreRef} onClose={() => setMenu(false)} className="min-w-[180px]">
              <MenuItem icon={<Pencil size={15} />} onClick={() => { setMenu(false); onEdit(); }}>{tr("Editar")}</MenuItem>
              <MenuItem icon={<GitFork size={15} />} onClick={() => { setMenu(false); onFork(); }}>{tr("Fork com esta mensagem")}</MenuItem>
            </AnchoredMenu>
          )}
        </>
      )}
    </div>
  );
}

function EditRow({ text, onSave, onCancel }: { text: string; onSave: (t: string) => void; onCancel: () => void }) {
  const [v, setV] = useState(text);
  const ref = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.focus();
    el.setSelectionRange(el.value.length, el.value.length);
  }, []);
  const save = () => { if (v.trim()) onSave(v.trim()); else onCancel(); };
  return (
    <div className="flex items-start gap-2 rounded-lg bg-surface px-1.5 py-1.5">
      <Pencil size={14} className="mt-1.5 flex-none text-accent-hover" />
      <textarea ref={ref} value={v} rows={Math.min(4, v.split("\n").length)}
        onChange={(e) => setV(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); save(); }
          if (e.key === "Escape") { e.preventDefault(); onCancel(); }
        }}
        className="min-w-0 flex-1 resize-none bg-transparent py-1 text-sm text-ink outline-none" />
      <button type="button" onClick={onCancel} className="h-7 flex-none rounded-lg px-2 text-xs text-muted hover:bg-hover hover:text-ink">{tr("Cancelar")}</button>
      <button type="button" onClick={save} className="h-7 flex-none rounded-lg bg-accent px-2.5 text-xs font-medium text-white hover:bg-accent-hover">{tr("Salvar")}</button>
    </div>
  );
}
