"use client";

import { useMemo, useState } from "react";
import { ArrowDownRight, ArrowUpRight, ExternalLink, TrendingUp } from "lucide-react";
import { api } from "@/lib/api";
import type { StockQuote } from "@/lib/types";

// verde/vermelho semânticos (sobem c/ seta+sinal, não cor sozinha) — legíveis no escuro
const UP = "#16c784";
const DOWN = "#f0616d";

const RANGES: { k: string; l: string }[] = [
  { k: "1d", l: "1D" }, { k: "5d", l: "5D" }, { k: "1mo", l: "1M" }, { k: "6mo", l: "6M" },
  { k: "ytd", l: "YTD" }, { k: "1y", l: "1A" }, { k: "5y", l: "5A" }, { k: "max", l: "Máx" },
];

const STAT_LABELS: Record<string, string> = {
  open: "Abertura", high: "Alta", low: "Baixa",
  high_52w: "Máx. 52 sem", low_52w: "Mín. 52 sem",
  market_cap: "Cap. merc.", pe: "Índice P/L", dividend_yield: "Dividendo", volume: "Volume",
};
const STAT_ORDER = ["open", "high", "low", "market_cap", "pe", "dividend_yield", "high_52w", "low_52w", "volume"];

function fmtPrice(v: number): string {
  return v.toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: Math.abs(v) < 1 ? 4 : 2 });
}
function fmtCompact(n: number): string {
  const a = Math.abs(n);
  const f = (x: number, s: string) => x.toLocaleString("pt-BR", { maximumFractionDigits: 2 }) + " " + s;
  if (a >= 1e12) return f(n / 1e12, "tri");
  if (a >= 1e9) return f(n / 1e9, "bi");
  if (a >= 1e6) return f(n / 1e6, "mi");
  if (a >= 1e3) return f(n / 1e3, "mil");
  return n.toLocaleString("pt-BR", { maximumFractionDigits: 2 });
}
function fmtStat(key: string, v: number): string {
  if (key === "market_cap" || key === "volume") return fmtCompact(v);
  if (key === "dividend_yield") return (v <= 1 ? v * 100 : v).toFixed(2).replace(".", ",") + "%";
  if (key === "pe") return v.toFixed(2).replace(".", ",");
  return fmtPrice(v);
}
function fmtTime(t: number, range: string): string {
  const d = new Date(t * 1000);
  if (range === "1d" || range === "5d")
    return d.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
  return d.toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit", year: range === "5y" || range === "max" ? "2-digit" : undefined });
}

/** Card de cotação estilo Google (finance.quote.get / rota /finance/quote).
 *  As abas de período re-buscam a série no backend com as prefs do usuário. */
