"use client";

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { AlertTriangle, Bell, BellOff, BellRing, CalendarClock, Check, ChevronLeft, Clock, Eye, History, Loader2, Minus, Pause, Play, Plus, Search, Trash2, X, Zap } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import type { AppNotification, Automation, AutomationRun } from "@/lib/types";
import { disablePush, enablePush, pushEnabled, pushSupported } from "@/lib/push";
import AutomationEditor from "@/components/AutomationEditor";
import { useConfirm } from "@/components/ConfirmDialog";

const UNIT_LABEL: Record<string, string> = { minutes: "min", hours: "h", days: "d" };
const WATCHER_LABEL: Record<string, string> = {
  page: "Página", web_search: "Busca web", price: "Preço", rss: "RSS",
};
const WEEKDAYS = ["Dom", "Seg", "Ter", "Qua", "Qui", "Sex", "Sáb"];

function fmtWhen(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString(undefined, { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}

// tempo decorrido "m:ss" desde o início da execução (para o chip "Em execução").
function fmtElapsed(startISO: string): string {
  const s = Math.max(0, Math.floor((Date.now() - new Date(startISO).getTime()) / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

// tipo pela cor do tile: agendada=violeta, monitor=azul, lembrete=âmbar.
const KIND_TILE: Record<string, string> = {
  scheduled: "bg-accent/15 text-accent-hover",
  monitor: "bg-sky-500/15 text-sky-300",
  reminder: "bg-amber-500/15 text-amber-300",
};
// estado por pílula (separado da cor do tipo).
function statePill(a: Automation): { label: string; cls: string } {
  if (a.kind === "reminder" && !a.next_run_at) return { label: "Concluído", cls: "bg-sky-500/12 text-sky-300" };
  return a.enabled
    ? { label: "Ativa", cls: "bg-green-500/12 text-green-300" }
    : { label: "Pausada", cls: "bg-surface2 text-muted" };
}
const KIND_FILTERS: { key: string; label: string }[] = [
  { key: "all", label: "Todas" },
  { key: "scheduled", label: "Agendadas" },
  { key: "monitor", label: "Monitores" },
  { key: "reminder", label: "Lembretes" },
];

function scheduleLabel(a: Automation): string {
  if (a.kind === "reminder") {
    if (!a.next_run_at) return "Lembrete · concluído";
    return `Lembrete · ${new Date(a.next_run_at).toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })}`;
  }
  if (a.kind === "monitor") {
    const w = WATCHER_LABEL[a.watcher_type ?? ""] ?? "Monitor";
    const m = Math.max(1, Math.round((a.interval_seconds ?? 300) / 60));
    return `${w} · a cada ${m}min`;
  }
  const s = a.schedule ?? {};
  const time = s.time ?? "09:00";
  if (s.mode === "daily") return `Diariamente às ${time}`;
  if (s.mode === "weekly") {
    const days = (s.days ?? []).map((d) => WEEKDAYS[d % 7]).join("/") || "?";
    return `${days} às ${time}`;
  }
  if (s.mode === "monthly") return `Dia ${s.day ?? 1} de cada mês às ${time}`;
  const e = s.every ?? 1;
  const u = UNIT_LABEL[s.unit ?? "hours"] ?? "h";
  return `A cada ${e}${u}`;
}

/** Tela de Automações embutida no chat (mantém a barra lateral). */
export default function AutomationsView({
  onOpenChat,
  onBack,
}: {
  /** abrir o chat de uma notificação (fecha a tela e seleciona o chat) */
  onOpenChat: (chatId: string) => void;
  /** voltar ao chat — sem isso o mobile (sidebar escondida) fica preso aqui */
  onBack?: () => void;
}) {
  const confirm = useConfirm();
  const [items, setItems] = useState<Automation[]>([]);
  const [notes, setNotes] = useState<AppNotification[]>([]);
  const [editing, setEditing] = useState<Automation | null>(null);
  const [creating, setCreating] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [historyFor, setHistoryFor] = useState<Automation | null>(null);
  // execuções em curso (verdade do servidor): id -> início. Alimenta o chip
  // "Em execução" com tempo decorrido e sobrevive a navegar/atualizar a página.
  const [running, setRunning] = useState<Record<string, string>>({});
  const [liveRun, setLiveRun] = useState<Automation | null>(null);
  const [, setTick] = useState(0); // 1s tick só p/ o tempo decorrido animar
  const itemsRef = useRef(items);
  itemsRef.current = items;
  const [query, setQuery] = useState("");
  const [kindFilter, setKindFilter] = useState("all");
  const [pushOn, setPushOn] = useState(false);
  const [pushBusy, setPushBusy] = useState(false);
  const pushOk = pushSupported();

  useEffect(() => { pushEnabled().then(setPushOn); }, []);
  async function togglePush() {
    setPushBusy(true);
    try {
      if (pushOn) { await disablePush(); setPushOn(false); }
      else { await enablePush(); setPushOn(true); }
    } catch (e) {
      setToast(e instanceof Error ? e.message : "Falha ao alterar notificações");
    } finally { setPushBusy(false); }
  }

  const reload = useCallback(() => {
    api.get<Automation[]>("/automations").then(setItems).catch(() => {});
    api.get<AppNotification[]>("/notifications").then(setNotes).catch(() => {});
  }, []);

  useEffect(() => {
    reload();
    const t = setInterval(reload, 20000);
    return () => clearInterval(t);
  }, [reload]);

  // acompanha o que está rodando AGORA (inclui disparos do agendador, não só o
  // "Testar"). Quando uma automação sai da lista de execução, ela acabou de
  // terminar → recarrega (atualiza "rodou"/próx.) e avisa.
  useEffect(() => {
    let alive = true;
    const poll = async () => {
      try {
        const r = await api.get<{ running: { id: string; started_at: string | null }[] }>("/automations/running");
        if (!alive) return;
        const next: Record<string, string> = {};
        for (const x of r.running) next[x.id] = x.started_at || new Date().toISOString();
        setRunning((prev) => {
          const finished = Object.keys(prev).filter((id) => !(id in next));
          if (finished.length) {
            reload();
            const done = itemsRef.current.find((it) => it.id === finished[0]);
            setToast(done ? `“${done.title}” concluída.` : "Automação concluída.");
          }
          return next;
        });
      } catch { /* silencioso */ }
    };
    poll();
    const t = setInterval(poll, 3000);
    return () => { alive = false; clearInterval(t); };
  }, [reload]);

  // relógio de 1s só enquanto algo roda (para o tempo decorrido subir na tela)
  useEffect(() => {
    if (!Object.keys(running).length) return;
    const t = setInterval(() => setTick((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, [running]);

  // some com o aviso sozinho. Uma instrução longa — com um endereço a digitar —
  // não se lê em 3,5s: fica mais tempo.
  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), toast.length > 80 ? 12000 : 3500);
    return () => clearTimeout(t);
  }, [toast]);

  async function toggle(a: Automation) {
    await api.post(`/automations/${a.id}/toggle`);
    reload();
  }
  async function remove(a: Automation) {
    const ok = await confirm({
      title: "Excluir automação?",
      body: <>Isso vai excluir <span className="font-medium text-ink">{a.title}</span>.</>,
      confirmLabel: "Excluir",
      danger: true,
    });
    if (!ok) return;
    await api.del(`/automations/${a.id}`);
    reload();
  }
  async function runNow(a: Automation) {
    setBusy(a.id);
    try {
      // dispara em segundo plano: volta na hora, sem travar. O acompanhamento é
      // pelo chip "Em execução" (poll de /running) e pela janela ao vivo.
      const r = await api.post<{ ok: boolean; started?: boolean; already_running?: boolean; error?: string }>(
        `/automations/${a.id}/run`,
      );
      if (r.already_running) setToast("Já está em execução.");
      else setToast(`“${a.title}” iniciada.`);
      setRunning((prev) => ({ ...prev, [a.id]: prev[a.id] || new Date().toISOString() }));
      setLiveRun(a); // abre a janela: dá pra ver o processo em tempo real
    } catch (e) {
      setToast(e instanceof ApiError ? e.message : "Falha ao executar");
    } finally {
      setBusy(null);
    }
  }
  async function openNote(n: AppNotification) {
    if (!n.read) await api.patch(`/notifications/${n.id}`).catch(() => {});
    if (n.chat_id) onOpenChat(n.chat_id);
    else reload();
  }
  async function readAll() {
    await api.post("/notifications/read-all").catch(() => {});
    reload();
  }
  async function clearAll() {
    const ok = await confirm({
      title: "Limpar notificações?",
      body: <>Isso vai apagar <span className="font-medium text-ink">todas as {notes.length}</span> notificações.</>,
      confirmLabel: "Apagar todas",
      danger: true,
    });
    if (!ok) return;
    await api.del("/notifications").catch(() => {});
    reload();
  }

  const unread = notes.filter((n) => !n.read).length;
  const q = query.trim().toLowerCase();
  const filtered = items.filter((a) => {
    if (kindFilter !== "all" && a.kind !== kindFilter) return false;
    if (q && !a.title.toLowerCase().includes(q) && !scheduleLabel(a).toLowerCase().includes(q)) return false;
    return true;
  });

  return (
    <div className="flex h-full flex-1 flex-col bg-bg">
      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto max-w-5xl px-3 py-6 sm:px-6">
          <div className="mb-6 flex flex-wrap items-center justify-between gap-3">
            <div className="flex min-w-0 items-center gap-3">
              {onBack && (
                <button
                  onClick={onBack}
                  title="Espaço de Trabalho"
                  aria-label="Voltar ao Espaço de Trabalho"
                  className="flex h-8 w-8 flex-none items-center justify-center rounded-xl border border-transparent bg-surface text-ink-soft transition-colors hover:border-border hover:bg-surface2 hover:text-ink"
                >
                  <ChevronLeft size={18} />
                </button>
              )}
              <h1 className="truncate text-2xl font-bold text-ink">Automações</h1>
            </div>
            <button
              onClick={() => setCreating(true)}
              className="flex items-center gap-1.5 rounded-full bg-accent px-4 py-1.5 text-sm font-medium text-white transition-colors hover:bg-accent-hover"
            >
              <Plus size={15} /> Nova automação
            </button>
          </div>
          <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
          {/* lista de automações */}
          <div className="space-y-3 lg:col-span-2">
            {items.length > 1 && (
              <div className="relative">
                <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
                <input
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="Buscar automações…"
                  className="w-full rounded-xl border border-border bg-surface py-2 pl-9 pr-9 text-sm text-ink placeholder:text-muted focus:border-accent/50 focus:outline-none"
                />
                {query && (
                  <button
                    onClick={() => setQuery("")}
                    title="Limpar busca"
                    className="absolute right-2 top-1/2 -translate-y-1/2 rounded-md p-1 text-muted transition-colors hover:bg-hover hover:text-ink"
                  >
                    <X size={14} />
                  </button>
                )}
              </div>
            )}
            {items.length > 0 && (
              <div className="flex flex-wrap gap-2">
                {KIND_FILTERS.map((f) => (
                  <button
                    key={f.key}
                    onClick={() => setKindFilter(f.key)}
                    className={`rounded-full border px-3 py-1 text-xs transition-colors ${
                      kindFilter === f.key
                        ? "border-accent/40 bg-accent/15 text-ink"
                        : "border-border bg-surface text-muted hover:bg-hover hover:text-ink"
                    }`}
                  >
                    {f.label}
                  </button>
                ))}
              </div>
            )}
            {items.length === 0 ? (
              <div className="flex flex-col items-center gap-2 rounded-2xl border border-dashed border-border py-16 text-center">
                <CalendarClock size={32} className="text-muted" />
                <p className="text-sm text-muted">Nenhuma automação ainda.</p>
              </div>
            ) : filtered.length === 0 ? (
              <div className="flex flex-col items-center gap-2 rounded-2xl border border-dashed border-border py-12 text-center">
                <Search size={28} className="text-muted" />
                <p className="text-sm text-muted">{query ? `Nenhuma automação encontrada para “${query}”.` : "Nenhuma automação com esse filtro."}</p>
              </div>
            ) : (
              filtered.map((a) => {
                const pill = statePill(a);
                return (
                <div key={a.id} className="rounded-2xl border border-border bg-surface p-4 transition-colors hover:border-accent/40">
                  <div className="flex items-start gap-3">
                    <button
                      onClick={() => setEditing(a)}
                      title="Editar automação"
                      className="flex min-w-0 flex-1 items-start gap-3 text-left"
                    >
                      <span className={`mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-xl ${KIND_TILE[a.kind] ?? KIND_TILE.scheduled}`}>
                        {a.kind === "monitor" ? <Eye size={17} /> : a.kind === "reminder" ? <Bell size={17} /> : <Clock size={17} />}
                      </span>
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2">
                          <p className="truncate text-sm font-medium text-ink">{a.title}</p>
                          <span className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide ${pill.cls}`}>{pill.label}</span>
                        </div>
                        <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-muted">
                          <span>{scheduleLabel(a)}</span>
                          {a.enabled && a.next_run_at && <><span>·</span><span className="font-mono tabular-nums">próx. {fmtWhen(a.next_run_at)}</span></>}
                          {a.run_count > 0 && <><span>·</span><span className="font-mono tabular-nums">{a.run_count}× rodou</span></>}
                        </div>
                        {a.last_error && <p className="mt-1 truncate text-xs text-red-400">Erro: {a.last_error}</p>}
                      </div>
                    </button>
                    <div className="flex shrink-0 items-center gap-1">
                      {running[a.id] ? (
                        <button
                          onClick={() => setLiveRun(a)}
                          title="Em execução — clique para ver o processo"
                          className="flex items-center gap-1.5 rounded-full bg-accent/15 px-2.5 py-1 text-xs font-medium text-accent-hover transition-colors hover:bg-accent/25"
                        >
                          <Loader2 size={13} className="animate-spin" />
                          <span className="font-mono tabular-nums">{fmtElapsed(running[a.id])}</span>
                        </button>
                      ) : (
                        <button onClick={() => runNow(a)} disabled={busy === a.id} title="Testar agora" className="rounded-lg p-1.5 text-muted transition-colors hover:bg-hover hover:text-ink disabled:opacity-50">
                          {busy === a.id ? <Loader2 size={15} className="animate-spin" /> : <Zap size={15} />}
                        </button>
                      )}
                      <button onClick={() => setHistoryFor(a)} title="Histórico de execuções" className="rounded-lg p-1.5 text-muted transition-colors hover:bg-hover hover:text-ink">
                        <History size={15} />
                      </button>
                      <button onClick={() => toggle(a)} title={a.enabled ? "Pausar" : "Ativar"} className="rounded-lg p-1.5 text-muted transition-colors hover:bg-hover hover:text-ink">
                        {a.enabled ? <Pause size={15} /> : <Play size={15} />}
                      </button>
                      <button onClick={() => remove(a)} title="Excluir" className="rounded-lg p-1.5 text-muted transition-colors hover:bg-hover hover:text-red-400">
                        <Trash2 size={15} />
                      </button>
                    </div>
                  </div>
                </div>
                );
              })
            )}
          </div>

          {/* notificações */}
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <p className="flex items-center gap-1.5 text-sm font-semibold text-ink">
                <Bell size={15} /> Notificações {unread > 0 && <span className="rounded-full bg-accent px-1.5 text-[11px] font-medium text-white">{unread}</span>}
              </p>
              <div className="flex items-center gap-2">
                {pushOk && (
                  <button
                    onClick={togglePush}
                    disabled={pushBusy}
                    title={pushOn ? "Notificações push ativas neste dispositivo — clique para desativar" : "Ativar notificações push neste dispositivo"}
                    className={`rounded-lg p-1 transition-colors disabled:opacity-50 ${pushOn ? "text-accent-hover hover:bg-hover" : "text-muted hover:bg-hover hover:text-ink"}`}
                  >
                    {pushBusy ? <Loader2 size={15} className="animate-spin" /> : pushOn ? <BellRing size={15} /> : <BellOff size={15} />}
                  </button>
                )}
                {unread > 0 && <button onClick={readAll} className="text-xs text-muted hover:text-ink">Marcar todas</button>}
                {notes.length > 0 && (
                  <button onClick={clearAll} title="Apagar todas as notificações" className="rounded-lg p-1 text-muted transition-colors hover:bg-hover hover:text-red-400">
                    <Trash2 size={15} />
                  </button>
                )}
              </div>
            </div>
            <div className="space-y-1.5">
              {notes.length === 0 ? (
                <p className="rounded-xl border border-dashed border-border px-3 py-6 text-center text-xs text-muted">Sem notificações.</p>
              ) : (
                notes.map((n) => (
                  <button
                    key={n.id}
                    onClick={() => openNote(n)}
                    className={`block w-full rounded-xl border px-3 py-2 text-left transition-colors hover:bg-hover ${n.read ? "border-border bg-surface" : "border-accent/40 bg-accent/10"}`}
                  >
                    <p className="truncate text-xs font-medium text-ink">{n.title || "Automação"}</p>
                    <p className="mt-0.5 line-clamp-2 text-xs text-muted">{n.body}</p>
                    <p className="mt-1 text-[10px] text-muted">{fmtWhen(n.created_at)}</p>
                  </button>
                ))
              )}
            </div>
          </div>
          </div>
        </div>
      </div>

      {toast && (
        <button
          onClick={() => setToast(null)}
          className="fixed bottom-5 left-1/2 max-w-[min(92vw,30rem)] -translate-x-1/2 rounded-2xl bg-surface2 px-4 py-2 text-center text-sm text-ink shadow-menu"
        >
          {toast}
        </button>
      )}

      {(creating || editing) && (
        <AutomationEditor
          automation={editing}
          onClose={() => { setCreating(false); setEditing(null); }}
          onSaved={() => { setCreating(false); setEditing(null); reload(); }}
        />
      )}

      {historyFor && (
        <RunHistoryModal
          automation={historyFor}
          onOpenChat={(id) => { setHistoryFor(null); onOpenChat(id); }}
          onClose={() => setHistoryFor(null)}
        />
      )}

      {liveRun && (
        <RunProgressModal
          automation={liveRun}
          isRunning={!!running[liveRun.id]}
          startedAt={running[liveRun.id] ?? null}
          onOpenChat={(id) => { setLiveRun(null); onOpenChat(id); }}
          onClose={() => setLiveRun(null)}
        />
      )}
    </div>
  );
}

/** Acompanhamento ao vivo de um disparo: tempo decorrido enquanto roda e, ao
 *  terminar, o resultado (status + texto) com atalho para a conversa gerada. */
function RunProgressModal({
  automation, isRunning, startedAt, onOpenChat, onClose,
}: {
  automation: Automation;
  isRunning: boolean;
  startedAt: string | null;
  onOpenChat: (chatId: string) => void;
  onClose: () => void;
}) {
  const [result, setResult] = useState<AutomationRun | null>(null);
  const [, setTick] = useState(0);

  useEffect(() => {
    if (!isRunning) return;
    const t = setInterval(() => setTick((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, [isRunning]);

  // parou de rodar → busca a execução mais recente para exibir o resultado
  useEffect(() => {
    if (isRunning) { setResult(null); return; }
    let alive = true;
    api.get<AutomationRun[]>(`/automations/${automation.id}/runs`)
      .then((rs) => { if (alive) setResult(rs[0] ?? null); })
      .catch(() => {});
    return () => { alive = false; };
  }, [isRunning, automation.id]);

  const s = result ? (RUN_STATUS[result.status] ?? RUN_STATUS.ok) : null;

  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/60 p-4" onClick={onClose}>
      <div onClick={(e) => e.stopPropagation()} className="flex max-h-[80vh] w-full max-w-lg flex-col overflow-hidden rounded-2xl border border-border bg-surface shadow-2xl">
        <div className="flex items-center justify-between border-b border-border px-4 py-3">
          <span className="flex items-center gap-2 text-sm font-semibold text-ink">
            {isRunning ? <Loader2 size={16} className="animate-spin text-accent-hover" /> : <Zap size={16} className="text-muted" />}
            {automation.title}
          </span>
          <button onClick={onClose} className="rounded-lg p-1 text-muted hover:bg-hover hover:text-ink"><X size={16} /></button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto p-4">
          {isRunning ? (
            <div className="flex flex-col items-center justify-center gap-3 py-10 text-center">
              <Loader2 size={26} className="animate-spin text-accent-hover" />
              <p className="text-sm font-medium text-ink">Trabalhando em segundo plano…</p>
              <p className="font-mono text-3xl tabular-nums text-ink">{startedAt ? fmtElapsed(startedAt) : "0:00"}</p>
              <p className="max-w-xs text-xs text-muted">Pode fechar esta janela — a execução continua e você é avisado quando terminar.</p>
            </div>
          ) : result ? (
            <div className="space-y-3">
              <div className="flex items-center gap-2">
                {s && <span className={`flex items-center gap-1 text-sm font-medium ${s.cls}`}>{s.icon} {s.label}</span>}
                <span className="ml-auto text-xs text-muted">{fmtWhen(result.created_at)}</span>
              </div>
              {(result.error || result.text) && (
                <div className={`max-h-64 overflow-y-auto whitespace-pre-wrap rounded-xl border border-border bg-bg p-3 text-xs leading-5 ${result.error ? "text-rose-400" : "text-ink-soft"}`}>
                  {result.error || result.text}
                </div>
              )}
              {result.chat_id && (
                <button onClick={() => onOpenChat(result.chat_id!)} className="w-full rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-accent-hover">
                  Abrir conversa
                </button>
              )}
            </div>
          ) : (
            <p className="flex items-center justify-center gap-2 py-10 text-sm text-muted"><Loader2 size={14} className="animate-spin" /> carregando…</p>
          )}
        </div>
      </div>
    </div>
  );
}

const RUN_STATUS: Record<string, { label: string; cls: string; icon: ReactNode }> = {
  ok: { label: "OK", cls: "text-emerald-500", icon: <Check size={13} /> },
  error: { label: "Erro", cls: "text-rose-500", icon: <AlertTriangle size={13} /> },
  no_change: { label: "Sem novidades", cls: "text-muted", icon: <Minus size={13} /> },
  skipped: { label: "Pulada", cls: "text-muted", icon: <Minus size={13} /> },
};

/** Histórico de execuções de uma automação (agendadas + testes manuais). */
function RunHistoryModal({
  automation, onOpenChat, onClose,
}: {
  automation: Automation;
  onOpenChat: (chatId: string) => void;
  onClose: () => void;
}) {
  const [runs, setRuns] = useState<AutomationRun[] | null>(null);
  useEffect(() => {
    api.get<AutomationRun[]>(`/automations/${automation.id}/runs`).then(setRuns).catch(() => setRuns([]));
  }, [automation.id]);

  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/60 p-4" onClick={onClose}>
      <div onClick={(e) => e.stopPropagation()} className="flex max-h-[80vh] w-full max-w-lg flex-col overflow-hidden rounded-2xl border border-border bg-surface shadow-2xl">
        <div className="flex items-center justify-between border-b border-border px-4 py-3">
          <span className="flex items-center gap-2 text-sm font-semibold text-ink">
            <History size={16} className="text-muted" /> Histórico — {automation.title}
          </span>
          <button onClick={onClose} className="rounded-lg p-1 text-muted hover:bg-hover hover:text-ink"><X size={16} /></button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto p-2">
          {runs === null ? (
            <p className="flex items-center justify-center gap-2 py-10 text-sm text-muted"><Loader2 size={14} className="animate-spin" /> carregando…</p>
          ) : runs.length === 0 ? (
            <p className="py-10 text-center text-sm text-muted">Nenhuma execução ainda.</p>
          ) : (
            <ul className="space-y-1">
              {runs.map((r) => {
                const s = RUN_STATUS[r.status] ?? RUN_STATUS.ok;
                const clickable = !!r.chat_id;
                return (
                  <li key={r.id}>
                    <button
                      onClick={() => r.chat_id && onOpenChat(r.chat_id)}
                      disabled={!clickable}
                      className={`w-full rounded-xl border border-border px-3 py-2 text-left transition-colors ${clickable ? "hover:border-accent/40 hover:bg-hover" : "cursor-default"}`}
                    >
                      <div className="flex items-center gap-2">
                        <span className={`flex items-center gap-1 text-xs font-medium ${s.cls}`}>{s.icon} {s.label}</span>
                        {r.trigger === "manual" && <span className="rounded-full bg-surface2 px-1.5 text-[10px] text-muted">teste</span>}
                        <span className="ml-auto text-[11px] text-muted">{fmtWhen(r.created_at)}</span>
                      </div>
                      {(r.error || r.text) && (
                        <p className={`mt-1 line-clamp-2 text-xs ${r.error ? "text-rose-400" : "text-muted"}`}>{r.error || r.text}</p>
                      )}
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}
