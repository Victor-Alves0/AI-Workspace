"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import {
  ArrowLeft, ArrowLeftRight, Bug, Check, ChevronLeft, DatabaseBackup, Download, Gauge, HeartPulse, Loader2, Network,
  RefreshCw, Shield, Trash2, Upload, Users, X,
} from "lucide-react";
import { api, API_URL, ApiError } from "@/lib/api";
import type { AdminUser } from "@/lib/types";
import ObservabilityView from "@/components/ObservabilityView";
import HealthView from "@/components/HealthView";
import SyncAdmin from "@/components/SyncAdmin";
import { InfoDot, Toggle } from "@/components/ui";
import { checkDesktopUpdate, installDesktopUpdate, isDesktop } from "@/lib/desktop";
import { tr } from "@/lib/i18n";

/* ------------------------------- navegação por cards ------------------------ */
type AdminSection = "users" | "network" | "update" | "backup" | "sync" | "observability" | "health";

const ADMIN_CARDS: { key: AdminSection; name: string; desc: string; icon: ReactNode }[] = [
  { key: "users", name: tr("Usuários"), desc: tr("Aprovar, remover e cadastros"), icon: <Users size={22} /> },
  { key: "health", name: tr("Saúde"), desc: tr("Estado das capacidades do sistema"), icon: <HeartPulse size={22} /> },
  { key: "observability", name: tr("Observabilidade"), desc: tr("Inspecione cada chamada"), icon: <Gauge size={22} /> },
  { key: "network", name: tr("Rede"), desc: tr("IPs permitidos, host e porta"), icon: <Network size={22} /> },
  { key: "update", name: tr("Atualização"), desc: tr("Verificar novas versões"), icon: <RefreshCw size={22} /> },
  { key: "backup", name: tr("Backup e migração"), desc: tr("Exportar/importar o sistema"), icon: <DatabaseBackup size={22} /> },
  { key: "sync", name: tr("Sincronização"), desc: tr("Mesmos dados no servidor e no desktop"), icon: <ArrowLeftRight size={22} /> },
];

/* card quadrado da grade (mesmo visual do Espaço de Trabalho) */
function AdminCard({ icon, name, desc, badge, onClick }: {
  icon: ReactNode; name: string; desc: string; badge?: number; onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className="group relative flex h-[168px] flex-col items-center justify-center gap-3 rounded-2xl border border-border bg-surface p-5 text-center transition-all duration-150 hover:-translate-y-0.5 hover:border-accent/40 hover:bg-hover hover:shadow-sm"
    >
      <span className="flex h-11 w-11 items-center justify-center rounded-xl bg-surface2 text-accent-hover transition-transform duration-150 group-hover:scale-105">
        {icon}
      </span>
      <div className="flex flex-col items-center">
        <p className="text-sm font-semibold text-ink">{name}</p>
        <p className="mt-0.5 line-clamp-2 text-xs leading-4 text-muted">{desc}</p>
      </div>
      {!!badge && (
        <span className="absolute right-3 top-3 flex h-5 min-w-[20px] items-center justify-center rounded-full bg-amber-500/90 px-1.5 text-[11px] font-semibold text-white">
          {badge}
        </span>
      )}
    </button>
  );
}

/* casca de uma seção. O "← Painel do Admin" fica só no cabeçalho da página: aqui
   havia um segundo (breadcrumb), e a tela mostrava dois botões de voltar. */
function AdminShell({ children }: { title: string; onBack: () => void; children: ReactNode }) {
  return <div>{children}</div>;
}

interface NetworkCfg { host: string; port: number; allowed_ips: string[]; repo: string; branch: string; trust_proxy?: boolean; web_origin?: string }
interface UpdateInfo {
  current_version: string; latest_release: string | null; update_available: boolean; error: string | null;
  /** agente de atualização instalado no host (servidor Docker) */
  agent?: boolean;
  update_status?: { state: "running" | "done" | "failed"; at?: string; log?: string } | null;
}

const STATUS_STYLE: Record<string, string> = {
  active: "bg-green-500/15 text-green-400",
  pending: "bg-amber-500/15 text-amber-400",
  rejected: "bg-red-500/15 text-red-400",
};