export default function StockCard({ quote }: { quote: StockQuote }) {
  const [data, setData] = useState<StockQuote>(quote);
  const [range, setRange] = useState<string>(quote.range || "1d");
  const [loading, setLoading] = useState(false);
  const [hover, setHover] = useState<number | null>(null);

  async function pick(r: string) {
    if (r === range || loading) return;
    setRange(r); setLoading(true); setHover(null);
    try {
      const q = await api.get<StockQuote>(`/finance/quote?symbol=${encodeURIComponent(data.symbol)}&range=${r}`);
      setData(q);
    } catch { /* mantém o anterior */ } finally { setLoading(false); }
  }

  const series = data.series ?? [];
  const vals = series.map((p) => p.v);

  // variação exibida: no 1d vs. fechamento anterior; nos demais, início→fim da série
  const disp = useMemo(() => {
    if (range === "1d" && typeof data.change === "number" && typeof data.change_pct === "number")
      return { abs: data.change, pct: data.change_pct };
    if (vals.length >= 2) {
      const a = vals[0], b = vals[vals.length - 1];
      return { abs: b - a, pct: a ? ((b - a) / a) * 100 : 0 };
    }
    return { abs: data.change ?? 0, pct: data.change_pct ?? 0 };
  }, [range, data, vals]);

  const up = disp.abs >= 0;
  const color = up ? UP : DOWN;

  // geometria do mini-gráfico
  const W = 640, H = 180, PAD = 10;
  const baseline = range === "1d" ? data.prev_close : undefined;
  const chart = useMemo(() => {
    if (vals.length < 2) return null;
    const pool = baseline != null ? [...vals, baseline] : vals;
    let min = Math.min(...pool), max = Math.max(...pool);
    if (min === max) { min -= 1; max += 1; }
    const x = (i: number) => (i / (vals.length - 1)) * W;
    const y = (v: number) => PAD + (1 - (v - min) / (max - min)) * (H - 2 * PAD);
    const line = vals.map((v, i) => `${i ? "L" : "M"} ${x(i).toFixed(1)} ${y(v).toFixed(1)}`).join(" ");
    const area = `${line} L ${W} ${H} L 0 ${H} Z`;
    const yFrac = (v: number) => (y(v) / H);
    return { min, max, line, area, x, y, yFrac, baseY: baseline != null ? y(baseline) : null };
  }, [vals, baseline]);

  const isWeb = data.source === "web" || (!vals.length && (data.web_results?.length ?? 0) > 0);

  const stats = STAT_ORDER.filter((k) => typeof data.stats?.[k] === "number");

  return (
    <div className="my-2 overflow-hidden rounded-2xl border border-border bg-surface">
      {/* cabeçalho */}
      <div className="flex items-start gap-2 px-4 pt-3.5">
        <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-surface2 text-accent-hover">
          <TrendingUp size={17} />
        </span>
        <div className="min-w-0 flex-1">
          <p className="truncate text-[15px] font-semibold text-ink">{data.name || data.symbol}</p>
          <p className="truncate text-xs text-muted">
            {data.exchange ? `${data.exchange} · ` : ""}{data.symbol}
          </p>
        </div>
        <span className="shrink-0 rounded-full bg-surface2 px-2 py-0.5 text-[10px] text-muted">{data.source || "—"}</span>
      </div>

      {/* preço + variação */}
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 px-4 pt-2">
        {typeof data.price === "number" ? (
          <>
            <span className="text-3xl font-semibold tracking-tight text-ink">{fmtPrice(data.price)}</span>
            {data.currency && <span className="text-sm text-muted">{data.currency}</span>}
            <span className="flex items-center gap-1 text-sm font-medium" style={{ color }}>
              {up ? <ArrowUpRight size={15} /> : <ArrowDownRight size={15} />}
              {up ? "+" : ""}{fmtPrice(disp.abs)} ({up ? "+" : ""}{disp.pct.toFixed(2).replace(".", ",")}%)
            </span>
          </>
        ) : (
          <span className="text-sm text-muted">Sem preço estruturado — veja as fontes abaixo.</span>
        )}
      </div>

      {/* fonte web: sem gráfico, mostra os resultados */}
      {isWeb ? (
        <div className="space-y-1.5 px-4 py-3">
          {(data.web_results ?? []).map((r, i) => (
            <a key={i} href={r.url} target="_blank" rel="noreferrer"
              className="block rounded-lg border border-border bg-bg px-3 py-2 transition-colors hover:bg-hover">
              <p className="flex items-center gap-1 truncate text-xs font-medium text-ink"><ExternalLink size={11} className="shrink-0 text-muted" /> {r.title}</p>
              <p className="mt-0.5 line-clamp-2 text-[11px] text-muted">{r.snippet}</p>
            </a>
          ))}
        </div>
      ) : (
        <>
          {/* abas de período */}
          <div className="flex items-center gap-1 px-3 pt-3">
            {RANGES.map((r) => (
              <button key={r.k} onClick={() => pick(r.k)}
                className={`rounded-md px-2 py-1 text-xs transition-colors ${range === r.k ? "bg-accent/15 font-medium text-accent-hover" : "text-muted hover:bg-hover hover:text-ink"}`}>
                {r.l}
              </button>
            ))}
            {loading && <span className="ml-1 text-[11px] text-muted">…</span>}
          </div>

          {/* mini-gráfico */}
          <div className="relative px-2 pt-1" style={{ height: H }}>
            {chart ? (
              <>
                <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" width="100%" height={H} className="block">
                  <defs>
                    <linearGradient id={`fill-${data.symbol}`} x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor={color} stopOpacity="0.22" />
                      <stop offset="100%" stopColor={color} stopOpacity="0" />
                    </linearGradient>
                  </defs>
                  {chart.baseY != null && (
                    <line x1="0" y1={chart.baseY} x2={W} y2={chart.baseY} stroke="rgb(var(--c-muted))"
                      strokeWidth={1} strokeDasharray="4 4" opacity={0.5} vectorEffect="non-scaling-stroke" />
                  )}
                  <path d={chart.area} fill={`url(#fill-${data.symbol})`} />
                  <path d={chart.line} fill="none" stroke={color} strokeWidth={2}
                    strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
                </svg>

                {/* overlay de hover (crosshair + ponto + tooltip) */}
                <div
                  className="absolute inset-0 cursor-crosshair"
                  onMouseLeave={() => setHover(null)}
                  onMouseMove={(e) => {
                    const rect = e.currentTarget.getBoundingClientRect();
                    const fx = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width));
                    setHover(Math.round(fx * (vals.length - 1)));
                  }}
                >
                  {hover != null && series[hover] && (
                    <>
                      <div className="absolute top-0 bottom-0 w-px bg-border" style={{ left: `${(hover / (vals.length - 1)) * 100}%` }} />
                      <div className="absolute h-2.5 w-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-bg"
                        style={{ left: `${(hover / (vals.length - 1)) * 100}%`, top: `${chart.yFrac(series[hover].v) * 100}%`, background: color }} />
                      <div className="pointer-events-none absolute -translate-x-1/2 rounded-lg border border-border bg-bg px-2 py-1 text-center shadow-menu"
                        style={{ left: `${Math.min(88, Math.max(12, (hover / (vals.length - 1)) * 100))}%`, top: 2 }}>
                        <p className="text-xs font-semibold text-ink">{fmtPrice(series[hover].v)} <span className="font-normal text-muted">{data.currency}</span></p>
                        <p className="text-[10px] text-muted">{fmtTime(series[hover].t, range)}</p>
                      </div>
                    </>
                  )}
                </div>
              </>
            ) : (
              <div className="flex h-full items-center justify-center text-xs text-muted">
                {loading ? "Carregando…" : "Sem série para este período."}
              </div>
            )}
          </div>

          {/* stats */}
          {stats.length > 0 && (
            <div className="grid grid-cols-3 gap-x-4 gap-y-2 border-t border-border px-4 py-3">
              {stats.map((k) => (
                <div key={k} className="flex items-center justify-between gap-2 text-xs">
                  <span className="truncate text-muted">{STAT_LABELS[k] ?? k}</span>
                  <span className="shrink-0 font-medium text-ink tabular-nums">{fmtStat(k, data.stats![k])}</span>
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}
