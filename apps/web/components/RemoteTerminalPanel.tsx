"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  ChevronLeft, Loader2, Plus, Play, RefreshCw, ShieldCheck, ShieldAlert, Terminal,
  Trash2, TriangleAlert, Wifi,
} from "lucide-react";
import { api } from "@/lib/api";
import { useConfirm } from "./ConfirmDialog";
import { Toggle } from "./ui";

/** Máquina remota como o servidor a devolve. Segredos NUNCA vêm: só `has_*` e as URLs
 *  de proxy mascaradas — por isso os campos secretos do formulário começam vazios e
 *  vazio significa "mantém o que está salvo". */
interface Host {
  id: string;
  name: string;
  slug: string;
  base_url: string;
  enabled: boolean;
  tls_mode: string;
  has_cert: boolean;
  has_token: boolean;
  proxy: string;
  has_proxy: boolean;
  require_proxy: boolean;
  egress: { mode: string; dns: string; killswitch: boolean; allow_lan: boolean; allow_hosts: string[] };
  egress_proxy: string;
  has_egress_proxy: boolean;
  workdir: string;
  shell: string;
  timeout_seconds: number;
  confirm_required: boolean;
  status: string;
  last_error: string;
  last_seen_at: string | null;
  agent_version: string;
  info: Record<string, any>;
}

const EMPTY: Partial<Host> & { token?: string; tls_cert_pem?: string; proxy_url?: string; egress_proxy_url?: string } = {
  name: "",
  base_url: "",
  tls_mode: "pinned",
  require_proxy: false,
  egress: { mode: "off", dns: "proxy", killswitch: true, allow_lan: false, allow_hosts: [] },
  workdir: "",
  shell: "",
  timeout_seconds: 120,
  confirm_required: true,
  enabled: true,
};

const STATUS_PT: Record<string, { label: string; tone: string }> = {
  online: { label: "Online", tone: "text-green-400" },
  offline: { label: "Fora do ar", tone: "text-red-400" },
  unauthorized: { label: "Token recusado", tone: "text-red-400" },
  blocked: { label: "Bloqueado pela política", tone: "text-amber-400" },
  unknown: { label: "Nunca testado", tone: "text-muted" },
};

const EGRESS_PT: Record<string, string> = {
  off: "Sem restrição",
  env: "Só variáveis (sem killswitch)",
  force: "Selada por proxy + killswitch",
};

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-medium text-muted">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-[11px] leading-4 text-muted">{hint}</span>}
    </label>
  );
}

const inputCls =
  "w-full rounded-lg border border-border bg-bg px-3 py-2 text-sm text-ink outline-none focus:border-accent";

function Row({ label, sub, children }: { label: string; sub?: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-4 py-2.5">
      <div className="min-w-0">
        <p className="text-sm text-ink">{label}</p>
        {sub && <p className="text-xs leading-4 text-muted">{sub}</p>}
      </div>
      {children}
    </div>
  );
}

/** Detalhe "Remote Terminal" (card em Integrações): conecta máquinas suas (VPS,
 *  servidor de casa) onde a IA tem terminal, e configura as DUAS camadas de rede —
 *  por onde o workspace fala com a máquina, e por onde os comandos da máquina saem. */
