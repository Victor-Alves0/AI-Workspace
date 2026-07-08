"use client";

import { useCallback, useEffect, useState } from "react";
import { Calendar, Check, ChevronLeft, Loader2, Mail, Plus, TriangleAlert, Trash2, Wifi } from "lucide-react";
import { api, API_URL, ApiError } from "@/lib/api";
import { useConfirm } from "./ConfirmDialog";

interface Account {
  id: string;
  email: string;
  connected_at: string;
}
interface GoogleStatus {
  configured: boolean;
  is_admin: boolean;
  client_id: string;
  redirect_uri: string;
  accounts: Account[];
}

/** Tela de detalhe "Google Workspace" (aberta pelo card em Integrações):
 *  configura o app OAuth (Client ID/Secret — só admin) e gerencia as CONTAS
 *  Google conectadas do usuário (Gmail + Agenda). */
export default function GoogleWorkspacePanel({ onBack }: { onBack: () => void }) {
  const confirm = useConfirm();
  const [st, setSt] = useState<GoogleStatus | null>(null);
  const [test, setTest] = useState<Record<string, "loading" | "ok" | "fail">>({});

  const load = useCallback(async () => {
    try {
      setSt(await api.get<GoogleStatus>("/integrations/google"));
    } catch {
      setSt({ configured: false, is_admin: false, client_id: "", redirect_uri: "", accounts: [] });
    }
  }, []);
  useEffect(() => {
    load();
  }, [load]);

  async function testAccount(id: string) {
    setTest((t) => ({ ...t, [id]: "loading" }));
    try {
      const r = await api.post<{ ok: boolean }>(`/integrations/google/accounts/${id}/test`);
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
          <Mail size={17} />
        </span>
        <div>
          <p className="text-sm font-semibold text-ink">Google Workspace</p>
          <p className="text-xs text-muted">Gmail e Agenda para os modelos usarem</p>
        </div>
      </div>

      {st == null ? (
        <div className="flex justify-center py-10"><Loader2 size={18} className="animate-spin text-muted" /></div>
      ) : (
        <>
          {st.is_admin && <OAuthAppConfig st={st} reload={load} />}

          <p className="mb-1 mt-6 text-xs font-semibold text-ink">Contas conectadas</p>
          {!st.configured ? (
            <p className="rounded-xl border border-border bg-surface px-3 py-3 text-xs text-muted">
              {st.is_admin
                ? "Configure o app OAuth acima para poder conectar contas."
                : "Um administrador precisa configurar o app do Google antes de conectar contas."}
            </p>
          ) : (
            <div className="rounded-xl border border-border bg-surface">
              {st.accounts.length === 0 ? (
                <p className="px-3 py-3 text-xs text-muted">Nenhuma conta conectada ainda.</p>
              ) : (
                st.accounts.map((a, i) => (
                  <div key={a.id} className={`flex items-center gap-3 px-3 py-2.5 ${i > 0 ? "border-t border-border" : ""}`}>
                    <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-surface2 text-[11px] font-medium text-ink">
                      {a.email[0]?.toUpperCase() || "G"}
                    </span>
                    <span className="min-w-0 flex-1 truncate text-sm text-ink">{a.email || "(conta Google)"}</span>
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
                        if (!(await confirm({ title: "Remover esta conta?", body: <>Os modelos deixarão de acessar <span className="font-medium text-ink">{a.email}</span>.</>, confirmLabel: "Remover", danger: true }))) return;
                        try { await api.del(`/integrations/google/accounts/${a.id}`); await load(); }
                        catch (e) { alert(e instanceof ApiError ? e.message : "Falha ao remover"); }
                      }}
                      title="Remover conta"
                      className="shrink-0 rounded-lg p-1.5 text-muted transition-colors hover:bg-hover hover:text-red-400"
                    >
                      <Trash2 size={15} />
                    </button>
                  </div>
                ))
              )}
              <div className="border-t border-border p-2">
                <button
                  onClick={() => { window.location.href = `${API_URL}/integrations/google/connect`; }}
                  className="flex w-full items-center justify-center gap-1.5 rounded-lg border border-dashed border-border py-2 text-sm text-ink-soft transition-colors hover:border-accent/50 hover:text-ink"
                >
                  <Plus size={15} /> Adicionar conta Google
                </button>
              </div>
            </div>
          )}
          <p className="mt-1.5 flex items-center gap-1.5 text-[11px] text-muted">
            <Calendar size={11} /> Conecte pelo computador (o Google exige <span className="text-ink-soft">localhost</span>); o acesso vale depois no celular.
          </p>
        </>
      )}
    </div>
  );
}

/** Config do app OAuth (só admin): Client ID/Secret globais + redirect a registrar. */
function OAuthAppConfig({ st, reload }: { st: GoogleStatus; reload: () => Promise<void> }) {
  const [clientId, setClientId] = useState(st.client_id);
  const [secret, setSecret] = useState("");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  async function save() {
    setSaving(true);
    try {
      await api.put("/integrations/google/oauth", { client_id: clientId.trim(), client_secret: secret.trim() || null });
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
      <p className="mb-1 flex items-center gap-1.5 text-xs font-semibold text-ink">
        App OAuth
        {st.configured && <span className="inline-flex items-center gap-0.5 text-[10px] font-normal text-green-500"><Check size={11} /> configurado</span>}
      </p>
      <div className="space-y-2 rounded-xl border border-border bg-surface p-3">
        <label className="block text-sm">
          <span className="text-ink-soft">Client ID</span>
          <input value={clientId} onChange={(e) => setClientId(e.target.value)} placeholder="…apps.googleusercontent.com"
            className="mt-1 w-full rounded-lg border border-border bg-surface2 px-3 py-1.5 text-sm text-ink outline-none focus:border-accent" />
        </label>
        <label className="block text-sm">
          <span className="text-ink-soft">Client Secret</span>
          <input type="password" value={secret} onChange={(e) => setSecret(e.target.value)}
            placeholder={st.configured ? "•••••••• (deixe em branco p/ manter)" : "GOCSPX-…"}
            className="mt-1 w-full rounded-lg border border-border bg-surface2 px-3 py-1.5 text-sm text-ink outline-none focus:border-accent" />
        </label>
        <div className="flex items-center justify-between gap-2 pt-0.5">
          <p className="min-w-0 flex-1 truncate text-[11px] text-muted">
            Redirect: <span className="text-ink-soft">{st.redirect_uri}</span>
          </p>
          <button onClick={save} disabled={saving || !clientId.trim()}
            className="shrink-0 rounded-full bg-accent px-4 py-1.5 text-xs font-medium text-white transition-colors hover:bg-accent-hover disabled:opacity-60">
            {saving ? "…" : saved ? "Salvo ✓" : "Salvar"}
          </button>
        </div>
      </div>
      <p className="mt-1 text-[11px] leading-4 text-muted">
        Crie um OAuth Client (tipo Web) no Google Cloud, registre o Redirect acima e ative as APIs de Gmail e Calendar.
      </p>
    </div>
  );
}
