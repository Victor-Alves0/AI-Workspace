"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowLeft, BarChart3, Check, ChevronDown, ChevronRight, Clock, Loader2, Pencil,
  Plus, Trash2, X, Zap,
} from "lucide-react";
import { api, ApiError } from "@/lib/api";
import type { Model, ModelConfig } from "@/lib/types";
import type { BenchmarkCase, BenchmarkDetail, BenchmarkRun, BenchmarkSummary, RunCell } from "@/lib/playground";
import { cellKey } from "@/lib/playground";
import ModelField from "./ModelField";
import { useConfirm } from "@/components/ConfirmDialog";

function splitModel(v: string): { model: string; model_config_id: string | null } {
  return v.startsWith("custom:") ? { model: "", model_config_id: v.slice(7) } : { model: v, model_config_id: null };
}
function fmtCost(c?: number | null): string {
  if (c === null || c === undefined) return "—";
  return c < 0.01 ? `$${c.toFixed(5)}` : `$${c.toFixed(4)}`;
}
const inputCls = "mt-1 w-full rounded-lg border border-border bg-bg px-3 py-1.5 text-sm text-ink outline-none focus:border-accent placeholder:text-muted";

// --------------------------- Editor de benchmark ------------------------- //
function emptyCase(): BenchmarkCase {
  return { prompt: "", expected: { mode: "none", value: "" }, judge_criteria: "" };
}

