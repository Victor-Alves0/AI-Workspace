"use client";

import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { Loader2 } from "lucide-react";

/** Anexo de TEXTO aberto numa janela, sem sair da conversa (como o "Texto colado" do
 *  ChatGPT). No compositor ele é editável — salvar troca o anexo antes do envio; numa
 *  mensagem já enviada é só leitura. */
export default function TextAttachmentModal({
  name,
  url,
  editable = false,
  onClose,
  onSave,
}: {
  name: string;
  url: string;
  editable?: boolean;
  onClose: () => void;
  onSave?: (text: string) => Promise<void> | void;
}) {
  const [original, setOriginal] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let vivo = true;
    fetch(url, { credentials: "include" })
      .then(async (r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.text();
      })
      .then((t) => { if (vivo) { setOriginal(t); setText(t); } })
      .catch(() => { if (vivo) setError("Não foi possível abrir o anexo."); });
    return () => { vivo = false; };
  }, [url]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const mudou = original !== null && text !== original;

  async function salvar() {
    if (!onSave || !mudou) return;
    setSaving(true);
    try {
      await onSave(text);
      onClose();
    } catch {
      setError("Não foi possível salvar as alterações.");
    } finally {
      setSaving(false);
    }
  }

  return createPortal(
    <div
      onClick={onClose}
      className="fixed inset-0 z-[100] flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm"
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="animate-pop flex h-[min(85dvh,760px)] w-full max-w-3xl flex-col rounded-2xl border border-border bg-surface shadow-2xl"
      >
        <p className="truncate border-b border-border px-5 py-3.5 text-base font-semibold text-ink" title={name}>
          {name}
        </p>
        <div className="min-h-0 flex-1 px-5 py-3">
          {original === null && !error ? (
            <div className="flex h-full items-center justify-center text-muted">
              <Loader2 size={20} className="animate-spin" />
            </div>
          ) : (
            <textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              readOnly={!editable}
              spellCheck={false}
              className="h-full w-full resize-none bg-transparent text-[15px] leading-7 text-ink outline-none"
            />
          )}
        </div>
        <div className="flex items-center justify-end gap-2.5 border-t border-border px-5 py-3.5">
          {error && <p className="mr-auto text-xs text-red-400">{error}</p>}
          <button
            onClick={onClose}
            className="rounded-full border border-border px-5 py-2 text-sm text-ink transition-colors hover:bg-hover"
          >
            Fechar
          </button>
          {editable && (
            <button
              onClick={salvar}
              disabled={!mudou || saving}
              className="rounded-full bg-ink px-5 py-2 text-sm font-medium text-bg transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40"
            >
              {saving ? "Salvando…" : "Salvar alterações"}
            </button>
          )}
        </div>
      </div>
    </div>,
    document.body,
  );
}
