"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowLeft, Check, Network, RefreshCw, Shield, Trash2, X } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import type { AdminUser } from "@/lib/types";

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

  return (
    <div className="h-full overflow-y-auto bg-bg p-6">
      <div className="mx-auto max-w-4xl space-y-5">
        <div className="flex items-center justify-between">
          <h1 className="flex items-center gap-2 text-xl font-semibold text-ink">
            <Shield size={20} /> Painel do Admin
          </h1>
          <div className="flex items-center gap-3 text-sm">
            <button onClick={() => router.push("/debug")} className="text-muted hover:text-ink">Debug</button>
            <button onClick={() => router.push("/chat")} className="flex items-center gap-1 text-muted hover:text-ink">
              <ArrowLeft size={16} /> Chat
            </button>
          </div>
        </div>

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

        {/* Rede: allowlist de IP (runtime) + host/porta (deploy) */}
        {net && (
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
        )}

        {/* Atualização (checa o GitHub; aplica pelo update.sh no host) */}
        {net && (
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
        )}

        {pending.length > 0 && (
          <p className="text-sm text-amber-400">{pending.length} usuário(s) aguardando aprovação.</p>
        )}

        {/* tabela de usuários */}
        <div className="overflow-hidden rounded-xl border border-border">
          <table className="w-full text-left text-sm">
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
    </div>
  );
}
