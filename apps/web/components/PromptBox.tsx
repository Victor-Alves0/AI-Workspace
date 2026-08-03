"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  AtSign,
  AudioLines,
  Brain,
  Camera,
  Check,
  Clock,
  CornerDownRight,
  Database,
  FileText,
  Hash,
  History,
  LayoutGrid,
  Mic,
  MessagesSquare,
  Minimize2,
  Plus,
  Search,
  Send,
  Sparkles,
  Square,
  Upload,
  Wrench,
  X,
} from "lucide-react";
import type { Attachment, KnowledgeRef, Prompt, Skill } from "@/lib/types";

/** um doc referenciado com "#" no compositor (chip + injeção no turno) */
export interface RefDoc { id: string; label: string; path: string }

/** achata a árvore de refs (bases→pastas→docs) numa lista de docs referenciáveis
 *  com caminho legível "base / pasta / arquivo". */
function flattenRefs(refs: KnowledgeRef[]): RefDoc[] {
  const out: RefDoc[] = [];
  for (const base of refs) {
    const byId = Object.fromEntries(base.folders.map((f) => [f.id, f]));
    for (const d of base.docs) {
      const parts: string[] = [];
      let fid = d.folder_id;
      let guard = 0;
      while (fid && byId[fid] && guard++ < 20) { parts.unshift(byId[fid].name); fid = byId[fid].parent_id; }
      const path = [base.name, ...parts, d.filename].join(" / ");
      out.push({ id: d.id, label: d.filename, path });
    }
  }
  return out;
}
import { fileToBase64, fileToImageDataUrl, fileToText } from "@/lib/image";
import { MenuItem, useClickOutside } from "./ui";
import { CODESPACE_DND_MIME, CODESPACE_SNIPPET_MIME } from "./CodespaceFileBrowser";
import { toolCategoryIcon, toolCategoryTitle } from "./toolCategory";

// docs binários com extração server-side (integração "Extração de Texto")
const DOC_RE = /\.(pdf|docx|xlsx|xlsm|pptx|csv)$/i;
// arquivos de texto lidos direto no cliente
const TEXT_RE = /\.(txt|md|markdown|json|ya?ml|log|tsv|xml|html?|py|js|ts|tsx|jsx|css|sh)$/i;
// teto generoso de anexos por mensagem (evita payloads absurdos, mas não atrapalha o uso)
const MAX_ATTACHMENTS = 50;

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

export type ReasoningEffort = "off" | "minimal" | "low" | "medium" | "high" | "xhigh";
const REASONING_LABELS: Record<ReasoningEffort, string> = {
  off: "Desligado",
  minimal: "Mínimo",
  low: "Baixo",
  medium: "Médio",
  high: "Alto",
  xhigh: "Máximo",
};

// Escada completa de raciocínio, oferecida em TODO modelo: o OpenRouter não expõe
// quais níveis cada um aceita, então deixamos o usuário escolher. Se o provider
// recusar o nível, o backend cai um degrau sozinho e emite `reasoning_effort` — o
// seletor então reflete o nível que de fato funcionou (ver useGeneration).
export function reasoningLevelsFor(_modelId?: string): ReasoningEffort[] {
  return ["minimal", "low", "medium", "high", "xhigh"];
}

// Seletor de nível de raciocínio (thinking) do modelo. Fica à esquerda do Ditar.
// As opções seguem o modelo ativo (`modelId`): alguns têm "Mínimo"/"Máximo", outros não.
function ThinkingSelect({
  value,
  onChange,
  modelId,
}: {
  value: ReasoningEffort;
  onChange: (v: ReasoningEffort) => void;
  modelId?: string;
}) {
  const [open, setOpen] = useState(false);
  const ref = useClickOutside<HTMLDivElement>(() => setOpen(false));
  const active = value !== "off";
  const label = REASONING_LABELS[value] ?? "Desligado";
  const opts: ReasoningEffort[] = ["off", ...reasoningLevelsFor(modelId)];
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
          {opts.map((k) => (
            <MenuItem
              key={k}
              icon={<Check size={14} className={value === k ? "" : "opacity-0"} />}
              onClick={() => { onChange(k); setOpen(false); }}
            >
              {REASONING_LABELS[k]}
            </MenuItem>
          ))}
        </div>
      )}
    </div>
  );
}

