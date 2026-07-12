"use client";

import { useEffect, useRef, useState } from "react";
import { Ban, Bold, BookmarkPlus, Brain, ChevronDown, ChevronRight, Copy, Check, FileText, Heading1, Heading2, Info, Italic, List, ListOrdered, Mail, Pencil, Play, RotateCcw, Send, ShieldAlert, Strikethrough, TriangleAlert, Trash2, Underline, Volume2, Wrench } from "lucide-react";
import type { BrainNoteEvent, ChartSpec, ChatArtifact, DeepResearch, Message, SkillProposal, StockQuote, ToolEvent } from "@/lib/types";
import { api, ApiError, API_URL } from "@/lib/api";
import Markdown from "./Markdown";
import ExcalidrawCanvas from "./ExcalidrawCanvas";
import StockCard from "./StockCard";
import ChartView from "./ChartView";
import DeepResearchCard from "./DeepResearchCard";

// artefatos visuais que uma ferramenta pode emitir (resultado compacto → o front
// desenha). O modelo pode chamar via `execute_tool` (resultado no topo) ou via
// `run_code` (aninhado em `output`), então a varredura é recursiva.
// (o artefato "ask" — kind:"ask" — NÃO é renderizado aqui: aparece acima da
// promptbox, tratado no chat/page.tsx.)
type Artifact =
  | { kind: "excalidraw"; data: { mermaid: string; title?: string } }
  | { kind: "chart"; data: ChartSpec }
  | { kind: "stock_card"; data: StockQuote }
  | { kind: "deep_research"; data: DeepResearch }
  | { kind: "image"; data: { url: string; prompt?: string } }
  | { kind: "email_draft"; data: EmailDraft }
  | { kind: "skill_proposal"; data: SkillProposal }
  | { kind: "brain_note"; data: BrainNoteEvent };

type EmailDraft = {
  draft_id: string; to: string; cc?: string; subject?: string;
  body?: string; account?: string; account_email?: string;
};

function collect(node: unknown, out: Artifact[], seen: Set<string>, depth = 0): void {
  if (node == null || depth > 6) return;
  if (Array.isArray(node)) { node.forEach((x) => collect(x, out, seen, depth + 1)); return; }
  if (typeof node !== "object") return;
  const o = node as Record<string, unknown>;
  const kind = o.kind;
  if (kind === "excalidraw" && typeof o.mermaid === "string" && o.mermaid.trim()) {
    if (!seen.has("x:" + o.mermaid)) { seen.add("x:" + o.mermaid); out.push({ kind, data: { mermaid: o.mermaid, title: typeof o.title === "string" ? o.title : undefined } }); }
    return;
  }
  if (kind === "chart" && Array.isArray(o.series)) {
    const key = "c:" + JSON.stringify(o.series).slice(0, 200);
    if (!seen.has(key)) { seen.add(key); out.push({ kind, data: o as unknown as ChartSpec }); }
    return;
  }
  if (kind === "stock_card" && typeof o.symbol === "string") {
    const key = "s:" + o.symbol + ":" + (o.range ?? "");
    if (!seen.has(key)) { seen.add(key); out.push({ kind, data: o as unknown as StockQuote }); }
    return;
  }
  if (kind === "deep_research" && Array.isArray(o.sources)) {
    const key = "r:" + (o.query ?? "") + ":" + o.sources.length;
    if (!seen.has(key)) { seen.add(key); out.push({ kind, data: o as unknown as DeepResearch }); }
    return;
  }
  if (kind === "image" && typeof o.url === "string" && o.url) {
    if (!seen.has("i:" + o.url)) { seen.add("i:" + o.url); out.push({ kind, data: { url: o.url, prompt: typeof o.prompt === "string" ? o.prompt : undefined } }); }
    return;
  }
  if (kind === "email_draft" && typeof o.draft_id === "string") {
    if (!seen.has("e:" + o.draft_id)) { seen.add("e:" + o.draft_id); out.push({ kind, data: o as unknown as EmailDraft }); }
    return;
  }
  if (kind === "skill_proposal" && typeof o.proposal_id === "string") {
    if (!seen.has("sp:" + o.proposal_id)) { seen.add("sp:" + o.proposal_id); out.push({ kind, data: o as unknown as SkillProposal }); }
    return;
  }
  if (kind === "brain_note" && typeof o.doc_id === "string") {
    const key = "bn:" + o.doc_id + ":" + (o.action ?? "") + ":" + String(o.preview ?? "").slice(0, 60);
    if (!seen.has(key)) { seen.add(key); out.push({ kind, data: o as unknown as BrainNoteEvent }); }
    return;
  }
  for (const v of Object.values(o)) collect(v, out, seen, depth + 1);
}

/** Artefatos visuais (Excalidraw / gráfico / card de ação) gerados neste segmento. */
function toolArtifacts(events: ToolEvent[]): Artifact[] {
  const out: Artifact[] = [];
  const seen = new Set<string>();
  for (const e of events) if (e.kind === "result") collect(e.data, out, seen);
  return out;
}

// ---------------------------------------------------------------------------
// Citações de fonte: URLs vindas das ferramentas (pesquisa profunda, busca na
// web, leitura de página). Viram a barra "Fontes" e linkificam os [n] do texto.
// ---------------------------------------------------------------------------
type Source = { title: string; url: string };

