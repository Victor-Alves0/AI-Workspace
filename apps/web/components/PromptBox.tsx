"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  AtSign,
  AudioLines,
  Brain,
  Camera,
  Check,
  Database,
  FileText,
  History,
  LayoutGrid,
  Mic,
  MessagesSquare,
  Minimize2,
  Plus,
  Search,
  Send,
  Sparkles,
  Upload,
  Wrench,
  X,
} from "lucide-react";
import type { Attachment, Prompt, Skill } from "@/lib/types";
import { fileToBase64, fileToImageDataUrl, fileToText } from "@/lib/image";
import { MenuItem, useClickOutside } from "./ui";

// docs binários com extração server-side (integração "Extração de Texto")
const DOC_RE = /\.(pdf|docx|xlsx|xlsm|pptx|csv)$/i;
// arquivos de texto lidos direto no cliente
const TEXT_RE = /\.(txt|md|markdown|json|ya?ml|log|tsv|xml|html?|py|js|ts|tsx|jsx|css|sh)$/i;

// Indicador circular do uso de contexto: verde → âmbar → vermelho conforme enche.
// Clicar abre um menu: Compactar (direto) ou Histórico (timeline + fixar).
function ContextMeter({
  pct,
  tokens,
  limit,
  busy,
  onCompact,
  onHistory,
}: {
  pct: number;
  tokens: number;
  limit: number;
  busy: boolean;
  onCompact: () => void;
  onHistory: () => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useClickOutside<HTMLDivElement>(() => setOpen(false));
  const p = Math.max(0, Math.min(1, pct));
  const color = p < 0.5 ? "#4ade80" : p < 0.8 ? "#fbbf24" : "#f87171";
  const r = 8;
  const c = 2 * Math.PI * r;
  const fmt = (n: number) => (n >= 1000 ? `${(n / 1000).toFixed(1)}k` : `${n}`);
  return (
    <div className="relative" ref={ref}>
      <button
        onMouseDown={(e) => e.stopPropagation()}
        onClick={() => setOpen((v) => !v)}
        disabled={busy}
        title={`Contexto: ${fmt(tokens)}${limit ? ` / ${fmt(limit)}` : ""} tokens`}
        className="relative flex h-8 w-8 items-center justify-center rounded-full text-ink-soft transition-colors hover:bg-hover disabled:opacity-60"
      >
        {busy ? (
          <span className="h-4 w-4 animate-spin rounded-full border-2 border-muted border-t-accent" />
        ) : (
          <svg viewBox="0 0 20 20" className="h-5 w-5 -rotate-90">
            <circle cx="10" cy="10" r={r} fill="none" stroke="currentColor" strokeWidth="2.5" className="text-surface2" />
            <circle
              cx="10" cy="10" r={r} fill="none" stroke={color} strokeWidth="2.5" strokeLinecap="round"
              strokeDasharray={c} strokeDashoffset={c * (1 - p)}
              style={{ transition: "stroke-dashoffset 0.3s, stroke 0.3s" }}
            />
          </svg>
        )}
      </button>
      {open && (
        <div className="animate-pop absolute bottom-11 right-0 z-50 min-w-[230px] rounded-xl border border-border bg-surface p-1.5 shadow-menu">
          <p className="px-2.5 pb-1 pt-1 text-[11px] text-muted">
            Contexto: <span className="font-medium text-ink-soft">{fmt(tokens)}{limit ? ` / ${fmt(limit)}` : ""}</span> tokens
          </p>
          <MenuItem icon={<Minimize2 size={15} />} onClick={() => { setOpen(false); onCompact(); }}>
            Compactar agora
          </MenuItem>
          <MenuItem icon={<History size={15} />} onClick={() => { setOpen(false); onHistory(); }}>
            Histórico de compactações
          </MenuItem>
        </div>
      )}
    </div>
  );
}

export type ReasoningEffort = "off" | "low" | "medium" | "high";
const REASONING_OPTS: { key: ReasoningEffort; label: string }[] = [
  { key: "off", label: "Desligado" },
  { key: "low", label: "Baixo" },
  { key: "medium", label: "Médio" },
  { key: "high", label: "Alto" },
];

// Seletor de nível de raciocínio (thinking) do modelo. Fica à esquerda do Ditar.
function ThinkingSelect({
  value,
  onChange,
}: {
  value: ReasoningEffort;
  onChange: (v: ReasoningEffort) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useClickOutside<HTMLDivElement>(() => setOpen(false));
  const active = value !== "off";
  const label = REASONING_OPTS.find((o) => o.key === value)?.label ?? "Desligado";
  return (
    <div className="relative" ref={ref}>
      <button
        onMouseDown={(e) => e.stopPropagation()}
        onClick={() => setOpen((v) => !v)}
        title="Nível de raciocínio"
        className={`flex items-center gap-1 rounded-full px-2 py-1.5 text-xs transition-colors ${
          active ? "bg-accent/15 text-accent-hover" : "text-ink-soft hover:bg-hover hover:text-ink"
        }`}
      >
        <Brain size={16} />
        {active && <span className="font-medium">{label}</span>}
      </button>
      {open && (
        <div className="animate-pop absolute bottom-11 right-0 z-50 min-w-[160px] rounded-xl border border-border bg-surface p-1.5 shadow-menu">
          <p className="px-2.5 pb-1 pt-1 text-[11px] font-medium uppercase tracking-wider text-muted">
            Raciocínio
          </p>
          {REASONING_OPTS.map((o) => (
            <MenuItem
              key={o.key}
              icon={<Check size={14} className={value === o.key ? "" : "opacity-0"} />}
              onClick={() => { onChange(o.key); setOpen(false); }}
            >
              {o.label}
            </MenuItem>
          ))}
        </div>
      )}
    </div>
  );
}

// Lista (com busca) das ferramentas que ESTE modelo pode usar. Abre para cima,
// no canto inferior-esquerdo, ao clicar no ícone da chave inglesa.
function ToolsMenu({ tools }: { tools: { name: string; description?: string }[] }) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const ref = useClickOutside<HTMLDivElement>(() => setOpen(false));
  const f = q.trim().toLowerCase();
  const filtered = f ? tools.filter((t) => (t.name + " " + (t.description ?? "")).toLowerCase().includes(f)) : tools;
  return (
    <div className="relative" ref={ref}>
      <button
        onMouseDown={(e) => e.stopPropagation()}
        onClick={() => setOpen((v) => !v)}
        title="Ferramentas do modelo"
        className="flex items-center gap-1 rounded-full px-2 py-1.5 text-ink-soft transition-colors hover:bg-hover hover:text-ink"
      >
        <Wrench size={16} />
        {tools.length > 0 && <span className="text-xs">{tools.length}</span>}
      </button>
      {open && (
        <div className="absolute bottom-11 left-0 z-50">
          <div className="animate-pop w-72 overflow-hidden rounded-xl border border-border bg-surface shadow-menu">
            <div className="flex items-center gap-2 border-b border-border px-3 py-2">
              <Search size={14} className="text-muted" />
              <input
                autoFocus value={q} onChange={(e) => setQ(e.target.value)} placeholder="Buscar ferramentas…"
                className="w-full bg-transparent text-sm text-ink outline-none placeholder:text-muted"
              />
            </div>
            <div className="max-h-64 overflow-y-auto py-1">
              {filtered.length === 0 ? (
                <p className="px-3 py-5 text-center text-xs text-muted">
                  {tools.length === 0 ? "Nenhuma ferramenta ativa neste modelo." : "Nada encontrado."}
                </p>
              ) : (
                filtered.map((t, i) => (
                  <div key={i} className="px-3 py-1.5">
                    <p className="flex items-center gap-1.5 text-sm text-ink"><Wrench size={12} className="shrink-0 text-accent-hover" /> {t.name}</p>
                    {t.description && <p className="mt-0.5 line-clamp-2 pl-[18px] text-xs text-muted">{t.description}</p>}
                  </div>
                ))
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

const MAX_HEIGHT = 192; // px — cresce até aqui e então rola internamente

export default function PromptBox({
  value,
  onChange,
  onSend,
  sending,
  recording,
  onToggleMic,
  modelTools = [],
  prompts = [],
  skills = [],
  attachedSkillIds = [],
  onAttachedSkillIdsChange,
  agents = [],
  agentId = null,
  onAgentChange,
  capabilities = {},
  attachments = [],
  onAttachmentsChange,
  menuUp = false,
  reasoning = "off",
  onReasoningChange,
  context,
  onCompact,
  onHistory,
  compacting = false,
  placeholder = "Como posso ajudar você hoje?",
}: {
  value: string;
  onChange: (v: string) => void;
  onSend: () => void;
  placeholder?: string;
  sending: boolean;
  recording: boolean;
  onToggleMic: () => void;
  /** ferramentas que o modelo ativo pode usar (nome + descrição) */
  modelTools?: { name: string; description?: string }[];
  prompts?: Prompt[];
  /** skills do usuário, invocáveis com "$" no campo de mensagem */
  skills?: Skill[];
  /** skills anexadas ad-hoc ao próximo turno (ids) */
  attachedSkillIds?: string[];
  onAttachedSkillIdsChange?: (ids: string[]) => void;
  /** agentes (modelos custom) invocáveis com "@" — roteia o turno a um deles */
  agents?: { id: string; name: string }[];
  agentId?: string | null;
  onAgentChange?: (id: string | null) => void;
  /** capacidades do modelo ativo (gate de upload: vision / file_upload) */
  capabilities?: Record<string, boolean>;
  /** anexos (imagens/arquivos) do próximo envio */
  attachments?: Attachment[];
  onAttachmentsChange?: (a: Attachment[]) => void;
  /** abre o menu do "+" para cima (durante uma conversa, p/ ficar sempre visível) */
  menuUp?: boolean;
  reasoning?: ReasoningEffort;
  onReasoningChange?: (v: ReasoningEffort) => void;
  /** uso de contexto p/ o medidor circular (null = não mostrar) */
  context?: { tokens: number; limit: number } | null;
  onCompact?: () => void;
  onHistory?: () => void;
  compacting?: boolean;
}) {
  const [plusOpen, setPlusOpen] = useState(false);
  const plusRef = useClickOutside<HTMLDivElement>(() => setPlusOpen(false));
  const taRef = useRef<HTMLTextAreaElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const [attachErr, setAttachErr] = useState<string | null>(null);

  // upload liberado quando o modelo pode ver imagens ou receber arquivos
  const canVision = !!capabilities.vision || !!capabilities["filter:vision_router"];
  const canFiles = !!capabilities.file_upload;
  const canAttach = canVision || canFiles;

  async function pickFiles(e: React.ChangeEvent<HTMLInputElement>) {
    setAttachErr(null);
    const files = Array.from(e.target.files ?? []);
    e.target.value = "";
    if (!files.length) return;
    const next: Attachment[] = [...attachments];
    for (const f of files) {
      if (next.length >= 6) { setAttachErr("Máximo de 6 anexos."); break; }
      try {
        if (f.type.startsWith("image/")) {
          if (!canVision) { setAttachErr("Este modelo não tem Visão nem Vision Router — habilite em Capacidades/Filtros."); continue; }
          next.push({ type: "image", name: f.name, url: await fileToImageDataUrl(f) });
        } else if (canFiles && DOC_RE.test(f.name)) {
          // doc binário → o servidor extrai o texto (config em Integrações › Extração de Texto)
          next.push({ type: "file", name: f.name, mime: f.type || undefined, data: await fileToBase64(f) });
        } else if (canFiles && (f.type.startsWith("text/") || TEXT_RE.test(f.name))) {
          next.push({ type: "file", name: f.name, text: await fileToText(f) });
        } else {
          setAttachErr(canFiles ? `Tipo não suportado: ${f.name} (imagens, PDF/Word/Excel/PPT/CSV ou texto).` : "Este modelo não aceita arquivos — habilite “Upload de Arquivos” em Capacidades.");
        }
      } catch (err) {
        setAttachErr(err instanceof Error ? err.message : "Falha ao ler o anexo");
      }
    }
    onAttachmentsChange?.(next.slice(0, 6));
  }
  function removeAttachment(i: number) {
    onAttachmentsChange?.(attachments.filter((_, idx) => idx !== i));
  }
  function openFilePicker(capture: boolean) {
    setPlusOpen(false);
    const inp = fileRef.current;
    if (!inp) return;
    if (capture) inp.setAttribute("capture", "environment");
    else inp.removeAttribute("capture");
    inp.click();
  }

  // auto-cresce até MAX_HEIGHT; depois rola internamente
  useEffect(() => {
    const ta = taRef.current;
    if (!ta) return;
    ta.style.height = "0px";
    ta.style.height = Math.min(ta.scrollHeight, MAX_HEIGHT) + "px";
  }, [value]);

  // Sistema de prompts: o menu só aparece quando a mensagem INTEIRA é "/comando"
  // (sem espaço/quebra) — assim não há ambiguidade entre "chamar um prompt" e
  // "escrever texto". Usar um prompt = selecionar no menu (clique/Enter/Tab);
  // ignorar = Esc (ou digitar qualquer coisa que não case), aí "/algo" vira texto.
  const [hi, setHi] = useState(0); // item destacado no menu
  const [dismissed, setDismissed] = useState(false); // usuário fechou o menu com Esc
  const [caret, setCaret] = useState(0); // posição do cursor (p/ detectar "$" inline)

  const slashQuery = useMemo(() => {
    const m = /^\/([\w-]*)$/.exec(value);
    return m ? m[1].toLowerCase() : null;
  }, [value]);
  const promptMatches = useMemo(() => {
    if (slashQuery === null) return [];
    return prompts.filter((p) => p.enabled && p.command.toLowerCase().startsWith(slashQuery));
  }, [prompts, slashQuery]);
  const promptMenuOpen = !dismissed && promptMatches.length > 0;

  // "$slug" em QUALQUER ponto da mensagem (menção): detecta o token "$query" logo
  // antes do cursor (início da linha ou após espaço). Assim dá p/ escrever a
  // mensagem E citar a skill inline — ela vira um chip e some do texto ao escolher.
  const dollarToken = useMemo(() => {
    const before = value.slice(0, caret);
    const m = /(^|\s)\$([\w]*)$/.exec(before);
    if (!m) return null;
    return { query: m[2].toLowerCase(), start: m.index + m[1].length };
  }, [value, caret]);
  const dollarQuery = dollarToken?.query ?? null;
  const skillMatches = useMemo(() => {
    if (dollarQuery === null) return [];
    return skills.filter(
      (s) =>
        s.enabled &&
        !attachedSkillIds.includes(s.id) &&
        (s.slug.toLowerCase().startsWith(dollarQuery) || s.name.toLowerCase().includes(dollarQuery)),
    );
  }, [skills, dollarQuery, attachedSkillIds]);
  const skillMenuOpen = !dismissed && !promptMenuOpen && skillMatches.length > 0;

  // "@agente" (menção): roteia ESTE turno a um modelo custom. Mesma mecânica do "$".
  const atToken = useMemo(() => {
    const before = value.slice(0, caret);
    const m = /(^|\s)@([\w-]*)$/.exec(before);
    if (!m) return null;
    return { query: m[2].toLowerCase(), start: m.index + m[1].length };
  }, [value, caret]);
  const atQuery = atToken?.query ?? null;
  const agentMatches = useMemo(() => {
    if (atQuery === null) return [];
    return agents.filter((a) => a.name.toLowerCase().includes(atQuery));
  }, [agents, atQuery]);
  const agentMenuOpen = !dismissed && !promptMenuOpen && !skillMenuOpen && agentMatches.length > 0;

  // ao mudar o que foi digitado, reabre o menu e reseta o destaque
  useEffect(() => {
    setHi(0);
    setDismissed(false);
  }, [slashQuery, dollarQuery, atQuery]);

  function pickAgent(a: { id: string; name: string }) {
    if (atToken) {
      const pos = atToken.start;
      onChange(value.slice(0, pos) + value.slice(caret));
      requestAnimationFrame(() => {
        const ta = taRef.current;
        if (ta) { ta.focus(); ta.setSelectionRange(pos, pos); }
        setCaret(pos);
      });
    }
    onAgentChange?.(a.id);
  }
  const attachedAgent = useMemo(() => agents.find((a) => a.id === agentId) ?? null, [agents, agentId]);

  function pickPrompt(p: Prompt) {
    onChange(p.content);
    requestAnimationFrame(() => taRef.current?.focus());
  }
  function pickSkill(s: Skill) {
    // remove só o token "$query" do texto (mantém o resto da mensagem); a skill
    // vira um chip acima do campo.
    if (dollarToken) {
      const pos = dollarToken.start;
      onChange(value.slice(0, pos) + value.slice(caret));
      requestAnimationFrame(() => {
        const ta = taRef.current;
        if (ta) { ta.focus(); ta.setSelectionRange(pos, pos); }
        setCaret(pos);
      });
    } else {
      onChange("");
      requestAnimationFrame(() => taRef.current?.focus());
    }
    onAttachedSkillIdsChange?.([...attachedSkillIds, s.id]);
  }
  const attachedSkills = useMemo(
    () => attachedSkillIds.map((id) => skills.find((s) => s.id === id)).filter((s): s is Skill => !!s),
    [attachedSkillIds, skills],
  );

  return (
    <div className="px-4 pb-5 pt-2">
      <div className="relative mx-auto max-w-3xl rounded-3xl border border-border bg-surface px-3 py-2.5 shadow-prompt transition-colors duration-200 focus-within:border-accent/50 hover:border-accent/30">
        {promptMenuOpen && (
          <div className="animate-pop absolute bottom-full left-3 right-3 z-50 mb-2 overflow-hidden rounded-xl border border-border bg-surface shadow-menu">
            <p className="px-3 pt-2 text-[11px] font-medium uppercase tracking-wider text-muted">Prompts</p>
            <div className="max-h-60 overflow-y-auto py-1">
              {promptMatches.map((p, i) => (
                <button
                  key={p.id}
                  onMouseEnter={() => setHi(i)}
                  onClick={() => pickPrompt(p)}
                  className={`block w-full px-3 py-2 text-left transition-colors ${i === hi ? "bg-hover" : ""}`}
                >
                  <span className="text-sm text-ink">{p.title}</span>{" "}
                  <span className="font-mono text-xs text-accent-hover">/{p.command}</span>
                  <span className="block truncate text-xs text-muted">{p.content}</span>
                </button>
              ))}
            </div>
            <p className="border-t border-border px-3 py-1.5 text-[10px] text-muted">
              ↑↓ navegar · Enter/Tab usar · Esc ignorar
            </p>
          </div>
        )}
        {skillMenuOpen && (
          <div className="animate-pop absolute bottom-full left-3 right-3 z-50 mb-2 overflow-hidden rounded-xl border border-border bg-surface shadow-menu">
            <p className="px-3 pt-2 text-[11px] font-medium uppercase tracking-wider text-muted">Skills</p>
            <div className="max-h-60 overflow-y-auto py-1">
              {skillMatches.map((s, i) => (
                <button
                  key={s.id}
                  onMouseEnter={() => setHi(i)}
                  onClick={() => pickSkill(s)}
                  className={`flex w-full items-start gap-2 px-3 py-2 text-left transition-colors ${i === hi ? "bg-hover" : ""}`}
                >
                  <Sparkles size={14} className="mt-0.5 shrink-0 text-accent-hover" />
                  <span className="min-w-0">
                    <span className="text-sm text-ink">{s.name}</span>{" "}
                    <span className="font-mono text-xs text-accent-hover">${s.slug}</span>
                    {s.description && <span className="block truncate text-xs text-muted">{s.description}</span>}
                  </span>
                </button>
              ))}
            </div>
            <p className="border-t border-border px-3 py-1.5 text-[10px] text-muted">
              ↑↓ navegar · Enter/Tab anexar · Esc ignorar
            </p>
          </div>
        )}
        {agentMenuOpen && (
          <div className="animate-pop absolute bottom-full left-3 right-3 z-50 mb-2 overflow-hidden rounded-xl border border-border bg-surface shadow-menu">
            <p className="px-3 pt-2 text-[11px] font-medium uppercase tracking-wider text-muted">Agentes</p>
            <div className="max-h-60 overflow-y-auto py-1">
              {agentMatches.map((a, i) => (
                <button
                  key={a.id}
                  onMouseEnter={() => setHi(i)}
                  onClick={() => pickAgent(a)}
                  className={`flex w-full items-center gap-2 px-3 py-2 text-left transition-colors ${i === hi ? "bg-hover" : ""}`}
                >
                  <AtSign size={14} className="shrink-0 text-accent-hover" />
                  <span className="truncate text-sm text-ink">{a.name}</span>
                </button>
              ))}
            </div>
            <p className="border-t border-border px-3 py-1.5 text-[10px] text-muted">
              Roteia esta mensagem para o agente · ↑↓ · Enter/Tab · Esc
            </p>
          </div>
        )}
        {attachedAgent && (
          <div className="mb-1.5 flex flex-wrap gap-1.5 px-1">
            <span className="flex items-center gap-1 rounded-full bg-accent/15 px-2.5 py-0.5 text-xs text-accent-hover">
              <AtSign size={11} /> {attachedAgent.name}
              <button onClick={() => onAgentChange?.(null)} className="text-accent-hover/70 transition-colors hover:text-accent-hover">
                <X size={11} />
              </button>
            </span>
          </div>
        )}
        {attachedSkills.length > 0 && (
          <div className="mb-1.5 flex flex-wrap gap-1.5 px-1">
            {attachedSkills.map((s) => (
              <span key={s.id} className="flex items-center gap-1 rounded-full bg-accent/15 px-2.5 py-0.5 text-xs text-accent-hover">
                <Sparkles size={11} /> ${s.slug}
                <button
                  onClick={() => onAttachedSkillIdsChange?.(attachedSkillIds.filter((id) => id !== s.id))}
                  className="text-accent-hover/70 transition-colors hover:text-accent-hover"
                >
                  <X size={11} />
                </button>
              </span>
            ))}
          </div>
        )}
        {/* input escondido para upload de imagens/arquivos */}
        <input
          ref={fileRef}
          type="file"
          multiple
          accept="image/*,text/*,.pdf,.docx,.xlsx,.xlsm,.pptx,.csv,.txt,.md,.json,.yaml,.yml,.log,.xml,.html,.py,.js,.ts"
          className="hidden"
          onChange={pickFiles}
        />
        {(attachments.length > 0 || attachErr) && (
          <div className="mb-1.5 space-y-1 px-1">
            {attachments.length > 0 && (
              <div className="flex flex-wrap gap-1.5">
                {attachments.map((a, i) =>
                  a.type === "image" ? (
                    <span key={i} className="group/att relative">
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      <img src={a.url} alt={a.name} className="h-14 w-14 rounded-lg border border-border object-cover" />
                      <button
                        onClick={() => removeAttachment(i)}
                        className="absolute -right-1.5 -top-1.5 rounded-full bg-bg p-0.5 text-muted shadow ring-1 ring-border transition-colors hover:text-red-300"
                      >
                        <X size={12} />
                      </button>
                    </span>
                  ) : (
                    <span key={i} className="flex max-w-[200px] items-center gap-1.5 rounded-lg border border-border bg-surface2 px-2.5 py-1 text-xs text-ink">
                      <FileText size={13} className="shrink-0 text-muted" />
                      <span className="truncate">{a.name}</span>
                      <button onClick={() => removeAttachment(i)} className="text-muted transition-colors hover:text-red-300">
                        <X size={12} />
                      </button>
                    </span>
                  ),
                )}
              </div>
            )}
            {attachErr && <p className="text-[11px] text-red-400">{attachErr}</p>}
          </div>
        )}
        <textarea
          ref={taRef}
          data-prompt-input
          rows={1}
          value={value}
          onChange={(e) => { onChange(e.target.value); setCaret(e.target.selectionStart ?? e.target.value.length); }}
          onSelect={(e) => setCaret((e.target as HTMLTextAreaElement).selectionStart ?? 0)}
          onKeyDown={(e) => {
            // menu ativo: prompts ("/"), skills ("$") ou agentes ("@") — nunca juntos
            const count = promptMenuOpen ? promptMatches.length : skillMenuOpen ? skillMatches.length : agentMenuOpen ? agentMatches.length : 0;
            if (count > 0) {
              if (e.key === "ArrowDown") {
                e.preventDefault();
                setHi((i) => (i + 1) % count);
                return;
              }
              if (e.key === "ArrowUp") {
                e.preventDefault();
                setHi((i) => (i - 1 + count) % count);
                return;
              }
              if (e.key === "Enter" || e.key === "Tab") {
                e.preventDefault();
                if (promptMenuOpen) pickPrompt(promptMatches[hi] ?? promptMatches[0]);
                else if (skillMenuOpen) pickSkill(skillMatches[hi] ?? skillMatches[0]);
                else pickAgent(agentMatches[hi] ?? agentMatches[0]);
                return;
              }
              if (e.key === "Escape") {
                e.preventDefault();
                setDismissed(true); // fecha o menu, mantém o texto (vira literal)
                return;
              }
            }
            // Backspace com campo vazio remove o agente ou a última skill anexada
            if (e.key === "Backspace" && !value && attachedAgent) {
              e.preventDefault();
              onAgentChange?.(null);
              return;
            }
            if (e.key === "Backspace" && !value && attachedSkills.length) {
              e.preventDefault();
              onAttachedSkillIdsChange?.(attachedSkillIds.slice(0, -1));
              return;
            }
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              onSend();
            }
          }}
          placeholder={placeholder}
          className="w-full resize-none overflow-y-auto bg-transparent px-2 py-1.5 text-[15px] text-ink outline-none placeholder:text-muted"
          style={{ maxHeight: MAX_HEIGHT }}
        />
        <div className="flex items-center justify-between px-1 pt-1">
          <div className="flex items-center gap-1">
            <div className="relative" ref={plusRef}>
              <button
                onMouseDown={(e) => e.stopPropagation()}
                onClick={() => setPlusOpen((v) => !v)}
                title="Anexar"
                className="rounded-full p-2 text-ink-soft transition-colors hover:bg-hover hover:text-ink"
              >
                <Plus size={18} />
              </button>
              {plusOpen && (
                <div className={`animate-pop absolute left-0 min-w-[240px] rounded-xl border border-border bg-surface p-1.5 shadow-menu ${menuUp ? "bottom-11" : "top-11"}`}>
                    <MenuItem
                      icon={<Upload size={16} />}
                      onClick={() => canAttach ? openFilePicker(false) : setPlusOpen(false)}
                    >
                      Carregar Arquivos
                    </MenuItem>
                    <MenuItem
                      icon={<Camera size={16} />}
                      onClick={() => canVision ? openFilePicker(true) : setPlusOpen(false)}
                    >
                      Enviar Captura
                    </MenuItem>
                    <MenuItem icon={<Database size={16} />} onClick={() => setPlusOpen(false)}>
                      Anexar Base de Conhecimento
                    </MenuItem>
                    <MenuItem icon={<MessagesSquare size={16} />} onClick={() => setPlusOpen(false)}>
                      Chats de Referência
                    </MenuItem>
                </div>
              )}
            </div>
            <button title="Integrações" className="hidden rounded-full p-2 text-ink-soft transition-colors hover:bg-hover hover:text-ink sm:inline-flex">
              <LayoutGrid size={17} />
            </button>
            <ToolsMenu tools={modelTools} />
          </div>

          <div className="flex items-center gap-1.5">
            {context && onCompact && context.limit > 0 && (
              <ContextMeter
                pct={context.tokens / context.limit}
                tokens={context.tokens}
                limit={context.limit}
                busy={compacting}
                onCompact={onCompact}
                onHistory={onHistory ?? (() => {})}
              />
            )}
            {onReasoningChange && (
              <ThinkingSelect value={reasoning} onChange={onReasoningChange} />
            )}
            <button
              onClick={onToggleMic}
              title="Ditar"
              className={`rounded-full p-2 transition-colors ${
                recording ? "animate-pulse bg-red-500/20 text-red-300" : "text-ink-soft hover:bg-hover hover:text-ink"
              }`}
            >
              <Mic size={18} />
            </button>
            {value.trim() ? (
              <button
                onClick={onSend}
                disabled={sending}
                title="Enviar"
                className="rounded-full bg-accent p-2 text-ink transition-colors hover:bg-accent-hover disabled:opacity-50"
              >
                <Send size={16} />
              </button>
            ) : (
              <button title="Modo de voz" className="rounded-full bg-accent p-2 text-ink transition-colors hover:bg-accent-hover">
                <AudioLines size={16} />
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