// Lista (com busca) das ferramentas que ESTE modelo pode usar. Abre para cima,
// no canto inferior-esquerdo, ao clicar no ícone da chave inglesa.
function ToolsMenu({ tools }: { tools: { name: string; description?: string; category?: "native" | "codespace" | "integration"; integration?: string }[] }) {
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
                    <p className="flex items-center gap-1.5 text-sm text-ink">
                      <span title={toolCategoryTitle(t.category, t.integration)} className="flex shrink-0 items-center text-accent-hover">
                        {toolCategoryIcon(t.category)}
                      </span>
                      {t.name}
                    </p>
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
  onStop,
  onQueue,
  queued = [],
  sending,
  recording,
  onToggleMic,
  onVoiceMode,
  modelTools = [],
  prompts = [],
  skills = [],
  attachedSkillIds = [],
  onAttachedSkillIdsChange,
  agents = [],
  agentId = null,
  onAgentChange,
  knowledgeRefs = [],
  refDocs = [],
  onRefDocsChange,
  chats = [],
  refChats = [],
  onRefChatsChange,
  capabilities = {},
  attachments = [],
  onAttachmentsChange,
  menuUp = false,
  reasoning = "off",
  onReasoningChange,
  reasoningModel,
  context,
  onCompact,
  onHistory,
  compacting = false,
  temporary = false,
  placeholder = "Como posso ajudar você hoje?",
}: {
  value: string;
  onChange: (v: string) => void;
  onSend: () => void;
  /** enquanto `sending`, o botão de enviar vira "Parar" e chama isto */
  onStop?: () => void;
  /** enviar DURANTE a geração: enfileira (steer=false) ou injeta no turno (steer=true) */
  onQueue?: (steer: boolean) => void;
  /** mensagens já enfileiradas neste turno (chips acima do composer) */
  queued?: { id: string; text: string; steer: boolean }[];
  placeholder?: string;
  sending: boolean;
  recording: boolean;
  onToggleMic: () => void;
  /** entra no modo voz (assistente hands-free) — ausente esconde o botão */
  onVoiceMode?: () => void;
  /** ferramentas que o modelo ativo pode usar (nome + descrição) */
  modelTools?: { name: string; description?: string; category?: "native" | "codespace" | "integration"; integration?: string }[];
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
  /** árvore de refs ("#"): bases acessíveis ao modelo/chat com pastas + docs */
  knowledgeRefs?: KnowledgeRef[];
  /** docs referenciados no próximo turno (chips) */
  refDocs?: RefDoc[];
  onRefDocsChange?: (r: RefDoc[]) => void;
  /** chats do usuário selecionáveis em "Chats de Referência" (sem o chat atual) */
  chats?: { id: string; title: string }[];
  /** chats anexados como contexto do próximo turno (chips) */
  refChats?: { id: string; title: string }[];
  onRefChatsChange?: (r: { id: string; title: string }[]) => void;
  /** capacidades do modelo ativo (gate de upload: vision / file_upload) */
  capabilities?: Record<string, boolean>;
  /** anexos (imagens/arquivos) do próximo envio */
  attachments?: Attachment[];
  onAttachmentsChange?: (a: Attachment[]) => void;
  /** abre o menu do "+" para cima (durante uma conversa, p/ ficar sempre visível) */
  menuUp?: boolean;
  reasoning?: ReasoningEffort;
  onReasoningChange?: (v: ReasoningEffort) => void;
  /** id do modelo base ativo — define quais níveis de raciocínio aparecem */
  reasoningModel?: string;
  /** uso de contexto p/ o medidor circular (null = não mostrar) */
  context?: { tokens: number; limit: number } | null;
  onCompact?: () => void;
  onHistory?: () => void;
  compacting?: boolean;
  /** chat temporário: moldura tracejada (estilo "modo temporário") p/ identificar */
  temporary?: boolean;
}) {
  const [plusOpen, setPlusOpen] = useState(false);
  const plusRef = useClickOutside<HTMLDivElement>(() => setPlusOpen(false));
  // "Chats de Referência": menu próprio com busca + lista (multi-seleção)
  const [chatPickOpen, setChatPickOpen] = useState(false);
  const [chatQuery, setChatQuery] = useState("");
  const chatPickRef = useClickOutside<HTMLDivElement>(() => setChatPickOpen(false));
  const taRef = useRef<HTMLTextAreaElement>(null);
  const backdropRef = useRef<HTMLDivElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const [attachErr, setAttachErr] = useState<string | null>(null);

  // upload liberado quando o modelo pode ver imagens, ouvir áudios ou receber arquivos
  const canVision = !!capabilities.vision || !!capabilities["filter:vision_router"];
  const canFiles = !!capabilities.file_upload;
  const canAudio = !!capabilities["filter:audio_router"];
  const canAttach = canVision || canFiles || canAudio;

  async function addFiles(files: File[]) {
    setAttachErr(null);
    if (!files.length) return;
    const next: Attachment[] = [...attachments];
    for (const f of files) {
      if (next.length >= MAX_ATTACHMENTS) { setAttachErr(`Máximo de ${MAX_ATTACHMENTS} anexos.`); break; }
      try {
        if (f.type.startsWith("image/")) {
          if (!canVision) { setAttachErr("Este modelo não tem Visão nem Vision Router — habilite em Capacidades/Filtros."); continue; }
          next.push({ type: "image", name: f.name || "imagem.png", url: await fileToImageDataUrl(f) });
        } else if (f.type.startsWith("audio/")) {
          if (!canAudio) { setAttachErr("Este modelo não tem Audio Router — habilite em Filtros p/ transcrever áudios."); continue; }
          if (f.size > 15 * 1024 * 1024) { setAttachErr(`Áudio muito grande: ${f.name} (máx. 15MB).`); continue; }
          next.push({ type: "audio", name: f.name || "audio", mime: f.type || undefined, url: `data:${f.type || "audio/mpeg"};base64,${await fileToBase64(f)}` });
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
    onAttachmentsChange?.(next.slice(0, MAX_ATTACHMENTS));
  }
  async function pickFiles(e: React.ChangeEvent<HTMLInputElement>) {
    const files = Array.from(e.target.files ?? []);
    e.target.value = "";
    await addFiles(files);
  }
  // colar imagem (Ctrl+V) direto no campo → vira anexo (não polui o texto)
  async function onPaste(e: React.ClipboardEvent) {
    const imgs = Array.from(e.clipboardData?.files ?? []).filter((f) => f.type.startsWith("image/"));
    if (imgs.length) { e.preventDefault(); await addFiles(imgs); }
  }
  // arrastar-e-soltar arquivos sobre o campo
  const [dragOver, setDragOver] = useState(false);
  // o que está sendo arrastado, só p/ o rótulo do overlay
  const [dropKind, setDropKind] = useState<"file" | "snippet" | "upload">("upload");
  // Rede de segurança: o realce SEMPRE some quando o arraste termina, aconteça o
  // que acontecer com o drop. Sem isto, soltar algo que não é anexo (uma seleção de
  // texto, p.ex.) deixava o "Solte para anexar" preso na tela até recarregar.
  useEffect(() => {
    const clear = () => setDragOver(false);
    window.addEventListener("dragend", clear);
    window.addEventListener("drop", clear);
    return () => {
      window.removeEventListener("dragend", clear);
      window.removeEventListener("drop", clear);
    };
  }, []);
  async function onDrop(e: React.DragEvent) {
    setDragOver(false);  // antes de qualquer coisa: nunca deixar o realce preso
    const files = Array.from(e.dataTransfer?.files ?? []);
    if (files.length) { e.preventDefault(); await addFiles(files); }
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

  // lista do seletor de "Chats de Referência", filtrada pela busca
  const chatRows = useMemo(() => {
    const q = chatQuery.trim().toLowerCase();
    return q ? chats.filter((c) => (c.title || "").toLowerCase().includes(q)) : chats;
  }, [chats, chatQuery]);

  // "#arquivo" (menção): referencia um doc da Base de Conhecimento p/ ESTE turno.
  // Mesma mecânica do "@"/"$". Só aparece o que o modelo/chat pode acessar.
  const refEntries = useMemo(() => flattenRefs(knowledgeRefs), [knowledgeRefs]);
  const hashToken = useMemo(() => {
    const before = value.slice(0, caret);
    const m = /(^|\s)#([\w.\-]*)$/.exec(before);
    if (!m) return null;
    return { query: m[2].toLowerCase(), start: m.index + m[1].length };
  }, [value, caret]);
  const hashQuery = hashToken?.query ?? null;
  const refMatches = useMemo(() => {
    if (hashQuery === null) return [];
    const picked = new Set(refDocs.map((r) => r.id));
    return refEntries.filter(
      (e) => !picked.has(e.id) && (e.label.toLowerCase().includes(hashQuery) || e.path.toLowerCase().includes(hashQuery)),
    );
  }, [refEntries, hashQuery, refDocs]);
  const refMenuOpen = !dismissed && !promptMenuOpen && !skillMenuOpen && !agentMenuOpen && refMatches.length > 0;

  // ao mudar o que foi digitado, reabre o menu e reseta o destaque
  useEffect(() => {
    setHi(0);
    setDismissed(false);
  }, [slashQuery, dollarQuery, atQuery, hashQuery]);

  function pickRef(e: RefDoc) {
    if (hashToken) {
      const pos = hashToken.start;
      onChange(value.slice(0, pos) + value.slice(caret));
      requestAnimationFrame(() => {
        const ta = taRef.current;
        if (ta) { ta.focus(); ta.setSelectionRange(pos, pos); }
        setCaret(pos);
      });
    }
    onRefDocsChange?.([...refDocs, e]);
  }

  function pickAgent(a: { id: string; name: string }) {
    // insere "@Nome " no texto (chip inline) e roteia o turno p/ este agente. A
    // seleção é EXPLÍCITA (só aqui, no menu) — colar/digitar "@nome" não roteia nada.
    const token = `@${a.name} `;
    if (atToken) {
      const pos = atToken.start;
      const newVal = value.slice(0, pos) + token + value.slice(caret);
      const np = pos + token.length;
      onChange(newVal);
      requestAnimationFrame(() => {
        const ta = taRef.current;
        if (ta) { ta.focus(); ta.setSelectionRange(np, np); }
        setCaret(np);
      });
    } else {
      onChange(value ? `${value.replace(/\s*$/, "")} ${token}` : token);
      requestAnimationFrame(() => taRef.current?.focus());
    }
    onAgentChange?.(a.id);
  }
  const attachedAgent = useMemo(() => agents.find((a) => a.id === agentId) ?? null, [agents, agentId]);

  function pickPrompt(p: Prompt) {
    onChange(p.content);
    requestAnimationFrame(() => taRef.current?.focus());
  }
  function pickSkill(s: Skill) {
    // troca o "$query" pelo token "$slug " (chip inline na frase). A ANEXAÇÃO é
    // EXPLÍCITA: só aqui, ao escolher no menu — colar/digitar "$slug" NÃO anexa.
    const token = `$${s.slug} `;
    if (dollarToken) {
      const pos = dollarToken.start;
      const newVal = value.slice(0, pos) + token + value.slice(caret);
      const np = pos + token.length;
      onChange(newVal);
      requestAnimationFrame(() => {
        const ta = taRef.current;
        if (ta) { ta.focus(); ta.setSelectionRange(np, np); }
        setCaret(np);
      });
    } else {
      onChange(value ? `${value.replace(/\s*$/, "")} ${token}` : token);
      requestAnimationFrame(() => taRef.current?.focus());
    }
    if (!attachedSkillIds.includes(s.id)) onAttachedSkillIdsChange?.([...attachedSkillIds, s.id]);
  }

  // skills habilitadas indexadas por slug (p/ casar os tokens "$slug" do texto)
  const skillBySlug = useMemo(() => {
    const m = new Map<string, Skill>();
    for (const s of skills) if (s.enabled) m.set(s.slug.toLowerCase(), s);
    return m;
  }, [skills]);

  // acha os tokens "$slug" (no início ou após espaço) que casam com uma skill
  function eachSkillToken(text: string, cb: (start: number, end: number, s: Skill) => void) {
    const re = /\$(\w+)/g;
    let m: RegExpExecArray | null;
    while ((m = re.exec(text))) {
      const boundaryOk = m.index === 0 || /\s/.test(text[m.index - 1]);
      const s = boundaryOk ? skillBySlug.get(m[1].toLowerCase()) : undefined;
      if (s) cb(m.index, m.index + m[0].length, s);
    }
  }

  // acha o token "@Nome" do agente SELECIONADO (o nome pode ter espaços, então
  // casa o literal exato com fronteira de espaço/início-fim)
  function eachAgentToken(text: string, name: string, cb: (start: number, end: number) => void) {
    const tok = `@${name}`;
    let idx = text.indexOf(tok);
    while (idx !== -1) {
      const before = idx === 0 || /\s/.test(text[idx - 1]);
      const after = idx + tok.length;
      const afterOk = after === text.length || /\s/.test(text[after]);
      if (before && afterOk) cb(idx, after);
      idx = text.indexOf(tok, idx + 1);
    }
  }

  // ranges dos "chips" ATIVOS no texto: skills ANEXADAS (não qualquer "$slug") + o
  // agente selecionado. É isso que faz colar "$slug"/"@nome" NÃO virar chip.
  const chipRanges = useMemo(() => {
    const attached = new Set(attachedSkillIds);
    const out: { start: number; end: number }[] = [];
    eachSkillToken(value, (start, end, s) => { if (attached.has(s.id)) out.push({ start, end }); });
    if (attachedAgent) eachAgentToken(value, attachedAgent.name, (start, end) => out.push({ start, end }));
    return out.sort((a, b) => a.start - b.start);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value, skillBySlug, attachedSkillIds, attachedAgent]);

  // nós de realce: texto comum (transparente, só ocupa espaço) + tokens como "chip"
  const highlightNodes = useMemo(() => {
    const nodes: React.ReactNode[] = [];
    let last = 0;
    chipRanges.forEach((r, k) => {
      if (r.start < last) return; // overlap improvável ($ vs @) — ignora o 2º
      if (r.start > last) nodes.push(value.slice(last, r.start));
      nodes.push(
        <span
          key={k}
          style={{ padding: "0 3px", margin: "0 -3px" }}
          className="rounded bg-accent/20 ring-1 ring-inset ring-accent/40"
        >
          {value.slice(r.start, r.end)}
        </span>,
      );
      last = r.end;
    });
    nodes.push(value.slice(last));
    return nodes;
  }, [value, chipRanges]);

  // PODA (nunca anexa): remove uma skill anexada se o token "$slug" dela sumiu do
  // texto (o usuário apagou o chip). Anexar é sempre explícito no menu.
  useEffect(() => {
    const present = new Set<string>();
    eachSkillToken(value, (_s, _e, s) => present.add(s.id));
    const kept = attachedSkillIds.filter((id) => present.has(id));
    if (kept.length !== attachedSkillIds.length) onAttachedSkillIdsChange?.(kept);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value, skillBySlug]);

  // PODA do agente: se o token "@Nome" some do texto, desliga o roteamento.
  useEffect(() => {
    if (!attachedAgent) return;
    let present = false;
    eachAgentToken(value, attachedAgent.name, () => { present = true; });
    if (!present) onAgentChange?.(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value, attachedAgent]);

  return (
    <div className="px-4 pb-5 pt-2">
      <div
        /* só realça o que REALMENTE dá pra soltar aqui: arquivos do sistema, um
           arquivo do Codespace ou um trecho de código — arrastar uma seleção de
           texto qualquer não deve prometer um anexo que não existe */
        onDragOver={(e) => {
          const t = e.dataTransfer.types;
          const snippet = t.includes(CODESPACE_SNIPPET_MIME);
          const csFile = t.includes(CODESPACE_DND_MIME);
          const dropavel = (canAttach && t.includes("Files")) || csFile || snippet;
          if (dropavel) {
            e.preventDefault();
            setDropKind(snippet ? "snippet" : csFile ? "file" : "upload");
            setDragOver(true);
          }
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={onDrop}
        className={`relative mx-auto max-w-3xl rounded-3xl border bg-surface px-3 py-2.5 shadow-prompt transition-colors duration-200 focus-within:border-accent/50 hover:border-accent/30 ${dragOver ? "border-accent border-dashed" : temporary ? "border-dashed border-ink-soft/60" : "border-border"}`}
      >
        {dragOver && (
          <div className="pointer-events-none absolute inset-0 z-40 flex items-center justify-center rounded-3xl bg-accent/5 text-sm font-medium text-accent-hover">
            {dropKind === "snippet" ? "Solte para anexar o trecho"
              : dropKind === "file" ? "Solte para anexar o arquivo"
              : "Solte para anexar"}
          </div>
        )}
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
        {refMenuOpen && (
          <div className="animate-pop absolute bottom-full left-3 right-3 z-50 mb-2 overflow-hidden rounded-xl border border-border bg-surface shadow-menu">
            <p className="px-3 pt-2 text-[11px] font-medium uppercase tracking-wider text-muted">Base de Conhecimento</p>
            <div className="max-h-60 overflow-y-auto py-1">
              {refMatches.map((e, i) => (
                <button
                  key={e.id}
                  onMouseEnter={() => setHi(i)}
                  onClick={() => pickRef(e)}
                  className={`flex w-full items-start gap-2 px-3 py-2 text-left transition-colors ${i === hi ? "bg-hover" : ""}`}
                >
                  <FileText size={14} className="mt-0.5 shrink-0 text-accent-hover" />
                  <span className="min-w-0">
                    <span className="block truncate text-sm text-ink">{e.label}</span>
                    <span className="block truncate text-xs text-muted">{e.path}</span>
                  </span>
                </button>
              ))}
            </div>
            <p className="border-t border-border px-3 py-1.5 text-[10px] text-muted">
              Anexa o arquivo a esta mensagem · ↑↓ · Enter/Tab · Esc
            </p>
          </div>
        )}
        {refDocs.length > 0 && (
          <div className="mb-1.5 flex flex-wrap gap-1.5 px-1">
            {refDocs.map((r) => (
              <span key={r.id} className="flex items-center gap-1 rounded-full bg-accent/15 px-2.5 py-0.5 text-xs text-accent-hover" title={r.path}>
                <Hash size={11} /> {r.label}
                <button
                  onClick={() => onRefDocsChange?.(refDocs.filter((x) => x.id !== r.id))}
                  className="text-accent-hover/70 transition-colors hover:text-accent-hover"
                >
                  <X size={11} />
                </button>
              </span>
            ))}
          </div>
        )}
        {refChats.length > 0 && (
          <div className="mb-1.5 flex flex-wrap gap-1.5 px-1">
            {refChats.map((c) => (
              <span key={c.id} className="flex items-center gap-1 rounded-full bg-accent/15 px-2.5 py-0.5 text-xs text-accent-hover" title={c.title}>
                <MessagesSquare size={11} /> <span className="max-w-[160px] truncate">{c.title || "Sem título"}</span>
                <button
                  onClick={() => onRefChatsChange?.(refChats.filter((x) => x.id !== c.id))}
                  className="text-accent-hover/70 transition-colors hover:text-accent-hover"
                >
                  <X size={11} />
                </button>
              </span>
            ))}
          </div>
        )}
        {/* skills ($slug) E agente (@nome) aparecem INLINE no compositor como chips,
            via a camada de realce atrás do textarea — sem linha separada acima */}
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
              /* teto de ~3 linhas + scroll: com muitos anexos (até 50) a lista não
                 pode empurrar o textarea e os botões p/ fora da tela */
              <div className="flex max-h-[7.5rem] flex-wrap gap-1.5 overflow-y-auto pr-0.5">
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
                      {a.type === "audio" ? <Mic size={13} className="shrink-0 text-accent-hover" /> : <FileText size={13} className="shrink-0 text-muted" />}
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
        {/* mensagens enviadas DURANTE a geração: chips (fila/steer) até o turno acabar */}
        {queued.length > 0 && (
          <div className="mb-1.5 flex flex-wrap gap-1.5 px-1">
            {queued.map((q) => (
              <span
                key={q.id}
                title={q.steer ? "Injetada no turno atual (steer)" : "Na fila — continua após esta resposta"}
                className={`flex max-w-[240px] items-center gap-1.5 rounded-lg border px-2.5 py-1 text-xs ${
                  q.steer ? "border-accent/40 bg-accent/10 text-accent-hover" : "border-border bg-surface2 text-ink-soft"
                }`}
              >
                {q.steer ? <CornerDownRight size={12} className="shrink-0" /> : <Clock size={12} className="shrink-0" />}
                <span className="truncate">{q.text}</span>
              </span>
            ))}
          </div>
        )}
        {/* compositor: uma camada de realce (chips $slug) atrás de um textarea de
            fundo transparente — mesma métrica de fonte/padding/altura de linha, então
            os glifos alinham e o cursor cai no lugar certo (o "chip" usa padding com
            margem negativa: o avanço do texto não muda, só o fundo/borda transborda) */}
        <div className="relative">
          <div
            ref={backdropRef}
            aria-hidden
            className="pointer-events-none absolute inset-0 overflow-hidden whitespace-pre-wrap break-words px-2 py-1.5 text-[15px] leading-6 text-transparent"
            style={{ maxHeight: MAX_HEIGHT }}
          >
            {highlightNodes}
          </div>
          <textarea
            ref={taRef}
            data-prompt-input
            rows={1}
            value={value}
            onChange={(e) => { onChange(e.target.value); setCaret(e.target.selectionStart ?? e.target.value.length); }}
            onSelect={(e) => setCaret((e.target as HTMLTextAreaElement).selectionStart ?? 0)}
            onScroll={(e) => { if (backdropRef.current) backdropRef.current.scrollTop = (e.target as HTMLTextAreaElement).scrollTop; }}
          onPaste={canAttach ? onPaste : undefined}
          onKeyDown={(e) => {
            // menu ativo: prompts ("/"), skills ("$"), agentes ("@") ou refs ("#") — nunca juntos
            const count = promptMenuOpen ? promptMatches.length : skillMenuOpen ? skillMatches.length : agentMenuOpen ? agentMatches.length : refMenuOpen ? refMatches.length : 0;
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
                else if (agentMenuOpen) pickAgent(agentMatches[hi] ?? agentMatches[0]);
                else pickRef(refMatches[hi] ?? refMatches[0]);
                return;
              }
              if (e.key === "Escape") {
                e.preventDefault();
                setDismissed(true); // fecha o menu, mantém o texto (vira literal)
                return;
              }
            }
            // agente (@nome) e skills ($slug) agora são tokens inline — removem-se
            // apagando o texto do chip (a poda desliga o roteamento/anexo).
            if (e.key === "Backspace" && !value && refDocs.length) {
              e.preventDefault();
              onRefDocsChange?.(refDocs.slice(0, -1));
              return;
            }
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              // durante a geração, Enter ENFILEIRA (não abre 2º turno); Alt+Enter faz
              // STEER (injeta no turno em curso). Fora da geração, envia normal.
              if (sending && onQueue && value.trim()) onQueue(e.altKey);
              else onSend();
            }
          }}
          placeholder={placeholder}
          className="relative z-[1] w-full resize-none overflow-y-auto bg-transparent px-2 py-1.5 text-[15px] leading-6 text-ink outline-none placeholder:text-muted"
          style={{ maxHeight: MAX_HEIGHT }}
          />
        </div>
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
                <div className={`animate-pop absolute left-0 z-50 min-w-[240px] rounded-xl border border-border bg-surface p-1.5 shadow-menu ${menuUp ? "bottom-11" : "top-11"}`}>
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
                    <MenuItem
                      icon={<Database size={16} />}
                      onClick={() => {
                        setPlusOpen(false);
                        if (!refEntries.length) {
                          alert("Nenhum documento disponível para referenciar. Acople uma Base de Conhecimento a este modelo (Editor do modelo → Conhecimento) e envie documentos em Espaço → Conhecimento.");
                          return;
                        }
                        // insere "#" no fim p/ abrir o menu de referências (garante que
                        // não fique "dismissed" de um uso anterior)
                        setDismissed(false);
                        const base = value.endsWith(" ") || !value ? value : value + " ";
                        onChange(base + "#");
                        requestAnimationFrame(() => {
                          const ta = taRef.current;
                          if (ta) { ta.focus(); const p = (base + "#").length; ta.setSelectionRange(p, p); setCaret(p); }
                        });
                      }}
                    >
                      Anexar Base de Conhecimento
                    </MenuItem>
                    <MenuItem
                      icon={<MessagesSquare size={16} />}
                      onClick={() => { setPlusOpen(false); setChatQuery(""); setChatPickOpen(true); }}
                    >
                      Chats de Referência
                    </MenuItem>
                </div>
              )}
              {/* seletor de chats de referência: busca + lista, multi-seleção */}
              {chatPickOpen && (
                <div
                  ref={chatPickRef}
                  className={`animate-pop absolute left-0 z-50 w-80 overflow-hidden rounded-xl border border-border bg-surface shadow-menu ${menuUp ? "bottom-11" : "top-11"}`}
                >
                  <div className="flex items-center gap-2 border-b border-border px-3 py-2">
                    <Search size={14} className="text-muted" />
                    <input
                      autoFocus
                      value={chatQuery}
                      onChange={(e) => setChatQuery(e.target.value)}
                      placeholder="Buscar chat…"
                      className="w-full bg-transparent text-sm text-ink outline-none placeholder:text-muted"
                    />
                    <button onClick={() => setChatPickOpen(false)} className="text-muted transition-colors hover:text-ink">
                      <X size={14} />
                    </button>
                  </div>
                  <div className="max-h-64 overflow-y-auto p-1">
                    {chatRows.map((c) => {
                      const on = refChats.some((r) => r.id === c.id);
                      return (
                        <button
                          key={c.id}
                          onClick={() =>
                            onRefChatsChange?.(
                              on ? refChats.filter((r) => r.id !== c.id)
                                 : [...refChats, { id: c.id, title: c.title }].slice(0, 5)
                            )
                          }
                          className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-sm text-ink transition-colors hover:bg-hover"
                        >
                          <span className={`flex h-4 w-4 shrink-0 items-center justify-center rounded border ${on ? "border-accent bg-accent text-white" : "border-border"}`}>
                            {on && <Check size={11} />}
                          </span>
                          <span className="truncate">{c.title || "Sem título"}</span>
                        </button>
                      );
                    })}
                    {chatRows.length === 0 && (
                      <p className="px-3 py-5 text-center text-sm text-muted">
                        {chats.length === 0 ? "Nenhum outro chat." : "Nada encontrado."}
                      </p>
                    )}
                  </div>
                  <p className="border-t border-border px-3 py-1.5 text-[10px] text-muted">
                    Anexa a conversa como contexto desta mensagem · máx. 5
                  </p>
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
              <ThinkingSelect value={reasoning} onChange={onReasoningChange} modelId={reasoningModel} />
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
            {sending && onStop ? (
              <div className="flex items-center gap-1">
                {onQueue && value.trim() && (
                  <button
                    onClick={() => onQueue(false)}
                    title="Enfileirar (segure Alt p/ steer: injeta no turno atual)"
                    className="rounded-full bg-surface2 p-2 text-ink-soft transition-colors hover:bg-hover hover:text-ink"
                  >
                    <Send size={16} />
                  </button>
                )}
                <button
                  onClick={onStop}
                  title="Parar geração"
                  className="rounded-full bg-accent p-2 text-ink transition-colors hover:bg-accent-hover"
                >
                  <Square size={16} fill="currentColor" />
                </button>
              </div>
            ) : value.trim() ? (
              <button
                onClick={onSend}
                disabled={sending}
                title="Enviar"
                className="rounded-full bg-accent p-2 text-ink transition-colors hover:bg-accent-hover disabled:opacity-50"
              >
                <Send size={16} />
              </button>
            ) : onVoiceMode ? (
              <button
                onClick={onVoiceMode}
                title="Modo voz (assistente)"
                className="rounded-full bg-accent p-2 text-ink transition-colors hover:bg-accent-hover"
              >
                <AudioLines size={16} />
              </button>
            ) : (
              <button disabled title="Enviar" className="rounded-full bg-accent p-2 text-ink opacity-50">
                <Send size={16} />
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
