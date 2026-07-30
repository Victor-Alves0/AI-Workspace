"use client";

import { useState } from "react";
import { ChevronLeft, FileText, Info, Plus, Sparkles, Tag, Trash2, X } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import type { Skill, SkillFile } from "@/lib/types";

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
  const [files, setFiles] = useState<SkillFile[]>(skill?.files ?? []);
  const [tags, setTags] = useState<string[]>(skill?.tags ?? []);
  // -1 = conteúdo principal; senão índice do arquivo de referência em edição
  const [active, setActive] = useState(-1);
  const [err, setErr] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  function addFile() {
    const base = "references/novo.md";
    let nm = base;
    for (let i = 2; files.some((f) => f.name === nm); i++) nm = `references/novo_${i}.md`;
    setFiles((fs) => [...fs, { name: nm, content: "" }]);
    setActive(files.length);
  }
  function updateFile(i: number, patch: Partial<SkillFile>) {
    setFiles((fs) => fs.map((f, j) => (j === i ? { ...f, ...patch } : f)));
  }
  function removeFile(i: number) {
    setFiles((fs) => fs.filter((_, j) => j !== i));
    setActive((a) => (a === i ? -1 : a > i ? a - 1 : a));
  }

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
    // arquivos válidos: nome não-vazio; dedupe fica a cargo do backend
    const cleanFiles = files
      .map((f) => ({ name: f.name.trim(), content: f.content }))
      .filter((f) => f.name);
    const body = { slug: id, name: name.trim(), description, content, files: cleanFiles, tags, enabled: skill?.enabled ?? true };
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

          {/* arquivos de referência: o SKILL.md é o índice; estes carregam sob demanda */}
          <div>
            <div className="mb-1.5 flex items-center justify-between">
              <FieldLabel>Arquivos de referência</FieldLabel>
              <button
                onClick={addFile}
                className="flex items-center gap-1 rounded-lg px-1.5 py-0.5 text-xs text-muted transition-colors hover:bg-hover hover:text-ink"
              >
                <Plus size={12} /> novo
              </button>
            </div>
            <div className="space-y-1">
              <button
                onClick={() => setActive(-1)}
                className={`flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left text-sm transition-colors ${active === -1 ? "bg-accent/15 text-accent-hover" : "text-ink-soft hover:bg-hover"}`}
              >
                <FileText size={13} className="shrink-0" />
                <span className="truncate font-medium">SKILL.md</span>
                <span className="ml-auto shrink-0 text-[10px] text-muted">índice</span>
              </button>
              {files.map((f, i) => (
                <div
                  key={i}
                  className={`group flex items-center gap-2 rounded-lg px-2.5 py-1.5 text-sm transition-colors ${active === i ? "bg-accent/15 text-accent-hover" : "text-ink-soft hover:bg-hover"}`}
                >
                  <FileText size={13} className="shrink-0" />
                  <button onClick={() => setActive(i)} className="min-w-0 flex-1 truncate text-left font-mono text-xs">
                    {f.name || "sem nome"}
                  </button>
                  <button
                    onClick={() => removeFile(i)}
                    className="shrink-0 text-muted opacity-0 transition-opacity hover:text-red-400 group-hover:opacity-100"
                    title="Remover arquivo"
                  >
                    <Trash2 size={12} />
                  </button>
                </div>
              ))}
              {files.length === 0 && (
                <p className="px-2.5 py-1 text-xs leading-5 text-muted">
                  Nenhum. O modelo carrega cada arquivo sob demanda via <span className="font-mono">view_skill</span>.
                </p>
              )}
            </div>
          </div>

          <div className="flex items-start gap-2.5 rounded-xl border border-accent/25 bg-accent/10 px-4 py-3 text-xs leading-5 text-ink-soft">
            <Info size={14} className="mt-0.5 shrink-0 text-accent-hover" />
            <span>
              Lazy loading: o modelo recebe só o <b>nome</b> e a <b>descrição</b>. O
              SKILL.md é carregado quando ele chama <span className="font-mono">view_skill</span>, e
              cada arquivo de referência só quando ele pede por nome — então detalhe e
              anexos não encarecem os turnos em que a skill não é usada.
            </span>
          </div>
        </aside>

        {/* área principal: SKILL.md ou o arquivo de referência selecionado */}
        <main className="flex min-w-0 flex-1 flex-col p-5">
          {active === -1 ? (
            <>
              <FieldLabel>SKILL.md — conteúdo principal (carregado sob demanda)</FieldLabel>
              <textarea
                value={content}
                onChange={(e) => setContent(e.target.value)}
                placeholder="# Instruções completas da skill…"
                className="w-full flex-1 resize-none rounded-xl border border-border bg-surface px-4 py-3 font-mono text-sm leading-6 text-ink outline-none transition-colors focus:border-accent/60 placeholder:text-muted"
              />
            </>
          ) : (
            <>
              <FieldLabel>Arquivo de referência</FieldLabel>
              <input
                value={files[active]?.name ?? ""}
                onChange={(e) => updateFile(active, { name: e.target.value })}
                placeholder="references/exemplo.md"
                className={`${inputCls} mb-2 font-mono`}
              />
              <textarea
                value={files[active]?.content ?? ""}
                onChange={(e) => updateFile(active, { content: e.target.value })}
                placeholder="Conteúdo do arquivo de referência…"
                className="w-full flex-1 resize-none rounded-xl border border-border bg-surface px-4 py-3 font-mono text-sm leading-6 text-ink outline-none transition-colors focus:border-accent/60 placeholder:text-muted"
              />
            </>
          )}
        </main>
      </div>
    </div>
  );
}
