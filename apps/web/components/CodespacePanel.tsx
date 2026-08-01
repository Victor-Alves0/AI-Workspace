"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  ArrowLeft, Brain, Check, CheckCircle2, ChevronLeft, Copy, Download, ExternalLink, FolderOpen, GitBranch, GitMerge, Globe, HardDrive,
  KeyRound, Loader2, MessageSquare, MoreVertical, Pencil, Play, Plus, RefreshCw, Search, Sparkles,
  Square, Terminal, Trash2, Upload, Waypoints, X, XCircle,
} from "lucide-react";
import { api, ApiError } from "@/lib/api";
import type { CodespaceChatLite, CodespaceEgo, CodespaceEgoEdge, CodespacePreview, CodespaceProject, CodespaceSymbol, CodespaceTask, MemoryItem, User } from "@/lib/types";
import { useConfirm, usePrompt } from "@/components/ConfirmDialog";
import { AnchoredMenu, MenuItem, Toggle, InfoDot } from "@/components/ui";
import { copyText } from "@/lib/clipboard";
import CodespaceFileBrowser, { extLang } from "@/components/CodespaceFileBrowser";
import CodespaceGraphView from "@/components/CodespaceGraphView";

interface GithubAccountLite {
  id: string;
  login: string;
}

type Source = "git" | "git-ssh" | "local" | "folder";

