"use client";

import { useCallback, useEffect, useState } from "react";
import { Check, ChevronDown, ChevronLeft, Copy } from "lucide-react";
import { SiGoogle } from "react-icons/si";
import { api } from "@/lib/api";
import { copyText } from "@/lib/clipboard";
import { useConfirm } from "./ConfirmDialog";
import { toast } from "./Toaster";
import { InfoDot, Toggle } from "./ui";
import ConnectedAccounts, { errText, useOAuthConnect } from "./ConnectedAccounts";
import { tr } from "@/lib/i18n";

interface Account {
  id: string;
  email: string;
  connected_at: string;
  primary: boolean;
  broken: boolean;
}
interface GoogleStatus {
  configured: boolean;
  /** app em vigor para novas conexões */
  app: "own" | "builtin" | null;
  /** a instalação traz o app do AI Workspace */
  builtin: boolean;
  is_admin: boolean;
  client_id: string;
  redirect_uri: string;
  fallback: boolean;
  accounts: Account[];
}

/** Tela "Google Workspace" (card em Integrações): contas Google do usuário (Gmail +
 *  Agenda) e, para o admin, o app OAuth — escondido quando o embutido já resolve. */
export default function GoogleWorkspacePanel({ onBack }: { onBack: () => void }) {
  const confirm = useConfirm();
  const [st, setSt] = useState<GoogleStatus | null>(null);

  const load = useCallback(async () => {
    try {
      setSt(await api.get<GoogleStatus>("/integrations/google"));
    } catch {
      setSt({ configured: false, app: null, builtin: false, is_admin: false, client_id: "", redirect_uri: "", fallback: false, accounts: [] });
    }
  }, []);
  useEffect(() => { void load(); }, [load]);
  const connect = useOAuthConnect("/integrations/google", () => { void load(); });

  async function setFallback(v: boolean) {
    setSt((s) => (s ? { ...s, fallback: v } : s));
    try { await api.put("/integrations/google/prefs", { fallback: v }); }
    catch (e) { toast(errText(e, tr("Falha ao salvar."))); void load(); }
  }

  return (
    <div className="pt-1">
      <div className="mb-3 flex items-center justify-between gap-4 border-b border-border pb-2">
        <h3 className="flex items-center gap-2 text-sm font-semibold text-ink">
          <SiGoogle size={14} className="text-accent-hover" />  {tr("Google Workspace")}
        </h3>
        <button onClick={onBack} className="flex shrink-0 items-center gap-1 rounded-lg px-2 py-1 text-sm text-muted transition-colors hover:bg-hover hover:text-ink">
          <ChevronLeft size={16} />  {tr("Voltar")}
        </button>
      </div>

      {st == null ? (
        <div className="h-40" />
      ) : (
        <>
          <p className="mb-2 flex items-center gap-1.5 text-xs font-semibold text-ink">
            
            {tr("Contas conectadas")}
            {st.accounts.length > 0 && <span className="font-normal text-muted">{st.accounts.length}</span>}
            <InfoDot text={tr("Gmail e Agenda de cada conta. A principal é a que a IA usa quando você não diz qual.")} />
          </p>
          <ConnectedAccounts
            items={st.accounts.map((a) => ({ id: a.id, label: a.email || tr("Conta Google"), primary: a.primary, broken: a.broken }))}
            connect={connect}
            connectLabel={tr("Conectar conta Google")}
            disabledReason={st.configured ? undefined : st.is_admin ? tr("Configure o app do Google abaixo") : tr("Indisponível: falta o app do Google")}
            onMakePrimary={async (id) => {
              try { await api.post(`/integrations/google/accounts/${id}/primary`); await load(); }
              catch (e) { toast(errText(e, tr("Falha ao salvar."))); }
            }}
            onTest={async (id) => {
              try { return (await api.post<{ ok: boolean }>(`/integrations/google/accounts/${id}/test`)).ok; }
              catch { return false; }
              finally { void load(); }
            }}
            onRemove={async (a) => {
              if (!(await confirm({ title: tr("Remover conta?"), body: <>{tr("A IA deixa de acessar")} <span className="font-medium text-ink">{a.label}</span>.</>, confirmLabel: tr("Remover"), danger: true }))) return;
              try { await api.del(`/integrations/google/accounts/${a.id}`); await load(); }
              catch (e) { toast(errText(e, tr("Falha ao remover."))); }
            }}
          />

          {st.accounts.length > 1 && (
            <div className="mt-2 flex items-center justify-between gap-3 rounded-xl border border-border bg-surface px-3 py-2.5">
              <span className="flex items-center gap-1.5 text-sm text-ink">
                
                {tr("Usar a próxima se a principal falhar")}
                <InfoDot text={tr("Só quando o pedido não diz a conta. A IA avisa qual conta usou.")} />
              </span>
              <Toggle on={st.fallback} onChange={(v) => void setFallback(v)} />
            </div>
          )}

          {st.is_admin && <OAuthApp st={st} reload={load} />}
        </>
      )}
    </div>
  );
}