function collectSources(events: ToolEvent[]): Source[] {
  const out: Source[] = [];
  const seen = new Set<string>();
  const add = (title: unknown, url: unknown) => {
    if (typeof url !== "string" || !url) return;
    // fontes da Base de Conhecimento vêm com URL relativa à API (/knowledge/docs/…)
    let u = url;
    if (u.startsWith("/")) u = API_URL + u;
    else if (!/^https?:\/\//i.test(u)) return;
    if (seen.has(u)) return;
    seen.add(u);
    out.push({ title: typeof title === "string" && title.trim() ? title.trim() : u, url: u });
  };
  // varre um resultado procurando fontes conhecidas (funciona aninhado no run_code)
  const scan = (node: unknown, wantDeep: boolean, depth = 0): void => {
    if (node == null || depth > 6) return;
    if (Array.isArray(node)) { node.forEach((x) => scan(x, wantDeep, depth + 1)); return; }
    if (typeof node !== "object") return;
    const o = node as Record<string, unknown>;
    if (wantDeep) {
      if (o.kind === "deep_research" && Array.isArray(o.sources)) {
        for (const s of o.sources as Record<string, unknown>[]) add(s?.title, s?.url);
      }
      // Base de Conhecimento: fontes (documentos) na ordem citada [n]
      if (o.kind === "knowledge" && Array.isArray(o.sources)) {
        for (const s of o.sources as Record<string, unknown>[]) add(s?.title, s?.url);
      }
    } else {
      if (Array.isArray(o.results)) {
        for (const r of o.results as Record<string, unknown>[]) add(r?.title, r?.url);
      }
      if (typeof o.url === "string" && (typeof o.title === "string" || typeof o.text === "string") && o.kind !== "image") {
        add(o.title, o.url); // web.page.read
      }
    }
    for (const v of Object.values(o)) scan(v, wantDeep, depth + 1);
  };
  // pesquisa profunda PRIMEIRO: o brief dela cita [n] na ordem das próprias fontes
  for (const e of events) if (e.kind === "result") scan(e.data, true);
  for (const e of events) if (e.kind === "result") scan(e.data, false);
  return out;
}

/** Torna os [n] do texto clicáveis, apontando p/ a fonte n — sem tocar em blocos
 *  de código (split pelas cercas ```) nem em links já formados (`[n](`). */
function linkifyCitations(content: string, sources: Source[]): string {
  if (!sources.length || !/\[\d{1,2}\]/.test(content)) return content;
  const parts = content.split(/(```[\s\S]*?(?:```|$)|`[^`\n]*`)/);
  return parts
    .map((seg, i) => {
      if (i % 2 === 1) return seg; // dentro de cerca de código
      return seg.replace(/(?<![\w\]])\[(\d{1,2})\](?!\()/g, (m, d: string) => {
        const n = Number(d);
        return n >= 1 && n <= sources.length ? `[[${d}]](${sources[n - 1].url})` : m;
      });
    })
    .join("");
}

function srcHost(u: string): string {
  try { return new URL(u).hostname.replace(/^www\./, ""); } catch { return u; }
}

/** Barra "Fontes": chips numerados com favicon + domínio, linkando a origem. */
function SourcesBar({ sources }: { sources: Source[] }) {
  const [all, setAll] = useState(false);
  const shown = all ? sources : sources.slice(0, 6);
  return (
    <div className="mt-2.5 flex flex-wrap items-center gap-1.5">
      <span className="mr-0.5 text-[11px] font-medium uppercase tracking-wider text-muted">Fontes</span>
      {shown.map((s, i) => {
        const isDoc = s.url.includes("/knowledge/docs/");
        return (
          <a
            key={s.url}
            href={s.url}
            target="_blank"
            rel="noreferrer noopener"
            title={s.title}
            className="flex max-w-[240px] items-center gap-1.5 rounded-full border border-border bg-surface px-2.5 py-1 text-xs text-ink-soft transition-colors hover:border-accent/40 hover:text-ink"
          >
            <span className="text-[10px] tabular-nums text-muted">{i + 1}</span>
            {isDoc ? (
              <FileText size={13} className="shrink-0 text-muted" />
            ) : (
              /* eslint-disable-next-line @next/next/no-img-element */
              <img
                src={`https://www.google.com/s2/favicons?sz=32&domain=${srcHost(s.url)}`}
                alt=""
                loading="lazy"
                className="h-3.5 w-3.5 rounded-sm"
              />
            )}
            <span className="truncate">{isDoc ? s.title : srcHost(s.url)}</span>
          </a>
        );
      })}
      {sources.length > 6 && (
        <button onClick={() => setAll((v) => !v)} className="text-xs text-muted transition-colors hover:text-ink">
          {all ? "menos" : `+${sources.length - 6}`}
        </button>
      )}
    </div>
  );
}

function renderArtifact(a: Artifact, key: React.Key) {
  return a.kind === "excalidraw" ? (
    <ExcalidrawCanvas key={key} mermaid={a.data.mermaid} title={a.data.title} />
  ) : a.kind === "stock_card" ? (
    <StockCard key={key} quote={a.data} />
  ) : a.kind === "deep_research" ? (
    <DeepResearchCard key={key} data={a.data} />
  ) : a.kind === "image" ? (
    <ImageCard key={key} url={a.data.url} prompt={a.data.prompt} />
  ) : a.kind === "email_draft" ? (
    <EmailComposer key={key} draft={a.data} />
  ) : a.kind === "skill_proposal" ? (
    <SkillProposalCard key={key} proposal={a.data} />
  ) : a.kind === "brain_note" ? (
    <BrainNoteCard key={key} note={a.data} />
  ) : (
    <ChartView key={key} spec={a.data} />
  );
}

function escapeHtml(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/\n/g, "<br>");
}

/** Botão da barra de formatação. onMouseDown+preventDefault mantém a seleção do
 *  editor (senão o clique tira o foco antes do execCommand). */
function FmtBtn({ icon, cmd, arg, title, run }: {
  icon: React.ReactNode; cmd?: string; arg?: string; title: string; run: (cmd: string, arg?: string) => void;
}) {
  return (
    <button
      title={title}
      onMouseDown={(e) => { e.preventDefault(); if (cmd) run(cmd, arg); }}
      className="flex h-7 w-7 items-center justify-center rounded text-muted transition-colors hover:bg-surface2 hover:text-ink"
    >
      {icon}
    </button>
  );
}

/** Rascunho de e-mail editável com formatação rica (envia HTML). O usuário revisa,
 *  formata e envia (rota direta). "Enviado" fica no localStorage (por draft_id). */
