"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { ArrowRight, Loader2, Play, Terminal, Wrench, Zap } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { streamToolTrace } from "@/lib/sse";
import type { Model, ModelConfig } from "@/lib/types";
import type { ToolCatalogItem } from "@/lib/playground";
import ModelField from "./ModelField";
import Markdown from "./Markdown";

function splitModel(v: string): { model: string; model_config_id: string | null } {
  return v.startsWith("custom:") ? { model: "", model_config_id: v.slice(7) } : { model: v, model_config_id: null };
}

function pretty(v: unknown): string {
  try { return JSON.stringify(v, null, 2); } catch { return String(v); }
}

// ------------------------------- Direto ---------------------------------- //
function DirectTab() {
  const [cat, setCat] = useState<{ builtins: ToolCatalogItem[]; user_tools: ToolCatalogItem[] } | null>(null);
  const [path, setPath] = useState("");
  const [paramsText, setParamsText] = useState("{}");
  const [result, setResult] = useState<unknown>(null);
  const [latency, setLatency] = useState<number | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.get<{ builtins: ToolCatalogItem[]; user_tools: ToolCatalogItem[] }>("/playground/tools").then(setCat).catch(() => {});
  }, []);

  const all = useMemo(() => [...(cat?.builtins || []), ...(cat?.user_tools || [])], [cat]);
  const selected = all.find((t) => t.path === path);

  function pick(p: string) {
    setPath(p);
    setResult(null);
    setErr(null);
    const t = all.find((x) => x.path === p);
    const keys = Object.keys(t?.params || {});
    setParamsText(keys.length ? pretty(Object.fromEntries(keys.map((k) => [k, ""]))) : "{}");
  }

  async function run() {
    setErr(null); setResult(null); setLatency(null);
    let params: Record<string, unknown>;
    try {
      params = JSON.parse(paramsText || "{}");
    } catch {
      setErr("JSON de parâmetros inválido"); return;
    }
    setBusy(true);
    try {
      const r = await api.post<{ result?: unknown; latency_ms?: number; error?: string }>("/playground/tool/dispatch", { path, params });
      if (r.error) setErr(r.error);
      else setResult(r.result);
      setLatency(r.latency_ms ?? null);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Falha ao executar");
    } finally { setBusy(false); }
  }

  return (
    <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
      <div className="rounded-2xl border border-border bg-surface p-4">
        <label className="block text-xs text-muted">Ferramenta
          <select value={path} onChange={(e) => pick(e.target.value)} className="mt-1 w-full rounded-lg border border-border bg-bg px-3 py-2 text-sm text-ink outline-none focus:border-accent">
            <option value="">Selecione…</option>
            {cat && (
              <>
                <optgroup label="Sistema">
                  {cat.builtins.map((t) => <option key={t.path} value={t.path}>{t.name} ({t.path})</option>)}
                </optgroup>
                {cat.user_tools.length > 0 && (
                  <optgroup label="Suas ferramentas">
                    {cat.user_tools.map((t) => <option key={t.path} value={t.path}>{t.name} ({t.path})</option>)}
                  </optgroup>
                )}
              </>
            )}
          </select>
        </label>
        {selected?.description && <p className="mt-2 text-xs text-muted">{selected.description}</p>}
        <label className="mt-3 block text-xs text-muted">Parâmetros (JSON)
          <textarea value={paramsText} onChange={(e) => setParamsText(e.target.value)} rows={7} spellCheck={false} className="mt-1 w-full rounded-lg border border-border bg-bg px-3 py-2 font-mono text-xs text-ink outline-none focus:border-accent" />
        </label>
        <button onClick={run} disabled={busy || !path} className="mt-3 flex items-center gap-1.5 rounded-lg bg-accent px-4 py-1.5 text-sm font-medium text-white transition-colors hover:bg-accent-hover disabled:opacity-50">
          {busy ? <Loader2 size={15} className="animate-spin" /> : <Play size={15} />} Executar
        </button>
        {err && <p className="mt-2 text-xs text-red-400">{err}</p>}
      </div>

      <div className="rounded-2xl border border-border bg-surface p-4">
        <div className="mb-2 flex items-center justify-between">
          <p className="flex items-center gap-1.5 text-sm font-medium text-ink"><Terminal size={15} /> Resultado</p>
          {latency !== null && <span className="flex items-center gap-1 text-[11px] text-muted"><Zap size={11} /> {latency} ms</span>}
        </div>
        {result !== null ? (
          <pre className="max-h-[420px] overflow-auto rounded-lg bg-bg p-3 font-mono text-xs text-ink">{pretty(result)}</pre>
        ) : (
          <p className="py-10 text-center text-sm text-muted">Escolha uma ferramenta e execute para ver o retorno cru.</p>
        )}
      </div>
    </div>
  );
}

// ------------------------------- Trace ----------------------------------- //
type TraceItem =
  | { kind: "call"; name: string; args: unknown }
  | { kind: "result"; name: string; result: unknown }
  | { kind: "notice"; message: string };

