"use client";

import { useEffect, useId, useState, useSyncExternalStore } from "react";
import { createPortal } from "react-dom";
import { ArrowLeft, Check, ChevronDown, ChevronRight, Clock, GitBranch, Loader2, Sparkles, TriangleAlert, Users, X } from "lucide-react";
import type { SubagentTimelineItem, ToolEvent } from "@/lib/types";
import { timelineFromSteps } from "@/lib/subagent";
import { describeStep } from "@/lib/activity";
import Markdown from "./Markdown";

export interface SubagentResult {
  kind: "subagent" | "subagent_started";
  agent: string;
  task?: string;
  output?: string;
  steps?: { tool: string; detail?: string; ok?: boolean | null }[];
  timeline?: SubagentTimelineItem[];
  adhoc?: boolean;
  task_id?: string;
  error?: string;
}

// ---------------------------------------------------------------------------
// Painel lateral: o trabalho de um agente abre à direita, sem expandir no meio da
// conversa. Um painel por vez (abrir outro fecha o anterior).
// ---------------------------------------------------------------------------
let panelKey: string | null = null;
const panelSubs = new Set<() => void>();
function setPanel(k: string | null) {
  panelKey = k;
  panelSubs.forEach((f) => f());
}
function usePanelOpen(k: string): boolean {
  return useSyncExternalStore(
    (cb) => { panelSubs.add(cb); return () => { panelSubs.delete(cb); }; },
    () => panelKey === k,
    () => false,
  );
}

function SidePanel({
  icon, title, subtitle, running, onClose, onBack, children,
}: {
  icon: React.ReactNode;
  title: string;
  subtitle?: string;
  running?: boolean;
  onClose: () => void;
  onBack?: () => void;
  children: React.ReactNode;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);
  if (typeof document === "undefined") return null;
  return createPortal(
    <aside
      aria-label={title}
      className="animate-slide-in-right fixed bottom-0 right-0 top-0 z-[70] flex w-full flex-col border-l border-border bg-bg shadow-2xl sm:w-[440px]"
    >
      <div className="flex items-center gap-2 border-b border-border px-4 py-3">
        {onBack && (
          <button onClick={onBack} title="Voltar" className="shrink-0 rounded-lg p-1 text-muted transition-colors hover:bg-hover hover:text-ink">
            <ArrowLeft size={16} />
          </button>
        )}
        <span className="shrink-0 text-accent-hover">{running ? <Loader2 size={16} className="animate-spin" /> : icon}</span>
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-semibold text-ink" title={title}>{title}</p>
          {subtitle && <p className="truncate text-xs text-muted" title={subtitle}>{subtitle}</p>}
        </div>
        <button onClick={onClose} title="Fechar" className="shrink-0 rounded-lg p-1 text-muted transition-colors hover:bg-hover hover:text-ink">
          <X size={18} />
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">{children}</div>
    </aside>,
    document.body,
  );
}

/** Tudo o que a UI precisa saber de um agente, do resultado final ou do estado ao vivo. */
function resumo(call: ToolEvent | undefined, result: ToolEvent | undefined, live: boolean) {
  const args = (call?.data ?? {}) as { agent?: string; name?: string; task?: string };
  const res = (result?.data && typeof result.data === "object" ? result.data : undefined) as SubagentResult | undefined;
  const now = call?.live;
  const name = res?.agent || now?.name || args.name || (args.agent && args.agent !== "new" ? args.agent : "") || "Agente";
  const task = res?.task || now?.task || args.task || "";
  const background = res?.kind === "subagent_started" || !!now?.background;
  const running = !res && !background && (now ? now.running : live);
  const timeline = res?.timeline ?? (res ? timelineFromSteps(res.steps) : now?.timeline ?? []);
  const tools = timeline.filter((t) => t.kind === "tool");
  const failed = tools.filter((t) => t.kind === "tool" && t.ok === false).length;
  const error = typeof res?.error === "string" ? res.error : "";
  const adhoc = res?.adhoc ?? now?.adhoc;
  const lastItem = timeline[timeline.length - 1];
  const queued = !res && now?.state === "queued";
  const status = running
    ? lastItem?.kind === "tool" ? describeStep(lastItem.tool, lastItem.detail, lastItem.args) : lastItem?.kind === "reasoning" ? "pensando…" : lastItem?.kind === "text" ? "escrevendo…" : "começando…"
    : queued ? "na fila"
    : background ? "em segundo plano"
    : error ? "falhou"
    : tools.length === 0 ? "concluído" : tools.length === 1 ? "1 passo" : `${tools.length} passos`;
  return { name, task, background, running, timeline, failed, error, adhoc, queued, status, res };
}

