"use client";

import { useCallback, useEffect, useState } from "react";
import {
  Check, Clock, Copy, ExternalLink, Link2, Lock, LockOpen, RefreshCw, Share2, Trash2, X,
} from "lucide-react";
import { api } from "@/lib/api";
import { copyText } from "@/lib/clipboard";
import { useConfirm, usePrompt } from "./ConfirmDialog";

type SharedChat = {
  id: string;
  title: string;
  public_id: string;
  url: string;
  has_password: boolean;
  expires_at: string | null;
  expired: boolean;
  created_at: string;
  updated_at: string;
};

// opções de validade (horas) do link
const TTL_OPTS: { key: string; hours: number | null; label: string }[] = [
  { key: "never", hours: null, label: "Sem prazo" },
  { key: "1", hours: 1, label: "1 hora" },
  { key: "24", hours: 24, label: "24 horas" },
  { key: "168", hours: 168, label: "7 dias" },
  { key: "720", hours: 720, label: "30 dias" },
];

function fmtExpiry(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleString(undefined, { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}

/** Gerenciador dos chats compartilhados: link, senha, validade, trocar link, revogar. */
export default function SharedChatsModal({ onClose }: { onClose: () => void }) {
  const confirm = useConfirm();
  const prompt = usePrompt();
  const [items, setItems] = useState<SharedChat[] | null>(null);
  const [copied, setCopied] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    try { setItems(await api.get<SharedChat[]>("/chats/shared")); } catch { setItems([]); }
  }, []);
  useEffect(() => { load(); }, [load]);

  const publicUrl = (c: SharedChat) =>
    typeof window !== "undefined" ? `${window.location.origin}/shared/${c.public_id}` : c.url;

  async function copyLink(c: SharedChat) {
    try { await copyText(publicUrl(c)); setCopied(c.id); setTimeout(() => setCopied(null), 1400); } catch {}
  }

  async function patchShare(c: SharedChat, body: Record<string, unknown>) {
    setBusy(c.id);
    try { await api.patch(`/chats/${c.id}/share`, body); await load(); } finally { setBusy(null); }
  }

  async function setPassword(c: SharedChat, change: boolean) {
    const raw = await prompt({
      title: change ? "Trocar senha do link" : "Proteger com senha",
      body: "Quem abrir o link precisará digitar esta senha para ver a conversa.",
      placeholder: "Nova senha",
      password: true,
      confirmLabel: "Salvar",
    });
    if (raw === null) return;
    if (!raw.trim()) return;
    await patchShare(c, { password: raw.trim() });
  }

  async function removePassword(c: SharedChat) {
    await patchShare(c, { password: "" });
  }

  async function setTtl(c: SharedChat, key: string) {
    const opt = TTL_OPTS.find((o) => o.key === key);
    if (!opt) return;
    const expires_at = opt.hours == null ? null : new Date(Date.now() + opt.hours * 3600_000).toISOString();
    await patchShare(c, { expires_at });
  }

  async function rotate(c: SharedChat) {
    if (!(await confirm({
      title: "Gerar um novo link?",
      body: <span className="text-muted">O link atual para de funcionar imediatamente. A senha e a validade são mantidas.</span>,
      confirmLabel: "Gerar novo link",
    }))) return;
    setBusy(c.id);
    try { await api.post(`/chats/${c.id}/share/rotate`); await load(); } finally { setBusy(null); }
  }

  async function unshare(c: SharedChat) {
    if (!(await confirm({
      title: "Deixar de compartilhar?",
      body: <span className="text-muted">“{c.title}” volta a ser privado e o link deixa de funcionar.</span>,
      confirmLabel: "Deixar de compartilhar", danger: true,
    }))) return;
    setBusy(c.id);
    try { await api.del(`/chats/${c.id}/share`); await load(); } finally { setBusy(null); }
  }

  return (
    <div className="fixed inset-0 z-[80] flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm" onClick={onClose}>
      <div onClick={(e) => e.stopPropagation()} className="flex max-h-[85vh] w-full max-w-2xl flex-col overflow-hidden rounded-2xl border border-border bg-surface shadow-2xl">
        <div className="flex items-center gap-2 border-b border-border px-5 py-3.5">
          <Share2 size={18} className="text-accent-hover" />
          <span className="flex-1 text-sm font-semibold text-ink">Chats compartilhados</span>
          <button onClick={onClose} className="rounded-lg p-1 text-muted hover:bg-hover hover:text-ink"><X size={18} /></button>
        </div>

        <div className="overflow-y-auto px-5 py-4">
          {items === null ? (
            <p className="py-16 text-center text-sm text-muted">Carregando…</p>
          ) : items.length === 0 ? (
            <div className="flex flex-col items-center gap-2 py-16 text-center">
              <Link2 size={26} className="text-muted" />
              <p className="text-sm text-ink">Nenhum chat compartilhado</p>
              <p className="max-w-sm text-xs text-muted">Abra uma conversa e use “Compartilhar” para gerar um link público — ele aparece aqui para você gerenciar.</p>
            </div>
          ) : (
            <ul className="flex flex-col gap-3">
              {items.map((c) => (
                <li key={c.id} className="rounded-xl border border-border bg-bg p-3.5">
                  <div className="flex items-start gap-2">
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-medium text-ink">{c.title}</p>
                      <div className="mt-0.5 flex flex-wrap items-center gap-2 text-[11px]">
                        <span className={`flex items-center gap-1 ${c.has_password ? "text-emerald-500" : "text-muted"}`}>
                          {c.has_password ? <Lock size={11} /> : <LockOpen size={11} />}
                          {c.has_password ? "Com senha" : "Sem senha"}
                        </span>
                        <span className={`flex items-center gap-1 ${c.expired ? "text-rose-500" : "text-muted"}`}>
                          <Clock size={11} />
                          {c.expired ? "Expirado" : c.expires_at ? `Expira ${fmtExpiry(c.expires_at)}` : "Sem prazo"}
                        </span>
                      </div>
                    </div>
                    <a href={publicUrl(c)} target="_blank" rel="noreferrer noopener" title="Abrir link" className="shrink-0 rounded-lg p-1.5 text-muted hover:bg-hover hover:text-ink">
                      <ExternalLink size={15} />
                    </a>
                    <button onClick={() => unshare(c)} disabled={busy === c.id} title="Deixar de compartilhar" className="shrink-0 rounded-lg p-1.5 text-muted hover:bg-hover hover:text-rose-500 disabled:opacity-50">
                      <Trash2 size={15} />
                    </button>
                  </div>

                  {/* link + copiar */}
                  <div className="mt-2.5 flex items-center gap-2 rounded-lg border border-border bg-surface px-2.5 py-1.5">
                    <Link2 size={13} className="shrink-0 text-muted" />
                    <span className="min-w-0 flex-1 truncate font-mono text-xs text-ink-soft">{publicUrl(c)}</span>
                    <button onClick={() => copyLink(c)} title="Copiar link" className="shrink-0 text-muted hover:text-ink">
                      {copied === c.id ? <Check size={14} className="text-green-400" /> : <Copy size={14} />}
                    </button>
                  </div>

                  {/* ações: senha · validade · trocar link */}
                  <div className="mt-2.5 flex flex-wrap items-center gap-2">
                    {c.has_password ? (
                      <>
                        <button onClick={() => setPassword(c, true)} disabled={busy === c.id} className="flex items-center gap-1.5 rounded-full border border-border px-3 py-1 text-xs text-ink transition-colors hover:bg-hover disabled:opacity-50">
                          <Lock size={12} /> Trocar senha
                        </button>
                        <button onClick={() => removePassword(c)} disabled={busy === c.id} className="flex items-center gap-1.5 rounded-full border border-border px-3 py-1 text-xs text-muted transition-colors hover:text-ink disabled:opacity-50">
                          <LockOpen size={12} /> Remover senha
                        </button>
                      </>
                    ) : (
                      <button onClick={() => setPassword(c, false)} disabled={busy === c.id} className="flex items-center gap-1.5 rounded-full border border-border px-3 py-1 text-xs text-ink transition-colors hover:bg-hover disabled:opacity-50">
                        <Lock size={12} /> Definir senha
                      </button>
                    )}

                    <label className="flex items-center gap-1.5 rounded-full border border-border px-3 py-1 text-xs text-ink">
                      <Clock size={12} className="text-muted" />
                      <select
                        value=""
                        onChange={(e) => { if (e.target.value) setTtl(c, e.target.value); e.target.value = ""; }}
                        disabled={busy === c.id}
                        className="bg-transparent text-xs text-ink outline-none disabled:opacity-50"
                      >
                        <option value="">Validade…</option>
                        {TTL_OPTS.map((o) => <option key={o.key} value={o.key}>{o.label}</option>)}
                      </select>
                    </label>

                    <button onClick={() => rotate(c)} disabled={busy === c.id} title="Gera um novo link e invalida o atual" className="flex items-center gap-1.5 rounded-full border border-border px-3 py-1 text-xs text-ink transition-colors hover:bg-hover disabled:opacity-50">
                      <RefreshCw size={12} /> Trocar link
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}
