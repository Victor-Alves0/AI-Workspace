"use client";

/**
 * Painel de Saúde (admin): a COMPETÊNCIA do sistema, não a latência (isso é a
 * Observabilidade). Mostra o self-check de infra (banco, pgvector) e o snapshot das
 * capacidades nas últimas 24h — quando uma degradou (mem0 em no-op, síntese caindo
 * p/ a camada C, watchdog abortando tool, codegraph truncando por deadline).
 */

import { useCallback, useEffect, useState } from "react";
import { Activity, HeartPulse, RefreshCw } from "lucide-react";
import { api } from "@/lib/api";

type Sev = "info" | "warn" | "degraded" | "error";

interface Capability {
  capability: string;
  counts: Record<string, number>;
  worst: Sev;
  latest?: { event: string; severity: Sev; detail: Record<string, unknown>; at: string | null };
}
interface HealthResp {
  ok: boolean;
  components: Record<string, { ok: boolean; error?: string }>;
  health: { ok: boolean; hours: number; capabilities: Capability[]; error?: string };
}
interface Primitive {
  primitive: string;
  total: number;
  events: Record<string, number>;
  degraded?: number;
}
interface PrimResp { days: number; primitives: Primitive[] }

const SEV_COLOR: Record<Sev, string> = {
  info: "#64748b", warn: "#f59e0b", degraded: "#f97316", error: "#ef4444",
};
const SEV_LABEL: Record<Sev, string> = {
  info: "info", warn: "atenção", degraded: "degradado", error: "erro",
};
const CAP_LABEL: Record<string, string> = {
  memory: "Memória (mem0)", synthesis: "Síntese final", tool_watchdog: "Watchdog de tools",
  codegraph: "Grafo de código", output_guard: "Guarda de saída", guard_judge: "Guarda-juiz",
  compaction: "Compactação", database: "Banco", embedder: "Embedder", browser: "Navegador",
  steering: "Steering (intervenção)", anti_spin: "Anti-spin", queue: "Fila de mensagens",
  ledger: "Ledger de tarefa",
};

const CARD = "rounded-xl border border-border bg-surface p-4";
const CHIP = "rounded-full px-2.5 py-0.5 text-[11px] font-medium";

function fmtTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" });
}

