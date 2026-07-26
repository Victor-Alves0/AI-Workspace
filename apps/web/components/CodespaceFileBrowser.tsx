"use client";

import { forwardRef, useEffect, useMemo, useRef, useState } from "react";
import {
  ChevronRight, File as FileIcon, Folder, Home, Loader2, MoreVertical, Pencil, Save, Search,
  Share2, X,
} from "lucide-react";
import { api, ApiError } from "@/lib/api";
import type { CodespaceFileEntry } from "@/lib/types";
import { usePrompt } from "@/components/ConfirmDialog";
import { AnchoredMenu, MenuItem } from "@/components/ui";

/** remove o prefixo "N\t" (número de linha) que /files/content devolve */
export function stripLineNumbers(content: string): string {
  return content.split("\n").map((l) => l.replace(/^\d+\t/, "")).join("\n");
}

export function extLang(path: string): string {
  const m = /\.([a-z0-9]+)$/i.exec(path);
  return m ? m[1].toLowerCase() : "";
}

const DND_MIME = "application/x-codespace-file";
type DragPayload = { projectId: string; path: string; kind: "dir" | "file" };

/** Trecho de código arrastado do visualizador: leva o ARQUIVO e o INTERVALO de
 *  linhas junto do texto, para a IA saber exatamente onde aquilo mora. */
const SNIPPET_MIME = "application/x-codespace-snippet";
type SnippetPayload = {
  projectId: string; path: string; startLine: number; endLine: number; text: string;
};

/** Intervalo de linhas da seleção atual dentro do visualizador (pelo `data-line`
 *  de cada linha renderizada). null = nada selecionado ali dentro.
 *
 *  O texto é remontado a partir do DOM (linhas INTEIRAS do intervalo), não de
 *  `Selection.toString()`: com a calha `select-none` no meio, o toString() pode vir
 *  vazio (medido) — e linhas inteiras são o que a IA precisa de qualquer forma,
 *  porque um recorte no meio da linha não é código válido. */
function selectionLines(root: HTMLElement | null): { start: number; end: number; text: string } | null {
  if (!root || typeof window === "undefined") return null;
  const sel = window.getSelection();
  if (!sel || sel.rangeCount === 0) return null;
  const range = sel.getRangeAt(0);
  if (range.collapsed || !root.contains(range.commonAncestorContainer)) return null;
  const lineOf = (node: Node | null): number | null => {
    let el: HTMLElement | null =
      node instanceof HTMLElement ? node : (node?.parentElement ?? null);
    while (el && !el.dataset.line) el = el.parentElement;
    return el ? Number(el.dataset.line) : null;
  };
  const a = lineOf(range.startContainer);
  const b = lineOf(range.endContainer);
  if (a == null || b == null) return null;
  const start = Math.min(a, b);
  const end = Math.max(a, b);
  const linhas: string[] = [];
  for (let n = start; n <= end; n++) {
    const row = root.querySelector<HTMLElement>(`[data-line="${n}"]`);
    // último filho da linha = o código (o primeiro é a calha do número)
    linhas.push(row?.lastElementChild?.textContent ?? "");
  }
  const text = linhas.join("\n");
  if (!text.trim()) return null;
  return { start, end, text };
}

// acima disto o visualizador volta ao <pre> simples: numerar linha a linha custa
// um nó de DOM por linha e trava a rolagem em arquivos enormes.
const MAX_NUMBERED_LINES = 4000;

function basename(p: string): string {
  return p.split("/").pop() || p;
}
function dirname(p: string): string {
  return p.includes("/") ? p.slice(0, p.lastIndexOf("/")) : "";
}
function joinPath(dir: string, name: string): string {
  return dir ? `${dir}/${name}` : name;
}

/** uma entrada (arquivo/pasta) do explorador — arrastável (mover) e, se pasta,
 *  também alvo de drop; menu "⋮" com Renomear. */