const SOURCE_META: Record<Source, { icon: typeof Globe; label: string }> = {
  git: { icon: Globe, label: "HTTPS" },
  "git-ssh": { icon: KeyRound, label: "SSH" },
  local: { icon: HardDrive, label: "Local" },
  folder: { icon: FolderOpen, label: "Pasta" },
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

/* chip de status semântico (verde=indexado, âmbar=em progresso, vermelho=erro) —
   estado do projeto que se lê num relance, separado do violeta da marca. */
const STATUS_CHIP: Record<string, { label: string; cls: string; dot: string }> = {
  pending:  { label: "Na fila",   cls: "border-amber-500/25 bg-amber-500/10 text-amber-300", dot: "bg-amber-400" },
  cloning:  { label: "Clonando",  cls: "border-amber-500/25 bg-amber-500/10 text-amber-300", dot: "bg-amber-400" },
  indexing: { label: "Indexando", cls: "border-amber-500/25 bg-amber-500/10 text-amber-300", dot: "bg-amber-400" },
  ready:    { label: "Indexado",  cls: "border-green-500/25 bg-green-500/10 text-green-300", dot: "bg-green-400" },
  error:    { label: "Erro",      cls: "border-red-500/25 bg-red-500/10 text-red-300",       dot: "bg-red-400" },
};
function StatusChip({ status }: { status: string }) {
  const s = STATUS_CHIP[status] ?? { label: status, cls: "border-border bg-surface2 text-muted", dot: "bg-muted" };
  const spin = status === "cloning" || status === "indexing" || status === "pending";
  return (
    <span className={`inline-flex shrink-0 items-center gap-1.5 rounded-full border px-2 py-0.5 font-mono text-[11px] ${s.cls}`}>
      <span className={`h-1.5 w-1.5 rounded-full ${s.dot} ${spin ? "animate-pulse" : ""}`} />
      {s.label}
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
  const [localPath, setLocalPath] = useState("");
  const [branch, setBranch] = useState("main");
  const [accountId, setAccountId] = useState(accounts[0]?.id ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [created, setCreated] = useState<CodespaceProject | null>(null);
  const [copied, setCopied] = useState(false);
  const needsRepo = source === "git" || source === "git-ssh";

  async function submit() {
    if (needsRepo && !repoUrl.trim()) { setError("Informe a URL do repositório."); return; }
    if (source === "folder" && !localPath.trim()) { setError("Informe o caminho da pasta no servidor."); return; }
    setSaving(true);
    setError("");
    try {
      const p = await api.post<CodespaceProject>("/codespace/projects", {
        name: name.trim() || (source === "folder" ? localPath.trim().split(/[\\/]/).pop() : repoUrl.trim().split("/").pop()?.replace(/\.git$/, "")) || "Projeto",
        source,
        repo_url: repoUrl.trim(),
        local_path: localPath.trim(),
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
            {(["git", "git-ssh", "local", "folder"] as Source[]).map((s) => {
              const M = SOURCE_META[s];
              return (
                <button key={s} onClick={() => setSource(s)}
                  className={`flex flex-1 items-center justify-center gap-1.5 rounded-md py-1.5 text-xs font-medium transition-colors ${source === s ? "bg-accent text-white" : "text-ink-soft hover:bg-hover"}`}>
                  <M.icon size={13} /> {M.label}
                </button>
              );
            })}
          </div>

          {needsRepo && (
            <label className="flex flex-col gap-1">
              <span className="text-xs font-medium text-muted">URL do repositório</span>
              <input value={repoUrl} onChange={(e) => setRepoUrl(e.target.value)}
                placeholder={source === "git-ssh" ? "git@github.com:usuario/repo.git" : "https://github.com/usuario/repo.git"}
                className="rounded-lg border border-border bg-surface2 px-3 py-2 font-mono text-xs text-ink outline-none focus:border-accent/50" />
            </label>
          )}
          {source === "folder" && (
            <label className="flex flex-col gap-1">
              <span className="text-xs font-medium text-muted">Caminho da pasta (no servidor)</span>
              <input value={localPath} onChange={(e) => setLocalPath(e.target.value)}
                placeholder="/home/voce/meu-projeto  ou  C:\\projetos\\app"
                className="rounded-lg border border-border bg-surface2 px-3 py-2 font-mono text-xs text-ink outline-none focus:border-accent/50" />
              <span className="text-[11px] text-muted">Abre um diretório existente (estilo VSCode). Vira um repositório git se ainda não for.</span>
            </label>
          )}
          <label className="flex flex-col gap-1">
            <span className="text-xs font-medium text-muted">Nome {needsRepo && "(opcional)"}</span>
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder={needsRepo ? "Deriva do repositório se vazio" : "Meu projeto"}
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
        <div className="grid grid-cols-1 gap-1.5 sm:grid-cols-2 lg:grid-cols-3">
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

/* ------------------------------ Preview vivo ------------------------------- */
function PreviewStatusBadge({ status, withLabel = false }: { status: CodespacePreview["status"]; withLabel?: boolean }) {
  const map: Record<CodespacePreview["status"], [string, string]> = {
    up: ["bg-green-400", "No ar"], starting: ["bg-amber-400 animate-pulse", "Subindo"],
    crashed: ["bg-red-400", "Caiu"], stopped: ["bg-muted", "Parado"],
  };
  const [dot, label] = map[status];
  return <span className="flex items-center gap-1.5 text-xs text-muted"><span className={`h-2 w-2 shrink-0 rounded-full ${dot}`} />{withLabel && label}</span>;
}

function ProjectPreviewTab({ project }: { project: CodespaceProject }) {
  const [previews, setPreviews] = useState<CodespacePreview[] | null>(null);
  const [selId, setSelId] = useState<string | null>(null);
  const [logsOpen, setLogsOpen] = useState(false);
  const [logs, setLogs] = useState("");
  const [reloadKey, setReloadKey] = useState(0);
  const confirm = useConfirm();

  const load = useCallback(async () => {
    try {
      const r = await api.get<{ previews: CodespacePreview[] }>(`/codespace/projects/${project.id}/previews`);
      setPreviews(r.previews);
    } catch { setPreviews([]); }
  }, [project.id]);
  useEffect(() => { load(); const iv = setInterval(load, 3000); return () => clearInterval(iv); }, [load]);

  const selected = previews?.find((p) => p.id === selId) ?? previews?.[0] ?? null;
  useEffect(() => { if (selected && selId !== selected.id) setSelId(selected.id); }, [selected, selId]);

  // logs ao vivo do selecionado (só quando o painel de logs está aberto)
  useEffect(() => {
    if (!logsOpen || !selected) return;
    let alive = true;
    const poll = async () => {
      try {
        const r = await api.get<CodespacePreview>(`/codespace/projects/${project.id}/previews/${selected.id}`);
        if (alive) setLogs(r.logs ?? "");
      } catch { /* ignore */ }
    };
    poll();
    const iv = setInterval(poll, 2000);
    return () => { alive = false; clearInterval(iv); };
  }, [logsOpen, selected?.id, project.id]);

  // o navegador alcança localhost só na MESMA máquina (desktop); LAN usa o host atual.
  const urlOf = (p: CodespacePreview) =>
    p.expose === "localhost" ? `http://localhost:${p.port}` : `http://${window.location.hostname}:${p.port}`;

  async function stop(p: CodespacePreview) {
    if (!(await confirm({ title: `Parar o preview na porta ${p.port}?`, confirmLabel: "Parar", danger: true }))) return;
    try { await api.post(`/codespace/projects/${project.id}/previews/${p.id}/stop`); await load(); } catch { /* ignore */ }
  }

  if (previews === null) return <p className="py-8 text-center text-sm text-muted">Carregando…</p>;
  if (previews.length === 0) {
    return (
      <div className="rounded-2xl border border-dashed border-border px-4 py-10 text-center">
        <Globe size={22} className="mx-auto mb-2 text-muted" />
        <p className="mb-1 text-sm text-ink">Nenhum app no ar.</p>
        <p className="text-xs text-muted">Peça à IA num chat do projeto para <span className="text-ink-soft">“pôr no ar pra eu testar”</span> — ela sobe o servidor e ele aparece aqui.</p>
      </div>
    );
  }
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2">
        {previews.map((p) => (
          <button key={p.id} onClick={() => setSelId(p.id)}
            className={`flex items-center gap-2 rounded-lg border px-3 py-1.5 text-xs transition-colors ${selected?.id === p.id ? "border-accent bg-accent/10 text-ink" : "border-border bg-surface2 text-ink-soft hover:text-ink"}`}>
            <PreviewStatusBadge status={p.status} />
            <span className="font-mono">:{p.port}</span>
            <span className="max-w-[160px] truncate text-muted">{p.command}</span>
            {p.expose === "lan" && <span className="rounded bg-amber-500/15 px-1 text-[10px] font-medium text-amber-400">LAN</span>}
          </button>
        ))}
      </div>

      {selected && (
        <div className="overflow-hidden rounded-xl border border-border">
          <div className="flex items-center gap-2 border-b border-border bg-surface2 px-3 py-1.5">
            <PreviewStatusBadge status={selected.status} withLabel />
            <a href={urlOf(selected)} target="_blank" rel="noreferrer"
              className="flex items-center gap-1 truncate font-mono text-xs text-ink-soft hover:text-accent-hover">
              {urlOf(selected)} <ExternalLink size={11} className="shrink-0" />
            </a>
            <div className="ml-auto flex shrink-0 items-center gap-1">
              <button onClick={() => setReloadKey((k) => k + 1)} title="Recarregar" className="rounded p-1 text-muted hover:bg-hover hover:text-ink"><RefreshCw size={13} /></button>
              <button onClick={() => setLogsOpen((v) => !v)} title="Logs do servidor" className={`rounded p-1 hover:bg-hover ${logsOpen ? "text-accent-hover" : "text-muted hover:text-ink"}`}><Terminal size={13} /></button>
              <button onClick={() => stop(selected)} title="Parar" className="rounded p-1 text-muted hover:bg-hover hover:text-red-400"><Square size={13} /></button>
            </div>
          </div>
          {selected.status === "up" ? (
            <iframe key={reloadKey} src={urlOf(selected)} title="preview" className="h-[62vh] w-full border-0 bg-white" />
          ) : (
            <div className="grid h-[62vh] place-items-center bg-bg px-8 text-center text-sm text-muted">
              {selected.status === "starting" ? (
                <span className="flex items-center gap-2"><Loader2 size={14} className="animate-spin" /> Subindo o servidor…</span>
              ) : selected.status === "crashed" ? "O servidor caiu — abra os logs para ver o erro." : "Parado."}
            </div>
          )}
          {logsOpen && (
            <pre className="max-h-48 overflow-auto whitespace-pre-wrap border-t border-border bg-bg px-3 py-2 font-mono text-[11px] leading-5 text-ink-soft">
              {logs || "(sem saída ainda)"}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}

/* ------------------------------ Project detail ----------------------------- */
type Tab = "chats" | "arquivos" | "grafo" | "preview" | "memoria" | "tarefas" | "config";

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
    <div className="mx-auto max-w-6xl px-4 py-6 md:px-8 md:py-8">
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
          <button onClick={reindex} disabled={busy || project.index_status === "cloning" || project.index_status === "indexing"}
            title="Reindexar (seguro)" className="rounded-lg p-1.5 text-muted transition-colors hover:bg-hover hover:text-ink disabled:opacity-60">
            <RefreshCw size={15} className={busy ? "animate-spin" : ""} />
          </button>
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
              ["chats", "Chats"], ["arquivos", "Arquivos"], ["grafo", "Grafo"],
              ["preview", "Preview"], ["tarefas", "Tarefas"], ["memoria", "Memória"], ["config", "Config"],
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
          {tab === "preview" && <ProjectPreviewTab project={project} />}
          {tab === "tarefas" && <ProjectTasksTab project={project} />}
          {tab === "config" && <ProjectConfigTab project={project} onUpdated={onUpdated} />}
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

/* --------------------------------- Tarefas -------------------------------- */
const TASK_STATUS: Record<string, { label: string; cls: string }> = {
  running: { label: "Trabalhando", cls: "bg-accent/15 text-accent-hover" },
  awaiting_review: { label: "A revisar", cls: "bg-amber-400/15 text-amber-400" },
  merged: { label: "Mesclada", cls: "bg-green-400/15 text-green-400" },
  discarded: { label: "Descartada", cls: "bg-surface2 text-muted" },
  error: { label: "Erro/conflito", cls: "bg-red-400/15 text-red-400" },
};

function ProjectTasksTab({ project }: { project: CodespaceProject }) {
  const [tasks, setTasks] = useState<CodespaceTask[] | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);
  const [diff, setDiff] = useState<string>("");
  const [diffLoading, setDiffLoading] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const confirm = useConfirm();
  const hasGithub = project.source === "git" && !!project.github_account_id;

  const load = () => api.get<{ tasks: CodespaceTask[] }>(`/codespace/projects/${project.id}/tasks`)
    .then((r) => setTasks(r.tasks || [])).catch(() => setTasks([]));
  useEffect(() => { load(); /* eslint-disable-next-line */ }, [project.id]);
  // enquanto houver tarefa "trabalhando", faz polling leve
  useEffect(() => {
    if (!tasks?.some((t) => t.status === "running")) return;
    const iv = setInterval(load, 3000);
    return () => clearInterval(iv);
    // eslint-disable-next-line
  }, [tasks]);

  async function openDiff(t: CodespaceTask) {
    if (openId === t.id) { setOpenId(null); return; }
    setOpenId(t.id); setDiff(""); setDiffLoading(true);
    try {
      const r = await api.get<{ diff: string }>(`/codespace/projects/${project.id}/tasks/${t.id}/diff`);
      setDiff(r.diff || "(sem mudanças)");
    } catch { setDiff("(não foi possível carregar o diff)"); }
    finally { setDiffLoading(false); }
  }

  async function merge(t: CodespaceTask, openPr: boolean) {
    const label = openPr ? "abrir um Pull Request no GitHub" : "mesclar no branch do projeto";
    if (!(await confirm({ title: `Aprovar a tarefa?`, body: `Isso vai ${label}.`, confirmLabel: "Aprovar" }))) return;
    setBusy(t.id);
    try {
      const r = await api.post<{ pr?: { html_url?: string } }>(`/codespace/projects/${project.id}/tasks/${t.id}/merge`,
        openPr ? { open_pr: true } : { push: hasGithub });
      if (openPr && r.pr?.html_url) window.open(r.pr.html_url, "_blank");
      await load(); setOpenId(null);
    } catch (e) { alert(e instanceof ApiError ? e.message : "Falha ao mesclar"); }
    finally { setBusy(null); }
  }

  async function discard(t: CodespaceTask) {
    if (!(await confirm({ title: "Descartar a tarefa?", body: "As mudanças não mescladas serão perdidas.", confirmLabel: "Descartar", danger: true }))) return;
    setBusy(t.id);
    try { await api.post(`/codespace/projects/${project.id}/tasks/${t.id}/discard`); await load(); setOpenId(null); }
    finally { setBusy(null); }
  }

  if (tasks === null) return <div className="py-10 text-center"><Loader2 size={18} className="mx-auto animate-spin text-muted" /></div>;
  if (tasks.length === 0) {
    return (
      <div className="rounded-2xl border border-dashed border-border px-4 py-10 text-center">
        <GitBranch size={22} className="mx-auto mb-2 text-muted" />
        <p className="text-sm text-muted">Nenhuma tarefa isolada ainda.</p>
        <p className="mx-auto mt-1 max-w-sm text-xs text-muted">
          Peça no chat para trabalhar numa tarefa isolada, ou use subagentes com “worktree isolado” —
          cada agente trabalha numa branch própria e o resultado aparece aqui para você revisar e mesclar.
        </p>
      </div>
    );
  }
  return (
    <div className="space-y-2">
      {tasks.map((t) => {
        const st = TASK_STATUS[t.status] ?? { label: t.status, cls: "bg-surface2 text-muted" };
        const isOpen = openId === t.id;
        const canAct = t.status === "awaiting_review" || t.status === "error" || t.status === "running";
        const ds = t.diff_stat || {};
        return (
          <div key={t.id} className="rounded-xl border border-border bg-surface">
            <div className="flex items-center gap-2 px-3 py-2.5">
              <GitBranch size={15} className="shrink-0 text-muted" />
              <button onClick={() => openDiff(t)} className="min-w-0 flex-1 text-left">
                <div className="truncate text-sm text-ink">{t.title || t.branch || "Tarefa"}</div>
                <div className="mt-0.5 flex items-center gap-2 text-[11px] text-muted">
                  <span className={`rounded-full px-1.5 py-0.5 ${st.cls}`}>{st.label}</span>
                  {t.agent && <span>{t.agent}</span>}
                  {(ds.files ?? 0) > 0 && (
                    <span>{ds.files} arq · <span className="text-green-400">+{ds.insertions ?? 0}</span> <span className="text-red-400">−{ds.deletions ?? 0}</span></span>
                  )}
                  {t.test_status === "pass" && <span className="inline-flex items-center gap-0.5 text-green-400"><CheckCircle2 size={11} /> testes ok</span>}
                  {t.test_status === "fail" && <span className="inline-flex items-center gap-0.5 text-red-400"><XCircle size={11} /> testes falharam</span>}
                </div>
              </button>
              {canAct && (
                <div className="flex shrink-0 items-center gap-1">
                  <button onClick={() => merge(t, false)} disabled={busy === t.id} title="Aprovar e mesclar"
                    className="inline-flex items-center gap-1 rounded-lg bg-accent px-2 py-1 text-xs font-medium text-white transition-colors hover:bg-accent-hover disabled:opacity-50">
                    {busy === t.id ? <Loader2 size={12} className="animate-spin" /> : <GitMerge size={12} />} Mesclar
                  </button>
                  {hasGithub && (
                    <button onClick={() => merge(t, true)} disabled={busy === t.id} title="Abrir Pull Request no GitHub"
                      className="rounded-lg border border-border px-2 py-1 text-xs text-muted transition-colors hover:text-ink">PR</button>
                  )}
                  <button onClick={() => discard(t)} disabled={busy === t.id} title="Descartar"
                    className="rounded-lg p-1 text-muted transition-colors hover:bg-hover hover:text-red-400"><Trash2 size={14} /></button>
                </div>
              )}
            </div>
            {t.error && <div className="border-t border-border px-3 py-2 text-xs text-red-400">{t.error}</div>}
            {isOpen && (
              <div className="border-t border-border">
                {diffLoading ? (
                  <div className="py-6 text-center"><Loader2 size={16} className="mx-auto animate-spin text-muted" /></div>
                ) : (
                  <pre className="max-h-96 overflow-auto p-3 font-mono text-[12px] leading-5 text-ink-soft">{diff}</pre>
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

/* -------------------------------- Config ---------------------------------- */
function ProjectConfigTab({ project, onUpdated }: { project: CodespaceProject; onUpdated: (p: CodespaceProject) => void }) {
  const [setupCmd, setSetupCmd] = useState(project.setup_command ?? "");
  const [testCmd, setTestCmd] = useState(project.test_command ?? "");
  const [execOn, setExecOn] = useState(!!project.exec_enabled);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const dirty = setupCmd !== (project.setup_command ?? "") || testCmd !== (project.test_command ?? "") || execOn !== !!project.exec_enabled;

  async function save() {
    setSaving(true); setSaved(false);
    try {
      const p = await api.patch<CodespaceProject>(`/codespace/projects/${project.id}`, {
        setup_command: setupCmd, test_command: testCmd, exec_enabled: execOn,
      });
      onUpdated(p); setSaved(true); setTimeout(() => setSaved(false), 2000);
    } finally { setSaving(false); }
  }

  return (
    <div className="space-y-5">
      <div>
        <div className="mb-3 flex items-center gap-2 text-sm font-medium text-ink"><Terminal size={15} /> Execução (sandbox)</div>
        <div className="flex items-center justify-between gap-3">
          <span className="flex items-center gap-1.5 text-sm text-ink">
            Permitir execução neste projeto
            <InfoDot text="A IA roda comandos do projeto (testes, build, lint, instalar deps) num sandbox com o projeto como diretório de trabalho, para verificar as mudanças. Desligado, a ferramenta de execução recusa." />
          </span>
          <Toggle on={execOn} onChange={setExecOn} />
        </div>
      </div>
      <div>
        <label className="mb-1 block text-xs font-medium text-muted">Comando de preparo (setup)</label>
        <input value={setupCmd} onChange={(e) => setSetupCmd(e.target.value)} placeholder="ex.: npm install"
          className="w-full rounded-lg border border-border bg-bg px-3 py-2 font-mono text-sm text-ink outline-none focus:border-accent" />
      </div>
      <div>
        <label className="mb-1 block text-xs font-medium text-muted">Comando de teste</label>
        <input value={testCmd} onChange={(e) => setTestCmd(e.target.value)} placeholder="ex.: npm test"
          className="w-full rounded-lg border border-border bg-bg px-3 py-2 font-mono text-sm text-ink outline-none focus:border-accent" />
        <p className="mt-1 text-[11px] text-muted">A IA usa este comando ao “rodar os testes” sem especificar outro.</p>
      </div>
      <button onClick={save} disabled={!dirty || saving}
        className="inline-flex items-center gap-2 rounded-full bg-accent px-4 py-1.5 text-sm font-medium text-white transition-colors hover:bg-accent-hover disabled:opacity-50">
        {saving ? <Loader2 size={14} className="animate-spin" /> : saved ? <Check size={14} /> : null}
        {saved ? "Salvo" : "Salvar"}
      </button>
    </div>
  );
}

/* --------------------------------- Painel --------------------------------- */
export default function CodespacePanel({ onOpenChat, onBack }: { onOpenChat: (chatId: string, prefill?: string) => void; onBack?: () => void }) {
  const [projects, setProjects] = useState<CodespaceProject[]>([]);
  const [accounts, setAccounts] = useState<GithubAccountLite[]>([]);
  const [loading, setLoading] = useState(true);
  const [showNew, setShowNew] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const confirm = useConfirm();
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  function exportProjects() {
    // exporta só a DEFINIÇÃO dos projetos (sem código nem chaves) — mesma ideia do
    // "Exportar" de Modelos: um JSON que descreve o que recriar.
    const data = projects.map((p) => ({
      name: p.name, source: p.source, repo_url: p.repo_url, branch: p.branch,
      default_model: p.default_model, setup_command: p.setup_command,
      test_command: p.test_command, exec_enabled: p.exec_enabled, local_path: p.local_path,
    }));
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "projetos-codespace.json";
    a.click();
    URL.revokeObjectURL(url);
  }

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

  const filtered = projects.filter((p) =>
    `${p.name} ${p.repo_url ?? ""}`.toLowerCase().includes(q.trim().toLowerCase()),
  );

  return (
    <div className="mx-auto max-w-6xl px-4 py-6 md:px-8 md:py-8">
      {/* mesma moldura de "Modelos": voltar + título/contagem + filtro + ações */}
      <div className="mb-6 flex flex-wrap items-center justify-between gap-x-3 gap-y-2.5">
        <div className="flex min-w-0 items-center gap-3">
          {onBack && (
            <button onClick={onBack} title="Espaço de Trabalho" aria-label="Voltar ao Espaço de Trabalho" className="flex h-8 w-8 flex-none items-center justify-center rounded-xl border border-transparent bg-surface text-ink-soft transition-colors hover:border-border hover:bg-surface2 hover:text-ink">
              <ChevronLeft size={18} />
            </button>
          )}
          <h1 className="truncate text-2xl font-bold text-ink">
            Codespace<span className="ml-2 font-semibold text-muted">{projects.length}</span>
          </h1>
        </div>
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <div className="relative">
            <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Filtrar projetos…"
              className="w-56 rounded-full border border-border bg-surface py-1.5 pl-9 pr-3 text-sm text-ink outline-none transition-[border-color] focus:border-accent/50 placeholder:text-muted" />
          </div>
          <button onClick={() => alert("Importar: em breve")}
            className="whitespace-nowrap rounded-full border border-border bg-surface px-4 py-1.5 text-ink-soft transition-colors hover:bg-surface2">
            <Upload size={14} className="mr-1.5 inline" />Importar
          </button>
          <button onClick={exportProjects} disabled={projects.length === 0}
            className="whitespace-nowrap rounded-full border border-border bg-surface px-4 py-1.5 text-ink-soft transition-colors hover:bg-surface2 disabled:opacity-50">
            <Download size={14} className="mr-1.5 inline" />Exportar
          </button>
          <button onClick={() => setShowNew(true)}
            className="flex items-center gap-1.5 whitespace-nowrap rounded-full bg-accent px-4 py-1.5 font-medium text-white transition-colors hover:bg-accent-hover">
            <Plus size={15} /> Novo projeto
          </button>
        </div>
      </div>

      {loading ? (
        <p className="py-10 text-center text-sm text-muted">Carregando…</p>
      ) : projects.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-border px-4 py-14 text-center">
          <Globe size={24} className="mx-auto mb-3 text-muted" />
          <p className="text-sm font-medium text-ink">Nenhum projeto ainda</p>
        </div>
      ) : filtered.length === 0 ? (
        <p className="py-10 text-center text-sm text-muted">Nenhum projeto encontrado.</p>
      ) : (
        <div className="grid grid-cols-1 gap-2.5 md:grid-cols-2">
          {filtered.map((p) => {
            const SourceIcon = SOURCE_META[p.source]?.icon ?? Globe;
            return (
              <button key={p.id} onClick={() => setOpenId(p.id)}
                className="group relative flex items-start gap-3 rounded-2xl border border-border bg-surface p-3.5 text-left transition-all duration-150 hover:border-accent/40 hover:bg-hover">
                <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-surface2 text-muted">
                  <SourceIcon size={17} />
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="truncate text-sm font-medium text-ink">{p.name}</span>
                    <StatusChip status={p.index_status} />
                  </div>
                  <p className="mt-0.5 truncate font-mono text-[11px] text-muted">
                    {p.source === "local" ? "local · sem remoto" : `${p.repo_url} · ${p.branch}`}
                  </p>
                  {p.index_status === "ready" && (fmtStats(p) || p.stats?.refined) && (
                    <p className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 font-mono text-[11px] tabular-nums text-muted">
                      {fmtStats(p) && <span>{fmtStats(p)}</span>}
                      {p.stats?.refined && <span className="inline-flex items-center gap-1 text-accent-hover"><Sparkles size={11} /> refinado</span>}
                    </p>
                  )}
                  {p.index_status === "error" && p.error_message && (
                    <p className="mt-1 truncate text-[11px] text-red-400">{p.error_message}</p>
                  )}
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