/** App OAuth (só admin). Com o app do AI Workspace embutido fica recolhido: é só
 *  para quem quer usar o próprio. */
function OAuthApp({ st, reload }: { st: GoogleStatus; reload: () => Promise<void> }) {
  const confirm = useConfirm();
  const [open, setOpen] = useState(!st.configured);
  const [clientId, setClientId] = useState(st.client_id);
  const [secret, setSecret] = useState("");
  const [saving, setSaving] = useState(false);
  const [copied, setCopied] = useState(false);
  const own = st.app === "own";

  async function save() {
    setSaving(true);
    try {
      await api.put("/integrations/google/oauth", { client_id: clientId.trim(), client_secret: secret.trim() || null });
      setSecret("");
      toast(tr("App do Google salvo."), "success");
      await reload();
    } catch (e) {
      toast(errText(e, tr("Falha ao salvar.")));
    } finally {
      setSaving(false);
    }
  }

  async function useBuiltin() {
    if (!(await confirm({ title: tr("Voltar ao app do AI Workspace?"), body: tr("Novas conexões usam o app embutido. Contas já conectadas continuam funcionando."), confirmLabel: tr("Voltar") }))) return;
    try { await api.del("/integrations/google/oauth"); setClientId(""); await reload(); }
    catch (e) { toast(errText(e, tr("Falha ao salvar."))); }
  }

  const estado = own ? tr("Próprio") : st.app === "builtin" ? tr("Do AI Workspace") : tr("Não configurado");
  return (
    <div className="mt-6">
      <button type="button" onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-1.5 text-left text-xs font-semibold text-ink">
        
        {tr("App do Google")}
        <span className={`font-normal ${st.configured ? "text-muted" : "text-amber-400"}`}>· {estado}</span>
        <ChevronDown size={14} className={`ml-auto text-muted transition-transform ${open ? "rotate-180" : ""}`} />
      </button>
      {open && (
        <div className="mt-2 space-y-2 rounded-xl border border-border bg-surface p-3">
          <label className="block text-sm">
            <span className="text-ink-soft">{tr("Client ID")}</span>
            <input value={clientId} onChange={(e) => setClientId(e.target.value)} placeholder="…apps.googleusercontent.com"
              className="mt-1 w-full rounded-lg border border-border bg-surface2 px-3 py-1.5 text-sm text-ink outline-none focus:border-accent" />
          </label>
          <label className="block text-sm">
            <span className="text-ink-soft">{tr("Client Secret")}</span>
            <input type="password" value={secret} onChange={(e) => setSecret(e.target.value)}
              placeholder={own ? tr("•••••••• (em branco mantém)") : "GOCSPX-…"}
              className="mt-1 w-full rounded-lg border border-border bg-surface2 px-3 py-1.5 text-sm text-ink outline-none focus:border-accent" />
          </label>
          <div className="text-sm">
            <span className="flex items-center gap-1.5 text-ink-soft">
              
              {tr("URI de redirecionamento")}
              <InfoDot text={tr("No Google Cloud: cliente OAuth do tipo Web com esta URI, APIs do Gmail e do Calendar ativas e app publicado (em teste o acesso expira em 7 dias).")} />
            </span>
            <div className="mt-1 flex items-center gap-2 rounded-lg border border-border bg-surface2 px-3 py-1.5">
              <span className="min-w-0 flex-1 truncate text-ink-soft">{st.redirect_uri}</span>
              <button type="button" aria-label={tr("Copiar URI")} onClick={async () => { if (await copyText(st.redirect_uri)) { setCopied(true); setTimeout(() => setCopied(false), 1200); } }}
                className="shrink-0 text-muted transition-colors hover:text-ink">
                {copied ? <Check size={14} className="text-green-500" /> : <Copy size={14} />}
              </button>
            </div>
          </div>
          <div className="flex items-center justify-end gap-2 pt-0.5">
            {own && st.builtin && (
              <button type="button" onClick={() => void useBuiltin()}
                className="rounded-full px-3 py-1.5 text-xs text-muted transition-colors hover:bg-hover hover:text-ink">
                
                {tr("Usar o do AI Workspace")}
              </button>
            )}
            <button type="button" onClick={() => void save()} disabled={saving || !clientId.trim() || (!own && !secret.trim())}
              className="rounded-full bg-accent px-4 py-1.5 text-xs font-medium text-white transition-colors hover:bg-accent-hover disabled:opacity-60">
              {saving ? tr("Salvando…") : tr("Salvar")}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
