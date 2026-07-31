"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Check,
  Code2,
  Copy,
  Download,
  Eye,
  FolderOpen,
  History,
  Link2,
  Loader2,
  Maximize2,
  Minimize2,
  Pencil,
  Search,
  Trash2,
  X,
} from "lucide-react";
import { api, API_URL, ApiError } from "@/lib/api";
import { copyText } from "@/lib/clipboard";
import type { ChatArtifact, ChatArtifactVersion } from "@/lib/types";
import Markdown from "./Markdown";
import CodeEditor from "./CodeEditor";
import { useClickOutside } from "./ui";

/** Artefato ainda em streaming (a resposta não terminou): mostrado ao vivo. */
export interface LiveArtifact {
  identifier: string;
  title: string;
  kind: string;
  content: string;
}

const EXT: Record<string, string> = {
  markdown: "md", html: "html", svg: "svg", mermaid: "mmd", json: "json", csv: "csv", text: "txt",
};
const CODE_EXT: Record<string, string> = {
  python: "py", javascript: "js", typescript: "ts", tsx: "tsx", jsx: "jsx", java: "java",
  csharp: "cs", cpp: "cpp", c: "c", go: "go", rust: "rs", ruby: "rb", php: "php", sql: "sql",
  bash: "sh", shell: "sh", html: "html", css: "css", kotlin: "kt", swift: "swift",
};

function fileName(a: { title: string; kind: string; language: string }) {
  const base = (a.title || "artefato").toLowerCase().replace(/[^\w\d-]+/g, "-").replace(/^-+|-+$/g, "") || "artefato";
  const ext = a.kind === "code" ? (CODE_EXT[(a.language || "").toLowerCase()] ?? "txt") : (EXT[a.kind] ?? "txt");
  return `${base}.${ext}`;
}

// kinds com pré-visualização própria (os demais só têm a visão de código)
const PREVIEWABLE = new Set(["markdown", "html", "svg", "mermaid", "csv"]);

/** Linha leve do explorador (GET /artifacts — sem conteúdo). */
interface ExplorerItem {
  id: string;
  chat_id: string;
  chat_title: string;
  identifier: string;
  title: string;
  kind: string;
  language: string;
  version: number;
  updated_at: string | null;
}

/* ------------------------------------------------------------------------- */
/* Pré-visualizações                                                          */
/* ------------------------------------------------------------------------- */
function HtmlPreview({ content, kind }: { content: string; kind: string }) {
  const doc = kind === "svg"
    ? `<!doctype html><body style="margin:0;display:grid;place-items:center;min-height:100vh;background:#fff">${content}</body>`
    : content;
  return (
    <iframe
      sandbox="allow-scripts"
      srcDoc={doc}
      className="h-full w-full rounded-xl border border-border bg-white"
      title="Pré-visualização"
    />
  );
}

function MermaidPreview({ content }: { content: string }) {
  const [svg, setSvg] = useState("");
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const mermaid = (await import("mermaid")).default;
        mermaid.initialize({ startOnLoad: false, theme: "dark", securityLevel: "strict" });
        const { svg } = await mermaid.render(`mmd-${Date.now()}`, content);
        if (alive) { setSvg(svg); setErr(null); }
      } catch (e) {
        if (alive) setErr(e instanceof Error ? e.message : "Diagrama inválido");
      }
    })();
    return () => { alive = false; };
  }, [content]);
  if (err) return <p className="p-4 text-xs text-red-400">Falha ao renderizar o diagrama: {err}</p>;
  if (!svg) return <div className="flex h-full items-center justify-center"><Loader2 size={18} className="animate-spin text-muted" /></div>;
  return <div className="h-full overflow-auto p-4 [&_svg]:mx-auto [&_svg]:h-auto [&_svg]:max-w-full" dangerouslySetInnerHTML={{ __html: svg }} />;
}

