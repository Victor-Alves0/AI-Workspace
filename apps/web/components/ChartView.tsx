"use client";

import { useMemo, useState } from "react";
import type { ChartSpec } from "@/lib/types";

// paleta categórica validada (coluna dark) — atribuída em ordem fixa, nunca ciclada
const CAT = ["#3987e5", "#199e70", "#c98500", "#008300", "#9085e9", "#e66767", "#d55181", "#d95926"];

const W = 640, H = 300, PL = 46, PR = 14, PT = 16, PB = 26;
const plotW = W - PL - PR, plotH = H - PT - PB;

function fmt(n: number): string {
  const a = Math.abs(n);
  if (a >= 1e9) return (n / 1e9).toLocaleString("pt-BR", { maximumFractionDigits: 1 }) + "B";
  if (a >= 1e6) return (n / 1e6).toLocaleString("pt-BR", { maximumFractionDigits: 1 }) + "M";
  if (a >= 1e3) return (n / 1e3).toLocaleString("pt-BR", { maximumFractionDigits: 1 }) + "k";
  return n.toLocaleString("pt-BR", { maximumFractionDigits: 2 });
}

/** Gráfico genérico (tool chart.render.plot). SVG inline, leve, tema escuro. */
export default function ChartView({ spec }: { spec: ChartSpec }) {
  const type = spec.type || "line";
  const series = (spec.series || []).filter((s) => s.data?.length);
  const labels = spec.labels || [];
  const [hover, setHover] = useState<number | null>(null);

  const n = Math.max(0, ...series.map((s) => s.data.length));
  const multi = series.length > 1;

  if (!series.length) return null;
  if (type === "pie") return <Pie spec={spec} />;

  const baseline0 = type === "bar" || type === "area";
  const all = series.flatMap((s) => s.data);
  let dMin = Math.min(...all), dMax = Math.max(...all);
  if (baseline0) dMin = Math.min(0, dMin);
  dMax = Math.max(dMax, dMin + 1e-9);
  if (dMin === dMax) { dMin -= 1; dMax += 1; }

  const yOf = (v: number) => PT + (1 - (v - dMin) / (dMax - dMin)) * plotH;
  const xLine = (i: number) => PL + (n <= 1 ? plotW / 2 : (i / (n - 1)) * plotW);
  const bandW = plotW / Math.max(1, n);

  // ticks Y (4 linhas)
  const yTicks = [0, 0.25, 0.5, 0.75, 1].map((f) => dMin + f * (dMax - dMin));
  // rótulos X: mostra no máx ~7 p/ não poluir
  const xEvery = Math.max(1, Math.ceil(n / 7));

  const label = (i: number) => labels[i] ?? String(i + 1);

  return (
    <div className="my-2 overflow-hidden rounded-xl border border-border bg-surface p-3">
      {spec.title && <p className="mb-1 px-1 text-sm font-medium text-ink">{spec.title}</p>}
      {multi && (
        <div className="mb-1 flex flex-wrap gap-x-3 gap-y-1 px-1">
          {series.map((s, i) => (
            <span key={i} className="flex items-center gap-1.5 text-[11px] text-muted">
              <span className="h-2 w-2 rounded-full" style={{ background: CAT[i % CAT.length] }} /> {s.name || `Série ${i + 1}`}
            </span>
          ))}
        </div>
      )}
      <div className="relative">
        <svg viewBox={`0 0 ${W} ${H}`} width="100%" className="block" style={{ height: "auto" }}>
          {/* grade + rótulos Y */}
          {yTicks.map((v, i) => (
            <g key={i}>
              <line x1={PL} y1={yOf(v)} x2={W - PR} y2={yOf(v)} stroke="rgb(var(--c-border))" strokeWidth={1} opacity={0.6} />
              <text x={PL - 6} y={yOf(v) + 3} textAnchor="end" fontSize={10} fill="rgb(var(--c-muted))">{fmt(v)}</text>
            </g>
          ))}
          {/* rótulos X */}
          {Array.from({ length: n }).map((_, i) =>
            i % xEvery === 0 ? (
              <text key={i} x={type === "bar" ? PL + i * bandW + bandW / 2 : xLine(i)} y={H - 8}
                textAnchor="middle" fontSize={10} fill="rgb(var(--c-muted))">{label(i).slice(0, 8)}</text>
            ) : null,
          )}

          {/* marcas */}
          {type === "bar"
            ? series.map((s, si) =>
                s.data.map((v, i) => {
                  const gw = (bandW * 0.7) / series.length;
                  const gx = PL + i * bandW + bandW * 0.15 + si * gw;
                  const y0 = yOf(Math.max(0, v)), y1 = yOf(Math.min(0, v));
                  return <rect key={`${si}-${i}`} x={gx} y={y0} width={Math.max(1, gw - 2)} height={Math.max(0, y1 - y0)} rx={2} fill={CAT[si % CAT.length]} opacity={hover == null || hover === i ? 1 : 0.4} />;
                }),
              )
            : series.map((s, si) => {
                const pts = s.data.map((v, i) => `${xLine(i).toFixed(1)} ${yOf(v).toFixed(1)}`);
                const line = "M " + pts.join(" L ");
                const col = CAT[si % CAT.length];
                return (
                  <g key={si}>
                    {type === "area" && (
                      <path d={`${line} L ${xLine(s.data.length - 1)} ${yOf(dMin < 0 ? 0 : dMin)} L ${xLine(0)} ${yOf(dMin < 0 ? 0 : dMin)} Z`} fill={col} opacity={0.14} />
                    )}
                    <path d={line} fill="none" stroke={col} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
                    {hover != null && s.data[hover] != null && (
                      <circle cx={xLine(hover)} cy={yOf(s.data[hover])} r={3.5} fill={col} stroke="rgb(var(--c-bg))" strokeWidth={2} />
                    )}
                  </g>
                );
              })}
        </svg>

        {/* overlay de hover */}
        <div className="absolute inset-0" onMouseLeave={() => setHover(null)}
          onMouseMove={(e) => {
            const rect = e.currentTarget.getBoundingClientRect();
            const vx = ((e.clientX - rect.left) / rect.width) * W;
            const i = type === "bar"
              ? Math.floor((vx - PL) / bandW)
              : Math.round(((vx - PL) / plotW) * (n - 1));
            setHover(i >= 0 && i < n ? i : null);
          }}
        >
          {hover != null && (
            <div className="pointer-events-none absolute -translate-x-1/2 rounded-lg border border-border bg-bg px-2 py-1 shadow-menu"
              style={{ left: `${(((type === "bar" ? PL + hover * bandW + bandW / 2 : xLine(hover))) / W) * 100}%`, top: 4 }}>
              <p className="mb-0.5 text-[10px] font-medium text-muted">{label(hover)}</p>
              {series.map((s, i) => (
                <p key={i} className="flex items-center gap-1.5 text-[11px] text-ink">
                  <span className="h-2 w-2 rounded-full" style={{ background: CAT[i % CAT.length] }} />
                  <span className="tabular-nums">{s.data[hover] != null ? fmt(s.data[hover]) : "—"}</span>
                </p>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

/** Pizza/rosca a partir de series[0] (labels = fatias). */
function Pie({ spec }: { spec: ChartSpec }) {
  const data = spec.series?.[0]?.data ?? [];
  const labels = spec.labels ?? [];
  const [hover, setHover] = useState<number | null>(null);
  const total = data.reduce((a, b) => a + Math.max(0, b), 0);
  const slices = useMemo(() => {
    let acc = 0;
    return data.map((v, i) => {
      const frac = total ? Math.max(0, v) / total : 0;
      const s = acc, e = acc + frac; acc = e;
      return { i, v, frac, s, e };
    });
  }, [data, total]);
  if (!total) return null;
  const R = 90, r = 52, cx = 110, cy = 110;
  const arc = (s: number, e: number, rad: number) => {
    const a0 = s * 2 * Math.PI - Math.PI / 2, a1 = e * 2 * Math.PI - Math.PI / 2;
    return [cx + rad * Math.cos(a0), cy + rad * Math.sin(a0), cx + rad * Math.cos(a1), cy + rad * Math.sin(a1)];
  };
  return (
    <div className="my-2 overflow-hidden rounded-xl border border-border bg-surface p-3">
      {spec.title && <p className="mb-1 px-1 text-sm font-medium text-ink">{spec.title}</p>}
      <div className="flex flex-wrap items-center gap-4">
        <svg viewBox="0 0 220 220" width="180" height="180" className="shrink-0">
          {slices.map((sl) => {
            const large = sl.e - sl.s > 0.5 ? 1 : 0;
            const [x0, y0, x1, y1] = arc(sl.s, sl.e, R);
            const [ix0, iy0, ix1, iy1] = arc(sl.s, sl.e, r);
            const d = `M ${x0} ${y0} A ${R} ${R} 0 ${large} 1 ${x1} ${y1} L ${ix1} ${iy1} A ${r} ${r} 0 ${large} 0 ${ix0} ${iy0} Z`;
            return <path key={sl.i} d={d} fill={CAT[sl.i % CAT.length]} opacity={hover == null || hover === sl.i ? 1 : 0.4}
              onMouseEnter={() => setHover(sl.i)} onMouseLeave={() => setHover(null)} />;
          })}
        </svg>
        <div className="min-w-0 flex-1 space-y-1">
          {slices.map((sl) => (
            <div key={sl.i} className="flex items-center gap-2 text-xs" onMouseEnter={() => setHover(sl.i)} onMouseLeave={() => setHover(null)}>
              <span className="h-2.5 w-2.5 shrink-0 rounded-sm" style={{ background: CAT[sl.i % CAT.length] }} />
              <span className="min-w-0 flex-1 truncate text-ink">{labels[sl.i] ?? `Item ${sl.i + 1}`}</span>
              <span className="shrink-0 text-muted tabular-nums">{fmt(sl.v)} · {(sl.frac * 100).toFixed(1)}%</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
