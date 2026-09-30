"use client";

import { createContext, useContext, useEffect, useId, useLayoutEffect, useRef, useState, useSyncExternalStore } from "react";
import { createPortal } from "react-dom";
import { ArrowDown, ArrowLeft, ArrowUp, Brain, Check, ChevronDown, ChevronRight, ChevronUp, Clock, GitBranch, Loader2, Mic, Sparkles, Square, TriangleAlert, Users, X } from "lucide-react";
import type { SubagentLive, SubagentTimelineItem, TeamLive, ToolEvent } from "@/lib/types";
import { api } from "@/lib/api";
import { applySubagentProgress, timelineFromSteps } from "@/lib/subagent";
import { messageAgent } from "@/lib/sse";
import { startRecording, transcribe } from "@/lib/voice";
import { describeStep } from "@/lib/activity";
import Markdown from "./Markdown";
import { toast } from "@/components/Toaster";

/** Conversa direta com o agente pelo painel dele (depois que ele terminou). */
export type AgentFollowup = { role: "user" | "assistant"; content: string; timeline?: SubagentTimelineItem[] };

/** O chat dono dos agentes: o painel precisa dele para falar com o agente e, quando a
 *  conversa termina, recarregar as mensagens (a continuação fica gravada nelas). */
export const AgentChatContext = createContext<{ chatId: string | null; reload?: () => void }>({ chatId: null });

