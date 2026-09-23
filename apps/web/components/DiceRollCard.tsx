"use client";

import { Dices, Trash2 } from "lucide-react";
import type { Message } from "@/lib/types";

export interface DiceRoll {
  expression: string;
  label: string;
  breakdown: string;
  total: number | string;
}

// rolagens gravadas antes dos dados estruturados: "🎲 **1d20** — rótulo: [4] = **4**"
const LEGADO = /^🎲 \*\*(.+?)\*\*(?: — (.+?))?: (.+) = \*\*(-?\d+)\*\*$/;

/** Rolagem do //roll, ou null se a mensagem não é uma. */
export function parseDiceRoll(m: Message): DiceRoll | null {
  if (m.role !== "user") return null;
  const u = m.usage as unknown as Record<string, unknown> | null | undefined;
  if (u?.kind === "dice") {
    return {
      expression: String(u.expression ?? ""),
      label: String(u.label ?? ""),
      breakdown: String(u.breakdown ?? ""),
      total: (u.total as number) ?? "",
    };
  }
  const x = LEGADO.exec((m.content || "").trim());
  return x ? { expression: x[1], label: x[2] ?? "", breakdown: x[3], total: x[4] } : null;
}

/** "[14, ~~3~~] + 2": dado descartado (kh/kl) aparece riscado. */
function Breakdown({ text }: { text: string }) {
  const partes = text.split(/(~~-?\d+~~)/g);
  return (
    <span className="tabular-nums">
      {partes.map((p, i) => {
        const riscado = /^~~(-?\d+)~~$/.exec(p);
        return riscado
          ? <s key={i} className="text-muted/70">{riscado[1]}</s>
          : <span key={i}>{p}</span>;
      })}
    </span>
  );
}

/** Rolagem de dados no CENTRO da conversa (é um evento da mesa, não uma fala). */
export default function DiceRollCard({ roll, onDelete }: { roll: DiceRoll; onDelete?: () => void }) {
  return (
    <div className="group mx-auto flex w-full max-w-3xl justify-center">
      <div className="relative flex items-center gap-3 rounded-2xl border border-border bg-surface px-4 py-2.5 text-sm">
        <Dices size={18} className="shrink-0 text-accent-hover" />
        <div className="flex min-w-0 flex-col leading-tight">
          <span className="text-[11px] text-muted">
            {roll.expression}
            {roll.label && <> · {roll.label}</>}
          </span>
          <span className="text-ink-soft"><Breakdown text={roll.breakdown} /></span>
        </div>
        <span className="text-2xl font-semibold tabular-nums text-ink">{roll.total}</span>
        {onDelete && (
          <button
            onClick={onDelete}
            title="Excluir rolagem"
            className="absolute -right-8 rounded p-1 text-muted touch-reveal opacity-0 transition-opacity hover:text-red-300 group-hover:opacity-100"
          >
            <Trash2 size={13} />
          </button>
        )}
      </div>
    </div>
  );
}
