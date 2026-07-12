"use client";

import { useState } from "react";
import { Brain, Eye, Loader2, Pencil, X } from "lucide-react";
import Markdown from "./Markdown";

/** Converte [[Alvo]], [[Alvo|apelido]] e [[Alvo#seção]] em links `#note:` — o
 *  clique é interceptado no preview e navega para (ou cria) a nota alvo. */
function wikilinksToMd(src: string): string {
  return (src || "").replace(
    /\[\[([^\[\]|#\n]+)(?:#[^\[\]|\n]*)?(?:\|([^\[\]\n]*))?\]\]/g,
    (_m, target: string, alias?: string) =>
      `[${(alias || target).trim()}](#note:${encodeURIComponent(target.trim())})`,
  );
}

/** Editor de NOTA do cérebro: tabs Editar (textarea) / Visualizar (markdown com
 *  [[wikilinks]] clicáveis). Evolução do TextEditorModal p/ o second brain. */
export default function NoteEditor({
  filename, content, isNew, saving,
  onChange, onSave, onClose, onNavigate,
}: {
  filename: string;
  content: string;
  isNew: boolean;
  saving: boolean;
  onChange: (patch: { filename?: string; content?: string }) => void;
  onSave: () => void;
  onClose: () => void;
  /** clique num [[wikilink]] no preview: abre (ou oferece criar) a nota alvo */
  onNavigate: (title: string) => void;
}) {
  const [tab, setTab] = useState<"edit" | "view">(isNew ? "edit" : "view");

  function interceptWikilink(e: React.MouseEvent) {
    const a = (e.target as HTMLElement).closest("a");
    const href = a?.getAttribute("href") || "";
    if (href.startsWith("#note:")) {
      e.preventDefault();
      e.stopPropagation();
      onNavigate(decodeURIComponent(href.slice("#note:".length)));
    }
  }

  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/60 p-4" onClick={onClose}>
      <div onClick={(e) => e.stopPropagation()} className="flex max-h-[85vh] min-h-[420px] w-full max-w-3xl flex-col overflow-hidden rounded-2xl border border-border bg-surface shadow-2xl">
        <div className="flex items-center gap-2 border-b border-border px-4 py-3">
          <Brain size={18} className="text-accent-hover" />
          {isNew ? (
            <input
              value={filename} onChange={(e) => onChange({ filename: e.target.value })}
              placeholder="Título da nota"
              className="flex-1 bg-transparent text-sm font-medium text-ink outline-none placeholder:text-muted"
            />
          ) : (
            <span className="flex-1 truncate text-sm font-semibold text-ink">{filename.replace(/\.(md|markdown)$/i, "")}</span>
          )}
          <div className="flex items-center rounded-lg border border-border p-0.5">
            <button
              onClick={() => setTab("edit")}
              className={`flex items-center gap-1 rounded-md px-2.5 py-1 text-xs transition-colors ${tab === "edit" ? "bg-surface2 text-ink" : "text-muted hover:text-ink"}`}
            >
              <Pencil size={12} /> Editar
            </button>
            <button
              onClick={() => setTab("view")}
              className={`flex items-center gap-1 rounded-md px-2.5 py-1 text-xs transition-colors ${tab === "view" ? "bg-surface2 text-ink" : "text-muted hover:text-ink"}`}
            >
              <Eye size={12} /> Visualizar
            </button>
          </div>
          <button onClick={onClose} className="rounded-lg p-1 text-muted hover:bg-hover hover:text-ink"><X size={16} /></button>
        </div>

        {tab === "edit" ? (
          <textarea
            autoFocus value={content} onChange={(e) => onChange({ content: e.target.value })}
            placeholder={"Escreva a nota em markdown…\n\nLigue notas relacionadas com [[Título da Outra Nota]]."}
            className="min-h-[320px] flex-1 resize-none bg-transparent px-4 py-3 font-mono text-sm leading-relaxed text-ink outline-none placeholder:text-muted"
          />
        ) : (
          <div onClickCapture={interceptWikilink} className="min-h-[320px] flex-1 overflow-y-auto px-5 py-4 text-sm [&_a[href^='#note:']]:text-accent-hover [&_a[href^='#note:']]:no-underline [&_a[href^='#note:']]:border-b [&_a[href^='#note:']]:border-dashed [&_a[href^='#note:']]:border-accent/50">
            {content.trim() ? (
              <Markdown content={wikilinksToMd(content)} />
            ) : (
              <p className="text-muted">Nota vazia.</p>
            )}
          </div>
        )}

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