export interface SubagentResult {
  kind: "subagent" | "subagent_started";
  followups?: AgentFollowup[];
  agent: string;
  task?: string;
  output?: string;
  steps?: { tool: string; detail?: string; ok?: boolean | null }[];
  timeline?: SubagentTimelineItem[];
  adhoc?: boolean;
  task_id?: string;
  error?: string;
  /** o agente parou para pedir algo ao orquestrador (request_from_lead) */
  status?: "needs_input";
  needs?: { kind?: "tool" | "data"; need?: string };
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

const PANEL_KEY = "agent_panel_w";
const PANEL_MIN = 320;
const PANEL_MAX = 960;

function SidePanel({
  icon, title, subtitle, running, onClose, onBack, footer, children,
}: {
  icon: React.ReactNode;
  title: string;
  subtitle?: string;
  running?: boolean;
  onClose: () => void;
  onBack?: () => void;
  /** caixa de texto presa embaixo (conversa com o agente) */
  footer?: React.ReactNode;
  children: React.ReactNode;
}) {
  // rolagem: acompanha o fim enquanto o usuário está lá; se ele subir, aparece a seta
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const contentRef = useRef<HTMLDivElement | null>(null);
  const [atBottom, setAtBottom] = useState(true);
  const stick = useRef(true);
  const onScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    const fim = el.scrollHeight - el.scrollTop - el.clientHeight < 48;
    stick.current = fim;
    setAtBottom(fim);
  };
  const toBottom = (smooth = true) => {
    const el = scrollRef.current;
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: smooth ? "smooth" : "auto" });
  };
  useLayoutEffect(() => {
    const c = contentRef.current;
    if (!c || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => { if (stick.current) toBottom(false); });
    ro.observe(c);
    return () => ro.disconnect();
  }, []);
  const [w, setW] = useState<number>(() => {
    if (typeof window === "undefined") return 440;
    const v = Number(window.localStorage.getItem(PANEL_KEY));
    return Number.isFinite(v) && v >= PANEL_MIN ? v : 440;
  });
  const ref = useRef<HTMLElement | null>(null);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  // arrastar a borda esquerda: mais para a esquerda = painel mais largo (como os Controles)
  const startDrag = (e: React.PointerEvent) => {
    e.preventDefault();
    const el = ref.current;
    if (!el) return;
    const right = el.getBoundingClientRect().right;
    // o pai direto é o encaixe `display: contents` (largura 0): mede o 1º ancestral com largura
    let anc: HTMLElement | null = el.parentElement;
    while (anc && anc.getBoundingClientRect().width === 0) anc = anc.parentElement;
    const cap = Math.min(PANEL_MAX, Math.round((anc?.getBoundingClientRect().width ?? window.innerWidth) * 0.7));
    const prev = document.body.style.userSelect;
    document.body.style.userSelect = "none";
    document.body.style.cursor = "col-resize";
    const move = (ev: PointerEvent) => setW(Math.max(PANEL_MIN, Math.min(Math.round(right - ev.clientX), cap)));
    const up = () => {
      document.body.style.userSelect = prev;
      document.body.style.cursor = "";
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      setW((cur) => { try { window.localStorage.setItem(PANEL_KEY, String(cur)); } catch { /* sem storage */ } return cur; });
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };

  if (typeof document === "undefined") return null;
  // no chat: entra na coluna do layout (o miolo encolhe e recentraliza); fora dele
  // (ex.: página compartilhada), flutua à direita
  const slot = document.getElementById("aiw-agent-slot");
  const inline = !!slot;
  return createPortal(
    <aside
      ref={ref}
      aria-label={title}
      style={{ "--agw": `${w}px` } as React.CSSProperties}
      className={`animate-slide-in-right z-50 flex flex-col border-l border-border bg-bg ${
        inline
          ? "fixed inset-0 md:relative md:inset-auto md:z-auto md:h-full md:w-[var(--agw)] md:shrink-0"
          : "fixed bottom-0 right-0 top-0 w-full shadow-2xl sm:w-[var(--agw)]"
      }`}
    >
      <div
        onPointerDown={startDrag}
        title="Arraste para redimensionar"
        className="group absolute inset-y-0 -left-1 z-10 hidden w-2.5 cursor-col-resize items-stretch justify-center md:flex"
      >
        <span className="my-auto h-10 w-1 rounded-full bg-border transition-colors group-hover:bg-accent" />
      </div>
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
      <div ref={scrollRef} onScroll={onScroll} className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
        <div ref={contentRef}>{children}</div>
      </div>
      {footer && (
        <div className="relative shrink-0 px-3 pb-3 pt-1">
          {!atBottom && (
            <button
              onClick={() => { stick.current = true; toBottom(); }}
              title="Ir para o fim"
              aria-label="Ir para o fim"
              className="animate-pop absolute -top-11 left-1/2 z-20 flex h-9 w-9 -translate-x-1/2 items-center justify-center rounded-full border border-border bg-surface text-ink-soft shadow-menu transition-colors hover:bg-hover hover:text-ink"
            >
              <ArrowDown size={18} />
            </button>
          )}
          {footer}
        </div>
      )}
    </aside>,
    slot ?? document.body,
  );
}

/** Progresso de um agente/equipe em segundo plano, lido do servidor enquanto roda. */
interface BgJob {
  status: "running" | "done" | "failed" | "unknown";
  timeline?: SubagentTimelineItem[];
  members?: { name: string; task: string; state: "queued" | "running" | "done" | "failed"; timeline: SubagentTimelineItem[] }[];
  synthesizing?: boolean;
}

function useBgJob(jobId: string | undefined): BgJob | null {
  const { chatId, reload } = useContext(AgentChatContext);
  const [job, setJob] = useState<BgJob | null>(null);
  useEffect(() => {
    if (!jobId || !chatId) return;
    let vivo = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const tick = async () => {
      try {
        const j = await api.get<BgJob>(`/chats/${chatId}/agents/jobs/${encodeURIComponent(jobId)}`);
        if (!vivo) return;
        setJob(j);
        if (j.status === "running") timer = setTimeout(tick, 2500);
        // terminou: o card da mensagem recebe o resultado quando o chat fica ocioso
        else if (j.status !== "unknown") timer = setTimeout(() => reload?.(), 4000);
      } catch {
        if (vivo) timer = setTimeout(tick, 8000);
      }
    };
    void tick();
    return () => { vivo = false; if (timer) clearTimeout(timer); };
  }, [jobId, chatId]); // eslint-disable-line react-hooks/exhaustive-deps
  return job;
}

