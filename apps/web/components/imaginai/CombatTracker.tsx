"use client";

import { Swords } from "lucide-react";
import type { ImaginaiEncounter } from "./types";

type Entry = ImaginaiEncounter["order"][number];

// Vida do inimigo como o 5e a mostra ao jogador: estado aparente, não número.
const HEALTH_TONE: Record<string, string> = {
  ileso: "bg-emerald-400/10 text-emerald-300",
  ferido: "bg-amber-400/10 text-amber-300",
  "gravemente ferido": "bg-rose-400/15 text-rose-300",
  caído: "bg-surface2 text-muted",
  desconhecido: "bg-surface2 text-muted",
};

function HealthChip({ entry }: { entry: Entry }) {
  if ((entry.side === "player" || entry.side === "ally") && typeof entry.hp === "number") {
    const ratio = entry.hp_max ? entry.hp / entry.hp_max : 1;
    const tone = entry.hp <= 0 ? HEALTH_TONE.caído : ratio > 0.5 ? HEALTH_TONE.ileso : ratio > 0.25 ? HEALTH_TONE.ferido : HEALTH_TONE["gravemente ferido"];
    return <span className={`shrink-0 rounded-md px-1.5 py-0.5 font-mono text-[10px] tabular-nums ${tone}`}>{entry.hp}/{entry.hp_max}</span>;
  }
  const label = entry.health ?? "desconhecido";
  return <span className={`shrink-0 rounded-md px-1.5 py-0.5 text-[10px] ${HEALTH_TONE[label] ?? HEALTH_TONE.desconhecido}`}>{label}</span>;
}

/** Ordem de iniciativa do combate em andamento, com a vez atual em destaque. */
export default function CombatTracker({ encounter }: { encounter: ImaginaiEncounter }) {
  return (
    <section className="imaginai-dock-card animate-pop" aria-label="Combate">
      <div className="flex min-h-7 items-center justify-between gap-2">
        <p className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-rose-300">
          <Swords size={12} /> Combate
        </p>
        <span className="font-mono text-[10px] tabular-nums text-muted">Rodada {encounter.round}</span>
      </div>
      <ol className="mt-1.5 space-y-1" aria-label="Ordem de iniciativa">
        {encounter.order.map((entry) => {
          const caido = entry.health === "caído";
          return (
            <li
              key={entry.id}
              aria-current={entry.current ? "true" : undefined}
              className={`flex min-w-0 items-center gap-2 rounded-lg px-2 py-1.5 text-xs transition-colors ${
                entry.current ? "bg-violet-500/15 ring-1 ring-violet-400/40" : "bg-surface2/50"
              } ${caido ? "opacity-50" : ""}`}
            >
              <span className="w-5 shrink-0 text-center font-mono text-[10px] tabular-nums text-muted">{entry.initiative}</span>
              <span className="min-w-0 flex-1">
                <span className={`block truncate ${entry.side === "player" ? "font-medium text-ink" : entry.side === "ally" ? "text-emerald-200" : "text-ink-soft"} ${caido ? "line-through" : ""}`}>
                  {entry.name}{entry.side === "ally" ? <span className="ml-1 text-[9px] text-emerald-300/80">aliado</span> : null}
                </span>
                {entry.conditions?.length ? (
                  <span className="mt-0.5 flex flex-wrap gap-1">
                    {entry.conditions.map((c) => (
                      <span key={c.key} className="rounded bg-amber-400/10 px-1 text-[9px] leading-4 text-amber-200" title={c.rounds ? `${c.rounds} rodada(s)` : "até ser removida"}>
                        {c.label}{c.rounds ? ` ${c.rounds}` : ""}
                      </span>
                    ))}
                  </span>
                ) : null}
              </span>
              {entry.current && entry.side === "player" ? (
                <span className="shrink-0 text-[10px] font-medium text-violet-200">Sua vez</span>
              ) : null}
              <HealthChip entry={entry} />
            </li>
          );
        })}
      </ol>
    </section>
  );
}
