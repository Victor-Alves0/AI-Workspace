"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Check, ChevronLeft, Copy, Github, Loader2, Plus, TriangleAlert, Trash2, Wifi } from "lucide-react";
import { api, API_URL, ApiError } from "@/lib/api";
import { copyText } from "@/lib/clipboard";
import { openExternal } from "@/lib/desktop";
import { useConfirm } from "./ConfirmDialog";

interface Account {
  id: string;
  login: string;
  auth_type: "pat" | "oauth";
  avatar_url: string;
  connected_at: string;
}
interface GithubStatus {
  oauth_configured: boolean;
  /** login por código (device flow) disponível — depende só de um client_id público */
  device_available: boolean;
  is_admin: boolean;
  client_id: string;
  redirect_uri: string;
  accounts: Account[];
}

/** Tela de detalhe "GitHub" (aberta pelo card em Integrações): conecta contas e
 *  gerencia as que os modelos usam pela ferramenta GitHub.
 *
 *  Três caminhos, nesta ordem de preferência: LOGIN POR CÓDIGO (device flow — só
 *  clicar, quando a instalação traz o client_id embutido), Personal Access Token
 *  (recolhido) e OAuth App próprio (admin). O token deixou de ser o caminho
 *  principal: colar credencial é o que estamos tirando da frente do usuário. */