export default function HealthView() {
  const [data, setData] = useState<HealthResp | null>(null);
  const [prims, setPrims] = useState<PrimResp | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");

  const load = useCallback(() => {
    setLoading(true); setErr("");
    Promise.all([
      api.get<HealthResp>("/debug/health").then((d) => setData(d)),
      api.get<PrimResp>("/debug/primitives?days=7").then((p) => setPrims(p)).catch(() => setPrims(null)),
    ])
      .catch((e) => setErr(String(e?.message || e)))
      .finally(() => setLoading(false));
  }, []);
  useEffect(() => { load(); }, [load]);

  const caps = data?.health?.capabilities ?? [];
  const healthy = !!data?.ok;

  return (
    <div className="space-y-4">
      {/* topo: estado geral + recarregar */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <HeartPulse size={18} className={healthy ? "text-emerald-500" : "text-orange-500"} />
          <span className="text-sm font-medium text-ink">
            {data ? (healthy ? "Tudo saudável" : "Há capacidades degradadas") : "Carregando…"}
          </span>
        </div>
        <button onClick={load} disabled={loading}
          className="flex items-center gap-1.5 rounded-lg border border-border px-2.5 py-1.5 text-xs text-muted transition-colors hover:bg-hover hover:text-ink disabled:opacity-50">
          <RefreshCw size={13} className={loading ? "animate-spin" : ""} /> Recarregar
        </button>
      </div>

      {err && <p className="text-sm text-red-500">{err}</p>}

      {/* infra (self-check): banco, pgvector */}
      {data && (
        <div className={CARD}>
          <p className="mb-2.5 text-xs font-semibold uppercase tracking-wide text-muted">Infraestrutura</p>
          <div className="flex flex-wrap gap-2">
            {Object.entries(data.components).map(([name, c]) => (
              <span key={name} className={`${CHIP} flex items-center gap-1.5`}
                style={{ background: c.ok ? "#22c55e22" : "#ef444422", color: c.ok ? "#16a34a" : "#dc2626" }}
                title={c.error || ""}>
                <span className="inline-block h-1.5 w-1.5 rounded-full" style={{ background: c.ok ? "#22c55e" : "#ef4444" }} />
                {name} {c.ok ? "ok" : "falha"}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* capacidades (últimas 24h) */}
      {data && (
        <div className={CARD}>
          <p className="mb-3 text-xs font-semibold uppercase tracking-wide text-muted">
            Capacidades · últimas {data.health?.hours ?? 24}h
          </p>
          {caps.length === 0 ? (
            <div className="flex items-center gap-2 py-3 text-sm text-muted">
              <Activity size={15} className="text-emerald-500" />
              Nenhuma degradação registrada — todas as capacidades operando normalmente.
            </div>
          ) : (
            <ul className="divide-y divide-border">
              {caps.map((c) => (
                <li key={c.capability} className="flex items-start gap-3 py-2.5">
                  <span className={`${CHIP} mt-0.5 shrink-0`}
                    style={{ background: `${SEV_COLOR[c.worst]}22`, color: SEV_COLOR[c.worst] }}>
                    {SEV_LABEL[c.worst]}
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="text-sm text-ink">{CAP_LABEL[c.capability] ?? c.capability}</p>
                    {c.latest && (
                      <p className="truncate text-[11px] text-muted">
                        {c.latest.event}
                        {typeof c.latest.detail?.reason === "string" ? ` · ${c.latest.detail.reason}` : ""}
                        {typeof c.latest.detail?.tool === "string" ? ` · ${c.latest.detail.tool}` : ""}
                        {" · "}{fmtTime(c.latest.at)}
                      </p>
                    )}
                  </div>
                  <div className="flex shrink-0 flex-wrap justify-end gap-1">
                    {Object.entries(c.counts).map(([sev, n]) => (
                      <span key={sev} className="rounded-md bg-surface2 px-1.5 py-0.5 text-[10px] tabular-nums"
                        style={{ color: SEV_COLOR[sev as Sev] ?? "#64748b" }}>
                        {sev} {n}
                      </span>
                    ))}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {/* medição de primitivos (últimos 7 dias) — uso + desfecho */}
      {prims && (
        <div className={CARD}>
          <p className="mb-3 text-xs font-semibold uppercase tracking-wide text-muted">
            Primitivos · últimos {prims.days} dias
          </p>
          {prims.primitives.length === 0 ? (
            <p className="py-2 text-sm text-muted">Nenhum primitivo acionado no período.</p>
          ) : (
            <ul className="divide-y divide-border">
              {prims.primitives.map((p) => (
                <li key={p.primitive} className="flex items-start gap-3 py-2.5">
                  <span className="mt-0.5 w-8 shrink-0 text-right font-mono text-sm tabular-nums text-ink">{p.total}</span>
                  <div className="min-w-0 flex-1">
                    <p className="text-sm text-ink">
                      {CAP_LABEL[p.primitive] ?? p.primitive}
                      {p.degraded ? (
                        <span className="ml-2 rounded-md bg-orange-500/15 px-1.5 py-0.5 text-[10px] text-orange-500">
                          {p.degraded} fallback
                        </span>
                      ) : null}
                    </p>
                    <div className="mt-0.5 flex flex-wrap gap-1">
                      {Object.entries(p.events).sort((a, b) => b[1] - a[1]).map(([ev, n]) => (
                        <span key={ev} className="rounded-md bg-surface2 px-1.5 py-0.5 text-[10px] text-muted tabular-nums">
                          {ev} {n}
                        </span>
                      ))}
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
