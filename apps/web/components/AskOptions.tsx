"use client";

import { X } from "lucide-react";
import type { AskSpec } from "@/lib/types";

/** Seletor de opções (primitiva "kind:ask") — flutua ACIMA da promptbox, SEM card:
 *  a pergunta e as opções (chips) ficam soltas sobre o fundo da página. Escolher
 *  envia o valor como a próxima mensagem (Padrão A). Resposta livre = digitar na
 *  promptbox. */
export default function AskOptions({
  spec,
  onPick,
  onDismiss,
}: {
  spec: AskSpec;
  onPick: (value: string) => void;
  onDismiss?: () => void;
}) {
  return (
    <div className="animate-fade-up mx-auto mb-2 w-[92%] max-w-2xl">
      {(spec.question || onDismiss) && (
        <div className="mb-1.5 flex items-start justify-between gap-2 px-1">
          {spec.question && <p className="text-sm font-medium text-ink">{spec.question}</p>}
          {onDismiss && (
            <button
              onClick={onDismiss}
              title="Dispensar"
              className="-mr-1 shrink-0 rounded-lg p-0.5 text-muted transition-colors hover:bg-hover hover:text-ink"
            >
              <X size={14} />
            </button>
          )}
        </div>
      )}
      <div className="flex flex-wrap gap-1.5">
        {spec.options.map((o, i) => (
          <button
            key={i}
            onClick={() => onPick(o.value)}
            title={o.hint}
            className="rounded-full border border-border/70 bg-surface px-3.5 py-1.5 text-sm text-ink shadow-sm transition-colors hover:border-accent/60 hover:bg-hover"
          >
            {o.label}
          </button>
        ))}
      </div>
    </div>
  );
}
