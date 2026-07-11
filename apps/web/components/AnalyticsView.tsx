"use client";

import { useEffect, useMemo, useState } from "react";
import { ArrowDown, ArrowLeftRight, ArrowUp, BarChart3, ChevronDown, Coins, Cpu, Database, DollarSign, MessageSquare, Search, Sparkles, Wrench, Zap } from "lucide-react";
import { api } from "@/lib/api";
import { useClickOutside } from "./ui";

type ByModel = { id: string; model: string; provider: string; vendor: string; messages: number; tokens: number; cost: number; pct: number };
type Stack = { t: number; c: number; m: number };
type PerDay = { date: string; label?: string; tokens: number; cost: number; messages: number; by: Record<string, Stack> };
type Series = { id: string; name: string; provider: string };
type Credits = { total: number; usage: number; remaining: number };
type Activity = { start: string; end?: string; year?: number; days: { d: string; t: number }[]; longest_streak: number; avg_day: number; avg_week: number; year_tokens?: number; total_tokens: number };
type RangeKey = "7d" | "30d" | "6m" | "1y" | "all";
type Metric = "tokens" | "cost" | "messages";
type Overview = {
  by_model: ByModel[];
  per_day: PerDay[];
  series: Series[];
  range?: RangeKey;
  granularity?: "day" | "week" | "month";
  credits: Credits | null;
  activity: Activity;
  prev_totals: { tokens: number; cost: number; messages: number } | null;
  totals: {
    chats: number; messages: number; tokens: number; prompt_tokens: number;
    completion_tokens: number; reasoning_tokens: number; cost: number; avg_tokens: number;
  };
};

type ModelDay = { date: string; label: string; tokens: number; cost: number; messages: number };
type ModelDetail = {
  key: string; model: string; provider: string; vendor: string;
  range: RangeKey; granularity: "day" | "week" | "month";
  per_day: ModelDay[];
  by_tool: { tool: string; tokens: number; calls: number }[];
  by_chat: { chat_id: string; title: string; tokens: number; cost: number; messages: number }[];
  totals: {
    messages: number; tokens: number; prompt_tokens: number; completion_tokens: number;
    reasoning_tokens: number; cached_tokens: number; cost: number; avg_tokens: number; avg_cost: number;
  };
};

// paleta das séries (top-5 + Outros)
const COLORS = ["#8b5cf6", "#60a5fa", "#34d399", "#fbbf24", "#f472b6", "#64748b"];
const MONTHS = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"];

const nf = new Intl.NumberFormat("pt-BR");
const fmtN = (n: number) => nf.format(Math.round(n));
const fmtUSD = (n: number) => `$${n.toLocaleString("pt-BR", { minimumFractionDigits: n < 1 ? 4 : 2, maximumFractionDigits: n < 1 ? 4 : 2 })}`;
// 4,78M / 144K / 982 — compacto como no painel do OpenRouter
const fmtC = (n: number) => {
  if (n >= 1_000_000) return `${(n / 1_000_000).toLocaleString("pt-BR", { maximumFractionDigits: 2 })}M`;
  if (n >= 1_000) return `${(n / 1_000).toLocaleString("pt-BR", { maximumFractionDigits: n >= 10_000 ? 0 : 1 })}K`;
  return fmtN(n);
};
const metricVal = (m: Metric, o: { tokens: number; cost: number; messages: number }) =>
  m === "tokens" ? o.tokens : m === "cost" ? o.cost : o.messages;
const fmtMetric = (m: Metric, v: number) => (m === "cost" ? fmtUSD(v) : fmtC(v));

const RANGES: { key: RangeKey; label: string }[] = [
  { key: "7d", label: "Últimos 7 dias" },
  { key: "30d", label: "Último mês" },
  { key: "6m", label: "Últimos 6 meses" },
  { key: "1y", label: "Último ano" },
  { key: "all", label: "Tudo" },
];
const METRICS: { key: Metric; label: string }[] = [
  { key: "tokens", label: "Tokens" },
  { key: "cost", label: "Gasto" },
  { key: "messages", label: "Requisições" },
];

