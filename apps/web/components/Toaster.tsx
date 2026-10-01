"use client";

/**
 * Avisos curtos do app (no lugar do `alert()` do navegador): aparecem no topo, somem
 * sozinhos e não travam a tela. `toast("texto")` funciona de qualquer lugar — hook,
 * callback, código fora de componente —, porque o <Toaster/> escuta um canal próprio.
 */

import { useEffect, useState } from "react";
import { AlertCircle, CheckCircle2, Info, X } from "lucide-react";
import { tr } from "@/lib/i18n";

export type ToastKind = "error" | "success" | "info";
interface Item { id: number; text: string; kind: ToastKind }

type Listener = (t: Item) => void;
const listeners = new Set<Listener>();
let seq = 0;

/** Mostra um aviso. Padrão: erro (é o uso mais comum). */
export function toast(text: string, kind: ToastKind = "error") {
  const item = { id: ++seq, text: String(text || "").trim() || tr("Algo deu errado."), kind };
  listeners.forEach((l) => l(item));
}

const ICON = {
  error: <AlertCircle size={16} className="shrink-0 text-rose-400" />,
  success: <CheckCircle2 size={16} className="shrink-0 text-emerald-400" />,
  info: <Info size={16} className="shrink-0 text-accent-hover" />,
};

export function Toaster() {
  const [items, setItems] = useState<Item[]>([]);
  useEffect(() => {
    const add: Listener = (t) => {
      // o mesmo aviso repetido não empilha
      setItems((xs) => [...xs.filter((x) => x.text !== t.text), t].slice(-3));
      window.setTimeout(() => setItems((xs) => xs.filter((x) => x.id !== t.id)), t.kind === "error" ? 6000 : 4000);
    };
    listeners.add(add);
    return () => { listeners.delete(add); };
  }, []);
  if (!items.length) return null;
  return (
    <div className="pointer-events-none fixed inset-x-0 top-3 z-[110] flex flex-col items-center gap-2 px-4" role="status" aria-live="polite">
      {items.map((t) => (
        <div key={t.id}
          className="animate-pop pointer-events-auto flex max-w-md items-start gap-2.5 rounded-xl border border-border bg-surface px-3.5 py-2.5 text-sm text-ink shadow-menu">
          <span className="mt-px">{ICON[t.kind]}</span>
          <span className="min-w-0 flex-1 whitespace-pre-line break-words">{t.text}</span>
          <button type="button" onClick={() => setItems((xs) => xs.filter((x) => x.id !== t.id))}
            aria-label={tr("Fechar aviso")} className="-mr-1 rounded-md p-0.5 text-muted transition-colors hover:bg-hover hover:text-ink">
            <X size={14} />
          </button>
        </div>
      ))}
    </div>
  );
}