export default function RemoteTerminalPanel({ onBack }: { onBack: () => void }) {
  const confirmDialog = useConfirm();
  const [hosts, setHosts] = useState<Host[]>([]);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState<string | "new" | null>(null);
  const [msg, setMsg] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await api.get<{ hosts: Host[] }>("/remote/hosts");
      setHosts(r.hosts || []);
    } catch {
      setHosts([]);
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => { load(); }, [load]);

  async function remove(h: Host) {
    const ok = await confirmDialog({
      title: `Remover ${h.name}?`,
      body: "A conexão sai do AI Workspace. O agente continua instalado na máquina — "
        + "para removê-lo de vez, rode lá: sudo ./install.sh --uninstall",
      confirmLabel: "Remover",
      danger: true,
    });
    if (!ok) return;
    await api.del(`/remote/hosts/${h.id}`);
    setMsg(`${h.name} removida.`);
    await load();
  }

  if (editing) {
    return (
      <HostForm
        host={editing === "new" ? null : hosts.find((h) => h.id === editing) || null}
        onBack={() => setEditing(null)}
        onSaved={async () => { setEditing(null); await load(); }}
      />
    );
  }

  return (
    <div className="pt-1">
      <button onClick={onBack} className="mb-3 flex items-center gap-1 text-sm text-muted transition-colors hover:text-ink">
        <ChevronLeft size={16} /> Voltar
      </button>

      <div className="flex items-center gap-2.5">
        <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-surface2 text-accent-hover">
          <Terminal size={17} />
        </span>
        <div className="min-w-0">
          <p className="text-sm font-semibold text-ink">Remote Terminal</p>
          <p className="text-xs text-muted">Terminal nas suas máquinas (VPS, servidor de casa)</p>
        </div>
        <button
          onClick={() => setEditing("new")}
          className="ml-auto flex items-center gap-1.5 rounded-lg bg-accent px-3 py-1.5 text-sm font-medium text-white transition-colors hover:bg-accent-hover"
        >
          <Plus size={14} /> Adicionar
        </button>
      </div>

      <div className="mt-4 rounded-xl border border-border bg-surface p-4 text-xs leading-5 text-muted">
        <p className="mb-2 font-medium text-ink">Como instalar o agente</p>
        <ol className="ml-4 list-decimal space-y-1">
          <li>Copie a pasta <code className="text-ink-soft">apps/remote-agent/</code> para a máquina.</li>
          <li>
            Rode lá: <code className="text-ink-soft">sudo ./install.sh --san SEU_IP</code>
            <br />Para selar a saída pelo proxy:{" "}
            <code className="text-ink-soft">sudo ./install.sh --san SEU_IP --proxy socks5h://127.0.0.1:9050 --force-egress</code>
          </li>
          <li>Cole aqui o endereço, o token e o certificado que o instalador imprimir.</li>
        </ol>
      </div>

      {loading ? (
        <p className="mt-4 flex items-center gap-2 text-sm text-muted"><Loader2 size={14} className="animate-spin" /> Carregando…</p>
      ) : hosts.length === 0 ? (
        <p className="mt-4 rounded-xl border border-dashed border-border px-4 py-8 text-center text-sm text-muted">
          Nenhuma máquina conectada ainda.
        </p>
      ) : (
        <div className="mt-4 space-y-2">
          {hosts.map((h) => {
            const st = STATUS_PT[h.status] || STATUS_PT.unknown;
            const sealed = h.egress.mode === "force";
            return (
              <div key={h.id} className="rounded-xl border border-border bg-surface p-3.5">
                <div className="flex items-center gap-2.5">
                  <span className={`flex h-8 w-8 items-center justify-center rounded-lg bg-surface2 ${sealed ? "text-green-400" : "text-muted"}`}>
                    {sealed ? <ShieldCheck size={16} /> : <ShieldAlert size={16} />}
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium text-ink">
                      {h.name} <span className="font-normal text-muted">({h.slug})</span>
                    </p>
                    <p className="truncate text-xs text-muted">{h.base_url}</p>
                  </div>
                  <span className={`shrink-0 text-xs ${st.tone}`}>{st.label}</span>
                </div>
                <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-muted">
                  <span>Saída dos comandos: <span className="text-ink-soft">{EGRESS_PT[h.egress.mode]}</span></span>
                  {h.has_proxy && <span>Workspace → máquina via <span className="text-ink-soft">{h.proxy}</span>{h.require_proxy && " (obrigatório)"}</span>}
                  {h.agent_version && <span>agente {h.agent_version}</span>}
                </div>
                {h.last_error && <p className="mt-2 text-[11px] leading-4 text-amber-400">{h.last_error}</p>}
                <div className="mt-3 flex flex-wrap gap-2">
                  <button onClick={() => setEditing(h.id)} className="rounded-lg border border-border px-3 py-1.5 text-xs text-ink-soft transition-colors hover:bg-hover">
                    Configurar
                  </button>
                  <TestButton host={h} onDone={load} />
                  <button onClick={() => remove(h)} className="flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs text-muted transition-colors hover:border-red-400/40 hover:text-red-400">
                    <Trash2 size={13} /> Remover
                  </button>
                </div>
                <RemoteConsole host={h} />
              </div>
            );
          })}
        </div>
      )}
      {msg && <p className="mt-3 text-xs text-muted">{msg}</p>}
    </div>
  );
}