/* --------------------- Resumo: nº grande + delta + barras empilhadas --------------------- */
function Summary({ data, metric, busy, onMetric }: {
  data: Overview; metric: Metric; busy: boolean; onMetric: (m: Metric) => void;
}) {
  const [tip, setTip] = useState<{ x: number; y: number; i: number } | null>(null);
  const total = metricVal(metric, data.totals);
  const prev = data.prev_totals ? metricVal(metric, data.prev_totals) : null;
  const delta = prev != null && prev > 0 ? ((total - prev) / prev) * 100 : null;

  const colorOf = (id: string) => {
    const i = data.series.findIndex((s) => s.id === id);
    return id === "__other__" ? COLORS[5] : COLORS[(i >= 0 ? i : 0) % 5];
  };
  const max = Math.max(1, ...data.per_day.map((d) => metricVal(metric, d)));
  const n = data.per_day.length;
  const step = n <= 12 ? 1 : Math.ceil(n / 8);
  const active = tip ? data.per_day[tip.i] : null;
  const granLabel = data.granularity === "day" ? "por dia" : data.granularity === "week" ? "por semana" : "por mês";

  return (
    <div className="relative rounded-2xl border border-border bg-surface p-4">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <h2 className="text-sm font-semibold text-ink">Resumo de uso</h2>
        <div className="flex rounded-lg border border-border bg-surface2 p-0.5">
          {METRICS.map((m) => (
            <button
              key={m.key}
              onClick={() => onMetric(m.key)}
              className={`rounded-md px-2.5 py-0.5 text-xs transition-colors ${metric === m.key ? "bg-bg font-medium text-ink shadow-sm" : "text-muted hover:text-ink-soft"}`}
            >
              {m.label}
            </button>
          ))}
        </div>
        <span className="ml-auto text-[11px] text-muted">Empilhado {granLabel}, por modelo</span>
      </div>

      <div className="mb-1 flex items-baseline gap-3">
        <p className="text-4xl font-bold tracking-tight text-ink">{fmtMetric(metric, total)}</p>
        {delta != null && (
          <span className={`flex items-center gap-0.5 text-xs font-medium ${delta >= 0 ? "text-emerald-400" : "text-red-400"}`}>
            {delta >= 0 ? <ArrowUp size={12} /> : <ArrowDown size={12} />}
            {Math.abs(delta).toLocaleString("pt-BR", { maximumFractionDigits: 1 })}% vs. período anterior
          </span>
        )}
      </div>

      {/* barras empilhadas por modelo — passe o mouse por cima p/ ver o dia */}
      <div className={`mt-3 transition-opacity ${busy ? "opacity-50" : ""}`}>
        <div className="flex items-end gap-1" style={{ height: 132 }}>
          {data.per_day.map((d, i) => {
            const v = metricVal(metric, d);
            const h = (v / max) * 124;
            return (
              <div key={d.date} className="flex h-full min-w-0 flex-1 flex-col justify-end"
                   onMouseMove={(e) => setTip({ x: e.clientX, y: e.clientY, i })}
                   onMouseLeave={() => setTip(null)}>
                <div className="flex w-full flex-col-reverse overflow-hidden rounded-sm"
                     style={{ height: Math.max(v > 0 ? 3 : 1, h), opacity: tip === null || tip.i === i ? 1 : 0.4 }}>
                  {data.series.map((s) => {
                    const e = d.by[s.id];
                    const sv = e ? (metric === "tokens" ? e.t : metric === "cost" ? e.c : e.m) : 0;
                    if (!sv || !v) return null;
                    return <div key={s.id} style={{ height: `${(sv / v) * 100}%`, background: colorOf(s.id) }} />;
                  })}
                  {v === 0 && <div className="h-full bg-surface2" />}
                </div>
              </div>
            );
          })}
        </div>
        <div className="mt-1 flex gap-1">
          {data.per_day.map((d, i) => (
            <span key={d.date} className="min-w-0 flex-1 truncate text-center text-[10px] leading-3 text-muted">
              {i % step === 0 || i === n - 1 ? d.label : ""}
            </span>
          ))}
        </div>
        {/* legenda: qual cor é qual modelo */}
        <div className="mt-3 flex flex-wrap gap-x-3 gap-y-1">
          {data.series.map((s) => (
            <span key={s.id} className="inline-flex items-center gap-1.5 text-[11px] text-muted">
              <span className="h-2 w-2 rounded-sm" style={{ background: colorOf(s.id) }} />
              {s.name}
            </span>
          ))}
        </div>
      </div>

      {active && tip && (
        <div className="pointer-events-none fixed z-50 -translate-x-1/2 -translate-y-full rounded-lg border border-border bg-surface2 px-2.5 py-2 text-xs shadow-menu"
             style={{ left: tip.x, top: tip.y - 10 }}>
          <p className="mb-1 font-medium text-ink">{active.label} · {fmtMetric(metric, metricVal(metric, active))}</p>
          {data.series.filter((s) => active.by[s.id]).slice(0, 6).map((s) => {
            const e = active.by[s.id];
            const sv = metric === "tokens" ? e.t : metric === "cost" ? e.c : e.m;
            return (
              <p key={s.id} className="flex items-center gap-1.5 tabular-nums text-muted">
                <span className="h-1.5 w-1.5 rounded-full" style={{ background: colorOf(s.id) }} />
                {s.name}: <span className="text-ink-soft">{fmtMetric(metric, sv)}</span>
              </p>
            );
          })}
          {!data.series.some((s) => active.by[s.id]) && <p className="text-muted">sem uso</p>}
        </div>
      )}
    </div>
  );
}