function CsvPreview({ content }: { content: string }) {
  const rows = useMemo(() => {
    // parser simples com suporte a aspas ("a,b",c) — suficiente p/ pré-visualizar
    const out: string[][] = [];
    for (const line of content.split(/\r?\n/)) {
      if (!line.trim()) continue;
      const cells: string[] = [];
      let cur = "", quoted = false;
      for (let i = 0; i < line.length; i++) {
        const ch = line[i];
        if (quoted) {
          if (ch === '"' && line[i + 1] === '"') { cur += '"'; i++; }
          else if (ch === '"') quoted = false;
          else cur += ch;
        } else if (ch === '"') quoted = true;
        else if (ch === ",") { cells.push(cur); cur = ""; }
        else cur += ch;
      }
      cells.push(cur);
      out.push(cells);
      if (out.length > 500) break; // pré-visualização, não planilha
    }
    return out;
  }, [content]);
  if (!rows.length) return <p className="p-4 text-xs text-muted">CSV vazio.</p>;
  return (
    <div className="h-full overflow-auto p-3">
      <table className="w-full border-collapse text-xs">
        <thead>
          <tr>
            {rows[0].map((h, i) => (
              <th key={i} className="sticky top-0 border-b border-border bg-surface px-2 py-1.5 text-left font-semibold text-ink">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.slice(1).map((r, i) => (
            <tr key={i} className="odd:bg-surface/40">
              {r.map((c, j) => <td key={j} className="border-b border-border/50 px-2 py-1 text-ink-soft">{c}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Preview({ kind, content }: { kind: string; content: string }) {
  if (kind === "markdown") return <div className="h-full overflow-y-auto px-4 py-3"><Markdown content={content} /></div>;
  if (kind === "html" || kind === "svg") return <HtmlPreview content={content} kind={kind} />;
  if (kind === "mermaid") return <MermaidPreview content={content} />;
  if (kind === "csv") return <CsvPreview content={content} />;
  return null;
}

/* ------------------------------------------------------------------------- */
/* Painel                                                                     */
/* ------------------------------------------------------------------------- */
export default function ArtifactPanel({
  artifacts,
  openIdentifier,
  live,
  onSelect,
  onClose,
  onChanged,
}: {
  artifacts: ChatArtifact[];
  openIdentifier: string | null;
  /** artefato chegando em streaming — tem prioridade de exibição */
  live: LiveArtifact | null;
  onSelect: (identifier: string) => void;
  onClose: () => void;
  /** recarrega a lista após editar/restaurar/duplicar/excluir */
  onChanged: () => Promise<void>;
}) {
  // artefato de OUTRO chat aberto via explorador (buscado por id, com conteúdo)
  const [external, setExternal] = useState<ChatArtifact | null>(null);
  const current = external ?? artifacts.find((a) => a.identifier === openIdentifier) ?? artifacts[0] ?? null;
  const showingLive = live != null;

  // ---------------- explorador (substitui as abas) ----------------
  const [explorerOpen, setExplorerOpen] = useState(false);
  const [explorerScope, setExplorerScope] = useState<"chat" | "all">("chat");
  const [explorerQ, setExplorerQ] = useState("");
  const [allItems, setAllItems] = useState<ExplorerItem[] | null>(null);
  const explorerRef = useClickOutside<HTMLDivElement>(() => setExplorerOpen(false));

  const loadAll = useCallback(async () => {
    try { setAllItems(await api.get<ExplorerItem[]>("/artifacts")); } catch { setAllItems([]); }
  }, []);

  async function pickFromExplorer(item: ExplorerItem) {
    setExplorerOpen(false);
    const local = artifacts.find((a) => a.id === item.id);
    if (local) {
      setExternal(null);
      onSelect(local.identifier);
      return;
    }
    try {
      setExternal(await api.get<ChatArtifact>(`/artifacts/${item.id}`));
    } catch { /* some artefato apagado entre a listagem e o clique */ }
  }
  const kind = showingLive ? live.kind : current?.kind ?? "text";
  const title = showingLive ? live.title : current?.title ?? "";
  const content = showingLive ? live.content : current?.content ?? "";

  const [view, setView] = useState<"preview" | "code">("preview");
  const [full, setFull] = useState(false);
  const [draft, setDraft] = useState<string | null>(null); // edição pendente (code view)
  const [saving, setSaving] = useState(false);
  const [copied, setCopied] = useState(false);
  const [shared, setShared] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [nameDraft, setNameDraft] = useState("");
  const [versionsOpen, setVersionsOpen] = useState(false);
  const [versions, setVersions] = useState<ChatArtifactVersion[]>([]);
  const [viewingVersion, setViewingVersion] = useState<{ version: number; content: string } | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const versionsRef = useClickOutside<HTMLDivElement>(() => setVersionsOpen(false));

  const canPreview = PREVIEWABLE.has(kind);
  // artefato trocou → descarta edição pendente e volta à visão padrão
  const key = showingLive ? `live:${live.identifier}` : current?.id ?? "none";
  const prevKey = useRef(key);
  useEffect(() => {
    if (prevKey.current !== key) {
      prevKey.current = key;
      setDraft(null);
      setViewingVersion(null);
      setErr(null);
      setView(PREVIEWABLE.has(kind) ? "preview" : "code");
    }
  }, [key, kind]);
  // primeiro mount também respeita o kind
  useEffect(() => { setView(canPreview ? "preview" : "code"); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, []);

  const dirty = draft != null && current != null && draft !== current.content;

  async function save() {
    if (!current || draft == null) return;
    setSaving(true);
    setErr(null);
    try {
      const updated = await api.patch<ChatArtifact>(`/artifacts/${current.id}`, { content: draft });
      if (external) setExternal(updated);
      else await onChanged();
      setDraft(null);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Falha ao salvar");
    } finally {
      setSaving(false);
    }
  }

  async function copy() {
    try {
      await copyText(viewingVersion?.content ?? draft ?? content);
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    } catch { /* ignore */ }
  }

  function download() {
    const blob = new Blob([viewingVersion?.content ?? draft ?? content], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = fileName({ title, kind, language: current?.language ?? "" });
    a.click();
    URL.revokeObjectURL(url);
  }

  async function share() {
    if (!current) return;
    try {
      const r = await api.post<{ path: string }>(`/artifacts/${current.id}/share`);
      await copyText(`${API_URL}${r.path}`);
      setShared(true);
      setTimeout(() => setShared(false), 1600);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Falha ao compartilhar");
    }
  }

  async function remove() {
    if (!current) return;
    try {
      await api.del(`/artifacts/${current.id}`);
      if (external) {
        setExternal(null);
        if (artifacts.length === 0) onClose();
      } else {
        await onChanged();
        if (artifacts.length <= 1) onClose();
      }
      setAllItems(null); // lista do explorador ficou stale
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Falha ao excluir");
    }
  }

  async function rename() {
    if (!current || !nameDraft.trim()) { setRenaming(false); return; }
    try {
      const updated = await api.patch<ChatArtifact>(`/artifacts/${current.id}`, { title: nameDraft.trim() });
      if (external) setExternal(updated);
      else await onChanged();
    } catch { /* mantém o título anterior */ }
    setRenaming(false);
  }

  const loadVersions = useCallback(async () => {
    if (!current) return;
    try {
      setVersions(await api.get<ChatArtifactVersion[]>(`/artifacts/${current.id}/versions`));
    } catch { setVersions([]); }
  }, [current]);

  async function viewVersion(v: number) {
    if (!current) return;
    setVersionsOpen(false);
    if (v === current.version) { setViewingVersion(null); return; }
    try {
      const r = await api.get<{ version: number; content: string }>(`/artifacts/${current.id}/versions/${v}`);
      setViewingVersion(r);
    } catch { /* ignore */ }
  }

  async function restore() {
    if (!current || !viewingVersion) return;
    try {
      await api.post(`/artifacts/${current.id}/restore`, { version: viewingVersion.version });
      if (external) setExternal(await api.get<ChatArtifact>(`/artifacts/${current.id}`));
      else await onChanged();
      setViewingVersion(null);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Falha ao restaurar");
    }
  }

  if (!current && !showingLive) return null;

  const btn = "rounded-lg p-1.5 text-muted transition-colors hover:bg-hover hover:text-ink disabled:opacity-40";
  const shown = viewingVersion?.content ?? (view === "code" ? (draft ?? content) : content);

  const body = (
    <div className={`pt-safe pb-safe flex min-w-0 flex-col border-border bg-bg ${full ? "fixed inset-0 z-[80]" : "h-full w-full border-l"}`}>
      {/* cabeçalho */}
      <div className="flex items-center gap-1.5 border-b border-border px-3 py-2">
        {/* explorador de artefatos (substitui as abas — escala p/ N artefatos) */}
        {!showingLive && (
          <div className="relative" ref={explorerRef}>
            <button
              onClick={() => { setExplorerOpen((v) => !v); if (!explorerOpen && allItems === null) loadAll(); }}
              title="Explorador de artefatos"
              className={`rounded-lg p-1.5 transition-colors ${explorerOpen || external ? "bg-accent/15 text-accent-hover" : "text-muted hover:bg-hover hover:text-ink"}`}
            >
              <FolderOpen size={15} />
            </button>
            {explorerOpen && (
              <div className="animate-pop absolute left-0 top-9 z-30 w-72 rounded-xl border border-border bg-surface shadow-menu">
                <div className="flex items-center gap-1 border-b border-border p-1.5">
                  {(["chat", "all"] as const).map((s) => (
                    <button
                      key={s}
                      onClick={() => { setExplorerScope(s); if (s === "all" && allItems === null) loadAll(); }}
                      className={`rounded-lg px-2.5 py-1 text-xs transition-colors ${explorerScope === s ? "bg-surface2 font-medium text-ink" : "text-muted hover:text-ink"}`}
                    >
                      {s === "chat" ? "Este chat" : "Todos"}
                    </button>
                  ))}
                  <div className="ml-auto flex items-center gap-1 pr-1">
                    <Search size={12} className="text-muted" />
                    <input
                      value={explorerQ}
                      onChange={(e) => setExplorerQ(e.target.value)}
                      placeholder="Filtrar…"
                      className="w-24 bg-transparent text-xs text-ink outline-none placeholder:text-muted"
                    />
                  </div>
                </div>
                <div className="max-h-72 overflow-y-auto p-1.5">
                  {(() => {
                    const base: ExplorerItem[] = explorerScope === "chat"
                      ? artifacts.map((a) => ({ id: a.id, chat_id: a.chat_id, chat_title: "", identifier: a.identifier, title: a.title, kind: a.kind, language: a.language, version: a.version, updated_at: a.updated_at ?? null }))
                      : (allItems ?? []);
                    const q = explorerQ.trim().toLowerCase();
                    const rows = q ? base.filter((r) => `${r.title} ${r.chat_title}`.toLowerCase().includes(q)) : base;
                    if (explorerScope === "all" && allItems === null) return <p className="px-2 py-4 text-center text-xs text-muted">Carregando…</p>;
                    if (rows.length === 0) return <p className="px-2 py-4 text-center text-xs text-muted">Nenhum artefato.</p>;
                    return rows.map((r) => (
                      <button
                        key={r.id}
                        onClick={() => pickFromExplorer(r)}
                        className={`flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left transition-colors hover:bg-hover ${current?.id === r.id ? "bg-accent/10" : ""}`}
                      >
                        <Code2 size={13} className="shrink-0 text-muted" />
                        <span className="min-w-0 flex-1">
                          <span className="block truncate text-xs text-ink">{r.title || r.identifier}</span>
                          {explorerScope === "all" && (
                            <span className="block truncate text-[10px] text-muted">{r.chat_title || "sem título"}</span>
                          )}
                        </span>
                        <span className="shrink-0 text-[10px] text-muted">v{r.version}</span>
                        {current?.id === r.id && <Check size={12} className="shrink-0 text-accent-hover" />}
                      </button>
                    ));
                  })()}
                </div>
              </div>
            )}
          </div>
        )}
        {renaming ? (
          <input
            autoFocus
            value={nameDraft}
            onChange={(e) => setNameDraft(e.target.value)}
            onBlur={rename}
            onKeyDown={(e) => { if (e.key === "Enter") rename(); if (e.key === "Escape") setRenaming(false); }}
            className="min-w-0 flex-1 rounded-lg border border-border bg-surface2 px-2 py-1 text-sm text-ink outline-none focus:border-accent"
          />
        ) : (
          <button
            onClick={() => { if (current) { setNameDraft(current.title); setRenaming(true); } }}
            title={current ? "Renomear" : undefined}
            className="group flex min-w-0 flex-1 items-center gap-1.5 text-left"
          >
            <p className="truncate text-sm font-semibold text-ink">{title || "Artefato"}</p>
            {showingLive ? (
              <span className="flex items-center gap-1 text-[11px] text-accent-hover"><Loader2 size={11} className="animate-spin" /> gerando…</span>
            ) : (
              <>
                <span className="shrink-0 text-[11px] text-muted">v{current!.version}</span>
                {external && (
                  <span className="shrink-0 rounded-full bg-surface2 px-1.5 py-0.5 text-[10px] text-muted">outro chat</span>
                )}
                <Pencil size={11} className="shrink-0 text-muted opacity-0 transition-opacity group-hover:opacity-100" />
              </>
            )}
          </button>
        )}

        {canPreview && (
          <div className="flex overflow-hidden rounded-lg border border-border">
            <button onClick={() => setView("preview")} title="Pré-visualizar"
              className={`px-2 py-1 transition-colors ${view === "preview" ? "bg-accent/15 text-accent-hover" : "text-muted hover:text-ink"}`}>
              <Eye size={13} />
            </button>
            <button onClick={() => setView("code")} title="Código"
              className={`px-2 py-1 transition-colors ${view === "code" ? "bg-accent/15 text-accent-hover" : "text-muted hover:text-ink"}`}>
              <Code2 size={13} />
            </button>
          </div>
        )}

        <button onClick={copy} title="Copiar conteúdo" className={btn}>
          {copied ? <Check size={14} className="text-green-400" /> : <Copy size={14} />}
        </button>
        <button onClick={download} title="Baixar arquivo" className={btn}><Download size={14} /></button>
        {!showingLive && (
          <>
            <button onClick={share} title="Compartilhar (copia um link público de 30 dias)" className={btn}>
              {shared ? <Check size={14} className="text-green-400" /> : <Link2 size={14} />}
            </button>
            <div className="relative" ref={versionsRef}>
              <button
                onClick={() => { setVersionsOpen((v) => !v); if (!versionsOpen) loadVersions(); }}
                title="Histórico de versões"
                className={`${btn} ${viewingVersion ? "text-accent-hover" : ""}`}
              >
                <History size={14} />
              </button>
              {versionsOpen && (
                <div className="animate-pop absolute right-0 top-9 z-30 max-h-64 w-56 overflow-y-auto rounded-xl border border-border bg-surface p-1.5 shadow-menu">
                  {versions.length === 0 && <p className="px-2 py-1.5 text-xs text-muted">Sem histórico.</p>}
                  {versions.map((v) => (
                    <button
                      key={v.version}
                      onClick={() => viewVersion(v.version)}
                      className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-xs text-ink transition-colors hover:bg-hover"
                    >
                      <span className="font-medium">v{v.version}</span>
                      <span className="text-muted">{v.label === "ai" ? "IA" : v.label === "user" ? "você" : "restauração"}</span>
                      <span className="ml-auto text-[10px] text-muted">
                        {v.created_at ? new Date(v.created_at).toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : ""}
                      </span>
                      {current && v.version === current.version && <Check size={12} className="text-accent-hover" />}
                    </button>
                  ))}
                </div>
              )}
            </div>
            <button onClick={remove} title="Excluir artefato" className={`${btn} hover:text-red-400`}><Trash2 size={14} /></button>
          </>
        )}
        <button onClick={() => setFull((v) => !v)} title={full ? "Reduzir" : "Expandir"} className={btn}>
          {full ? <Minimize2 size={14} /> : <Maximize2 size={14} />}
        </button>
        <button onClick={() => { setFull(false); setExternal(null); onClose(); }} title="Fechar" className={btn}><X size={15} /></button>
      </div>

      {/* aviso de versão antiga */}
      {viewingVersion && (
        <div className="flex items-center gap-2 border-b border-border bg-accent/10 px-3 py-1.5 text-xs text-accent-hover">
          Vendo a versão v{viewingVersion.version} (somente leitura)
          <button onClick={restore} className="rounded-full bg-accent px-2.5 py-0.5 font-medium text-white hover:bg-accent-hover">Restaurar esta versão</button>
          <button onClick={() => setViewingVersion(null)} className="text-muted hover:text-ink">Voltar à atual</button>
        </div>
      )}
      {err && <p className="border-b border-border px-3 py-1.5 text-xs text-red-400">{err}</p>}

      {/* corpo */}
      <div className="min-h-0 flex-1 overflow-hidden p-2">
        {view === "preview" && canPreview && !viewingVersion ? (
          <Preview kind={kind} content={content} />
        ) : showingLive || viewingVersion ? (
          <pre className="h-full overflow-auto rounded-xl border border-border bg-surface p-3 font-mono text-[12.5px] leading-5 text-ink-soft">
            {shown}
          </pre>
        ) : (
          <CodeEditor
            value={draft ?? content}
            onChange={(v) => setDraft(v)}
            language={kind === "code" ? current?.language || "python" : kind}
          />
        )}
      </div>

      {/* rodapé de edição */}
      {dirty && !viewingVersion && (
        <div className="flex items-center justify-end gap-2 border-t border-border px-3 py-2">
          <button onClick={() => setDraft(null)} className="rounded-full border border-border px-3 py-1 text-xs text-muted hover:text-ink">Descartar</button>
          <button onClick={save} disabled={saving} className="rounded-full bg-accent px-4 py-1 text-xs font-medium text-white hover:bg-accent-hover disabled:opacity-60">
            {saving ? "…" : "Salvar (nova versão)"}
          </button>
        </div>
      )}
    </div>
  );

  return body;
}
