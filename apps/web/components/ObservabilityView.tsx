"use client";

/**
 * Painel de Observabilidade (admin): onde o tempo vai, com números exatos.
 *
 * - Resumo: totais do período e os endpoints que mais consomem tempo.
 * - Gargalos: cada ETAPA agregada (chamada ao modelo, ferramenta, requisição
 *   externa, preparação do turno, agente…) — p50/p95/p99, tempo total e tempo
 *   PRÓPRIO (sem os filhos); o clique abre os números que explicam a demora.
 * - Endpoints: um endpoint por dentro — histograma, tempo por etapa por chamada,
 *   o que ele dispara (ex.: a geração do chat) e as chamadas mais lentas.
 * - Chamadas: a lista de traces; o detalhe é um waterfall com a cadeia (quem
 *   disparou / o que foi disparado) e o tempo próprio de cada etapa.
 * - Tempo real: atraso do event loop, travadas e os suspeitos, o que está rodando.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Activity, AlertTriangle, ArrowLeft, ChevronRight, Clock, Copy, Cpu, Database, Gauge,
  GitBranch, Layers, RefreshCw, Search, Timer, X, Zap,
} from "lucide-react";
import { api } from "@/lib/api";
import type { ObsConfig, ObsSpan, ObsSummary, ObsTrace } from "@/lib/types";
import { InfoDot, Select } from "@/components/ui";
import { dateLocale, tr } from "@/lib/i18n";

const KIND_COLOR: Record<string, string> = {
  http: "#6366f1", chat: "#8b5cf6", api: "#0ea5e9", automation: "#f59e0b",
  channel: "#10b981", worker: "#64748b", client: "#ec4899", runtime: "#ef4444",
};
const SPAN_COLOR: Record<string, string> = {
  internal: "#94a3b8", db: "#0ea5e9", http: "#22c55e", llm: "#8b5cf6",
  tool: "#f59e0b", rag: "#14b8a6", memory: "#ec4899", auth: "#ef4444",
  client: "#ec4899", agent: "#06b6d4",
};
const SPAN_KIND_LABEL: Record<string, string> = {
  "": tr("Todas"), llm: tr("Modelo"), tool: "Ferramentas", http: tr("Requisições externas"),
  agent: tr("Agentes"), internal: tr("Preparação e turno"), rag: "Conhecimento", memory: tr("Memória"),
};

// nomes legíveis dos números que as etapas registram
const ATTR_LABEL: Record<string, string> = {
  ttfb_ms: tr("1º byte do provedor"), first_reasoning_ms: tr("começou a pensar"), first_token_ms: "1ª palavra",
  first_tool_call_ms: tr("1ª chamada de ferramenta"), reasoning_ms: tr("tempo pensando"),
  generation_ms: tr("tempo gerando"), tokens_per_s: tr("tokens por segundo"), prompt_chars: "contexto (caracteres)",
  prompt_msgs: tr("mensagens no contexto"), tools_n: tr("ferramentas anunciadas"), prompt_tokens: tr("tokens de entrada"),
  completion_tokens: tr("tokens de saída"), total_tokens: tr("tokens no total"), cached_tokens: tr("tokens do cache"),
  reasoning_tokens: tr("tokens de raciocínio"), cost: "custo (US$)", args_chars: "entrada (caracteres)",
  result_chars: tr("saída (caracteres)"), queue_ms: tr("espera por vaga"), steps: tr("passos"), output_chars: tr("saída (caracteres)"),
  pool_queue_ms: tr("fila do pool de busca"), engines_ms: tr("tempo nos motores"), attempts: tr("tentativas"),
  results: tr("resultados"), status_code: tr("status HTTP"), resp_bytes: tr("bytes da resposta"), http_ms: tr("tempo HTTP"),
  depth: tr("profundidade"), attachments: tr("anexos"), lag_ms: tr("atraso do loop"), iteration: tr("iteração"),
  server_ttft_ms: "1ª palavra (servidor)", llm_ms: tr("tempo no modelo"), llm_iterations: tr("chamadas ao modelo"),
  tool_calls: tr("ferramentas chamadas"), tool_results: tr("resultados de ferramentas"), tool_executions: tr("ferramentas executadas"),
  user_chars: "mensagem (caracteres)", outcome: tr("resultado"), turn_kind: tr("tipo de turno"), parent_name: tr("disparado por"),
  parent_span_name: "na etapa", bg: "2º plano", has_tools: tr("com ferramentas"), background: "2º plano",
  cache: tr("cache"), backend: tr("motores"), provider: "provedor", inner: tr("ferramenta"), timed_out: tr("estourou o tempo"),
  until: tr("medido até"),
};
const MS_ATTR = (k: string) => k.endsWith("_ms");

function color(map: Record<string, string>, k: string): string {
  return map[k] ?? "#94a3b8";
}

function fmtMs(ms: number | null | undefined): string {
  const v = Number(ms || 0);
  if (v >= 60000) return `${(v / 60000).toFixed(1)}min`;
  if (v >= 1000) return `${(v / 1000).toFixed(2)}s`;
  return `${v.toFixed(v < 10 ? 1 : 0)}ms`;
}

function fmtNum(k: string, v: number): string {
  if (MS_ATTR(k)) return fmtMs(v);
  if (k === "cost") return `$${v.toFixed(4)}`;
  if (Math.abs(v) >= 1000) return v.toLocaleString(dateLocale(), { maximumFractionDigits: 0 });
  return v.toLocaleString(dateLocale(), { maximumFractionDigits: 1 });
}

function fmtTime(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString(dateLocale(), { dateStyle: "short", timeStyle: "medium" });
}

const CARD = "rounded-xl border border-border bg-surface p-4";
const CHIP = "rounded-full px-2.5 py-0.5 text-[11px] font-medium";
const TH = "px-3 py-2 font-medium whitespace-nowrap";
const TD = "px-3 py-2 whitespace-nowrap";

function KindChip({ kind, label, map = KIND_COLOR }: { kind: string; label?: string; map?: Record<string, string> }) {
  return (
    <span className={CHIP} style={{ background: `${color(map, kind)}22`, color: color(map, kind) }}>
      {label || kind}
    </span>
  );
}

function Stat({ label, value, sub, icon, hint }: {
  label: string; value: string; sub?: string; icon: React.ReactNode; hint?: string;
}) {
  return (
    <div className={CARD}>
      <div className="flex items-center gap-1.5 text-[11px] text-muted">{icon}{label}{hint && <InfoDot text={hint} />}</div>
      <p className="mt-1 text-xl font-semibold tabular-nums text-ink">{value}</p>
      {sub && <p className="text-[11px] text-muted">{sub}</p>}
    </div>
  );
}

function Sparkline({ values, h = 28 }: { values: number[]; h?: number }) {
  if (values.length < 2) return null;
  const w = 100;
  const max = Math.max(...values, 1);
  const pts = values.map((v, i) => `${((i / (values.length - 1)) * w).toFixed(1)},${(h - (v / max) * h).toFixed(1)}`).join(" ");
  return (
    <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" className="h-8 w-full">
      <polyline points={pts} fill="none" stroke="rgb(var(--c-accent))" strokeWidth="1.5" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

function Bar({ value, max, colorHex = "rgb(var(--c-accent))" }: { value: number; max: number; colorHex?: string }) {
  return (
    <div className="h-1.5 w-full min-w-[60px] overflow-hidden rounded-full bg-surface2">
      <div className="h-full rounded-full" style={{ width: `${max ? Math.min(100, (value / max) * 100) : 0}%`, background: colorHex }} />
    </div>
  );
}

function Loading() {
  return <p className="py-8 text-center text-sm text-muted">{tr("Carregando…")}</p>;
}

function Modal({ title, sub, onClose, onBack, children, wide }: {
  title: React.ReactNode; sub?: React.ReactNode; onClose: () => void; onBack?: () => void;
  children: React.ReactNode; wide?: boolean;
}) {
  useEffect(() => {
    const k = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", k);
    return () => document.removeEventListener("keydown", k);
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" onClick={onClose}>
      <div onClick={(e) => e.stopPropagation()}
        className={`flex max-h-[90vh] w-full ${wide ? "max-w-5xl" : "max-w-4xl"} flex-col overflow-hidden rounded-2xl border border-border bg-bg shadow-xl`}>
        <div className="flex items-center gap-2 border-b border-border px-5 py-3.5">
          {onBack && (
            <button onClick={onBack} title={tr("Voltar")} className="rounded-lg p-1 text-muted hover:bg-hover hover:text-ink"><ArrowLeft size={16} /></button>
          )}
          <div className="min-w-0 flex-1">
            <h2 className="truncate text-base font-semibold text-ink">{title}</h2>
            {sub && <div className="text-xs text-muted">{sub}</div>}
          </div>
          <button onClick={onClose} className="rounded-lg p-1 text-muted hover:bg-hover hover:text-ink"><X size={18} /></button>
        </div>
        <div className="flex-1 overflow-y-auto px-5 py-4">{children}</div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tipos das rotas novas
// ---------------------------------------------------------------------------

type RouteRow = ObsSummary["routes"][number] & { p50_ms?: number; p99_ms?: number; avg_llm_ms?: number; total_ms?: number };

interface Operation {
  name: string; kind: string; count: number; errors: number;
  p50_ms: number; p95_ms: number; p99_ms: number; max_ms: number; total_ms: number;
  avg_db_ms: number; avg_http_ms: number; avg_queries: number;
  avg_self_ms: number; self_total_ms: number; self_share: number;
}
interface NumStats { n: number; avg: number; p50: number; p95: number; max: number }
interface OperationDetailData {
  name: string; count: number; duration: NumStats | null;
  attrs: Record<string, NumStats>;
  groups: ({ key: string } & NumStats)[];
  errors: { error: string; count: number }[];
  slowest: { trace_id: string; span_id: string; duration_ms: number; started_at: string | null; status: string; attrs: Record<string, unknown> }[];
}
interface RouteDetailData {
  path: string; method: string; count: number; errors?: number;
  p50_ms?: number; p95_ms?: number; p99_ms?: number; max_ms?: number; avg_ms?: number;
  avg_db_ms?: number; avg_queries?: number; avg_llm_ms?: number; avg_http_ms?: number; avg_unaccounted_ms?: number;
  breakdown: { name: string; kind: string; count: number; traces: number; per_call_ms: number; p95_ms: number; calls_per_trace: number; top_level: boolean }[];
  histogram: { bucket: string; count: number }[];
  slowest: ObsTrace[];
  children: { name: string; kind: string; count: number; avg_ms: number; p95_ms: number }[];
}
interface Runtime {
  uptime_s: number; pid: number; python: string; memory_mb: number | null; cpu_s: number | null;
  loop_lag: Record<"1m" | "5m" | "15m", { p50: number; p95: number; p99: number; max: number; samples: number }>;
  lag_series: { t: number; ms: number }[];
  stalls: { at: number; lag_ms: number; suspects: { trace: string; name: string; age_ms: number; last_span: string }[] }[];
  open_traces: { id: string; name: string; kind: string; age_ms: number; spans: number; last_span: string; parent_trace?: string }[];
  tasks: number | null; bg_tasks: number; active_generations: number | null;
  db_pool: Record<string, unknown>;
  sink: { queued: number; dropped: number; written: number; running: boolean };
}

// ---------------------------------------------------------------------------
// Resumo
// ---------------------------------------------------------------------------

function RoutesTable({ routes, onOpen }: { routes: RouteRow[]; onOpen: (r: RouteRow) => void }) {
  const maxTot = Math.max(...routes.map((r) => r.total_ms ?? r.avg_ms * r.count), 1);
  return (
    <div className="overflow-x-auto rounded-xl border border-border">
      <table className="w-full text-left text-xs">
        <thead className="bg-surface2 text-muted">
          <tr>
            <th className={TH}>{tr("Endpoint")}</th><th className={TH}>{tr("Chamadas")}</th><th className={TH}>p50</th>
            <th className={TH}>p95</th><th className={TH}>p99</th><th className={TH}>{tr("Banco")}</th>
            <th className={TH}>IA</th><th className={TH}>{tr("Tempo total")}</th><th className={TH}>{tr("Erros")}</th>
          </tr>
        </thead>
        <tbody>
          {routes.map((r, i) => {
            const tot = r.total_ms ?? r.avg_ms * r.count;
            return (
              <tr key={i} onClick={() => onOpen(r)} className="cursor-pointer border-t border-border hover:bg-hover">
                <td className="px-3 py-2">
                  <KindChip kind={r.kind} label={r.method || r.kind} />
                  <span className="ml-2 font-mono text-ink">{r.path || "—"}</span>
                </td>
                <td className={`${TD} tabular-nums text-ink-soft`}>{r.count}</td>
                <td className={`${TD} tabular-nums text-ink-soft`}>{r.p50_ms != null ? fmtMs(r.p50_ms) : "—"}</td>
                <td className={`${TD} tabular-nums text-ink-soft`}>{fmtMs(r.p95_ms)}</td>
                <td className={`${TD} tabular-nums text-muted`}>{r.p99_ms != null ? fmtMs(r.p99_ms) : "—"}</td>
                <td className={`${TD} tabular-nums text-muted`}>{fmtMs(r.avg_db_ms)} · {r.avg_queries}q</td>
                <td className={`${TD} tabular-nums text-muted`}>{r.avg_llm_ms ? fmtMs(r.avg_llm_ms) : "—"}</td>
                <td className="px-3 py-2">
                  <div className="flex items-center gap-2"><Bar value={tot} max={maxTot} /><span className="tabular-nums text-muted">{fmtMs(tot)}</span></div>
                </td>
                <td className={TD}>{r.errors > 0 ? <span className="text-red-500">{r.errors}</span> : <span className="text-muted">0</span>}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function SummaryPanel({ summary, onRoute }: { summary: ObsSummary; onRoute: (r: RouteRow) => void }) {
  const t = summary.totals;
  const errRate = t.traces ? ((t.errors / t.traces) * 100).toFixed(1) : "0";
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Stat label={tr("Chamadas")} value={String(t.traces)} sub={tr("{errors} com erro ({errRate}%)", { errors: t.errors, errRate: errRate })} icon={<Activity size={13} />} />
        <Stat label={tr("Latência p50")} value={fmtMs(t.p50_ms)} sub={`p95 ${fmtMs(t.p95_ms)} · p99 ${fmtMs(t.p99_ms)}`} icon={<Gauge size={13} />} />
        <Stat label={tr("Banco (médio)")} value={fmtMs(t.avg_db_ms)} sub={`${t.db_queries.toLocaleString(dateLocale())} queries`} icon={<Database size={13} />} />
        <Stat label={tr("Tempo no modelo")} value={fmtMs(t.llm_ms)} sub={tr("soma do período")} icon={<Zap size={13} />} />
      </div>
      <div className={CARD}>
        <p className="mb-1 text-xs font-medium text-ink-soft">{tr("Volume por hora")}</p>
        <Sparkline values={summary.series.map((s) => s.count)} />
      </div>
      <div>
        <p className="mb-2 flex items-center gap-1.5 text-xs font-medium text-ink-soft">
          
          {tr("Endpoints por tempo total")} <InfoDot text={tr("Tempo total = chamadas × duração. Clique para ver onde o tempo de cada chamada vai.")} />
        </p>
        <RoutesTable routes={summary.routes as RouteRow[]} onOpen={onRoute} />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Gargalos (operações)
// ---------------------------------------------------------------------------

function OperationsPanel({ hours, onOpen }: { hours: number; onOpen: (name: string) => void }) {
  const [ops, setOps] = useState<Operation[] | null>(null);
  const [spanKind, setSpanKind] = useState("");
  const [q, setQ] = useState("");
  const [ordem, setOrdem] = useState<"total" | "self" | "p95" | "count">("total");

  useEffect(() => {
    let vivo = true;
    setOps(null);
    const p = new URLSearchParams({ hours: String(hours), limit: "200" });
    if (spanKind) p.set("span_kind", spanKind);
    api.get<{ operations: Operation[] }>(`/observability/operations?${p}`)
      .then((d) => vivo && setOps(d.operations)).catch(() => vivo && setOps([]));
    return () => { vivo = false; };
  }, [hours, spanKind]);

  const lista = useMemo(() => {
    const f = (ops ?? []).filter((o) => !q || o.name.toLowerCase().includes(q.toLowerCase()));
    const key: Record<typeof ordem, (o: Operation) => number> = {
      total: (o) => o.total_ms, self: (o) => o.self_total_ms, p95: (o) => o.p95_ms, count: (o) => o.count,
    };
    return [...f].sort((a, b) => key[ordem](b) - key[ordem](a));
  }, [ops, q, ordem]);
  const maxSelf = Math.max(...lista.map((o) => o.self_total_ms), 1);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        {Object.entries(SPAN_KIND_LABEL).map(([k, label]) => (
          <button key={k || "all"} onClick={() => setSpanKind(k)}
            className={`rounded-full px-3 py-1 text-xs transition-colors ${spanKind === k ? "bg-accent text-white" : "border border-border text-ink-soft hover:bg-hover"}`}>
            {label}
          </button>
        ))}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex items-center gap-1.5 rounded-lg border border-border bg-surface px-2.5 py-1.5">
          <Search size={14} className="text-muted" />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={tr("Filtrar etapa…")}
            className="w-44 bg-transparent text-xs text-ink outline-none placeholder:text-muted" />
        </div>
        <Select value={ordem} onChange={(e) => setOrdem(e.target.value as typeof ordem)}
          className="rounded-lg border border-border bg-surface px-2 py-1.5 text-xs text-ink outline-none">
          <option value="total">{tr("Ordenar por tempo total")}</option>
          <option value="self">{tr("Ordenar por tempo próprio")}</option>
          <option value="p95">{tr("Ordenar por p95")}</option>
          <option value="count">{tr("Ordenar por nº de execuções")}</option>
        </Select>
        <InfoDot text={tr("Tempo próprio = duração da etapa menos o tempo das etapas dentro dela. É o tempo gasto NELA mesma — o melhor indicador de gargalo.")} />
      </div>
      {!ops ? <Loading /> : lista.length === 0 ? <p className="py-8 text-center text-sm text-muted">{tr("Nenhuma etapa registrada no período.")}</p> : (
        <div className="overflow-x-auto rounded-xl border border-border">
          <table className="w-full text-left text-xs">
            <thead className="bg-surface2 text-muted">
              <tr>
                <th className={TH}>{tr("Etapa")}</th><th className={TH}>{tr("Vezes")}</th><th className={TH}>p50</th><th className={TH}>p95</th>
                <th className={TH}>p99</th><th className={TH}>{tr("Máx")}</th><th className={TH}>{tr("Total")}</th>
                <th className={TH}>{tr("Tempo próprio")}</th><th className={TH}>{tr("Banco")}</th><th className={TH}>{tr("Erros")}</th>
              </tr>
            </thead>
            <tbody>
              {lista.map((o) => (
                <tr key={`${o.kind}:${o.name}`} onClick={() => onOpen(o.name)} className="cursor-pointer border-t border-border hover:bg-hover">
                  <td className="max-w-[320px] px-3 py-2">
                    <div className="flex items-center gap-2">
                      <span className="h-2 w-2 shrink-0 rounded-sm" style={{ background: color(SPAN_COLOR, o.kind) }} />
                      <span className="truncate font-mono text-ink" title={o.name}>{o.name}</span>
                    </div>
                  </td>
                  <td className={`${TD} tabular-nums text-ink-soft`}>{o.count.toLocaleString(dateLocale())}</td>
                  <td className={`${TD} tabular-nums text-ink-soft`}>{fmtMs(o.p50_ms)}</td>
                  <td className={`${TD} tabular-nums text-ink-soft`}>{fmtMs(o.p95_ms)}</td>
                  <td className={`${TD} tabular-nums text-muted`}>{fmtMs(o.p99_ms)}</td>
                  <td className={`${TD} tabular-nums text-muted`}>{fmtMs(o.max_ms)}</td>
                  <td className={`${TD} tabular-nums text-ink-soft`}>{fmtMs(o.total_ms)}</td>
                  <td className="px-3 py-2">
                    <div className="flex items-center gap-2">
                      <Bar value={o.self_total_ms} max={maxSelf} colorHex={color(SPAN_COLOR, o.kind)} />
                      <span className="tabular-nums text-muted">{fmtMs(o.self_total_ms)} · {(o.self_share * 100).toFixed(1)}%</span>
                    </div>
                  </td>
                  <td className={`${TD} tabular-nums text-muted`}>{o.avg_queries > 0 ? `${fmtMs(o.avg_db_ms)} · ${o.avg_queries}q` : "—"}</td>
                  <td className={TD}>{o.errors > 0 ? <span className="text-red-500">{o.errors}</span> : <span className="text-muted">0</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function OperationDetail({ name, hours, onClose, onTrace }: {
  name: string; hours: number; onClose: () => void; onTrace: (id: string) => void;
}) {
  const [d, setD] = useState<OperationDetailData | null>(null);
  useEffect(() => {
    let vivo = true;
    api.get<OperationDetailData>(`/observability/operations/detail?${new URLSearchParams({ name, hours: String(hours) })}`)
      .then((x) => vivo && setD(x)).catch(() => vivo && setD(null));
    return () => { vivo = false; };
  }, [name, hours]);
  const attrs = Object.entries(d?.attrs ?? {}).filter(([k]) => k !== "offset_ms");
  return (
    <Modal title={<span className="font-mono">{name}</span>} onClose={onClose} wide
      sub={d?.duration ? tr("{count} execuções · média {1} · p50 {2} · p95 {3} · máx {4}", { count: d.count, "1": fmtMs(d.duration.avg), "2": fmtMs(d.duration.p50), "3": fmtMs(d.duration.p95), "4": fmtMs(d.duration.max) }) : undefined}>
      {!d ? <Loading /> : d.count === 0 ? <p className="py-8 text-center text-sm text-muted">{tr("Sem execuções no período.")}</p> : (
        <div className="space-y-5">
          {attrs.length > 0 && (
            <section>
              <p className="mb-2 text-xs font-medium text-ink-soft">{tr("O que ela registra")}</p>
              <div className="overflow-x-auto rounded-xl border border-border">
                <table className="w-full text-left text-xs">
                  <thead className="bg-surface2 text-muted"><tr><th className={TH}>{tr("Número")}</th><th className={TH}>{tr("Média")}</th><th className={TH}>p50</th><th className={TH}>p95</th><th className={TH}>{tr("Máx")}</th><th className={TH}>{tr("Amostras")}</th></tr></thead>
                  <tbody>
                    {attrs.map(([k, s]) => (
                      <tr key={k} className="border-t border-border">
                        <td className="px-3 py-2 text-ink">{ATTR_LABEL[k] ?? k}<span className="ml-1.5 font-mono text-[10px] text-muted">{k}</span></td>
                        <td className={`${TD} tabular-nums text-ink-soft`}>{fmtNum(k, s.avg)}</td>
                        <td className={`${TD} tabular-nums text-ink-soft`}>{fmtNum(k, s.p50)}</td>
                        <td className={`${TD} tabular-nums text-ink-soft`}>{fmtNum(k, s.p95)}</td>
                        <td className={`${TD} tabular-nums text-muted`}>{fmtNum(k, s.max)}</td>
                        <td className={`${TD} tabular-nums text-muted`}>{s.n}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          )}
          {d.groups.length > 0 && (
            <section>
              <p className="mb-2 text-xs font-medium text-ink-soft">{tr("Por destino")}</p>
              <div className="overflow-x-auto rounded-xl border border-border">
                <table className="w-full text-left text-xs">
                  <thead className="bg-surface2 text-muted"><tr><th className={TH}>{tr("Destino")}</th><th className={TH}>{tr("Vezes")}</th><th className={TH}>{tr("Média")}</th><th className={TH}>p95</th><th className={TH}>{tr("Máx")}</th></tr></thead>
                  <tbody>
                    {d.groups.map((g) => (
                      <tr key={g.key} className="border-t border-border">
                        <td className="max-w-[420px] truncate px-3 py-2 font-mono text-ink" title={g.key}>{g.key}</td>
                        <td className={`${TD} tabular-nums text-ink-soft`}>{g.n}</td>
                        <td className={`${TD} tabular-nums text-ink-soft`}>{fmtMs(g.avg)}</td>
                        <td className={`${TD} tabular-nums text-ink-soft`}>{fmtMs(g.p95)}</td>
                        <td className={`${TD} tabular-nums text-muted`}>{fmtMs(g.max)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          )}
          {d.errors.length > 0 && (
            <section>
              <p className="mb-2 text-xs font-medium text-ink-soft">{tr("Erros mais comuns")}</p>
              <div className="space-y-1">
                {d.errors.map((e) => (
                  <div key={e.error} className="flex items-start gap-2 rounded-lg bg-red-500/10 px-3 py-1.5 text-xs">
                    <span className="shrink-0 font-semibold tabular-nums text-red-500">{e.count}×</span>
                    <span className="min-w-0 break-all text-ink-soft">{e.error}</span>
                  </div>
                ))}
              </div>
            </section>
          )}
          <section>
            <p className="mb-2 text-xs font-medium text-ink-soft">{tr("Execuções mais lentas")}</p>
            <div className="overflow-hidden rounded-xl border border-border">
              {d.slowest.map((s) => (
                <button key={s.span_id} onClick={() => onTrace(s.trace_id)}
                  className="flex w-full items-center gap-2 border-b border-border/60 px-3 py-2 text-left text-xs last:border-0 hover:bg-hover">
                  <span className={`w-16 shrink-0 font-mono tabular-nums ${s.status === "error" ? "text-red-500" : "text-ink"}`}>{fmtMs(s.duration_ms)}</span>
                  <span className="min-w-0 flex-1 truncate text-muted">
                    {Object.entries(s.attrs).filter(([, v]) => typeof v === "number" || typeof v === "string").slice(0, 6)
                      .map(([k, v]) => `${ATTR_LABEL[k] ?? k}: ${typeof v === "number" ? fmtNum(k, v) : String(v).slice(0, 40)}`).join(" · ")}
                  </span>
                  <span className="hidden shrink-0 text-[10px] text-muted sm:block">{fmtTime(s.started_at)}</span>
                  <ChevronRight size={14} className="shrink-0 text-muted" />
                </button>
              ))}
            </div>
          </section>
        </div>
      )}
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// Endpoint por dentro
// ---------------------------------------------------------------------------

function RouteDetail({ route, hours, onClose, onTrace, onOperation }: {
  route: { path: string; method: string; kind: string }; hours: number;
  onClose: () => void; onTrace: (id: string) => void; onOperation: (name: string) => void;
}) {
  const [d, setD] = useState<RouteDetailData | null>(null);
  useEffect(() => {
    let vivo = true;
    const p = new URLSearchParams({ path: route.path, method: route.method, hours: String(hours) });
    if (route.kind) p.set("kind", route.kind);
    api.get<RouteDetailData>(`/observability/route?${p}`).then((x) => vivo && setD(x)).catch(() => vivo && setD(null));
    return () => { vivo = false; };
  }, [route, hours]);

  const topo = (d?.breakdown ?? []).filter((b) => b.top_level && b.kind !== "client");
  const explicado = topo.reduce((a, b) => a + b.per_call_ms, 0) + (d?.avg_unaccounted_ms ?? 0);
  const maxHist = Math.max(...(d?.histogram ?? []).map((h) => h.count), 1);
  return (
    <Modal wide onClose={onClose}
      title={<span><KindChip kind={route.kind} label={route.method || route.kind} /> <span className="ml-1 font-mono">{route.path}</span></span>}
      sub={d && d.count > 0 ? `${d.count} chamadas · ${d.errors ?? 0} erros` : undefined}>
      {!d ? <Loading /> : d.count === 0 ? <p className="py-8 text-center text-sm text-muted">{tr("Sem chamadas no período.")}</p> : (
        <div className="space-y-5">
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Stat label="p50" value={fmtMs(d.p50_ms)} sub={tr("média {0}", { "0": fmtMs(d.avg_ms) })} icon={<Gauge size={13} />} />
            <Stat label="p95 / p99" value={fmtMs(d.p95_ms)} sub={`p99 ${fmtMs(d.p99_ms)} · máx ${fmtMs(d.max_ms)}`} icon={<Timer size={13} />} />
            <Stat label={tr("Banco por chamada")} value={fmtMs(d.avg_db_ms)} sub={`${d.avg_queries} queries`} icon={<Database size={13} />} />
            <Stat label="Modelo / rede" value={fmtMs(d.avg_llm_ms)} sub={`externo ${fmtMs(d.avg_http_ms)}`} icon={<Zap size={13} />} />
          </div>

          <section>
            <p className="mb-2 flex items-center gap-1.5 text-xs font-medium text-ink-soft">
              
              {tr("Onde o tempo de cada chamada vai")} <InfoDot text={tr("Média por chamada de cada etapa de 1º nível. 'Sem etapa medida' é o tempo do próprio handler fora de qualquer etapa (candidato a instrumentar).")} />
            </p>
            <div className="mb-2 flex h-5 w-full overflow-hidden rounded-lg bg-surface2">
              {topo.map((b) => (
                <div key={b.name} title={`${b.name}: ${fmtMs(b.per_call_ms)}`}
                  style={{ width: `${explicado ? (b.per_call_ms / explicado) * 100 : 0}%`, background: color(SPAN_COLOR, b.kind) }} />
              ))}
              {(d.avg_unaccounted_ms ?? 0) > 0 && (
                <div title={tr("sem etapa medida: {0}", { "0": fmtMs(d.avg_unaccounted_ms) })}
                  style={{ width: `${explicado ? ((d.avg_unaccounted_ms ?? 0) / explicado) * 100 : 0}%` }}
                  className="bg-[repeating-linear-gradient(45deg,transparent,transparent_4px,rgb(var(--c-border))_4px,rgb(var(--c-border))_8px)]" />
              )}
            </div>
            <div className="overflow-x-auto rounded-xl border border-border">
              <table className="w-full text-left text-xs">
                <thead className="bg-surface2 text-muted">
                  <tr><th className={TH}>{tr("Etapa")}</th><th className={TH}>{tr("Por chamada")}</th><th className={TH}>{tr("Vezes por chamada")}</th><th className={TH}>p95</th></tr>
                </thead>
                <tbody>
                  {d.breakdown.map((b) => (
                    <tr key={b.name} onClick={() => onOperation(b.name)} className="cursor-pointer border-t border-border hover:bg-hover">
                      <td className="px-3 py-2">
                        <div className="flex items-center gap-2">
                          <span className="h-2 w-2 shrink-0 rounded-sm" style={{ background: color(SPAN_COLOR, b.kind) }} />
                          <span className={`font-mono ${b.top_level ? "text-ink" : "text-ink-soft"}`}>{b.top_level ? "" : "↳ "}{b.name}</span>
                        </div>
                      </td>
                      <td className={`${TD} tabular-nums text-ink-soft`}>{fmtMs(b.per_call_ms)}</td>
                      <td className={`${TD} tabular-nums text-muted`}>{b.calls_per_trace}</td>
                      <td className={`${TD} tabular-nums text-muted`}>{fmtMs(b.p95_ms)}</td>
                    </tr>
                  ))}
                  <tr className="border-t border-border">
                    <td className="px-3 py-2 italic text-muted">{tr("sem etapa medida")}</td>
                    <td className={`${TD} tabular-nums text-muted`}>{fmtMs(d.avg_unaccounted_ms)}</td>
                    <td className={TD} /><td className={TD} />
                  </tr>
                </tbody>
              </table>
            </div>
          </section>

          {d.children.length > 0 && (
            <section>
              <p className="mb-2 flex items-center gap-1.5 text-xs font-medium text-ink-soft"><GitBranch size={13} />  {tr("O que estas chamadas disparam")}</p>
              <div className="overflow-x-auto rounded-xl border border-border">
                <table className="w-full text-left text-xs">
                  <thead className="bg-surface2 text-muted"><tr><th className={TH}>{tr("Trabalho disparado")}</th><th className={TH}>{tr("Vezes")}</th><th className={TH}>{tr("Média")}</th><th className={TH}>p95</th></tr></thead>
                  <tbody>
                    {d.children.map((c) => (
                      <tr key={c.name} className="border-t border-border">
                        <td className="px-3 py-2"><KindChip kind={c.kind} /> <span className="ml-1 font-mono text-ink">{c.name}</span></td>
                        <td className={`${TD} tabular-nums text-ink-soft`}>{c.count}</td>
                        <td className={`${TD} tabular-nums text-ink-soft`}>{fmtMs(c.avg_ms)}</td>
                        <td className={`${TD} tabular-nums text-muted`}>{fmtMs(c.p95_ms)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          )}

          <section>
            <p className="mb-2 text-xs font-medium text-ink-soft">{tr("Distribuição da duração")}</p>
            <div className="space-y-1">
              {d.histogram.map((h) => (
                <div key={h.bucket} className="flex items-center gap-2 text-xs">
                  <span className="w-24 shrink-0 text-right tabular-nums text-muted">{h.bucket}</span>
                  <div className="h-3 flex-1 overflow-hidden rounded bg-surface2">
                    <div className="h-full rounded bg-accent/70" style={{ width: `${(h.count / maxHist) * 100}%` }} />
                  </div>
                  <span className="w-10 shrink-0 tabular-nums text-ink-soft">{h.count}</span>
                </div>
              ))}
            </div>
          </section>

          <section>
            <p className="mb-2 text-xs font-medium text-ink-soft">{tr("Chamadas mais lentas")}</p>
            <TraceRows traces={d.slowest} onOpen={onTrace} />
          </section>
        </div>
      )}
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// Waterfall de um trace (com a cadeia e o tempo próprio)
// ---------------------------------------------------------------------------

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

function AttrChips({ attrs }: { attrs: Record<string, unknown> }) {
  const ents = Object.entries(attrs).filter(([k]) => k !== "queries" && k !== "suspects" && k !== "child_traces");
  if (!ents.length) return null;
  return (
    <div className="flex flex-wrap gap-1.5">
      {ents.map(([k, v]) => (
        <span key={k} className="rounded bg-surface2 px-1.5 py-0.5 text-[10px] text-muted" title={k}>
          {ATTR_LABEL[k] ?? k}: <span className="text-ink-soft">{typeof v === "number" ? fmtNum(k, v) : Array.isArray(v) ? v.join(", ") : String(v)}</span>
        </span>
      ))}
    </div>
  );
}

function SpanRow({ span, total, depth, selfMs }: { span: ObsSpan; total: number; depth: number; selfMs: number }) {
  const [open, setOpen] = useState(false);
  const left = total ? (span.offset_ms / total) * 100 : 0;
  const width = total ? Math.max((span.duration_ms / total) * 100, 0.4) : 0.4;
  const queries = (span.attrs?.queries as { sql: string; ms: number; rows: number | null }[]) ?? [];
  // marcos (1º byte, 1ª palavra…) desenhados dentro da barra
  const marcos = ["ttfb_ms", "first_reasoning_ms", "first_token_ms"]
    .map((k) => [k, Number(span.attrs?.[k])] as const)
    .filter(([, v]) => Number.isFinite(v) && v > 0 && span.duration_ms > 0);
  return (
    <div className="border-b border-border/60 last:border-0">
      <button onClick={() => setOpen((v) => !v)} className="flex w-full items-center gap-2 py-1.5 text-left hover:bg-hover">
        <div className="flex min-w-0 shrink-0 items-center gap-1.5" style={{ width: 260, paddingLeft: 4 + depth * 12 }}>
          <ChevronRight size={12} className={`shrink-0 text-muted transition-transform ${open ? "rotate-90" : ""}`} />
          <span className="h-2 w-2 shrink-0 rounded-sm" style={{ background: color(SPAN_COLOR, span.kind) }} />
          <span className={`truncate text-xs ${span.status === "error" ? "text-red-500" : "text-ink"}`} title={span.name}>{span.name}</span>
        </div>
        <div className="relative h-4 flex-1 rounded bg-surface2">
          <div className="absolute top-0 h-4 rounded" style={{
            left: `${left}%`, width: `${width}%`,
            background: span.status === "error" ? "#ef4444" : color(SPAN_COLOR, span.kind), opacity: 0.85,
          }}>
            {marcos.map(([k, v]) => (
              <span key={k} title={`${ATTR_LABEL[k]}: ${fmtMs(v)}`} className="absolute top-0 h-4 w-0.5 bg-white/90"
                style={{ left: `${Math.min(100, (v / span.duration_ms) * 100)}%` }} />
            ))}
          </div>
        </div>
        <span className="w-16 shrink-0 text-right font-mono text-[11px] tabular-nums text-ink-soft">{fmtMs(span.duration_ms)}</span>
        <span className="w-16 shrink-0 text-right font-mono text-[10px] tabular-nums text-muted" title={tr("tempo próprio (sem as etapas de dentro)")}>{fmtMs(selfMs)}</span>
        <span className="w-16 shrink-0 text-right text-[10px] text-muted">
          {(span.db_reads + span.db_writes) > 0 && `${span.db_reads}r/${span.db_writes}w`}
        </span>
      </button>
      {open && (
        <div className="space-y-1.5 px-4 pb-2 pl-8">
          {span.error && <p className="text-[11px] text-red-500">{span.error}</p>}
          <p className="text-[10px] text-muted">
            
            {tr("começou em")} {fmtMs(span.offset_ms)}  {tr("· duração")} {fmtMs(span.duration_ms)}  {tr("· próprio")} {fmtMs(selfMs)}
            {span.db_ms > 0 && ` · banco ${fmtMs(span.db_ms)}`}{span.http_ms > 0 && ` · HTTP ${fmtMs(span.http_ms)}`}
          </p>
          <AttrChips attrs={span.attrs || {}} />
          {queries.length > 0 && <QueryList queries={queries} />}
        </div>
      )}
    </div>
  );
}

function TraceRows({ traces, onOpen }: { traces: ObsTrace[]; onOpen: (id: string) => void }) {
  return (
    <div className="overflow-hidden rounded-xl border border-border">
      {traces.map((t) => (
        <button key={t.id} onClick={() => onOpen(t.id)}
          className="flex w-full items-center gap-2 border-b border-border/60 px-3 py-2 text-left last:border-0 hover:bg-hover">
          <KindChip kind={t.kind} label={t.method || t.kind} />
          <span className="min-w-0 flex-1 truncate font-mono text-xs text-ink">{t.path || t.name}</span>
          {typeof t.attrs?.parent_trace === "string" && <GitBranch size={11} className="shrink-0 text-muted" aria-label={tr("disparado por outra operação")} />}
          {t.status === "error" && <span className="shrink-0 text-[10px] text-red-500">erro {t.status_code || ""}</span>}
          {t.llm_ms > 0 && <span className="shrink-0 text-[10px] text-violet-400"><Zap size={10} className="inline" /> {fmtMs(t.llm_ms)}</span>}
          {t.db_queries > 0 && <span className="shrink-0 text-[10px] text-sky-400"><Database size={10} className="inline" /> {t.db_queries}</span>}
          <span className="w-16 shrink-0 text-right font-mono text-xs tabular-nums text-ink-soft">{fmtMs(t.duration_ms)}</span>
          <span className="hidden w-32 shrink-0 text-right text-[10px] text-muted sm:block">{fmtTime(t.started_at)}</span>
          <ChevronRight size={14} className="shrink-0 text-muted" />
        </button>
      ))}
    </div>
  );
}

function TraceDetail({ traceId, onClose }: { traceId: string; onClose: () => void }) {
  const [pilha, setPilha] = useState<string[]>([traceId]);
  const atual = pilha[pilha.length - 1];
  const [data, setData] = useState<{ trace: ObsTrace; spans: ObsSpan[]; parent: ObsTrace | null; children: ObsTrace[] } | null>(null);
  const [erro, setErro] = useState(false);

  useEffect(() => {
    let alive = true;
    setData(null);
    setErro(false);
    api.get<{ trace: ObsTrace; spans: ObsSpan[]; parent: ObsTrace | null; children: ObsTrace[] }>(`/observability/traces/${atual}`)
      .then((d) => alive && setData(d)).catch(() => alive && setErro(true));
    return () => { alive = false; };
  }, [atual]);

  const abrir = (id: string) => setPilha((p) => [...p, id]);

  const { ordered, depth, self } = useMemo(() => {
    const spans = data?.spans ?? [];
    const byId = new Map(spans.map((s) => [s.id, s]));
    const kids = new Map<string | null, ObsSpan[]>();
    for (const s of spans) {
      const p = s.parent_id && byId.has(s.parent_id) ? s.parent_id : null;
      kids.set(p, [...(kids.get(p) ?? []), s]);
    }
    const ordered: ObsSpan[] = [];
    const depth = new Map<string, number>();
    const walk = (p: string | null, d: number) => {
      for (const s of (kids.get(p) ?? []).sort((a, b) => a.offset_ms - b.offset_ms)) {
        ordered.push(s);
        depth.set(s.id, d);
        walk(s.id, d + 1);
      }
    };
    walk(null, 0);
    const self = new Map<string, number>();
    for (const s of spans) {
      const filhos = (kids.get(s.id) ?? []).reduce((a, c) => a + c.duration_ms, 0);
      self.set(s.id, Math.max(0, s.duration_ms - filhos));
    }
    return { ordered, depth, self };
  }, [data]);

  const t = data?.trace;
  const attrsTrace = { ...(t?.attrs ?? {}) };
  delete attrsTrace.parent_trace;
  delete attrsTrace.parent_span;
  return (
    <Modal wide onClose={onClose} onBack={pilha.length > 1 ? () => setPilha((p) => p.slice(0, -1)) : undefined}
      title={t ? <span><KindChip kind={t.kind} label={t.method || t.kind} /> <span className="ml-1">{t.name}</span></span> : tr("Trace")}
      sub={t && (
        <span className="flex flex-wrap items-center gap-x-2">
          <span>{fmtTime(t.started_at)} · {fmtMs(t.duration_ms)} · {t.db_queries} queries ({fmtMs(t.db_ms)})
            {t.llm_ms > 0 && ` · modelo ${fmtMs(t.llm_ms)}`} · {t.span_count} etapas</span>
          <button onClick={() => void navigator.clipboard?.writeText(t.id)} title={tr("Copiar id")} className="inline-flex items-center gap-1 text-muted hover:text-ink">
            <Copy size={11} /> {t.id.slice(0, 8)}
          </button>
        </span>
      )}>
      {erro ? <p className="py-8 text-center text-sm text-muted">{tr("Trace não encontrado (pode ainda estar em andamento).")}</p>
        : !data ? <Loading /> : (
          <div className="space-y-4">
            {(data.parent || data.children.length > 0) && (
              <section className="rounded-xl border border-border p-3">
                <p className="mb-2 flex items-center gap-1.5 text-xs font-medium text-ink-soft"><GitBranch size={13} />  {tr("Cadeia")}</p>
                {data.parent && (
                  <button onClick={() => abrir(data.parent!.id)} className="mb-1 flex w-full items-center gap-2 rounded-lg px-2 py-1 text-left text-xs hover:bg-hover">
                    <span className="w-24 shrink-0 text-muted">disparado por</span>
                    <KindChip kind={data.parent.kind} label={data.parent.method || data.parent.kind} />
                    <span className="min-w-0 flex-1 truncate text-ink">{data.parent.name}</span>
                    {typeof t?.attrs?.parent_span_name === "string" && <span className="shrink-0 font-mono text-[10px] text-muted">em {String(t.attrs.parent_span_name)}</span>}
                    <span className="shrink-0 font-mono tabular-nums text-muted">{fmtMs(data.parent.duration_ms)}</span>
                  </button>
                )}
                {data.children.map((c) => (
                  <button key={c.id} onClick={() => abrir(c.id)} className="flex w-full items-center gap-2 rounded-lg px-2 py-1 text-left text-xs hover:bg-hover">
                    <span className="w-24 shrink-0 text-muted">disparou</span>
                    <KindChip kind={c.kind} label={c.method || c.kind} />
                    <span className="min-w-0 flex-1 truncate text-ink">{c.name}</span>
                    {c.status === "error" && <span className="text-[10px] text-red-500">erro</span>}
                    <span className="shrink-0 font-mono tabular-nums text-muted">{fmtMs(c.duration_ms)}</span>
                  </button>
                ))}
              </section>
            )}
            <AttrChips attrs={attrsTrace} />
            {ordered.length === 0 ? <p className="py-6 text-center text-sm text-muted">{tr("Sem etapas instrumentadas nesta chamada.")}</p> : (
              <div className="rounded-xl border border-border">
                <div className="flex items-center gap-2 border-b border-border bg-surface2 px-1 py-1 text-[10px] text-muted">
                  <span style={{ width: 260 }} className="pl-6">{tr("Etapa")}</span>
                  <span className="flex-1">{tr("Linha do tempo")}</span>
                  <span className="w-16 text-right">{tr("Duração")}</span>
                  <span className="w-16 text-right">{tr("Próprio")}</span>
                  <span className="w-16 text-right">{tr("Banco")}</span>
                </div>
                {ordered.map((s) => (
                  <SpanRow key={s.id} span={s} total={t?.duration_ms || 1} depth={depth.get(s.id) ?? 0} selfMs={self.get(s.id) ?? 0} />
                ))}
              </div>
            )}
            {t?.error && <div className="rounded-lg border border-red-500/30 bg-red-500/10 p-3 text-xs text-red-500">{t.error}</div>}
          </div>
        )}
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// Lista de chamadas
// ---------------------------------------------------------------------------

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
    const params = new URLSearchParams({ hours: String(hours), limit: "200" });
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
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={tr("Buscar rota/nome…")}
            className="w-40 bg-transparent text-xs text-ink outline-none placeholder:text-muted" />
        </div>
        <Select value={kind} onChange={(e) => setKind(e.target.value)} className="rounded-lg border border-border bg-surface px-2 py-1.5 text-xs text-ink outline-none">
          <option value="">{tr("Todo tipo")}</option>
          {["http", "chat", "api", "automation", "channel", "worker", "runtime", "client"].map((k) => <option key={k} value={k}>{k}</option>)}
        </Select>
        <Select value={statusF} onChange={(e) => setStatusF(e.target.value)} className="rounded-lg border border-border bg-surface px-2 py-1.5 text-xs text-ink outline-none">
          <option value="">{tr("Qualquer status")}</option>
          <option value="ok">OK</option>
          <option value="error">{tr("Erro")}</option>
        </Select>
        <Select value={minMs} onChange={(e) => setMinMs(Number(e.target.value))} className="rounded-lg border border-border bg-surface px-2 py-1.5 text-xs text-ink outline-none">
          <option value={0}>{tr("Qualquer duração")}</option>
          <option value={200}>≥ 200ms</option>
          <option value={1000}>≥ 1s</option>
          <option value={3000}>≥ 3s</option>
          <option value={10000}>≥ 10s</option>
          <option value={60000}>≥ 1min</option>
        </Select>
        <button onClick={() => void load()} className="ml-auto flex items-center gap-1.5 rounded-lg border border-border px-2.5 py-1.5 text-xs text-ink-soft hover:bg-hover">
          <RefreshCw size={13} />  {tr("Atualizar")}
        </button>
      </div>
      <p className="text-[11px] text-muted">{total}  {tr("chamadas no período (mostrando")} {traces.length})</p>
      {loading ? <Loading /> : traces.length === 0 ? <p className="py-8 text-center text-sm text-muted">{tr("Nenhuma chamada encontrada.")}</p>
        : <TraceRows traces={traces} onOpen={onOpen} />}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tempo real
// ---------------------------------------------------------------------------

function RuntimePanel({ onTrace }: { onTrace: (id: string) => void }) {
  const [r, setR] = useState<Runtime | null>(null);
  useEffect(() => {
    let vivo = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const tick = async () => {
      try {
        const d = await api.get<Runtime>("/observability/runtime");
        if (vivo) setR(d);
      } catch { /* tenta de novo */ }
      if (vivo) timer = setTimeout(tick, 3000);
    };
    void tick();
    return () => { vivo = false; if (timer) clearTimeout(timer); };
  }, []);
  if (!r) return <Loading />;
  const l = r.loop_lag;
  const lagRuim = l["1m"].p95 >= 100;
  const pool = r.db_pool as { checkedout?: number; size?: number; overflow?: number };
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Stat label={tr("Atraso do event loop (1 min)")} value={`p95 ${fmtMs(l["1m"].p95)}`}
          sub={tr("máx {0} · 15 min: máx {1}", { "0": fmtMs(l["1m"].max), "1": fmtMs(l["15m"].max) })} icon={<Cpu size={13} />}
          hint={tr("Quanto o servidor demora para atender uma tarefa pronta. Alto = algo síncrono pesado travando TODAS as requisições.")} />
        <Stat label={tr("Rodando agora")} value={String(r.open_traces.length)} sub={tr("{0} gerações · {bg_tasks} em 2º plano", { "0": r.active_generations ?? 0, bg_tasks: r.bg_tasks })} icon={<Activity size={13} />} />
        <Stat label={tr("Banco (conexões)")} value={`${pool.checkedout ?? "?"} em uso`} sub={`pool ${pool.size ?? "?"} · extra ${pool.overflow ?? 0}`} icon={<Database size={13} />} />
        <Stat label={tr("Processo")} value={r.memory_mb != null ? `${r.memory_mb} MB` : "—"}
          sub={tr("{0} tarefas · CPU {1}s · no ar {2} min", { "0": r.tasks ?? "?", "1": r.cpu_s ?? "?", "2": Math.round(r.uptime_s / 60) })} icon={<Layers size={13} />} />
      </div>
      <div className={CARD}>
        <p className={`mb-1 text-xs font-medium ${lagRuim ? "text-amber-400" : "text-ink-soft"}`}>{tr("Atraso do event loop (últimos 60s)")}</p>
        <Sparkline values={r.lag_series.map((s) => s.ms)} h={40} />
        <p className="mt-1 text-[11px] text-muted">
          
          {tr("fila de gravação")} {r.sink.queued} · gravados {r.sink.written.toLocaleString(dateLocale())} · descartados {r.sink.dropped}
        </p>
      </div>
      <section>
        <p className="mb-2 text-xs font-medium text-ink-soft">{tr("Travadas recentes")}</p>
        {r.stalls.length === 0 ? <p className="rounded-xl border border-border px-3 py-3 text-xs text-muted">{tr("Nenhuma travada acima de 250ms desde que o servidor subiu.")}</p> : (
          <div className="space-y-1.5">
            {r.stalls.map((s) => (
              <div key={s.at} className="rounded-xl border border-border px-3 py-2 text-xs">
                <div className="flex items-center gap-2">
                  <AlertTriangle size={13} className={s.lag_ms >= 1000 ? "text-red-500" : "text-amber-400"} />
                  <span className="font-semibold tabular-nums text-ink">{fmtMs(s.lag_ms)}</span>
                  <span className="text-muted">{new Date(s.at * 1000).toLocaleTimeString(dateLocale())}</span>
                </div>
                {s.suspects.length > 0 && (
                  <div className="mt-1 space-y-0.5 pl-5">
                    {s.suspects.map((x) => (
                      <button key={x.trace} onClick={() => onTrace(x.trace)} className="block text-left text-muted hover:text-ink">
                        {x.name} <span className="font-mono">{x.last_span && tr("· última etapa {last_span}", { last_span: x.last_span })}</span>  {tr("· aberto há")} {fmtMs(x.age_ms)}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </section>
      <section>
        <p className="mb-2 text-xs font-medium text-ink-soft">{tr("Operações em andamento")}</p>
        {r.open_traces.length === 0 ? <p className="rounded-xl border border-border px-3 py-3 text-xs text-muted">{tr("Nada rodando agora.")}</p> : (
          <div className="overflow-hidden rounded-xl border border-border">
            {r.open_traces.map((t) => (
              <div key={t.id} className="flex items-center gap-2 border-b border-border/60 px-3 py-2 text-xs last:border-0">
                <KindChip kind={t.kind} />
                <span className="min-w-0 flex-1 truncate text-ink">{t.name}</span>
                {t.parent_trace && <GitBranch size={11} className="shrink-0 text-muted" />}
                <span className="hidden min-w-0 max-w-[40%] truncate font-mono text-[10px] text-muted sm:block">{t.last_span}</span>
                <span className="shrink-0 tabular-nums text-muted">{t.spans} etapas</span>
                <span className="w-16 shrink-0 text-right font-mono tabular-nums text-ink-soft"><Clock size={10} className="mr-1 inline" />{fmtMs(t.age_ms)}</span>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Painel
// ---------------------------------------------------------------------------

type Tab = "summary" | "ops" | "routes" | "traces" | "runtime";
const TABS: [Tab, string][] = [
  ["summary", tr("Resumo")], ["ops", tr("Gargalos")], ["routes", tr("Endpoints")], ["traces", tr("Chamadas")], ["runtime", tr("Tempo real")],
];

export default function ObservabilityView() {
  const [tab, setTab] = useState<Tab>("summary");
  const [hours, setHours] = useState(24);
  const [summary, setSummary] = useState<ObsSummary | null>(null);
  const [config, setConfig] = useState<ObsConfig | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);
  const [opName, setOpName] = useState<string | null>(null);
  const [route, setRoute] = useState<{ path: string; method: string; kind: string } | null>(null);

  const loadSummary = useCallback(async () => {
    const [s, c] = await Promise.all([
      api.get<ObsSummary>(`/observability/summary?hours=${hours}`),
      api.get<ObsConfig>("/observability/config"),
    ]);
    setSummary(s); setConfig(c);
  }, [hours]);

  useEffect(() => { if (tab === "summary" || tab === "routes") void loadSummary(); }, [tab, loadSummary]);

  const abrirRota = (r: RouteRow) => setRoute({ path: r.path, method: r.method, kind: r.kind });

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        {TABS.map(([t, label]) => (
          <button key={t} onClick={() => setTab(t)}
            className={`rounded-full px-3.5 py-1.5 text-sm transition-colors ${tab === t ? "bg-accent text-white" : "border border-border text-ink-soft hover:bg-hover"}`}>
            {label}
          </button>
        ))}
        {tab !== "runtime" && (
          <Select value={hours} onChange={(e) => setHours(Number(e.target.value))}
            className="ml-auto rounded-lg border border-border bg-surface px-2 py-1.5 text-xs text-ink outline-none">
            <option value={1}>{tr("Última hora")}</option>
            <option value={6}>6 horas</option>
            <option value={24}>24 horas</option>
            <option value={168}>7 dias</option>
            <option value={720}>30 dias</option>
          </Select>
        )}
        {config && (
          <span className={`${CHIP} ${tab === "runtime" ? "ml-auto" : ""} ${config.enabled ? "bg-emerald-500/15 text-emerald-500" : "bg-muted/15 text-muted"}`}
            title={`amostra ${(config.sample_rate * 100).toFixed(0)}% · retenção ${config.retention_days}d · fila ${config.sink.queued} · descartados ${config.sink.dropped}`}>
            <Clock size={11} className="mr-1 inline" />
            {config.enabled ? "captura ligada" : "desligada"}
          </span>
        )}
      </div>

      {tab === "summary" && (summary ? <SummaryPanel summary={summary} onRoute={abrirRota} /> : <Loading />)}
      {tab === "ops" && <OperationsPanel hours={hours} onOpen={setOpName} />}
      {tab === "routes" && (summary ? <RoutesTable routes={summary.routes as RouteRow[]} onOpen={abrirRota} /> : <Loading />)}
      {tab === "traces" && <TraceList hours={hours} onOpen={setOpenId} />}
      {tab === "runtime" && <RuntimePanel onTrace={setOpenId} />}

      {route && <RouteDetail route={route} hours={hours} onClose={() => setRoute(null)} onTrace={setOpenId} onOperation={setOpName} />}
      {opName && <OperationDetail name={opName} hours={hours} onClose={() => setOpName(null)} onTrace={setOpenId} />}
      {openId && <TraceDetail key={openId} traceId={openId} onClose={() => setOpenId(null)} />}
    </div>
  );
}