export default function GitHubPanel({ onBack }: { onBack: () => void }) {
  const confirm = useConfirm();
  const [st, setSt] = useState<GithubStatus | null>(null);
  const [test, setTest] = useState<Record<string, "loading" | "ok" | "fail">>({});

  const load = useCallback(async () => {
    try {
      setSt(await api.get<GithubStatus>("/integrations/github"));
    } catch {
      setSt({ oauth_configured: false, device_available: false, is_admin: false, client_id: "", redirect_uri: "", accounts: [] });
    }
  }, []);
  useEffect(() => { load(); }, [load]);

  async function testAccount(id: string) {
    setTest((t) => ({ ...t, [id]: "loading" }));
    try {
      const r = await api.post<{ ok: boolean }>(`/integrations/github/accounts/${id}/test`);
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
          <Github size={17} />
        </span>
        <div>
          <p className="text-sm font-semibold text-ink">GitHub</p>
          <p className="text-xs text-muted">Repos, arquivos, issues e PRs para os modelos usarem</p>
        </div>
      </div>

      {st == null ? (
        <div className="flex justify-center py-10"><Loader2 size={18} className="animate-spin text-muted" /></div>
      ) : (
        <>
          {st.device_available && <DeviceConnect reload={load} />}

          <PatConnect reload={load} startOpen={!st.device_available} />

          {st.is_admin && <OAuthAppConfig st={st} reload={load} />}

          <p className="mb-1 mt-6 text-xs font-semibold text-ink">Contas conectadas</p>
          <div className="rounded-xl border border-border bg-surface">
            {st.accounts.length === 0 ? (
              <p className="px-3 py-3 text-xs text-muted">Nenhuma conta conectada ainda.</p>
            ) : (
              st.accounts.map((a, i) => (
                <div key={a.id} className={`flex items-center gap-3 px-3 py-2.5 ${i > 0 ? "border-t border-border" : ""}`}>
                  {a.avatar_url ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={a.avatar_url} alt="" className="h-8 w-8 shrink-0 rounded-full" />
                  ) : (
                    <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-surface2 text-[11px] font-medium text-ink">
                      {a.login[0]?.toUpperCase() || "G"}
                    </span>
                  )}
                  <span className="min-w-0 flex-1 truncate text-sm text-ink">
                    {a.login || "(conta GitHub)"}
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
                      if (!(await confirm({ title: "Remover esta conta?", body: <>Os modelos deixarão de acessar <span className="font-medium text-ink">{a.login}</span>.</>, confirmLabel: "Remover", danger: true }))) return;
                      try { await api.del(`/integrations/github/accounts/${a.id}`); await load(); }
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
            {st.oauth_configured && (
              <div className="border-t border-border p-2">
                <button
                  onClick={() => { window.location.href = `${API_URL}/integrations/github/connect`; }}
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

interface DeviceStart {
  handle: string;
  user_code: string;
  verification_uri: string;
  interval: number;
  expires_in: number;
}
type PollOut =
  | { status: "pending" }
  | { status: "slow_down"; interval: number }
  | { status: "ok"; login: string }
  | { status: "error"; error: string };

/** Login por código (device flow): o caminho de BOTÃO, sem token nenhum.
 *
 *  O usuário clica, recebe um código curto, digita no github.com/login/device e a
 *  conta aparece conectada. Escolhido em vez do OAuth com redirect porque o device
 *  flow não usa client secret nem callback registrado — ou seja, funciona em
 *  qualquer instalação (desktop, Docker, VPS) sem ninguém cadastrar nada.
 *
 *  A repetição fica AQUI, e não num laço no servidor: o backend faz uma tentativa
 *  por chamada, então nenhum worker fica preso os 15 minutos de validade do código. */
function DeviceConnect({ reload }: { reload: () => Promise<void> }) {
  const [flow, setFlow] = useState<DeviceStart | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [copied, setCopied] = useState(false);
  // o timer é cancelado no unmount: fechar o painel no meio do fluxo não pode
  // deixar uma sondagem rodando contra um componente que já saiu da tela.
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);

  async function start() {
    setBusy(true); setErr("");
    try {
      const r = await api.post<DeviceStart>("/integrations/github/device/start");
      setFlow(r);
      copyText(r.user_code).then((ok) => setCopied(ok));
      schedule(r.handle, r.interval);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Falha ao iniciar o login");
    } finally { setBusy(false); }
  }

  function schedule(handle: string, seconds: number) {
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => { poll(handle, seconds); }, seconds * 1000);
  }

  async function poll(handle: string, seconds: number) {
    let r: PollOut;
    try {
      r = await api.post<PollOut>("/integrations/github/device/poll", { handle });
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Falha ao concluir o login");
      setFlow(null);
      return;
    }
    if (r.status === "ok") {
      setFlow(null);
      await reload();
      return;
    }
    if (r.status === "error") {
      setErr(r.error === "expired" ? "O código expirou. Comece de novo." : r.error);
      setFlow(null);
      return;
    }
    // pending → mesma cadência; slow_down → a que o GitHub mandou
    schedule(handle, r.status === "slow_down" ? r.interval : seconds);
  }

  function cancel() {
    if (timer.current) clearTimeout(timer.current);
    setFlow(null);
  }

  return (
    <div className="mt-4">
      <p className="mb-1 text-xs font-semibold text-ink">Entrar com GitHub</p>
      <div className="space-y-2 rounded-xl border border-border bg-surface p-3">
        {flow == null ? (
          <>
            <button onClick={start} disabled={busy}
              className="flex items-center gap-2 rounded-full bg-accent px-4 py-1.5 text-xs font-medium text-white transition-colors hover:bg-accent-hover disabled:opacity-60">
              {busy ? <Loader2 size={13} className="animate-spin" /> : <Github size={13} />}
              {busy ? "Abrindo…" : "Entrar com GitHub"}
            </button>
            <p className="text-[11px] text-muted">Autorize com um código curto — sem criar nem colar token.</p>
          </>
        ) : (
          <>
            <p className="text-[11px] text-muted">
              1. Abra{" "}
              {/* openExternal e não <a target="_blank">: no webview do desktop um link
                  de nova janela simplesmente não faz nada (docs/desktop-updates.md). */}
              <button onClick={() => openExternal(flow.verification_uri)} className="text-accent-hover underline">
                {flow.verification_uri}
              </button>
              {" "}2. digite o código abaixo. Esta tela conclui sozinha.
            </p>
            <div className="flex items-center gap-2">
              <code className="flex-1 rounded-lg border border-border bg-surface2 px-3 py-2 text-center font-mono text-lg tracking-[0.3em] text-ink">
                {flow.user_code}
              </code>
              <button
                onClick={async () => setCopied(await copyText(flow.user_code))}
                className="shrink-0 rounded-lg p-2 text-muted transition-colors hover:bg-hover hover:text-ink"
                title="Copiar código"
              >
                {copied ? <Check size={15} className="text-green-500" /> : <Copy size={15} />}
              </button>
            </div>
            <div className="flex items-center gap-2 text-[11px] text-muted">
              <Loader2 size={12} className="animate-spin" /> Esperando você autorizar…
              <button onClick={cancel} className="ml-auto underline transition-colors hover:text-ink">Cancelar</button>
            </div>
          </>
        )}
        {err && <p className="flex items-start gap-1.5 text-[11px] text-red-400"><TriangleAlert size={12} className="mt-0.5 shrink-0" /> {err}</p>}
      </div>
    </div>
  );
}

/** Conectar colando um Personal Access Token (fine-grained recomendado). */
function PatConnect({ reload, startOpen }: { reload: () => Promise<void>; startOpen: boolean }) {
  const [open, setOpen] = useState(startOpen);
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  async function connect() {
    if (!token.trim()) return;
    setBusy(true); setErr("");
    try {
      await api.post("/integrations/github/pat", { token: token.trim() });
      setToken("");
      await reload();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Falha ao conectar");
    } finally {
      setBusy(false);
    }
  }

  if (!open) {
    return (
      <button onClick={() => setOpen(true)} className="mt-3 text-[11px] text-muted underline transition-colors hover:text-ink">
        Prefiro colar um Personal Access Token
      </button>
    );
  }

  return (
    <div className="mt-4">
      <p className="mb-1 text-xs font-semibold text-ink">Conectar com token</p>
      <div className="space-y-2 rounded-xl border border-border bg-surface p-3">
        <input
          type="password" value={token} onChange={(e) => setToken(e.target.value)}
          placeholder="ghp_… ou github_pat_…"
          className="w-full rounded-lg border border-border bg-surface2 px-3 py-1.5 text-sm text-ink outline-none focus:border-accent"
        />
        <div className="flex items-center justify-between gap-2">
          {err ? <p className="min-w-0 flex-1 truncate text-[11px] text-red-400">{err}</p>
               : <p className="min-w-0 flex-1 text-[11px] text-muted">Crie um Personal Access Token (fine-grained) com acesso aos repos e permissões desejadas.</p>}
          <button onClick={connect} disabled={busy || !token.trim()}
            className="shrink-0 rounded-full bg-accent px-4 py-1.5 text-xs font-medium text-white transition-colors hover:bg-accent-hover disabled:opacity-60">
            {busy ? "…" : "Conectar"}
          </button>
        </div>
      </div>
    </div>
  );
}

/** Config do OAuth App (só admin): Client ID/Secret globais — alternativa ao PAT. */
function OAuthAppConfig({ st, reload }: { st: GithubStatus; reload: () => Promise<void> }) {
  const [open, setOpen] = useState(false);
  const [clientId, setClientId] = useState(st.client_id);
  const [secret, setSecret] = useState("");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  async function save() {
    setSaving(true);
    try {
      await api.put("/integrations/github/oauth", { client_id: clientId.trim(), client_secret: secret.trim() || null });
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
              <input value={clientId} onChange={(e) => setClientId(e.target.value)} placeholder="Iv1.…"
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
                Callback: <span className="text-ink-soft">{st.redirect_uri}</span>
              </p>
              <button onClick={save} disabled={saving || !clientId.trim()}
                className="shrink-0 rounded-full bg-accent px-4 py-1.5 text-xs font-medium text-white transition-colors hover:bg-accent-hover disabled:opacity-60">
                {saving ? "…" : saved ? "Salvo ✓" : "Salvar"}
              </button>
            </div>
          </div>
          <p className="mt-1 text-[11px] leading-4 text-muted">
            Crie um OAuth App no GitHub, registre a Callback acima. O PAT acima é mais simples e não precisa disto.
          </p>
        </>
      )}
    </div>
  );
}