function TestButton({ host, onDone }: { host: Host; onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  const [out, setOut] = useState("");
  async function go() {
    setBusy(true); setOut("");
    try {
      const r = await api.post<{ ok: boolean; status: string; error?: string; info?: any }>(`/remote/hosts/${host.id}/test`);
      setOut(r.ok ? `Online — ${r.info?.hostname || ""} ${r.info?.os || ""}`.trim() : r.error || "Falhou");
    } catch (e) {
      setOut(e instanceof Error ? e.message : "Falhou");
    } finally {
      setBusy(false);
      onDone();
    }
  }
  return (
    <>
      <button onClick={go} disabled={busy} className="flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs text-ink-soft transition-colors hover:bg-hover disabled:opacity-60">
        {busy ? <Loader2 size={13} className="animate-spin" /> : <Wifi size={13} />} Testar
      </button>
      {out && <span className="self-center text-[11px] text-muted">{out}</span>}
    </>
  );
}

/** Terminal do painel: o usuário roda um comando na máquina sem passar pela IA.
 *  Existe porque a primeira dúvida depois de conectar é sempre "isso funciona mesmo?",
 *  e responder isso pedindo para um modelo tentar é o caminho mais longo e mais caro. */
function RemoteConsole({ host }: { host: Host }) {
  const [open, setOpen] = useState(false);
  const [cmd, setCmd] = useState("");
  const [busy, setBusy] = useState(false);
  const [lines, setLines] = useState<{ cmd: string; out: string; code: number | null }[]>([]);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => { endRef.current?.scrollIntoView({ block: "nearest" }); }, [lines]);

  async function run() {
    const c = cmd.trim();
    if (!c || busy) return;
    setBusy(true); setCmd("");
    try {
      const r = await api.post<{ output?: string; exit_code?: number | null; error?: string; timed_out?: boolean }>(
        `/remote/hosts/${host.id}/exec`, { command: c },
      );
      const out = r.error ? r.error : (r.output || "") + (r.timed_out ? "\n[tempo esgotado]" : "");
      setLines((l) => [...l.slice(-30), { cmd: c, out, code: r.error ? null : r.exit_code ?? null }]);
    } catch (e) {
      setLines((l) => [...l, { cmd: c, out: e instanceof Error ? e.message : "falhou", code: null }]);
    } finally {
      setBusy(false);
    }
  }

  if (!open) {
    return (
      <button onClick={() => setOpen(true)} className="mt-2 flex items-center gap-1.5 text-xs text-muted transition-colors hover:text-ink">
        <Terminal size={13} /> Abrir terminal
      </button>
    );
  }
  return (
    <div className="mt-3 overflow-hidden rounded-lg border border-border bg-bg">
      <div className="max-h-64 overflow-y-auto px-3 py-2 font-mono text-[11px] leading-5">
        {lines.length === 0 && <p className="text-muted">Rode um comando (ex.: <span className="text-ink-soft">uname -a</span>).</p>}
        {lines.map((l, i) => (
          <div key={i} className="mb-2">
            <p className="text-accent-hover">$ {l.cmd}</p>
            <pre className="whitespace-pre-wrap break-words text-ink-soft">{l.out || "(sem saída)"}</pre>
            {l.code !== null && l.code !== 0 && <p className="text-red-400">exit {l.code}</p>}
          </div>
        ))}
        <div ref={endRef} />
      </div>
      <div className="flex items-center gap-2 border-t border-border px-2 py-2">
        <span className="pl-1 font-mono text-xs text-muted">$</span>
        <input
          value={cmd}
          onChange={(e) => setCmd(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") run(); }}
          placeholder="comando"
          spellCheck={false}
          className="min-w-0 flex-1 bg-transparent font-mono text-xs text-ink outline-none"
        />
        <button onClick={run} disabled={busy || !cmd.trim()} className="flex items-center gap-1 rounded-md bg-surface2 px-2 py-1 text-xs text-ink-soft transition-colors hover:bg-hover disabled:opacity-50">
          {busy ? <Loader2 size={12} className="animate-spin" /> : <Play size={12} />}
        </button>
        <button onClick={() => setOpen(false)} className="rounded-md px-2 py-1 text-xs text-muted hover:text-ink">Fechar</button>
      </div>
    </div>
  );
}

/* ------------------------------- formulário ------------------------------- */
function HostForm({ host, onBack, onSaved }: { host: Host | null; onBack: () => void; onSaved: () => void }) {
  const isNew = host === null;
  const [f, setF] = useState<any>(host ? { ...host } : { ...EMPTY });
  const [token, setToken] = useState("");
  const [cert, setCert] = useState("");
  const [proxy, setProxy] = useState("");
  const [egressProxy, setEgressProxy] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [leak, setLeak] = useState<any>(null);

  const set = (k: string, v: any) => setF((s: any) => ({ ...s, [k]: v }));
  const eg = f.egress || EMPTY.egress!;
  const setEg = (k: string, v: any) => set("egress", { ...eg, [k]: v });

  async function save() {
    setBusy(true); setMsg("");
    const body: any = {
      name: f.name, base_url: f.base_url, tls_mode: f.tls_mode,
      require_proxy: f.require_proxy, egress: eg, workdir: f.workdir, shell: f.shell,
      timeout_seconds: Number(f.timeout_seconds) || 120,
      confirm_required: f.confirm_required, enabled: f.enabled,
    };
    if (token.trim()) body.token = token.trim();
    if (cert.trim()) body.tls_cert_pem = cert.trim();
    if (proxy.trim()) body.proxy_url = proxy.trim();
    if (egressProxy.trim()) body.egress_proxy = egressProxy.trim();
    try {
      if (isNew) await api.post("/remote/hosts", body);
      else await api.put(`/remote/hosts/${host!.id}`, body);
      onSaved();
    } catch (e) {
      setMsg(e instanceof Error ? e.message : "Falha ao salvar.");
    } finally {
      setBusy(false);
    }
  }

  async function pushEgress() {
    if (isNew) { setMsg("Salve a máquina primeiro."); return; }
    setBusy(true); setMsg("");
    try {
      const r = await api.post<{ ok: boolean; status?: string; warning?: string; error?: string }>(
        `/remote/hosts/${host!.id}/egress`);
      setMsg(r.error || r.warning || `Política aplicada na máquina (${r.status}).`);
    } catch (e) {
      setMsg(e instanceof Error ? e.message : "Falha ao aplicar.");
    } finally { setBusy(false); }
  }

  async function runLeakTest() {
    if (isNew) { setMsg("Salve a máquina primeiro."); return; }
    setBusy(true); setLeak(null); setMsg("");
    try {
      setLeak(await api.post(`/remote/hosts/${host!.id}/egress/test`));
    } catch (e) {
      setMsg(e instanceof Error ? e.message : "Falha no teste.");
    } finally { setBusy(false); }
  }

  return (
    <div className="pt-1">
      <button onClick={onBack} className="mb-3 flex items-center gap-1 text-sm text-muted transition-colors hover:text-ink">
        <ChevronLeft size={16} /> Voltar
      </button>
      <p className="text-sm font-semibold text-ink">{isNew ? "Nova máquina" : f.name}</p>

      {/* ---------------------------- conexão ---------------------------- */}
      <p className="mb-2 mt-4 border-b border-border pb-1.5 text-xs font-semibold text-ink">Conexão</p>
      <div className="space-y-3 rounded-xl border border-border bg-surface p-4">
        <Field label="Nome"><input value={f.name || ""} onChange={(e) => set("name", e.target.value)} placeholder="VPS Oracle" className={inputCls} /></Field>
        <Field label="Endereço do agente" hint="O que o instalador imprimiu, ex.: https://203.0.113.10:8791">
          <input value={f.base_url || ""} onChange={(e) => set("base_url", e.target.value)} placeholder="https://203.0.113.10:8791" spellCheck={false} className={`${inputCls} font-mono text-xs`} />
        </Field>
        <Field label="Token" hint={host?.has_token ? "Já configurado — cole um novo só para trocar." : "O token que o instalador imprimiu."}>
          <input type="password" value={token} onChange={(e) => setToken(e.target.value)} placeholder={host?.has_token ? "••••••••" : "cole o token"} className={`${inputCls} font-mono text-xs`} />
        </Field>
        <Field label="Verificação de TLS" hint="'Certificado do agente' valida contra o certificado colado abaixo — é a opção certa para o certificado autoassinado que o instalador gera.">
          <select value={f.tls_mode} onChange={(e) => set("tls_mode", e.target.value)} className={inputCls}>
            <option value="pinned">Certificado do agente (recomendado)</option>
            <option value="system">Cadeia pública (proxy reverso com certificado próprio)</option>
            <option value="off">Sem verificação (só dentro de um túnel confiável)</option>
          </select>
        </Field>
        {f.tls_mode === "pinned" && (
          <Field label="Certificado do agente" hint={host?.has_cert ? "Já configurado — cole outro só para trocar." : "O bloco -----BEGIN CERTIFICATE----- impresso pelo instalador."}>
            <textarea value={cert} onChange={(e) => setCert(e.target.value)} rows={4} spellCheck={false}
              placeholder={host?.has_cert ? "(certificado salvo)" : "-----BEGIN CERTIFICATE-----"}
              className={`${inputCls} resize-y font-mono text-[11px]`} />
          </Field>
        )}
      </div>

      {/* ------------------- camada 1: saída do workspace ------------------- */}
      <p className="mb-2 mt-5 border-b border-border pb-1.5 text-xs font-semibold text-ink">Como o AI Workspace fala com esta máquina</p>
      <div className="space-y-3 rounded-xl border border-border bg-surface p-4">
        <p className="text-xs leading-5 text-muted">
          Todo tráfego do servidor até esta máquina sai por aqui. Com proxy, o IP e as consultas de DNS do
          servidor não aparecem no caminho — o nome é resolvido pelo proxy, não aqui.
        </p>
        <Field label="Proxy da conexão" hint={`socks5h://user:senha@host:1080 ou http://host:8080. ${host?.has_proxy ? `Atual: ${host.proxy}. Deixe vazio para manter; digite "-" para remover.` : "Vazio = conexão direta."}`}>
          <input value={proxy} onChange={(e) => setProxy(e.target.value)} placeholder={host?.has_proxy ? host.proxy : "socks5h://127.0.0.1:9050"} spellCheck={false} className={`${inputCls} font-mono text-xs`} />
        </Field>
        <div className="border-t border-border pt-1">
          <Row label="Exigir o proxy (killswitch)" sub="Sem proxy utilizável, a conexão NÃO é feita. Nunca há tentativa direta — é ela que exporia o IP do servidor.">
            <Toggle on={!!f.require_proxy} onChange={(v) => set("require_proxy", v)} />
          </Row>
        </div>
      </div>

      {/* ------------------- camada 2: saída dos comandos ------------------- */}
      <p className="mb-2 mt-5 border-b border-border pb-1.5 text-xs font-semibold text-ink">Como os comandos saem DESTA máquina</p>
      <div className="space-y-3 rounded-xl border border-border bg-surface p-4">
        <p className="text-xs leading-5 text-muted">
          Vale para tudo que os comandos fizerem na máquina. No modo selado, uma regra de firewall por
          usuário descarta qualquer saída fora do proxy: se o proxy cair, o comando falha em vez de sair
          pelo IP real.
        </p>
        <Field label="Modo">
          <select value={eg.mode} onChange={(e) => setEg("mode", e.target.value)} className={inputCls}>
            <option value="off">Sem restrição</option>
            <option value="env">Só variáveis de ambiente (sem killswitch)</option>
            <option value="force">Selada por proxy + killswitch (recomendado)</option>
          </select>
        </Field>
        {eg.mode === "env" && (
          <p className="flex items-start gap-1.5 rounded-lg bg-amber-400/10 px-3 py-2 text-[11px] leading-4 text-amber-400">
            <TriangleAlert size={13} className="mt-0.5 shrink-0" />
            Um programa que ignore as variáveis (ou resolva DNS por conta própria) sai pelo IP real sem aviso.
            Use este modo só onde o firewall não puder ser instalado.
          </p>
        )}
        {eg.mode !== "off" && (
          <>
            <Field label="Proxy de saída da máquina" hint={host?.has_egress_proxy ? `Atual: ${host.egress_proxy}. Vazio mantém; "-" remove.` : "Endereço visto de DENTRO da máquina (ex.: socks5h://127.0.0.1:9050 para um Tor/túnel local)."}>
              <input value={egressProxy} onChange={(e) => setEgressProxy(e.target.value)} placeholder={host?.has_egress_proxy ? host.egress_proxy : "socks5h://127.0.0.1:9050"} spellCheck={false} className={`${inputCls} font-mono text-xs`} />
            </Field>
            <div className="divide-y divide-border border-t border-border">
              <Row label="DNS pelo proxy" sub="Nenhuma consulta de nome sai da máquina. Desligue só se algo interno precisar do resolvedor local.">
                <Toggle on={eg.dns !== "system"} onChange={(v) => setEg("dns", v ? "proxy" : "system")} />
              </Row>
              <Row label="Killswitch" sub="Sem proxy no ar, o agente RECUSA rodar comandos em vez de deixá-los sair por fora.">
                <Toggle on={eg.killswitch !== false} onChange={(v) => setEg("killswitch", v)} />
              </Row>
              <Row label="Permitir rede local" sub="Libera 10/8, 172.16/12 e 192.168/16 fora do proxy (bancos e serviços internos).">
                <Toggle on={!!eg.allow_lan} onChange={(v) => setEg("allow_lan", v)} />
              </Row>
            </div>
            <Field label="IPs liberados fora do proxy" hint="Separados por vírgula. Use com parcimônia: cada um é uma via que não passa pelo proxy.">
              <input value={(eg.allow_hosts || []).join(", ")} spellCheck={false}
                onChange={(e) => setEg("allow_hosts", e.target.value.split(",").map((x) => x.trim()).filter(Boolean))}
                placeholder="203.0.113.9" className={`${inputCls} font-mono text-xs`} />
            </Field>
          </>
        )}
        {!isNew && (
          <div className="flex flex-wrap gap-2 border-t border-border pt-3">
            <button onClick={pushEgress} disabled={busy} className="flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs text-ink-soft transition-colors hover:bg-hover disabled:opacity-60">
              <RefreshCw size={13} /> Aplicar na máquina
            </button>
            <button onClick={runLeakTest} disabled={busy} className="flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs text-ink-soft transition-colors hover:bg-hover disabled:opacity-60">
              <ShieldCheck size={13} /> Testar vazamento
            </button>
          </div>
        )}
        {leak && <LeakResult data={leak} />}
      </div>

      {/* ---------------------------- execução ---------------------------- */}
      <p className="mb-2 mt-5 border-b border-border pb-1.5 text-xs font-semibold text-ink">Execução</p>
      <div className="space-y-3 rounded-xl border border-border bg-surface p-4">
        <Field label="Diretório inicial" hint="Vazio = a home do usuário que roda os comandos."><input value={f.workdir || ""} onChange={(e) => set("workdir", e.target.value)} placeholder="/srv/app" spellCheck={false} className={`${inputCls} font-mono text-xs`} /></Field>
        <Field label="Shell" hint="Vazio = o configurado no agente (/bin/bash)."><input value={f.shell || ""} onChange={(e) => set("shell", e.target.value)} placeholder="/bin/bash" spellCheck={false} className={`${inputCls} font-mono text-xs`} /></Field>
        <Field label="Tempo máximo por comando (s)"><input type="number" value={f.timeout_seconds ?? 120} onChange={(e) => set("timeout_seconds", e.target.value)} className={inputCls} /></Field>
        <div className="divide-y divide-border border-t border-border">
          <Row label="Pedir confirmação a cada comando" sub="Recomendado: é uma máquina de verdade, não um sandbox descartável.">
            <Toggle on={f.confirm_required !== false} onChange={(v) => set("confirm_required", v)} />
          </Row>
          <Row label="Ativa" sub="Desligada, some das ferramentas sem perder a configuração.">
            <Toggle on={f.enabled !== false} onChange={(v) => set("enabled", v)} />
          </Row>
        </div>
      </div>

      <div className="mt-4 flex items-center gap-2">
        <button onClick={save} disabled={busy || !f.name || !f.base_url || (isNew && !token.trim())}
          className="flex items-center gap-1.5 rounded-lg bg-accent px-4 py-1.5 text-sm font-medium text-white transition-colors hover:bg-accent-hover disabled:opacity-50">
          {busy ? <Loader2 size={14} className="animate-spin" /> : null} Salvar
        </button>
        <button onClick={onBack} className="rounded-lg border border-border px-3 py-1.5 text-sm text-muted transition-colors hover:bg-hover">Cancelar</button>
      </div>
      {msg && <p className="mt-2 text-xs text-muted">{msg}</p>}
    </div>
  );
}

/** Resultado do teste de vazamento. O sinal de saúde é contraintuitivo de propósito:
 *  o que se quer ver é a conexão DIRETA falhando. */
function LeakResult({ data }: { data: any }) {
  const checks = data.checks || {};
  const problems: string[] = data.problems || (data.error ? [data.error] : []);
  const ok = data.ok && problems.length === 0;
  return (
    <div className={`rounded-lg border px-3 py-2.5 text-xs leading-5 ${ok ? "border-green-400/30 bg-green-400/5" : "border-amber-400/30 bg-amber-400/5"}`}>
      <p className={`flex items-center gap-1.5 font-medium ${ok ? "text-green-400" : "text-amber-400"}`}>
        {ok ? <ShieldCheck size={13} /> : <TriangleAlert size={13} />}
        {data.verdict || (ok ? "Sem vazamento" : problems.join("; ") || "Não foi possível concluir")}
      </p>
      <ul className="mt-1.5 space-y-0.5 text-muted">
        {checks.proxy_ip ? <li>IP visto pelo destino através do proxy: <span className="font-mono text-ink-soft">{checks.proxy_ip}</span></li> : null}
        {checks.proxy_error ? <li className="text-amber-400">Proxy: {checks.proxy_error}</li> : null}
        <li>Conexão direta: {checks.direct_blocked ? <span className="text-green-400">bloqueada ✓</span> : <span className="text-red-400">passou ✗</span>}</li>
        <li>DNS local: {checks.dns_blocked ? <span className="text-green-400">bloqueado ✓</span> : <span className="text-ink-soft">respondendo</span>}</li>
      </ul>
    </div>
  );
}