function TraceTab() {
  const [ext, setExt] = useState<Model[]>([]);
  const [custom, setCustom] = useState<ModelConfig[]>([]);
  const [model, setModel] = useState("");
  const [prompt, setPrompt] = useState("");
  const [items, setItems] = useState<TraceItem[]>([]);
  const [answer, setAnswer] = useState("");
  const [running, setRunning] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [started, setStarted] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    api.get<ModelConfig[]>("/models").then(setCustom).catch(() => {});
    Promise.all([
      api.get<Model[]>("/settings/models").catch(() => [] as Model[]),
      api.get<Model[]>("/integrations/ollama/models").catch(() => [] as Model[]),
      api.get<Model[]>("/integrations/subscriptions/chatgpt/models").catch(() => [] as Model[]),
    ]).then(([e, l, s]) => setExt([...e, ...l, ...s])).catch(() => {});
    return () => abortRef.current?.abort();
  }, []);

  async function run() {
    setErr(null);
    if (!model) { setErr("Escolha um modelo."); return; }
    if (!prompt.trim()) { setErr("Escreva um prompt."); return; }
    setRunning(true); setItems([]); setAnswer(""); setStarted(true);
    const ac = new AbortController();
    abortRef.current = ac;
    const m = splitModel(model);
    try {
      await streamToolTrace({ model: m.model, model_config_id: m.model_config_id, prompt }, (raw) => {
        const e = raw as { type: string; name?: string; arguments?: unknown; result?: unknown; text?: string; content?: string; message?: string };
        if (e.type === "tool_call") setItems((it) => [...it, { kind: "call", name: e.name || "?", args: e.arguments }]);
        else if (e.type === "tool_result") setItems((it) => [...it, { kind: "result", name: e.name || "?", result: e.result }]);
        else if (e.type === "notice") setItems((it) => [...it, { kind: "notice", message: e.message || "" }]);
        else if (e.type === "token") setAnswer((a) => a + (e.text || ""));
        else if (e.type === "done") { if (e.content) setAnswer(e.content); }
        else if (e.type === "error") setErr(e.message || "Falha no trace");
      }, ac.signal);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Falha no trace");
    } finally { setRunning(false); }
  }

  return (
    <div>
      <div className="rounded-2xl border border-border bg-surface p-4">
        <div className="text-xs text-muted">Modelo (use um preset com ferramentas ativas)</div>
        <ModelField models={ext} custom={custom} includeCustom value={model} onChange={setModel} className="mt-1 flex w-full items-center justify-between gap-2 rounded-lg border border-border bg-bg px-3 py-2 text-sm text-ink outline-none transition-colors hover:border-accent/50" />
        <textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} rows={3} placeholder="Prompt que force o uso de uma ferramenta (ex.: 'que horas são em Tóquio?')" className="mt-2 w-full rounded-lg border border-border bg-bg px-3 py-2 text-sm text-ink outline-none focus:border-accent placeholder:text-muted" />
        {err && <p className="mt-2 text-xs text-red-400">{err}</p>}
        <button onClick={run} disabled={running} className="mt-3 flex items-center gap-1.5 rounded-lg bg-accent px-4 py-1.5 text-sm font-medium text-white transition-colors hover:bg-accent-hover disabled:opacity-50">
          {running ? <Loader2 size={15} className="animate-spin" /> : <Play size={15} />} Rodar
        </button>
      </div>

      {started && (
        <div className="mt-4 space-y-2">
          <p className="text-xs font-medium uppercase tracking-wide text-muted">Linha do tempo</p>
          {items.length === 0 && !running && <p className="rounded-xl border border-dashed border-border px-4 py-6 text-center text-sm text-muted">Nenhuma ferramenta foi chamada neste turno.</p>}
          {items.map((it, i) => (
            <div key={i} className="rounded-xl border border-border bg-surface p-3">
              {it.kind === "call" && (
                <>
                  <p className="flex items-center gap-1.5 text-xs font-medium text-accent-hover"><Wrench size={12} /> chamada · {it.name}</p>
                  <pre className="mt-1.5 overflow-auto rounded-lg bg-bg p-2 font-mono text-[11px] text-ink">{pretty(it.args)}</pre>
                </>
              )}
              {it.kind === "result" && (
                <>
                  <p className="flex items-center gap-1.5 text-xs font-medium text-green-500"><ArrowRight size={12} /> resultado · {it.name}</p>
                  <pre className="mt-1.5 max-h-64 overflow-auto rounded-lg bg-bg p-2 font-mono text-[11px] text-ink">{pretty(it.result)}</pre>
                </>
              )}
              {it.kind === "notice" && <p className="text-xs text-muted">{it.message}</p>}
            </div>
          ))}
          {running && <span className="flex items-center gap-1.5 text-sm text-muted"><Loader2 size={13} className="animate-spin" /> processando…</span>}
          {answer && (
            <div className="mt-3 rounded-xl border border-border bg-surface p-3">
              <p className="mb-1.5 text-xs font-medium uppercase tracking-wide text-muted">Resposta final</p>
              <div className="text-sm"><Markdown content={answer} /></div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function ToolDebugView() {
  const [tab, setTab] = useState<"trace" | "direct">("direct");
  return (
    <div>
      <div className="mb-4 flex items-center gap-1 rounded-lg border border-border bg-surface p-1 text-sm w-fit">
        <button onClick={() => setTab("direct")} className={`rounded-md px-3 py-1 transition-colors ${tab === "direct" ? "bg-surface2 font-medium text-ink" : "text-muted hover:text-ink"}`}>Direto</button>
        <button onClick={() => setTab("trace")} className={`rounded-md px-3 py-1 transition-colors ${tab === "trace" ? "bg-surface2 font-medium text-ink" : "text-muted hover:text-ink"}`}>Trace de turno</button>
      </div>
      {tab === "direct" ? <DirectTab /> : <TraceTab />}
    </div>
  );
}
