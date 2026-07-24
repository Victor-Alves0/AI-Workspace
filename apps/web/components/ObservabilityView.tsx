"use client";

/**
 * Painel de Observabilidade (admin): consulta o rastro fim-a-fim.
 *
 * Três visões: um resumo (latência p50/p95/p99, tempo de banco, erro, rotas mais
 * pesadas), a lista de traces filtrável, e o detalhe de um trace como waterfall —
 * cada span posicionado pelo seu offset, com duração, leituras/escritas no banco e
 * as queries (SQL normalizado, sem valores) daquela chamada.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Activity, AlertTriangle, ChevronRight, Clock, Database, Gauge, RefreshCw,
  Search, X, Zap,
} from "lucide-react";
import { api } from "@/lib/api";
import type { ObsConfig, ObsSpan, ObsSummary, ObsTrace } from "@/lib/types";

const KIND_COLOR: Record<string, string> = {
  http: "#6366f1", chat: "#8b5cf6", api: "#0ea5e9", automation: "#f59e0b",
  channel: "#10b981", worker: "#64748b", client: "#ec4899",
};
const SPAN_COLOR: Record<string, string> = {
  internal: "#94a3b8", db: "#0ea5e9", http: "#22c55e", llm: "#8b5cf6",
  tool: "#f59e0b", rag: "#14b8a6", memory: "#ec4899", auth: "#ef4444",
  client: "#ec4899",
};

function color(map: Record<string, string>, k: string): string {
  return map[k] ?? "#94a3b8";
}

function fmtMs(ms: number): string {
  if (ms >= 1000) return `${(ms / 1000).toFixed(2)}s`;
  return `${ms.toFixed(ms < 10 ? 1 : 0)}ms`;
}

function fmtTime(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "medium" });
}

const CARD = "rounded-xl border border-border bg-surface p-4";
const CHIP = "rounded-full px-2.5 py-0.5 text-[11px] font-medium";

/* --------------------------------------------------------------------------- */
/* Resumo                                                                      */
/* --------------------------------------------------------------------------- */

function Stat({ label, value, sub, icon }: {
  label: string; value: string; sub?: string; icon: React.ReactNode;
}) {
  return (
    <div className={CARD}>
      <div className="flex items-center gap-1.5 text-[11px] text-muted">{icon}{label}</div>
      <p className="mt-1 text-xl font-semibold text-ink">{value}</p>
      {sub && <p className="text-[11px] text-muted">{sub}</p>}
    </div>
  );
}