function EmailComposer({ draft }: { draft: EmailDraft }) {
  const sentKey = "email_sent:" + draft.draft_id;
  const bodyRef = useRef<HTMLDivElement>(null);
  const [to, setTo] = useState(draft.to || "");
  const [cc, setCc] = useState(draft.cc || "");
  const [subject, setSubject] = useState(draft.subject || "");
  const [showCc, setShowCc] = useState(!!(draft.cc || "").trim());
  const [count, setCount] = useState({ words: 0, chars: 0 });
  const [status, setStatus] = useState<"idle" | "sending" | "sent" | "error">(
    () => (typeof window !== "undefined" && localStorage.getItem(sentKey) ? "sent" : "idle"),
  );
  const [err, setErr] = useState("");

  // inicializa o editor com o texto do rascunho (uma vez); depois é livre p/ editar.
  useEffect(() => {
    if (bodyRef.current && status !== "sent") {
      bodyRef.current.innerHTML = escapeHtml(draft.body || "");
      updateCount();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function updateCount() {
    const t = bodyRef.current?.innerText || "";
    setCount({ words: t.trim() ? t.trim().split(/\s+/).length : 0, chars: t.length });
  }
  function exec(cmd: string, arg?: string) {
    bodyRef.current?.focus();
    try { document.execCommand(cmd, false, arg); } catch { /* ignore */ }
    updateCount();
  }

  async function send() {
    if (!to.trim() || status === "sending") return;
    setStatus("sending");
    setErr("");
    try {
      await api.post("/integrations/google/gmail/send", {
        account: draft.account || "", to, cc, subject,
        body: bodyRef.current?.innerText || "",   // fallback texto
        html: bodyRef.current?.innerHTML || "",   // versão rica
      });
      try { localStorage.setItem(sentKey, "1"); } catch { /* ignore */ }
      setStatus("sent");
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Falha ao enviar");
      setStatus("error");
    }
  }

  if (status === "sent") {
    return (
      <div className="my-2 max-w-xl rounded-xl border border-border bg-surface px-4 py-3">
        <p className="flex items-center gap-2 text-sm text-green-400">
          <Check size={16} /> E-mail enviado{draft.account_email ? ` de ${draft.account_email}` : ""}.
        </p>
        <p className="mt-1 truncate text-xs text-muted">Para {to} · {subject || "(sem assunto)"}</p>
      </div>
    );
  }

  return (
    <div className="my-2 max-w-xl overflow-hidden rounded-xl border border-border bg-surface">
      <div className="flex items-center gap-2 border-b border-border px-4 py-2">
        <Mail size={15} className="text-accent-hover" />
        <span className="text-sm font-medium text-ink">E-mail</span>
        {draft.account_email && <span className="ml-auto truncate text-xs text-muted">de {draft.account_email}</span>}
      </div>
      <div className="divide-y divide-border">
        <label className="flex items-center gap-2 px-4 py-2 text-sm">
          <span className="w-16 shrink-0 text-muted">Para</span>
          <input value={to} onChange={(e) => setTo(e.target.value)} placeholder="destinatario@exemplo.com" className="flex-1 bg-transparent text-ink outline-none placeholder:text-muted" />
          {!showCc && <button onClick={() => setShowCc(true)} className="shrink-0 text-xs text-muted transition-colors hover:text-ink">Cc</button>}
        </label>
        {showCc && (
          <label className="flex items-center gap-2 px-4 py-2 text-sm">
            <span className="w-16 shrink-0 text-muted">Cc</span>
            <input value={cc} onChange={(e) => setCc(e.target.value)} placeholder="opcional" className="flex-1 bg-transparent text-ink outline-none placeholder:text-muted" />
          </label>
        )}
        <label className="flex items-center gap-2 px-4 py-2 text-sm">
          <span className="w-16 shrink-0 text-muted">Assunto</span>
          <input value={subject} onChange={(e) => setSubject(e.target.value)} placeholder="(sem assunto)" className="flex-1 bg-transparent font-medium text-ink outline-none placeholder:font-normal placeholder:text-muted" />
        </label>
        {/* barra de formatação */}
        <div className="flex flex-wrap items-center gap-0.5 px-3 py-1.5">
          <FmtBtn title="Negrito" icon={<Bold size={15} />} cmd="bold" run={exec} />
          <FmtBtn title="Itálico" icon={<Italic size={15} />} cmd="italic" run={exec} />
          <FmtBtn title="Sublinhado" icon={<Underline size={15} />} cmd="underline" run={exec} />
          <FmtBtn title="Tachado" icon={<Strikethrough size={15} />} cmd="strikeThrough" run={exec} />
          <span className="mx-1 h-4 w-px bg-border" />
          <FmtBtn title="Título 1" icon={<Heading1 size={15} />} cmd="formatBlock" arg="H1" run={exec} />
          <FmtBtn title="Título 2" icon={<Heading2 size={15} />} cmd="formatBlock" arg="H2" run={exec} />
          <span className="mx-1 h-4 w-px bg-border" />
          <FmtBtn title="Lista com marcadores" icon={<List size={15} />} cmd="insertUnorderedList" run={exec} />
          <FmtBtn title="Lista numerada" icon={<ListOrdered size={15} />} cmd="insertOrderedList" run={exec} />
        </div>
        <div
          ref={bodyRef}
          contentEditable
          suppressContentEditableWarning
          onInput={updateCount}
          className="min-h-[140px] w-full px-4 py-3 text-sm leading-relaxed text-ink outline-none [&_a]:text-accent-hover [&_a]:underline [&_h1]:mb-1 [&_h1]:text-lg [&_h1]:font-bold [&_h2]:mb-1 [&_h2]:text-base [&_h2]:font-semibold [&_ol]:list-decimal [&_ol]:pl-5 [&_ul]:list-disc [&_ul]:pl-5"
        />
      </div>
      <div className="flex items-center gap-3 border-t border-border px-4 py-2">
        <span className="text-xs text-muted">{count.words} palavras · {count.chars} chars</span>
        {err && <span className="truncate text-xs text-red-400">{err}</span>}
        <button
          onClick={send}
          disabled={!to.trim() || status === "sending"}
          className="ml-auto flex items-center gap-1.5 rounded-full bg-accent px-4 py-1.5 text-sm font-medium text-white transition-colors hover:bg-accent-hover disabled:opacity-50"
        >
          <Send size={14} /> {status === "sending" ? "Enviando…" : "Enviar"}
        </button>
      </div>
    </div>
  );
}

/** Proposta de skill do /learn: card editável (proposal-only — quem salva é o
 *  usuário via POST /skills). Estado "Aprovada/Descartada" no localStorage. */
function SkillProposalCard({ proposal }: { proposal: SkillProposal }) {
  const savedKey = "skill_saved:" + proposal.proposal_id;
  const dismissKey = "skill_dismissed:" + proposal.proposal_id;
  const [name, setName] = useState(proposal.name || "");
  const [slug, setSlug] = useState(proposal.slug || "");
  const [description, setDescription] = useState(proposal.description || "");
  const [content, setContent] = useState(proposal.content || "");
  const [tags, setTags] = useState((proposal.tags || []).join(", "));
  const [open, setOpen] = useState(false);
  const [err, setErr] = useState("");
  const [status, setStatus] = useState<"idle" | "saving" | "saved" | "dismissed">(() => {
    if (typeof window === "undefined") return "idle";
    if (localStorage.getItem(savedKey)) return "saved";
    if (localStorage.getItem(dismissKey)) return "dismissed";
    return "idle";
  });

  async function approve() {
    if (status === "saving" || !name.trim() || !content.trim()) return;
    setStatus("saving");
    setErr("");
    try {
      await api.post("/skills", {
        slug: slug.trim().toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 64) || "skill",
        name: name.trim(),
        description: description.trim(),
        content,
        tags: tags.split(",").map((t) => t.trim()).filter(Boolean),
        enabled: true,
      });
      try { localStorage.setItem(savedKey, "1"); } catch { /* ignore */ }
      setStatus("saved");
    } catch (e) {
      setErr(
        e instanceof ApiError && e.status === 409
          ? "Já existe uma skill com este identificador — troque o slug."
          : e instanceof ApiError ? e.message : "Falha ao salvar a skill",
      );
      setStatus("idle");
    }
  }

  function dismiss() {
    try { localStorage.setItem(dismissKey, "1"); } catch { /* ignore */ }
    setStatus("dismissed");
  }

  if (status === "saved" || status === "dismissed") {
    return (
      <div className="my-2 max-w-xl rounded-xl border border-border bg-surface px-4 py-3">
        <p className={`flex items-center gap-2 text-sm ${status === "saved" ? "text-green-400" : "text-muted"}`}>
          {status === "saved" ? <Check size={16} /> : <Ban size={16} />}
          {status === "saved" ? "Skill aprovada" : "Proposta descartada"}
          <span className="truncate text-muted">· {name || proposal.name}</span>
        </p>
      </div>
    );
  }

  return (
    <div className="my-2 max-w-xl overflow-hidden rounded-xl border border-border bg-surface">
      <div className="flex items-center gap-2 border-b border-border px-4 py-2">
        <BookmarkPlus size={15} className="text-accent-hover" />
        <span className="text-sm font-medium text-ink">Proposta de skill</span>
        <span className="ml-auto text-xs text-muted">revise e aprove</span>
      </div>
      <div className="divide-y divide-border">
        <label className="flex items-center gap-2 px-4 py-2 text-sm">
          <span className="w-24 shrink-0 text-muted">Nome</span>
          <input value={name} onChange={(e) => setName(e.target.value)} className="flex-1 bg-transparent font-medium text-ink outline-none" />
        </label>
        <label className="flex items-center gap-2 px-4 py-2 text-sm">
          <span className="w-24 shrink-0 text-muted">Identificador</span>
          <span className="text-muted">$</span>
          <input value={slug} onChange={(e) => setSlug(e.target.value)} className="flex-1 bg-transparent font-mono text-xs text-ink outline-none" />
        </label>
        <label className="flex items-start gap-2 px-4 py-2 text-sm">
          <span className="w-24 shrink-0 pt-0.5 text-muted">Quando usar</span>
          <textarea value={description} onChange={(e) => setDescription(e.target.value)} rows={2} className="flex-1 resize-none bg-transparent text-ink outline-none" />
        </label>
        <label className="flex items-center gap-2 px-4 py-2 text-sm">
          <span className="w-24 shrink-0 text-muted">Tags</span>
          <input value={tags} onChange={(e) => setTags(e.target.value)} placeholder="separadas por vírgula" className="flex-1 bg-transparent text-ink outline-none placeholder:text-muted" />
        </label>
        <div className="px-4 py-2">
          <button onClick={() => setOpen((v) => !v)} className="flex items-center gap-1 text-xs text-muted transition-colors hover:text-ink">
            {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />} Como fazer ({content.length} chars)
          </button>
          {open && (
            <textarea
              value={content}
              onChange={(e) => setContent(e.target.value)}
              rows={12}
              className="mt-2 w-full resize-y rounded-lg border border-border bg-bg px-3 py-2 font-mono text-xs leading-relaxed text-ink outline-none"
            />
          )}
        </div>
      </div>
      <div className="flex items-center gap-3 border-t border-border px-4 py-2">
        {err && <span className="truncate text-xs text-red-400">{err}</span>}
        <button onClick={dismiss} className="ml-auto rounded-full px-3 py-1.5 text-sm text-muted transition-colors hover:text-ink">
          Descartar
        </button>
        <button
          onClick={approve}
          disabled={!name.trim() || !content.trim() || status === "saving"}
          className="flex items-center gap-1.5 rounded-full bg-accent px-4 py-1.5 text-sm font-medium text-white transition-colors hover:bg-accent-hover disabled:opacity-50"
        >
          <Check size={14} /> {status === "saving" ? "Salvando…" : "Aprovar skill"}
        </button>
      </div>
    </div>
  );
}

/** Nota escrita no cérebro pela IA neste turno (card compacto com preview). */
function BrainNoteCard({ note }: { note: BrainNoteEvent }) {
  const href = note.url?.startsWith("/") ? API_URL + note.url : note.url;
  return (
    <div className="my-2 max-w-xl overflow-hidden rounded-xl border border-border bg-surface">
      <div className="flex items-center gap-2 border-b border-border px-4 py-2">
        <Brain size={15} className="text-accent-hover" />
        <span className="text-sm font-medium text-ink">
          {note.action === "updated" ? "Nota atualizada no cérebro" : "Nota criada no cérebro"}
        </span>
        {href && (
          <a href={href} target="_blank" rel="noreferrer noopener" className="ml-auto text-xs text-muted transition-colors hover:text-ink">
            Abrir
          </a>
        )}
      </div>
      <div className="px-4 py-3">
        <p className="text-sm font-medium text-ink">{note.title}</p>
        {note.preview && (
          <p className="mt-1 line-clamp-3 whitespace-pre-wrap text-xs leading-relaxed text-muted">{note.preview}</p>
        )}
      </div>
    </div>
  );
}

/** Card de uma imagem gerada pela IA (servida por /images/{id}?t=…). */
function ImageCard({ url, prompt }: { url: string; prompt?: string }) {
  const src = url.startsWith("http") ? url : `${API_URL}${url}`;
  return (
    <div className="my-2 max-w-md overflow-hidden rounded-xl border border-border bg-surface">
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <a href={src} target="_blank" rel="noreferrer noopener">
        <img src={src} alt={prompt || "imagem gerada"} loading="lazy" className="block h-auto w-full" />
      </a>
      {prompt && <p className="truncate px-3 py-1.5 text-xs text-muted" title={prompt}>{prompt}</p>}
    </div>
  );
}

// A IA pode posicionar um artefato inline escrevendo um marcador no texto:
//   [[diagram]] (Excalidraw) · [[chart]] (gráfico) · [[quote]]/[[stock]] (card de
//   ação) · [[canvas]] (qualquer, na ordem). Sem marcador → renderiza no fim.
const MARKER_SRC = "\\[\\[(canvas|diagram|chart|quote|stock|research|image|email)\\]\\]";
const KIND_OF: Record<string, Artifact["kind"] | null> = {
  diagram: "excalidraw", chart: "chart", quote: "stock_card", stock: "stock_card",
  research: "deep_research", image: "image", email: "email_draft", canvas: null,
};

// marcador dos ARTEFATOS DE CHAT (janela dedicada): o servidor troca o bloco
// <artifact> por [[artifact:slug]] ao persistir; aqui vira um cartão clicável.
const CHAT_ART_RE = /\[\[artifact:([\w-]+)\]\]/gi;

function ArtifactChip({
  identifier, meta, onOpen,
}: {
  identifier: string;
  meta?: Pick<ChatArtifact, "title" | "kind" | "version">;
  onOpen?: (identifier: string) => void;
}) {
  return (
    <button
      onClick={() => onOpen?.(identifier)}
      className="group my-2 flex w-full max-w-md items-center gap-2.5 rounded-xl border border-border bg-surface px-3 py-2.5 text-left transition-colors hover:border-accent/40 hover:bg-hover"
    >
      <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-accent/15 text-accent-hover">
        <FileText size={16} />
      </span>
      <span className="min-w-0">
        <span className="block truncate text-sm font-medium text-ink">{meta?.title || identifier}</span>
        <span className="block text-[11px] text-muted">
          Artefato{meta ? ` · ${meta.kind}${meta.version > 1 ? ` · v${meta.version}` : ""}` : ""} — clique para abrir
        </span>
      </span>
    </button>
  );
}

/** Corpo da mensagem da IA: primeiro separa os cartões de ARTEFATO DE CHAT
 *  ([[artifact:slug]]), depois os marcadores de artefatos de ferramenta. */
function AssistantBody({
  content, artifacts, chatArtifacts = [], onOpenArtifact,
}: {
  content: string;
  artifacts: Artifact[];
  chatArtifacts?: ChatArtifact[];
  onOpenArtifact?: (identifier: string) => void;
}) {
  CHAT_ART_RE.lastIndex = 0;
  if (CHAT_ART_RE.test(content)) {
    const nodes: React.ReactNode[] = [];
    CHAT_ART_RE.lastIndex = 0;
    let last = 0, seg = 0, m: RegExpExecArray | null;
    const seen = new Set<string>();
    while ((m = CHAT_ART_RE.exec(content))) {
      const before = content.slice(last, m.index);
      if (before.trim()) nodes.push(<ToolMarkedBody key={`s${seg++}`} content={before} artifacts={[]} />);
      const ident = m[1].toLowerCase();
      if (!seen.has(ident)) {
        seen.add(ident);
        nodes.push(
          <ArtifactChip
            key={`chip-${ident}-${seg}`}
            identifier={ident}
            meta={chatArtifacts.find((a) => a.identifier === ident)}
            onOpen={onOpenArtifact}
          />,
        );
      }
      last = m.index + m[0].length;
    }
    const tail = content.slice(last);
    if (tail.trim() || artifacts.length) nodes.push(<ToolMarkedBody key={`s${seg++}`} content={tail} artifacts={artifacts} />);
    return <>{nodes}</>;
  }
  return <ToolMarkedBody content={content} artifacts={artifacts} />;
}

/** Markdown + artefatos de FERRAMENTA, interleaved nos marcadores [[chart]] etc. */
function ToolMarkedBody({ content, artifacts }: { content: string; artifacts: Artifact[] }) {
  if (!artifacts.length || !new RegExp(MARKER_SRC, "i").test(content)) {
    return (
      <>
        <Markdown content={content} clamp />
        {artifacts.map((a, i) => renderArtifact(a, i))}
      </>
    );
  }
  const used = new Set<number>();
  const take = (marker: string) => {
    const want = KIND_OF[marker] ?? null;
    for (let i = 0; i < artifacts.length; i++) {
      if (used.has(i)) continue;
      if (want === null || artifacts[i].kind === want) { used.add(i); return i; }
    }
    return -1;
  };
  const nodes: React.ReactNode[] = [];
  const re = new RegExp(MARKER_SRC, "gi");
  let last = 0, seg = 0, m: RegExpExecArray | null;
  while ((m = re.exec(content))) {
    const text = content.slice(last, m.index);
    if (text.trim()) nodes.push(<Markdown key={`t${seg++}`} content={text} clamp />);
    const idx = take(m[1].toLowerCase());
    if (idx >= 0) nodes.push(renderArtifact(artifacts[idx], `a${idx}`));
    last = m.index + m[0].length;
  }
  const tail = content.slice(last);
  if (tail.trim()) nodes.push(<Markdown key={`t${seg++}`} content={tail} clamp />);
  // artefatos sem marcador correspondente vão para o fim
  artifacts.forEach((a, i) => { if (!used.has(i)) nodes.push(renderArtifact(a, `a${i}`)); });
  return <>{nodes}</>;
}

/** Data/hora do envio, mostrada só no hover da mensagem. Omite o dia quando é hoje. */
export function fmtTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const now = new Date();
  const sameDay = d.toDateString() === now.toDateString();
  const hm = d.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
  if (sameDay) return hm;
  const dm = d.toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit" });
  return `${dm} ${hm}`;
}

/** Painel embutido dos usos de ferramenta do segmento (aberto pelo botão de chave).
 *  Ao abrir, rola a si mesmo para a área visível (na última mensagem ele nasceria
 *  escondido atrás do composer flutuante — o scroll-padding do container compensa). */
export function ToolEventsPanel({ events }: { events: ToolEvent[] }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    ref.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, []);
  return (
    <div ref={ref} className="animate-pop mt-1.5 w-full max-w-full space-y-1.5 rounded-xl border border-border bg-surface p-2 shadow-menu">
      {events.map((e, i) => (
        <ToolEventRow key={i} event={e} />
      ))}
    </div>
  );
}

