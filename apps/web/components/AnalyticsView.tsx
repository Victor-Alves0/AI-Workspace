"use client";

import { useEffect, useMemo, useState } from "react";
import { ArrowDown, ArrowUp, BarChart3, Coins, Cpu, DollarSign, MessageSquare, Sparkles, Zap } from "lucide-react";
import { api } from "@/lib/api";

type ByModel = { id: string; model: string; provider: string; vendor: string; messages: number; tokens: number; cost: number; pct: number };
type Stack = { t: number; c: number; m: number };
type PerDay = { date: string; label?: string; tokens: number; cost: number; messages: number; by: Record<string, Stack> };
type Series = { id: string; name: string; provider: string };
type Credits = { total: number; usage: number; remaining: number };
type Activity = { start: string; days: { d: string; t: number }[]; longest_streak: number; avg_day: number; avg_week: number; total_tokens: number };
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
function Summary({ data, range, metric, busy, onRange, onMetric }: {
  data: Overview; range: RangeKey; metric: Metric; busy: boolean;
  onRange: (r: RangeKey) => void; onMetric: (m: Metric) => void;
}) {
  const [hover, setHover] = useState<number | null>(null);
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
  const active = hover !== null ? data.per_day[hover] : null;

  return (
    <div className="rounded-2xl border border-border bg-surface p-5">
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <h2 className="text-sm font-semibold text-ink">Resumo de uso</h2>
        <select
          value={range}
          onChange={(e) => onRange(e.target.value as RangeKey)}
          className="rounded-lg border border-border bg-surface2 px-2 py-1 text-xs text-ink outline-none transition-colors focus:border-accent"
        >
          {RANGES.map((r) => <option key={r.key} value={r.key}>{r.label}</option>)}
        </select>
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
        <span className="ml-auto text-[11px] text-muted">{data.granularity === "day" ? "Diário" : data.granularity === "week" ? "Semanal" : "Mensal"} · por modelo</span>
      </div>

      <div className="mb-1 flex items-baseline gap-3">
        <p className="text-4xl font-bold tracking-tight text-ink">{fmtMetric(metric, total)}</p>
        {delta != null && (
          <span className={`flex items-center gap-0.5 text-xs font-medium ${delta >= 0 ? "text-emerald-400" : "text-red-400"}`}>
            {delta >= 0 ? <ArrowUp size={12} /> : <ArrowDown size={12} />}
            {Math.abs(delta).toLocaleString("pt-BR", { maximumFractionDigits: 1 })}% vs período anterior
          </span>
        )}
      </div>

      {/* barras empilhadas por modelo */}
      <div className={`mt-4 transition-opacity ${busy ? "opacity-50" : ""}`}>
        <div className="flex items-end gap-1" style={{ height: 150 }}>
          {data.per_day.map((d, i) => {
            const v = metricVal(metric, d);
            const h = (v / max) * 142;
            return (
              <div key={d.date} className="flex h-full min-w-0 flex-1 flex-col justify-end"
                   onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}>
                <div className="flex w-full flex-col-reverse overflow-hidden rounded-sm"
                     style={{ height: Math.max(v > 0 ? 3 : 1, h), opacity: hover === null || hover === i ? 1 : 0.45 }}>
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
        <div className="mt-2 min-h-4 text-xs tabular-nums text-ink-soft">
          {active && (
            <>
              <span className="font-medium">{active.label}</span>
              {" · "}{fmtMetric(metric, metricVal(metric, active))}
              {data.series.filter((s) => active.by[s.id]).slice(0, 4).map((s) => {
                const e = active.by[s.id];
                const sv = metric === "tokens" ? e.t : metric === "cost" ? e.c : e.m;
                return (
                  <span key={s.id} className="ml-2 inline-flex items-center gap-1 text-muted">
                    <span className="h-1.5 w-1.5 rounded-full" style={{ background: colorOf(s.id) }} />
                    {s.name}: {fmtMetric(metric, sv)}
                  </span>
                );
              })}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

/* ------------------------------- Top modelos ------------------------------- */
function TopModels({ data, metric }: { data: Overview; metric: Metric }) {
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
    <div className="rounded-2xl border border-border bg-surface p-5">
      <div className="mb-3 flex items-center gap-2">
        <Cpu size={15} className="text-accent-hover" />
        <h2 className="text-sm font-semibold text-ink">Top modelos</h2>
        <span className="text-[11px] text-muted">por {METRICS.find((m) => m.key === metric)?.label.toLowerCase()}</span>
      </div>
      <div className="space-y-3">
        {list.map((m) => {
          const v = metricVal(metric, m);
          return (
            <div key={m.id}>
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
            </div>
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
    const today = new Date(); today.setHours(12, 0, 0, 0);
    const start = new Date(act.start + "T12:00:00");
    // alinha o início na segunda-feira (linhas Seg..Dom, colunas = semanas)
    const s = new Date(start);
    s.setDate(s.getDate() - ((s.getDay() + 6) % 7));
    const weeks: { date: Date; iso: string; t: number }[][] = [];
    const cur = new Date(s);
    while (cur <= today) {
      const col: { date: Date; iso: string; t: number }[] = [];
      for (let i = 0; i < 7 && cur <= today; i++) {
        const iso = cur.toISOString().slice(0, 10);
        col.push({ date: new Date(cur), iso, t: byDay.get(iso) ?? 0 });
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
    <div className="relative rounded-2xl border border-border bg-surface p-5">
      <div className="mb-3 flex items-center gap-2">
        <BarChart3 size={15} className="text-accent-hover" />
        <h2 className="text-sm font-semibold text-ink">Atividade</h2>
        <span className="ml-auto text-[11px] text-muted">Tokens · 12 meses</span>
      </div>

      <div className="mb-4 flex flex-wrap gap-x-8 gap-y-2">
        {[
          ["Maior sequência", `${act.longest_streak} dias`],
          ["Média/dia", fmtC(act.avg_day)],
          ["Média/semana", fmtC(act.avg_week)],
          ["Total", fmtC(act.total_tokens)],
        ].map(([l, v]) => (
          <div key={l}>
            <p className="text-[11px] text-muted">{l}</p>
            <p className="text-sm font-bold text-ink">{v}</p>
          </div>
        ))}
      </div>

      <div className="overflow-x-auto pb-1">
        <div className="inline-block">
          <div className="mb-1 flex gap-[3px] pl-7">
            {grid.weeks.map((col, i) => (
              <span key={i} className="w-[11px] shrink-0 text-[9px] leading-3 text-muted">{monthLabel(col)}</span>
            ))}
          </div>
          <div className="flex gap-1">
            <div className="flex w-6 flex-col gap-[3px] pr-1 text-right">
              {["S", "", "Q", "", "S", "", ""].map((l, i) => (
                <span key={i} className="h-[11px] text-[9px] leading-[11px] text-muted">{l}</span>
              ))}
            </div>
            <div className="flex gap-[3px]">
              {grid.weeks.map((col, wi) => (
                <div key={wi} className="flex flex-col gap-[3px]">
                  {col.map((c) => (
                    <div
                      key={c.iso}
                      className={`h-[11px] w-[11px] rounded-[2px] ${CELL[level(c.t)]}`}
                      onMouseEnter={(e) => {
                        const r = (e.target as HTMLElement).getBoundingClientRect();
                        setTip({ x: r.left + 6, y: r.top - 8, text: `${fmtN(c.t)} tokens em ${c.date.toLocaleDateString("pt-BR")}` });
                      }}
                      onMouseLeave={() => setTip(null)}
                    />
                  ))}
                </div>
              ))}
            </div>
          </div>
          <div className="mt-2 flex items-center gap-1 pl-7 text-[10px] text-muted">
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
    <div className="rounded-2xl border border-border bg-surface p-5">
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

export default function AnalyticsView() {
  const [data, setData] = useState<Overview | null>(null);
  const [loading, setLoading] = useState(true);
  const [range, setRange] = useState<RangeKey>("7d");
  const [metric, setMetric] = useState<Metric>("tokens");
  const [busy, setBusy] = useState(false);

  function loadRange(r: RangeKey, initial = false) {
    const off = new Date().getTimezoneOffset();
    if (!initial) setBusy(true);
    api
      .get<Overview>(`/analytics/overview?range=${r}&tz_offset=${off}`)
      .then(setData)
      .catch(() => { if (initial) setData(null); })
      .finally(() => { setLoading(false); setBusy(false); });
  }

  useEffect(() => { loadRange("7d", true); }, []);

  function changeRange(r: RangeKey) {
    if (r === range) return;
    setRange(r);
    loadRange(r);
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
      <div className="grid gap-4 lg:grid-cols-5">
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

  return (
    <div className="space-y-4">
      <div className="grid gap-4 lg:grid-cols-3">
        <div className="lg:col-span-2">
          <Summary data={data} range={range} metric={metric} busy={busy} onRange={changeRange} onMetric={setMetric} />
        </div>
        <div className="space-y-4">
          <TopModels data={data} metric={metric} />
          <CreditsCard credits={data.credits} />
        </div>
      </div>

      <ActivityCard act={data.activity} />

      <div className="grid grid-cols-2 gap-4 md:grid-cols-3 lg:grid-cols-6">
        <Stat icon={<MessageSquare size={15} />} label="Conversas" value={fmtN(data.totals.chats)} />
        <Stat icon={<Sparkles size={15} />} label="Respostas" value={fmtN(data.totals.messages)} />
        <Stat icon={<Zap size={15} />} label="Tokens" value={fmtC(data.totals.tokens)} sub={`${fmtN(data.totals.avg_tokens)}/msg`} />
        <Stat icon={<DollarSign size={15} />} label="Custo" value={fmtUSD(data.totals.cost)} />
        <Stat icon={<Cpu size={15} />} label="Raciocínio" value={fmtC(data.totals.reasoning_tokens)} sub="tokens" />
        <Stat icon={<BarChart3 size={15} />} label="Entrada/Saída" value={`${fmtC(data.totals.prompt_tokens)}/${fmtC(data.totals.completion_tokens)}`} sub="tokens" />
      </div>
    </div>
  );
}