/** Tudo o que a UI precisa saber de um agente, do resultado final ou do estado ao vivo. */
function resumo(call: ToolEvent | undefined, result: ToolEvent | undefined, live: boolean) {
  const args = (call?.data ?? {}) as { agent?: string; name?: string; task?: string };
  const res = (result?.data && typeof result.data === "object" ? result.data : undefined) as SubagentResult | undefined;
  const now = call?.live;
  const name = res?.agent || now?.name || args.name || (args.agent && args.agent !== "new" ? args.agent : "") || "Agente";
  const task = res?.task || now?.task || args.task || "";
  const started = res?.kind === "subagent_started";
  const background = started || !!now?.background;
  // em segundo plano: o estado ao vivo vem do servidor (useBgJob) até o card receber o resultado
  const running = started ? !!now?.running : !res && !now?.background && (now ? now.running : live);
  const timeline = res && !started ? (res.timeline ?? timelineFromSteps(res.steps)) : now?.timeline ?? [];
  const tools = timeline.filter((t) => t.kind === "tool");
  const failed = tools.filter((t) => t.kind === "tool" && t.ok === false).length;
  const error = typeof res?.error === "string" ? res.error : "";
  const adhoc = res?.adhoc ?? now?.adhoc;
  const lastItem = timeline[timeline.length - 1];
  const queued = !res && now?.state === "queued";
  const status = running
    ? (started ? "em segundo plano · " : "") + (lastItem?.kind === "tool" ? describeStep(lastItem.tool, lastItem.detail, lastItem.args) : lastItem?.kind === "reasoning" ? "pensando…" : lastItem?.kind === "text" ? "escrevendo…" : "começando…")
    : queued ? "na fila"
    : started ? "em segundo plano"
    : error ? "falhou"
    : res?.status === "needs_input" && res.needs?.need ? `precisa de: ${res.needs.need}`
    : tools.length === 0 ? "concluído" : tools.length === 1 ? "1 passo" : `${tools.length} passos`;
  return { name, task, background, running, timeline, failed, error, adhoc, queued, status, res };
}

function UserBubble({ text, pending }: { text: string; pending?: boolean }) {
  return (
    <div className="flex flex-col items-end gap-0.5">
      <div className="max-w-[92%] whitespace-pre-wrap rounded-2xl bg-accent/15 px-3 py-1.5 text-ink">{text}</div>
      {pending && (
        <span className="flex items-center gap-1 text-[11px] text-muted"><Clock size={10} /> entra no próximo passo do agente</span>
      )}
    </div>
  );
}

