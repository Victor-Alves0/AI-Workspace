"use client";

import { useEffect, useMemo, useState } from "react";
import { BarChart3, Coins, Cpu, DollarSign, MessageSquare, Sparkles, Zap } from "lucide-react";
import { api } from "@/lib/api";

type ByModel = { id: string; model: string; messages: number; tokens: number; cost: number; pct: number };
type PerDay = { date: string; label?: string; tokens: number; cost: number; messages: number };
type Credits = { total: number; usage: number; remaining: number };
type RangeKey = "7d" | "30d" | "6m" | "1y" | "all";
type Overview = {
  by_model: ByModel[];
  per_day: PerDay[];
  range?: RangeKey;
  granularity?: "day" | "week" | "month";
  credits: Credits | null;
  totals: {
    chats: number; messages: number; tokens: number; prompt_tokens: number;
    completion_tokens: number; reasoning_tokens: number; cost: number; avg_tokens: number;
  };
};

const COLORS = ["#6366f1", "#22c55e", "#f59e0b", "#ec4899", "#06b6d4", "#a855f7", "#ef4444", "#84cc16", "#14b8a6", "#f97316"];
const WD = ["Dom", "Seg", "Ter", "Qua", "Qui", "Sex", "Sáb"];

const nf = new Intl.NumberFormat("pt-BR");
const fmtN = (n: number) => nf.format(Math.round(n));
const fmtUSD = (n: number) => `$${n.toLocaleString("pt-BR", { minimumFractionDigits: n < 1 ? 4 : 2, maximumFractionDigits: n < 1 ? 4 : 2 })}`;
const fmtTokens = (n: number) => (n >= 1000 ? `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}k` : `${n}`);

