"use client";

import { useCallback, useEffect, useState } from "react";
import { Blocks, Check, ChevronLeft, Loader2, Plus, TriangleAlert, Trash2, Wifi } from "lucide-react";
import { api, API_URL, ApiError } from "@/lib/api";
import { useConfirm } from "./ConfirmDialog";

interface Account {
  id: string;
  team: string;
  auth_type: "token" | "oauth";
  avatar_url: string;
  connected_at: string;
}
interface SlackStatus {
  oauth_configured: boolean;
  is_admin: boolean;
  client_id: string;
  redirect_uri: string;
  accounts: Account[];
}

/** Tela de detalhe "Slack" (aberta pelo card em Integrações): conecta workspaces via
 *  Bot User OAuth Token (caminho principal) ou OAuth (opcional, admin), e gerencia os
 *  workspaces conectados que os modelos usam pela ferramenta Slack. */
export default function SlackPanel({ onBack, onOpenChannel }: { onBack: () => void; onOpenChannel?: () => void }) {
  const confirm = useConfirm();
  const [st, setSt] = useState<SlackStatus | null>(null);
  const [test, setTest] = useState<Record<string, "loading" | "ok" | "fail">>({});

  const load = useCallback(async () => {
    try {
      setSt(await api.get<SlackStatus>("/integrations/slack"));
    } catch {
      setSt({ oauth_configured: false, is_admin: false, client_id: "", redirect_uri: "", accounts: [] });
    }
  }, []);
  useEffect(() => { load(); }, [load]);

  async function testAccount(id: string) {
    setTest((t) => ({ ...t, [id]: "loading" }));
    try {
      const r = await api.post<{ ok: boolean }>(`/integrations/slack/accounts/${id}/test`);
      setTest((t) => ({ ...t, [id]: r.ok ? "ok" : "fail" }));
    } catch {
      setTest((t) => ({ ...t, [id]: "fail" }));
    }
  }

  return (
    <div className="pt-1">
      <button onClick={onBack} className="mb-3 flex items-center gap-1 text-sm text-muted transition-colors hover:text-ink">
        <ChevronLeft size={16} /> Voltar
      </button>

      <div className="flex items-center gap-2.5">
        <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-surface2 text-accent-hover">
          <Blocks size={17} />
        </span>
        <div className="flex-1">
          <p className="text-sm font-semibold text-ink">Slack</p>
          <p className="text-xs text-muted">Ferramenta: a IA lê e envia mensagens sob demanda</p>
        </div>
        {onOpenChannel && (
          <button onClick={onOpenChannel} className="shrink-0 rounded-lg border border-border px-2.5 py-1.5 text-xs text-ink-soft transition-colors hover:border-accent/50 hover:text-ink">
            Usar como canal →
          </button>
        )}
      </div>

      {st == null ? (
        <div className="flex justify-center py-10"><Loader2 size={18} className="animate-spin text-muted" /></div>
      ) : (
        <>
          <TokenConnect reload={load} />

          {st.is_admin && <OAuthAppConfig st={st} reload={load} />}

          <p className="mb-1 mt-6 text-xs font-semibold text-ink">Workspaces conectados</p>
          <div className="rounded-xl border border-border bg-surface">
            {st.accounts.length === 0 ? (
              <p className="px-3 py-3 text-xs text-muted">Nenhum workspace conectado ainda.</p>
            ) : (
              st.accounts.map((a, i) => (
                <div key={a.id} className={`flex items-center gap-3 px-3 py-2.5 ${i > 0 ? "border-t border-border" : ""}`}>
                  {a.avatar_url ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={a.avatar_url} alt="" className="h-8 w-8 shrink-0 rounded-lg" />
                  ) : (
                    <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-surface2 text-ink">
                      <Blocks size={15} />
                    </span>
                  )}
                  <span className="min-w-0 flex-1 truncate text-sm text-ink">
                    {a.team || "(workspace Slack)"}
                    <span className="ml-1.5 rounded-full bg-surface2 px-1.5 py-0.5 text-[10px] uppercase text-muted">{a.auth_type}</span>
                  </span>
                  <button
                    onClick={() => testAccount(a.id)}
                    disabled={test[a.id] === "loading"}
                    title="Testar conexão"
                    className={`flex shrink-0 items-center gap-1 rounded-lg px-2 py-1 text-xs transition-colors ${
                      test[a.id] === "ok" ? "text-green-500"
                      : test[a.id] === "fail" ? "text-red-400"
                      : "text-muted hover:bg-hover hover:text-ink"
                    }`}
                  >
                    {test[a.id] === "loading" ? <Loader2 size={13} className="animate-spin" />
                      : test[a.id] === "ok" ? <Check size={13} />
                      : test[a.id] === "fail" ? <TriangleAlert size={13} />
                      : <Wifi size={13} />}
                    {test[a.id] === "ok" ? "OK" : test[a.id] === "fail" ? "Falhou" : "Testar"}
                  </button>
                  <button
                    onClick={async () => {
                      if (!(await confirm({ title: "Remover este workspace?", body: <>Os modelos deixarão de acessar <span className="font-medium text-ink">{a.team}</span>.</>, confirmLabel: "Remover", danger: true }))) return;
                      try { await api.del(`/integrations/slack/accounts/${a.id}`); await load(); }
                      catch (e) { alert(e instanceof ApiError ? e.message : "Falha ao remover"); }
                    }}
                    title="Remover workspace"
                    className="shrink-0 rounded-lg p-1.5 text-muted transition-colors hover:bg-hover hover:text-red-400"
                  >
                    <Trash2 size={15} />
                  </button>
                </div>
              ))
            )}
            {st.oauth_configured && (
              <div className="border-t border-border p-2">
                <button
                  onClick={() => { window.location.href = `${API_URL}/integrations/slack/connect`; }}
                  className="flex w-full items-center justify-center gap-1.5 rounded-lg border border-dashed border-border py-2 text-sm text-ink-soft transition-colors hover:border-accent/50 hover:text-ink"
                >
                  <Plus size={15} /> Conectar com OAuth
                </button>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}

/** Conectar colando um Bot User OAuth Token (api.slack.com/apps → OAuth & Permissions). */
function TokenConnect({ reload }: { reload: () => Promise<void> }) {
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  async function connect() {
    if (!token.trim()) return;
    setBusy(true); setErr("");
    try {
      await api.post("/integrations/slack/token", { token: token.trim() });
      setToken("");
      await reload();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Falha ao conectar");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mt-4">
      <p className="mb-1 text-xs font-semibold text-ink">Conectar com token</p>
      <div className="space-y-2 rounded-xl border border-border bg-surface p-3">
        <input
          type="password" value={token} onChange={(e) => setToken(e.target.value)}
          placeholder="xoxb-… ou xoxp-…"
          className="w-full rounded-lg border border-border bg-surface2 px-3 py-1.5 text-sm text-ink outline-none focus:border-accent"
        />
        <div className="flex items-center justify-between gap-2">
          {err ? <p className="min-w-0 flex-1 truncate text-[11px] text-red-400">{err}</p>
               : <p className="min-w-0 flex-1 text-[11px] text-muted">Crie um app em api.slack.com/apps, adicione os escopos e instale-o no workspace para pegar o Bot User OAuth Token.</p>}
          <button onClick={connect} disabled={busy || !token.trim()}
            className="shrink-0 rounded-full bg-accent px-4 py-1.5 text-xs font-medium text-white transition-colors hover:bg-accent-hover disabled:opacity-60">
            {busy ? "…" : "Conectar"}
          </button>
        </div>
      </div>
    </div>
  );
}

/** Config do OAuth (só admin): Client ID/Secret globais — alternativa ao token. */
function OAuthAppConfig({ st, reload }: { st: SlackStatus; reload: () => Promise<void> }) {
  const [open, setOpen] = useState(false);
  const [clientId, setClientId] = useState(st.client_id);
  const [secret, setSecret] = useState("");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  async function save() {
    setSaving(true);
    try {
      await api.put("/integrations/slack/oauth", { client_id: clientId.trim(), client_secret: secret.trim() || null });
      setSecret("");
      setSaved(true);
      setTimeout(() => setSaved(false), 1500);
      await reload();
    } catch (e) {
      alert(e instanceof ApiError ? e.message : "Falha ao salvar");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="mt-4">
      <button onClick={() => setOpen((v) => !v)} className="mb-1 flex items-center gap-1.5 text-xs font-semibold text-ink">
        App OAuth (opcional)
        {st.oauth_configured && <span className="inline-flex items-center gap-0.5 text-[10px] font-normal text-green-500"><Check size={11} /> configurado</span>}
      </button>
      {open && (
        <>
          <div className="space-y-2 rounded-xl border border-border bg-surface p-3">
            <label className="block text-sm">
              <span className="text-ink-soft">Client ID</span>
              <input value={clientId} onChange={(e) => setClientId(e.target.value)} placeholder="client id"
                className="mt-1 w-full rounded-lg border border-border bg-surface2 px-3 py-1.5 text-sm text-ink outline-none focus:border-accent" />
            </label>
            <label className="block text-sm">
              <span className="text-ink-soft">Client Secret</span>
              <input type="password" value={secret} onChange={(e) => setSecret(e.target.value)}
                placeholder={st.oauth_configured ? "•••••••• (deixe em branco p/ manter)" : "client secret"}
                className="mt-1 w-full rounded-lg border border-border bg-surface2 px-3 py-1.5 text-sm text-ink outline-none focus:border-accent" />
            </label>
            <div className="flex items-center justify-between gap-2 pt-0.5">
              <p className="min-w-0 flex-1 truncate text-[11px] text-muted">
                Redirect URL: <span className="text-ink-soft">{st.redirect_uri}</span>
              </p>
              <button onClick={save} disabled={saving || !clientId.trim()}
                className="shrink-0 rounded-full bg-accent px-4 py-1.5 text-xs font-medium text-white transition-colors hover:bg-accent-hover disabled:opacity-60">
                {saving ? "…" : saved ? "Salvo ✓" : "Salvar"}
              </button>
            </div>
          </div>
          <p className="mt-1 text-[11px] leading-4 text-muted">
            No app do Slack, registre a Redirect URL acima em OAuth &amp; Permissions. O token acima é mais simples e não precisa disto.
          </p>
        </>
      )}
    </div>
  );
}