/* ------------------------------- Top modelos ------------------------------- */
function TopModels({ data, metric, onPick }: { data: Overview; metric: Metric; onPick?: (id: string) => void }) {
  const list = useMemo(
    () => [...data.by_model].sort((a, b) => metricVal(metric, b) - metricVal(metric, a)).slice(0, 6),
    [data, metric],
  );
  const max = Math.max(1, ...list.map((m) => metricVal(metric, m)));
  const colorOf = (id: string) => {
    const i = data.series.findIndex((s) => s.id === id);
    return i >= 0 && i < 5 ? COLORS[i] : COLORS[5];
  };
  return (
    <div className="rounded-2xl border border-border bg-surface p-4">
      <div className="mb-3 flex items-center gap-2">
        <Cpu size={15} className="text-accent-hover" />
        <h2 className="text-sm font-semibold text-ink">Top modelos</h2>
        <span className="text-[11px] text-muted">por {METRICS.find((m) => m.key === metric)?.label.toLowerCase()}</span>
      </div>
      <div className="space-y-2.5">
        {list.map((m) => {
          const v = metricVal(metric, m);
          return (
            <button
              key={m.id}
              onClick={() => onPick?.(m.id)}
              title="Ver detalhes deste modelo"
              className="block w-full rounded-lg px-1 py-0.5 text-left transition-colors hover:bg-hover"
            >
              <div className="mb-1 flex items-center gap-2">
                <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-surface2 text-[10px] font-semibold" style={{ color: colorOf(m.id) }}>
                  {m.model[0]?.toUpperCase()}
                </span>
                <span className="min-w-0 flex-1 truncate text-sm font-medium text-ink">{m.model}</span>
                <span className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] ${m.provider === "ollama" ? "bg-emerald-400/15 text-emerald-300" : "bg-surface2 text-muted"}`}>
                  {m.provider === "ollama" ? "local" : m.vendor}
                </span>
                <span className="shrink-0 text-xs font-semibold tabular-nums text-ink">{fmtMetric(metric, v)}</span>
              </div>
              <div className="ml-8 h-1 overflow-hidden rounded-full bg-surface2">
                <div className="h-full rounded-full" style={{ width: `${(v / max) * 100}%`, background: colorOf(m.id) }} />
              </div>
            </button>
          );
        })}
        {list.length === 0 && <p className="text-xs text-muted">Sem uso no período.</p>}
      </div>
    </div>
  );
}

/* ------------------------- Atividade (heatmap anual) ------------------------- */
function ActivityCard({ act }: { act: Activity }) {
  const [tip, setTip] = useState<{ x: number; y: number; text: string } | null>(null);

  const grid = useMemo(() => {
    const byDay = new Map(act.days.map((d) => [d.d, d.t]));
    const todayIso = new Date().toISOString().slice(0, 10);
    const start = new Date(act.start + "T12:00:00");
    // grid = ano-calendário inteiro (1º/jan → 31/dez); dias futuros ficam esmaecidos
    const end = act.end ? new Date(act.end + "T12:00:00") : new Date();
    // alinha o início na segunda-feira (linhas Seg..Dom, colunas = semanas)
    const s = new Date(start);
    s.setDate(s.getDate() - ((s.getDay() + 6) % 7));
    const weeks: { date: Date; iso: string; t: number; future: boolean }[][] = [];
    const cur = new Date(s);
    while (cur <= end) {
      const col: { date: Date; iso: string; t: number; future: boolean }[] = [];
      for (let i = 0; i < 7 && cur <= end; i++) {
        const iso = cur.toISOString().slice(0, 10);
        col.push({ date: new Date(cur), iso, t: byDay.get(iso) ?? 0, future: iso > todayIso });
        cur.setDate(cur.getDate() + 1);
      }
      weeks.push(col);
    }
    const max = Math.max(1, ...act.days.map((d) => d.t));
    return { weeks, max };
  }, [act]);

  const level = (t: number) => (t <= 0 ? 0 : Math.min(4, 1 + Math.floor((t / grid.max) * 3.999)));
  const CELL = ["bg-surface2", "bg-accent/25", "bg-accent/45", "bg-accent/70", "bg-accent"];

  // rótulo de mês na coluna que contém o dia 1
  const monthLabel = (col: { date: Date }[]) => {
    const d1 = col.find((c) => c.date.getDate() === 1);
    return d1 ? MONTHS[d1.date.getMonth()] : "";
  };

  return (
    <div className="relative rounded-2xl border border-border bg-surface p-4">
      <div className="mb-3 flex items-center gap-2">
        <BarChart3 size={15} className="text-accent-hover" />
        <h2 className="text-sm font-semibold text-ink">Atividade</h2>
        <span className="ml-auto text-[11px] text-muted">Tokens · {act.year ?? new Date().getFullYear()}</span>
      </div>

      <div className="mb-4 flex flex-wrap gap-x-6 gap-y-2">
        {[
          ["Maior sequência", `${act.longest_streak} dias`],
          ["Média/dia", fmtC(act.avg_day)],
          ["Média/semana", fmtC(act.avg_week)],
          ["Total no ano", fmtC(act.year_tokens ?? act.total_tokens)],
        ].map(([l, v]) => (
          <div key={l}>
            <p className="text-[11px] text-muted">{l}</p>
            <p className="text-sm font-bold text-ink">{v}</p>
          </div>
        ))}
      </div>

      <div className="overflow-x-auto pb-1">
        <div className="inline-block">
          <div className="mb-1 flex gap-[2px] pl-9">
            {grid.weeks.map((col, i) => (
              <span key={i} className="w-[10px] shrink-0 text-[9px] leading-3 text-muted">{monthLabel(col)}</span>
            ))}
          </div>
          <div className="flex gap-1">
            <div className="flex w-8 shrink-0 flex-col gap-[2px] pr-1 text-right">
              {["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"].map((l, i) => (
                <span key={i} className="h-[10px] text-[9px] leading-[10px] text-muted">{l}</span>
              ))}
            </div>
            <div className="flex gap-[2px]">
              {grid.weeks.map((col, wi) => (
                <div key={wi} className="flex flex-col gap-[2px]">
                  {col.map((c) => (
                    <div
                      key={c.iso}
                      className={`h-[10px] w-[10px] rounded-[2px] ${CELL[level(c.t)]} ${c.future ? "opacity-30" : ""}`}
                      onMouseEnter={c.future ? undefined : (e) => {
                        const r = (e.target as HTMLElement).getBoundingClientRect();
                        setTip({ x: r.left + 5, y: r.top - 8, text: `${fmtN(c.t)} tokens em ${c.date.toLocaleDateString("pt-BR")}` });
                      }}
                      onMouseLeave={() => setTip(null)}
                    />
                  ))}
                </div>
              ))}
            </div>
          </div>
          <div className="mt-2 flex items-center gap-1 pl-9 text-[10px] text-muted">
            Menos
            {CELL.map((c, i) => <span key={i} className={`h-[10px] w-[10px] rounded-[2px] ${c}`} />)}
            Mais
          </div>
        </div>
      </div>

      {tip && (
        <div className="pointer-events-none fixed z-50 -translate-x-1/2 -translate-y-full rounded-lg border border-border bg-surface2 px-2.5 py-1.5 text-xs text-ink shadow-menu"
             style={{ left: tip.x, top: tip.y }}>
          {tip.text}
        </div>
      )}
    </div>
  );
}

/* ---------------------------------- créditos -------------------------------- */
function CreditsCard({ credits }: { credits: Credits | null }) {
  const usedPct = credits && credits.total > 0 ? Math.min(100, (credits.usage / credits.total) * 100) : 0;
  return (
    <div className="rounded-2xl border border-border bg-surface p-4">
      <div className="mb-3 flex items-center gap-2">
        <Coins size={16} className="text-accent-hover" />
        <h2 className="text-sm font-semibold text-ink">Créditos</h2>
        <span className="ml-auto text-[11px] text-muted">OpenRouter</span>
      </div>
      {credits ? (
        <>
          <p className="text-2xl font-bold text-ink">{fmtUSD(credits.remaining)}</p>
          <p className="text-xs text-muted">restante de {fmtUSD(credits.total)}</p>
          <div className="mt-3 h-2 w-full overflow-hidden rounded-full bg-surface2">
            <div className="h-full rounded-full bg-accent" style={{ width: `${usedPct}%` }} />
          </div>
          <p className="mt-1.5 text-[11px] text-muted">{fmtUSD(credits.usage)} usados</p>
        </>
      ) : (
        <p className="text-sm text-muted">Configure sua chave do OpenRouter em Configurações → Conexões para ver o saldo.</p>
      )}
    </div>
  );
}

function Stat({ icon, label, value, sub }: { icon: React.ReactNode; label: string; value: string; sub?: string }) {
  return (
    <div className="rounded-2xl border border-border bg-surface p-4">
      <div className="flex items-center gap-2 text-muted">
        <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-surface2 text-accent-hover">{icon}</span>
        <span className="text-xs">{label}</span>
      </div>
      <p className="mt-2.5 text-xl font-bold text-ink">{value}</p>
      {sub && <p className="text-xs text-muted">{sub}</p>}
    </div>
  );
}

/* ----------------------------- Detalhe por modelo ---------------------------- */
// nome amigável da ferramenta: último segmento do caminho (web.search.query → query),
// mas mantém o caminho no title p/ quem quiser o completo.
function toolLabel(path: string): string {
  const parts = path.split(/[.\/]/).filter(Boolean);
  return parts.length > 1 ? parts.slice(-2).join(".") : path;
}

function DetailBars({ days }: { days: ModelDay[] }) {
  const max = Math.max(1, ...days.map((d) => d.tokens));
  const n = days.length;
  const step = n <= 12 ? 1 : Math.ceil(n / 8);
  const [tip, setTip] = useState<{ x: number; y: number; i: number } | null>(null);
  const act = tip ? days[tip.i] : null;
  return (
    <div className="relative">
      <div className="flex items-end gap-1" style={{ height: 96 }}>
        {days.map((d, i) => (
          <div key={d.date} className="flex h-full min-w-0 flex-1 flex-col justify-end"
               onMouseMove={(e) => setTip({ x: e.clientX, y: e.clientY, i })}
               onMouseLeave={() => setTip(null)}>
            <div className="w-full rounded-sm bg-accent/70"
                 style={{ height: Math.max(d.tokens > 0 ? 3 : 1, (d.tokens / max) * 90), opacity: tip === null || tip.i === i ? 1 : 0.4 }} />
          </div>
        ))}
      </div>
      <div className="mt-1 flex gap-1">
        {days.map((d, i) => (
          <span key={d.date} className="min-w-0 flex-1 truncate text-center text-[10px] leading-3 text-muted">
            {i % step === 0 || i === n - 1 ? d.label : ""}
          </span>
        ))}
      </div>
      {act && tip && (
        <div className="pointer-events-none fixed z-50 -translate-x-1/2 -translate-y-full rounded-lg border border-border bg-surface2 px-2.5 py-1.5 text-xs shadow-menu"
             style={{ left: tip.x, top: tip.y - 10 }}>
          <span className="font-medium text-ink">{act.label}</span>
          <span className="text-muted"> · {fmtC(act.tokens)} tok · {fmtUSD(act.cost)}</span>
        </div>
      )}
    </div>
  );
}

function ModelDetail({ detail, loading }: { detail: ModelDetail | null; loading: boolean }) {
  if (loading) return <div className="flex items-center justify-center py-16 text-sm text-muted">Carregando…</div>;
  if (!detail) return <div className="flex items-center justify-center py-16 text-sm text-muted">Escolha um modelo.</div>;
  if (detail.totals.messages === 0) {
    return (
      <div className="flex flex-col items-center gap-2 rounded-2xl border border-dashed border-border py-16 text-center">
        <Cpu size={28} className="text-muted" />
        <p className="text-sm text-muted">Sem uso de <span className="font-medium text-ink">{detail.model}</span> no período.</p>
      </div>
    );
  }
  const t = detail.totals;
  const io = Math.max(1, t.prompt_tokens + t.completion_tokens);
  const inPct = (t.prompt_tokens / io) * 100;
  const toolMax = Math.max(1, ...detail.by_tool.map((x) => x.tokens));
  const chatMax = Math.max(1, ...detail.by_chat.map((x) => x.tokens));

  return (
    <div className="space-y-3">
      {/* cabeçalho do modelo */}
      <div className="flex items-center gap-2">
        <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-surface2 text-accent-hover"><Cpu size={17} /></span>
        <div className="min-w-0">
          <p className="truncate text-base font-semibold text-ink">{detail.model}</p>
          <p className="text-xs text-muted">
            <span className={`rounded px-1.5 py-0.5 ${detail.provider === "ollama" ? "bg-emerald-400/15 text-emerald-300" : "bg-surface2 text-muted"}`}>
              {detail.provider === "ollama" ? "local" : detail.vendor}
            </span>
            <span className="ml-2">{RANGES.find((r) => r.key === detail.range)?.label}</span>
          </p>
        </div>
      </div>

      {/* números-chave */}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat icon={<Sparkles size={15} />} label="Requisições" value={fmtN(t.messages)} />
        <Stat icon={<Zap size={15} />} label="Tokens" value={fmtC(t.tokens)} sub={`${fmtN(t.avg_tokens)}/msg`} />
        <Stat icon={<DollarSign size={15} />} label="Custo" value={fmtUSD(t.cost)} />
        <Stat icon={<DollarSign size={15} />} label="Custo médio" value={fmtUSD(t.avg_cost)} sub="por resposta" />
      </div>

      <div className="grid gap-3 lg:grid-cols-2">
        {/* entrada × saída */}
        <div className="rounded-2xl border border-border bg-surface p-4">
          <div className="mb-3 flex items-center gap-2">
            <ArrowLeftRight size={15} className="text-accent-hover" />
            <h3 className="text-sm font-semibold text-ink">Entrada × Saída</h3>
          </div>
          <div className="flex h-3 w-full overflow-hidden rounded-full bg-surface2">
            <div className="h-full bg-[#60a5fa]" style={{ width: `${inPct}%` }} />
            <div className="h-full bg-[#34d399]" style={{ width: `${100 - inPct}%` }} />
          </div>
          <div className="mt-3 grid grid-cols-2 gap-3 text-sm">
            <div>
              <p className="flex items-center gap-1.5 text-xs text-muted"><span className="h-2 w-2 rounded-sm bg-[#60a5fa]" /> Entrada</p>
              <p className="font-semibold text-ink">{fmtC(t.prompt_tokens)}</p>
              <p className="flex items-center gap-1 text-[11px] text-muted"><Database size={11} /> {fmtC(t.cached_tokens)} em cache</p>
            </div>
            <div>
              <p className="flex items-center gap-1.5 text-xs text-muted"><span className="h-2 w-2 rounded-sm bg-[#34d399]" /> Saída</p>
              <p className="font-semibold text-ink">{fmtC(t.completion_tokens)}</p>
              <p className="flex items-center gap-1 text-[11px] text-muted"><Cpu size={11} /> {fmtC(t.reasoning_tokens)} de raciocínio</p>
            </div>
          </div>
        </div>

        {/* uso ao longo do tempo */}
        <div className="rounded-2xl border border-border bg-surface p-4">
          <div className="mb-3 flex items-center gap-2">
            <BarChart3 size={15} className="text-accent-hover" />
            <h3 className="text-sm font-semibold text-ink">Tokens no período</h3>
            <span className="ml-auto text-[11px] text-muted">
              {detail.granularity === "day" ? "por dia" : detail.granularity === "week" ? "por semana" : "por mês"}
            </span>
          </div>
          <DetailBars days={detail.per_day} />
        </div>
      </div>

      <div className="grid gap-3 lg:grid-cols-2">
        {/* por ferramenta */}
        <div className="rounded-2xl border border-border bg-surface p-4">
          <div className="mb-3 flex items-center gap-2">
            <Wrench size={15} className="text-accent-hover" />
            <h3 className="text-sm font-semibold text-ink">Por ferramenta</h3>
            <span className="ml-auto text-[11px] text-muted">tokens · chamadas</span>
          </div>
          {detail.by_tool.length === 0 ? (
            <p className="py-4 text-center text-xs text-muted">Nenhuma ferramenta usada no período.</p>
          ) : (
            <div className="space-y-2.5">
              {detail.by_tool.slice(0, 10).map((x) => (
                <div key={x.tool}>
                  <div className="mb-1 flex items-center gap-2">
                    <span className="min-w-0 flex-1 truncate text-sm text-ink" title={x.tool}>{toolLabel(x.tool)}</span>
                    <span className="shrink-0 rounded bg-surface2 px-1.5 py-0.5 text-[10px] text-muted">{x.calls}×</span>
                    <span className="shrink-0 text-xs font-semibold tabular-nums text-ink">{fmtC(x.tokens)}</span>
                  </div>
                  <div className="h-1 overflow-hidden rounded-full bg-surface2">
                    <div className="h-full rounded-full bg-accent" style={{ width: `${(x.tokens / toolMax) * 100}%` }} />
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* por conversa (interação) */}
        <div className="rounded-2xl border border-border bg-surface p-4">
          <div className="mb-3 flex items-center gap-2">
            <MessageSquare size={15} className="text-accent-hover" />
            <h3 className="text-sm font-semibold text-ink">Por conversa</h3>
            <span className="ml-auto text-[11px] text-muted">tokens · custo</span>
          </div>
          {detail.by_chat.length === 0 ? (
            <p className="py-4 text-center text-xs text-muted">Sem conversas no período.</p>
          ) : (
            <div className="space-y-2.5">
              {detail.by_chat.slice(0, 10).map((x) => (
                <div key={x.chat_id}>
                  <div className="mb-1 flex items-center gap-2">
                    <span className="min-w-0 flex-1 truncate text-sm text-ink" title={x.title}>{x.title}</span>
                    <span className="shrink-0 text-[11px] text-muted">{fmtUSD(x.cost)}</span>
                    <span className="shrink-0 text-xs font-semibold tabular-nums text-ink">{fmtC(x.tokens)}</span>
                  </div>
                  <div className="h-1 overflow-hidden rounded-full bg-surface2">
                    <div className="h-full rounded-full bg-[#8b5cf6]" style={{ width: `${(x.tokens / chatMax) * 100}%` }} />
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

/** seletor de modelo estilo "seletor de modelos" (avatar + busca + badge de fonte) */
function ModelSelect({ models, value, onPick, colorOf }: {
  models: ByModel[]; value: string | null; onPick: (id: string) => void; colorOf: (id: string) => string;
}) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const ref = useClickOutside<HTMLDivElement>(() => setOpen(false));
  const cur = models.find((m) => m.id === value) ?? null;
  const rows = useMemo(() => {
    const f = q.trim().toLowerCase();
    return f ? models.filter((m) => m.model.toLowerCase().includes(f)) : models;
  }, [models, q]);
  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-2 rounded-lg border border-border bg-surface2 px-2.5 py-1.5 text-sm text-ink transition-colors hover:border-accent/50"
      >
        {cur ? (
          <>
            <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-md bg-surface text-[10px] font-semibold" style={{ color: colorOf(cur.id) }}>
              {cur.model[0]?.toUpperCase()}
            </span>
            <span className="max-w-[180px] truncate">{cur.model}</span>
          </>
        ) : (
          <span className="text-muted">Escolha um modelo</span>
        )}
        <ChevronDown size={14} className="text-muted" />
      </button>
      {open && (
        <div className="absolute left-0 top-10 z-50 w-72 overflow-hidden rounded-xl border border-border bg-surface shadow-menu animate-pop">
          <div className="flex items-center gap-2 border-b border-border px-3 py-2">
            <Search size={14} className="text-muted" />
            <input
              autoFocus value={q} onChange={(e) => setQ(e.target.value)}
              placeholder="Buscar modelo…"
              className="w-full bg-transparent text-sm text-ink outline-none placeholder:text-muted"
            />
          </div>
          <div className="max-h-64 overflow-y-auto p-1">
            {rows.map((m) => (
              <button
                key={m.id}
                onClick={() => { onPick(m.id); setOpen(false); setQ(""); }}
                className={`flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-sm transition-colors hover:bg-hover ${m.id === value ? "bg-hover" : ""}`}
              >
                <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-surface2 text-[10px] font-semibold" style={{ color: colorOf(m.id) }}>
                  {m.model[0]?.toUpperCase()}
                </span>
                <span className="min-w-0 flex-1 truncate text-ink">{m.model}</span>
                <span className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] ${m.provider === "ollama" ? "bg-emerald-400/15 text-emerald-300" : "bg-surface2 text-muted"}`}>
                  {m.provider === "ollama" ? "local" : m.vendor}
                </span>
              </button>
            ))}
            {rows.length === 0 && <p className="px-3 py-5 text-center text-sm text-muted">Nenhum modelo.</p>}
          </div>
        </div>
      )}
    </div>
  );
}