function EntryRow({
  entry, projectId, selected, onOpen, onDropInto, onChanged, fullPath = false,
}: {
  entry: CodespaceFileEntry;
  projectId: string;
  selected: boolean;
  onOpen: () => void;
  onDropInto?: (srcPath: string) => void;
  onChanged: () => void;
  /** busca por nome: mostra o caminho inteiro em vez de só o nome do arquivo */
  fullPath?: boolean;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const btnRef = useRef<HTMLButtonElement>(null);
  const prompt = usePrompt();
  const isDir = entry.kind === "dir";

  function onDragStart(e: React.DragEvent) {
    const payload: DragPayload = { projectId, path: entry.path, kind: entry.kind };
    e.dataTransfer.setData(DND_MIME, JSON.stringify(payload));
    e.dataTransfer.effectAllowed = "move";
  }

  async function rename() {
    setMenuOpen(false);
    const base = basename(entry.path);
    const v = await prompt({ title: "Renomear", defaultValue: base });
    if (!v || !v.trim() || v.trim() === base) return;
    const dest = joinPath(dirname(entry.path), v.trim());
    try {
      await api.patch(`/codespace/projects/${projectId}/files/move`, { path: entry.path, dest_path: dest });
      onChanged();
    } catch (e) {
      alert(e instanceof ApiError ? e.message : "Falha ao renomear");
    }
  }

  return (
    <div
      draggable
      onDragStart={onDragStart}
      onDragOver={isDir ? (e) => { e.preventDefault(); setDragOver(true); } : undefined}
      onDragLeave={isDir ? () => setDragOver(false) : undefined}
      onDrop={isDir ? (e) => {
        e.preventDefault();
        setDragOver(false);
        const raw = e.dataTransfer.getData(DND_MIME);
        if (!raw) return;
        try {
          const payload: DragPayload = JSON.parse(raw);
          if (payload.projectId === projectId && payload.path !== entry.path) onDropInto?.(payload.path);
        } catch { /* payload de outra origem — ignora */ }
      } : undefined}
      className={`group flex w-full items-center gap-1 rounded-lg px-2 py-1.5 text-xs transition-colors hover:bg-hover ${selected ? "bg-hover text-ink" : "text-ink-soft"} ${dragOver ? "bg-accent/15 ring-1 ring-inset ring-accent/40" : ""}`}
    >
      <button onClick={onOpen} className="flex min-w-0 flex-1 items-center gap-1.5 text-left">
        {isDir ? <Folder size={13} className="shrink-0 text-accent-hover" /> : <FileIcon size={13} className="shrink-0 text-muted" />}
        <span className="truncate" title={entry.path}>{fullPath ? entry.path : basename(entry.path)}</span>
      </button>
      <button
        ref={btnRef} onClick={() => setMenuOpen(true)}
        className={`shrink-0 rounded p-0.5 text-muted hover:bg-hover hover:text-ink ${menuOpen ? "opacity-100" : "opacity-0 group-hover:opacity-100"}`}
      >
        <MoreVertical size={12} />
      </button>
      {menuOpen && (
        <AnchoredMenu anchorRef={btnRef} onClose={() => setMenuOpen(false)}>
          <MenuItem icon={<Pencil size={13} />} onClick={rename}>Renomear</MenuItem>
        </AnchoredMenu>
      )}
    </div>
  );
}

/** Explorador + editor de arquivos de um projeto do Codespace — usado tanto na
 *  aba "Arquivos" do Espaço de Trabalho quanto na barra lateral dentro do chat.
 *  `onUse` (opcional) mostra um botão de ação sobre o conteúdo aberto (referenciar
 *  no chat / inserir no composer — o rótulo e o efeito ficam a cargo do chamador).
 *  Cada entrada é arrastável: solta numa pasta pra mover, ou fora do explorador
 *  (ex.: sobre o campo de mensagem do chat) pra referenciar o arquivo. */
export default function CodespaceFileBrowser({
  projectId, onUse, useLabel = "Referenciar no chat", dense = false, initialPath,
}: {
  projectId: string;
  onUse?: (path: string, content: string) => void;
  useLabel?: string;
  dense?: boolean;
  /** abre direto este arquivo ao montar (ex.: veio de um clique no grafo) */
  initialPath?: string;
}) {
  const [path, setPath] = useState(() => (initialPath ? initialPath.split("/").slice(0, -1).join("/") : ""));
  const [entries, setEntries] = useState<CodespaceFileEntry[] | null>(null);
  const [listError, setListError] = useState("");
  const [query, setQuery] = useState("");
  const [searchResults, setSearchResults] = useState<CodespaceFileEntry[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  // raiz do visualizador: o arraste de trecho lê a seleção de dentro dela
  const viewRef = useRef<HTMLDivElement>(null);
  const [content, setContent] = useState<string>("");
  const [contentMeta, setContentMeta] = useState<{ total_lines?: number; truncated?: boolean } | null>(null);
  const [loadingContent, setLoadingContent] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");

  const [searchTick, setSearchTick] = useState(0);

  function loadEntries() {
    setEntries(null);
    setListError("");
    api.get<{ entries?: CodespaceFileEntry[]; error?: string }>(
      `/codespace/projects/${projectId}/files?path=${encodeURIComponent(path)}&depth=1`,
    ).then((r) => {
      if (r.error) setListError(r.error);
      else setEntries((r.entries ?? []).sort((a, b) => (a.kind === b.kind ? a.path.localeCompare(b.path) : a.kind === "dir" ? -1 : 1)));
    }).catch(() => setListError("falha ao listar"));
  }

  /** recarrega a visão ATUAL após uma mudança (mover/renomear) — a listagem por
   *  diretório e, se houver uma busca ativa, os resultados dela também (senão o
   *  nome antigo continuaria na lista de resultados). */
  function refresh() {
    loadEntries();
    setSearchTick((t) => t + 1);
  }

  useEffect(loadEntries, [projectId, path]);

  useEffect(() => {
    if (initialPath) openFile(initialPath);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialPath]);

  // busca por nome (todo o projeto) — debounced; some ao esvaziar o campo.
  // `searchTick` força re-busca após mover/renomear (ver `refresh`).
  useEffect(() => {
    const q = query.trim();
    if (!q) { setSearchResults(null); setSearching(false); return; }
    setSearching(true);
    const t = setTimeout(() => {
      api.get<{ entries?: CodespaceFileEntry[]; error?: string }>(
        `/codespace/projects/${projectId}/files/search?q=${encodeURIComponent(q)}`,
      ).then((r) => setSearchResults(r.entries ?? [])).catch(() => setSearchResults([])).finally(() => setSearching(false));
    }, 300);
    return () => clearTimeout(t);
  }, [query, projectId, searchTick]);

  async function openFile(p: string) {
    setSelected(p);
    setEditing(false);
    setSaveError("");
    setLoadingContent(true);
    setContent("");
    try {
      const r = await api.get<{ content?: string; total_lines?: number; end_line?: number; error?: string }>(
        `/codespace/projects/${projectId}/files/content?path=${encodeURIComponent(p)}`,
      );
      if (r.error) { setContent(`(${r.error})`); setContentMeta(null); }
      else {
        const clean = stripLineNumbers(r.content ?? "");
        setContent(clean);
        setContentMeta({ total_lines: r.total_lines, truncated: !!r.total_lines && !!r.end_line && r.end_line < r.total_lines });
      }
    } catch {
      setContent("(falha ao ler o arquivo)");
      setContentMeta(null);
    } finally {
      setLoadingContent(false);
    }
  }

  async function moveInto(srcPath: string, destDir: string) {
    const dest = joinPath(destDir, basename(srcPath));
    if (dest === srcPath) return;
    try {
      await api.patch(`/codespace/projects/${projectId}/files/move`, { path: srcPath, dest_path: dest });
      refresh();
      if (selected === srcPath) setSelected(dest);
    } catch (e) {
      alert(e instanceof ApiError ? e.message : "Falha ao mover");
    }
  }

  function onDropOnPath(e: React.DragEvent, destDir: string) {
    e.preventDefault();
    const raw = e.dataTransfer.getData(DND_MIME);
    if (!raw) return;
    try {
      const payload: DragPayload = JSON.parse(raw);
      if (payload.projectId === projectId) moveInto(payload.path, destDir);
    } catch { /* payload de outra origem — ignora */ }
  }

  function startEdit() {
    setDraft(content);
    setEditing(true);
    setSaveError("");
  }

  async function save() {
    if (!selected) return;
    setSaving(true);
    setSaveError("");
    try {
      await api.put(`/codespace/projects/${projectId}/files/content`, { path: selected, content: draft });
      setContent(draft);
      setContentMeta((m) => (m ? { ...m, truncated: false } : m));
      setEditing(false);
    } catch (e) {
      setSaveError(e instanceof ApiError ? e.message : "Falha ao salvar");
    } finally {
      setSaving(false);
    }
  }

  const crumbs = useMemo(() => {
    const parts = path ? path.split("/") : [];
    return parts.map((_, i) => ({ label: parts[i], path: parts.slice(0, i + 1).join("/") }));
  }, [path]);

  const showingSearch = query.trim().length > 0;

  return (
    <div className={`flex flex-col gap-3 ${dense ? "h-full" : "sm:flex-row sm:h-[56vh] sm:min-h-[320px]"}`}>
      <div className={`flex w-full flex-col rounded-xl border border-border bg-surface ${dense ? "max-h-[55%] shrink-0" : "sm:w-64 sm:shrink-0"}`}>
        <div className="flex items-center gap-1.5 border-b border-border px-2 py-1.5">
          <Search size={12} className="shrink-0 text-muted" />
          <input
            value={query} onChange={(e) => setQuery(e.target.value)}
            placeholder="Buscar arquivo…"
            className="min-w-0 flex-1 bg-transparent py-0.5 text-xs text-ink outline-none placeholder:text-muted"
          />
          {searching && <Loader2 size={11} className="shrink-0 animate-spin text-muted" />}
          {!searching && query && (
            <button onClick={() => setQuery("")} className="shrink-0 rounded p-0.5 text-muted hover:bg-hover hover:text-ink"><X size={11} /></button>
          )}
        </div>
        {!showingSearch && (
          <div
            onDragOver={(e) => e.preventDefault()} onDrop={(e) => onDropOnPath(e, "")}
            className="flex flex-wrap items-center gap-1 border-b border-border px-2.5 py-2 text-xs text-muted"
          >
            <button onClick={() => setPath("")} className="rounded p-0.5 hover:bg-hover hover:text-ink" title="Raiz"><Home size={13} /></button>
            {crumbs.map((c) => (
              <span key={c.path} className="flex items-center gap-1" onDragOver={(e) => e.preventDefault()} onDrop={(e) => { e.stopPropagation(); onDropOnPath(e, c.path); }}>
                <ChevronRight size={11} />
                <button onClick={() => setPath(c.path)} className="max-w-[90px] truncate rounded px-1 hover:bg-hover hover:text-ink">{c.label}</button>
              </span>
            ))}
          </div>
        )}
        <div className="flex-1 overflow-y-auto p-1.5">
          {showingSearch ? (
            searchResults === null ? (
              <p className="px-2 py-4 text-xs text-muted">Buscando…</p>
            ) : searchResults.length === 0 ? (
              <p className="px-2 py-4 text-xs text-muted">Nada encontrado.</p>
            ) : (
              searchResults.map((e) => (
                <EntryRow
                  key={e.path} entry={e} projectId={projectId} selected={selected === e.path}
                  onOpen={() => openFile(e.path)} onChanged={refresh} fullPath
                />
              ))
            )
          ) : listError ? (
            <p className="px-2 py-4 text-xs text-red-400">{listError}</p>
          ) : entries === null ? (
            <p className="px-2 py-4 text-xs text-muted">Carregando…</p>
          ) : entries.length === 0 ? (
            <p className="px-2 py-4 text-xs text-muted">Pasta vazia.</p>
          ) : (
            entries.map((e) => (
              <EntryRow
                key={e.path} entry={e} projectId={projectId} selected={selected === e.path}
                onOpen={() => (e.kind === "dir" ? setPath(e.path) : openFile(e.path))}
                onDropInto={(src) => moveInto(src, e.path)}
                onChanged={refresh}
              />
            ))
          )}
        </div>
      </div>

      <div className="flex min-w-0 flex-1 flex-col rounded-xl border border-border bg-surface">
        {!selected ? (
          <p className="grid h-full min-h-[120px] place-items-center px-6 text-center text-sm text-muted">
            Escolha um arquivo.
          </p>
        ) : (
          <>
            <div className="flex items-center justify-between gap-2 border-b border-border px-3 py-2">
              <span className="truncate font-mono text-xs text-ink-soft">{selected}</span>
              <div className="flex shrink-0 items-center gap-1.5">
                {editing ? (
                  <>
                    <button onClick={save} disabled={saving}
                      className="flex items-center gap-1 rounded-full bg-accent px-2.5 py-1 text-[11px] font-medium text-white hover:bg-accent-hover disabled:opacity-60">
                      {saving ? <Loader2 size={12} className="animate-spin" /> : <Save size={12} />} Salvar
                    </button>
                    <button onClick={() => setEditing(false)} disabled={saving}
                      className="flex items-center gap-1 rounded-full border border-border bg-surface2 px-2.5 py-1 text-[11px] text-ink-soft hover:bg-hover">
                      <X size={12} /> Cancelar
                    </button>
                  </>
                ) : (
                  <>
                    <button onClick={startEdit} disabled={loadingContent}
                      className="flex items-center gap-1 rounded-full border border-border bg-surface2 px-2.5 py-1 text-[11px] text-ink-soft transition-colors hover:bg-hover disabled:opacity-60">
                      <Pencil size={12} /> Editar
                    </button>
                    {onUse && (
                      <button
                        onClick={() => onUse(
                          selected,
                          // arquivo grande vem cortado do servidor — marca o corte
                          // no texto inserido, senão a IA acha que o arquivo acaba ali
                          contentMeta?.truncated
                            ? `${content}\n… (arquivo truncado — ${contentMeta.total_lines} linhas no total)`
                            : content,
                        )}
                        disabled={loadingContent}
                        className="flex items-center gap-1 rounded-full border border-border bg-surface2 px-2.5 py-1 text-[11px] font-medium text-ink-soft transition-colors hover:bg-hover disabled:opacity-60">
                        <Share2 size={12} /> {useLabel}
                      </button>
                    )}
                  </>
                )}
              </div>
            </div>
            {saveError && <p className="border-b border-border bg-red-500/10 px-3 py-1.5 text-[11px] text-red-400">{saveError}</p>}
            <div className="flex-1 overflow-auto">
              {loadingContent ? (
                <p className="p-4 text-xs text-muted">Carregando…</p>
              ) : editing ? (
                <textarea
                  value={draft} onChange={(e) => setDraft(e.target.value)} spellCheck={false}
                  className="h-full min-h-[160px] w-full resize-none bg-transparent p-3 font-mono text-[11px] leading-5 text-ink outline-none"
                />
              ) : (
                <CodeView
                  ref={viewRef}
                  content={content}
                  onDragStartSnippet={(e) => {
                    const sel = selectionLines(viewRef.current);
                    if (!sel || !selected) return;
                    const payload: SnippetPayload = {
                      projectId, path: selected,
                      startLine: sel.start, endLine: sel.end, text: sel.text,
                    };
                    e.dataTransfer.setData(SNIPPET_MIME, JSON.stringify(payload));
                    e.dataTransfer.setData("text/plain", sel.text);
                    e.dataTransfer.effectAllowed = "copy";
                  }}
                />
              )}
            </div>
            {!editing && contentMeta?.truncated && (
              <p className="border-t border-border px-3 py-1.5 text-[11px] text-amber-500">
                arquivo truncado ({contentMeta.total_lines} linhas no total).
              </p>
            )}
          </>
        )}
      </div>
    </div>
  );
}

/** Visualizador com CALHA DE NÚMEROS de linha.
 *
 *  A calha é `select-none`: ela não entra na cópia nem na seleção, então arrastar
 *  um trecho leva só o código. Cada linha carrega `data-line`, que é como o
 *  `selectionLines` descobre o intervalo selecionado sem precisar contar caracteres.
 *  Arquivos gigantes caem no <pre> simples (ver MAX_NUMBERED_LINES). */
const CodeView = forwardRef<HTMLDivElement, {
  content: string;
  onDragStartSnippet: (e: React.DragEvent) => void;
}>(function CodeView({ content, onDragStartSnippet }, ref) {
  const lines = useMemo(() => content.split("\n"), [content]);
  if (lines.length > MAX_NUMBERED_LINES) {
    return (
      <pre className="whitespace-pre p-3 font-mono text-[11px] leading-5 text-ink-soft">
        <code>{content}</code>
      </pre>
    );
  }
  // Largura do gutter = dígitos + padding lateral (pl-3 pr-3 = 1.5rem). Com
  // box-sizing:border-box (padrão do Tailwind) a `width` inclui o padding, então
  // ela precisa somar os dois — senão o número estoura e cola/sobrepõe o código.
  const gutter = `calc(${String(lines.length).length}ch + 1.5rem)`;
  return (
    <div
      ref={ref}
      draggable
      onDragStart={onDragStartSnippet}
      title="Selecione um trecho e arraste até o chat para enviá-lo com arquivo e linhas"
      className="w-max min-w-full py-3 font-mono text-[11px] leading-5"
    >
      {lines.map((l, i) => (
        <div key={i} data-line={i + 1} className="flex">
          <span
            style={{ width: gutter }}
            className="shrink-0 select-none border-r border-border/60 pl-3 pr-3 text-right tabular-nums text-muted/60"
          >
            {i + 1}
          </span>
          <span className="whitespace-pre pl-3 pr-3 text-ink-soft">{l}</span>
        </div>
      ))}
    </div>
  );
});

/** mime types do arraste-e-solte do Codespace — exportados pra quem aceita o drop
 *  (o campo de mensagem do chat). */
export { DND_MIME as CODESPACE_DND_MIME, SNIPPET_MIME as CODESPACE_SNIPPET_MIME };
export type { SnippetPayload as CodespaceSnippetPayload };
export type { DragPayload as CodespaceDragPayload };
