"use client";

import { useState } from "react";
import { ChevronLeft, Info, Sparkles, Tag, X } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import type { Skill } from "@/lib/types";

const NEW_CONTENT = `# Título da skill

Descreva aqui, em detalhe, o passo a passo/instruções que o modelo deve seguir
quando carregar esta skill. Este conteúdo só é entregue ao modelo sob demanda
(quando ele chama view_skill) — pode ser longo sem encarecer todo turno.
`;

function slugify(s: string) {
  return s.toLowerCase().trim().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
}

const inputCls =
  "w-full rounded-xl border border-border bg-surface px-3 py-2 text-sm text-ink outline-none transition-colors placeholder:text-muted focus:border-accent/60";

function FieldLabel({ children }: { children: React.ReactNode }) {
  return (
    <span className="mb-1.5 block text-[11px] font-medium uppercase tracking-wider text-muted">
      {children}
    </span>
  );
}

/* input de tags em chips (mesmo padrão do ToolEditor) */
function TagsInput({ tags, onChange }: { tags: string[]; onChange: (t: string[]) => void }) {
  const [draft, setDraft] = useState("");
  function add(raw: string) {
    const t = raw.trim().toLowerCase().replace(/,+$/, "");
    if (t && !tags.includes(t)) onChange([...tags, t]);
    setDraft("");
  }
  return (
    <div className="flex flex-wrap items-center gap-1.5 rounded-xl border border-border bg-surface px-2.5 py-2 transition-colors focus-within:border-accent/60">
      {tags.map((t) => (
        <span key={t} className="flex items-center gap-1 rounded-full bg-accent/15 px-2.5 py-0.5 text-xs text-accent-hover">
          {t}
          <button onClick={() => onChange(tags.filter((x) => x !== t))} className="text-accent-hover/70 transition-colors hover:text-accent-hover">
            <X size={11} />
          </button>
        </span>
      ))}
      <input
        value={draft}
        onChange={(e) => {
          if (e.target.value.endsWith(",")) add(e.target.value);
          else setDraft(e.target.value);
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            add(draft);
          } else if (e.key === "Backspace" && !draft && tags.length) {
            onChange(tags.slice(0, -1));
          }
        }}
        placeholder={tags.length ? "" : "adicionar tag…"}
        className="min-w-[90px] flex-1 bg-transparent text-sm text-ink outline-none placeholder:text-muted"
      />
    </div>
  );
}

export default function SkillEditor({
  skill,
  onClose,
  onSaved,
}: {
  skill: Skill | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const isNew = skill === null;
  const [name, setName] = useState(skill?.name ?? "");
  const [slug, setSlug] = useState(skill?.slug ?? "");
  const [description, setDescription] = useState(skill?.description ?? "");
  const [content, setContent] = useState(skill?.content ?? (isNew ? NEW_CONTENT : ""));
  const [tags, setTags] = useState<string[]>(skill?.tags ?? []);
  const [err, setErr] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  async function save() {
    setErr(null);
    const id = (slug || slugify(name)).trim();
    if (!id) {
      setErr("Defina um identificador para a skill");
      return;
    }
    if (!name.trim()) {
      setErr("Defina um nome para a skill");
      return;
    }
    const body = { slug: id, name: name.trim(), description, content, tags, enabled: skill?.enabled ?? true };
    setSaving(true);
    try {
      if (isNew) await api.post("/skills", body);
      else await api.patch(`/skills/${skill!.id}`, body);
      onSaved();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Falha ao salvar");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="flex h-full flex-1 flex-col bg-bg">
      {/* header */}
      <div className="flex items-center gap-3 border-b border-border px-5 py-3">
        <button onClick={onClose} className="rounded-lg p-1.5 text-muted transition-colors hover:bg-hover hover:text-ink">
          <ChevronLeft size={20} />
        </button>
        <span className="flex items-center gap-2 text-accent-hover">
          <Sparkles size={18} />
        </span>
        <input
          value={name}
          onChange={(e) => {
            setName(e.target.value);
            if (isNew && !slug) setSlug(slugify(e.target.value));
          }}
          placeholder={isNew ? "Nova skill" : "Nome da skill"}
          className="min-w-0 flex-1 bg-transparent text-xl font-semibold tracking-tight text-ink outline-none placeholder:text-muted"
        />
        {err && <span className="max-w-[320px] truncate text-xs text-red-400">{err}</span>}
        <button
          onClick={save}
          disabled={saving}
          className="rounded-full bg-accent px-6 py-2 text-sm font-medium text-white transition-colors hover:bg-accent-hover disabled:opacity-60"
        >
          {saving ? "…" : "Salvar"}
        </button>
      </div>

      <div className="flex min-h-0 flex-1">
        {/* coluna do formulário */}
        <aside className="w-[340px] shrink-0 space-y-5 overflow-y-auto border-r border-border p-5">
          <div>
            <FieldLabel>Identificador ($)</FieldLabel>
            <input
              value={slug}
              onChange={(e) => setSlug(e.target.value.toLowerCase())}
              placeholder="revisao_de_codigo"
              className={`${inputCls} font-mono`}
            />
            <p className="mt-1.5 text-xs leading-5 text-muted">
              Invocada no chat com <span className="font-mono text-ink-soft">${slug || "identificador"}</span>.
            </p>
          </div>

          <div>
            <FieldLabel>Descrição (sempre visível ao modelo)</FieldLabel>
            <textarea
              rows={4}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="O que a skill faz — o modelo usa isto para decidir quando carregá-la"
              className={`${inputCls} resize-y leading-5`}
            />
          </div>

          <div>
            <FieldLabel>
              <span className="flex items-center gap-1"><Tag size={11} /> Tags</span>
            </FieldLabel>
            <TagsInput tags={tags} onChange={setTags} />
          </div>

          <div className="flex items-start gap-2.5 rounded-xl border border-accent/25 bg-accent/10 px-4 py-3 text-xs leading-5 text-ink-soft">
            <Info size={14} className="mt-0.5 shrink-0 text-accent-hover" />
            <span>
              Lazy loading: o modelo recebe só o <b>nome</b> e a <b>descrição</b>. O
              conteúdo completo só é carregado quando ele chama <span className="font-mono">view_skill</span> —
              então pode ser detalhado sem encarecer os turnos em que a skill não é usada.
            </span>
          </div>
        </aside>

        {/* área principal: conteúdo da skill */}
        <main className="min-w-0 flex-1 p-5">
          <FieldLabel>Conteúdo (carregado sob demanda)</FieldLabel>
          <textarea
            value={content}
            onChange={(e) => setContent(e.target.value)}
            placeholder="# Instruções completas da skill…"
            className="h-[calc(100%-2rem)] w-full resize-none rounded-xl border border-border bg-surface px-4 py-3 font-mono text-sm leading-6 text-ink outline-none transition-colors focus:border-accent/60 placeholder:text-muted"
          />
        </main>
      </div>
    </div>
  );
}
