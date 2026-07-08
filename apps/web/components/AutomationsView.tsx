"use client";

import { useCallback, useEffect, useState } from "react";
import { Bell, CalendarClock, Clock, Eye, Loader2, Pause, Play, Plus, Trash2, Zap } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import type { AppNotification, Automation } from "@/lib/types";
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
}: {
  /** abrir o chat de uma notificação (fecha a tela e seleciona o chat) */
  onOpenChat: (chatId: string) => void;
}) {
  const confirm = useConfirm();
  const [items, setItems] = useState<Automation[]>([]);
  const [notes, setNotes] = useState<AppNotification[]>([]);
  const [editing, setEditing] = useState<Automation | null>(null);
  const [creating, setCreating] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  const reload = useCallback(() => {
    api.get<Automation[]>("/automations").then(setItems).catch(() => {});
    api.get<AppNotification[]>("/notifications").then(setNotes).catch(() => {});
  }, []);

  useEffect(() => {
    reload();
    const t = setInterval(reload, 20000);
    return () => clearInterval(t);
  }, [reload]);

  // some com o aviso sozinho (menos o "Executando…", que fica até terminar)
  useEffect(() => {
    if (!toast || toast === "Executando…") return;
    const t = setTimeout(() => setToast(null), 3500);
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
    setToast("Executando…");
    try {
      const r = await api.post<{
        ok: boolean;
        error?: string;
        result?: { chat_id?: string; skipped?: string; changed?: boolean };
      }>(`/automations/${a.id}/run`);
      if (!r.ok) {
        setToast(`Falha: ${r.error}`);
        return;
      }
      const res = r.result ?? {};
      // torna a execução VISÍVEL: abre a conversa gerada (fecha esta tela)
      if (res.chat_id) {
        onOpenChat(res.chat_id);
        return;
      }
      if (res.skipped === "already_running") setToast("Já está em execução.");
      else if (res.changed === false) setToast("Executada — sem novidades no monitor.");
      else setToast("Automação executada.");
    } catch (e) {
      setToast(e instanceof ApiError ? e.message : "Falha ao executar");
    } finally {
      setBusy(null);
      reload();
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

  return (
    <div className="flex h-full flex-1 flex-col bg-bg">
      <div className="flex items-center gap-3 border-b border-border px-6 py-3">
        <CalendarClock size={20} className="text-accent-hover" />
        <span className="font-semibold text-ink">Automações</span>
        <button
          onClick={() => setCreating(true)}
          className="ml-auto flex items-center gap-1.5 rounded-full bg-accent px-4 py-1.5 text-sm font-medium text-white transition-colors hover:bg-accent-hover"
        >
          <Plus size={15} /> Nova automação
        </button>
      </div>

      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto grid max-w-5xl grid-cols-1 gap-6 px-6 py-6 lg:grid-cols-3">
          {/* lista de automações */}
          <div className="space-y-3 lg:col-span-2">
            {items.length === 0 ? (
              <div className="flex flex-col items-center gap-2 rounded-2xl border border-dashed border-border py-16 text-center">
                <CalendarClock size={32} className="text-muted" />
                <p className="text-sm text-muted">Nenhuma automação ainda.</p>
              </div>
            ) : (
              items.map((a) => (
                <div key={a.id} className="rounded-2xl border border-border bg-surface p-4 transition-colors hover:border-accent/40">
                  <div className="flex items-start gap-3">
                    <button
                      onClick={() => setEditing(a)}
                      title="Editar automação"
                      className="flex min-w-0 flex-1 items-start gap-3 text-left"
                    >
                      <span className={`mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-xl ${a.enabled ? "bg-accent/15 text-accent-hover" : "bg-surface2 text-muted"}`}>
                        {a.kind === "monitor" ? <Eye size={17} /> : a.kind === "reminder" ? <Bell size={17} /> : <Clock size={17} />}
                      </span>
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-sm font-medium text-ink">{a.title}</p>
                        <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-muted">
                          <span>{scheduleLabel(a)}</span>
                          <span>·</span>
                          <span>{a.enabled ? `próx.: ${fmtWhen(a.next_run_at)}` : "pausada"}</span>
                          {a.run_count > 0 && <><span>·</span><span>{a.run_count}× rodou</span></>}
                        </div>
                        {a.last_error && <p className="mt-1 truncate text-xs text-red-400">Erro: {a.last_error}</p>}
                      </div>
                    </button>
                    <div className="flex shrink-0 items-center gap-1">
                      <button onClick={() => runNow(a)} disabled={busy === a.id} title="Rodar agora" className="rounded-lg p-1.5 text-muted transition-colors hover:bg-hover hover:text-ink disabled:opacity-50">
                        {busy === a.id ? <Loader2 size={15} className="animate-spin" /> : <Zap size={15} />}
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
              ))
            )}
          </div>

          {/* notificações */}
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <p className="flex items-center gap-1.5 text-sm font-semibold text-ink">
                <Bell size={15} /> Notificações {unread > 0 && <span className="rounded-full bg-accent px-1.5 text-[11px] font-medium text-white">{unread}</span>}
              </p>
              <div className="flex items-center gap-2">
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

      {toast && (
        <div className="pointer-events-none fixed bottom-5 left-1/2 -translate-x-1/2 rounded-full bg-surface2 px-4 py-2 text-sm text-ink shadow-menu">
          {toast}
        </div>
      )}

      {(creating || editing) && (
        <AutomationEditor
          automation={editing}
          onClose={() => { setCreating(false); setEditing(null); }}
          onSaved={() => { setCreating(false); setEditing(null); reload(); }}
        />
      )}
    </div>
  );
}
