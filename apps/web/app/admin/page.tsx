"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import {
  ArrowLeft, Bug, Check, DatabaseBackup, Download, Gauge, HeartPulse, Loader2, Network,
  RefreshCw, Shield, Trash2, Upload, Users, X,
} from "lucide-react";
import { api, API_URL, ApiError } from "@/lib/api";
import type { AdminUser } from "@/lib/types";
import ObservabilityView from "@/components/ObservabilityView";
import HealthView from "@/components/HealthView";

/* ------------------------------- navegação por cards ------------------------ */
type AdminSection = "users" | "network" | "update" | "backup" | "observability" | "health";

const ADMIN_CARDS: { key: AdminSection; name: string; desc: string; icon: ReactNode }[] = [
  { key: "users", name: "Usuários", desc: "Aprovar, remover e cadastros", icon: <Users size={22} /> },
  { key: "health", name: "Saúde", desc: "Estado das capacidades do sistema", icon: <HeartPulse size={22} /> },
  { key: "observability", name: "Observabilidade", desc: "Inspecione cada chamada", icon: <Gauge size={22} /> },
  { key: "network", name: "Rede", desc: "IPs permitidos, host e porta", icon: <Network size={22} /> },
  { key: "update", name: "Atualização", desc: "Verificar novas versões", icon: <RefreshCw size={22} /> },
  { key: "backup", name: "Backup e migração", desc: "Exportar/importar o sistema", icon: <DatabaseBackup size={22} /> },
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

/* casca de uma seção: breadcrumb de volta + título */
function AdminShell({ title, onBack, children }: { title: string; onBack: () => void; children: ReactNode }) {
  return (
    <div>
      <nav className="mb-4 flex items-center gap-1.5 text-sm">
        <button onClick={onBack} className="flex items-center gap-1.5 rounded-lg px-2 py-1 text-muted transition-colors hover:bg-hover hover:text-ink">
          <ArrowLeft size={16} /> Painel do Admin
        </button>
        <span className="text-muted">/</span>
        <span className="font-medium text-ink">{title}</span>
      </nav>
      {children}
    </div>
  );
}

interface NetworkCfg { host: string; port: number; allowed_ips: string[]; repo: string; branch: string; trust_proxy?: boolean; web_origin?: string }
interface UpdateInfo { current_version: string; repo: string; branch: string; latest_release: string | null; latest_commit: string | null; update_available: boolean; error: string | null }

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
  const [upd, setUpd] = useState<UpdateInfo | null>(null);
  const [checking, setChecking] = useState(false);
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

  async function checkUpdate() {
    setChecking(true);
    try {
      setUpd(await api.get<UpdateInfo>("/admin/update-check"));
    } finally {
      setChecking(false);
    }
  }
  const approve = async (id: string) => { await api.post(`/admin/users/${id}/approve`); loadUsers(); };
  const reject = async (id: string) => { await api.post(`/admin/users/${id}/reject`); loadUsers(); };
  const remove = async (id: string) => { await api.del(`/admin/users/${id}`); loadUsers(); };

  if (denied) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 text-muted">
        <p>Acesso negado — apenas admin.</p>
        <button onClick={() => router.push("/chat")} className="text-accent">← Voltar ao chat</button>
      </div>
    );
  }

  const pending = users.filter((u) => u.status === "pending");
  const curCard = ADMIN_CARDS.find((c) => c.key === section);

  return (
    <div className="h-full overflow-y-auto bg-bg px-4 py-5 md:p-6">
      <div className="mx-auto max-w-4xl space-y-5">
        {/* cabeçalho: no home volta ao chat; numa seção, o breadcrumb volta ao painel */}
        <div>
          {section ? (
            <button onClick={() => setSection(null)} className="mb-3 flex items-center gap-1.5 rounded-lg px-2 py-1 text-sm text-muted transition-colors hover:bg-hover hover:text-ink">
              <ArrowLeft size={16} /> Painel do Admin
            </button>
          ) : (
            <button onClick={() => router.push("/chat")} className="mb-3 flex items-center gap-1.5 rounded-lg px-2 py-1 text-sm text-muted transition-colors hover:bg-hover hover:text-ink">
              <ArrowLeft size={16} /> Voltar ao chat
            </button>
          )}
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-3">
              <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-accent/15 text-accent-hover">
                <Shield size={20} />
              </span>
              <div>
                <h1 className="text-xl font-bold tracking-tight text-ink">
                  {section ? curCard?.name ?? "Painel do Admin" : "Painel do Admin"}
                </h1>
                <p className="text-xs text-muted">
                  {section ? curCard?.desc : "Usuários, observabilidade, rede, atualização e backup"}
                </p>
              </div>
            </div>
            {!section && (
              <button onClick={() => router.push("/debug")} className="flex items-center gap-1.5 whitespace-nowrap rounded-full border border-border px-4 py-1.5 text-sm text-ink-soft transition-colors hover:bg-hover hover:text-ink">
                <Bug size={15} /> Debug
              </button>
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
          <AdminShell title="Saúde" onBack={() => setSection(null)}>
            <HealthView />
          </AdminShell>
        )}

        {section === "observability" && (
          <AdminShell title="Observabilidade" onBack={() => setSection(null)}>
            <ObservabilityView />
          </AdminShell>
        )}

        {/* SEÇÃO: Usuários (cadastros + pendências + tabela) */}
        {section === "users" && (
        <AdminShell title="Usuários" onBack={() => setSection(null)}>
        <div className="space-y-5">
        {/* config de cadastro */}
        <div className="flex items-center justify-between rounded-xl border border-border bg-surface p-4">
          <div>
            <p className="text-sm font-medium text-ink">Permitir novos cadastros</p>
            <p className="text-xs text-muted">
              Quando ligado, a opção &quot;Cadastrar&quot; aparece na tela de login. Novos usuários ficam pendentes até você aprovar.
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
          <p className="text-sm text-amber-400">{pending.length} usuário(s) aguardando aprovação.</p>
        )}

        {/* tabela de usuários */}
        <div className="overflow-x-auto rounded-xl border border-border">
          <table className="w-full min-w-[480px] text-left text-sm">
            <thead className="bg-surface text-muted">
              <tr>
                <th className="px-4 py-3 font-medium">Email</th>
                <th className="px-4 py-3 font-medium">Papel</th>
                <th className="px-4 py-3 font-medium">Status</th>
                <th className="px-4 py-3 text-right font-medium">Ações</th>
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
                        <button onClick={() => approve(u.id)} title="Aprovar" className="rounded-md p-1.5 text-green-400 hover:bg-surface2">
                          <Check size={16} />
                        </button>
                      )}
                      {u.status !== "rejected" && u.role !== "admin" && (
                        <button onClick={() => reject(u.id)} title="Rejeitar" className="rounded-md p-1.5 text-amber-400 hover:bg-surface2">
                          <X size={16} />
                        </button>
                      )}
                      {u.role !== "admin" && (
                        <button onClick={() => remove(u.id)} title="Excluir" className="rounded-md p-1.5 text-red-400 hover:bg-surface2">
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
        <AdminShell title="Rede" onBack={() => setSection(null)}>
        {net ? (
          <div className="space-y-3 rounded-xl border border-border bg-surface p-4">
            <p className="flex items-center gap-2 text-sm font-semibold text-ink"><Network size={16} /> Rede</p>

            <div>
              <p className="text-sm text-ink-soft">IPs permitidos</p>
              <p className="mb-1.5 text-xs text-muted">Um IP ou faixa CIDR por linha (ex.: 203.0.113.4 ou 10.0.0.0/24). <span className="text-ink-soft">Vazio = libera todos.</span> Aplicado imediatamente.</p>
              <textarea
                rows={3}
                value={ipsText}
                onChange={(e) => setIpsText(e.target.value)}
                placeholder="(vazio = todos os IPs)"
                className="w-full resize-y rounded-lg border border-border bg-surface2 px-3 py-2 font-mono text-xs text-ink outline-none focus:border-accent placeholder:text-muted"
              />
              {net.trust_proxy === false && (
                <p className="mt-1 text-xs text-amber-400/80">Atrás de um proxy reverso? Ligue TRUST_PROXY=1 no .env para o IP real ser lido do X-Forwarded-For.</p>
              )}
            </div>

            <div className="grid grid-cols-2 gap-3">
              <label className="text-sm">
                <span className="text-ink-soft">Host (bind IP)</span>
                <input value={net.host} onChange={(e) => setNet({ ...net, host: e.target.value })} placeholder="0.0.0.0"
                  className="mt-1 w-full rounded-lg border border-border bg-surface2 px-3 py-1.5 font-mono text-sm text-ink outline-none focus:border-accent" />
              </label>
              <label className="text-sm">
                <span className="text-ink-soft">Porta</span>
                <input type="number" value={net.port} onChange={(e) => setNet({ ...net, port: Number(e.target.value) })} placeholder="8000"
                  className="mt-1 w-full rounded-lg border border-border bg-surface2 px-3 py-1.5 font-mono text-sm text-ink outline-none focus:border-accent" />
              </label>
            </div>
            <p className="rounded-lg bg-surface2 px-3 py-2 text-xs leading-5 text-muted">
              Host/porta são aplicados no próximo deploy. Ponha no seu <span className="font-mono text-ink-soft">.env</span>:
              <span className="mt-1 block font-mono text-ink-soft">SERVER_BIND={net.host}  ·  SERVER_PORT={net.port}</span>
              e rode <span className="font-mono text-ink-soft">./update.sh</span> (ou <span className="font-mono">docker compose up -d</span>).
            </p>

            <div className="flex items-center gap-3">
              <button onClick={saveNet} className="rounded-lg bg-accent px-4 py-1.5 text-sm font-medium text-white hover:bg-accent-hover">Salvar rede</button>
              {netSaved && <span className="text-xs text-green-400">Salvo ✓</span>}
            </div>
          </div>
        ) : <p className="text-sm text-muted">Carregando…</p>}
        </AdminShell>
        )}

        {/* SEÇÃO: Atualização (checa o GitHub; aplica pelo update.sh no host) */}
        {section === "update" && (
        <AdminShell title="Atualização" onBack={() => setSection(null)}>
        {net ? (
          <div className="space-y-3 rounded-xl border border-border bg-surface p-4">
            <p className="flex items-center gap-2 text-sm font-semibold text-ink"><RefreshCw size={16} /> Atualização</p>
            <div className="grid grid-cols-2 gap-3">
              <label className="text-sm">
                <span className="text-ink-soft">Repositório (owner/repo)</span>
                <input value={net.repo ?? ""} onChange={(e) => setNet({ ...net, repo: e.target.value })} placeholder="usuario/ai-workspace"
                  className="mt-1 w-full rounded-lg border border-border bg-surface2 px-3 py-1.5 font-mono text-sm text-ink outline-none focus:border-accent" />
              </label>
              <label className="text-sm">
                <span className="text-ink-soft">Branch</span>
                <input value={net.branch || "main"} onChange={(e) => setNet({ ...net, branch: e.target.value })} placeholder="main"
                  className="mt-1 w-full rounded-lg border border-border bg-surface2 px-3 py-1.5 font-mono text-sm text-ink outline-none focus:border-accent" />
              </label>
            </div>
            <div className="flex flex-wrap items-center gap-3">
              <button onClick={saveNet} className="rounded-lg border border-border px-3 py-1.5 text-sm text-ink-soft hover:bg-surface2">Salvar repo</button>
              <button onClick={checkUpdate} disabled={checking} className="flex items-center gap-1.5 rounded-lg bg-accent px-4 py-1.5 text-sm font-medium text-white hover:bg-accent-hover disabled:opacity-60">
                <RefreshCw size={14} className={checking ? "animate-spin" : ""} /> Verificar atualizações
              </button>
            </div>
            {upd && (
              <div className={`rounded-lg border px-3 py-2.5 text-sm ${upd.update_available ? "border-amber-500/40 bg-amber-500/10" : "border-border bg-surface2"}`}>
                {upd.error ? (
                  <p className="text-red-400">{upd.error}</p>
                ) : (
                  <>
                    <p className="text-ink">
                      Versão atual: <span className="font-mono">{upd.current_version}</span>
                      {upd.latest_release && <> · Último release: <span className="font-mono">{upd.latest_release}</span></>}
                      {upd.latest_commit && <> · Commit: <span className="font-mono">{upd.latest_commit}</span></>}
                    </p>
                    {upd.update_available ? (
                      <p className="mt-1 text-amber-300">Há uma atualização disponível. No host, rode <span className="font-mono">./update.sh</span> para aplicar.</p>
                    ) : (
                      <p className="mt-1 text-green-400">Você está atualizado.</p>
                    )}
                  </>
                )}
              </div>
            )}
            <p className="text-xs leading-5 text-muted">
              Por segurança, a atualização roda no <span className="text-ink-soft">host</span> (o container não tem acesso ao Docker):
              execute <span className="font-mono text-ink-soft">./update.sh</span> na pasta do projeto — ele puxa do git, reconstrói, sobe e migra.
            </p>
          </div>
        ) : <p className="text-sm text-muted">Carregando…</p>}
        </AdminShell>
        )}

        {/* SEÇÃO: Backup e migração */}
        {section === "backup" && (
          <AdminShell title="Backup e migração" onBack={() => setSection(null)}>
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
function BackupCard() {
  const fileRef = useRef<HTMLInputElement>(null);
  const [exporting, setExporting] = useState(false);
  const [importing, setImporting] = useState(false);
  const [confirmFile, setConfirmFile] = useState<File | null>(null);
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null);

  async function exportBackup() {
    setExporting(true);
    setResult(null);
    try {
      const r = await fetch(`${API_URL}/admin/backup`, { credentials: "include" });
      if (!r.ok) throw new Error((await r.json().catch(() => null))?.detail ?? `Falha (${r.status})`);
      const blob = await r.blob();
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = r.headers.get("Content-Disposition")?.match(/filename="(.+?)"/)?.[1] ?? "aiworkspace.backup";
      a.click();
      URL.revokeObjectURL(a.href);
    } catch (e) {
      setResult({ ok: false, text: e instanceof Error ? e.message : "Falha ao exportar" });
    } finally {
      setExporting(false);
    }
  }

  async function importBackup(f: File) {
    setImporting(true);
    setResult(null);
    try {
      const form = new FormData();
      form.append("file", f);
      const r = await fetch(`${API_URL}/admin/restore`, { method: "POST", credentials: "include", body: form });
      const data = await r.json().catch(() => null);
      if (!r.ok) throw new Error(data?.detail ?? `Falha (${r.status})`);
      setResult({ ok: true, text: data?.note ?? "Backup restaurado." });
    } catch (e) {
      setResult({ ok: false, text: e instanceof Error ? e.message : "Falha ao importar" });
    } finally {
      setImporting(false);
      setConfirmFile(null);
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  return (
    <div className="space-y-3 rounded-xl border border-border bg-surface p-4">
      <p className="flex items-center gap-2 text-sm font-semibold text-ink"><DatabaseBackup size={16} /> Backup e migração</p>
      <p className="text-xs leading-5 text-muted">
        Exporta o sistema <span className="text-ink-soft">inteiro</span> (usuários, chats, modelos, memórias, integrações,
        segredos cifrados) num único arquivo. Para migrar de servidor: suba o app na máquina nova com o
        <span className="font-mono text-ink-soft"> mesmo APP_SECRET</span> no .env e importe o arquivo aqui.
      </p>
      <div className="flex flex-wrap items-center gap-2.5">
        <button onClick={exportBackup} disabled={exporting} className="flex items-center gap-1.5 rounded-full bg-accent px-4 py-1.5 text-sm font-medium text-white transition-colors hover:bg-accent-hover disabled:opacity-60">
          {exporting ? <Loader2 size={14} className="animate-spin" /> : <Download size={14} />} Exportar sistema
        </button>
        <input ref={fileRef} type="file" accept=".backup,.dump" className="hidden"
          onChange={(e) => { const f = e.target.files?.[0]; if (f) setConfirmFile(f); }} />
        <button onClick={() => fileRef.current?.click()} disabled={importing} className="flex items-center gap-1.5 rounded-full border border-border px-4 py-1.5 text-sm text-ink-soft transition-colors hover:bg-hover hover:text-ink disabled:opacity-60">
          {importing ? <Loader2 size={14} className="animate-spin" /> : <Upload size={14} />} Importar backup
        </button>
      </div>
      {confirmFile && (
        <div className="space-y-2 rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2.5 text-sm">
          <p className="text-ink">
            Importar <span className="font-mono text-xs">{confirmFile.name}</span>?{" "}
            <span className="text-red-300">Isto SUBSTITUI todos os dados atuais</span> (usuários, chats, tudo). Não tem volta.
          </p>
          <div className="flex items-center gap-2">
            <button onClick={() => importBackup(confirmFile)} className="rounded-full bg-red-500/90 px-4 py-1 text-xs font-medium text-white hover:bg-red-500">
              Sim, substituir tudo
            </button>
            <button onClick={() => { setConfirmFile(null); if (fileRef.current) fileRef.current.value = ""; }} className="rounded-full border border-border px-4 py-1 text-xs text-muted hover:text-ink">
              Cancelar
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