function BenchmarkEditor({ id, onBack, onSaved }: { id: string | null; onBack: () => void; onSaved: () => void }) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [cases, setCases] = useState<BenchmarkCase[]>([emptyCase()]);
  const [judge, setJudge] = useState("");
  const [ext, setExt] = useState<Model[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([
      api.get<Model[]>("/settings/models").catch(() => [] as Model[]),
      api.get<Model[]>("/integrations/ollama/models").catch(() => [] as Model[]),
    ]).then(([e, l]) => setExt([...e, ...l])).catch(() => {});
    if (id) {
      api.get<BenchmarkDetail>(`/playground/benchmarks/${id}`).then((b) => {
        setName(b.name); setDescription(b.description);
        setCases((b.cases || []).map((c) => ({ ...emptyCase(), ...c, expected: c.expected || { mode: "none", value: "" } })));
        setJudge(b.judge_model || "");
      }).catch(() => {});
    }
  }, [id]);

  function patchCase(i: number, patch: Partial<BenchmarkCase>) {
    setCases((cs) => cs.map((c, j) => (j === i ? { ...c, ...patch } : c)));
  }

  async function save() {
    setErr(null);
    if (!name.trim()) { setErr("Dê um nome ao benchmark."); return; }
    if (!cases.some((c) => c.prompt.trim())) { setErr("Adicione ao menos um caso com prompt."); return; }
    setBusy(true);
    const body = {
      name, description,
      judge_model: judge || null,
      cases: cases.filter((c) => c.prompt.trim()),
    };
    try {
      if (id) await api.patch(`/playground/benchmarks/${id}`, body);
      else await api.post("/playground/benchmarks", body);
      onSaved();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Falha ao salvar");
    } finally { setBusy(false); }
  }

  return (
    <div>
      <button onClick={onBack} className="mb-3 flex items-center gap-1 text-sm text-muted hover:text-ink"><ArrowLeft size={16} /> Voltar</button>
      <div className="space-y-3 rounded-2xl border border-border bg-surface p-4">
        <label className="block text-xs text-muted">Nome
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Ex.: Perguntas de conhecimento geral" className={inputCls} />
        </label>
        <label className="block text-xs text-muted">Descrição (opcional)
          <input value={description} onChange={(e) => setDescription(e.target.value)} className={inputCls} />
        </label>
        <div className="text-xs text-muted">Modelo-juiz (opcional — pontua 0-100 por critério)
          <ModelField models={ext} value={judge} onChange={setJudge} placeholder="Sem juiz" className="mt-1 flex w-full items-center justify-between gap-2 rounded-lg border border-border bg-bg px-3 py-1.5 text-sm text-ink outline-none transition-colors hover:border-accent/50" />
        </div>
      </div>

      <div className="mt-4 space-y-3">
        <div className="flex items-center justify-between">
          <p className="text-sm font-medium text-ink">Casos de teste ({cases.length})</p>
          <button onClick={() => setCases((cs) => [...cs, emptyCase()])} className="flex items-center gap-1 text-xs text-accent-hover hover:underline"><Plus size={13} /> Adicionar caso</button>
        </div>
        {cases.map((c, i) => (
          <div key={i} className="space-y-2 rounded-2xl border border-border bg-surface p-3">
            <div className="flex items-start justify-between gap-2">
              <span className="mt-1 text-xs font-medium text-muted">#{i + 1}</span>
              {cases.length > 1 && <button onClick={() => setCases((cs) => cs.filter((_, j) => j !== i))} className="rounded p-1 text-muted hover:text-red-400"><Trash2 size={14} /></button>}
            </div>
            <textarea value={c.prompt} onChange={(e) => patchCase(i, { prompt: e.target.value })} rows={2} placeholder="Prompt do caso" className={inputCls} />
            <input value={c.system || ""} onChange={(e) => patchCase(i, { system: e.target.value })} placeholder="System prompt do caso (opcional)" className={inputCls} />
            <div className="flex flex-wrap items-center gap-2">
              <select value={c.expected?.mode || "none"} onChange={(e) => patchCase(i, { expected: { mode: e.target.value as "none" | "contains" | "regex", value: c.expected?.value || "" } })} className="rounded-lg border border-border bg-bg px-2 py-1.5 text-xs text-ink outline-none focus:border-accent">
                <option value="none">Sem regra</option>
                <option value="contains">Deve conter</option>
                <option value="regex">Regex</option>
              </select>
              {c.expected && c.expected.mode !== "none" && (
                <input value={c.expected.value} onChange={(e) => patchCase(i, { expected: { mode: c.expected!.mode, value: e.target.value } })} placeholder={c.expected.mode === "regex" ? "expressão regular" : "texto esperado"} className="flex-1 rounded-lg border border-border bg-bg px-3 py-1.5 text-sm text-ink outline-none focus:border-accent" />
              )}
            </div>
            <input value={c.judge_criteria || ""} onChange={(e) => patchCase(i, { judge_criteria: e.target.value })} placeholder="Critério do juiz p/ este caso (opcional)" className={inputCls} />
          </div>
        ))}
      </div>

      {err && <p className="mt-3 text-xs text-red-400">{err}</p>}
      <button onClick={save} disabled={busy} className="mt-4 flex items-center gap-1.5 rounded-lg bg-accent px-4 py-1.5 text-sm font-medium text-white transition-colors hover:bg-accent-hover disabled:opacity-50">
        {busy && <Loader2 size={15} className="animate-spin" />} Salvar
      </button>
    </div>
  );
}

