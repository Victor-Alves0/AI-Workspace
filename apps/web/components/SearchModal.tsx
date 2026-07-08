"use client";

import { useMemo, useState } from "react";
import { Search } from "lucide-react";
import type { Chat } from "@/lib/types";
import { useClickOutside } from "./ui";

export default function SearchModal({
  chats,
  onSelect,
  onClose,
}: {
  chats: Chat[];
  onSelect: (id: string) => void;
  onClose: () => void;
}) {
  const [q, setQ] = useState("");
  const ref = useClickOutside<HTMLDivElement>(onClose);
  const results = useMemo(
    () => chats.filter((c) => c.title.toLowerCase().includes(q.toLowerCase())).slice(0, 50),
    [chats, q],
  );

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/60 pt-28 backdrop-blur-sm">
      <div ref={ref} className="w-full max-w-lg overflow-hidden rounded-2xl border border-border bg-surface shadow-2xl">
        <div className="flex items-center gap-2 border-b border-border px-4 py-3">
          <Search size={18} className="text-muted" />
          <input
            autoFocus
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Pesquisar chats…"
            className="w-full bg-transparent text-sm outline-none placeholder:text-muted"
          />
        </div>
        <div className="max-h-80 overflow-y-auto p-2">
          {results.map((c) => (
            <button
              key={c.id}
              onClick={() => {
                onSelect(c.id);
                onClose();
              }}
              className="block w-full truncate rounded-lg px-3 py-2 text-left text-sm hover:bg-hover"
            >
              {c.title}
            </button>
          ))}
          {results.length === 0 && <p className="px-3 py-4 text-center text-sm text-muted">Nada encontrado.</p>}
        </div>
      </div>
    </div>
  );
}