function AgentTimeline({ r }: { r: ReturnType<typeof resumo> }) {
  const { task, timeline, running, error, res, background } = r;
  return (
    <ol className="ml-1.5 space-y-3 border-l border-border pb-1 pl-5 text-sm leading-6 text-muted">
      {task && (
        <Item dot="bg-accent">
          <p className="text-[11px] font-medium uppercase tracking-wider text-muted">Tarefa</p>
          <p className="whitespace-pre-wrap text-ink-soft">{task}</p>
        </Item>
      )}
      {timeline.map((t, i) => (
        <Item key={i} dot={t.kind === "tool" ? (t.ok === false ? "bg-amber-400" : "bg-emerald-400") : "bg-muted"}>
          {t.kind === "tool" ? (
            <div className="flex min-w-0 items-center gap-2 text-xs">
              {t.ok === false
                ? <TriangleAlert size={12} className="shrink-0 text-amber-400" />
                : t.ok == null && running && i === timeline.length - 1
                  ? <Loader2 size={12} className="shrink-0 animate-spin text-accent-hover" />
                  : <Check size={12} className="shrink-0 text-green-400" />}
              <span className="min-w-0 truncate text-ink-soft" title={[t.tool, t.detail].filter(Boolean).join(" · ")}>
                {describeStep(t.tool, t.detail, t.args)}
              </span>
            </div>
          ) : t.kind === "reasoning" ? (
            <div className="whitespace-pre-wrap">{t.text}</div>
          ) : (
            <Markdown content={t.text} fast={running} />
          )}
        </Item>
      ))}
      {running && timeline.length === 0 && (
        <Item dot="bg-accent"><p className="flex items-center gap-2 text-xs"><Loader2 size={12} className="animate-spin text-accent-hover" /> começando…</p></Item>
      )}
      {error ? (
        <Item dot="bg-red-400"><p className="text-red-400">{error}</p></Item>
      ) : res?.output && !background ? (
        <Item dot="bg-accent">
          <p className="text-[11px] font-medium uppercase tracking-wider text-muted">Relatório</p>
          <div className="text-ink"><Markdown content={res.output} /></div>
        </Item>
      ) : null}
      {res?.task_id && (
        <Item dot="bg-muted">
          <p className="flex items-center gap-1.5 text-xs"><GitBranch size={12} /> Worktree isolado, aguardando revisão em Tarefas.</p>
        </Item>
      )}
    </ol>
  );
}

function Chip({ running, icon, name, status, failed, background, open, onClick }: {
  running: boolean; icon: React.ReactNode; name: string; status: string; failed: number;
  background: boolean; open: boolean; onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-expanded={open}
      title={`${name} · ${status}`}
      className={`inline-flex max-w-full items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs transition-colors hover:bg-hover ${open ? "border-accent/60 " : ""}${running ? "border-accent/40 bg-accent/10 text-accent-hover" : "border-border bg-surface text-ink-soft"}`}
    >
      {running ? <Loader2 size={13} className="shrink-0 animate-spin" /> : icon}
      <span className="shrink-0 font-medium">{name}</span>
      <span className={`min-w-0 truncate ${running ? "text-accent-hover/75" : "text-muted"}`}>
        {background && <Clock size={11} className="-mt-px mr-1 inline" />}
        {status}
        {failed > 0 && !running && <span className="text-amber-400"> · {failed} com erro</span>}
      </span>
      <ChevronRight size={13} className="shrink-0 text-muted" />
    </button>
  );
}

