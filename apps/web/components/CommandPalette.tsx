"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { CornerDownLeft, Search } from "lucide-react";
import { useClickOutside } from "./ui";

export interface PaletteItem {
  id: string;
  group: string;
  label: string;
  sublabel?: string;
  /** termos extras para a busca casar (não exibidos) */
  keywords?: string;
  icon?: React.ReactNode;
  run: () => void;
}

/* Paleta de comandos (Ctrl/⌘ K): um lançador único para pular a qualquer chat,
 * modelo, ação ou configuração. Busca difusa + navegação por teclado. */
export default function CommandPalette({ items, onClose }: {
  items: PaletteItem[];
  onClose: () => void;
}) {
  const ref = useClickOutside<HTMLDivElement>(onClose);
  const [q, setQ] = useState("");
  const [sel, setSel] = useState(0);
  const rowsRef = useRef<HTMLDivElement>(null);

  const flat = useMemo(() => {
    const f = q.trim().toLowerCase();
    if (!f) return items;
    // match por todas as palavras (ordem livre) em label+sublabel+keywords+grupo
    const words = f.split(/\s+/);
    return items.filter((it) => {
      const hay = `${it.label} ${it.sublabel ?? ""} ${it.keywords ?? ""} ${it.group}`.toLowerCase();
      return words.every((w) => hay.includes(w));
    });
  }, [q, items]);

  useEffect(() => { setSel(0); }, [q]);

  function run(i: number) {
    const it = flat[i];
    if (!it) return;
    onClose();
    it.run();
  }

  function onKeyDown(e: React.KeyboardEvent) {
    if (e.key === "ArrowDown") { e.preventDefault(); setSel((s) => Math.min(s + 1, flat.length - 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setSel((s) => Math.max(s - 1, 0)); }
    else if (e.key === "Enter") { e.preventDefault(); run(sel); }
    else if (e.key === "Escape") { e.preventDefault(); onClose(); }
  }

  // mantém o item selecionado visível durante a navegação
  useEffect(() => {
    rowsRef.current?.querySelector<HTMLElement>(`[data-idx="${sel}"]`)?.scrollIntoView({ block: "nearest" });
  }, [sel]);

  // renderiza a lista plana inserindo cabeçalhos quando o grupo muda (mantém o
  // índice global p/ a navegação por teclado bater com o que aparece)
  let lastGroup = "";
  return (
    <div className="fixed inset-0 z-[90] flex items-start justify-center bg-black/60 px-4 pt-[calc(6rem+env(safe-area-inset-top))] backdrop-blur-sm">
      <div ref={ref} className="animate-pop flex max-h-[70vh] w-full max-w-xl flex-col overflow-hidden rounded-2xl border border-border bg-surface shadow-2xl">
        <div className="flex items-center gap-2 border-b border-border px-4 py-3">
          <Search size={18} className="shrink-0 text-muted" />
          <input
            autoFocus
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder="Busque um chat, modelo, ação ou configuração…"
            className="w-full bg-transparent text-sm text-ink outline-none placeholder:text-muted"
          />
          <kbd className="hidden shrink-0 rounded border border-border px-1.5 py-0.5 text-[10px] text-muted sm:block">Esc</kbd>
        </div>

        <div ref={rowsRef} className="min-h-0 flex-1 overflow-y-auto p-1.5">
          {flat.map((it, i) => {
            const header = it.group !== lastGroup ? it.group : null;
            lastGroup = it.group;
            return (
              <div key={it.id}>
                {header && (
                  <p className="px-2.5 pb-1 pt-2 text-[11px] font-medium uppercase tracking-wider text-muted first:pt-1">{header}</p>
                )}
                <button
                  data-idx={i}
                  onClick={() => run(i)}
                  onMouseMove={() => setSel(i)}
                  className={`flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left transition-colors ${i === sel ? "bg-accent/15" : "hover:bg-hover"}`}
                >
                  {it.icon && <span className={`shrink-0 ${i === sel ? "text-accent-hover" : "text-muted"}`}>{it.icon}</span>}
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm text-ink">{it.label}</span>
                    {it.sublabel && <span className="block truncate text-xs text-muted">{it.sublabel}</span>}
                  </span>
                  {i === sel && <CornerDownLeft size={14} className="shrink-0 text-muted" />}
                </button>
              </div>
            );
          })}
          {flat.length === 0 && <p className="px-3 py-8 text-center text-sm text-muted">Nada encontrado.</p>}
        </div>
      </div>
    </div>
  );
}
