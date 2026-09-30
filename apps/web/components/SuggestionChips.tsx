"use client";

import { useState } from "react";
import { Code2, GraduationCap, Lightbulb, ListChecks, PenLine, X } from "lucide-react";
import { SUGGESTION_CATEGORIES, pickFrom, type SuggestionCategoryId } from "@/lib/suggestions";

const ICONS: Record<SuggestionCategoryId, typeof PenLine> = {
  escrever: PenLine,
  aprender: GraduationCap,
  codigo: Code2,
  planejar: ListChecks,
  ideias: Lightbulb,
};

/** Sugestões do novo chat: chips por categoria; clicar abre a lista daquela categoria
 *  no lugar dos chips (o X volta). Escolher uma sugestão a escreve no composer. */
export default function SuggestionChips({ onPick }: { onPick: (text: string) => void }) {
  const [open, setOpen] = useState<{ id: SuggestionCategoryId; items: string[] } | null>(null);

  if (open) {
    const cat = SUGGESTION_CATEGORIES.find((c) => c.id === open.id)!;
    const Icon = ICONS[cat.id];
    return (
      <div className="animate-pop overflow-hidden rounded-2xl border border-border bg-surface/60">
        <div className="flex items-center gap-2 px-4 pb-1 pt-3 text-xs text-muted">
          <Icon size={14} />
          <span className="flex-1">{cat.label}</span>
          <button type="button" onClick={() => setOpen(null)} title="Fechar" aria-label="Fechar sugestões"
            className="-mr-1 rounded-lg p-1 text-muted transition-colors hover:bg-hover hover:text-ink">
            <X size={16} />
          </button>
        </div>
        <ul className="px-2 pb-2">
          {open.items.map((p, i) => (
            <li key={p} className={i > 0 ? "border-t border-border/70" : ""}>
              <button type="button" onClick={() => { setOpen(null); onPick(p); }}
                className="w-full rounded-lg px-2 py-2.5 text-left text-sm text-ink-soft transition-colors hover:bg-hover hover:text-ink">
                {p}
              </button>
            </li>
          ))}
        </ul>
      </div>
    );
  }

  return (
    <div className="flex flex-wrap justify-center gap-2">
      {SUGGESTION_CATEGORIES.map((c) => {
        const Icon = ICONS[c.id];
        return (
          <button key={c.id} type="button" onClick={() => setOpen({ id: c.id, items: pickFrom(c.prompts) })}
            className="flex items-center gap-2 rounded-xl border border-border bg-surface/60 px-3 py-1.5 text-sm text-ink-soft transition-colors hover:bg-hover hover:text-ink">
            <Icon size={15} className="text-muted" />
            {c.label}
          </button>
        );
      })}
    </div>
  );
}
