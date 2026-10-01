"use client";

import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, Check, Copy, KeyRound, Laptop, Link2, Loader2, Pencil, Plus, RefreshCw, Server, Trash2, X } from "lucide-react";
import { api, API_URL } from "@/lib/api";
import { useConfirm } from "@/components/ConfirmDialog";
import { InfoDot } from "@/components/ui";
import { tr } from "@/lib/i18n";

type Peer = {
  id: string;
  name: string;
  url: string | null;
  active: boolean;
  accounts: string[];
  last_sync_at: string | null;
  last_error: string | null;
  stats: { sent?: number; received?: number; conflicts?: number; pending?: number; blobs?: number };
  pending: number;
  same_version: boolean;
};
type Overview = { instance: { id: string; name: string }; enabled: boolean; peers: Peer[] };

const SYNC_HINT =
  tr("Sincroniza nas duas direções as contas com o mesmo e-mail: conversas, anexos, artefatos, modelos, ") +
  tr("prompts, skills, ferramentas, conhecimento, cérebros, memórias, integrações e chaves de API. Ficam só ") +
  tr("em cada instância: automações, bots de canal (WhatsApp, Telegram, Discord, Slack), projetos do Codespace ") +
  tr("e as configurações da própria instância. Se a mesma coisa for editada nos dois lados, vale a edição mais recente.");

function quando(iso: string | null): string {
  if (!iso) return "nunca";
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return tr("agora há pouco");
  if (s < 3600) return tr("há {0} min", { "0": Math.round(s / 60) });
  if (s < 86400) return `há ${Math.round(s / 3600)} h`;
  return new Date(iso).toLocaleString();
}

function CopyButton({ value }: { value: string }) {
  const [ok, setOk] = useState(false);
  return (
    <button
      type="button"
      onClick={() => { void navigator.clipboard?.writeText(value).then(() => { setOk(true); setTimeout(() => setOk(false), 1500); }); }}
      title={tr("Copiar")}
      aria-label={tr("Copiar")}
      className="flex h-7 w-7 flex-none items-center justify-center rounded-lg text-muted transition-colors hover:bg-hover hover:text-ink"
    >
      {ok ? <Check size={14} className="text-green-400" /> : <Copy size={14} />}
    </button>
  );
}

