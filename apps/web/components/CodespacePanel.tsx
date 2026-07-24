"use client";

import { useEffect, useRef, useState } from "react";
import {
  ArrowLeft, Brain, Check, Copy, Globe, HardDrive, KeyRound, Loader2, MessageSquare, MoreVertical,
  Pencil, Plus, RefreshCw, Search, Sparkles, Trash2, Waypoints, X,
} from "lucide-react";
import { api, ApiError } from "@/lib/api";
import type { CodespaceChatLite, CodespaceEgo, CodespaceEgoEdge, CodespaceProject, CodespaceSymbol, MemoryItem, User } from "@/lib/types";
import { useConfirm, usePrompt } from "@/components/ConfirmDialog";
import { AnchoredMenu, MenuItem } from "@/components/ui";
import { copyText } from "@/lib/clipboard";
import CodespaceFileBrowser, { extLang } from "@/components/CodespaceFileBrowser";
import CodespaceGraphView from "@/components/CodespaceGraphView";

interface GithubAccountLite {
  id: string;
  login: string;
}

type Source = "git" | "git-ssh" | "local";

const SOURCE_META: Record<Source, { icon: typeof Globe; label: string }> = {
  git: { icon: Globe, label: "HTTPS" },
  "git-ssh": { icon: KeyRound, label: "SSH" },
  local: { icon: HardDrive, label: "Local" },
};

const STATUS_DOT: Record<string, string> = {
  pending: "bg-muted", cloning: "bg-accent-hover", indexing: "bg-accent-hover",
  ready: "bg-green-400", error: "bg-red-400",
};
const STATUS_LABEL: Record<string, string> = {
  pending: "Na fila", cloning: "Clonando…", indexing: "Indexando…", ready: "Pronto", error: "Erro",
};

function StatusDot({ status }: { status: string }) {
  const spin = status === "cloning" || status === "indexing" || status === "pending";
  return (
    <span className="flex items-center gap-1.5 text-[11px] text-muted">
      <span className={`h-1.5 w-1.5 rounded-full ${STATUS_DOT[status] ?? "bg-muted"} ${spin ? "animate-pulse" : ""}`} />
      {STATUS_LABEL[status] ?? status}
    </span>
  );
}

/* título editável inline (clique no lápis → vira input; Enter/blur salva, Esc cancela) */
function EditableTitle({ value, onSave, className = "" }: { value: string; onSave: (v: string) => Promise<void>; className?: string }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value);
  const [saving, setSaving] = useState(false);
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => { if (editing) { setDraft(value); requestAnimationFrame(() => ref.current?.select()); } }, [editing, value]);

  async function commit() {
    const v = draft.trim();
    setEditing(false);
    if (!v || v === value) return;
    setSaving(true);
    try { await onSave(v); } finally { setSaving(false); }
  }

  if (editing) {
    return (
      <input
        ref={ref} value={draft} onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => { if (e.key === "Enter") commit(); if (e.key === "Escape") setEditing(false); }}
        className={`rounded-lg border border-accent/50 bg-surface2 px-2 py-0.5 outline-none ${className}`}
      />
    );
  }
  return (
    <span className={`group flex min-w-0 items-center gap-1.5 ${className}`}>
      <span className="truncate">{value}</span>
      {saving ? <Loader2 size={12} className="shrink-0 animate-spin text-muted" /> : (
        <button onClick={() => setEditing(true)} title="Renomear" className="shrink-0 rounded p-0.5 text-muted opacity-0 transition-opacity hover:text-ink group-hover:opacity-100">
          <Pencil size={12} />
        </button>
      )}
    </span>
  );
}

