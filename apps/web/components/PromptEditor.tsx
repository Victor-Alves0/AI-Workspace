"use client";

import { useState } from "react";
import { ChevronLeft, Info, Lock } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import type { Prompt } from "@/lib/types";

function slugify(s: string) {
  return s.toLowerCase().trim().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
}

export default function PromptEditor({
  prompt,
  onClose,
  onSaved,
}: {
  prompt: Prompt | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const isNew = prompt === null;
  const [title, setTitle] = useState(prompt?.title ?? "");
  const [command, setCommand] = useState(prompt?.command ?? "");
  const [commandDirty, setCommandDirty] = useState(!isNew);
  const [content, setContent] = useState(prompt?.content ?? "");
  const [err, setErr] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  async function save() {
    setErr(null);
    const cmd = command.trim();
    if (!title.trim()) {
      setErr("Dê um nome ao prompt");
      return;
    }
    if (!cmd) {
      setErr("Defina um comando");
      return;
    }
    const body = { command: cmd, title: title.trim(), content, enabled: prompt?.enabled ?? true };
    setSaving(true);
    try {
      if (isNew) await api.post("/prompts", body);
      else await api.patch(`/prompts/${prompt!.id}`, body);
      onSaved();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Falha ao salvar");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="flex h-full flex-1 flex-col bg-bg">
      {/* topo */}
      <div className="flex items-center gap-2 px-6 pt-4">
        <button onClick={onClose} className="rounded-lg p-1 text-muted hover:bg-hover hover:text-ink">
          <ChevronLeft size={22} />
        </button>
        <span className="text-sm text-muted">Voltar</span>
      </div>

      <div className="flex-1 overflow-y-auto px-6 py-4">
        <div className="mx-auto max-w-3xl space-y-3">
          <div className="flex items-start justify-between gap-3">
            <input
              value={title}
              onChange={(e) => {
                setTitle(e.target.value);
                if (!commandDirty) setCommand(slugify(e.target.value));
              }}
              placeholder="Nome"
              className="w-full bg-transparent text-3xl font-bold text-ink outline-none placeholder:text-muted"
            />
            <button className="flex shrink-0 items-center gap-1.5 rounded-full border border-border px-3 py-1.5 text-xs text-muted hover:text-ink">
              <Lock size={13} /> Acesso
            </button>
          </div>

          <div className="flex items-center gap-1 text-sm text-muted">
            <span>/</span>
            <input
              value={command}
              onChange={(e) => {
                setCommandDirty(true);
                setCommand(e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, "-"));
              }}
              placeholder="comando"
              className="w-full bg-transparent font-mono text-ink-soft outline-none placeholder:text-muted"
            />
          </div>

          <div className="space-y-1 pt-3">
            <p className="text-xs text-muted">Conteúdo do Prompt</p>
            <textarea
              rows={10}
              value={content}
              onChange={(e) => setContent(e.target.value)}
              placeholder="Escreva um resumo em 50 palavras que resuma {{topic}}."
              className="w-full resize-y rounded-lg border border-border bg-surface px-3 py-2 text-sm text-ink outline-none focus:border-accent placeholder:text-muted"
            />
            <p className="flex items-center gap-1 text-xs text-muted">
              <Info size={12} /> Usar <span className="font-mono text-ink-soft">{"{{variável}}"}</span> para espaços reservados
            </p>
          </div>
        </div>
      </div>

      {/* rodapé */}
      <div className="flex items-center justify-end gap-3 border-t border-border px-6 py-3">
        {err && <span className="text-xs text-red-400">{err}</span>}
        <button onClick={save} disabled={saving} className="rounded-full bg-white px-6 py-2 text-sm font-medium text-black hover:opacity-90 disabled:opacity-60">
          {saving ? "…" : "Salvar e Criar"}
        </button>
      </div>
    </div>
  );
}