// ------------------------------ Resultados ------------------------------- //
function Cell({ cell }: { cell?: RunCell }) {
  const [open, setOpen] = useState(false);
  if (!cell) return <td className="border border-border p-2 text-center text-xs text-muted"><Loader2 size={12} className="mx-auto animate-spin" /></td>;
  if (cell.error) return <td className="border border-border p-2 align-top text-xs text-red-400">{cell.error}</td>;
  return (
    <td className="border border-border p-2 align-top text-xs">
      <div className="flex flex-wrap items-center gap-1.5">
        {typeof cell.rule_pass === "boolean" && (
          <span className={`rounded px-1 py-0.5 text-[10px] font-medium ${cell.rule_pass ? "bg-green-500/15 text-green-500" : "bg-red-500/15 text-red-400"}`}>{cell.rule_pass ? "✔ regra" : "✗ regra"}</span>
        )}
        {typeof cell.judge_score === "number" && <span className="rounded bg-accent/15 px-1 py-0.5 text-[10px] font-medium text-accent-hover">{cell.judge_score}/100</span>}
      </div>
      <div className="mt-1 flex flex-wrap gap-x-2 text-[10px] text-muted">
        <span>{cell.latency_ms ?? "—"}ms</span>
        <span>{(cell.prompt_tokens ?? 0) + (cell.completion_tokens ?? 0)}tok</span>
        <span>{fmtCost(cell.cost)}</span>
      </div>
      <button onClick={() => setOpen((v) => !v)} className="mt-1 flex items-center gap-0.5 text-[10px] text-muted hover:text-ink">
        {open ? <ChevronDown size={10} /> : <ChevronRight size={10} />} saída
      </button>
      {open && (
        <div className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap rounded bg-bg p-2 text-[11px] text-ink">
          {cell.text || "(vazio)"}
          {cell.judge_reason && <p className="mt-1.5 border-t border-border pt-1 text-muted">Juiz: {cell.judge_reason}</p>}
        </div>
      )}
    </td>
  );
}