/** Memórias (mem0) injetadas nesta resposta — com desativar/excluir inline. */
function MemoriesUsedPanel({ items }: { items: { id: string; text: string; scope?: string }[] }) {
  const ref = useRef<HTMLDivElement>(null);
  const [gone, setGone] = useState<Record<string, "disabled" | "deleted">>({});
  useEffect(() => { ref.current?.scrollIntoView({ block: "nearest", behavior: "smooth" }); }, []);
  const act = async (id: string, kind: "disabled" | "deleted") => {
    setGone((g) => ({ ...g, [id]: kind }));
    try {
      if (kind === "deleted") await api.del(`/memory/${id}`);
      else await api.post("/memory/bulk", { action: "disable", ids: [id] });
    } catch { setGone((g) => { const n = { ...g }; delete n[id]; return n; }); }
  };
  return (
    <div ref={ref} className="animate-pop mt-1.5 w-full max-w-full space-y-1.5 rounded-xl border border-border bg-surface p-2 shadow-menu">
      <p className="px-1 pb-0.5 text-[11px] font-medium uppercase tracking-wider text-muted">Memórias usadas nesta resposta</p>
      {items.map((m) => (
        <div key={m.id} className="group/mem flex items-start gap-2 rounded-lg border border-border/70 bg-bg px-2.5 py-1.5 text-xs">
          <Brain size={12} className="mt-0.5 shrink-0 text-accent-hover" />
          <span className={`min-w-0 flex-1 ${gone[m.id] ? "text-muted line-through decoration-muted/50" : "text-ink-soft"}`}>{m.text}</span>
          {gone[m.id] ? (
            <span className="shrink-0 text-[10px] uppercase text-muted">{gone[m.id] === "deleted" ? "excluída" : "desativada"}</span>
          ) : (
            <div className="flex shrink-0 items-center gap-1 opacity-0 transition-opacity group-hover/mem:opacity-100">
              <button onClick={() => act(m.id, "disabled")} title="Desativar" className="rounded p-1 text-muted hover:text-ink"><Ban size={12} /></button>
              <button onClick={() => act(m.id, "deleted")} title="Excluir" className="rounded p-1 text-muted hover:text-red-400"><Trash2 size={12} /></button>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

const GUARD_DETECT_LABEL: Record<string, string> = {
  refusal: "recusa detectada",
  empty: "resposta vazia/curta",
  regex: "padrão (regex) casou",
  judge: "juiz LLM acionou",
};

/** Acionamento de um Guarda de saída: escudo + fluxo do que aconteceu
 * (tentativa rejeitada → detecção → reação → nova tentativa). */
function GuardEventRow({ event }: { event: ToolEvent }) {
  const [open, setOpen] = useState(false);
  const d = (event.data ?? {}) as {
    attempt?: number; detect?: string; action?: string; model?: string;
    fallback_model?: string | null; injected?: string | null; rejected_preview?: string | null;
  };
  return (
    <div className="overflow-hidden rounded-lg border border-amber-500/30 bg-amber-500/5 text-xs">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-2.5 py-1.5 text-amber-300/90 transition-colors hover:text-amber-200"
      >
        <ShieldAlert size={12} className="shrink-0" />
        <span className="truncate">guarda de saída · {event.name}</span>
        <span className="ml-auto shrink-0 text-[10px] uppercase tracking-wide text-amber-300/60">
          tentativa {d.attempt ?? "?"}
        </span>
        <ChevronRight size={12} className={`shrink-0 transition-transform duration-150 ${open ? "rotate-90" : ""}`} />
      </button>
      {open && (
        <div className="space-y-1.5 border-t border-amber-500/20 px-2.5 py-2 leading-5 text-ink-soft">
          <p>
            <span className="text-muted">Detecção:</span> {GUARD_DETECT_LABEL[d.detect ?? ""] ?? d.detect}
            {d.model && <> · <span className="text-muted">modelo:</span> <span className="font-mono text-[11px]">{d.model}</span></>}
          </p>
          {d.rejected_preview && (
            <div>
              <p className="text-muted">Resposta rejeitada:</p>
              <p className="mt-0.5 rounded-md bg-bg px-2 py-1.5 italic text-muted">“{d.rejected_preview}”</p>
            </div>
          )}
          <p>
            <span className="text-muted">Reação:</span>{" "}
            {d.action === "fallback_model" ? (
              <>trocou para o modelo <span className="font-mono text-[11px] text-ink">{d.fallback_model}</span> e refez</>
            ) : (
              <>reforçou as instruções e refez</>
            )}
          </p>
          {d.injected && (
            <p className="text-muted">Instrução injetada: <span className="italic text-ink-soft">“{d.injected}”</span></p>
          )}
        </div>
      )}
    </div>
  );
}

function ToolEventRow({ event }: { event: ToolEvent }) {
  const [open, setOpen] = useState(false);
  const preRef = useRef<HTMLPreElement>(null);
  useEffect(() => {
    if (open) preRef.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [open]);
  if (event.kind === "guard") return <GuardEventRow event={event} />;
  const isCall = event.kind === "call";
  return (
    <div className="overflow-hidden rounded-lg border border-border/70 bg-bg text-xs">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-2.5 py-1.5 text-muted transition-colors hover:text-ink"
      >
        {isCall ? (
          <Wrench size={12} className="shrink-0 text-accent-hover" />
        ) : (
          <Check size={12} className="shrink-0 text-green-400" />
        )}
        <span className="font-mono">{isCall ? "chamada" : "resultado"} · {event.name}</span>
        <ChevronRight size={12} className={`ml-auto shrink-0 transition-transform duration-150 ${open ? "rotate-90" : ""}`} />
      </button>
      {open && (
        <pre ref={preRef} className="max-h-56 overflow-auto border-t border-border/70 px-2.5 py-2 font-mono text-[11px] leading-5 text-ink-soft">
          {typeof event.data === "string" ? event.data : JSON.stringify(event.data, null, 2)}
        </pre>
      )}
    </div>
  );
}

function fmtThinkTime(s: number) {
  const v = Math.max(1, Math.round(s));
  if (v < 60) return `${v} segundo${v === 1 ? "" : "s"}`;
  const m = Math.round(v / 60);
  return `${m} minuto${m === 1 ? "" : "s"}`;
}

/** Raciocínio do modelo: colapsado por padrão ("Pensou por Xs"); ao vivo
 *  durante o streaming aparece aberto como "Pensando…". */
export function ReasoningBlock({
  text,
  seconds,
  live = false,
}: {
  text: string;
  seconds?: number;
  live?: boolean;
}) {
  const [open, setOpen] = useState(live);
  const label = live
    ? "Pensando…"
    : seconds && seconds > 0
      ? `Pensou por ${fmtThinkTime(seconds)}`
      : "Pensou";
  return (
    <div className="mb-2">
      <button
        onClick={() => setOpen((v) => !v)}
        className={`flex items-center gap-1 text-sm transition-colors hover:text-ink-soft ${live ? "animate-pulse text-muted" : "text-muted"}`}
      >
        {label}
        <ChevronDown size={14} className={`transition-transform duration-150 ${open ? "" : "-rotate-90"}`} />
      </button>
      {open && (
        <div className="mt-2 whitespace-pre-wrap border-l-2 border-border pl-3 text-sm leading-6 text-muted">
          {text}
        </div>
      )}
    </div>
  );
}

function IconButton({
  title,
  onClick,
  disabled,
  children,
}: {
  title: string;
  onClick: () => void;
  disabled?: boolean;
  children: React.ReactNode;
}) {
  return (
    <button
      title={title}
      onClick={onClick}
      disabled={disabled}
      className="rounded-lg p-1.5 text-muted transition-colors hover:bg-hover hover:text-ink disabled:cursor-not-allowed disabled:opacity-40"
    >
      {children}
    </button>
  );
}

function fmtCost(c: number) {
  if (!c) return "US$ 0";
  if (c < 0.01) return `US$ ${c.toFixed(6)}`;
  return `US$ ${c.toFixed(4)}`;
}

/** Linha "▸ Rótulo  N tokens" que expande p/ mostrar as sub-parcelas.
 * `showZero` (modo Extenso) mantém visíveis também as fontes que não gastaram. */
function BreakdownRow({
  label,
  total,
  parts,
  showZero = false,
  startOpen = false,
}: {
  label: string;
  total: number;
  parts: { label: string; value: number; sub?: boolean }[];
  showZero?: boolean;
  startOpen?: boolean;
}) {
  const [open, setOpen] = useState(startOpen);
  const listRef = useRef<HTMLDivElement>(null);
  // ao expandir, traz as sub-parcelas p/ a área visível (na última mensagem elas
  // nasceriam atrás do composer; o scroll-padding do container compensa)
  useEffect(() => {
    if (open) listRef.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [open]);
  const shown = showZero ? parts : parts.filter((p) => p.value > 0);
  return (
    <div>
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-1.5 rounded-md py-0.5 text-left transition-colors hover:text-ink"
        disabled={!shown.length}
      >
        <ChevronRight size={12} className={`shrink-0 transition-transform duration-150 ${open ? "rotate-90" : ""} ${!shown.length ? "opacity-0" : ""}`} />
        <span className="flex-1">{label}</span>
        <span className="font-medium text-ink">{total.toLocaleString("pt-BR")}</span>
        <span className="text-muted">tokens</span>
      </button>
      {open && shown.length > 0 && (
        <div ref={listRef} className="ml-5 space-y-0.5 border-l border-border pl-3 pt-0.5">
          {shown.map((p) => (
            <div key={p.label} className={`flex items-center justify-between gap-4 ${p.sub ? "pl-3" : ""} ${p.value === 0 ? "opacity-50" : ""}`}>
              <span className="truncate">{p.label}</span>
              <span className="shrink-0 font-mono text-ink-soft">{p.value.toLocaleString("pt-BR")}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function UsagePanel({ u }: { u: NonNullable<Message["usage"]> }) {
  const inb = u.input_breakdown;
  const outb = u.output_breakdown;
  const perTool = Object.entries(u.tools_breakdown ?? {}).sort((a, b) => b[1] - a[1]);
  // Compacto (padrão): esconde fontes que não gastaram. Extenso: mostra TODAS as
  // fontes possíveis + o gasto por ferramenta. Preferência lembrada no navegador.
  const [full, setFull] = useState(false);
  useEffect(() => {
    try { setFull(localStorage.getItem("aw_usage_view") === "full"); } catch {}
  }, []);
  const setMode = (v: boolean) => {
    setFull(v);
    try { localStorage.setItem("aw_usage_view", v ? "full" : "compact"); } catch {}
  };
  const ref = useRef<HTMLDivElement>(null);
  // como o ToolEventsPanel: ao abrir, rola a si mesmo p/ cima do composer
  useEffect(() => {
    ref.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, []);

  // detalhe do "extra" por origem (só no modo Extenso, aninhado sob a linha)
  const EXTRA_LABELS: Record<string, string> = {
    artifacts: "Artefatos (instruções + conteúdo)",
    channel: "Canal (WhatsApp/Telegram)",
    guards: "Guardas de saída (reforços acionados)",
  };
  const perExtra = Object.entries(u.extra_breakdown ?? {}).sort((a, b) => b[1] - a[1]);
  const inputParts = [
    { label: "Usuário (mensagem atual)", value: inb?.user ?? 0 },
    { label: "Contexto (histórico do chat)", value: inb?.context ?? 0 },
    { label: "Prompt do sistema", value: inb?.system ?? 0 },
    { label: "Instruções extras (artefatos/canal/guardas)", value: inb?.extra ?? 0 },
    ...(full ? perExtra.map(([k, v]) => ({ label: EXTRA_LABELS[k] ?? k, value: v, sub: true })) : []),
    { label: "Memória (mem0)", value: inb?.memory ?? 0 },
    { label: "Ferramentas (instruções + schemas)", value: inb?.tools ?? 0 },
    { label: "Skills", value: inb?.skills ?? 0 },
    { label: "Resultados de ferramentas", value: inb?.tool_results ?? 0 },
    // Extenso: o gasto de cada ferramenta, aninhado sob "Resultados"
    ...(full ? perTool.map(([tool, v]) => ({ label: tool, value: v, sub: true })) : []),
    { label: "Arquivos e anexos", value: inb?.file ?? 0 },
  ];
  const outputParts = [
    { label: "Resposta", value: outb?.output ?? Math.max(0, u.completion_tokens - (u.reasoning_tokens ?? 0)) },
    { label: "Raciocínio (thinking)", value: outb?.thinking ?? u.reasoning_tokens ?? 0 },
  ];
  const cached = u.cached_tokens ?? 0;
  const showCached = full || cached > 0;

  return (
    <div ref={ref} className="animate-pop mt-1.5 w-80 max-w-full rounded-xl border border-border bg-surface px-3.5 py-2.5 text-xs text-muted shadow-menu">
      <div className="mb-2 flex items-center justify-between gap-2">
        <p className="min-w-0 truncate text-ink-soft">
          Origem: <span className="text-ink">{u.model_name}</span>{" "}
          <span className="font-mono text-[11px]">({u.model})</span>
        </p>
        {/* Compacto = só o que gastou · Extenso = todas as fontes + por ferramenta */}
        <div className="flex shrink-0 overflow-hidden rounded-md border border-border text-[10px]">
          <button onClick={() => setMode(false)} className={`px-2 py-0.5 transition-colors ${!full ? "bg-surface2 font-medium text-ink" : "text-muted hover:text-ink"}`}>
            Compacto
          </button>
          <button onClick={() => setMode(true)} className={`border-l border-border px-2 py-0.5 transition-colors ${full ? "bg-surface2 font-medium text-ink" : "text-muted hover:text-ink"}`}>
            Extenso
          </button>
        </div>
      </div>

      <div className="space-y-0.5">
        <BreakdownRow label="Entrada" total={u.prompt_tokens} parts={inputParts} showZero={full} startOpen={full} />
        <BreakdownRow label="Saída" total={u.completion_tokens} parts={outputParts} showZero={full} />
        {showCached && (
          <BreakdownRow
            label="Cacheado"
            total={cached}
            showZero={full}
            parts={[
              { label: "Entrada em cache (leitura)", value: cached },
              { label: "Não cacheado", value: Math.max(0, u.prompt_tokens - cached) },
            ]}
          />
        )}
      </div>

      <div className="mt-2 flex items-center justify-between border-t border-border pt-2">
        <span>Total <span className="font-medium text-ink">{u.total_tokens.toLocaleString("pt-BR")}</span></span>
        <span>Custo <span className="text-ink">{fmtCost(u.cost)}</span></span>
      </div>
    </div>
  );
}

export default function MessageItem({
  message,
  onSpeak,
  onEdit,
  onRegenerate,
  onContinue,
  onDelete,
  onRemember,
  busy = false,
  modelName,
  chatArtifacts,
  onOpenArtifact,
}: {
  message: Message;
  onSpeak: (content: string) => void;
  onEdit: (id: string, content: string) => Promise<void>;
  onRegenerate: (id: string) => void;
  onContinue: (id: string) => void;
  onDelete: (id: string) => void;
  /** salva o texto da mensagem como memória no escopo escolhido (null = sem chat ativo) */
  onRemember?: (text: string, scope: "global" | "model" | "chat") => Promise<void>;
  busy?: boolean;
  /** nome exibido acima da mensagem do assistente (fallback qdo não há usage) */
  modelName?: string;
  /** artefatos de chat (janela dedicada) — p/ os cartões [[artifact:slug]] */
  chatArtifacts?: ChatArtifact[];
  onOpenArtifact?: (identifier: string) => void;
}) {
  const isUser = message.role === "user";
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(message.content);
  const [showCost, setShowCost] = useState(false);
  const [showTools, setShowTools] = useState(false);
  const [showMem, setShowMem] = useState(false);
  const [remOpen, setRemOpen] = useState(false);
  const [remSaved, setRemSaved] = useState(false);
  const [copied, setCopied] = useState(false);
  const [saving, setSaving] = useState(false);
  const toolEvents = message.tool_events ?? [];
  const usedTools = toolEvents.length > 0;
  // guardas de saída que agiram nesta resposta (fluxo no painel de ferramentas)
  const guardEvents = toolEvents.filter((e) => e.kind === "guard");
  const usedMemories = message.memories_used ?? [];
  const artifacts = toolArtifacts(toolEvents);
  const sources = isUser ? [] : collectSources(toolEvents);

  async function copy() {
    try {
      await navigator.clipboard.writeText(message.content);
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    } catch {
      /* ignore */
    }
  }

  async function saveEdit() {
    setSaving(true);
    try {
      await onEdit(message.id, draft);
      setEditing(false);
    } finally {
      setSaving(false);
    }
  }

  const u = message.usage;

  const editor = (
    <div className="rounded-2xl border border-border bg-surface p-2 shadow-menu">
      <textarea
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        rows={Math.min(16, Math.max(3, draft.split("\n").length))}
        className="w-full resize-y bg-transparent px-2 py-1 text-sm text-ink outline-none"
      />
      <div className="flex justify-end gap-2 px-1 pt-1">
        <button
          onClick={() => { setEditing(false); setDraft(message.content); }}
          className="rounded-full px-3 py-1.5 text-xs text-muted transition-colors hover:text-ink"
        >
          Cancelar
        </button>
        <button
          onClick={saveEdit}
          disabled={saving}
          className="rounded-full bg-accent px-4 py-1.5 text-xs font-medium text-ink transition-colors hover:bg-accent-hover disabled:opacity-60"
        >
          {saving ? "…" : "Salvar"}
        </button>
      </div>
    </div>
  );

  // mensagem do usuário: bolha compacta à direita
  if (isUser) {
    const atts = message.attachments ?? [];
    return (
      <div className="mx-auto flex max-w-3xl justify-end">
        <div className="group relative max-w-[85%]">
          {editing ? (
            editor
          ) : (
            <>
              {atts.length > 0 && (
                <div className="mb-1.5 flex flex-wrap justify-end gap-1.5">
                  {atts.map((a, i) =>
                    a.type === "image" && a.url ? (
                      // eslint-disable-next-line @next/next/no-img-element
                      <a key={i} href={a.url} target="_blank" rel="noreferrer" title={a.name}>
                        <img src={a.url} alt={a.name} className="max-h-52 max-w-[85%] rounded-xl border border-border object-cover" />
                      </a>
                    ) : a.type === "audio" && a.url ? (
                      // nota de voz/áudio anexado → player nativo compacto
                      // eslint-disable-next-line jsx-a11y/media-has-caption
                      <audio key={i} src={a.url} controls preload="none" className="h-9 max-w-[240px]" />
                    ) : (
                      <span key={i} className="flex items-center gap-1.5 rounded-lg border border-border bg-surface2 px-2.5 py-1 text-xs text-ink-soft">
                        <FileText size={13} className="shrink-0 text-muted" />
                        <span className="max-w-[220px] truncate">{a.name || "arquivo"}</span>
                      </span>
                    ),
                  )}
                </div>
              )}
              {message.content && (
                <div className="whitespace-pre-wrap rounded-2xl rounded-br-md bg-surface2 px-4 py-2.5 text-[15px] leading-7 text-ink [overflow-wrap:anywhere]">
                  {message.content}
                </div>
              )}
              <div className="mt-1 flex items-center justify-end gap-1.5 pr-1 opacity-0 transition-opacity duration-150 group-hover:opacity-100">
                <span className="text-[11px] text-muted">{fmtTime(message.created_at)}</span>
                <button
                  title="Tentar novamente — a IA responde de novo a partir desta mensagem"
                  onClick={() => onRegenerate(message.id)}
                  disabled={busy}
                  className="rounded p-1 text-muted transition-colors hover:bg-hover hover:text-ink disabled:cursor-not-allowed disabled:opacity-40"
                >
                  <RotateCcw size={13} />
                </button>
                <button
                  title="Editar"
                  onClick={() => { setDraft(message.content); setEditing(true); }}
                  className="rounded p-1 text-muted transition-colors hover:bg-hover hover:text-ink"
                >
                  <Pencil size={13} />
                </button>
                <button
                  title="Excluir"
                  onClick={() => onDelete(message.id)}
                  className="rounded p-1 text-muted transition-colors hover:bg-hover hover:text-red-300"
                >
                  <Trash2 size={13} />
                </button>
              </div>
            </>
          )}
        </div>
      </div>
    );
  }

  // mensagem do assistente: texto corrido (markdown), largura total da coluna
  const name = message.usage?.model_name || modelName;
  return (
    <div className="mx-auto max-w-3xl">
      <div className="group relative">
        {name && (
          <p className="mb-1.5 flex items-center gap-1.5 text-lg font-semibold tracking-tight text-ink">
            {name}
            {usedTools && (
              <span title="Ferramentas usadas neste segmento" className="text-muted">
                <Wrench size={15} />
              </span>
            )}
            {guardEvents.length > 0 && (
              <button
                onClick={() => setShowTools(true)}
                title="Um Guarda de saída agiu nesta resposta — clique para ver o fluxo"
                className="flex items-center gap-1 rounded-full bg-amber-500/15 px-2 py-0.5 text-[11px] font-medium text-amber-400 transition-colors hover:bg-amber-500/25"
              >
                <ShieldAlert size={12} /> Guarda{guardEvents.length > 1 ? ` ×${guardEvents.length}` : ""}
              </button>
            )}
            {u?.over_budget ? (
              <span
                title={`Este turno usou ${u.total_tokens.toLocaleString("pt-BR")} tokens (limite de aviso: ${u.over_budget.toLocaleString("pt-BR")})`}
                className="flex items-center gap-1 rounded-full bg-amber-500/15 px-2 py-0.5 text-[11px] font-medium text-amber-500"
              >
                <TriangleAlert size={12} /> Uso alto
              </span>
            ) : null}
            <span className="text-[11px] font-normal text-muted opacity-0 transition-opacity duration-150 group-hover:opacity-100">
              {fmtTime(message.created_at)}
            </span>
          </p>
        )}
        {message.reasoning?.text && (
          <ReasoningBlock text={message.reasoning.text} seconds={message.reasoning.seconds} />
        )}
        {editing ? editor : <AssistantBody content={linkifyCitations(message.content, sources)} artifacts={artifacts} chatArtifacts={chatArtifacts} onOpenArtifact={onOpenArtifact} />}
        {!editing && sources.length > 0 && <SourcesBar sources={sources} />}

        {/* barra de ações — abaixo de toda mensagem da IA */}
        {!editing && (
          <>
            <div className="mt-1.5 flex items-center gap-0.5 opacity-0 transition-opacity duration-150 group-hover:opacity-100">
              <IconButton title="Editar" onClick={() => { setDraft(message.content); setEditing(true); }} disabled={busy}>
                <Pencil size={15} />
              </IconButton>
              <IconButton title="Copiar" onClick={copy}>
                {copied ? <Check size={15} className="text-green-400" /> : <Copy size={15} />}
              </IconButton>
              <IconButton title="Ler em voz alta" onClick={() => onSpeak(message.content)}>
                <Volume2 size={15} />
              </IconButton>
              <IconButton title="Custo / tokens" onClick={() => setShowCost((v) => !v)}>
                <Info size={15} />
              </IconButton>
              <IconButton title="Continuar" onClick={() => onContinue(message.id)} disabled={busy}>
                <Play size={15} />
              </IconButton>
              <IconButton title="Tentar novamente" onClick={() => onRegenerate(message.id)} disabled={busy}>
                <RotateCcw size={15} />
              </IconButton>
              {usedTools && (
                <IconButton title="Ferramentas usadas" onClick={() => setShowTools((v) => !v)}>
                  <Wrench size={15} className={showTools ? "text-accent-hover" : ""} />
                </IconButton>
              )}
              {guardEvents.length > 0 && (
                <IconButton title="Guardas de saída (fluxo)" onClick={() => setShowTools((v) => !v)}>
                  <ShieldAlert size={15} className={showTools ? "text-amber-400" : "text-amber-400/70"} />
                </IconButton>
              )}
              {usedMemories.length > 0 && (
                <IconButton title="Memórias usadas" onClick={() => setShowMem((v) => !v)}>
                  <Brain size={15} className={showMem ? "text-accent-hover" : ""} />
                </IconButton>
              )}
              {onRemember && message.content.trim() && (
                <div className="relative">
                  <IconButton title="Lembrar disto" onClick={() => { setRemOpen((v) => !v); setRemSaved(false); }}>
                    <BookmarkPlus size={15} className={remOpen ? "text-accent-hover" : ""} />
                  </IconButton>
                  {remOpen && (
                    <div className="absolute bottom-full left-0 z-20 mb-1 w-44 overflow-hidden rounded-xl border border-border bg-surface py-1 text-sm shadow-menu">
                      {remSaved ? (
                        <p className="px-3 py-1.5 text-xs text-green-400">Salvo na memória ✓</p>
                      ) : (
                        <>
                          <p className="px-3 py-1 text-[11px] font-medium uppercase tracking-wider text-muted">Lembrar em</p>
                          {([["global", "Global"], ["model", "Este modelo"], ["chat", "Este chat"]] as const).map(([sc, label]) => (
                            <button
                              key={sc}
                              onClick={async () => { await onRemember(message.content, sc); setRemSaved(true); setTimeout(() => setRemOpen(false), 900); }}
                              className="block w-full px-3 py-1.5 text-left text-ink transition-colors hover:bg-hover"
                            >
                              {label}
                            </button>
                          ))}
                        </>
                      )}
                    </div>
                  )}
                </div>
              )}
              <IconButton title="Excluir" onClick={() => onDelete(message.id)} disabled={busy}>
                <Trash2 size={15} className="hover:text-red-300" />
              </IconButton>
            </div>

            {showTools && usedTools && <ToolEventsPanel events={toolEvents} />}

            {showMem && usedMemories.length > 0 && <MemoriesUsedPanel items={usedMemories} />}

            {showCost &&
              (u ? (
                <UsagePanel u={u} />
              ) : (
                <div className="animate-pop mt-1.5 w-fit rounded-xl border border-border bg-surface px-3.5 py-2.5 text-xs text-muted shadow-menu">
                  Sem dados de uso para esta mensagem.
                </div>
              ))}
          </>
        )}
      </div>
    </div>
  );
}
