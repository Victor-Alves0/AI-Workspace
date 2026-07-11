"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  AlertTriangle, ArrowLeft, BookOpen, Check, FileText, Loader2, Plus,
  RotateCcw, Trash2, Upload,
} from "lucide-react";
import { api, API_URL } from "@/lib/api";
import type { KnowledgeBase, KnowledgeDoc } from "@/lib/types";
import { useConfirm } from "@/components/ConfirmDialog";

function fmtSize(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
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

/** Base de Conhecimento (RAG): criar bases, subir documentos, acompanhar a
 *  indexação. Fica em Espaço → Conhecimento. Acople as bases a um modelo (no
 *  editor de modelos) ou a um chat (nos Controles) para a IA consultá-las. */
export default function KnowledgeView() {
  const confirm = useConfirm();
  const [bases, setBases] = useState<KnowledgeBase[]>([]);
  const [sel, setSel] = useState<string | null>(null);
  const [docs, setDocs] = useState<KnowledgeDoc[]>([]);
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");
  const [uploading, setUploading] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const loadBases = useCallback(async () => {
    try { setBases(await api.get<KnowledgeBase[]>("/knowledge/bases")); } catch {}
  }, []);

  const loadDocs = useCallback(async (baseId: string) => {
    try { setDocs(await api.get<KnowledgeDoc[]>(`/knowledge/bases/${baseId}/docs`)); } catch {}
  }, []);

  useEffect(() => { loadBases(); }, [loadBases]);
  useEffect(() => { if (sel) loadDocs(sel); }, [sel, loadDocs]);

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
      const b = await api.post<KnowledgeBase>("/knowledge/bases", { name });
      setNewName(""); setCreating(false);
      await loadBases();
      setSel(b.id);
    } catch {}
  }

  async function deleteBase(b: KnowledgeBase) {
    if (!(await confirm({
      title: "Excluir base?",
      body: <span className="text-muted">“{b.name}” e todos os seus documentos serão removidos.</span>,
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
      // upload multipart: NÃO usar o api client (ele força Content-Type json)
      const res = await fetch(`${API_URL}/knowledge/bases/${sel}/docs`, {
        method: "POST", credentials: "include", body: fd,
      });
      if (!res.ok) throw new Error(String(res.status));
      await loadDocs(sel);
      loadBases();
    } catch {} finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  async function reindexDoc(d: KnowledgeDoc) {
    try { await api.post(`/knowledge/docs/${d.id}/reindex`); if (sel) loadDocs(sel); } catch {}
  }

  async function deleteDoc(d: KnowledgeDoc) {
    try { await api.del(`/knowledge/docs/${d.id}`); if (sel) { loadDocs(sel); loadBases(); } } catch {}
  }

  const current = bases.find((b) => b.id === sel);

  // ---- Documentos de uma base ------------------------------------------- //
  if (current) {
    return (
      <div className="flex flex-col gap-4">
        <div className="flex items-center gap-3">
          <button onClick={() => setSel(null)} className="flex items-center gap-1.5 text-sm text-muted transition-colors hover:text-ink">
            <ArrowLeft size={16} /> Bases
          </button>
          <span className="text-muted">/</span>
          <span className="text-sm font-semibold text-ink">{current.name}</span>
        </div>

        <div
          onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
          onDragLeave={() => setDragOver(false)}
          onDrop={(e) => { e.preventDefault(); setDragOver(false); uploadFiles(e.dataTransfer.files); }}
          className={`flex flex-col items-center justify-center gap-2 rounded-2xl border border-dashed p-8 text-center transition-colors ${dragOver ? "border-accent bg-accent/5" : "border-border"}`}
        >
          <Upload size={22} className="text-muted" />
          <p className="text-sm text-ink">Arraste arquivos aqui ou{" "}
            <button onClick={() => fileRef.current?.click()} className="text-accent-hover underline underline-offset-2">selecione</button>
          </p>
          <p className="text-xs text-muted">PDF, DOCX, TXT, Markdown, CSV — até 25 MB por arquivo</p>
          {uploading && <p className="flex items-center gap-1.5 text-xs text-amber-500"><Loader2 size={12} className="animate-spin" /> enviando…</p>}
          <input
            ref={fileRef} type="file" multiple hidden
            accept=".pdf,.docx,.txt,.md,.markdown,.csv,.json,.text"
            onChange={(e) => e.target.files && uploadFiles(e.target.files)}
          />
        </div>

        {docs.length === 0 ? (
          <p className="py-8 text-center text-sm text-muted">Nenhum documento ainda.</p>
        ) : (
          <ul className="flex flex-col gap-1.5">
            {docs.map((d) => (
              <li key={d.id} className="flex items-center gap-3 rounded-xl border border-border bg-surface px-3.5 py-2.5">
                <FileText size={18} className="shrink-0 text-muted" />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm text-ink">{d.filename}</p>
                  <div className="flex items-center gap-2">
                    <StatusBadge d={d} />
                    <span className="text-xs text-muted">· {fmtSize(d.size)}</span>
                  </div>
                </div>
                {d.status === "error" && (
                  <button onClick={() => reindexDoc(d)} title="Reindexar" className="rounded-lg p-1.5 text-muted transition-colors hover:bg-hover hover:text-ink">
                    <RotateCcw size={15} />
                  </button>
                )}
                <button onClick={() => deleteDoc(d)} title="Excluir" className="rounded-lg p-1.5 text-muted transition-colors hover:bg-hover hover:text-rose-500">
                  <Trash2 size={15} />
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    );
  }

  // ---- Lista de bases --------------------------------------------------- //
  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-muted">
          Suba documentos e acople as bases a um modelo (editor de modelos) ou a um chat (Controles) para a IA respondê-los com citações.
        </p>
        {!creating && (
          <button onClick={() => setCreating(true)} className="flex shrink-0 items-center gap-1.5 rounded-lg bg-accent px-3 py-1.5 text-sm font-medium text-white transition-colors hover:bg-accent-hover">
            <Plus size={16} /> Nova base
          </button>
        )}
      </div>

      {creating && (
        <div className="flex items-center gap-2 rounded-xl border border-border bg-surface p-2.5">
          <input
            autoFocus value={newName} onChange={(e) => setNewName(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") createBase(); if (e.key === "Escape") { setCreating(false); setNewName(""); } }}
            placeholder="Nome da base (ex.: Manuais, Contratos…)"
            className="flex-1 bg-transparent px-1.5 text-sm text-ink outline-none placeholder:text-muted"
          />
          <button onClick={createBase} className="rounded-lg bg-accent px-3 py-1.5 text-sm font-medium text-white hover:bg-accent-hover">Criar</button>
          <button onClick={() => { setCreating(false); setNewName(""); }} className="rounded-lg px-2 py-1.5 text-sm text-muted hover:text-ink">Cancelar</button>
        </div>
      )}

      {bases.length === 0 && !creating ? (
        <div className="flex flex-col items-center gap-2 py-12 text-center">
          <BookOpen size={26} className="text-muted" />
          <p className="text-sm text-ink">Nenhuma base ainda</p>
          <p className="max-w-sm text-xs text-muted">Crie uma base, suba seus documentos e a IA poderá consultá-los nas conversas (RAG).</p>
        </div>
      ) : (
        <ul className="grid gap-2 sm:grid-cols-2">
          {bases.map((b) => (
            <li key={b.id} className="group flex items-center gap-3 rounded-xl border border-border bg-surface p-3.5 transition-colors hover:border-accent/40">
              <button onClick={() => setSel(b.id)} className="flex min-w-0 flex-1 items-center gap-3 text-left">
                <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-surface2 text-accent-hover">
                  <BookOpen size={18} />
                </span>
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium text-ink">{b.name}</p>
                  <p className="text-xs text-muted">{b.doc_count} doc(s) · {b.chunk_count} trechos</p>
                </div>
              </button>
              <button onClick={() => deleteBase(b)} title="Excluir base" className="rounded-lg p-1.5 text-muted opacity-0 transition-all hover:bg-hover hover:text-rose-500 group-hover:opacity-100">
                <Trash2 size={15} />
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
