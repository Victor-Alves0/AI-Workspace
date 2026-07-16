"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle, ArrowLeft, BookOpen, Brain, Check, ChevronRight, FilePlus2, FileText,
  Folder, FolderInput, FolderPlus, Home, Loader2, Pencil, Plus, RotateCcw, Tag,
  Trash2, Upload, Waypoints, X,
} from "lucide-react";
import { api, API_URL } from "@/lib/api";
import type { KnowledgeBase, KnowledgeDoc, KnowledgeDocMeta, KnowledgeFolder } from "@/lib/types";
import { useConfirm } from "@/components/ConfirmDialog";
import { AnchoredMenu, MenuDivider, MenuItem, TagInput } from "./ui";
import NoteEditor from "./NoteEditor";
import BrainGraph from "./BrainGraph";

function fmtSize(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

const TEXT_RE = /\.(txt|md|markdown|csv|json|log|ya?ml)$/i;
function isTextDoc(d: KnowledgeDoc): boolean {
  return (d.mime || "").startsWith("text/") || TEXT_RE.test(d.filename || "");
}

const STATUS: Record<KnowledgeDoc["status"], { label: string; cls: string }> = {
  pending: { label: "Na fila", cls: "text-muted" },
  indexing: { label: "Indexando", cls: "text-amber-500" },
  ready: { label: "Pronto", cls: "text-emerald-500" },
  error: { label: "Erro", cls: "text-rose-500" },
};

function StatusBadge({ d }: { d: KnowledgeDoc }) {
  const s = STATUS[d.status] ?? STATUS.pending;
  return (
    <span className={`flex items-center gap-1 text-xs ${s.cls}`} title={d.error || undefined}>
      {d.status === "indexing" || d.status === "pending" ? <Loader2 size={12} className="animate-spin" /> :
        d.status === "ready" ? <Check size={12} /> : <AlertTriangle size={12} />}
      {s.label}{d.status === "ready" && d.chunk_count ? ` · ${d.chunk_count} trechos` : ""}
    </span>
  );
}

/** Menu "Mover para" — lista pastas (indentadas por profundidade) + raiz. */
function MoveMenu({
  folders, onMove, onClose, anchorRef, excludeId,
}: {
  folders: KnowledgeFolder[];
  onMove: (folderId: string | null) => void;
  onClose: () => void;
  anchorRef: React.RefObject<HTMLElement | null>;
  excludeId?: string;
}) {
  // ordena por caminho p/ exibir hierarquia; calcula profundidade
  const byId = useMemo(() => Object.fromEntries(folders.map((f) => [f.id, f])), [folders]);
  const depth = (f: KnowledgeFolder): number => {
    let d = 0, cur: KnowledgeFolder | undefined = f;
    while (cur?.parent_id) { d++; cur = byId[cur.parent_id]; if (d > 20) break; }
    return d;
  };
  const list = [...folders].filter((f) => f.id !== excludeId);
  return (
    <AnchoredMenu anchorRef={anchorRef} onClose={onClose} align="right" className="max-h-72 overflow-y-auto">
      <MenuItem icon={<Home size={14} />} onClick={() => { onMove(null); onClose(); }}>Raiz da base</MenuItem>
      {list.length > 0 && <MenuDivider />}
      {list.map((f) => (
        <button
          key={f.id}
          onClick={() => { onMove(f.id); onClose(); }}
          className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-sm text-ink transition-colors hover:bg-hover"
          style={{ paddingLeft: 10 + depth(f) * 14 }}
        >
          <Folder size={14} className="shrink-0 text-muted" />
          <span className="truncate">{f.name}</span>
        </button>
      ))}
    </AnchoredMenu>
  );
}

/** Base de Conhecimento (RAG) — explorador: pastas, arquivos, criar `.txt`,
 *  metadados e tags. Fica em Espaço → Conhecimento. Acople as bases a um modelo
 *  (editor de modelos) ou a um chat (Controles) para a IA consultá-las.
 *
 *  Com `kind="brain"` vira o explorador de CÉREBROS (Espaço → Cérebros): notas
 *  markdown [[interligadas]] que a IA lê/escreve, com vista de Grafo e editor
 *  de nota com preview/navegação por wikilink. */
export default function KnowledgeView({ kind = "kb" }: { kind?: "kb" | "brain" }) {
  const isBrain = kind === "brain";
  const confirm = useConfirm();
  const [bases, setBases] = useState<KnowledgeBase[]>([]);
  const [sel, setSel] = useState<string | null>(null);
  const [docs, setDocs] = useState<KnowledgeDoc[]>([]);
  const [folders, setFolders] = useState<KnowledgeFolder[]>([]);
  const [cwd, setCwd] = useState<string | null>(null); // pasta atual (null = raiz)
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");
  const [uploading, setUploading] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const [newFolder, setNewFolder] = useState(false);
  const [folderName, setFolderName] = useState("");
  // editor de arquivo de texto: {docId?, filename, content}
  const [editor, setEditor] = useState<{ docId?: string; filename: string; content: string } | null>(null);
  const [saving, setSaving] = useState(false);
  const [metaDoc, setMetaDoc] = useState<KnowledgeDoc | null>(null);
  const [renaming, setRenaming] = useState<string | null>(null); // folder id
  const [renameVal, setRenameVal] = useState("");
  const [renamingBase, setRenamingBase] = useState<string | null>(null); // base id
  const [baseRenameVal, setBaseRenameVal] = useState("");
  const [moveFor, setMoveFor] = useState<{ kind: "doc" | "folder"; id: string } | null>(null);
  const moveAnchor = useRef<HTMLButtonElement>(null);
  // vista do cérebro: lista de notas (files) ou grafo de [[wikilinks]]
  const [brainView, setBrainView] = useState<"files" | "graph">("files");

  const loadBases = useCallback(async () => {
    try { setBases(await api.get<KnowledgeBase[]>(`/knowledge/bases?kind=${kind}`)); } catch {}
  }, [kind]);
  const loadDocs = useCallback(async (baseId: string) => {
    try { setDocs(await api.get<KnowledgeDoc[]>(`/knowledge/bases/${baseId}/docs`)); } catch {}
  }, []);
  const loadFolders = useCallback(async (baseId: string) => {
    try { setFolders(await api.get<KnowledgeFolder[]>(`/knowledge/bases/${baseId}/folders`)); } catch {}
  }, []);

  useEffect(() => { loadBases(); }, [loadBases]);
  useEffect(() => { if (sel) { loadDocs(sel); loadFolders(sel); setCwd(null); setBrainView("files"); } }, [sel, loadDocs, loadFolders]);

  // enquanto houver doc indexando, atualiza a cada 2s (status + contagens)
  useEffect(() => {
    if (!sel) return;
    const busy = docs.some((d) => d.status === "pending" || d.status === "indexing");
    if (!busy) return;
    const t = setInterval(() => { loadDocs(sel); loadBases(); }, 2000);
    return () => clearInterval(t);
  }, [sel, docs, loadDocs, loadBases]);

  async function createBase() {
    const name = newName.trim();
    if (!name) return;
    try {
      const b = await api.post<KnowledgeBase>("/knowledge/bases", { name, kind });
      setNewName(""); setCreating(false);
      await loadBases();
      setSel(b.id);
    } catch {}
  }
  async function deleteBase(b: KnowledgeBase) {
    if (!(await confirm({
      title: isBrain ? "Excluir cérebro?" : "Excluir base?",
      body: <span className="text-muted">“{b.name}” e {isBrain ? "todas as suas notas serão removidas" : "todos os seus documentos serão removidos"}.</span>,
      confirmLabel: "Excluir", danger: true,
    }))) return;
    try { await api.del(`/knowledge/bases/${b.id}`); } catch {}
    if (sel === b.id) setSel(null);
    loadBases();
  }

  async function uploadFiles(files: FileList | File[]) {
    if (!sel || !files || files.length === 0) return;
    const fd = new FormData();
    for (const f of Array.from(files)) fd.append("files", f);
    setUploading(true);
    try {
      const url = `${API_URL}/knowledge/bases/${sel}/docs${cwd ? `?folder_id=${cwd}` : ""}`;
      const res = await fetch(url, { method: "POST", credentials: "include", body: fd });
      if (!res.ok) throw new Error(String(res.status));
      await loadDocs(sel); loadBases();
    } catch {} finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  async function createFolder() {
    const name = folderName.trim();
    if (!name || !sel) return;
    try {
      await api.post(`/knowledge/bases/${sel}/folders`, { name, parent_id: cwd });
      setFolderName(""); setNewFolder(false);
      loadFolders(sel);
    } catch {}
  }
  async function submitRenameFolder(id: string) {
    const name = renameVal.trim();
    setRenaming(null);
    if (name && sel) { try { await api.patch(`/knowledge/folders/${id}`, { name }); loadFolders(sel); } catch {} }
  }
  async function deleteFolder(f: KnowledgeFolder) {
    if (!(await confirm({
      title: "Excluir pasta?",
      body: <span className="text-muted">“{f.name}” será removida. Os arquivos e subpastas sobem para o nível acima.</span>,
      confirmLabel: "Excluir", danger: true,
    }))) return;
    try { await api.del(`/knowledge/folders/${f.id}`); } catch {}
    if (sel) { loadFolders(sel); loadDocs(sel); }
  }

  async function saveTextDoc() {
    if (!sel || !editor) return;
    const filename = editor.filename.trim() || (isBrain ? "nota" : "documento.txt");
    setSaving(true);
    try {
      if (editor.docId) {
        await api.put(`/knowledge/docs/${editor.docId}/text`, { content: editor.content });
      } else {
        await api.post(`/knowledge/bases/${sel}/docs/text`, { filename, content: editor.content, folder_id: cwd });
      }
      setEditor(null);
      loadDocs(sel); loadBases();
    } catch {} finally { setSaving(false); }
  }
  async function openTextEditor(d: KnowledgeDoc) {
    try {
      const r = await api.get<{ content: string }>(`/knowledge/docs/${d.id}/text`);
      setEditor({ docId: d.id, filename: d.filename, content: r.content });
    } catch {}
  }
  async function openNoteById(docId: string) {
    try {
      const r = await api.get<{ filename: string; content: string }>(`/knowledge/docs/${docId}/text`);
      setEditor({ docId, filename: r.filename, content: r.content });
    } catch {}
  }
  // clique num [[wikilink]] no preview: abre a nota alvo ou oferece criá-la
  async function navigateWikilink(title: string) {
    if (!sel) return;
    try {
      const r = await api.get<{ doc_id: string }>(`/brain/bases/${sel}/resolve?title=${encodeURIComponent(title)}`);
      await openNoteById(r.doc_id);
    } catch {
      setEditor({ filename: title, content: "" });
    }
  }
  function openGraphNode(node: { id: string; title: string; ghost: boolean }) {
    if (node.ghost) setEditor({ filename: node.title, content: "" });
    else openNoteById(node.id);
  }
  async function saveMeta(meta: KnowledgeDocMeta) {
    if (!sel || !metaDoc) return;
    setSaving(true);
    try {
      await api.patch(`/knowledge/docs/${metaDoc.id}`, { meta });
      setMetaDoc(null);
      loadDocs(sel);
    } catch {} finally { setSaving(false); }
  }
  async function moveDoc(id: string, folderId: string | null) {
    if (!sel) return;
    try { await api.patch(`/knowledge/docs/${id}`, folderId ? { folder_id: folderId } : { to_root: true }); loadDocs(sel); } catch {}
  }
  async function moveFolder(id: string, parentId: string | null) {
    if (!sel) return;
    try { await api.patch(`/knowledge/folders/${id}`, parentId ? { parent_id: parentId } : { to_root: true }); loadFolders(sel); } catch {}
  }
  async function reindexDoc(d: KnowledgeDoc) {
    try { await api.post(`/knowledge/docs/${d.id}/reindex`); if (sel) loadDocs(sel); } catch {}
  }
  async function deleteDoc(d: KnowledgeDoc) {
    try { await api.del(`/knowledge/docs/${d.id}`); if (sel) { loadDocs(sel); loadBases(); } } catch {}
  }
  async function submitRenameBase(b: KnowledgeBase) {
    const name = baseRenameVal.trim();
    setRenamingBase(null);
    if (!name || name === b.name) return;
    setBases((bs) => bs.map((x) => (x.id === b.id ? { ...x, name } : x)));
    try { await api.patch(`/knowledge/bases/${b.id}`, { name }); } catch { loadBases(); }
  }
  async function saveBaseTags(tags: string[]) {
    if (!current) return;
    setBases((bs) => bs.map((b) => (b.id === current.id ? { ...b, tags } : b)));
    try { await api.patch(`/knowledge/bases/${current.id}`, { name: current.name, description: current.description, tags }); } catch {}
  }

  const current = bases.find((b) => b.id === sel);
  const folderById = useMemo(() => Object.fromEntries(folders.map((f) => [f.id, f])), [folders]);
  const breadcrumb = useMemo(() => {
    const path: KnowledgeFolder[] = [];
    let cur = cwd ? folderById[cwd] : undefined;
    while (cur) { path.unshift(cur); cur = cur.parent_id ? folderById[cur.parent_id] : undefined; }
    return path;
  }, [cwd, folderById]);
  const shownFolders = folders.filter((f) => (f.parent_id ?? null) === cwd);
  const shownDocs = docs.filter((d) => (d.folder_id ?? null) === cwd);

  // ---- Explorador de uma base ------------------------------------------- //
  if (current) {
    return (
      <div className="flex flex-col gap-4">
        {/* breadcrumb + tags da base */}
        <div className="flex flex-wrap items-center gap-2">
          <button onClick={() => setSel(null)} className="flex items-center gap-1.5 text-sm text-muted transition-colors hover:text-ink">
            <ArrowLeft size={16} /> {isBrain ? "Cérebros" : "Bases"}
          </button>
          <span className="text-muted">/</span>
          <button onClick={() => setCwd(null)} className="text-sm font-semibold text-ink transition-colors hover:text-accent-hover">{current.name}</button>
          {breadcrumb.map((f) => (
            <span key={f.id} className="flex items-center gap-2">
              <ChevronRight size={14} className="text-muted" />
              <button onClick={() => setCwd(f.id)} className="text-sm text-ink-soft transition-colors hover:text-accent-hover">{f.name}</button>
            </span>
          ))}
          {isBrain && (
            <div className="ml-auto flex items-center rounded-lg border border-border p-0.5">
              <button
                onClick={() => setBrainView("files")}
                className={`flex items-center gap-1 rounded-md px-2.5 py-1 text-xs transition-colors ${brainView === "files" ? "bg-surface2 text-ink" : "text-muted hover:text-ink"}`}
              >
                <FileText size={12} /> Notas
              </button>
              <button
                onClick={() => setBrainView("graph")}
                className={`flex items-center gap-1 rounded-md px-2.5 py-1 text-xs transition-colors ${brainView === "graph" ? "bg-surface2 text-ink" : "text-muted hover:text-ink"}`}
              >
                <Waypoints size={12} /> Grafo
              </button>
            </div>
          )}
        </div>

        {isBrain && brainView === "graph" ? (
          <BrainGraph baseId={current.id} onOpenNote={openGraphNode} />
        ) : (
        <>
        <div className="rounded-xl border border-border bg-surface p-3">
          <p className="mb-1.5 flex items-center gap-1.5 text-xs font-medium text-muted"><Tag size={12} /> {isBrain ? "Etiquetas do cérebro" : "Etiquetas da base"}</p>
          <TagInput tags={current.tags || []} onChange={saveBaseTags} placeholder={isBrain ? "Ex.: projetos, estudos…" : "Ex.: manuais, fiscal…"} />
        </div>

        {/* barra de ações */}
        <div className="flex flex-wrap items-center gap-2">
          <button onClick={() => setNewFolder(true)} className="flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-sm text-ink transition-colors hover:bg-hover">
            <FolderPlus size={15} /> Nova pasta
          </button>
          <button onClick={() => setEditor({ filename: isBrain ? "" : "novo.txt", content: "" })} className="flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-sm text-ink transition-colors hover:bg-hover">
            <FilePlus2 size={15} /> {isBrain ? "Nova nota" : "Novo arquivo de texto"}
          </button>
          <button onClick={() => fileRef.current?.click()} className="flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-sm text-ink transition-colors hover:bg-hover">
            <Upload size={15} /> Enviar arquivos
          </button>
          {uploading && <span className="flex items-center gap-1.5 text-xs text-amber-500"><Loader2 size={12} className="animate-spin" /> enviando…</span>}
          <input ref={fileRef} type="file" multiple hidden accept=".pdf,.docx,.txt,.md,.markdown,.csv,.json" onChange={(e) => e.target.files && uploadFiles(e.target.files)} />
        </div>

        {newFolder && (
          <div className="flex items-center gap-2 rounded-xl border border-border bg-surface p-2.5">
            <FolderPlus size={16} className="text-muted" />
            <input
              autoFocus value={folderName} onChange={(e) => setFolderName(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") createFolder(); if (e.key === "Escape") { setNewFolder(false); setFolderName(""); } }}
              placeholder="Nome da pasta" className="flex-1 bg-transparent px-1 text-sm text-ink outline-none placeholder:text-muted"
            />
            <button onClick={createFolder} className="rounded-lg bg-accent px-3 py-1.5 text-sm font-medium text-white hover:bg-accent-hover">Criar</button>
            <button onClick={() => { setNewFolder(false); setFolderName(""); }} className="rounded-lg px-2 py-1.5 text-sm text-muted hover:text-ink">Cancelar</button>
          </div>
        )}

        {/* lista (pastas + docs da pasta atual) */}
        <div
          onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
          onDragLeave={() => setDragOver(false)}
          onDrop={(e) => { e.preventDefault(); setDragOver(false); uploadFiles(e.dataTransfer.files); }}
          className={`min-h-[120px] rounded-2xl border ${dragOver ? "border-accent border-dashed bg-accent/5" : "border-border"}`}
        >
          {shownFolders.length === 0 && shownDocs.length === 0 ? (
            <p className="py-14 text-center text-sm text-muted">
              {isBrain ? "Nenhuma nota aqui. Crie uma nota — ou peça à IA para anotar algo no cérebro." : "Pasta vazia. Arraste arquivos aqui, crie uma subpasta ou um arquivo de texto."}
            </p>
          ) : (
            <ul className="flex flex-col gap-1 p-2">
              {shownFolders.map((f) => (
                <li key={f.id} className="group flex items-center gap-3 rounded-xl px-3 py-2.5 transition-colors hover:bg-hover">
                  {renaming === f.id ? (
                    <input
                      autoFocus value={renameVal} onChange={(e) => setRenameVal(e.target.value)}
                      onBlur={() => submitRenameFolder(f.id)}
                      onKeyDown={(e) => { if (e.key === "Enter") submitRenameFolder(f.id); if (e.key === "Escape") setRenaming(null); }}
                      className="flex-1 rounded-lg border border-accent bg-surface px-2 py-1 text-sm outline-none"
                    />
                  ) : (
                    <>
                      <button onClick={() => setCwd(f.id)} className="flex min-w-0 flex-1 items-center gap-2.5 text-left">
                        <Folder size={18} className="shrink-0 text-accent-hover" />
                        <span className="truncate text-sm text-ink">{f.name}</span>
                      </button>
                      <div className="flex items-center gap-1 opacity-0 transition-opacity group-hover:opacity-100">
                        <button ref={moveFor?.id === f.id ? moveAnchor : undefined} onClick={() => setMoveFor({ kind: "folder", id: f.id })} title="Mover" className="rounded-lg p-1.5 text-muted hover:bg-hover hover:text-ink"><FolderInput size={15} /></button>
                        <button onClick={() => { setRenaming(f.id); setRenameVal(f.name); }} title="Renomear" className="rounded-lg p-1.5 text-muted hover:bg-hover hover:text-ink"><Pencil size={15} /></button>
                        <button onClick={() => deleteFolder(f)} title="Excluir" className="rounded-lg p-1.5 text-muted hover:bg-hover hover:text-rose-500"><Trash2 size={15} /></button>
                      </div>
                    </>
                  )}
                </li>
              ))}
              {shownDocs.map((d) => (
                <li key={d.id} className="group flex items-center gap-3 rounded-xl px-3 py-2.5 transition-colors hover:bg-hover">
                  <FileText size={18} className="shrink-0 text-muted" />
                  <div
                    className={`min-w-0 flex-1 ${isBrain && isTextDoc(d) ? "cursor-pointer" : ""}`}
                    onClick={() => { if (isBrain && isTextDoc(d)) openTextEditor(d); }}
                  >
                    <p className="truncate text-sm text-ink">{d.meta?.title || d.filename}</p>
                    <div className="flex flex-wrap items-center gap-2">
                      <StatusBadge d={d} />
                      <span className="text-xs text-muted">· {fmtSize(d.size)}</span>
                      {(d.meta?.tags || []).slice(0, 4).map((t) => (
                        <span key={t} className="rounded-full bg-surface2 px-1.5 py-0.5 text-[10px] text-muted">{t}</span>
                      ))}
                    </div>
                  </div>
                  <div className="flex items-center gap-1 opacity-0 transition-opacity group-hover:opacity-100">
                    {isTextDoc(d) && (
                      <button onClick={() => openTextEditor(d)} title="Editar texto" className="rounded-lg p-1.5 text-muted hover:bg-hover hover:text-ink"><Pencil size={15} /></button>
                    )}
                    <button onClick={() => setMetaDoc(d)} title="Metadados" className="rounded-lg p-1.5 text-muted hover:bg-hover hover:text-ink"><Tag size={15} /></button>
                    <button ref={moveFor?.id === d.id ? moveAnchor : undefined} onClick={() => setMoveFor({ kind: "doc", id: d.id })} title="Mover" className="rounded-lg p-1.5 text-muted hover:bg-hover hover:text-ink"><FolderInput size={15} /></button>
                    {d.status === "error" && (
                      <button onClick={() => reindexDoc(d)} title="Reindexar" className="rounded-lg p-1.5 text-muted hover:bg-hover hover:text-ink"><RotateCcw size={15} /></button>
                    )}
                    <button onClick={() => deleteDoc(d)} title="Excluir" className="rounded-lg p-1.5 text-muted hover:bg-hover hover:text-rose-500"><Trash2 size={15} /></button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>

        {moveFor && (
          <MoveMenu
            folders={folders}
            excludeId={moveFor.kind === "folder" ? moveFor.id : undefined}
            anchorRef={moveAnchor}
            onClose={() => setMoveFor(null)}
            onMove={(fid) => moveFor.kind === "doc" ? moveDoc(moveFor.id, fid) : moveFolder(moveFor.id, fid)}
          />
        )}
        </>
        )}

        {editor && (isBrain ? (
          <NoteEditor
            filename={editor.filename}
            content={editor.content}
            isNew={!editor.docId}
            saving={saving}
            onChange={(patch) => setEditor((e) => (e ? { ...e, ...patch } : e))}
            onSave={saveTextDoc}
            onClose={() => setEditor(null)}
            onNavigate={navigateWikilink}
          />
        ) : (
          <TextEditorModal
            filename={editor.filename}
            content={editor.content}
            isNew={!editor.docId}
            saving={saving}
            onChange={(patch) => setEditor((e) => (e ? { ...e, ...patch } : e))}
            onSave={saveTextDoc}
            onClose={() => setEditor(null)}
          />
        ))}
        {metaDoc && (
          <MetaModal doc={metaDoc} saving={saving} onSave={saveMeta} onClose={() => setMetaDoc(null)} />
        )}
      </div>
    );
  }

  // ---- Lista de bases --------------------------------------------------- //
  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-muted">
          {isBrain
            ? "Notas interligadas que a IA lê e escreve. Acople um cérebro a um modelo ou chat e ele passa a anotar e consultar o que aprende."
            : "Organize documentos em pastas, crie arquivos de texto e acople as bases a um modelo ou chat para a IA respondê-los com citações."}
        </p>
        {!creating && (
          <button onClick={() => setCreating(true)} className="flex shrink-0 items-center gap-1.5 rounded-lg bg-accent px-3 py-1.5 text-sm font-medium text-white transition-colors hover:bg-accent-hover">
            <Plus size={16} /> {isBrain ? "Novo cérebro" : "Nova base"}
          </button>
        )}
      </div>

      {creating && (
        <div className="flex items-center gap-2 rounded-xl border border-border bg-surface p-2.5">
          <input
            autoFocus value={newName} onChange={(e) => setNewName(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") createBase(); if (e.key === "Escape") { setCreating(false); setNewName(""); } }}
            placeholder={isBrain ? "Nome do cérebro (ex.: Projetos, Estudos…)" : "Nome da base (ex.: Manuais, Contratos…)"}
            className="flex-1 bg-transparent px-1.5 text-sm text-ink outline-none placeholder:text-muted"
          />
          <button onClick={createBase} className="rounded-lg bg-accent px-3 py-1.5 text-sm font-medium text-white hover:bg-accent-hover">Criar</button>
          <button onClick={() => { setCreating(false); setNewName(""); }} className="rounded-lg px-2 py-1.5 text-sm text-muted hover:text-ink">Cancelar</button>
        </div>
      )}

      {bases.length === 0 && !creating ? (
        <div className="flex flex-col items-center gap-2 py-12 text-center">
          {isBrain ? <Brain size={26} className="text-muted" /> : <BookOpen size={26} className="text-muted" />}
          <p className="text-sm text-ink">{isBrain ? "Nenhum cérebro ainda" : "Nenhuma base ainda"}</p>
          <p className="max-w-sm text-xs text-muted">
            {isBrain
              ? "Crie um cérebro e acople-o a um modelo: a IA passa a guardar e ligar o que aprende em notas [[interligadas]]."
              : "Crie uma base, suba seus documentos e a IA poderá consultá-los nas conversas (RAG)."}
          </p>
        </div>
      ) : (
        <ul className="grid gap-2 sm:grid-cols-2">
          {bases.map((b) => (
            <li key={b.id} className="group flex items-center gap-3 rounded-xl border border-border bg-surface p-3.5 transition-colors hover:border-accent/40">
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-surface2 text-accent-hover">
                {isBrain ? <Brain size={18} /> : <BookOpen size={18} />}
              </span>
              {renamingBase === b.id ? (
                <input
                  autoFocus
                  value={baseRenameVal}
                  onChange={(e) => setBaseRenameVal(e.target.value)}
                  onBlur={() => submitRenameBase(b)}
                  onKeyDown={(e) => { if (e.key === "Enter") submitRenameBase(b); if (e.key === "Escape") setRenamingBase(null); }}
                  className="min-w-0 flex-1 rounded-md border border-accent bg-surface px-2 py-1 text-sm text-ink outline-none"
                />
              ) : (
                <button onClick={() => setSel(b.id)} className="flex min-w-0 flex-1 items-center text-left">
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium text-ink">{b.name}</p>
                    <p className="text-xs text-muted">{isBrain ? `${b.doc_count} nota(s)` : `${b.doc_count} doc(s) · ${b.chunk_count} trechos`}</p>
                    {(b.tags || []).length > 0 && (
                      <div className="mt-1 flex flex-wrap gap-1">
                        {(b.tags || []).slice(0, 4).map((t) => (
                          <span key={t} className="rounded-full bg-surface2 px-1.5 py-0.5 text-[10px] text-muted">{t}</span>
                        ))}
                      </div>
                    )}
                  </div>
                </button>
              )}
              {renamingBase !== b.id && (
                <>
                  <button onClick={() => { setRenamingBase(b.id); setBaseRenameVal(b.name); }} title="Renomear" className="rounded-lg p-1.5 text-muted opacity-0 transition-all hover:bg-hover hover:text-ink group-hover:opacity-100">
                    <Pencil size={15} />
                  </button>
                  <button onClick={() => deleteBase(b)} title="Excluir base" className="rounded-lg p-1.5 text-muted opacity-0 transition-all hover:bg-hover hover:text-rose-500 group-hover:opacity-100">
                    <Trash2 size={15} />
                  </button>
                </>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// --------------------------------------------------------------------------- //
// Modal: editor de arquivo de texto
// --------------------------------------------------------------------------- //
function TextEditorModal({
  filename, content, isNew, saving, onChange, onSave, onClose,
}: {
  filename: string; content: string; isNew: boolean; saving: boolean;
  onChange: (patch: { filename?: string; content?: string }) => void;
  onSave: () => void; onClose: () => void;
}) {
  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/60 p-4" onClick={onClose}>
      <div onClick={(e) => e.stopPropagation()} className="flex max-h-[85vh] w-full max-w-2xl flex-col overflow-hidden rounded-2xl border border-border bg-surface shadow-2xl">
        <div className="flex items-center gap-2 border-b border-border px-4 py-3">
          <FileText size={18} className="text-accent-hover" />
          {isNew ? (
            <input
              value={filename} onChange={(e) => onChange({ filename: e.target.value })}
              placeholder="nome-do-arquivo.txt"
              className="flex-1 bg-transparent text-sm font-medium text-ink outline-none placeholder:text-muted"
            />
          ) : (
            <span className="flex-1 truncate text-sm font-semibold text-ink">{filename}</span>
          )}
          <button onClick={onClose} className="rounded-lg p-1 text-muted hover:bg-hover hover:text-ink"><X size={16} /></button>
        </div>
        <textarea
          autoFocus value={content} onChange={(e) => onChange({ content: e.target.value })}
          placeholder="Escreva o conteúdo do documento…"
          className="min-h-[300px] flex-1 resize-none bg-transparent px-4 py-3 font-mono text-sm text-ink outline-none placeholder:text-muted"
        />
        <div className="flex items-center justify-end gap-2 border-t border-border px-4 py-3">
          <button onClick={onClose} className="rounded-lg px-3 py-1.5 text-sm text-muted hover:text-ink">Cancelar</button>
          <button onClick={onSave} disabled={saving} className="flex items-center gap-1.5 rounded-lg bg-accent px-4 py-1.5 text-sm font-medium text-white hover:bg-accent-hover disabled:opacity-50">
            {saving && <Loader2 size={14} className="animate-spin" />} Salvar e indexar
          </button>
        </div>
      </div>
    </div>
  );
}

// --------------------------------------------------------------------------- //
// Modal: metadados do documento
// --------------------------------------------------------------------------- //
function MetaModal({
  doc, saving, onSave, onClose,
}: {
  doc: KnowledgeDoc; saving: boolean;
  onSave: (meta: KnowledgeDocMeta) => void; onClose: () => void;
}) {
  const [title, setTitle] = useState(doc.meta?.title || "");
  const [description, setDescription] = useState(doc.meta?.description || "");
  const [tags, setTags] = useState<string[]>(doc.meta?.tags || []);
  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/60 p-4" onClick={onClose}>
      <div onClick={(e) => e.stopPropagation()} className="w-full max-w-lg overflow-hidden rounded-2xl border border-border bg-surface shadow-2xl">
        <div className="flex items-center gap-2 border-b border-border px-4 py-3">
          <Tag size={18} className="text-accent-hover" />
          <span className="flex-1 truncate text-sm font-semibold text-ink">Metadados · {doc.filename}</span>
          <button onClick={onClose} className="rounded-lg p-1 text-muted hover:bg-hover hover:text-ink"><X size={16} /></button>
        </div>
        <div className="flex flex-col gap-3 p-4">
          <label className="flex flex-col gap-1">
            <span className="text-xs font-medium text-muted">Título</span>
            <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Título descritivo (opcional)"
              className="rounded-lg border border-border bg-surface px-3 py-2 text-sm text-ink outline-none focus:border-accent/50 placeholder:text-muted" />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-xs font-medium text-muted">Descrição</span>
            <textarea value={description} onChange={(e) => setDescription(e.target.value)} rows={3} placeholder="Do que trata este documento (opcional)"
              className="resize-none rounded-lg border border-border bg-surface px-3 py-2 text-sm text-ink outline-none focus:border-accent/50 placeholder:text-muted" />
          </label>
          <div className="flex flex-col gap-1">
            <span className="text-xs font-medium text-muted">Etiquetas</span>
            <TagInput tags={tags} onChange={setTags} />
          </div>
          <p className="text-[11px] text-muted">Título e etiquetas são incluídos no índice para a IA encontrar melhor. Salvar reindexa o documento.</p>
        </div>
        <div className="flex items-center justify-end gap-2 border-t border-border px-4 py-3">
          <button onClick={onClose} className="rounded-lg px-3 py-1.5 text-sm text-muted hover:text-ink">Cancelar</button>
          <button onClick={() => onSave({ title, description, tags })} disabled={saving} className="flex items-center gap-1.5 rounded-lg bg-accent px-4 py-1.5 text-sm font-medium text-white hover:bg-accent-hover disabled:opacity-50">
            {saving && <Loader2 size={14} className="animate-spin" />} Salvar
          </button>
        </div>
      </div>
    </div>
  );
}
