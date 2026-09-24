"use client";

import { useState } from "react";
import { Check, ChevronDown, ChevronRight, Clock, GitBranch, Sparkles, TriangleAlert, Users } from "lucide-react";
import Markdown from "./Markdown";

export interface SubagentStep {
  tool: string;
  detail?: string;
  ok?: boolean | null;
}

export interface SubagentResult {
  kind: "subagent" | "subagent_started";
  agent: string;
  task?: string;
  output?: string;
  steps?: SubagentStep[];
  adhoc?: boolean;
  task_id?: string;
  error?: string;
}

/** O trabalho de um subagente numa resposta: tarefa, passos e relatório (recolhido). */
export default function SubagentCard({ data }: { data: SubagentResult }) {
  const [open, setOpen] = useState(false);
  const background = data.kind === "subagent_started";
  const steps = data.steps ?? [];
  const falhas = steps.filter((s) => s.ok === false).length;
  const Icon = data.adhoc ? Sparkles : Users;

  return (
    <div className="my-2 max-w-2xl overflow-hidden rounded-xl border border-border bg-surface">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-3.5 py-2.5 text-left transition-colors hover:bg-hover"
      >
        <Icon size={15} className="shrink-0 text-accent-hover" />
        <span className="min-w-0 flex-1 truncate text-sm font-medium text-ink">{data.agent}</span>
        {background ? (
          <span className="flex shrink-0 items-center gap-1 text-xs text-muted"><Clock size={12} /> em segundo plano</span>
        ) : (
          <span className="shrink-0 text-xs text-muted">
            {steps.length === 1 ? "1 passo" : `${steps.length} passos`}
            {falhas > 0 && <span className="text-amber-400"> · {falhas} com erro</span>}
          </span>
        )}
        {open ? <ChevronDown size={14} className="shrink-0 text-muted" /> : <ChevronRight size={14} className="shrink-0 text-muted" />}
      </button>

      {open && (
        <div className="space-y-3 border-t border-border px-3.5 py-3">
          {data.task && (
            <div>
              <p className="mb-1 text-[11px] font-medium uppercase tracking-wider text-muted">Tarefa</p>
              <p className="whitespace-pre-wrap text-sm text-ink-soft">{data.task}</p>
            </div>
          )}
          {steps.length > 0 && (
            <div>
              <p className="mb-1 text-[11px] font-medium uppercase tracking-wider text-muted">Passos</p>
              <ol className="space-y-1">
                {steps.map((s, i) => (
                  <li key={i} className="flex items-start gap-2 text-xs">
                    {s.ok === false
                      ? <TriangleAlert size={12} className="mt-0.5 shrink-0 text-amber-400" />
                      : <Check size={12} className="mt-0.5 shrink-0 text-muted" />}
                    <span className="shrink-0 font-mono text-ink-soft">{s.tool}</span>
                    {s.detail && <span className="min-w-0 truncate text-muted" title={s.detail}>{s.detail}</span>}
                  </li>
                ))}
              </ol>
            </div>
          )}
          {data.error ? (
            <p className="text-sm text-red-400">{data.error}</p>
          ) : data.output ? (
            <div>
              <p className="mb-1 text-[11px] font-medium uppercase tracking-wider text-muted">Relatório</p>
              <div className="text-sm"><Markdown content={data.output} /></div>
            </div>
          ) : null}
          {data.task_id && (
            <p className="flex items-center gap-1.5 text-xs text-muted">
              <GitBranch size={12} /> Trabalho num worktree isolado, aguardando revisão em Tarefas.
            </p>
          )}
        </div>
      )}
    </div>
  );
}

/** Nota de um trabalho em segundo plano que acordou o chat (agente ou comando). */
export function BackgroundNote({ content }: { content: string }) {
  const partes = content.split(/\n\n---\n\n/);
  return (
    <div className="space-y-2">
      {partes.map((p, i) => <NotePart key={i} text={p} />)}
    </div>
  );
}

function NotePart({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  const [primeira, ...resto] = text.split("\n");
  const titulo = primeira.replace(/^\[[^\]]+\]\s*/, "");
  const agente = primeira.startsWith(AGENT_NOTE_PREFIX);
  return (
    <div className="max-w-2xl overflow-hidden rounded-xl border border-border bg-surface">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-3.5 py-2 text-left transition-colors hover:bg-hover"
      >
        {agente ? <Users size={14} className="shrink-0 text-accent-hover" /> : <Clock size={14} className="shrink-0 text-accent-hover" />}
        <span className="shrink-0 text-xs text-muted">{agente ? "Agente concluído" : "Comando concluído"}</span>
        <span className="min-w-0 flex-1 truncate text-sm text-ink">{titulo}</span>
        {open ? <ChevronDown size={14} className="shrink-0 text-muted" /> : <ChevronRight size={14} className="shrink-0 text-muted" />}
      </button>
      {open && (
        <div className="border-t border-border px-3.5 py-3 text-sm">
          <Markdown content={resto.join("\n").trim()} />
        </div>
      )}
    </div>
  );
}

export const AGENT_NOTE_PREFIX = "[Agente em segundo plano concluído]";
export const COMMAND_NOTE_PREFIX = "[Comando em background concluído]";

export function isBackgroundNote(content: string | null | undefined): boolean {
  const c = (content || "").trimStart();
  return c.startsWith(AGENT_NOTE_PREFIX) || c.startsWith(COMMAND_NOTE_PREFIX);
}