type Tab = "overview" | "model";

export default function AnalyticsView() {
  const [data, setData] = useState<Overview | null>(null);
  const [loading, setLoading] = useState(true);
  const [range, setRange] = useState<RangeKey>("7d");
  const [metric, setMetric] = useState<Metric>("tokens");
  const [busy, setBusy] = useState(false);
  const [tab, setTab] = useState<Tab>("overview");
  const [modelKey, setModelKey] = useState<string | null>(null);
  const [detail, setDetail] = useState<ModelDetail | null>(null);
  const [detailBusy, setDetailBusy] = useState(false);

  function loadRange(r: RangeKey, initial = false) {
    const off = new Date().getTimezoneOffset();
    if (!initial) setBusy(true);
    api
      .get<Overview>(`/analytics/overview?range=${r}&tz_offset=${off}`)
      .then(setData)
      .catch(() => { if (initial) setData(null); })
      .finally(() => { setLoading(false); setBusy(false); });
  }

  function loadDetail(key: string, r: RangeKey) {
    const off = new Date().getTimezoneOffset();
    setDetailBusy(true);
    api
      .get<ModelDetail>(`/analytics/model?key=${encodeURIComponent(key)}&range=${r}&tz_offset=${off}`)
      .then(setDetail)
      .catch(() => setDetail(null))
      .finally(() => setDetailBusy(false));
  }

  useEffect(() => { loadRange("7d", true); }, []);

  function changeRange(r: RangeKey) {
    if (r === range) return;
    setRange(r);
    loadRange(r);
    if (modelKey) loadDetail(modelKey, r);
  }

  // ao entrar na aba "Por modelo" sem seleção, pega o modelo mais usado
  function openModel(key: string) {
    setModelKey(key);
    setTab("model");
    loadDetail(key, range);
  }
  function selectModel(key: string) {
    setModelKey(key);
    loadDetail(key, range);
  }

  const empty = useMemo(() => !!data && (data.activity?.total_tokens ?? 0) === 0 && data.totals.messages === 0, [data]);

  if (loading) {
    return <div className="flex items-center justify-center py-24 text-sm text-muted">Carregando…</div>;
  }
  if (!data) {
    return <div className="flex items-center justify-center py-24 text-sm text-muted">Não foi possível carregar a analítica.</div>;
  }

  if (empty) {
    return (
      <div className="mx-auto grid max-w-5xl gap-3 lg:grid-cols-5">
        <div className="flex flex-col items-center justify-center rounded-2xl border border-dashed border-border bg-surface py-20 text-center lg:col-span-3">
          <span className="mb-4 flex h-14 w-14 items-center justify-center rounded-2xl bg-surface2 text-accent-hover"><BarChart3 size={26} /></span>
          <h2 className="text-lg font-semibold text-ink">Sem dados ainda</h2>
          <p className="mt-1.5 max-w-sm text-sm text-muted">Converse com seus modelos e as métricas de uso aparecem aqui.</p>
        </div>
        <div className="lg:col-span-2">
          <CreditsCard credits={data.credits} />
        </div>
      </div>
    );
  }

  // cor do avatar do modelo — mesma lógica de série do overview (top-5 + "outros")
  const overviewColorOf = (id: string) => {
    const i = data.series.findIndex((s) => s.id === id);
    return i >= 0 && i < 5 ? COLORS[i] : COLORS[5];
  };

  return (
    <div className="mx-auto max-w-6xl space-y-3">
      {/* barra: abas + período (compartilhado pelas duas abas) */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex rounded-lg border border-border bg-surface2 p-0.5">
          {([["overview", "Visão geral"], ["model", "Por modelo"]] as [Tab, string][]).map(([k, l]) => (
            <button
              key={k}
              onClick={() => {
                setTab(k);
                if (k === "model" && !modelKey && data.by_model[0]) selectModel(data.by_model[0].id);
              }}
              className={`rounded-md px-3 py-1 text-xs font-medium transition-colors ${tab === k ? "bg-bg text-ink shadow-sm" : "text-muted hover:text-ink-soft"}`}
            >
              {l}
            </button>
          ))}
        </div>
        {tab === "model" && (
          <ModelSelect models={data.by_model} value={modelKey} onPick={selectModel} colorOf={overviewColorOf} />
        )}
        <select
          value={range}
          onChange={(e) => changeRange(e.target.value as RangeKey)}
          className="ml-auto rounded-lg border border-border bg-surface2 px-2 py-1 text-xs text-ink outline-none transition-colors focus:border-accent"
        >
          {RANGES.map((r) => <option key={r.key} value={r.key}>{r.label}</option>)}
        </select>
      </div>

      {tab === "overview" ? (
        <>
          <div className="grid items-start gap-3 lg:grid-cols-3">
            <div className="space-y-3 lg:col-span-2">
              <Summary data={data} metric={metric} busy={busy} onMetric={setMetric} />
              <ActivityCard act={data.activity} />
            </div>
            <div className="space-y-3">
              <TopModels data={data} metric={metric} onPick={openModel} />
              <CreditsCard credits={data.credits} />
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-6">
            <Stat icon={<MessageSquare size={15} />} label="Conversas" value={fmtN(data.totals.chats)} />
            <Stat icon={<Sparkles size={15} />} label="Respostas" value={fmtN(data.totals.messages)} />
            <Stat icon={<Zap size={15} />} label="Tokens" value={fmtC(data.totals.tokens)} sub={`${fmtN(data.totals.avg_tokens)}/msg`} />
            <Stat icon={<DollarSign size={15} />} label="Custo" value={fmtUSD(data.totals.cost)} />
            <Stat icon={<Cpu size={15} />} label="Raciocínio" value={fmtC(data.totals.reasoning_tokens)} sub="tokens" />
            <Stat icon={<BarChart3 size={15} />} label="Entrada/Saída" value={`${fmtC(data.totals.prompt_tokens)}/${fmtC(data.totals.completion_tokens)}`} sub="tokens" />
          </div>
        </>
      ) : (
        <div className={busy || detailBusy ? "opacity-60 transition-opacity" : "transition-opacity"}>
          <ModelDetail detail={detail} loading={detailBusy && !detail} />
        </div>
      )}
    </div>
  );
}