/* --------------------------------- Modal: novo projeto --------------------------------- */
function NewProjectModal({
  accounts, onClose, onCreated,
}: {
  accounts: GithubAccountLite[]; onClose: () => void; onCreated: (p: CodespaceProject) => void;
}) {
  const [source, setSource] = useState<Source>("git");
  const [name, setName] = useState("");
  const [repoUrl, setRepoUrl] = useState("");
  const [branch, setBranch] = useState("main");
  const [accountId, setAccountId] = useState(accounts[0]?.id ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [created, setCreated] = useState<CodespaceProject | null>(null);
  const [copied, setCopied] = useState(false);

  async function submit() {
    if (source !== "local" && !repoUrl.trim()) { setError("Informe a URL do repositório."); return; }
    setSaving(true);
    setError("");
    try {
      const p = await api.post<CodespaceProject>("/codespace/projects", {
        name: name.trim() || repoUrl.trim().split("/").pop()?.replace(/\.git$/, "") || "Projeto",
        source,
        repo_url: repoUrl.trim(),
        branch: branch.trim() || "main",
        github_account_id: source === "git" ? (accountId || null) : null,
      });
      if (source === "git-ssh" && p.ssh_public_key) {
        setCreated(p);
      } else {
        onCreated(p);
        onClose();
      }
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Falha ao criar o projeto");
    } finally {
      setSaving(false);
    }
  }

  if (created) {
    return (
      <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/60 p-4">
        <div className="w-full max-w-md overflow-hidden rounded-2xl border border-border bg-surface shadow-2xl">
          <div className="flex items-center gap-2 border-b border-border px-4 py-3">
            <KeyRound size={18} className="text-accent-hover" />
            <span className="flex-1 text-sm font-semibold text-ink">Adicione a deploy key</span>
          </div>
          <div className="flex flex-col gap-3 p-4">
            <p className="text-xs text-muted">
              Cole esta chave pública como deploy key (leitura/escrita) no repositório remoto,
              depois reindexe.
            </p>
            <div className="flex items-start gap-2 rounded-lg border border-border bg-surface2 px-3 py-2">
              <code className="min-w-0 flex-1 break-all font-mono text-[11px] text-ink-soft">{created.ssh_public_key}</code>
              <button
                onClick={() => { copyText(created.ssh_public_key ?? ""); setCopied(true); setTimeout(() => setCopied(false), 1500); }}
                className="shrink-0 rounded-lg p-1.5 text-muted hover:bg-hover hover:text-ink">
                {copied ? <Check size={14} className="text-green-400" /> : <Copy size={14} />}
              </button>
            </div>
            <button onClick={() => { onCreated(created); onClose(); }}
              className="mt-1 flex items-center justify-center gap-1.5 rounded-full bg-accent px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-accent-hover">
              Concluir
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/60 p-4" onClick={onClose}>
      <div onClick={(e) => e.stopPropagation()} className="w-full max-w-md overflow-hidden rounded-2xl border border-border bg-surface shadow-2xl">
        <div className="flex items-center gap-2 border-b border-border px-4 py-3">
          <span className="flex-1 text-sm font-semibold text-ink">Novo projeto</span>
          <button onClick={onClose} className="rounded-lg p-1 text-muted hover:bg-hover hover:text-ink"><X size={16} /></button>
        </div>
        <div className="flex flex-col gap-3 p-4">
          <div className="flex rounded-lg border border-border bg-surface2 p-0.5">
            {(["git", "git-ssh", "local"] as Source[]).map((s) => {
              const M = SOURCE_META[s];
              return (
                <button key={s} onClick={() => setSource(s)}
                  className={`flex flex-1 items-center justify-center gap-1.5 rounded-md py-1.5 text-xs font-medium transition-colors ${source === s ? "bg-accent text-white" : "text-ink-soft hover:bg-hover"}`}>
                  <M.icon size={13} /> {M.label}
                </button>
              );
            })}
          </div>

          {source !== "local" && (
            <label className="flex flex-col gap-1">
              <span className="text-xs font-medium text-muted">URL do repositório</span>
              <input value={repoUrl} onChange={(e) => setRepoUrl(e.target.value)}
                placeholder={source === "git-ssh" ? "git@github.com:usuario/repo.git" : "https://github.com/usuario/repo.git"}
                className="rounded-lg border border-border bg-surface2 px-3 py-2 font-mono text-xs text-ink outline-none focus:border-accent/50" />
            </label>
          )}
          <label className="flex flex-col gap-1">
            <span className="text-xs font-medium text-muted">Nome {source !== "local" && "(opcional)"}</span>
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder={source === "local" ? "Meu projeto" : "Deriva do repositório se vazio"}
              className="rounded-lg border border-border bg-surface px-3 py-2 text-sm text-ink outline-none focus:border-accent/50 placeholder:text-muted" />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-xs font-medium text-muted">Branch</span>
            <input value={branch} onChange={(e) => setBranch(e.target.value)}
              className="rounded-lg border border-border bg-surface px-3 py-2 text-sm text-ink outline-none focus:border-accent/50" />
          </label>
          {source === "git" && (
            <label className="flex flex-col gap-1">
              <span className="text-xs font-medium text-muted">Conta GitHub (repos privados)</span>
              <select value={accountId} onChange={(e) => setAccountId(e.target.value)}
                className="rounded-lg border border-border bg-surface2 px-3 py-2 text-sm text-ink outline-none">
                <option value="">Nenhuma (só repos públicos)</option>
                {accounts.map((a) => <option key={a.id} value={a.id}>{a.login}</option>)}
              </select>
            </label>
          )}
          {source === "git-ssh" && (
            <p className="text-[11px] text-muted">Uma deploy key é gerada e mostrada após criar — cole no repositório remoto.</p>
          )}
          {error && <p className="text-xs text-red-400">{error}</p>}
          <button onClick={submit} disabled={saving}
            className="mt-1 flex items-center justify-center gap-1.5 rounded-full bg-accent px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-accent-hover disabled:opacity-60">
            {saving ? <Loader2 size={15} className="animate-spin" /> : <Plus size={15} />} Criar
          </button>
        </div>
      </div>
    </div>
  );
}

/** Resolve um "modelo padrão" ("custom:<id>" ou modelo base) no par
 *  {model, model_config_id} que o POST /chats espera — `chat.model` guarda o
 *  MODELO BASE, nunca a string "custom:..." (senão o seletor mostra o id cru e
 *  o envio falha no provedor). Null = não dá pra resolver (sem padrão, ou o
 *  modelo custom foi excluído). */
async function resolveChatModel(preferred: string | null | undefined): Promise<{ model: string; model_config_id: string | null } | null> {
  const dm = (preferred || "").trim();
  if (!dm) return null;
  if (!dm.startsWith("custom:")) return { model: dm, model_config_id: null };
  const id = dm.slice(7);
  try {
    const configs = await api.get<{ id: string; base_model: string }[]>("/models");
    const mc = configs.find((c) => c.id === id);
    if (mc) return { model: mc.base_model, model_config_id: mc.id };
  } catch { /* sem lista de modelos → trata como não-resolvido */ }
  return null;
}

/** Modelo dos novos chats do projeto: padrão do projeto → padrão do usuário. */
async function resolveProjectChatModel(project: CodespaceProject, userDefault: string | null | undefined) {
  return (await resolveChatModel(project.default_model)) ?? (await resolveChatModel(userDefault));
}

const NO_MODEL_MSG =
  "Selecione um modelo primeiro: abra um chat do projeto e use “Definir como padrão do projeto”, " +
  "ou defina um modelo padrão nas Configurações.";

function fmtStats(p: CodespaceProject): string {
  const s = p.stats;
  if (!s) return "";
  const parts: string[] = [];
  if (s.files != null) parts.push(`${s.files} arquivos`);
  if (s.symbols != null) parts.push(`${s.symbols} símbolos`);
  if (s.edges != null) parts.push(`${s.edges} relações`);
  return parts.join(" · ");
}

function fmtDate(iso: string): string {
  try {
    return new Date(iso).toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
  } catch { return iso; }
}

/* --------------------------------- Chats --------------------------------- */
function ChatRow({ chat, onOpen, onRenamed, onDeleted }: {
  chat: CodespaceChatLite; onOpen: () => void; onRenamed: (title: string) => void; onDeleted: () => void;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const btnRef = useRef<HTMLButtonElement>(null);
  const prompt = usePrompt();
  const confirm = useConfirm();

  async function rename() {
    setMenuOpen(false);
    const v = await prompt({ title: "Renomear chat", defaultValue: chat.title });
    if (!v || !v.trim() || v.trim() === chat.title) return;
    try {
      const updated = await api.patch<{ title: string }>(`/chats/${chat.id}`, { title: v.trim() });
      onRenamed(updated.title);
    } catch (e) {
      alert(e instanceof ApiError ? e.message : "Falha ao renomear o chat");
    }
  }

  async function del() {
    setMenuOpen(false);
    if (!(await confirm({ title: `Excluir "${chat.title}"?`, confirmLabel: "Excluir", danger: true }))) return;
    try {
      await api.del(`/chats/${chat.id}`);
      onDeleted();
    } catch (e) {
      alert(e instanceof ApiError ? e.message : "Falha ao excluir o chat");
    }
  }

  return (
    <div className="flex items-center gap-1 rounded-xl border border-border bg-surface pr-1.5 transition-colors hover:bg-hover">
      <button onClick={onOpen} className="flex min-w-0 flex-1 items-center justify-between gap-2 px-3 py-2.5 text-left">
        <span className="flex min-w-0 items-center gap-2">
          <MessageSquare size={14} className="shrink-0 text-muted" />
          <span className="truncate text-sm text-ink">{chat.title || "Novo Chat"}</span>
        </span>
        <span className="shrink-0 text-[11px] text-muted">{fmtDate(chat.updated_at)}</span>
      </button>
      <button ref={btnRef} onClick={() => setMenuOpen(true)} className="shrink-0 rounded-lg p-1.5 text-muted hover:bg-hover hover:text-ink">
        <MoreVertical size={14} />
      </button>
      {menuOpen && (
        <AnchoredMenu anchorRef={btnRef} onClose={() => setMenuOpen(false)}>
          <MenuItem icon={<Pencil size={14} />} onClick={rename}>Renomear</MenuItem>
          <MenuItem icon={<Trash2 size={14} />} onClick={del} danger>Excluir</MenuItem>
        </AnchoredMenu>
      )}
    </div>
  );
}

function ProjectChatsTab({ project, onOpenChat }: { project: CodespaceProject; onOpenChat: (chatId: string) => void }) {
  const [chats, setChats] = useState<CodespaceChatLite[] | null>(null);
  const [q, setQ] = useState("");
  const [creating, setCreating] = useState(false);
  const [user, setUser] = useState<User | null>(null);

  useEffect(() => {
    api.get<CodespaceChatLite[]>(`/codespace/projects/${project.id}/chats`).then(setChats).catch(() => setChats([]));
    api.get<User>("/auth/me").then(setUser).catch(() => {});
  }, [project.id]);

  async function novoChat() {
    setCreating(true);
    try {
      const resolved = await resolveProjectChatModel(project, user?.default_model);
      if (!resolved) {
        alert(NO_MODEL_MSG);
        return;
      }
      const chat = await api.post<{ id: string }>("/chats", {
        title: project.name, model: resolved.model, model_config_id: resolved.model_config_id,
        project_id: project.id,
      });
      onOpenChat(chat.id);
    } catch (e) {
      alert(e instanceof ApiError ? e.message : "Falha ao criar o chat");
    } finally {
      setCreating(false);
    }
  }

  const filtered = (chats ?? []).filter((c) => c.title.toLowerCase().includes(q.trim().toLowerCase()));

  return (
    <div>
      <div className="mb-3 flex items-center gap-2">
        <div className="flex flex-1 items-center gap-2 rounded-full border border-border bg-surface2 px-3 py-1.5">
          <Search size={13} className="text-muted" />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Buscar chat…"
            className="flex-1 bg-transparent text-sm text-ink outline-none placeholder:text-muted" />
        </div>
        <button onClick={novoChat} disabled={creating}
          className="flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full bg-accent px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-accent-hover disabled:opacity-60">
          {creating ? <Loader2 size={13} className="animate-spin" /> : <Plus size={13} />} Novo chat
        </button>
      </div>
      {chats === null ? (
        <p className="py-8 text-center text-sm text-muted">Carregando…</p>
      ) : filtered.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-border px-4 py-10 text-center">
          <MessageSquare size={22} className="mx-auto mb-2 text-muted" />
          <p className="text-sm text-muted">{chats.length ? "Nada encontrado." : "Nenhum chat ainda."}</p>
        </div>
      ) : (
        <div className="space-y-1.5">
          {filtered.map((c) => (
            <ChatRow
              key={c.id} chat={c} onOpen={() => onOpenChat(c.id)}
              onRenamed={(title) => setChats((cs) => (cs ?? []).map((x) => (x.id === c.id ? { ...x, title } : x)))}
              onDeleted={() => setChats((cs) => (cs ?? []).filter((x) => x.id !== c.id))}
            />
          ))}
        </div>
      )}
    </div>
  );
}

/* ---------------------------------- Grafo --------------------------------- */
function EgoColumn({ title, edges }: { title: string; edges: CodespaceEgoEdge[] }) {
  return (
    <div className="flex min-w-0 flex-1 flex-col gap-1.5">
      <span className="px-1 text-[11px] font-medium uppercase tracking-wide text-muted">{title}</span>
      {edges.length === 0 ? (
        <p className="rounded-lg border border-dashed border-border px-2 py-3 text-center text-[11px] text-muted">nenhuma</p>
      ) : (
        edges.map((e, i) => (
          <div key={i} className="rounded-lg border border-border bg-surface2 px-2 py-1.5 text-[11px]">
            <div className="truncate font-mono text-ink-soft">{e.fqn || "?"}</div>
            <div className="flex items-center justify-between gap-1 text-muted">
              <span className="truncate">{e.path}{e.line ? `:${e.line}` : ""}</span>
              {e.confidence && <span className={e.confidence === "certain" ? "text-green-400" : e.confidence === "inferred" ? "text-amber-400" : "text-muted"}>{e.confidence}</span>}
            </div>
          </div>
        ))
      )}
    </div>
  );
}

function ProjectGraphTab({ project, onOpenFile }: { project: CodespaceProject; onOpenFile: (path: string) => void }) {
  const [mode, setMode] = useState<"overview" | "search">("overview");
  const [q, setQ] = useState("");
  const [searching, setSearching] = useState(false);
  const [results, setResults] = useState<CodespaceSymbol[] | null>(null);
  const [ego, setEgo] = useState<CodespaceEgo | null>(null);
  const [loadingEgo, setLoadingEgo] = useState(false);

  async function search() {
    if (!q.trim()) return;
    setSearching(true);
    setEgo(null);
    try {
      const r = await api.get<{ symbols: CodespaceSymbol[] }>(`/codespace/projects/${project.id}/graph/find?query=${encodeURIComponent(q.trim())}`);
      setResults(r.symbols ?? []);
    } catch {
      setResults([]);
    } finally {
      setSearching(false);
    }
  }

  async function openEgo(fqn: string | null) {
    if (!fqn) return;
    setLoadingEgo(true);
    try {
      const r = await api.get<CodespaceEgo>(`/codespace/projects/${project.id}/graph/ego?symbol=${encodeURIComponent(fqn)}`);
      setEgo(r);
    } catch {
      setEgo({ symbol: null, children: [], calls: [], called_by: [], warnings: [], error: "falha ao carregar" });
    } finally {
      setLoadingEgo(false);
    }
  }

  return (
    <div>
      <div className="mb-3 flex rounded-lg border border-border bg-surface2 p-0.5">
        <button onClick={() => setMode("overview")}
          className={`flex-1 rounded-md py-1.5 text-xs font-medium transition-colors ${mode === "overview" ? "bg-accent text-white" : "text-ink-soft hover:bg-hover"}`}>
          Visão geral
        </button>
        <button onClick={() => setMode("search")}
          className={`flex-1 rounded-md py-1.5 text-xs font-medium transition-colors ${mode === "search" ? "bg-accent text-white" : "text-ink-soft hover:bg-hover"}`}>
          Buscar símbolo
        </button>
      </div>

      {mode === "overview" ? (
        <CodespaceGraphView projectId={project.id} onOpenFile={onOpenFile} />
      ) : (
        <>
          <div className="mb-3 flex items-center gap-2">
            <div className="flex flex-1 items-center gap-2 rounded-full border border-border bg-surface2 px-3 py-1.5">
              <Search size={14} className="text-muted" />
              <input value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === "Enter" && search()}
                placeholder="Nome do símbolo…" className="flex-1 bg-transparent text-sm text-ink outline-none placeholder:text-muted" />
            </div>
            <button onClick={search} disabled={searching || !q.trim()}
              className="flex shrink-0 items-center gap-1.5 rounded-full bg-accent px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-accent-hover disabled:opacity-60">
              {searching ? <Loader2 size={13} className="animate-spin" /> : "Buscar"}
            </button>
          </div>

          {loadingEgo ? (
            <p className="py-10 text-center text-sm text-muted">Carregando…</p>
          ) : ego ? (
            <div>
              <button onClick={() => setEgo(null)} className="mb-3 flex items-center gap-1 text-xs text-muted hover:text-ink">
                <ArrowLeft size={13} /> Voltar
              </button>
              {ego.error ? (
                <p className="text-sm text-red-400">{ego.error}</p>
              ) : (
                <div className="flex flex-col gap-3">
                  <div className="flex flex-col gap-3 sm:flex-row">
                    <EgoColumn title="Quem chama" edges={ego.called_by} />
                    <div className="flex min-w-0 flex-1 flex-col gap-2">
                      <span className="px-1 text-[11px] font-medium uppercase tracking-wide text-accent-hover">Símbolo</span>
                      <div className="rounded-xl border-2 border-accent/40 bg-accent/10 px-3 py-2.5">
                        <div className="truncate font-mono text-sm font-semibold text-ink">{ego.symbol?.fqn}</div>
                        <div className="mt-0.5 truncate text-[11px] text-muted">{ego.symbol?.kind} · {ego.symbol?.path}{ego.symbol?.line ? `:${ego.symbol.line}` : ""}</div>
                        {ego.symbol?.signature && <div className="mt-1 truncate font-mono text-[11px] text-ink-soft">{ego.symbol.signature}</div>}
                      </div>
                      {ego.children.length > 0 && (
                        <div className="mt-1 flex flex-col gap-1">
                          {ego.children.map((c, i) => (
                            <div key={i} className="truncate rounded-lg bg-surface2 px-2 py-1 font-mono text-[11px] text-ink-soft">{c.name} <span className="text-muted">({c.kind})</span></div>
                          ))}
                        </div>
                      )}
                    </div>
                    <EgoColumn title="Quem ele chama" edges={ego.calls} />
                  </div>
                  {ego.warnings.length > 0 && <p className="text-[11px] text-amber-500">{ego.warnings.join(" · ")}</p>}
                </div>
              )}
            </div>
          ) : results === null ? null : results.length === 0 ? (
            <p className="py-10 text-center text-sm text-muted">Nada encontrado.</p>
          ) : (
            <div className="space-y-1.5">
              {results.map((s, i) => (
                <button key={i} onClick={() => openEgo(s.fqn)}
                  className="flex w-full items-center justify-between gap-2 rounded-xl border border-border bg-surface px-3 py-2 text-left transition-colors hover:bg-hover">
                  <span className="min-w-0">
                    <span className="block truncate font-mono text-xs text-ink">{s.fqn}</span>
                    <span className="block truncate text-[11px] text-muted">{s.kind} · {s.path}{s.line ? `:${s.line}` : ""}</span>
                  </span>
                  <Waypoints size={14} className="shrink-0 text-muted" />
                </button>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}

/* ------------------------------- Memória ---------------------------------- */
function ProjectMemoryTab({ bankId }: { bankId: string }) {
  const [items, setItems] = useState<MemoryItem[] | null>(null);
  const [q, setQ] = useState("");
  const [draft, setDraft] = useState("");
  const [adding, setAdding] = useState(false);

  function load() {
    api.get<MemoryItem[]>(`/memory?bank_id=${bankId}&q=${encodeURIComponent(q.trim())}`)
      .then(setItems).catch(() => setItems([]));
  }

  // com `q`, cada requisição é uma busca SEMÂNTICA no mem0 (embedding) — debounce
  // pra não disparar uma por tecla digitada; sem `q`, carrega na hora.
  useEffect(() => {
    if (!q.trim()) { load(); return; }
    const t = setTimeout(load, 300);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bankId, q]);

  async function add() {
    const text = draft.trim();
    if (!text) return;
    setAdding(true);
    try {
      await api.post("/memory", { text, scope: "bank", bank_id: bankId });
      setDraft("");
      load();
    } catch (e) {
      alert(e instanceof ApiError ? e.message : "Falha ao adicionar");
    } finally {
      setAdding(false);
    }
  }

  async function del(id: string) {
    setItems((its) => (its ?? []).filter((i) => i.id !== id));
    await api.del(`/memory/${id}`);
  }

  return (
    <div>
      <div className="mb-3 flex items-center gap-2 rounded-full border border-border bg-surface2 px-3 py-1.5">
        <Search size={13} className="text-muted" />
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Buscar memórias…"
          className="flex-1 bg-transparent text-sm text-ink outline-none placeholder:text-muted" />
      </div>
      <div className="mb-3 flex items-start gap-2 rounded-xl border border-border bg-surface px-3 py-2">
        <textarea
          value={draft} onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) add(); }}
          placeholder="Anotar algo neste projeto (Ctrl+Enter para salvar)…" rows={2}
          className="flex-1 resize-none bg-transparent text-sm text-ink outline-none placeholder:text-muted"
        />
        <button onClick={add} disabled={adding || !draft.trim()}
          className="flex shrink-0 items-center gap-1 self-end rounded-full bg-accent px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-accent-hover disabled:opacity-60">
          {adding ? <Loader2 size={13} className="animate-spin" /> : <Plus size={13} />} Adicionar
        </button>
      </div>
      {items === null ? (
        <p className="py-8 text-center text-sm text-muted">Carregando…</p>
      ) : items.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-border px-4 py-10 text-center">
          <Brain size={22} className="mx-auto mb-2 text-muted" />
          <p className="text-sm text-muted">{q ? "Nada encontrado." : "Nenhuma memória ainda neste projeto."}</p>
        </div>
      ) : (
        <div className="space-y-1.5">
          {items.map((m) => (
            <div key={m.id} className="flex items-start gap-2 rounded-xl border border-border bg-surface px-3 py-2.5">
              <p className="min-w-0 flex-1 whitespace-pre-wrap break-words text-sm text-ink-soft">{m.text}</p>
              <button onClick={() => del(m.id)} title="Excluir"
                className="shrink-0 rounded-lg p-1 text-muted transition-colors hover:bg-hover hover:text-red-400">
                <Trash2 size={13} />
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/* ------------------------------ Project detail ----------------------------- */
type Tab = "chats" | "arquivos" | "grafo" | "memoria";

function ProjectDetail({
  project, onBack, onUpdated, onDeleted, onOpenChat,
}: {
  project: CodespaceProject;
  onBack: () => void;
  onUpdated: (p: CodespaceProject) => void;
  onDeleted: () => void;
  onOpenChat: (chatId: string, prefill?: string) => void;
}) {
  const [tab, setTab] = useState<Tab>("chats");
  const [jumpPath, setJumpPath] = useState<string | undefined>(undefined);
  const [busy, setBusy] = useState(false);
  const [showKey, setShowKey] = useState(false);
  const confirm = useConfirm();
  const SourceIcon = SOURCE_META[project.source].icon;

  async function rename(name: string) {
    const updated = await api.patch<CodespaceProject>(`/codespace/projects/${project.id}`, { name });
    onUpdated(updated);
  }

  async function reindex() {
    setBusy(true);
    try { onUpdated(await api.post<CodespaceProject>(`/codespace/projects/${project.id}/reindex`)); } finally { setBusy(false); }
  }

  async function resync() {
    if (!(await confirm({
      title: `Ressincronizar "${project.name}"?`,
      body: "Reclona do zero a partir do repositório remoto — DESCARTA qualquer commit local não enviado. Envie (push) antes se quiser manter esse trabalho.",
      confirmLabel: "Descartar e ressincronizar",
      danger: true,
    }))) return;
    setBusy(true);
    try { onUpdated(await api.post<CodespaceProject>(`/codespace/projects/${project.id}/resync`)); } finally { setBusy(false); }
  }

  async function refine() {
    setBusy(true);
    try { await api.post(`/codespace/projects/${project.id}/refine`); } finally { setBusy(false); }
  }

  async function del() {
    if (!(await confirm({ title: `Excluir "${project.name}"?`, body: "A working copy e o índice são apagados.", confirmLabel: "Excluir", danger: true }))) return;
    setBusy(true);
    try { await api.del(`/codespace/projects/${project.id}`); onDeleted(); } finally { setBusy(false); }
  }

  function reference(path: string, content: string) {
    const fence = extLang(path);
    const prefill = `Sobre o arquivo \`${path}\`:\n\n\`\`\`${fence}\n${content}\n\`\`\`\n\n`;
    api.get<CodespaceChatLite[]>(`/codespace/projects/${project.id}/chats`).then(async (chats) => {
      if (chats.length > 0) { onOpenChat(chats[0].id, prefill); return; }
      const me = await api.get<User>("/auth/me");
      const resolved = await resolveProjectChatModel(project, me.default_model);
      if (!resolved) { alert(NO_MODEL_MSG); return; }
      const chat = await api.post<{ id: string }>("/chats", {
        title: project.name, model: resolved.model, model_config_id: resolved.model_config_id,
        project_id: project.id,
      });
      onOpenChat(chat.id, prefill);
    }).catch(() => alert("Falha ao abrir o chat para referenciar o arquivo"));
  }

  function openInExplorer(path: string) {
    setJumpPath(path);
    setTab("arquivos");
  }

  return (
    <div>
      <button onClick={onBack} className="mb-3 flex items-center gap-1 text-xs text-muted hover:text-ink">
        <ArrowLeft size={13} /> Todos os projetos
      </button>

      <div className="mb-4 flex flex-wrap items-start justify-between gap-3 rounded-xl border border-border bg-surface px-4 py-3">
        <div className="flex min-w-0 items-start gap-2.5">
          <SourceIcon size={16} className="mt-1 shrink-0 text-muted" />
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <EditableTitle value={project.name} onSave={rename} className="text-base font-semibold text-ink" />
              <StatusDot status={project.index_status} />
            </div>
            {project.source !== "local" && <p className="mt-0.5 truncate text-xs text-muted">{project.repo_url} · {project.branch}</p>}
            {project.index_status === "ready" && (
              <p className="mt-1 text-xs text-muted">
                {fmtStats(project)}
                {project.stats?.refined && <span className="ml-1.5 inline-flex items-center gap-1 text-accent-hover"><Sparkles size={11} /> refinado</span>}
              </p>
            )}
            {project.index_status === "ready" && !project.stats?.files && (
              <p className="mt-1 text-xs text-amber-400">
                Nenhum arquivo com símbolos para o grafo
                {project.stats?.unsupported_ext?.length ? ` (${project.stats.unsupported_ext.join(", ")})` : ""}
                {" — "}os arquivos continuam legíveis e pesquisáveis pela IA.
              </p>
            )}
            {project.index_status === "error" && <p className="mt-1 text-xs text-red-400">{project.error_message}</p>}
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          {project.source === "git-ssh" && (
            <button onClick={() => setShowKey(true)} title="Ver deploy key pública" className="rounded-lg p-1.5 text-muted transition-colors hover:bg-hover hover:text-ink">
              <KeyRound size={15} />
            </button>
          )}
          {project.index_status === "ready" && !project.stats?.refined && (
            <button onClick={refine} disabled={busy} title="Refinar (jedi): promove chamadas Python inferidas a certas"
              className="rounded-lg p-1.5 text-muted transition-colors hover:bg-hover hover:text-ink disabled:opacity-60">
              <Sparkles size={15} />
            </button>
          )}
          <button onClick={reindex} disabled={busy || project.index_status === "cloning" || project.index_status === "indexing"}
            title="Reindexar (seguro)" className="rounded-lg p-1.5 text-muted transition-colors hover:bg-hover hover:text-ink disabled:opacity-60">
            <RefreshCw size={15} className={busy ? "animate-spin" : ""} />
          </button>
          {project.source !== "local" && (
            <button onClick={resync} disabled={busy || project.index_status === "cloning" || project.index_status === "indexing"}
              title="Ressincronizar: reclona do zero" className="rounded-lg p-1.5 text-muted transition-colors hover:bg-hover hover:text-amber-400 disabled:opacity-60">
              <RefreshCw size={15} className="rotate-180" />
            </button>
          )}
          <button onClick={del} disabled={busy} title="Excluir" className="rounded-lg p-1.5 text-muted transition-colors hover:bg-hover hover:text-red-400 disabled:opacity-60">
            <Trash2 size={15} />
          </button>
        </div>
      </div>

      {showKey && project.ssh_public_key && (
        <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/60 p-4" onClick={() => setShowKey(false)}>
          <div onClick={(e) => e.stopPropagation()} className="w-full max-w-md rounded-2xl border border-border bg-surface p-4 shadow-2xl">
            <div className="mb-2 flex items-center justify-between">
              <span className="text-sm font-semibold text-ink">Deploy key pública</span>
              <button onClick={() => setShowKey(false)} className="rounded-lg p-1 text-muted hover:bg-hover hover:text-ink"><X size={16} /></button>
            </div>
            <div className="flex items-start gap-2 rounded-lg border border-border bg-surface2 px-3 py-2">
              <code className="min-w-0 flex-1 break-all font-mono text-[11px] text-ink-soft">{project.ssh_public_key}</code>
              <button onClick={() => copyText(project.ssh_public_key ?? "")} className="shrink-0 rounded-lg p-1.5 text-muted hover:bg-hover hover:text-ink"><Copy size={14} /></button>
            </div>
          </div>
        </div>
      )}

      {project.index_status !== "ready" ? (
        <p className="rounded-2xl border border-dashed border-border px-4 py-10 text-center text-sm text-muted">
          {project.index_status === "error" ? "A indexação falhou — corrija e reindexe." : "Aguardando a indexação terminar…"}
        </p>
      ) : (
        <>
          <div className="mb-3 flex items-center gap-1 overflow-x-auto border-b border-border">
            {([
              ["chats", "Chats"], ["arquivos", "Arquivos"], ["grafo", "Grafo"], ["memoria", "Memória"],
            ] as [Tab, string][]).map(([key, label]) => (
              <button key={key} onClick={() => setTab(key)}
                className={`border-b-2 px-3 py-2 text-sm font-medium transition-colors ${tab === key ? "border-accent text-ink" : "border-transparent text-muted hover:text-ink"}`}>
                {label}
              </button>
            ))}
          </div>
          {tab === "chats" && <ProjectChatsTab project={project} onOpenChat={onOpenChat} />}
          {tab === "arquivos" && <CodespaceFileBrowser key={jumpPath} projectId={project.id} onUse={reference} initialPath={jumpPath} />}
          {tab === "grafo" && <ProjectGraphTab project={project} onOpenFile={openInExplorer} />}
          {tab === "memoria" && (
            project.memory_bank_id ? <ProjectMemoryTab bankId={project.memory_bank_id} /> : (
              <div className="rounded-2xl border border-dashed border-border px-4 py-10 text-center">
                <Brain size={22} className="mx-auto mb-2 text-muted" />
                <p className="mb-3 text-sm text-muted">Este projeto ainda não tem um banco de memória.</p>
                <button
                  onClick={async () => onUpdated(await api.post<CodespaceProject>(`/codespace/projects/${project.id}/memory-bank`))}
                  className="rounded-full bg-accent px-4 py-1.5 text-xs font-medium text-white transition-colors hover:bg-accent-hover">
                  Criar banco de memória
                </button>
              </div>
            )
          )}
        </>
      )}
    </div>
  );
}

/* --------------------------------- Painel --------------------------------- */
export default function CodespacePanel({ onOpenChat }: { onOpenChat: (chatId: string, prefill?: string) => void }) {
  const [projects, setProjects] = useState<CodespaceProject[]>([]);
  const [accounts, setAccounts] = useState<GithubAccountLite[]>([]);
  const [loading, setLoading] = useState(true);
  const [showNew, setShowNew] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);
  const confirm = useConfirm();
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const load = () => api.get<CodespaceProject[]>("/codespace/projects").then(setProjects).catch(() => {});

  useEffect(() => {
    Promise.all([
      load(),
      api.get<{ accounts: GithubAccountLite[] }>("/integrations/github").then((r) => setAccounts(r.accounts || [])).catch(() => {}),
    ]).finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    const live = projects.some((p) => p.index_status === "pending" || p.index_status === "cloning" || p.index_status === "indexing");
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
    if (live) pollRef.current = setInterval(load, 2000);
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [projects]);

  const openProject = projects.find((p) => p.id === openId) ?? null;

  async function del(p: CodespaceProject) {
    if (!(await confirm({ title: `Excluir "${p.name}"?`, body: "A working copy e o índice são apagados.", confirmLabel: "Excluir", danger: true }))) return;
    setBusyId(p.id);
    try {
      await api.del(`/codespace/projects/${p.id}`);
      setProjects((ps) => ps.filter((x) => x.id !== p.id));
    } finally {
      setBusyId(null);
    }
  }

  if (openProject) {
    return (
      <ProjectDetail
        project={openProject}
        onBack={() => setOpenId(null)}
        onUpdated={(p) => setProjects((ps) => ps.map((x) => (x.id === p.id ? p : x)))}
        onDeleted={() => { setOpenId(null); setProjects((ps) => ps.filter((x) => x.id !== openProject.id)); }}
        onOpenChat={onOpenChat}
      />
    );
  }

  return (
    <div>
      <div className="mb-3 flex justify-end">
        <button onClick={() => setShowNew(true)}
          className="flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full bg-accent px-4 py-1.5 text-sm font-medium text-white transition-colors hover:bg-accent-hover">
          <Plus size={15} /> Novo projeto
        </button>
      </div>

      {loading ? (
        <p className="py-10 text-center text-sm text-muted">Carregando…</p>
      ) : projects.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-border px-4 py-14 text-center">
          <Globe size={24} className="mx-auto mb-3 text-muted" />
          <p className="text-sm font-medium text-ink">Nenhum projeto ainda</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-2.5 md:grid-cols-2">
          {projects.map((p) => {
            const SourceIcon = SOURCE_META[p.source]?.icon ?? Globe;
            return (
              <button key={p.id} onClick={() => setOpenId(p.id)}
                className="group relative flex items-center gap-3 rounded-2xl border border-border bg-surface p-3.5 text-left transition-all duration-150 hover:border-accent/40 hover:bg-hover">
                <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-surface2 text-ink-soft">
                  <SourceIcon size={17} />
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="truncate text-sm font-medium text-ink">{p.name}</span>
                    <StatusDot status={p.index_status} />
                  </div>
                  <p className="truncate text-[11px] text-muted">
                    {p.source === "local" ? "sem remoto" : `${p.repo_url} · ${p.branch}`}
                    {p.index_status === "ready" && fmtStats(p) ? ` · ${fmtStats(p)}` : ""}
                  </p>
                </div>
                <span
                  role="button"
                  onClick={(e) => { e.stopPropagation(); del(p); }}
                  title="Excluir"
                  className="shrink-0 rounded-lg p-1.5 text-muted opacity-0 transition-opacity hover:bg-hover hover:text-red-400 group-hover:opacity-100"
                >
                  {busyId === p.id ? <Loader2 size={14} className="animate-spin" /> : <Trash2 size={14} />}
                </span>
              </button>
            );
          })}
        </div>
      )}

      {showNew && (
        <NewProjectModal
          accounts={accounts}
          onClose={() => setShowNew(false)}
          onCreated={(p) => setProjects((ps) => [...ps, p])}
        />
      )}
    </div>
  );
}