export default function AdminPage() {
  const router = useRouter();
  const [denied, setDenied] = useState(false);
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [allowSignups, setAllowSignups] = useState(false);
  const [net, setNet] = useState<NetworkCfg | null>(null);
  const [ipsText, setIpsText] = useState("");
  const [netSaved, setNetSaved] = useState(false);
  const [section, setSection] = useState<AdminSection | null>(null);

  const loadUsers = () =>
    api
      .get<AdminUser[]>("/admin/users")
      .then(setUsers)
      .catch((e) => {
        if (e instanceof ApiError && e.status === 403) setDenied(true);
        if (e instanceof ApiError && e.status === 401) router.replace("/login");
      });

  const loadNet = () =>
    api.get<NetworkCfg>("/admin/network").then((n) => { setNet(n); setIpsText((n.allowed_ips ?? []).join("\n")); }).catch(() => {});

  useEffect(() => {
    loadUsers();
    api.get<{ allow_signups: boolean }>("/admin/config").then((c) => setAllowSignups(c.allow_signups)).catch(() => {});
    loadNet();
  }, []);

  async function toggleSignups() {
    const next = !allowSignups;
    setAllowSignups(next);
    await api.put("/admin/config", { allow_signups: next });
  }

  async function saveNet() {
    if (!net) return;
    const allowed_ips = ipsText.split(/[\n,]+/).map((s) => s.trim()).filter(Boolean);
    const body = { host: net.host, port: Number(net.port) || 8000, allowed_ips, repo: net.repo ?? "", branch: net.branch || "main" };
    const saved = await api.put<NetworkCfg>("/admin/network", body);
    setNet(saved); setIpsText((saved.allowed_ips ?? []).join("\n"));
    setNetSaved(true); setTimeout(() => setNetSaved(false), 1500);
  }

  const approve = async (id: string) => { await api.post(`/admin/users/${id}/approve`); loadUsers(); };
  const reject = async (id: string) => { await api.post(`/admin/users/${id}/reject`); loadUsers(); };
  const remove = async (id: string) => { await api.del(`/admin/users/${id}`); loadUsers(); };

  if (denied) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 text-muted">
        <p>{tr("Acesso negado — apenas admin.")}</p>
        <button onClick={() => router.push("/chat")} className="text-accent">{tr("← Voltar ao chat")}</button>
      </div>
    );
  }

  const pending = users.filter((u) => u.status === "pending");
  const curCard = ADMIN_CARDS.find((c) => c.key === section);

  return (
    <div className="h-full overflow-y-auto bg-bg px-4 py-5 md:p-6">
      <div className="mx-auto max-w-4xl space-y-5">
        {/* cabeçalho no padrão do Espaço de Trabalho: no início, "Voltar ao chat" à direita;
            numa seção, o botão quadrado ‹ ao lado do título volta ao painel */}
        <div>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex min-w-0 items-center gap-3">
              {section ? (
                <button
                  onClick={() => setSection(null)}
                  title={tr("Painel do Admin")}
                  aria-label={tr("Voltar ao Painel do Admin")}
                  className="flex h-8 w-8 flex-none items-center justify-center rounded-xl border border-transparent bg-surface text-ink-soft transition-colors hover:border-border hover:bg-surface2 hover:text-ink"
                >
                  <ChevronLeft size={18} />
                </button>
              ) : (
                <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-accent/15 text-accent-hover">
                  <Shield size={20} />
                </span>
              )}
              <div>
                <h1 className="text-xl font-bold tracking-tight text-ink">
                  {section ? curCard?.name ?? tr("Painel do Admin") : tr("Painel do Admin")}
                </h1>
                <p className="text-xs text-muted">
                  {section ? curCard?.desc : tr("Usuários, observabilidade, rede, atualização e backup")}
                </p>
              </div>
            </div>
            {!section && (
              <div className="flex items-center gap-2">
                <button onClick={() => router.push("/debug")} className="flex items-center gap-1.5 whitespace-nowrap rounded-full border border-border px-4 py-1.5 text-sm text-ink-soft transition-colors hover:bg-hover hover:text-ink">
                  <Bug size={15} />  {tr("Debug")}
                </button>
                <button onClick={() => router.push("/chat")} className="flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full border border-border px-4 py-1.5 text-sm text-ink-soft transition-colors hover:bg-hover hover:text-ink">
                  <ArrowLeft size={16} />  {tr("Voltar ao chat")}
                </button>
              </div>
            )}
          </div>
        </div>

        {/* HOME: grade de cards (igual ao Espaço de Trabalho) */}
        {!section && (
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
            {ADMIN_CARDS.map((c) => (
              <AdminCard
                key={c.key}
                icon={c.icon}
                name={c.name}
                desc={c.desc}
                badge={c.key === "users" ? pending.length : 0}
                onClick={() => setSection(c.key)}
              />
            ))}
          </div>
        )}

        {/* SEÇÃO: Observabilidade */}
        {section === "health" && (
          <AdminShell title={tr("Saúde")} onBack={() => setSection(null)}>
            <HealthView />
          </AdminShell>
        )}

        {section === "observability" && (
          <AdminShell title={tr("Observabilidade")} onBack={() => setSection(null)}>
            <ObservabilityView />
          </AdminShell>
        )}

        {/* SEÇÃO: Usuários (cadastros + pendências + tabela) */}
        {section === "users" && (
        <AdminShell title={tr("Usuários")} onBack={() => setSection(null)}>
        <div className="space-y-5">
        {/* config de cadastro */}
        <div className="flex items-center justify-between rounded-xl border border-border bg-surface p-4">
          <div>
            <p className="text-sm font-medium text-ink">{tr("Permitir novos cadastros")}</p>
            <p className="text-xs text-muted">
              
              {tr("Quando ligado, a opção \"Cadastrar\" aparece na tela de login. Novos usuários ficam pendentes até você aprovar.")}
            </p>
          </div>
          <button
            onClick={toggleSignups}
            className={`relative h-6 w-11 rounded-full transition-colors ${allowSignups ? "bg-accent" : "bg-surface2"}`}
          >
            <span className={`absolute top-0.5 h-5 w-5 rounded-full bg-white transition-all ${allowSignups ? "left-[22px]" : "left-0.5"}`} />
          </button>
        </div>

        {pending.length > 0 && (
          <p className="text-sm text-amber-400">{pending.length}  {tr("usuário(s) aguardando aprovação.")}</p>
        )}

        {/* tabela de usuários */}
        <div className="overflow-x-auto rounded-xl border border-border">
          <table className="w-full min-w-[480px] text-left text-sm">
            <thead className="bg-surface text-muted">
              <tr>
                <th className="px-4 py-3 font-medium">{tr("Email")}</th>
                <th className="px-4 py-3 font-medium">{tr("Papel")}</th>
                <th className="px-4 py-3 font-medium">{tr("Status")}</th>
                <th className="px-4 py-3 text-right font-medium">{tr("Ações")}</th>
              </tr>
            </thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.id} className="border-t border-border">
                  <td className="px-4 py-3 text-ink">{u.email}</td>
                  <td className="px-4 py-3 text-muted">{u.role}</td>
                  <td className="px-4 py-3">
                    <span className={`rounded-full px-2 py-0.5 text-xs ${STATUS_STYLE[u.status] ?? "text-muted"}`}>
                      {u.status}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex justify-end gap-1">
                      {u.status !== "active" && (
                        <button onClick={() => approve(u.id)} title={tr("Aprovar")} className="rounded-md p-1.5 text-green-400 hover:bg-surface2">
                          <Check size={16} />
                        </button>
                      )}
                      {u.status !== "rejected" && u.role !== "admin" && (
                        <button onClick={() => reject(u.id)} title={tr("Rejeitar")} className="rounded-md p-1.5 text-amber-400 hover:bg-surface2">
                          <X size={16} />
                        </button>
                      )}
                      {u.role !== "admin" && (
                        <button onClick={() => remove(u.id)} title={tr("Excluir")} className="rounded-md p-1.5 text-red-400 hover:bg-surface2">
                          <Trash2 size={16} />
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        </div>
        </AdminShell>
        )}

        {/* SEÇÃO: Rede */}
        {section === "network" && (
        <AdminShell title={tr("Rede")} onBack={() => setSection(null)}>
        {net ? (
          <div className="space-y-3 rounded-xl border border-border bg-surface p-4">
            <p className="flex items-center gap-2 text-sm font-semibold text-ink"><Network size={16} />  {tr("Rede")}</p>

            <div>
              <p className="text-sm text-ink-soft">{tr("IPs permitidos")}</p>
              <p className="mb-1.5 text-xs text-muted">{tr("Um IP ou faixa CIDR por linha (ex.: 203.0.113.4 ou 10.0.0.0/24).")} <span className="text-ink-soft">{tr("Vazio = libera todos.")}</span>  {tr("Aplicado imediatamente.")}</p>
              <textarea
                rows={3}
                value={ipsText}
                onChange={(e) => setIpsText(e.target.value)}
                placeholder={tr("(vazio = todos os IPs)")}
                className="w-full resize-y rounded-lg border border-border bg-surface2 px-3 py-2 font-mono text-xs text-ink outline-none focus:border-accent placeholder:text-muted"
              />
              {net.trust_proxy === false && (
                <p className="mt-1 text-xs text-amber-400/80">{tr("Atrás de um proxy reverso? Ligue TRUST_PROXY=1 no .env para o IP real ser lido do X-Forwarded-For.")}</p>
              )}
            </div>

            <div className="grid grid-cols-2 gap-3">
              <label className="text-sm">
                <span className="text-ink-soft">{tr("Host (bind IP)")}</span>
                <input value={net.host} onChange={(e) => setNet({ ...net, host: e.target.value })} placeholder="0.0.0.0"
                  className="mt-1 w-full rounded-lg border border-border bg-surface2 px-3 py-1.5 font-mono text-sm text-ink outline-none focus:border-accent" />
              </label>
              <label className="text-sm">
                <span className="text-ink-soft">{tr("Porta")}</span>
                <input type="number" value={net.port} onChange={(e) => setNet({ ...net, port: Number(e.target.value) })} placeholder="8000"
                  className="mt-1 w-full rounded-lg border border-border bg-surface2 px-3 py-1.5 font-mono text-sm text-ink outline-none focus:border-accent" />
              </label>
            </div>
            <p className="rounded-lg bg-surface2 px-3 py-2 text-xs leading-5 text-muted">
              
              {tr("Host/porta são aplicados no próximo deploy. Ponha no seu")} <span className="font-mono text-ink-soft">.env</span>:
              <span className="mt-1 block font-mono text-ink-soft">SERVER_BIND={net.host}    {tr("·  SERVER_PORT=")}{net.port}</span>
              {tr("e rode")} <span className="font-mono text-ink-soft">./update.sh</span> ({tr("ou")} <span className="font-mono">{tr("docker compose up -d")}</span>).
            </p>

            <div className="flex items-center gap-3">
              <button onClick={saveNet} className="rounded-lg bg-accent px-4 py-1.5 text-sm font-medium text-white hover:bg-accent-hover">{tr("Salvar rede")}</button>
              {netSaved && <span className="text-xs text-green-400">{tr("Salvo ✓")}</span>}
            </div>
          </div>
        ) : <p className="text-sm text-muted">{tr("Carregando…")}</p>}
        </AdminShell>
        )}

        {/* SEÇÃO: Atualização — compara com o repositório do projeto e atualiza */}
        {section === "update" && (
          <AdminShell title={tr("Atualização")} onBack={() => setSection(null)}>
            <UpdateSection />
          </AdminShell>
        )}

        {/* SEÇÃO: Sincronização entre instâncias */}
        {section === "sync" && (
          <AdminShell title={tr("Sincronização")} onBack={() => setSection(null)}>
            <SyncAdmin />
          </AdminShell>
        )}

        {/* SEÇÃO: Backup e migração */}
        {section === "backup" && (
          <AdminShell title={tr("Backup e migração")} onBack={() => setSection(null)}>
            <BackupCard />
          </AdminShell>
        )}
      </div>
    </div>
  );
}