/** Um subagente na linha do tempo da resposta, no ponto em que a IA o chamou: um chip
 *  com o que ele faz agora; o clique abre o trabalho dele numa barra lateral. */
export function SubagentStep({ call, result, live = false }: { call?: ToolEvent; result?: ToolEvent; live?: boolean }) {
  const fallback = useId();
  const key = `a:${call?.id ?? result?.id ?? fallback}`;
  const open = usePanelOpen(key);
  const r = resumo(call, result, live);
  const icon = r.adhoc ? <Sparkles size={13} className={`shrink-0 ${r.queued ? "text-muted" : "text-accent-hover"}`} /> : <Users size={13} className="shrink-0 text-accent-hover" />;
  return (
    <div className="min-w-0">
      <Chip running={r.running} icon={icon} name={r.name} status={r.status} failed={r.failed}
        background={r.background} open={open} onClick={() => setPanel(open ? null : key)} />
      {open && (
        <SidePanel icon={r.adhoc ? <Sparkles size={16} /> : <Users size={16} />} title={r.name} subtitle={r.status}
          running={r.running} onClose={() => setPanel(null)}>
          <AgentTimeline r={r} />
        </SidePanel>
      )}
    </div>
  );
}

interface TeamResult {
  kind: "subagent_team" | "subagent_team_started";
  team?: string;
  goal?: string;
  size?: number;
  succeeded?: number;
  failed?: number;
  report?: string;
  synthesized?: boolean;
  chained?: boolean;
  members?: (SubagentResult & { agent?: string })[];
  error?: string;
}

const PAGE = 60;

/** Uma equipe (delegate_team): chip com o placar ao vivo; o clique abre na barra lateral
 *  o objetivo, o progresso, os membros (clique num para ver o trabalho dele) e o relatório. */
