"use client";

import { useEffect, useState } from "react";
import { ArchiveRestore, Trash2, X } from "lucide-react";
import { api } from "@/lib/api";
import type { Chat } from "@/lib/types";
import { useClickOutside } from "./ui";

export default function ArchivedModal({
  onChanged,
  onClose,
}: {
  onChanged: () => void;
  onClose: () => void;
}) {
  const [items, setItems] = useState<Chat[]>([]);
  const ref = useClickOutside<HTMLDivElement>(onClose);

  const load = () => api.get<Chat[]>("/chats?archived=true").then(setItems).catch(() => {});
  useEffect(() => {
    load();
  }, []);

  async function unarchive(id: string) {
    await api.patch(`/chats/${id}`, { archived: false });
    await load();
    onChanged();
  }
  async function remove(id: string) {
    await api.del(`/chats/${id}`);
    await load();
    onChanged();
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 px-4 backdrop-blur-sm">
      <div ref={ref} className="w-full max-w-lg rounded-2xl border border-border bg-surface p-5 shadow-2xl">
        <div className="mb-3 flex items-center justify-between gap-2">
          <h2 className="text-lg font-semibold">Chats Arquivados</h2>
          <button onClick={onClose} title="Fechar" className="rounded-lg p-1 text-muted transition-colors hover:bg-hover hover:text-ink"><X size={18} /></button>
        </div>
        <div className="max-h-96 space-y-1 overflow-y-auto">
          {items.map((c) => (
            <div key={c.id} className="flex items-center justify-between rounded-lg px-3 py-2 hover:bg-hover">
              <span className="truncate text-sm">{c.title}</span>
              <div className="flex shrink-0 gap-1">
                <button onClick={() => unarchive(c.id)} title="Desarquivar" className="rounded-md p-1.5 text-muted hover:bg-surface2 hover:text-ink-soft">
                  <ArchiveRestore size={16} />
                </button>
                <button onClick={() => remove(c.id)} title="Excluir" className="rounded-md p-1.5 text-muted hover:bg-surface2 hover:text-red-400">
                  <Trash2 size={16} />
                </button>
              </div>
            </div>
          ))}
          {items.length === 0 && <p className="py-6 text-center text-sm text-muted">Nenhum chat arquivado.</p>}
        </div>
      </div>
    </div>
  );
}