/* ------------------------------ donut (SVG) ------------------------------ */
function Donut({ data }: { data: ByModel[] }) {
  const [hover, setHover] = useState<number | null>(null);
  const R = 62;
  const C = 2 * Math.PI * R;
  let acc = 0;
  const segs = data.map((d, i) => {
    const len = (d.pct / 100) * C;
    const seg = { len, offset: acc, color: COLORS[i % COLORS.length] };
    acc += len;
    return seg;
  });
  const totalMsgs = data.reduce((s, d) => s + d.messages, 0);
  const active = hover !== null ? data[hover] : null;

  return (
    <div className="flex h-full flex-col items-center justify-center gap-6 sm:flex-row sm:gap-8">
      <div className="relative aspect-square w-full max-w-[280px] shrink-0 sm:h-full sm:w-auto">
        <svg viewBox="0 0 180 180" className="h-full w-full -rotate-90">
          <circle cx="90" cy="90" r={R} fill="none" stroke="currentColor" strokeWidth="24" className="text-surface2" />
          {segs.map((s, i) => (
            <circle
              key={i}
              cx="90"
              cy="90"
              r={R}
              fill="none"
              stroke={s.color}
              strokeWidth={hover === i ? 28 : 24}
              strokeDasharray={`${s.len} ${C - s.len}`}
              strokeDashoffset={-s.offset}
              className="cursor-pointer transition-[stroke-width] duration-150"
              style={{ opacity: hover === null || hover === i ? 1 : 0.35 }}
              onMouseEnter={() => setHover(i)}
              onMouseLeave={() => setHover(null)}
            />
          ))}
        </svg>
        <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center text-center">
          {active ? (
            <>
              <span className="max-w-[70%] truncate text-sm font-medium text-ink">{active.model}</span>
              <span className="text-3xl font-bold text-ink">{active.pct}%</span>
              <span className="text-xs text-muted">{fmtN(active.messages)} msgs</span>
              <span className="mt-0.5 text-xs tabular-nums text-ink-soft">{fmtTokens(active.tokens)} tok · {fmtUSD(active.cost)}</span>
            </>
          ) : (
            <>
              <span className="text-3xl font-bold text-ink">{fmtN(totalMsgs)}</span>
              <span className="text-xs text-muted">mensagens</span>
            </>
          )}
        </div>
      </div>

      {/* legenda — nome, %, tokens e custo por modelo */}
      <div className="min-w-0 flex-1 space-y-1.5">
        {data.slice(0, 8).map((d, i) => (
          <button
            key={d.id}
            onMouseEnter={() => setHover(i)}
            onMouseLeave={() => setHover(null)}
            className={`flex w-full items-center gap-2.5 rounded-lg px-2 py-1 text-left transition-colors ${hover === i ? "bg-hover" : ""}`}
          >
            <span className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: COLORS[i % COLORS.length] }} />
            <span className="min-w-0 flex-1 truncate text-sm text-ink-soft">{d.model}</span>
            <span className="flex shrink-0 items-center gap-2.5 text-xs tabular-nums">
              <span className="w-9 text-right font-medium text-ink-soft">{d.pct}%</span>
              <span className="hidden w-12 text-right text-muted sm:inline">{fmtTokens(d.tokens)}</span>
              <span className="w-14 text-right text-muted">{fmtUSD(d.cost)}</span>
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}

/* ------------------------------- gráfico de uso ------------------------------- */
const RANGES: { key: RangeKey; label: string }[] = [
  { key: "7d", label: "7 dias" },
  { key: "30d", label: "1 mês" },
  { key: "6m", label: "6 meses" },
  { key: "1y", label: "1 ano" },
  { key: "all", label: "Tudo" },
];

function DayBars({ data, range, onRange, busy }: { data: PerDay[]; range: RangeKey; onRange: (r: RangeKey) => void; busy: boolean }) {
  const [hover, setHover] = useState<number | null>(null);
  const max = Math.max(1, ...data.map((d) => d.tokens));
  const active = hover !== null ? data[hover] : null;
  const n = data.length;
  // muitas barras → mostra só alguns rótulos p/ não amontoar
  const step = n <= 12 ? 1 : Math.ceil(n / 8);
  const barLabel = (d: PerDay, i: number) => d.label ?? WD[new Date(d.date + "T12:00:00").getDay()];

  return (
    <div>
      <div className="mb-2 flex min-h-7 flex-wrap items-center justify-between gap-x-2 gap-y-1">
        <span className="text-xs font-medium text-muted">
          Uso{active ? "" : ` — ${RANGES.find((r) => r.key === range)?.label ?? ""}`}
        </span>
        {active ? (
          <span className="text-xs tabular-nums text-ink-soft">
            {active.label ? `${active.label} · ` : ""}{fmtTokens(active.tokens)} tok · {fmtUSD(active.cost)}
          </span>
        ) : (
          <div className="flex flex-wrap gap-1">
            {RANGES.map((r) => (
              <button
                key={r.key}
                onClick={() => onRange(r.key)}
                className={`rounded-md px-2 py-0.5 text-[11px] transition-colors ${
                  range === r.key ? "bg-accent text-white" : "text-muted hover:bg-hover hover:text-ink-soft"
                }`}
              >
                {r.label}
              </button>
            ))}
          </div>
        )}
      </div>
      <div className={`flex items-end gap-1 transition-opacity ${busy ? "opacity-50" : ""}`} style={{ height: 90 }}>
        {data.map((d, i) => {
          const h = Math.max(3, (d.tokens / max) * 82);
          const show = i % step === 0 || i === n - 1;
          return (
            <div
              key={d.date}
              className="flex min-w-0 flex-1 flex-col items-center gap-1"
              title={`${barLabel(d, i)} · ${fmtTokens(d.tokens)} tok · ${fmtUSD(d.cost)}`}
              onMouseEnter={() => setHover(i)}
              onMouseLeave={() => setHover(null)}
            >
              <div className="flex w-full flex-1 items-end">
                <div
                  className={`w-full rounded-sm transition-colors ${hover === i ? "bg-accent" : "bg-accent/45"}`}
                  style={{ height: h }}
                />
              </div>
              <span className="h-3 truncate text-[10px] leading-3 text-muted">{show ? barLabel(d, i) : ""}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* --------------------------------- métrica --------------------------------- */
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

export default function AnalyticsView() {
  const [data, setData] = useState<Overview | null>(null);
  const [loading, setLoading] = useState(true);
  const [range, setRange] = useState<RangeKey>("7d");
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

  const empty = useMemo(() => !!data && data.totals.messages === 0, [data]);

  if (loading) {
    return <div className="flex items-center justify-center py-24 text-sm text-muted">Carregando…</div>;
  }
  if (!data) {
    return <div className="flex items-center justify-center py-24 text-sm text-muted">Não foi possível carregar a analítica.</div>;
  }

  // sem respostas ainda: ainda assim mostra os Créditos (saldo do OpenRouter não
  // depende de uso no app) ao lado de um aviso de "sem dados" para os gráficos.
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
      <div className="grid gap-4 lg:grid-cols-5">
        {/* modelos mais usados */}
        <div className="flex flex-col rounded-2xl border border-border bg-surface p-5 lg:col-span-3">
          <div className="mb-4 flex items-center gap-2">
            <Cpu size={16} className="text-accent-hover" />
            <h2 className="text-sm font-semibold text-ink">Modelos mais usados</h2>
          </div>
          <div className="min-h-[220px] flex-1">
            <Donut data={data.by_model} />
          </div>
        </div>

        {/* coluna direita: créditos + uso por dia */}
        <div className="space-y-4 lg:col-span-2">
          <CreditsCard credits={data.credits} />
          <div className="rounded-2xl border border-border bg-surface p-5">
            <DayBars data={data.per_day} range={range} onRange={changeRange} busy={busy} />
          </div>
        </div>
      </div>

      {/* demais métricas */}
      <div className="grid grid-cols-2 gap-4 md:grid-cols-3 lg:grid-cols-6">
        <Stat icon={<MessageSquare size={15} />} label="Conversas" value={fmtN(data.totals.chats)} />
        <Stat icon={<Sparkles size={15} />} label="Respostas" value={fmtN(data.totals.messages)} />
        <Stat icon={<Zap size={15} />} label="Tokens" value={fmtTokens(data.totals.tokens)} sub={`${fmtN(data.totals.avg_tokens)}/msg`} />
        <Stat icon={<DollarSign size={15} />} label="Custo total" value={fmtUSD(data.totals.cost)} />
        <Stat icon={<Cpu size={15} />} label="Raciocínio" value={fmtTokens(data.totals.reasoning_tokens)} sub="tokens" />
        <Stat icon={<BarChart3 size={15} />} label="Entrada/Saída" value={`${fmtTokens(data.totals.prompt_tokens)}/${fmtTokens(data.totals.completion_tokens)}`} sub="tokens" />
      </div>
    </div>
  );
}
