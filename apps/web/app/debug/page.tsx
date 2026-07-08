"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { api, ApiError } from "@/lib/api";

type Json = Record<string, any>;

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-border bg-surface p-4">
      <h3 className="mb-3 text-sm font-semibold uppercase tracking-wide text-muted">{title}</h3>
      {children}
    </div>
  );
}

function Dot({ ok }: { ok: boolean }) {
  return (
    <span className={`inline-block h-2.5 w-2.5 rounded-full ${ok ? "bg-green-400" : "bg-red-400"}`} />
  );
}

export default function DebugPage() {
  const router = useRouter();
  const [denied, setDenied] = useState(false);
  const [info, setInfo] = useState<Json | null>(null);
  const [health, setHealth] = useState<Json | null>(null);
  const [metrics, setMetrics] = useState<Json | null>(null);
  const [providers, setProviders] = useState<Json | null>(null);
  const [sift, setSift] = useState<Json | null>(null);
  const [logs, setLogs] = useState<Json[]>([]);
  const [level, setLevel] = useState<string>("");
  const [auto, setAuto] = useState(true);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  const loadStatic = useCallback(() => {
    api.get<Json>("/debug/info").then(setInfo).catch((e) => {
      if (e instanceof ApiError && e.status === 403) setDenied(true);
      if (e instanceof ApiError && e.status === 401) router.replace("/login");
    });
    api.get<Json>("/debug/health").then(setHealth).catch(() => {});
    api.get<Json>("/debug/providers").then(setProviders).catch(() => {});
    api.get<Json>("/debug/sift").then(setSift).catch(() => {});
  }, [router]);

  const loadLive = useCallback(() => {
    api.get<Json>("/debug/metrics").then(setMetrics).catch(() => {});
    api
      .get<{ logs: Json[] }>(`/debug/logs?limit=150${level ? `&level=${level}` : ""}`)
      .then((r) => setLogs(r.logs))
      .catch(() => {});
  }, [level]);

  useEffect(() => {
    loadStatic();
    loadLive();
  }, [loadStatic, loadLive]);

  useEffect(() => {
    if (timer.current) clearInterval(timer.current);
    if (auto) timer.current = setInterval(loadLive, 4000);
    return () => {
      if (timer.current) clearInterval(timer.current);
    };
  }, [auto, loadLive]);

  if (denied) {
    return (
      <div className="flex h-screen flex-col items-center justify-center gap-3 text-muted">
        <p>Acesso negado — o painel de debug é apenas para admin.</p>
        <button onClick={() => router.push("/chat")} className="text-accent">← Voltar ao chat</button>
      </div>
    );
  }

  return (
    <div className="h-screen overflow-y-auto bg-bg p-6">
      <div className="mx-auto max-w-5xl space-y-4">
        <div className="flex items-center justify-between">
          <h1 className="text-xl font-semibold">Painel de Debug</h1>
          <div className="flex items-center gap-3 text-sm">
            <label className="flex items-center gap-2 text-muted">
              <input type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)} />
              Auto-refresh
            </label>
            <button onClick={() => { loadStatic(); loadLive(); }} className="rounded-lg border border-border px-3 py-1.5 hover:border-accent">
              Atualizar
            </button>
            <button onClick={() => router.push("/chat")} className="text-muted hover:text-ink-soft">← Chat</button>
          </div>
        </div>

        <div className="grid grid-cols-2 gap-4">
          <Section title="Sistema">
            {info ? (
              <div className="space-y-1 text-sm">
                <Row k="Versão" v={info.version} />
                <Row k="Ambiente" v={info.app_env} />
                <Row k="Python" v={info.python} />
                <Row k="Plataforma" v={info.platform} />
                <div className="flex items-center justify-between">
                  <span className="text-muted">APP_SECRET seguro</span>
                  <Dot ok={!info.secret_insecure} />
                </div>
                <Row k="Provider de busca" v={info.web_search_provider} />
                <Row k="Sandbox timeout" v={`${info.tool_sandbox?.timeout_s}s / ${info.tool_sandbox?.cpu_s}s CPU / ${info.tool_sandbox?.mem_mb}MB`} />
              </div>
            ) : (
              <p className="text-sm text-muted">…</p>
            )}
          </Section>

          <Section title="Saúde">
            {health ? (
              <div className="space-y-2 text-sm">
                <div className="flex items-center gap-2">
                  <Dot ok={health.ok} />
                  <span>{health.ok ? "Tudo ok" : "Problemas detectados"}</span>
                </div>
                {Object.entries(health.components ?? {}).map(([k, v]: [string, any]) => (
                  <div key={k} className="flex items-center justify-between">
                    <span className="text-muted">{k}</span>
                    <span className="flex items-center gap-2">
                      <Dot ok={v.ok} />
                      {v.error && <span className="text-xs text-red-400">{v.error.slice(0, 40)}</span>}
                    </span>
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-sm text-muted">…</p>
            )}
          </Section>

          <Section title="Provedores">
            {providers ? (
              <div className="space-y-2 text-sm">
                {Object.entries(providers).map(([k, v]: [string, any]) => (
                  <div key={k} className="flex items-center justify-between">
                    <span className="text-muted">{k}</span>
                    <span className="flex items-center gap-2 text-xs">
                      {"key_configured" in v && (
                        <span className={v.key_configured ? "text-green-400" : "text-muted"}>
                          {v.key_configured ? "chave ✓" : "sem chave"}
                        </span>
                      )}
                      {"reachable" in v && <Dot ok={v.reachable} />}
                      {v.status && <span className="text-muted">{v.status}</span>}
                    </span>
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-sm text-muted">…</p>
            )}
          </Section>

          <Section title="SIFT (tools por usuário)">
            {sift ? (
              <div className="space-y-1 text-sm">
                <Row k="Usuários em cache" v={sift.cached_users} />
                {(sift.instances ?? []).map((i: any) => (
                  <div key={i.user_id} className="flex items-center justify-between text-xs">
                    <span className="font-mono text-muted">{i.user_id.slice(0, 8)}…</span>
                    <span className="flex items-center gap-2">
                      <Dot ok={i.ready} /> {i.meta_tools} meta-tools
                    </span>
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-sm text-muted">…</p>
            )}
          </Section>
        </div>

        <Section title="Métricas de Request">
          {metrics ? (
            <div className="space-y-2 text-sm">
              <div className="flex gap-6 text-xs text-muted">
                <span>uptime: {metrics.uptime_seconds}s</span>
                <span>total: {metrics.total_requests}</span>
                <span className={metrics.error_requests ? "text-red-400" : ""}>
                  erros 5xx: {metrics.error_requests}
                </span>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-left text-xs">
                  <thead className="text-muted">
                    <tr>
                      <th className="py-1">Rota</th>
                      <th>Reqs</th>
                      <th>Média</th>
                      <th>Erros</th>
                    </tr>
                  </thead>
                  <tbody className="font-mono">
                    {(metrics.routes ?? []).slice(0, 12).map((r: any) => (
                      <tr key={r.route} className="border-t border-border">
                        <td className="py-1">{r.route}</td>
                        <td>{r.count}</td>
                        <td>{r.avg_ms}ms</td>
                        <td className={r.errors ? "text-red-400" : ""}>{r.errors}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          ) : (
            <p className="text-sm text-muted">…</p>
          )}
        </Section>

        <Section title="Logs recentes">
          <div className="mb-2 flex gap-2 text-xs">
            {["", "INFO", "WARNING", "ERROR"].map((l) => (
              <button
                key={l || "all"}
                onClick={() => setLevel(l)}
                className={`rounded px-2 py-1 ${level === l ? "bg-accent text-ink" : "border border-border text-muted"}`}
              >
                {l || "todos"}
              </button>
            ))}
          </div>
          <div className="max-h-80 overflow-y-auto rounded-lg bg-surface2 p-2 font-mono text-xs">
            {logs.length === 0 && <p className="text-muted">sem logs</p>}
            {logs.map((l, i) => (
              <div key={i} className="border-b border-border/40 py-0.5">
                <span
                  className={
                    l.level === "ERROR"
                      ? "text-red-400"
                      : l.level === "WARNING"
                        ? "text-amber-400"
                        : "text-muted"
                  }
                >
                  [{l.level}]
                </span>{" "}
                <span className="text-gray-400">{l.logger}</span> {l.message}
                {l.exc && <pre className="whitespace-pre-wrap text-red-300/80">{l.exc}</pre>}
              </div>
            ))}
          </div>
        </Section>
      </div>
    </div>
  );
}

function Row({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-muted">{k}</span>
      <span className="font-mono text-xs">{v}</span>
    </div>
  );
}