function RunResults({ run }: { run: BenchmarkRun }) {
  const cases = run.cases || [];
  const models = run.models || [];
  return (
    <div className="mt-4 overflow-x-auto">
      <div className="mb-2 flex items-center gap-2 text-sm">
        {run.status === "running" ? (
          <span className="flex items-center gap-1.5 text-accent-hover"><Loader2 size={14} className="animate-spin" /> rodando…</span>
        ) : run.status === "error" ? (
          <span className="text-red-400">erro: {run.error}</span>
        ) : (
          <span className="flex items-center gap-1.5 text-green-500"><Check size={14} /> concluído</span>
        )}
      </div>
      <table className="w-full border-collapse text-left">
        <thead>
          <tr>
            <th className="border border-border bg-surface2 p-2 text-xs font-medium text-muted">Caso</th>
            {models.map((m, i) => (
              <th key={i} className="border border-border bg-surface2 p-2 text-xs font-medium text-ink">{m.label || m.model}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {cases.map((c) => (
            <tr key={c.id}>
              <td className="border border-border p-2 align-top text-xs text-ink-soft">{c.prompt.slice(0, 80)}</td>
              {models.map((_m, i) => <Cell key={i} cell={run.results[cellKey(c.id || "", i)]} />)}
            </tr>
          ))}
          {/* agregados */}
          <tr className="bg-surface2/50">
            <td className="border border-border p-2 text-xs font-medium text-muted">Agregado</td>
            {models.map((_m, i) => {
              const a = run.aggregates?.[`m${i}`];
              return (
                <td key={i} className="border border-border p-2 align-top text-[11px] text-ink">
                  {a && a.count ? (
                    <>
                      {typeof a.pass_rate === "number" && <div>regra: {Math.round((a.pass_rate || 0) * 100)}%</div>}
                      {typeof a.avg_judge === "number" && <div>juiz méd: {a.avg_judge}/100</div>}
                      <div className="text-muted">{a.avg_latency}ms · {a.total_tokens}tok · {fmtCost(a.total_cost)}</div>
                    </>
                  ) : <span className="text-muted">—</span>}
                </td>
              );
            })}
          </tr>
        </tbody>
      </table>
    </div>
  );
}

// ------------------------------- Runner ---------------------------------- //
function RunPanel({ bench, onBack }: { bench: BenchmarkSummary; onBack: () => void }) {
  const [ext, setExt] = useState<Model[]>([]);
  const [custom, setCustom] = useState<ModelConfig[]>([]);
  const [slots, setSlots] = useState<string[]>([""]);
  const [run, setRun] = useState<BenchmarkRun | null>(null);
  const [history, setHistory] = useState<BenchmarkRun[]>([]);
  const [starting, setStarting] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const loadHistory = useCallback(() => {
    api.get<BenchmarkRun[]>(`/playground/benchmarks/${bench.id}/runs`).then(setHistory).catch(() => {});
  }, [bench.id]);

  useEffect(() => {
    api.get<ModelConfig[]>("/models").then(setCustom).catch(() => {});
    Promise.all([
      api.get<Model[]>("/settings/models").catch(() => [] as Model[]),
      api.get<Model[]>("/integrations/ollama/models").catch(() => [] as Model[]),
    ]).then(([e, l]) => setExt([...e, ...l])).catch(() => {});
    loadHistory();
    return () => { if (timer.current) clearTimeout(timer.current); };
  }, [loadHistory]);

  const poll = useCallback((runId: string) => {
    const tick = async () => {
      try {
        const r = await api.get<BenchmarkRun>(`/playground/runs/${runId}`);
        setRun(r);
        if (r.status === "running") timer.current = setTimeout(tick, 1500);
        else loadHistory();
      } catch { /* ignore */ }
    };
    tick();
  }, [loadHistory]);

  async function start() {
    setErr(null);
    const chosen = slots.filter((s) => s);
    if (chosen.length < 1) { setErr("Escolha ao menos 1 modelo."); return; }
    setStarting(true);
    const models = chosen.map((s) => {
      const m = splitModel(s);
      const label = s.startsWith("custom:") ? custom.find((c) => c.id === s.slice(7))?.name : ext.find((x) => x.id === s)?.name;
      return { ...m, label: label || m.model };
    });
    try {
      const { run_id } = await api.post<{ run_id: string }>(`/playground/benchmarks/${bench.id}/run`, { models });
      poll(run_id);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Falha ao iniciar");
    } finally { setStarting(false); }
  }

  return (
    <div>
      <button onClick={onBack} className="mb-3 flex items-center gap-1 text-sm text-muted hover:text-ink"><ArrowLeft size={16} /> Voltar</button>
      <h3 className="text-lg font-semibold text-ink">{bench.name}</h3>
      <p className="text-xs text-muted">{bench.case_count} caso(s){bench.judge_model ? ` · juiz: ${bench.judge_model}` : ""}</p>

      <div className="mt-3 rounded-2xl border border-border bg-surface p-4">
        <p className="mb-2 text-xs text-muted">Modelos a testar</p>
        <div className="space-y-2">
          {slots.map((s, i) => (
            <div key={i} className="flex items-center gap-2">
              <ModelField models={ext} custom={custom} includeCustom value={s} onChange={(v) => setSlots((sl) => sl.map((x, j) => (j === i ? v : x)))} className="flex w-full items-center justify-between gap-2 rounded-lg border border-border bg-bg px-3 py-2 text-sm text-ink outline-none transition-colors hover:border-accent/50" />
              {slots.length > 1 && <button onClick={() => setSlots((sl) => sl.filter((_, j) => j !== i))} className="rounded-lg p-2 text-muted hover:bg-hover hover:text-red-400"><X size={15} /></button>}
            </div>
          ))}
          {slots.length < 5 && <button onClick={() => setSlots((sl) => [...sl, ""])} className="flex items-center gap-1.5 rounded-lg px-2 py-1 text-xs text-muted hover:text-ink"><Plus size={14} /> Adicionar modelo</button>}
        </div>
        {err && <p className="mt-2 text-xs text-red-400">{err}</p>}
        <button onClick={start} disabled={starting || run?.status === "running"} className="mt-3 flex items-center gap-1.5 rounded-lg bg-accent px-4 py-1.5 text-sm font-medium text-white transition-colors hover:bg-accent-hover disabled:opacity-50">
          {starting ? <Loader2 size={15} className="animate-spin" /> : <Zap size={15} />} Rodar benchmark
        </button>
      </div>

      {run && <RunResults run={run} />}

      {history.length > 0 && (
        <div className="mt-6">
          <p className="mb-2 flex items-center gap-1.5 text-sm font-medium text-ink"><Clock size={15} /> Histórico</p>
          <div className="space-y-1.5">
            {history.map((h) => (
              <button key={h.id} onClick={() => { if (timer.current) clearTimeout(timer.current); if (h.status === "running") poll(h.id); else api.get<BenchmarkRun>(`/playground/runs/${h.id}`).then(setRun).catch(() => {}); }} className="flex w-full items-center justify-between gap-2 rounded-lg border border-border bg-surface px-3 py-2 text-left text-xs transition-colors hover:bg-hover">
                <span className="text-ink-soft">{(h.models || []).map((m) => m.label || m.model).join(", ")}</span>
                <span className={h.status === "done" ? "text-green-500" : h.status === "error" ? "text-red-400" : "text-accent-hover"}>{h.status}</span>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// ------------------------------- Root ------------------------------------ //
export default function BenchmarkView() {
  const confirm = useConfirm();
  const [view, setView] = useState<"list" | "edit" | "run">("list");
  const [editId, setEditId] = useState<string | null>(null);
  const [runBench, setRunBench] = useState<BenchmarkSummary | null>(null);
  const [items, setItems] = useState<BenchmarkSummary[] | null>(null);

  const load = useCallback(() => {
    api.get<BenchmarkSummary[]>("/playground/benchmarks").then(setItems).catch(() => setItems([]));
  }, []);
  useEffect(() => { load(); }, [load]);

  async function del(b: BenchmarkSummary) {
    if (!(await confirm({ title: "Excluir benchmark?", body: <>“{b.name}” e seu histórico serão removidos.</>, confirmLabel: "Excluir", danger: true }))) return;
    await api.del(`/playground/benchmarks/${b.id}`).catch(() => {});
    load();
  }

  if (view === "edit") return <BenchmarkEditor id={editId} onBack={() => setView("list")} onSaved={() => { setView("list"); load(); }} />;
  if (view === "run" && runBench) return <RunPanel bench={runBench} onBack={() => setView("list")} />;

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <p className="text-sm text-muted">Suítes de casos de teste com nota, custo e histórico.</p>
        <button onClick={() => { setEditId(null); setView("edit"); }} className="flex items-center gap-1.5 rounded-full bg-accent px-4 py-1.5 text-sm font-medium text-white transition-colors hover:bg-accent-hover">
          <Plus size={15} /> Novo benchmark
        </button>
      </div>
      {items === null ? (
        <p className="py-8 text-center text-sm text-muted">Carregando…</p>
      ) : items.length === 0 ? (
        <div className="flex flex-col items-center justify-center rounded-2xl border border-dashed border-border py-16 text-center">
          <BarChart3 size={28} className="mb-3 text-muted" />
          <p className="text-sm text-muted">Nenhum benchmark ainda.</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-2.5 md:grid-cols-2">
          {items.map((b) => (
            <div key={b.id} className="group flex items-center gap-3 rounded-2xl border border-border bg-surface p-3.5 transition-colors hover:border-accent/40 hover:bg-hover">
              <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-surface2 text-accent-hover"><BarChart3 size={17} /></span>
              <button onClick={() => { setRunBench(b); setView("run"); }} className="min-w-0 flex-1 text-left">
                <p className="truncate text-sm font-medium text-ink">{b.name}</p>
                <p className="truncate text-xs text-muted">{b.case_count} caso(s){b.judge_model ? " · com juiz" : ""}</p>
              </button>
              <button onClick={() => { setRunBench(b); setView("run"); }} title="Rodar" className="rounded-lg p-1.5 text-muted opacity-0 hover:bg-surface2 hover:text-accent-hover group-hover:opacity-100"><Zap size={16} /></button>
              <button onClick={() => { setEditId(b.id); setView("edit"); }} title="Editar" className="rounded-lg p-1.5 text-muted opacity-0 hover:bg-surface2 hover:text-ink group-hover:opacity-100"><Pencil size={15} /></button>
              <button onClick={() => del(b)} title="Excluir" className="rounded-lg p-1.5 text-muted opacity-0 hover:bg-surface2 hover:text-red-400 group-hover:opacity-100"><Trash2 size={15} /></button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
