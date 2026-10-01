"use client";

import { useEffect, useState } from "react";
import { Gauge, Layers, Timer, Zap } from "lucide-react";
import type { CallStats } from "@/app/chat/useGeneration";
import { dateLocale, tr } from "@/lib/i18n";

const fmtInt = new Intl.NumberFormat(dateLocale());
const fmtRate = new Intl.NumberFormat(dateLocale(), { minimumFractionDigits: 2, maximumFractionDigits: 2 });

function fmtLatency(ms: number): string {
  return ms < 1000 ? `${Math.round(ms)} ms` : `${fmtRate.format(ms / 1000)} s`;
}

function fmtElapsed(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  if (s < 60) return `${s}s`;
  return `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s`;
}

/** Linha discreta sob o composer: tokens gerados, latência, tempo e velocidade da chamada
 *  atual (ou da última, até a próxima começar). O relógio anda sozinho enquanto a
 *  resposta não termina — os tokens só mudam no ritmo do stream. */
export function CallStatsBar({ stats }: { stats: CallStats }) {
  const live = stats.endedAt === null;
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!live) return;
    setNow(Date.now());
    const t = setInterval(() => setNow(Date.now()), 250);
    return () => clearInterval(t);
  }, [live, stats.startedAt]);
  const elapsed = (stats.endedAt ?? now) - stats.startedAt;
  // abaixo de meio segundo a divisão explode (1º token chega com ~0s)
  const rate = elapsed >= 500 && stats.tokens > 0 ? stats.tokens / (elapsed / 1000) : null;
  return (
    <div className="flex items-center justify-center gap-4 overflow-hidden whitespace-nowrap px-2 pt-0.5 text-xs text-muted tabular-nums max-[340px]:hidden md:-mt-2 md:pt-0">
      <span className="flex items-center gap-1" title={stats.exact ? tr("Tokens gerados nesta resposta") : tr("Tokens gerados nesta resposta (estimativa)")}>
        <Layers size={12} />
        {stats.exact ? "" : "~"}{fmtInt.format(stats.tokens)} tokens
      </span>
      <span className="flex items-center gap-1" title={tr("Latência do provedor: do pedido ao 1º byte da resposta (última chamada ao modelo)")}>
        <Gauge size={12} />
        {stats.latencyMs === null ? "—" : fmtLatency(stats.latencyMs)}
      </span>
      <span className="flex items-center gap-1" title={tr("Tempo desta resposta")}>
        <Timer size={12} />
        {fmtElapsed(elapsed)}
      </span>
      <span className="flex items-center gap-1" title={tr("Tokens por segundo")}>
        <Zap size={12} />
        {rate === null ? "—" : fmtRate.format(rate)} t/s
      </span>
    </div>
  );
}