function AgentTimeline({ r, after }: { r: ReturnType<typeof resumo>; after?: React.ReactNode }) {
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
        <Item key={i} bubble={t.kind === "user"} dot={t.kind === "tool" ? (t.ok === false ? "bg-amber-400" : "bg-emerald-400") : t.kind === "user" ? "bg-accent" : "bg-muted"}>
          {t.kind === "tool" ? (
            <ToolStepRow step={t} spinning={t.ok == null && running && i === timeline.length - 1} />
          ) : t.kind === "user" ? (
            <UserBubble text={t.text} />
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
      {res?.status === "needs_input" && res.needs?.need && (
        <Item dot="bg-amber-400">
          <p className="text-[11px] font-medium uppercase tracking-wider text-muted">
            {res.needs.kind === "tool" ? "Pediu uma ferramenta" : "Pediu informação"}
          </p>
          <p className="whitespace-pre-wrap text-ink-soft">{res.needs.need}</p>
        </Item>
      )}
      {res?.task_id && (
        <Item dot="bg-muted">
          <p className="flex items-center gap-1.5 text-xs"><GitBranch size={12} /> Worktree isolado, aguardando revisão em Tarefas.</p>
        </Item>
      )}
      {after}
    </ol>
  );
}

/** Uma troca da conversa de continuação: o que o usuário disse e a resposta do agente
 *  (com os passos que ele deu). `live` = resposta ainda chegando. */
function FollowupItems({ items, live }: { items: AgentFollowup[]; live?: SubagentLive | null }) {
  return (
    <>
      {items.map((f, i) => (
        <Item key={i} bubble={f.role === "user"} dot={f.role === "user" ? "bg-accent" : "bg-muted"}>
          {f.role === "user" ? <UserBubble text={f.content} /> : (
            <div className="space-y-2">
              {(f.timeline ?? []).filter((t) => t.kind === "tool").map((t, j) => (
                t.kind === "tool" && <ToolStepRow key={j} step={t} spinning={false} />
              ))}
              <div className="text-ink"><Markdown content={f.content} /></div>
            </div>
          )}
        </Item>
      ))}
      {live && (
        <Item dot="bg-accent">
          <div className="space-y-2">
            {live.timeline.map((t, j) => (
              t.kind === "tool" ? <ToolStepRow key={j} step={t} spinning={t.ok == null && j === live.timeline.length - 1} />
                : t.kind === "text" ? <div key={j} className="text-ink"><Markdown content={t.text} fast /></div>
                : t.kind === "reasoning" ? <div key={j} className="whitespace-pre-wrap text-muted">{t.text}</div>
                : t.kind === "user" ? <UserBubble key={j} text={t.text} /> : null
            ))}
            {!live.timeline.some((t) => t.kind === "text") && (
              <p className="flex items-center gap-2 text-xs"><Loader2 size={12} className="animate-spin text-accent-hover" /> respondendo…</p>
            )}
          </div>
        </Item>
      )}
    </>
  );
}

/** Caixa de texto do painel: fala com o agente (instrui enquanto trabalha, continua a
 *  conversa depois). Pensar mais · ditar · enviar. */
function AgentComposer({ name, running, busy, onSend }: {
  name: string; running: boolean; busy: boolean; onSend: (text: string, think: boolean) => void;
}) {
  const [text, setText] = useState("");
  const [think, setThink] = useState(false);
  const [rec, setRec] = useState<{ stop: () => Promise<Blob> } | null>(null);
  const [transcribing, setTranscribing] = useState(false);
  const ta = useRef<HTMLTextAreaElement | null>(null);
  useLayoutEffect(() => {
    const el = ta.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 180)}px`;
  }, [text]);
  const enviar = () => {
    const t = text.trim();
    if (!t || busy) return;
    onSend(t, think);
    setText("");
  };
  async function mic() {
    if (rec) {
      setRec(null);
      setTranscribing(true);
      try {
        const t = await transcribe(await rec.stop());
        if (t) setText((v) => (v ? `${v} ${t}` : t));
      } catch (e) {
        toast((e as Error).message || "Transcrição falhou.");
      } finally {
        setTranscribing(false);
        ta.current?.focus();
      }
      return;
    }
    try {
      setRec(await startRecording());
    } catch (e) {
      toast("Sem acesso ao microfone.");
    }
  }
  const btn = "flex h-8 w-8 items-center justify-center rounded-full transition-colors";
  return (
    <div className="rounded-2xl border border-border bg-surface px-3 py-2 transition-colors focus-within:border-accent/50">
      <textarea
        ref={ta}
        rows={1}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); enviar(); } }}
        placeholder={running ? `Instrua ${name}…` : `Continue com ${name}…`}
        aria-label={`Mensagem para ${name}`}
        className="block max-h-[180px] w-full resize-none bg-transparent px-1 py-1 text-sm leading-6 text-ink outline-none placeholder:text-muted"
      />
      <div className="mt-1 flex items-center gap-1">
        <button type="button" onClick={() => setThink((v) => !v)} aria-pressed={think}
          title={think ? "Pensar mais: ligado" : "Pensar mais"} aria-label="Pensar mais"
          className={`${btn} ${think ? "bg-accent/15 text-accent-hover" : "text-muted hover:bg-hover hover:text-ink"}`}>
          <Brain size={16} />
        </button>
        <div className="flex-1" />
        <button type="button" onClick={() => void mic()} disabled={transcribing}
          title={rec ? "Parar e transcrever" : "Ditar"} aria-label={rec ? "Parar e transcrever" : "Ditar"}
          className={`${btn} ${rec ? "animate-pulse bg-red-500/20 text-red-300" : "text-muted hover:bg-hover hover:text-ink"} disabled:opacity-50`}>
          {transcribing ? <Loader2 size={16} className="animate-spin" /> : rec ? <Square size={14} fill="currentColor" /> : <Mic size={16} />}
        </button>
        <button type="button" onClick={enviar} disabled={!text.trim() || busy}
          title="Enviar" aria-label="Enviar"
          className={`${btn} bg-accent text-white hover:bg-accent-hover disabled:bg-surface2 disabled:text-muted`}>
          <ArrowUp size={16} />
        </button>
      </div>
    </div>
  );
}

/** Painel de um agente com a conversa: timeline + relatório + continuação + caixa. */
function AgentPanel({ r, agentRef, icon, subtitle, onClose, onBack }: {
  r: ReturnType<typeof resumo>; agentRef: string | null; icon: React.ReactNode; subtitle: string;
  onClose: () => void; onBack?: () => void;
}) {
  const { chatId, reload } = useContext(AgentChatContext);
  const persisted: AgentFollowup[] = r.res?.followups ?? [];
  // continuação em andamento/recém-terminada, até as mensagens recarregadas a trazerem
  const [local, setLocal] = useState<{ base: number; items: AgentFollowup[]; live: SubagentLive | null } | null>(null);
  const [steers, setSteers] = useState<string[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (local && !local.live && persisted.length >= local.base + local.items.length) setLocal(null);
  }, [persisted.length, local]);
  // a mensagem entregue ao agente aparece na timeline dele: sai o balão "pendente"
  const vistos = r.timeline.filter((t) => t.kind === "user").map((t) => (t as { text: string }).text);
  useEffect(() => {
    setSteers((s) => s.filter((x) => !vistos.some((v) => x.startsWith(v.slice(0, 150)))));
  }, [vistos.length]); // eslint-disable-line react-hooks/exhaustive-deps

  const podeFalar = !!chatId && !!agentRef && !r.background && !r.queued;

  async function send(text: string, think: boolean) {
    if (!chatId || !agentRef) return;
    setErr(null);
    setBusy(true);
    const comecar = () => setLocal((cur) => cur ?? {
      base: persisted.length, items: [{ role: "user", content: text }],
      live: { name: r.name, running: true, timeline: [] },
    });
    if (r.running) setSteers((s) => [...s, text]); else comecar();
    try {
      const modo = await messageAgent(chatId, agentRef, { content: text, think }, (ev) => {
        if (ev.type === "progress") {
          comecar();
          setSteers((s) => s.filter((x) => x !== text));
          setLocal((cur) => cur && cur.live ? { ...cur, live: applySubagentProgress(cur.live, ev) } : cur);
        } else if (ev.type === "done") {
          const novos = (ev.followups as AgentFollowup[] | undefined) ?? [];
          setLocal((cur) => cur ? { ...cur, items: novos.length ? novos : cur.items, live: null } : cur);
          reload?.();
        } else if (ev.type === "error") {
          setErr(String(ev.detail ?? ev.message ?? "falha"));
          setLocal(null);
        }
      });
      if (modo === "steer") setLocal((cur) => (cur && cur.live && cur.live.timeline.length === 0 ? null : cur));
      if (modo === "steer" && !r.running) setSteers((s) => [...s, text]);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Não foi possível falar com o agente");
      setLocal(null);
      setSteers((s) => s.filter((x) => x !== text));
    } finally {
      setBusy(false);
    }
  }

  const itens = [...persisted, ...(local ? local.items : [])];
  return (
    <SidePanel icon={icon} title={r.name} subtitle={subtitle} running={r.running || !!local?.live}
      onClose={onClose} onBack={onBack}
      footer={podeFalar ? (
        <>
          {err && <p className="mb-1.5 px-1 text-xs text-red-400">{err}</p>}
          <AgentComposer name={r.name} running={r.running} busy={busy && !r.running} onSend={(t, k) => void send(t, k)} />
        </>
      ) : undefined}>
      <AgentTimeline r={r} after={
        <>
          {steers.map((t, i) => <Item key={`s${i}`} bubble dot="bg-accent"><UserBubble text={t} pending /></Item>)}
          <FollowupItems items={itens} live={local?.live} />
        </>
      } />
    </SidePanel>
  );
}

/** Um passo de ferramenta do agente ("Pesquisando “x”"). Mesma cara de sempre; quando
 *  há o que mostrar, clicar abre a chamada e o que voltou (a setinha no fim diz se está
 *  aberto). Passos antigos, sem chamada/resultado gravados, ficam só como texto. */
function ToolStepRow({ step, spinning }: {
  step: Extract<SubagentTimelineItem, { kind: "tool" }>;
  spinning: boolean;
}) {
  const [open, setOpen] = useState(false);
  const podeAbrir = !!(step.call || step.preview);
  const icon = step.ok === false
    ? <TriangleAlert size={12} className="shrink-0 text-amber-400" />
    : spinning
      ? <Loader2 size={12} className="shrink-0 animate-spin text-accent-hover" />
      : <Check size={12} className="shrink-0 text-green-400" />;
  const texto = describeStep(step.tool, step.detail, step.args);
  if (!podeAbrir) {
    return (
      <div className="flex min-w-0 items-center gap-2 text-xs">
        {icon}
        <span className="min-w-0 truncate text-ink-soft" title={[step.tool, step.detail].filter(Boolean).join(" · ")}>{texto}</span>
      </div>
    );
  }
  const Seta = open ? ChevronUp : ChevronDown;
  return (
    <div className="min-w-0">
      <button type="button" onClick={() => setOpen((v) => !v)} aria-expanded={open}
        className="flex w-full min-w-0 items-center gap-2 text-left text-xs">
        {icon}
        <span className="min-w-0 truncate text-ink-soft" title={[step.tool, step.detail].filter(Boolean).join(" · ")}>{texto}</span>
        <Seta size={12} className="shrink-0 text-muted" />
      </button>
      {open && (
        <div className="mt-1.5 space-y-1.5">
          {step.call && <StepBlock label="Chamada" text={step.call} />}
          {step.preview
            ? <StepBlock label={step.ok === false ? "Erro" : "Resultado"} text={step.preview} />
            : spinning && <p className="text-[11px] text-muted">aguardando o resultado…</p>}
        </div>
      )}
    </div>
  );
}

function StepBlock({ label, text }: { label: string; text: string }) {
  return (
    <div>
      <p className="mb-0.5 text-[10px] font-medium uppercase tracking-wider text-muted">{label}</p>
      <pre className="max-h-56 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-surface2 px-2.5 py-2 font-mono text-[11px] leading-[1.45] text-ink-soft">{text}</pre>
    </div>
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
  const started = (result?.data as { kind?: string; job_id?: string } | undefined);
  const job = useBgJob(started?.kind === "subagent_started" ? started.job_id : undefined);
  const callVivo: ToolEvent | undefined = job && job.status !== "unknown" && call && started?.kind === "subagent_started"
    ? { ...call, live: { name: "", running: job.status === "running", background: true, timeline: job.timeline ?? [] } }
    : call;
  const r = resumo(callVivo, result, live);
  const icon = r.adhoc ? <Sparkles size={13} className={`shrink-0 ${r.queued ? "text-muted" : "text-accent-hover"}`} /> : <Users size={13} className="shrink-0 text-accent-hover" />;
  return (
    <div className="min-w-0">
      <Chip running={r.running} icon={icon} name={r.name} status={r.status} failed={r.failed}
        background={r.background} open={open} onClick={() => setPanel(open ? null : key)} />
      {open && (
        <AgentPanel r={r} agentRef={call?.id ?? result?.id ?? null}
          icon={r.adhoc ? <Sparkles size={16} /> : <Users size={16} />} subtitle={r.status}
          onClose={() => setPanel(null)} />
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
  job_id?: string;
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
  const job = useBgJob(res?.kind === "subagent_team_started" ? res.job_id : undefined);
  const bgLive: TeamLive | undefined = job?.members && res?.kind === "subagent_team_started" ? {
    name: res?.team || args.team_name || "Equipe", goal: res?.goal, size: job.members.length,
    running: job.status === "running", background: true, synthesizing: job.synthesizing,
    members: job.members.map((m) => ({ name: m.name, task: m.task, adhoc: true, running: m.state === "running", state: m.state, timeline: m.timeline })),
  } : undefined;
  const now = call?.team ?? bgLive;
  const name = res?.team || now?.name || args.team_name || "Equipe";
  const goal = res?.goal || now?.goal || args.goal || "";
  const background = res?.kind === "subagent_team_started" || !!now?.background;
  const bgRunning = res?.kind === "subagent_team_started" && job?.status === "running";
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
    : bgRunning ? (now?.synthesizing ? "em segundo plano · consolidando…" : `em segundo plano · ${done}/${size} · ${working} trabalhando`)
    : res?.kind === "subagent_team_started" ? `em segundo plano · ${size} agentes`
    : running ? (now?.synthesizing ? "consolidando relatórios…"
      : chain ? `etapa ${Math.min(done + 1, size)} de ${size}` : `${done}/${size} · ${working} trabalhando`)
    : chain ? `${size} etapas` : `${size} agentes`;

  const membro = sel != null && members[sel] ? resumo(members[sel].call, members[sel].result, false) : null;
  const close = () => { setPanel(null); setSel(null); };

  return (
    <div className="min-w-0">
      <Chip running={running || bgRunning} icon={<Users size={13} className="shrink-0 text-accent-hover" />} name={name} status={status}
        failed={failed} background={background} open={open} onClick={() => { setSel(null); setPanel(open ? null : key); }} />
      {open && (membro ? (
        <AgentPanel r={membro} agentRef={call?.id && sel != null ? `${call.id}#${sel}` : null}
          icon={membro.adhoc ? <Sparkles size={16} /> : <Users size={16} />}
          subtitle={`${name} · ${membro.status}`} onClose={close} onBack={() => setSel(null)} />
      ) : (
        <SidePanel icon={<Users size={16} />} title={name} subtitle={status} running={running || bgRunning} onClose={close}>
          <div className="space-y-5 text-sm">
            {goal && (
              <div>
                <p className="text-[11px] font-medium uppercase tracking-wider text-muted">Objetivo</p>
                <p className="mt-1 whitespace-pre-wrap text-ink-soft">{goal}</p>
              </div>
            )}
            {size > 0 && (!background || !!bgLive) && (
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

function Item({ dot, bubble, children }: { dot: string; bubble?: boolean; children: React.ReactNode }) {
  return (
    <li className="relative min-w-0 [overflow-wrap:anywhere]">
      <span aria-hidden className={`absolute -left-[25px] ${bubble ? "top-[14px]" : "top-2"} h-2 w-2 rounded-full ${dot}`} />
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