function Sparkline({ series }: { series: ObsSummary["series"] }) {
  if (series.length < 2) return null;
  const w = 100, h = 28;
  const max = Math.max(...series.map((s) => s.count), 1);
  const pts = series.map((s, i) => {
    const x = (i / (series.length - 1)) * w;
    const y = h - (s.count / max) * h;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
  return (
    <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" className="h-8 w-full">
      <polyline points={pts} fill="none" stroke="var(--accent)" strokeWidth="1.5"
        vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

function SummaryPanel({ summary }: { summary: ObsSummary }) {
  const t = summary.totals;
  const errRate = t.traces ? ((t.errors / t.traces) * 100).toFixed(1) : "0";
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Stat label="Chamadas" value={String(t.traces)} sub={`${t.errors} com erro`}
          icon={<Activity size={13} />} />
        <Stat label="Latência p50 / p95" value={fmtMs(t.p50_ms)} sub={`p95 ${fmtMs(t.p95_ms)} · p99 ${fmtMs(t.p99_ms)}`}
          icon={<Gauge size={13} />} />
        <Stat label="Banco (médio)" value={fmtMs(t.avg_db_ms)} sub={`${t.db_queries} queries no período`}
          icon={<Database size={13} />} />
        <Stat label="Taxa de erro" value={`${errRate}%`} sub={`IA: ${fmtMs(t.llm_ms)} no total`}
          icon={<AlertTriangle size={13} />} />
      </div>

      <div className={CARD}>
        <p className="mb-1 text-xs font-medium text-ink-soft">Volume por hora</p>
        <Sparkline series={summary.series} />
      </div>

      <div>
        <p className="mb-2 text-xs font-medium text-ink-soft">Rotas mais pesadas (p95)</p>
        <div className="overflow-x-auto rounded-xl border border-border">
          <table className="w-full text-left text-xs">
            <thead className="bg-surface2 text-muted">
              <tr>
                <th className="px-3 py-2 font-medium">Rota</th>
                <th className="px-3 py-2 font-medium">Chamadas</th>
                <th className="px-3 py-2 font-medium">p95</th>
                <th className="px-3 py-2 font-medium">Média</th>
                <th className="px-3 py-2 font-medium">Banco</th>
                <th className="px-3 py-2 font-medium">Queries</th>
                <th className="px-3 py-2 font-medium">Erros</th>
              </tr>
            </thead>
            <tbody>
              {summary.routes.map((r, i) => (
                <tr key={i} className="border-t border-border">
                  <td className="px-3 py-2">
                    <span className={CHIP} style={{ background: `${color(KIND_COLOR, r.kind)}22`, color: color(KIND_COLOR, r.kind) }}>
                      {r.method || r.kind}
                    </span>
                    <span className="ml-2 font-mono text-ink">{r.path}</span>
                  </td>
                  <td className="px-3 py-2 text-ink-soft">{r.count}</td>
                  <td className="px-3 py-2 text-ink-soft">{fmtMs(r.p95_ms)}</td>
                  <td className="px-3 py-2 text-muted">{fmtMs(r.avg_ms)}</td>
                  <td className="px-3 py-2 text-muted">{fmtMs(r.avg_db_ms)}</td>
                  <td className="px-3 py-2 text-muted">{r.avg_queries}</td>
                  <td className="px-3 py-2">
                    {r.errors > 0 ? <span className="text-red-500">{r.errors}</span> : <span className="text-muted">0</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

/* --------------------------------------------------------------------------- */
/* Waterfall de um trace                                                       */
/* --------------------------------------------------------------------------- */

function QueryList({ queries }: { queries: { sql: string; ms: number; rows: number | null }[] }) {
  return (
    <div className="mt-2 space-y-1">
      {queries.map((q, i) => (
        <div key={i} className="flex items-start gap-2 rounded-lg bg-surface2 px-2 py-1 font-mono text-[11px]">
          <span className="shrink-0 text-muted">{fmtMs(q.ms)}</span>
          <span className="min-w-0 flex-1 break-all text-ink-soft">{q.sql}</span>
          {q.rows != null && <span className="shrink-0 text-muted">{q.rows} linhas</span>}
        </div>
      ))}
    </div>
  );
}

function SpanRow({ span, total, depthOf }: {
  span: ObsSpan; total: number; depthOf: (s: ObsSpan) => number;
}) {
  const [open, setOpen] = useState(false);
  const left = total ? (span.offset_ms / total) * 100 : 0;
  const width = total ? Math.max((span.duration_ms / total) * 100, 0.5) : 0.5;
  const depth = depthOf(span);
  const queries = (span.attrs?.queries as { sql: string; ms: number; rows: number | null }[]) ?? [];
  const hasDetail = queries.length > 0 || Object.keys(span.attrs || {}).some((k) => k !== "queries");

  return (
    <div className="border-b border-border/60 last:border-0">
      <button
        onClick={() => hasDetail && setOpen((v) => !v)}
        className="flex w-full items-center gap-2 py-1.5 text-left hover:bg-hover"
      >
        <div className="flex min-w-0 shrink-0 items-center gap-1.5" style={{ width: 220, paddingLeft: depth * 12 }}>
          {hasDetail ? <ChevronRight size={12} className={`shrink-0 text-muted transition-transform ${open ? "rotate-90" : ""}`} />
            : <span className="w-3 shrink-0" />}
          <span className="h-2 w-2 shrink-0 rounded-sm" style={{ background: color(SPAN_COLOR, span.kind) }} />
          <span className={`truncate text-xs ${span.status === "error" ? "text-red-500" : "text-ink"}`}>{span.name}</span>
        </div>
        <div className="relative h-4 flex-1 rounded bg-surface2">
          <div className="absolute top-0 h-4 rounded" style={{
            left: `${left}%`, width: `${width}%`,
            background: span.status === "error" ? "#ef4444" : color(SPAN_COLOR, span.kind),
            opacity: 0.85,
          }} />
        </div>
        <span className="w-16 shrink-0 text-right font-mono text-[11px] text-ink-soft">{fmtMs(span.duration_ms)}</span>
        <span className="w-20 shrink-0 text-right text-[10px] text-muted">
          {(span.db_reads + span.db_writes) > 0 && `${span.db_reads}r/${span.db_writes}w`}
        </span>
      </button>
      {open && (
        <div className="px-4 pb-2 pl-8">
          {span.error && <p className="mb-1 text-[11px] text-red-500">{span.error}</p>}
          {Object.entries(span.attrs || {}).filter(([k]) => k !== "queries").length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {Object.entries(span.attrs || {}).filter(([k]) => k !== "queries").map(([k, v]) => (
                <span key={k} className="rounded bg-surface2 px-1.5 py-0.5 text-[10px] text-muted">
                  {k}: <span className="text-ink-soft">{String(v)}</span>
                </span>
              ))}
            </div>
          )}
          {queries.length > 0 && <QueryList queries={queries} />}
        </div>
      )}
    </div>
  );
}

function TraceDetail({ traceId, onClose }: { traceId: string; onClose: () => void }) {
  const [data, setData] = useState<{ trace: ObsTrace; spans: ObsSpan[] } | null>(null);

  useEffect(() => {
    let alive = true;
    api.get<{ trace: ObsTrace; spans: ObsSpan[] }>(`/observability/traces/${traceId}`)
      .then((d) => alive && setData(d)).catch(() => alive && setData(null));
    return () => { alive = false; };
  }, [traceId]);

  const depthOf = useCallback((spans: ObsSpan[]) => {
    const byId = new Map(spans.map((s) => [s.id, s]));
    const cache = new Map<string, number>();
    const d = (s: ObsSpan): number => {
      if (cache.has(s.id)) return cache.get(s.id)!;
      let depth = 0;
      let cur = s;
      while (cur.parent_id && byId.has(cur.parent_id)) { depth++; cur = byId.get(cur.parent_id)!; }
      cache.set(s.id, depth);
      return depth;
    };
    return d;
  }, []);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" onClick={onClose}>
      <div onClick={(e) => e.stopPropagation()}
        className="flex max-h-[88vh] w-full max-w-4xl flex-col overflow-hidden rounded-2xl border border-border bg-bg shadow-xl">
        <div className="flex items-center justify-between border-b border-border px-5 py-3.5">
          <div className="min-w-0">
            <h2 className="truncate text-base font-semibold text-ink">{data?.trace.name ?? "Trace"}</h2>
            {data && (
              <p className="text-xs text-muted">
                {fmtTime(data.trace.started_at)} · {fmtMs(data.trace.duration_ms)} ·
                {" "}{data.trace.db_queries} queries ({fmtMs(data.trace.db_ms)}) ·
                {data.trace.llm_ms > 0 && ` IA ${fmtMs(data.trace.llm_ms)} ·`}
                {" "}{data.trace.span_count} spans
              </p>
            )}
          </div>
          <button onClick={onClose} className="rounded-lg p-1 text-muted hover:bg-hover hover:text-ink"><X size={18} /></button>
        </div>
        <div className="flex-1 overflow-y-auto px-5 py-4">
          {!data ? <p className="py-8 text-center text-sm text-muted">Carregando…</p>
            : data.spans.length === 0 ? <p className="py-8 text-center text-sm text-muted">Sem spans (chamada sem etapas internas instrumentadas).</p>
            : (
              <div className="rounded-xl border border-border">
                {data.spans.map((s) => (
                  <SpanRow key={s.id} span={s} total={data.trace.duration_ms} depthOf={depthOf(data.spans)} />
                ))}
              </div>
            )}
          {data?.trace.error && (
            <div className="mt-3 rounded-lg border border-red-500/30 bg-red-500/10 p-3 text-xs text-red-500">{data.trace.error}</div>
          )}
        </div>
      </div>
    </div>
  );
}

/* --------------------------------------------------------------------------- */
/* Lista de traces                                                             */
/* --------------------------------------------------------------------------- */

function TraceList({ hours, onOpen }: { hours: number; onOpen: (id: string) => void }) {
  const [traces, setTraces] = useState<ObsTrace[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [kind, setKind] = useState("");
  const [statusF, setStatusF] = useState("");
  const [q, setQ] = useState("");
  const [minMs, setMinMs] = useState(0);

  const load = useCallback(async () => {
    setLoading(true);
    const params = new URLSearchParams({ hours: String(hours), limit: "150" });
    if (kind) params.set("kind", kind);
    if (statusF) params.set("status", statusF);
    if (q) params.set("q", q);
    if (minMs > 0) params.set("min_ms", String(minMs));
    try {
      const d = await api.get<{ total: number; traces: ObsTrace[] }>(`/observability/traces?${params}`);
      setTraces(d.traces); setTotal(d.total);
    } finally { setLoading(false); }
  }, [hours, kind, statusF, q, minMs]);

  useEffect(() => { void load(); }, [load]);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex items-center gap-1.5 rounded-lg border border-border bg-surface px-2.5 py-1.5">
          <Search size={14} className="text-muted" />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Buscar rota/nome…"
            className="w-40 bg-transparent text-xs text-ink outline-none placeholder:text-muted" />
        </div>
        <select value={kind} onChange={(e) => setKind(e.target.value)}
          className="rounded-lg border border-border bg-surface px-2 py-1.5 text-xs text-ink outline-none">
          <option value="">Todo tipo</option>
          {["http", "chat", "api", "automation", "channel", "client"].map((k) => <option key={k} value={k}>{k}</option>)}
        </select>
        <select value={statusF} onChange={(e) => setStatusF(e.target.value)}
          className="rounded-lg border border-border bg-surface px-2 py-1.5 text-xs text-ink outline-none">
          <option value="">Qualquer status</option>
          <option value="ok">OK</option>
          <option value="error">Erro</option>
        </select>
        <select value={minMs} onChange={(e) => setMinMs(Number(e.target.value))}
          className="rounded-lg border border-border bg-surface px-2 py-1.5 text-xs text-ink outline-none">
          <option value={0}>Qualquer duração</option>
          <option value={200}>≥ 200ms</option>
          <option value={1000}>≥ 1s</option>
          <option value={3000}>≥ 3s</option>
        </select>
        <button onClick={() => void load()} className="ml-auto flex items-center gap-1.5 rounded-lg border border-border px-2.5 py-1.5 text-xs text-ink-soft hover:bg-hover">
          <RefreshCw size={13} /> Atualizar
        </button>
      </div>

      <p className="text-[11px] text-muted">{total} chamadas no período (mostrando {traces.length})</p>

      {loading ? <p className="py-8 text-center text-sm text-muted">Carregando…</p>
        : traces.length === 0 ? <p className="py-8 text-center text-sm text-muted">Nenhuma chamada encontrada.</p>
        : (
          <div className="overflow-hidden rounded-xl border border-border">
            {traces.map((t) => (
              <button key={t.id} onClick={() => onOpen(t.id)}
                className="flex w-full items-center gap-2 border-b border-border/60 px-3 py-2 text-left last:border-0 hover:bg-hover">
                <span className={CHIP} style={{ background: `${color(KIND_COLOR, t.kind)}22`, color: color(KIND_COLOR, t.kind) }}>
                  {t.method || t.kind}
                </span>
                <span className="min-w-0 flex-1 truncate font-mono text-xs text-ink">{t.path || t.name}</span>
                {t.status === "error" && <span className="shrink-0 text-[10px] text-red-500">erro {t.status_code}</span>}
                {t.llm_ms > 0 && <span className="shrink-0 text-[10px] text-violet-400"><Zap size={10} className="inline" /> {fmtMs(t.llm_ms)}</span>}
                {t.db_queries > 0 && <span className="shrink-0 text-[10px] text-sky-400"><Database size={10} className="inline" /> {t.db_queries}</span>}
                <span className="w-14 shrink-0 text-right font-mono text-xs text-ink-soft">{fmtMs(t.duration_ms)}</span>
                <span className="hidden w-32 shrink-0 text-right text-[10px] text-muted sm:block">{fmtTime(t.started_at)}</span>
                <ChevronRight size={14} className="shrink-0 text-muted" />
              </button>
            ))}
          </div>
        )}
    </div>
  );
}

/* --------------------------------------------------------------------------- */
/* Painel                                                                      */
/* --------------------------------------------------------------------------- */

export default function ObservabilityView() {
  const [tab, setTab] = useState<"summary" | "traces">("summary");
  const [hours, setHours] = useState(24);
  const [summary, setSummary] = useState<ObsSummary | null>(null);
  const [config, setConfig] = useState<ObsConfig | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);

  const loadSummary = useCallback(async () => {
    const [s, c] = await Promise.all([
      api.get<ObsSummary>(`/observability/summary?hours=${hours}`),
      api.get<ObsConfig>("/observability/config"),
    ]);
    setSummary(s); setConfig(c);
  }, [hours]);

  useEffect(() => { if (tab === "summary") void loadSummary(); }, [tab, loadSummary]);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        {(["summary", "traces"] as const).map((t) => (
          <button key={t} onClick={() => setTab(t)}
            className={`rounded-full px-3.5 py-1.5 text-sm transition-colors ${tab === t ? "bg-accent text-white" : "border border-border text-ink-soft hover:bg-hover"}`}>
            {t === "summary" ? "Resumo" : "Chamadas"}
          </button>
        ))}
        <select value={hours} onChange={(e) => setHours(Number(e.target.value))}
          className="ml-auto rounded-lg border border-border bg-surface px-2 py-1.5 text-xs text-ink outline-none">
          <option value={1}>Última hora</option>
          <option value={24}>24 horas</option>
          <option value={168}>7 dias</option>
          <option value={720}>30 dias</option>
        </select>
        {config && (
          <span className={`${CHIP} ${config.enabled ? "bg-emerald-500/15 text-emerald-500" : "bg-muted/15 text-muted"}`}
            title={`amostra ${(config.sample_rate * 100).toFixed(0)}% · retenção ${config.retention_days}d · fila ${config.sink.queued} · descartados ${config.sink.dropped}`}>
            <Clock size={11} className="mr-1 inline" />
            {config.enabled ? "captura ligada" : "desligada"}
          </span>
        )}
      </div>

      {tab === "summary" && (summary ? <SummaryPanel summary={summary} /> : <p className="py-8 text-center text-sm text-muted">Carregando…</p>)}
      {tab === "traces" && <TraceList hours={hours} onOpen={setOpenId} />}

      {openId && <TraceDetail traceId={openId} onClose={() => setOpenId(null)} />}
    </div>
  );
}