/* ------------------------ Backup / migração de sistema ------------------------ */
/* Exporta/importa o sistema INTEIRO (usuários, chats, modelos, memórias,
 * segredos cifrados, imagens…) — tudo vive no Postgres, então um dump = backup
 * completo. Para migrar de VPS: exporte aqui, suba o app na máquina nova com o
 * MESMO APP_SECRET no .env e importe o arquivo. */
/* Atualização: compara a versão instalada com o repositório do projeto e, se houver
   versão nova, oferece atualizar. Desktop → atualizador do app (Tauri). Servidor →
   pedido ao agente do host (scripts/update-agent.sh), que roda o update.sh. */
function UpdateSection() {
  const [info, setInfo] = useState<UpdateInfo | null>(null);
  const [desktop, setDesktop] = useState<{ version: string } | null>(null);
  const [checking, setChecking] = useState(false);
  const [updating, setUpdating] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [showLog, setShowLog] = useState(false);
  const onDesktop = isDesktop();

  async function check() {
    setChecking(true);
    setMsg(null);
    try {
      const [i, d] = await Promise.all([
        api.get<UpdateInfo>("/admin/update-check"),
        onDesktop ? checkDesktopUpdate() : Promise.resolve(null),
      ]);
      setInfo(i);
      setDesktop(d);
    } catch (e) {
      setMsg({ ok: false, text: e instanceof Error ? e.message : tr("Falha ao verificar.") });
    } finally {
      setChecking(false);
    }
  }
  useEffect(() => { void check(); }, []);

  async function updateNow() {
    setUpdating(true);
    setMsg(null);
    if (onDesktop) {
      // baixa, instala e reinicia o app; só volta aqui se der erro
      const err = await installDesktopUpdate();
      if (err) setMsg({ ok: false, text: err });
      setUpdating(false);
      return;
    }
    try {
      await api.post("/admin/update");
    } catch (e) {
      setMsg({ ok: false, text: e instanceof Error ? e.message : tr("Não foi possível pedir a atualização.") });
      setUpdating(false);
      return;
    }
    // o host reconstrói e reinicia os containers: a API some por um tempo — segue
    // perguntando até o agente dizer como terminou (ou desistir depois de 20 min)
    const fim = Date.now() + 20 * 60_000;
    let visto = false;
    while (Date.now() < fim) {
      await new Promise((r) => setTimeout(r, 4000));
      try {
        const i = await api.get<UpdateInfo>("/admin/update-check");
        const st = i.update_status?.state;
        if (st === "running") visto = true;
        if (visto && (st === "done" || st === "failed")) {
          setInfo(i);
          setMsg(st === "done"
            ? { ok: true, text: tr("Atualizado para a versão {current_version}.", { current_version: i.current_version }) }
            : { ok: false, text: tr("A atualização falhou no servidor.") });
          break;
        }
      } catch {
        visto = true; // servidor reiniciando no meio do update.sh
      }
    }
    setUpdating(false);
  }

  const disponivel = onDesktop ? !!desktop : !!info?.update_available;
  const nova = onDesktop ? desktop?.version : info?.latest_release;
  const podeAtualizar = onDesktop || !!info?.agent;
  const log = info?.update_status?.log;

  return (
    <div className="space-y-3 rounded-xl border border-border bg-surface p-4">
      {!info && !msg ? (
        <p className="flex items-center gap-2 text-sm text-muted"><Loader2 size={15} className="animate-spin" />  {tr("Verificando…")}</p>
      ) : (
        <>
          {info && (
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <p className="text-sm text-ink">
                  
                  {tr("Versão instalada:")} <span className="font-mono">{info.current_version}</span>
                </p>
                {info.error ? (
                  <p className="mt-0.5 text-xs text-red-400">{info.error}</p>
                ) : disponivel ? (
                  <p className="mt-0.5 text-xs text-amber-300">
                    
                    {tr("Nova versão disponível")}{nova ? <>: <span className="font-mono">{nova}</span></> : ""}
                  </p>
                ) : (
                  <p className="mt-0.5 flex items-center gap-1 text-xs text-green-400"><Check size={13} />  {tr("Você está na versão mais recente")}</p>
                )}
              </div>
              <div className="flex items-center gap-2">
                <button onClick={check} disabled={checking || updating}
                  className="flex items-center gap-1.5 rounded-full border border-border px-4 py-1.5 text-sm text-ink-soft transition-colors hover:bg-hover disabled:opacity-50">
                  <RefreshCw size={14} className={checking ? "animate-spin" : ""} />  {tr("Verificar")}
                </button>
                {disponivel && podeAtualizar && (
                  <button onClick={updateNow} disabled={updating}
                    className="flex items-center gap-1.5 rounded-full bg-accent px-4 py-1.5 text-sm font-medium text-white transition-colors hover:bg-accent-hover disabled:opacity-60">
                    {updating ? <Loader2 size={14} className="animate-spin" /> : <Download size={14} />}
                    {updating ? tr("Atualizando…") : tr("Atualizar agora")}
                  </button>
                )}
              </div>
            </div>
          )}
          {disponivel && !podeAtualizar && (
            <p className="text-xs text-muted">
              
              {tr("Para atualizar por aqui, suba o atualizador no servidor:")}{" "}
              <span className="font-mono text-ink-soft">{tr("docker compose up -d updater")}</span>
            </p>
          )}
          {msg && <p className={`text-sm ${msg.ok ? "text-green-400" : "text-red-400"}`}>{msg.text}</p>}
          {log && msg && !msg.ok && (
            <div>
              <button onClick={() => setShowLog((v) => !v)} className="text-xs text-muted hover:text-ink">
                {showLog ? tr("Ocultar log") : tr("Ver log")}
              </button>
              {showLog && (
                <pre className="mt-1.5 max-h-60 overflow-auto rounded-lg bg-surface2 p-3 font-mono text-[11px] text-ink-soft">{log}</pre>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}

/** Cabeçalho do arquivo diz se o backup tem senha: AIWBK3 = cifrado por senha
 *  (vale em qualquer instalação); AIWBK1/2 = pela chave desta instalação. */
async function backupHasPassword(f: File): Promise<boolean> {
  const head = new TextDecoder().decode(await f.slice(0, 7).arrayBuffer());
  return head === "AIWBK3\n";
}

type BackupPanel = "export" | "import" | "reset" | null;

const inputCls =
  "w-full rounded-lg border border-border bg-surface2 px-3 py-1.5 text-sm text-ink outline-none transition-colors placeholder:text-muted focus:border-accent";
const pillCls =
  "flex items-center gap-1.5 rounded-full border px-4 py-1.5 text-sm transition-colors disabled:opacity-50";

function BackupCard() {
  const fileRef = useRef<HTMLInputElement>(null);
  const [panel, setPanel] = useState<BackupPanel>(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null);
  // exportar
  const [withPass, setWithPass] = useState(false);
  const [exportPass, setExportPass] = useState("");
  const [exportPass2, setExportPass2] = useState("");
  // importar
  const [file, setFile] = useState<File | null>(null);
  const [needsPass, setNeedsPass] = useState(false);
  const [importPass, setImportPass] = useState("");
  const [legacySecret, setLegacySecret] = useState("");
  const [showLegacy, setShowLegacy] = useState(false);
  // resetar
  const [resetPass, setResetPass] = useState("");
  const [resetWord, setResetWord] = useState("");

  function open(p: BackupPanel) {
    setPanel((cur) => (cur === p ? null : p));
    setResult(null);
  }

  function clearImport() {
    setFile(null);
    setNeedsPass(false);
    setImportPass("");
    setLegacySecret("");
    setShowLegacy(false);
    if (fileRef.current) fileRef.current.value = "";
  }

  async function pickFile(f: File) {
    setResult(null);
    setFile(f);
    setNeedsPass(await backupHasPassword(f).catch(() => false));
    setPanel("import");
  }

  const exportPassOk = !withPass || (exportPass.length >= 8 && exportPass === exportPass2);

  async function exportBackup() {
    setBusy(true);
    setResult(null);
    try {
      const r = await fetch(`${API_URL}/admin/backup`, {
        method: "POST", credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password: withPass ? exportPass : "" }),
      });
      if (!r.ok) throw new Error((await r.json().catch(() => null))?.detail ?? `Falha (${r.status})`);
      const blob = await r.blob();
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = r.headers.get("Content-Disposition")?.match(/filename="(.+?)"/)?.[1] ?? "aiworkspace.backup";
      a.click();
      URL.revokeObjectURL(a.href);
      setPanel(null);
      setWithPass(false);
      setExportPass("");
      setExportPass2("");
      setResult({ ok: true, text: withPass ? tr("Backup exportado com senha.") : tr("Backup exportado.") });
    } catch (e) {
      setResult({ ok: false, text: e instanceof Error ? e.message : tr("Falha ao exportar") });
    } finally {
      setBusy(false);
    }
  }

  async function importBackup() {
    if (!file) return;
    setBusy(true);
    setResult(null);
    try {
      const form = new FormData();
      form.append("file", file);
      form.append("password", needsPass ? importPass : "");
      form.append("source_secret", showLegacy ? legacySecret : "");
      const r = await fetch(`${API_URL}/admin/restore`, { method: "POST", credentials: "include", body: form });
      const data = await r.json().catch(() => null);
      if (!r.ok) {
        const msg: string = data?.detail ?? `Falha (${r.status})`;
        // backup sem senha de OUTRA instalação: só abre com a chave de lá
        if (!needsPass && msg.includes("APP_SECRET")) setShowLegacy(true);
        throw new Error(msg);
      }
      clearImport();
      setPanel(null);
      setResult({ ok: true, text: data?.note ?? tr("Backup importado.") });
    } catch (e) {
      setResult({ ok: false, text: e instanceof Error ? e.message : tr("Falha ao importar") });
    } finally {
      setBusy(false);
    }
  }

  async function resetAll() {
    setBusy(true);
    setResult(null);
    try {
      await api.post("/admin/reset", { password: resetPass });
      // banco vazio: a sessão não existe mais → volta ao primeiro uso
      window.location.href = "/setup";
    } catch (e) {
      setResult({ ok: false, text: e instanceof Error ? e.message : tr("Falha ao resetar") });
      setBusy(false);
    }
  }

  const btn = (p: BackupPanel, active: string) =>
    `${pillCls} ${panel === p ? active : "border-border text-ink-soft hover:bg-hover hover:text-ink"}`;

  return (
    <div className="space-y-3 rounded-xl border border-border bg-surface p-4">
      <div className="flex flex-wrap items-center gap-2.5">
        <button onClick={() => fileRef.current?.click()} disabled={busy} className={btn("import", "border-accent/60 bg-accent/10 text-ink")}>
          <Upload size={14} />  {tr("Importar")}
        </button>
        <input ref={fileRef} type="file" accept=".backup,.dump" className="hidden"
          onChange={(e) => { const f = e.target.files?.[0]; if (f) void pickFile(f); }} />
        <button onClick={() => open("export")} disabled={busy} className={btn("export", "border-accent/60 bg-accent/10 text-ink")}>
          <Download size={14} />  {tr("Exportar")}
        </button>
        <button onClick={() => open("reset")} disabled={busy} className={`${btn("reset", "border-red-500/60 bg-red-500/10 text-red-300")} ml-auto`}>
          <Trash2 size={14} />  {tr("Resetar")}
        </button>
      </div>

      {panel === "export" && (
        <div className="space-y-2.5 rounded-lg border border-border bg-surface2/40 p-3">
          <label className="flex items-center justify-between gap-3 text-sm text-ink">
            <span className="flex items-center gap-1.5">
              
              {tr("Proteger com senha")}
              <InfoDot text={tr("Com senha, o backup pode ser importado em outra instalação (outro servidor ou o app desktop). Sem senha, só nesta.")} />
            </span>
            <Toggle on={withPass} onChange={setWithPass} />
          </label>
          {withPass && (
            <div className="grid gap-2 sm:grid-cols-2">
              <input type="password" value={exportPass} onChange={(e) => setExportPass(e.target.value)}
                placeholder={tr("Senha (mín. 8 caracteres)")} autoComplete="new-password" className={inputCls} />
              <input type="password" value={exportPass2} onChange={(e) => setExportPass2(e.target.value)}
                placeholder={tr("Repita a senha")} autoComplete="new-password" className={inputCls} />
              {exportPass2 && exportPass !== exportPass2 && (
                <p className="text-xs text-red-400 sm:col-span-2">{tr("As senhas não conferem.")}</p>
              )}
            </div>
          )}
          <div className="flex justify-end">
            <button onClick={exportBackup} disabled={busy || !exportPassOk}
              className="flex items-center gap-1.5 rounded-full bg-accent px-4 py-1.5 text-sm font-medium text-white transition-colors hover:bg-accent-hover disabled:opacity-60">
              {busy ? <Loader2 size={14} className="animate-spin" /> : <Download size={14} />}  {tr("Baixar backup")}
            </button>
          </div>
        </div>
      )}

      {panel === "import" && file && (
        <div className="space-y-2.5 rounded-lg border border-red-500/40 bg-red-500/10 p-3 text-sm">
          <p className="text-ink">
            
            {tr("Importar")} <span className="font-mono text-xs">{file.name}</span>?{" "}
            <span className="text-red-300">{tr("Substitui todos os dados atuais")}</span>  {tr("(usuários, chats, tudo).")}
          </p>
          {needsPass && (
            <input type="password" value={importPass} onChange={(e) => setImportPass(e.target.value)}
              placeholder={tr("Senha do backup")} autoComplete="off" autoFocus className={inputCls} />
          )}
          {showLegacy && (
            <input type="password" value={legacySecret} onChange={(e) => setLegacySecret(e.target.value)}
              placeholder={tr("APP_SECRET da instalação de origem")} autoComplete="off"
              className={`${inputCls} font-mono placeholder:font-sans`} />
          )}
          <div className="flex items-center justify-end gap-2">
            <button onClick={() => { clearImport(); setPanel(null); }} className="rounded-full border border-border px-4 py-1.5 text-xs text-muted hover:text-ink">
              
              {tr("Cancelar")}
            </button>
            <button onClick={importBackup} disabled={busy || (needsPass && !importPass) || (showLegacy && !legacySecret)}
              className="flex items-center gap-1.5 rounded-full bg-red-500/90 px-4 py-1.5 text-xs font-medium text-white hover:bg-red-500 disabled:opacity-60">
              {busy && <Loader2 size={13} className="animate-spin" />}  {tr("Importar e substituir")}
            </button>
          </div>
        </div>
      )}

      {panel === "reset" && (
        <div className="space-y-2.5 rounded-lg border border-red-500/40 bg-red-500/10 p-3 text-sm">
          <p className="text-ink">
            <span className="font-medium text-red-300">{tr("Apaga tudo")}</span>  {tr("— usuários, chats, modelos, memórias, conexões, anexos e projetos — e volta a instalação ao primeiro uso. Não tem volta.")}
          </p>
          <div className="grid gap-2 sm:grid-cols-2">
            <input type="password" value={resetPass} onChange={(e) => setResetPass(e.target.value)}
              placeholder={tr("Sua senha")} autoComplete="current-password" autoFocus className={inputCls} />
            <input value={resetWord} onChange={(e) => setResetWord(e.target.value)}
              placeholder={tr("Digite \"APAGAR\" para confirmar")} autoComplete="off" className={inputCls} />
          </div>
          <div className="flex items-center justify-end gap-2">
            <button onClick={() => { setPanel(null); setResetPass(""); setResetWord(""); }} className="rounded-full border border-border px-4 py-1.5 text-xs text-muted hover:text-ink">
              
              {tr("Cancelar")}
            </button>
            <button onClick={resetAll} disabled={busy || !resetPass || resetWord.trim().toUpperCase() !== "APAGAR"}
              className="flex items-center gap-1.5 rounded-full bg-red-500/90 px-4 py-1.5 text-xs font-medium text-white hover:bg-red-500 disabled:opacity-60">
              {busy ? <Loader2 size={13} className="animate-spin" /> : <Trash2 size={13} />}  {tr("Apagar tudo")}
            </button>
          </div>
        </div>
      )}

      {result && (
        <p className={`rounded-lg px-3 py-2 text-xs ${result.ok ? "bg-green-500/10 text-green-400" : "bg-red-500/10 text-red-400"}`}>
          {result.text}
        </p>
      )}
    </div>
  );
}