export function TeamStep({ call, result, live = false }: { call?: ToolEvent; result?: ToolEvent; live?: boolean }) {
  const fallback = useId();
  const key = `t:${call?.id ?? result?.id ?? fallback}`;
  const open = usePanelOpen(key);
  const [sel, setSel] = useState<number | null>(null);
  const [shown, setShown] = useState(PAGE);
  const args = (call?.data ?? {}) as { team_name?: string; goal?: string; members?: { name?: string; task?: string }[] };
  const res = (result?.data && typeof result.data === "object" ? result.data : undefined) as TeamResult | undefined;
  const now = call?.team;
  const name = res?.team || now?.name || args.team_name || "Equipe";
  const goal = res?.goal || now?.goal || args.goal || "";
  const background = res?.kind === "subagent_team_started" || !!now?.background;
  const running = !res && !background && (now ? now.running : live);
  const error = typeof res?.error === "string" ? res.error : "";

  // membros: o resultado final quando existe; senão o estado ao vivo
  const members: { call: ToolEvent; result?: ToolEvent }[] = res?.members?.length
    ? res.members.map((m, i) => ({
        call: { kind: "call", name: "delegate", data: { name: m.agent, task: m.task }, id: `${call?.id ?? "t"}:${i}` },
        result: { kind: "result", name: "delegate", data: { ...m, kind: "subagent" } },
      }))
    : (now?.members ?? []).map((m, i) => ({
        call: { kind: "call", name: "delegate", data: { name: m.name, task: m.task }, id: `${call?.id ?? "t"}:${i}`, live: m },
      }));
  const size = res?.size ?? now?.size ?? args.members?.length ?? members.length;
  const done = res?.kind === "subagent_team"
    ? size
    : (now?.members ?? []).filter((m) => m.state === "done" || m.state === "failed").length;
  const working = (now?.members ?? []).filter((m) => m.state === "running").length;
  const failed = res?.failed ?? (now?.members ?? []).filter((m) => m.state === "failed").length;

  const chain = res?.chained ?? now?.chain ?? false;
  const status = error ? "falhou"
    : background ? `em segundo plano · ${size} agentes`
    : running ? (now?.synthesizing ? "consolidando relatórios…"
      : chain ? `etapa ${Math.min(done + 1, size)} de ${size}` : `${done}/${size} · ${working} trabalhando`)
    : chain ? `${size} etapas` : `${size} agentes`;

  const membro = sel != null && members[sel] ? resumo(members[sel].call, members[sel].result, false) : null;
  const close = () => { setPanel(null); setSel(null); };

  return (
    <div className="min-w-0">
      <Chip running={running} icon={<Users size={13} className="shrink-0 text-accent-hover" />} name={name} status={status}
        failed={failed} background={background} open={open} onClick={() => { setSel(null); setPanel(open ? null : key); }} />
      {open && (membro ? (
        <SidePanel icon={membro.adhoc ? <Sparkles size={16} /> : <Users size={16} />} title={membro.name}
          subtitle={`${name} · ${membro.status}`} running={membro.running} onClose={close} onBack={() => setSel(null)}>
          <AgentTimeline r={membro} />
        </SidePanel>
      ) : (
        <SidePanel icon={<Users size={16} />} title={name} subtitle={status} running={running} onClose={close}>
          <div className="space-y-5 text-sm">
            {goal && (
              <div>
                <p className="text-[11px] font-medium uppercase tracking-wider text-muted">Objetivo</p>
                <p className="mt-1 whitespace-pre-wrap text-ink-soft">{goal}</p>
              </div>
            )}
            {size > 0 && !background && (
              <div className="flex items-center gap-2 text-xs text-muted">
                <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-border">
                  <div className="h-full rounded-full bg-accent transition-[width] duration-300" style={{ width: `${Math.round((done / size) * 100)}%` }} />
                </div>
                <span className="shrink-0 tabular-nums">{done}/{size} concluídos</span>
              </div>
            )}
            {members.length > 0 && (
              <div>
                <p className="mb-1.5 text-[11px] font-medium uppercase tracking-wider text-muted">{chain ? "Etapas, em ordem" : "Agentes"}</p>
                <div className="space-y-1">
                  {members.slice(0, shown).map((m, i) => {
                    const rm = resumo(m.call, m.result, false);
                    return (
                      <button
                        key={m.call.id ?? i}
                        type="button"
                        onClick={() => setSel(i)}
                        title={`${rm.name} · ${rm.status}`}
                        className="flex w-full items-center gap-2 rounded-lg border border-border bg-surface px-3 py-2 text-left transition-colors hover:bg-hover"
                      >
                        {rm.running ? <Loader2 size={13} className="shrink-0 animate-spin text-accent-hover" />
                          : rm.error ? <TriangleAlert size={13} className="shrink-0 text-amber-400" />
                          : rm.queued ? <Clock size={13} className="shrink-0 text-muted" />
                          : <Check size={13} className="shrink-0 text-green-400" />}
                        <span className="shrink-0 text-sm text-ink">{rm.name}</span>
                        <span className="min-w-0 flex-1 truncate text-xs text-muted">{rm.status}</span>
                        <ChevronRight size={13} className="shrink-0 text-muted" />
                      </button>
                    );
                  })}
                </div>
                {members.length > shown && (
                  <button type="button" onClick={() => setShown((n) => n + PAGE * 3)} className="mt-2 text-xs text-accent-hover hover:underline">
                    Mostrar mais {Math.min(PAGE * 3, members.length - shown)} de {members.length - shown}
                  </button>
                )}
              </div>
            )}
            {error ? (
              <p className="text-red-400">{error}</p>
            ) : res?.report && !background ? (
              <div>
                <p className="text-[11px] font-medium uppercase tracking-wider text-muted">
                  Relatório final{res.synthesized ? ` · consolidado de ${size} relatórios` : ""}
                </p>
                <div className="mt-1 text-ink"><Markdown content={res.report} /></div>
              </div>
            ) : null}
          </div>
        </SidePanel>
      ))}
    </div>
  );
}

function Item({ dot, children }: { dot: string; children: React.ReactNode }) {
  return (
    <li className="relative min-w-0 [overflow-wrap:anywhere]">
      <span aria-hidden className={`absolute -left-[25px] top-2 h-2 w-2 rounded-full ${dot}`} />
      {children}
    </li>
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