export default function SyncAdmin() {
  const confirm = useConfirm();
  const [data, setData] = useState<Overview | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [panel, setPanel] = useState<"code" | "add" | null>(null);
  const [code, setCode] = useState<{ code: string; expires_at: number } | null>(null);
  const [now, setNow] = useState(Date.now());
  const [url, setUrl] = useState("");
  const [pairCode, setPairCode] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [addErr, setAddErr] = useState<string | null>(null);
  const [editName, setEditName] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setData(await api.get<Overview>("/admin/sync"));
      setErr(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : tr("Falha ao carregar"));
    }
  }, []);
  useEffect(() => {
    void load();
    const t = setInterval(() => { void load(); }, 10000);
    return () => clearInterval(t);
  }, [load]);
  useEffect(() => {
    if (!code) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [code]);

  async function genCode() {
    setPanel("code");
    setBusy("code");
    try {
      setCode(await api.post<{ code: string; expires_at: number }>("/admin/sync/code"));
    } finally {
      setBusy(null);
    }
  }

  async function addPeer() {
    setBusy("add");
    setAddErr(null);
    try {
      await api.post("/admin/sync/peers", { url, code: pairCode });
      setPanel(null);
      setUrl("");
      setPairCode("");
      await load();
    } catch (e) {
      setAddErr(e instanceof Error ? e.message : tr("Não foi possível parear"));
    } finally {
      setBusy(null);
    }
  }

  async function syncNow(p: Peer) {
    setBusy(p.id);
    try {
      await api.post(`/admin/sync/peers/${p.id}/sync`);
    } catch {
      /* o erro aparece no card (last_error) */
    } finally {
      await load();
      setBusy(null);
    }
  }

  async function remove(p: Peer) {
    const ok = await confirm({
      title: `Desconectar ${p.name || "esta instância"}?`,
      body: <span className="text-muted">{tr("A sincronização para. Os dados que já vieram continuam aqui.")}</span>,
      confirmLabel: tr("Desconectar"),
      danger: true,
    });
    if (!ok) return;
    await api.del(`/admin/sync/peers/${p.id}`);
    await load();
  }

  async function saveName() {
    if (editName === null) return;
    await api.patch("/admin/sync/instance", { name: editName });
    setEditName(null);
    await load();
  }

  const restante = code ? Math.max(0, Math.round(code.expires_at - now / 1000)) : 0;
  const inputCls = "w-full rounded-lg border border-border bg-surface2 px-3 py-1.5 text-sm text-ink outline-none transition-colors placeholder:text-muted focus:border-accent";

  if (!data && !err) {
    return <p className="flex items-center gap-2 text-sm text-muted"><Loader2 size={15} className="animate-spin" />  {tr("Carregando…")}</p>;
  }
  if (err && !data) return <p className="text-sm text-red-400">{err}</p>;
  const d = data!;

  return (
    <div className="space-y-4">
      {/* esta instância */}
      <div className="space-y-3 rounded-xl border border-border bg-surface p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="min-w-0">
            <p className="text-[11px] font-medium uppercase tracking-wider text-muted">{tr("Esta instância")}</p>
            {editName === null ? (
              <p className="flex items-center gap-1.5 text-base font-semibold text-ink">
                {d.instance.name}
                <button onClick={() => setEditName(d.instance.name)} title={tr("Renomear")} aria-label={tr("Renomear")}
                  className="rounded-md p-1 text-muted transition-colors hover:bg-hover hover:text-ink">
                  <Pencil size={13} />
                </button>
              </p>
            ) : (
              <div className="mt-1 flex items-center gap-1.5">
                <input value={editName} onChange={(e) => setEditName(e.target.value)} autoFocus maxLength={120}
                  onKeyDown={(e) => { if (e.key === "Enter") void saveName(); if (e.key === "Escape") setEditName(null); }}
                  className="w-56 rounded-lg border border-border bg-surface2 px-2.5 py-1 text-sm text-ink outline-none focus:border-accent" />
                <button onClick={() => void saveName()} className="rounded-lg p-1.5 text-green-400 hover:bg-hover" aria-label={tr("Salvar")}><Check size={15} /></button>
                <button onClick={() => setEditName(null)} className="rounded-lg p-1.5 text-muted hover:bg-hover" aria-label={tr("Cancelar")}><X size={15} /></button>
              </div>
            )}
            <p className="mt-0.5 flex items-center gap-1 text-xs text-muted">
              <span className="font-mono">{API_URL}</span>
              <CopyButton value={API_URL} />
            </p>
          </div>
          <div className="flex items-center gap-2">
            <InfoDot text={SYNC_HINT} />
            <button onClick={() => void genCode()}
              className={`flex items-center gap-1.5 rounded-full border px-4 py-1.5 text-sm transition-colors ${panel === "code" ? "border-accent/60 bg-accent/10 text-ink" : "border-border text-ink-soft hover:bg-hover hover:text-ink"}`}>
              <KeyRound size={14} />  {tr("Gerar código")}
            </button>
            <button onClick={() => { setPanel(panel === "add" ? null : "add"); setAddErr(null); }}
              className="flex items-center gap-1.5 rounded-full bg-accent px-4 py-1.5 text-sm font-medium text-white transition-colors hover:bg-accent-hover">
              <Plus size={14} />  {tr("Adicionar instância")}
            </button>
          </div>
        </div>

        {panel === "code" && (
          <div className="flex flex-wrap items-center gap-4 rounded-lg border border-border bg-surface2/40 p-3">
            {busy === "code" || !code ? (
              <Loader2 size={16} className="animate-spin text-muted" />
            ) : restante > 0 ? (
              <>
                <div className="flex items-center gap-1">
                  <span className="font-mono text-2xl font-semibold tracking-[0.2em] text-ink">{code.code}</span>
                  <CopyButton value={code.code} />
                </div>
                <p className="text-xs text-muted">
                  
                  {tr("Válido por")} {Math.floor(restante / 60)}:{String(restante % 60).padStart(2, "0")}  {tr("· uso único")}
                </p>
              </>
            ) : (
              <p className="text-sm text-muted">{tr("O código expirou.")} <button onClick={() => void genCode()} className="text-accent-hover hover:underline">{tr("Gerar outro")}</button></p>
            )}
            <button onClick={() => { setPanel(null); setCode(null); }} className="ml-auto rounded-lg p-1.5 text-muted hover:bg-hover hover:text-ink" aria-label={tr("Fechar")}><X size={15} /></button>
          </div>
        )}

        {panel === "add" && (
          <div className="space-y-2.5 rounded-lg border border-border bg-surface2/40 p-3">
            <div className="grid gap-2 sm:grid-cols-[1fr_180px]">
              <input value={url} onChange={(e) => setUrl(e.target.value)} placeholder={tr("Endereço da outra instância (ex.: https://ia.minhacasa.com)")}
                autoFocus className={inputCls} />
              <input value={pairCode} onChange={(e) => setPairCode(e.target.value.toUpperCase())} placeholder={tr("Código")}
                className={`${inputCls} font-mono tracking-widest placeholder:font-sans placeholder:tracking-normal`} />
            </div>
            {addErr && <p className="text-xs text-red-400">{addErr}</p>}
            <div className="flex justify-end gap-2">
              <button onClick={() => setPanel(null)} className="rounded-full border border-border px-4 py-1.5 text-xs text-muted hover:text-ink">{tr("Cancelar")}</button>
              <button onClick={() => void addPeer()} disabled={busy === "add" || !url.trim() || pairCode.replace(/[^A-Z0-9]/g, "").length < 6}
                className="flex items-center gap-1.5 rounded-full bg-accent px-4 py-1.5 text-xs font-medium text-white hover:bg-accent-hover disabled:opacity-60">
                {busy === "add" ? <Loader2 size={13} className="animate-spin" /> : <Link2 size={13} />}  {tr("Parear")}
              </button>
            </div>
          </div>
        )}
      </div>

      {/* instâncias pareadas */}
      {d.peers.length === 0 ? (
        <div className="rounded-xl border border-dashed border-border px-4 py-10 text-center text-sm text-muted">
          
          {tr("Nenhuma instância pareada.")}
        </div>
      ) : (
        <div className="space-y-2.5">
          {d.peers.map((p) => (
            <div key={p.id} className="rounded-xl border border-border bg-surface p-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="flex min-w-0 items-start gap-3">
                  <span className="flex h-9 w-9 flex-none items-center justify-center rounded-xl bg-accent/15 text-accent-hover">
                    {p.active ? <Server size={18} /> : <Laptop size={18} />}
                  </span>
                  <div className="min-w-0">
                    <p className="truncate text-sm font-semibold text-ink">{p.name || tr("Instância")}</p>
                    <p className="truncate text-xs text-muted">{p.url ?? tr("Conecta a esta instância")}</p>
                    <div className="mt-1.5 flex flex-wrap gap-1">
                      {p.accounts.map((a) => (
                        <span key={a} className="rounded-full bg-surface2 px-2 py-0.5 text-[11px] text-ink-soft">{a}</span>
                      ))}
                    </div>
                  </div>
                </div>
                <div className="flex items-center gap-1.5">
                  {p.active && (
                    <button onClick={() => void syncNow(p)} disabled={busy === p.id}
                      className="flex items-center gap-1.5 rounded-full border border-border px-3.5 py-1.5 text-xs text-ink-soft transition-colors hover:bg-hover hover:text-ink disabled:opacity-60">
                      <RefreshCw size={13} className={busy === p.id ? "animate-spin" : ""} />  {tr("Sincronizar agora")}
                    </button>
                  )}
                  <button onClick={() => void remove(p)} title={tr("Desconectar")} aria-label={tr("Desconectar")}
                    className="flex h-8 w-8 items-center justify-center rounded-full text-muted transition-colors hover:bg-hover hover:text-red-400">
                    <Trash2 size={14} />
                  </button>
                </div>
              </div>
              <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-border pt-2.5 text-xs text-muted">
                {p.last_error ? (
                  <span className="flex items-center gap-1 text-red-400"><AlertTriangle size={12} /> {p.last_error}</span>
                ) : (
                  <span className="flex items-center gap-1"><Check size={12} className="text-green-400" />  {tr("Sincronizado")} {quando(p.last_sync_at)}</span>
                )}
                {(p.stats.sent ?? 0) + (p.stats.received ?? 0) > 0 && (
                  <span>{p.stats.sent ?? 0} enviadas · {p.stats.received ?? 0} recebidas</span>
                )}
                {(p.stats.conflicts ?? 0) > 0 && <span className="text-amber-300">{p.stats.conflicts} conflitos</span>}
                {p.pending > 0 && <span>{p.pending} aguardando</span>}
                {!p.same_version && (
                  <span className="flex items-center gap-1 text-amber-300"><AlertTriangle size={12} />  {tr("Versões diferentes — atualize as duas")}</span>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
