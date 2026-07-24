"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Gauge, Loader2, Plus, Save, Trophy, X, Zap } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { streamCompare } from "@/lib/sse";
import type { Model, ModelConfig } from "@/lib/types";
import ModelField from "./ModelField";
import Markdown from "./Markdown";

function splitModel(v: string): { model: string; model_config_id: string | null } {
  return v.startsWith("custom:") ? { model: "", model_config_id: v.slice(7) } : { model: v, model_config_id: null };
}

interface Col {
  label: string;
  model: string;
  text: string;
  done: boolean;
  error?: string;
  latency_ms?: number;
  prompt_tokens?: number;
  completion_tokens?: number;
  cost?: number | null;
}

function fmtCost(c?: number | null): string {
  if (c === null || c === undefined) return "—";
  return c < 0.01 ? `$${c.toFixed(5)}` : `$${c.toFixed(4)}`;
}

export default function CompareView() {
  const [ext, setExt] = useState<Model[]>([]);
  const [custom, setCustom] = useState<ModelConfig[]>([]);
  const [slots, setSlots] = useState<string[]>(["", ""]);
  const [prompt, setPrompt] = useState("");
  const [system, setSystem] = useState("");
  const [showSystem, setShowSystem] = useState(false);
  const [cols, setCols] = useState<Col[]>([]);
  const [running, setRunning] = useState(false);
  const [err, setErr] = useState<string | null>(null);
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

  const chosen = useMemo(() => slots.filter((s) => s), [slots]);

  async function run() {
    setErr(null);
    if (chosen.length < 2) { setErr("Escolha ao menos 2 modelos."); return; }
    if (!prompt.trim()) { setErr("Escreva um prompt."); return; }
    setRunning(true);
    setCols([]);
    const ac = new AbortController();
    abortRef.current = ac;
    const models = chosen.map((s) => splitModel(s));
    try {
      await streamCompare({ models, prompt, system }, (raw) => {
        const e = raw as { type: string; col?: number; text?: string; cols?: { label: string; model: string }[]; message?: string; latency_ms?: number; prompt_tokens?: number; completion_tokens?: number; cost?: number | null };
        if (e.type === "cols" && e.cols) {
          setCols(e.cols.map((c) => ({ label: c.label, model: c.model, text: "", done: false })));
        } else if (e.type === "delta" && typeof e.col === "number") {
          setCols((cs) => cs.map((c, i) => (i === e.col ? { ...c, text: c.text + (e.text || "") } : c)));
        } else if (e.type === "done" && typeof e.col === "number") {
          setCols((cs) => cs.map((c, i) => (i === e.col ? { ...c, done: true, latency_ms: e.latency_ms, prompt_tokens: e.prompt_tokens, completion_tokens: e.completion_tokens, cost: e.cost } : c)));
        } else if (e.type === "error" && typeof e.col === "number") {
          setCols((cs) => cs.map((c, i) => (i === e.col ? { ...c, done: true, error: e.message } : c)));
        } else if (e.type === "error") {
          setErr(e.message || "Falha na comparação");
        }
      }, ac.signal);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Falha na comparação");
    } finally {
      setRunning(false);
    }
  }

  // selos: mais rápido / mais barato entre as colunas concluídas sem erro
  const doneCols = cols.filter((c) => c.done && !c.error);
  const fastest = doneCols.length > 1 ? doneCols.reduce((a, b) => ((b.latency_ms ?? 1e12) < (a.latency_ms ?? 1e12) ? b : a)) : null;
  const cheapest = doneCols.filter((c) => typeof c.cost === "number");
  const cheap = cheapest.length > 1 ? cheapest.reduce((a, b) => ((b.cost ?? 1e12) < (a.cost ?? 1e12) ? b : a)) : null;

  async function saveAsBenchmark() {
    try {
      const { id } = await api.post<{ id: string }>("/playground/benchmarks", {
        name: `Comparação: ${prompt.slice(0, 40)}`,
        cases: [{ prompt, ...(system ? { system } : {}) }],
      });
      alert(`Salvo como benchmark. Abra em Benchmarks para rodar.\nID: ${id}`);
    } catch {
      setErr("Falha ao salvar como benchmark");
    }
  }

  const gridCols = cols.length >= 3 ? "md:grid-cols-3" : cols.length === 2 ? "md:grid-cols-2" : "md:grid-cols-1";

  return (
    <div>
      <div className="rounded-2xl border border-border bg-surface p-4">
        <div className="mb-3 space-y-2">
          {slots.map((s, i) => (
            <div key={i} className="flex items-center gap-2">
              <ModelField models={ext} custom={custom} includeCustom value={s} onChange={(v) => setSlots((sl) => sl.map((x, j) => (j === i ? v : x)))} className="flex w-full items-center justify-between gap-2 rounded-lg border border-border bg-bg px-3 py-2 text-sm text-ink outline-none transition-colors hover:border-accent/50" />
              {slots.length > 2 && (
                <button onClick={() => setSlots((sl) => sl.filter((_, j) => j !== i))} className="rounded-lg p-2 text-muted hover:bg-hover hover:text-red-400"><X size={15} /></button>
              )}
            </div>
          ))}
          {slots.length < 4 && (
            <button onClick={() => setSlots((sl) => [...sl, ""])} className="flex items-center gap-1.5 rounded-lg px-2 py-1 text-xs text-muted transition-colors hover:text-ink">
              <Plus size={14} /> Adicionar modelo
            </button>
          )}
        </div>

        <textarea
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          rows={3}
          placeholder="Prompt para enviar a todos os modelos…"
          className="w-full rounded-lg border border-border bg-bg px-3 py-2 text-sm text-ink outline-none focus:border-accent placeholder:text-muted"
        />
        {showSystem ? (
          <textarea
            value={system}
            onChange={(e) => setSystem(e.target.value)}
            rows={2}
            placeholder="System prompt (opcional, aplicado a todos)…"
            className="mt-2 w-full rounded-lg border border-border bg-bg px-3 py-2 text-sm text-ink outline-none focus:border-accent placeholder:text-muted"
          />
        ) : (
          <button onClick={() => setShowSystem(true)} className="mt-2 text-xs text-muted hover:text-ink">+ System prompt</button>
        )}

        {err && <p className="mt-2 text-xs text-red-400">{err}</p>}

        <div className="mt-3 flex items-center gap-2">
          <button onClick={run} disabled={running} className="flex items-center gap-1.5 rounded-lg bg-accent px-4 py-1.5 text-sm font-medium text-white transition-colors hover:bg-accent-hover disabled:opacity-50">
            {running ? <Loader2 size={15} className="animate-spin" /> : <Zap size={15} />} Rodar
          </button>
          {cols.length > 0 && !running && prompt && (
            <button onClick={saveAsBenchmark} className="flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-sm text-ink-soft transition-colors hover:bg-hover">
              <Save size={14} /> Salvar como benchmark
            </button>
          )}
        </div>
      </div>

      {cols.length > 0 && (
        <div className={`mt-4 grid grid-cols-1 gap-3 ${gridCols}`}>
          {cols.map((c, i) => (
            <div key={i} className="flex flex-col rounded-2xl border border-border bg-surface">
              <div className="flex items-center justify-between gap-2 border-b border-border px-3 py-2">
                <p className="truncate text-sm font-medium text-ink" title={c.model}>{c.label}</p>
                <div className="flex shrink-0 items-center gap-1">
                  {fastest === c && <span title="Mais rápido" className="flex items-center gap-0.5 rounded-full bg-accent/15 px-1.5 py-0.5 text-[10px] font-medium text-accent-hover"><Zap size={10} /> rápido</span>}
                  {cheap === c && <span title="Mais barato" className="flex items-center gap-0.5 rounded-full bg-green-500/15 px-1.5 py-0.5 text-[10px] font-medium text-green-500"><Trophy size={10} /> barato</span>}
                </div>
              </div>
              <div className="min-h-[120px] flex-1 overflow-y-auto px-3 py-2 text-sm">
                {c.error ? (
                  <p className="text-red-400">{c.error}</p>
                ) : c.text ? (
                  <Markdown content={c.text} />
                ) : (
                  <span className="flex items-center gap-1.5 text-muted"><Loader2 size={13} className="animate-spin" /> gerando…</span>
                )}
              </div>
              {c.done && !c.error && (
                <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-border px-3 py-1.5 text-[11px] text-muted">
                  <span className="flex items-center gap-1"><Gauge size={11} /> {c.latency_ms ?? "—"} ms</span>
                  <span>{(c.prompt_tokens ?? 0) + (c.completion_tokens ?? 0)} tok</span>
                  <span>{fmtCost(c.cost)}</span>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
