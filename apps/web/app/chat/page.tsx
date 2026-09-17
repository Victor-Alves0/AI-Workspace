"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowDown, ArrowUpRight, Bell, BookOpen, Check, ChevronDown, ChevronLeft, ChevronRight, ChevronUp, Code2, Copy, FlaskConical, GitBranch, Heart, Image as ImageIcon, Link2, Loader2, LockKeyhole, Map as MapIcon, Menu, MessageSquareDashed, Mic, Package, Pause, Pencil, Pin, Play, Plus, RotateCcw, RotateCw, ScrollText, Search, Scissors, Settings, Share2, Shield, ShieldAlert, SlidersHorizontal, Sparkles, Square, Trash2, Users, Volume2, Wrench, X, type LucideIcon } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { copyText } from "@/lib/clipboard";
import { streamContinue, streamEphemeral, streamMessage, streamRegenerate, streamRoundtable } from "@/lib/sse";
import { seekSpeaking, seekSpeakingTo, setSpeakingRate, speak, startBrowserDictation, startRecording, stopSpeaking, subscribeSpeechProgress, toggleSpeakingPaused, transcribe, type SpeechProgress } from "@/lib/voice";
import { captureUtterance } from "@/lib/voiceSession";
import { transcribeWhisper } from "@/lib/wakeword";
import { onVoiceActivate } from "@/lib/desktop";
import { browserNotify, playChime, requestNotifPermission } from "@/lib/notify";
import { downloadJSON, downloadPDF, downloadTXT } from "@/lib/download";
import { pickSuggestions, type Suggestion } from "@/lib/suggestions";
import type { AskSpec, Attachment, Chat, ChatArtifact, CodespaceProject, Folder, KnowledgeRef, ListenConfig, Message, Model, ModelConfig, Prompt, RoundtableConfig, RoundtableParticipant, Skill, Speaker, SystemTool, Tool, ToolEvent, User, VoiceSession } from "@/lib/types";
import ArtifactPanel from "@/components/ArtifactPanel";
import CodespaceFileBrowser, { CODESPACE_DND_MIME, CODESPACE_SNIPPET_MIME, extLang, stripLineNumbers } from "@/components/CodespaceFileBrowser";
import type { CodespaceDragPayload, CodespaceSnippetPayload } from "@/components/CodespaceFileBrowser";
import Roundtable, { nextColor, RT_COLORS } from "@/components/Roundtable";
import Markdown from "@/components/Markdown";
import { ReasoningBlock, fmtTime } from "@/components/MessageItem";
import { setFormatPrefs } from "@/lib/format";
import AskOptions from "@/components/AskOptions";
import { useConfirm } from "@/components/ConfirmDialog";
import Sidebar from "@/components/Sidebar";
import Controls from "@/components/Controls";
import ModelPicker from "@/components/ModelPicker";
import SettingsModal from "@/components/SettingsModal";
import OnboardingModal from "@/components/OnboardingModal";
import CommandPalette, { type PaletteItem } from "@/components/CommandPalette";
import ArchivedModal from "@/components/ArchivedModal";
import ChatManager from "@/components/ChatManager";
import ChatInfoModal from "@/components/ChatInfo";
import CompactionHistory from "@/components/CompactionHistory";
import PromptBox, { type MiniAppId, type ReasoningEffort, type RefDoc } from "@/components/PromptBox";
import MessageItem from "@/components/MessageItem";
import WorkspaceView, { type Section as WorkspaceSection } from "@/components/WorkspaceView";
import type { ChatActions } from "@/components/ChatItem";
import { SHORTCUTS, eventToCombo, resolveBinding, comboHasModifier, type ShortcutMap } from "@/lib/shortcuts";
import { useGeneration } from "./useGeneration";

type RoundtableStream = { speaker: Speaker; content: string; reasoning: string };

type ImaginaiEntity = {
  id: string;
  kind: string;
  key: string;
  name: string;
  description: string;
  location_id: string | null;
  owner_entity_id: string | null;
  state: Record<string, unknown>;
  active: boolean;
};

type ImaginaiSnapshot = {
  campaign: {
    id: string;
    chat_id: string;
    name: string;
    system_key: string;
    system_version: string;
    status: string;
    world_tick: number;
    settings?: {
      narration_style?: "balanced" | "cinematic" | "gritty";
      difficulty?: "story" | "balanced" | "challenging";
      premise?: string;
      opening_scene?: string;
      [key: string]: unknown;
    };
  };
  character: ImaginaiEntity | null;
  location: ImaginaiEntity | null;
};

type ImaginaiSystemDefinition = {
  key: string;
  name: string;
  version: string;
  inventory: {
    weight: { supported: boolean; default_enabled: boolean; unit: string };
    currency_weight: { supported: boolean; default_enabled: boolean };
    currencies: { key: string; label: string; name: string; weight: number }[];
    equipment_slots: string[];
  };
  sheet: {
    summary: { key: string; label: string }[];
    attributes: { key: string; label: string; short: string; skills: string[] }[];
    skills: Record<string, string>;
  };
};

type ImaginaiJournalEntry = {
  id: string;
  title: string;
  content: string;
  tags: string[];
  pinned: boolean;
  created_at: string;
  updated_at: string;
};

type ImaginaiEvent = {
  id: string;
  sequence: number;
  world_tick: number;
  event_type: string;
  actor_id: string | null;
  target_id: string | null;
  location_id: string | null;
  payload: Record<string, unknown>;
  visibility: string;
  created_at: string;
};

type ImaginaiInventory = {
  items: {
    id: string;
    name: string;
    description: string;
    quantity: number;
    weight: number;
    equipped: boolean;
    slot: string | null;
    container: string | null;
    charges: number | null;
  }[];
  currencies: Record<string, number>;
  total_weight: number;
  weight?: {
    enabled: boolean;
    currency_enabled: boolean;
    items: number;
    currencies: number;
    total: number;
    unit: string;
  };
};

type ImaginaiCodexResult = {
  id: string;
  result_type: "entity" | "lore";
  kind: string;
  name: string;
  knowledge: "aware" | "rumor" | "known";
  description: string | Record<string, unknown> | null;
  subject?: string | null;
  confidence?: number;
  redacted: string[];
};

type ImaginaiSpell = {
  key: string;
  name: string;
  level: number;
  school: string;
  prepared: boolean;
  known: boolean;
  ritual: boolean;
  concentration: boolean;
  casting_time: string;
  range: string;
  duration: string;
  components: unknown;
  description: string;
};

type ImaginaiSpells = {
  spells: ImaginaiSpell[];
  slots: Record<string, { current: number; max: number }>;
  spellcasting_ability: string | null;
  attack_modifier: number;
  save_dc: number;
};

type ImaginaiMap = {
  locations: { id: string; name: string; description: string; current: boolean; x: number | null; y: number | null; index: number }[];
  routes: { from: string; to: string; label: string }[];
};

type ImaginaiWorldEntity = ImaginaiEntity & { private_notes: string };

// Override reservado do composer, persistido no Chat sem alterar o preset do
// modelo. O backend consome e remove esta chave antes de chamar o provider.
const CHAT_REASONING_EFFORT_PARAM = "_chat_reasoning_effort";
const REASONING_EFFORTS = new Set<ReasoningEffort>(["off", "minimal", "low", "medium", "high", "xhigh"]);

function reasoningFromParams(params: Record<string, unknown> | null | undefined): ReasoningEffort | null {
  const effort = (params?.reasoning as { effort?: unknown } | undefined)?.effort;
  return typeof effort === "string" && REASONING_EFFORTS.has(effort as ReasoningEffort)
    ? effort as ReasoningEffort
    : null;
}

function chatReasoningOverride(params: Record<string, unknown> | null | undefined): ReasoningEffort | null {
  const effort = params?.[CHAT_REASONING_EFFORT_PARAM];
  return typeof effort === "string" && REASONING_EFFORTS.has(effort as ReasoningEffort)
    ? effort as ReasoningEffort
    : null;
}

// varre os resultados de ferramenta em busca de um artefato "kind:ask" (o seletor
// de opções). Recursivo (execute_tool no topo ou run_code aninhado em `output`).
function findAskInNode(node: unknown, depth = 0): AskSpec | null {
  if (node == null || depth > 6) return null;
  if (Array.isArray(node)) {
    for (const x of node) { const r = findAskInNode(x, depth + 1); if (r) return r; }
    return null;
  }
  if (typeof node !== "object") return null;
  const o = node as Record<string, unknown>;
  if (o.kind === "ask" && Array.isArray(o.options) && o.options.length) {
    const opts = (o.options as unknown[])
      .map((x) => {
        if (typeof x === "string") return { label: x.slice(0, 100), value: x };
        const r = x as Record<string, unknown>;
        const label = String(r.label ?? r.value ?? "").slice(0, 100);
        return label ? { label, value: String(r.value ?? label), hint: r.hint ? String(r.hint) : undefined } : null;
      })
      .filter(Boolean) as AskSpec["options"];
    if (opts.length) {
      return {
        question: String(o.question ?? ""),
        options: opts,
        allow_custom: o.allow_custom !== false,
        custom_label: typeof o.custom_label === "string" ? o.custom_label : undefined,
      };
    }
  }
  for (const v of Object.values(o)) { const r = findAskInNode(v, depth + 1); if (r) return r; }
  return null;
}
function findAsk(events: ToolEvent[]): AskSpec | null {
  for (const e of events) if (e.kind === "result") { const r = findAskInNode(e.data); if (r) return r; }
  return null;
}

// Comando builtin "/learn": pede ao modelo p/ destilar uma skill do trabalho do
// chat via propose_skill (proposal-only — o card editável é quem salva).
const LEARN_BUILTIN: Prompt = {
  id: "builtin-learn",
  command: "learn",
  title: "Aprender skill deste chat",
  content:
    "Review the work we completed in this conversation. If it contains a reusable " +
    "multi-step procedure, distill it into a skill and call the propose_skill tool " +
    "(short name, a when-to-use description, and complete step-by-step content in " +
    "markdown). If nothing here is worth turning into a skill, say so briefly instead.",
  enabled: true,
  created_at: "",
  updated_at: "",
};

/** Redimensionamento horizontal por DIVISOR arrastável, lembrado no localStorage.
 *  `edge`="left": o painel fica à DIREITA e o divisor na sua borda esquerda (arrastar
 *  p/ a esquerda AUMENTA — artefatos/Controles). `edge`="right": painel à ESQUERDA,
 *  divisor na borda direita (barra lateral). O teto respeita 70% do contêiner (verdade
 *  de layout — viewport/emulação mentem) além do `max` fixo. Só desktop (o divisor é
 *  `hidden md:flex`); no mobile os painéis são tela cheia/drawer. */
function useHResize(key: string, def: number, min: number, max: number, edge: "left" | "right", subtle = false) {
  const [w, setW] = useState<number>(() => {
    if (typeof window === "undefined") return def;
    const s = Number(window.localStorage.getItem(key));
    return Number.isFinite(s) && s >= min ? s : def;
  });
  const ref = useRef<HTMLDivElement | null>(null);
  const start = useCallback((e: React.PointerEvent) => {
    e.preventDefault();
    const panel = ref.current;
    if (!panel) return;
    const rect = panel.getBoundingClientRect();
    const anchor = edge === "left" ? rect.right : rect.left;
    const prevSelect = document.body.style.userSelect;
    document.body.style.userSelect = "none";
    document.body.style.cursor = "col-resize";
    const parentW = panel.parentElement?.getBoundingClientRect().width ?? window.innerWidth;
    const cap = Math.min(max, Math.round(parentW * 0.7));
    const onMove = (ev: PointerEvent) => {
      const raw = edge === "left" ? anchor - ev.clientX : ev.clientX - anchor;
      setW(Math.max(min, Math.min(Math.round(raw), cap)));
    };
    const onUp = () => {
      document.body.style.userSelect = prevSelect;
      document.body.style.cursor = "";
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      setW((cur) => { window.localStorage.setItem(key, String(cur)); return cur; });
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  }, [key, min, max, edge]);
  const divider = (
    <div
      onPointerDown={start}
      title="Arraste para redimensionar"
      className={`group absolute inset-y-0 z-10 hidden w-2.5 cursor-col-resize items-stretch justify-center md:flex ${
        edge === "left" ? "-left-1" : "-right-1"
      }`}
    >
      {/* `subtle`: sem alça visível em repouso (não poluir a UI) — o pega ainda
          responde ao arrasto e a alça aparece (accent) só no hover. */}
      <span
        className={`my-auto h-10 w-1 rounded-full transition-all ${
          subtle ? "bg-accent opacity-0 group-hover:opacity-100" : "bg-border group-hover:bg-accent"
        }`}
      />
    </div>
  );
  return { w, ref, divider };
}

export default function ChatPage() {
  const router = useRouter();
  const confirm = useConfirm();
  const [user, setUser] = useState<User | null>(null);
  const [chats, setChats] = useState<Chat[]>([]);
  const [folders, setFolders] = useState<Folder[]>([]);
  const [customModels, setCustomModels] = useState<ModelConfig[]>([]);
  const [extModels, setExtModels] = useState<Model[]>([]);
  const [tools, setTools] = useState<Tool[]>([]);
  const [systemTools, setSystemTools] = useState<SystemTool[]>([]);
  const [prompts, setPrompts] = useState<Prompt[]>([]);
  const [skills, setSkills] = useState<Skill[]>([]);
  // skills anexadas ad-hoc ao próximo envio (via "$" no promptbox)
  const [attachedSkillIds, setAttachedSkillIds] = useState<string[]>([]);
  // docs da Base de Conhecimento referenciados via "#" no próximo envio
  const [refDocs, setRefDocs] = useState<RefDoc[]>([]);
  // outros chats anexados como contexto ("Chats de Referência") do próximo envio
  const [refChats, setRefChats] = useState<{ id: string; title: string }[]>([]);
  // árvore de refs acessíveis (bases acopladas ao modelo/chat) p/ o menu "#"
  const [knowledgeRefs, setKnowledgeRefs] = useState<KnowledgeRef[]>([]);
  // anexos (imagens/arquivos) do próximo envio
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [active, setActive] = useState<Chat | null>(null);
  // Mini Apps pertencem ao chat, não à página inteira. A seleção por id impede
  // o Imaginai de aparecer ou materializar campanha ao navegar para outro chat.
  const [miniAppByChat, setMiniAppByChat] = useState<Record<string, MiniAppId | undefined>>({});
  const activeMiniApp = active?.id ? miniAppByChat[active.id] ?? null : null;
  const setActiveMiniApp = useCallback((app: MiniAppId | null) => {
    const chatId = active?.id;
    if (!chatId) return;
    setMiniAppByChat((current) => {
      if (app) return { ...current, [chatId]: app };
      const next = { ...current };
      delete next[chatId];
      return next;
    });
  }, [active?.id]);
  const [imaginaiSnapshot, setImaginaiSnapshot] = useState<ImaginaiSnapshot | null>(null);
  const [imaginaiLoading, setImaginaiLoading] = useState(false);
  const [imaginaiError, setImaginaiError] = useState<string | null>(null);
  // espelho do id do chat ativo: os handlers de stream (assíncronos) consultam
  // este ref para saber, a QUALQUER instante, se ainda estão pintando o chat que
  // o usuário está vendo — sem isso, o parcial de um chat vaza para outro ao trocar.
  const activeIdRef = useRef<string | null>(null);
  // Identifica a navegação mais recente. Sem isto, clicar A e depois B podia
  // terminar em A quando o GET de A respondesse por último.
  const selectChatRequestRef = useRef(0);
  const isActiveChat = (id: string | null) => (id ?? null) === activeIdRef.current;
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  // mensagens enviadas DURANTE a geração (fila/steer) — chips acima do composer até
  // o turno terminar. steer=injeta no turno em curso; senão vira turno de continuação.
  const [queued, setQueued] = useState<{ id: string; text: string; steer: boolean }[]>([]);
  const queuedRef = useRef(queued);
  queuedRef.current = queued;
  // Subsistema de GERAÇÃO (streaming/tools/imagem/conhecimento/áudio/subagentes/
  // guardas/artefato ao vivo + parser SSE + retomada + parar) — extraído p/ um hook.
  // getDeps é lido pós-render, então as deps podem ser declaradas mais abaixo.
  const gen = useGeneration(() => ({
    artifactsEnabled, temporary, setArtifactOpen, setActive,
    reloadMessages, reloadArtifacts, refreshChats, isActiveChat, prepareStreamLanding,
    onReasoningEffort: (e: string) => setReasoningEffort(e as ReasoningEffort),
    onImaginaiTurnComplete: refreshImaginaiCampaign,
  }));
  const {
    streaming, setStreaming, streamingReasoning, setStreamingReasoning, streamingSteps,
    toolEvents, setToolEvents, generatingImage, setGeneratingImage,
    consultingKnowledge, setConsultingKnowledge, transcribingAudio, setTranscribingAudio,
    subagents, setSubagents, guardNote, setGuardNote, liveArtifact, setLiveArtifact,
    sending, setSending, streamPhase, setStreamPhase, stopRef, makeStreamHandler, resumeStream, handleStop,
  } = gen;
  // mantém o espelho do chat ativo em dia (cobre todos os setActive de uma vez)
  useEffect(() => { activeIdRef.current = active?.id ?? null; }, [active?.id]);

  // Ativar o Imaginai materializa um World Kernel por chat. O POST é idempotente:
  // voltar ao mini app apenas recupera a mesma campanha, sem duplicar entidades.
  useEffect(() => {
    const chatId = active?.id;
    if (activeMiniApp !== "imaginai" || !chatId) {
      setImaginaiSnapshot(null);
      setImaginaiLoading(false);
      setImaginaiError(null);
      return;
    }
    let cancelled = false;
    setImaginaiLoading(true);
    setImaginaiError(null);
    api.post<ImaginaiSnapshot>("/mini-apps/imaginai/campaigns", {
      chat_id: chatId,
      name: "Nome da Campanha",
      system_key: "dnd5e",
      system_version: "5e",
      character_name: "Nome do personagem",
    }).then((snapshot) => {
      if (!cancelled) setImaginaiSnapshot(snapshot);
    }).catch((error: unknown) => {
      if (cancelled) return;
      setImaginaiSnapshot(null);
      setImaginaiError(error instanceof Error ? error.message : "Não foi possível abrir a campanha");
    }).finally(() => {
      if (!cancelled) setImaginaiLoading(false);
    });
    return () => { cancelled = true; };
  }, [active?.id, activeMiniApp]);
  // Artefatos (janela dedicada): lista do chat + qual está aberto (o "ao vivo"
  // vive no hook de geração)
  const [chatArtifacts, setChatArtifacts] = useState<ChatArtifact[]>([]);
  const [artifactOpen, setArtifactOpen] = useState<string | null>(null);
  // painel "Informações" do chat (menu dos 3 pontinhos)
  const [infoChatId, setInfoChatId] = useState<string | null>(null);
  // "@" no promptbox: agente (modelo custom) que recebe SÓ o próximo turno
  const [agentId, setAgentId] = useState<string | null>(null);
  // mesa-redonda: rodando + fala em streaming do participante atual
  const [rtRunning, setRtRunning] = useState(false);
  const [rtStreaming, setRtStreaming] = useState<RoundtableStream | null>(null);
  const rtAbort = useRef<AbortController | null>(null);
  // Mesa-redonda usa um SSE próprio e antes atualizava React por token. Espelhamos
  // o acumulador em ref e pintamos no mesmo ritmo do chat normal.
  const rtLiveRef = useRef<RoundtableStream | null>(null);
  const rtFlushTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => {
    if (rtFlushTimerRef.current !== null) clearTimeout(rtFlushTimerRef.current);
  }, []);

  // seleção de modelo (vale para home e para o chat ativo)
  const [curModel, setCurModel] = useState("");
  const [curCustomId, setCurCustomId] = useState<string | null>(null);
  // true depois que o usuário abriu um chat/escolheu um modelo — impede o
  // "modelo padrão" (carregado async no boot) de sobrescrever a seleção
  const modelChosenRef = useRef(false);
  // rascunho de controles (system prompt / params) usado quando ainda não há chat ativo
  const [draftSystemPrompt, setDraftSystemPrompt] = useState("");
  const [draftParams, setDraftParams] = useState<Record<string, unknown>>({});
  // Impede que um envio imediatamente após trocar o nível ultrapasse o PATCH
  // que persiste a preferência no chat.
  const reasoningSaveRef = useRef<Promise<unknown> | null>(null);

  const [temporary, setTemporary] = useState(false);
  const [workspaceOpen, setWorkspaceOpen] = useState(false);
  // muda a cada clique em "Espaço de Trabalho" na barra lateral: força remontar o
  // WorkspaceView (via key) para SEMPRE voltar à grade inicial, mesmo se o usuário
  // estava dentro de um editor (modelo/skill) ou seção.
  const [workspaceKey, setWorkspaceKey] = useState(0);
  // quando != null, o Espaço de Trabalho abre direto no editor deste modelo
  const [editModelTarget, setEditModelTarget] = useState<ModelConfig | null>(null);
  // quando != null, o Espaço de Trabalho abre direto nesta seção (ex.: Analítica)
  const [workspaceSection, setWorkspaceSection] = useState<WorkspaceSection | null>(null);
  const [collapsed, setCollapsed] = useState(false);
  const [showControls, setShowControls] = useState(false);
  // barra lateral de arquivos do Codespace (só existe quando o chat está vinculado
  // a um projeto — active.project_id)
  const [csFilesOpen, setCsFilesOpen] = useState(false);
  // largura da coluna "Arquivos do projeto" (desktop) — arrastável pelo divisor
  // e lembrada entre sessões. No mobile o painel é tela cheia (largura ignorada).
  const [csFilesW, setCsFilesW] = useState<number>(() => {
    if (typeof window === "undefined") return 480;
    const saved = Number(window.localStorage.getItem("cs_files_w"));
    return Number.isFinite(saved) && saved >= 300 ? saved : 480;
  });
  const csFilesRef = useRef<HTMLDivElement | null>(null);
  const startCsResize = useCallback((e: React.PointerEvent) => {
    e.preventDefault();
    const panel = csFilesRef.current;
    if (!panel) return;
    const right = panel.getBoundingClientRect().right;
    const prevSelect = document.body.style.userSelect;
    document.body.style.userSelect = "none";           // sem seleção fantasma no arrasto
    document.body.style.cursor = "col-resize";
    // teto = 70% do CONTÊINER de colunas (verdade de layout — viewport/emulação
    // mentem em ambiente remoto): nunca engole a conversa inteira
    const maxW = Math.round(
      (panel.parentElement?.getBoundingClientRect().width ?? window.innerWidth) * 0.7,
    );
    const onMove = (ev: PointerEvent) => {
      const w = Math.round(right - ev.clientX);
      setCsFilesW(Math.max(300, Math.min(w, maxW)));
    };
    const onUp = () => {
      document.body.style.userSelect = prevSelect;
      document.body.style.cursor = "";
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      setCsFilesW((w) => { window.localStorage.setItem("cs_files_w", String(w)); return w; });
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  }, []);
  // painéis redimensionáveis pelo divisor (desktop): artefatos, Controles (à direita) e
  // a barra lateral esquerda. Cada um lembra a largura no localStorage.
  const artResize = useHResize("artifact_w", 620, 380, 960, "left");
  const ctrlResize = useHResize("controls_w", 340, 300, 620, "left");
  const sbResize = useHResize("sidebar_w", 256, 210, 460, "right", true);
  const [csDropOver, setCsDropOver] = useState(false);
  // projeto do chat ativo (pro botão "Definir como padrão do projeto" saber o
  // padrão atual); null = chat sem projeto
  const [csProject, setCsProject] = useState<CodespaceProject | null>(null);
  useEffect(() => {
    const pid = active?.project_id;
    if (!pid) { setCsProject(null); return; }
    api.get<CodespaceProject>(`/codespace/projects/${pid}`).then(setCsProject).catch(() => setCsProject(null));
  }, [active?.project_id]);

  async function setAsProjectDefault() {
    const pid = active?.project_id;
    const value = curCustomId ? `custom:${curCustomId}` : curModel;
    if (!pid || !value) return;
    try {
      const updated = await api.patch<CodespaceProject>(`/codespace/projects/${pid}`, { default_model: value });
      setCsProject(updated);
    } catch { /* melhor esforço — o botão continua mostrando o estado antigo */ }
  }

  // arraste do explorador do Codespace até aqui: um ARQUIVO inteiro ou só um
  // TRECHO selecionado no visualizador (que já vem com o intervalo de linhas)
  async function handleComposerFileDrop(e: React.DragEvent) {
    setCsDropOver(false);  // sempre limpa o realce, seja qual for o conteúdo solto

    const rawSnippet = e.dataTransfer.getData(CODESPACE_SNIPPET_MIME);
    if (rawSnippet) {
      e.preventDefault();
      try {
        const s: CodespaceSnippetPayload = JSON.parse(rawSnippet);
        const linhas = s.startLine === s.endLine
          ? `linha ${s.startLine}`
          : `linhas ${s.startLine}-${s.endLine}`;
        // o intervalo vai no texto: é o que permite a IA dizer "na linha 42 do X"
        // e usar code.files.write no lugar certo sem reler o arquivo inteiro
        const prefill =
          `Sobre \`${s.path}\` (${linhas}):\n\n\`\`\`${extLang(s.path)}\n${s.text}\n\`\`\`\n\n`;
        setInput((v) => (v ? `${v}\n\n${prefill}` : prefill));
      } catch { /* payload inválido — ignora */ }
      return;
    }

    const raw = e.dataTransfer.getData(CODESPACE_DND_MIME);
    if (!raw) return;
    e.preventDefault();
    let payload: CodespaceDragPayload;
    try {
      payload = JSON.parse(raw);
    } catch {
      return;
    }
    if (payload.kind !== "file") return;
    try {
      const r = await api.get<{ content?: string; total_lines?: number; end_line?: number; error?: string }>(
        `/codespace/projects/${payload.projectId}/files/content?path=${encodeURIComponent(payload.path)}`,
      );
      if (r.error || r.content == null) return;
      // arquivo grande vem cortado do servidor — marca o corte, senão a IA acha
      // que o arquivo acaba ali
      const truncated = !!r.total_lines && !!r.end_line && r.end_line < r.total_lines;
      const body = stripLineNumbers(r.content)
        + (truncated ? `\n… (arquivo truncado — ${r.total_lines} linhas no total)` : "");
      const prefill = `Sobre o arquivo \`${payload.path}\`:\n\n\`\`\`${extLang(payload.path)}\n${body}\n\`\`\`\n\n`;
      setInput((v) => (v ? `${v}\n\n${prefill}` : prefill));
    } catch { /* falha ao ler — ignora, o usuário pode tentar de novo */ }
  }
  const [showShare, setShowShare] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  // mobile: drawer da barra lateral + detecção de tela pequena (< md)
  const [mobileNav, setMobileNav] = useState(false);
  const [isMobile, setIsMobile] = useState(false);
  const [showPalette, setShowPalette] = useState(false);
  const [settingsCat, setSettingsCat] = useState<string | undefined>(undefined);
  const [settingsView, setSettingsView] = useState<string | undefined>(undefined);
  const openSettings = useCallback((cat?: string, view?: string) => { setSettingsCat(cat); setSettingsView(view); setShowSettings(true); }, []);
  const [showArchived, setShowArchived] = useState(false);
  const [showChatMgr, setShowChatMgr] = useState(false);
  const [showCompactions, setShowCompactions] = useState(false);

  const [recording, setRecording] = useState(false);
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  // notificações (toast + som), controladas pela config "Notificações" da Conta
  const [toasts, setToasts] = useState<{ id: number; title: string; body?: string }[]>([]);
  const recorderRef = useRef<{ stop: () => Promise<Blob> } | null>(null);
  const browserDictRef = useRef<{ stop: () => Promise<string> } | null>(null);
  // Modo voz (assistente hands-free): fase visível no HUD + controle do loop.
  const [voicePhase, setVoicePhase] = useState<"off" | "listening" | "thinking" | "speaking">("off");
  const [voiceLevel, setVoiceLevel] = useState(0);
  const voiceRef = useRef<{ active: boolean; utter: { stop: () => void; cancel: () => void } | null; session: VoiceSession | null }>({ active: false, utter: null, session: null });
  const [speakingMessageId, setSpeakingMessageId] = useState<string | null>(null);
  const [speechProgress, setSpeechProgress] = useState<SpeechProgress>({
    phase: "idle", currentTime: 0, duration: 0, rate: 1, seekable: false,
  });
  // Id/run são transitórios: ficam em ref para trocar/parar a leitura sem fazer
  // callbacks de todas as mensagens dependerem do estado que muda a cada clique.
  const messageSpeechRef = useRef<{ id: string | null; run: number }>({ id: null, run: 0 });
  const scrollRef = useRef<HTMLDivElement>(null);
  // O scroll é agendado em um frame, não a cada delta. A pausa durante a seleção
  // protege o intervalo que o usuário está arrastando para copiar.
  const stickFrameRef = useRef<number | null>(null);
  const lastStickyHeightRef = useRef(-1);
  const selectingTextRef = useRef(false);
  // Durante a troca stream -> mensagem persistida, preserva o grude que existia
  // antes do DOM trocar de identidade. Um scroll manual cancela esta intenção.
  const forceBottomAfterLandingRef = useRef(false);
  // botão "ir até o fim": visível só quando o usuário rolou p/ cima
  const [atBottom, setAtBottom] = useState(true);
  // id da última mensagem cujo seletor de opções (kind:ask) foi dispensado
  const [dismissedAsk, setDismissedAsk] = useState<string | null>(null);
  const typeTimer = useRef<ReturnType<typeof setInterval> | null>(null);

  const onScrollArea = () => {
    const el = scrollRef.current;
    if (!el) return;
    const next = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    setAtBottom((current) => current === next ? current : next);
  };
  const scrollToBottom = () => {
    const el = scrollRef.current;
    if (!el) return;
    // Esta é uma ação explícita do usuário. Uma animação `smooth` podia terminar
    // antes de o streaming/imagens terminarem de alterar a altura do painel,
    // deixando o botão aparentemente "no meio" da última resposta.
    selectingTextRef.current = false;
    forceBottomAfterLandingRef.current = true;
    lastStickyHeightRef.current = -1;
    setAtBottom(true);
    const moveToEnd = () => {
      const current = scrollRef.current;
      if (current) current.scrollTop = current.scrollHeight;
    };
    moveToEnd();
    requestAnimationFrame(moveToEnd);
  };
  useEffect(() => () => {
    if (stickFrameRef.current !== null) cancelAnimationFrame(stickFrameRef.current);
  }, []);

  // "digita" o texto da sugestão no promptbox, caractere a caractere
  function typeSuggestion(text: string) {
    if (typeTimer.current) clearInterval(typeTimer.current);
    let i = 0;
    setInput("");
    typeTimer.current = setInterval(() => {
      i += 2;
      setInput(text.slice(0, i));
      if (i >= text.length) {
        clearInterval(typeTimer.current!);
        typeTimer.current = null;
      }
    }, 14);
  }
  useEffect(() => () => {
    if (typeTimer.current) clearInterval(typeTimer.current);
  }, []);

  // sorteia sugestões ao montar (rotaciona a cada visita à página)
  useEffect(() => {
    setSuggestions(pickSuggestions(3));
  }, []);

  const refreshChats = useCallback(async () => {
    const list = await api.get<Chat[]>("/chats");
    setChats(list);
    return list;
  }, []);
  const refreshFolders = useCallback(() => api.get<Folder[]>("/folders").then(setFolders).catch(() => {}), []);
  const refreshModels = useCallback(() => api.get<ModelConfig[]>("/models").then(setCustomModels).catch(() => {}), []);
  // skills alimentam o seletor "$" do compositor; refetch p/ não ficar defasado
  // depois que o usuário cria/importa/edita uma skill no Espaço de Trabalho.
  const refreshSkills = useCallback(() => api.get<Skill[]>("/skills").then(setSkills).catch(() => {}), []);
  // modelos externos = OpenRouter + provedores customizados + locais do Ollama +
  // assinaturas (mesclados no seletor). Cada fetch é independente: sem chave OpenRouter
  // ainda mostra os locais/provedores, e vice-versa.
  const refreshExtModels = useCallback(async () => {
    const [ext, providers, local, subs] = await Promise.all([
      api.get<Model[]>("/settings/models").catch(() => [] as Model[]),
      api.get<Model[]>("/integrations/providers/models").catch(() => [] as Model[]),
      api.get<Model[]>("/integrations/ollama/models").catch(() => [] as Model[]),
      api.get<Model[]>("/integrations/subscriptions/chatgpt/models").catch(() => [] as Model[]),
    ]);
    setExtModels([...ext, ...providers, ...local, ...subs]);
  }, []);

  function applyDefaultModel(u: User, customs: ModelConfig[]) {
    // este carregamento é assíncrono e LENTO (espera chats + modelos): se o
    // usuário já abriu um chat (deep-link ?c= no F5) ou escolheu um modelo
    // enquanto isso, NÃO sobrescreve — senão o seletor marca o modelo padrão
    // em vez do modelo do chat aberto.
    if (modelChosenRef.current) return;
    const dm = u.default_model;
    if (!dm) return;
    if (dm.startsWith("custom:")) {
      const mc = customs.find((c) => c.id === dm.slice(7));
      if (mc) {
        setCurModel(mc.base_model);
        setCurCustomId(mc.id);
      }
      // modelo apagado: a referência ficou pendurada. NÃO cair no setCurModel(dm)
      // abaixo — isso colocava a string crua "custom:<uuid>" como se fosse um modelo
      // (era o rótulo esquisito no seletor). Mantém o modelo atual e limpa o padrão
      // (cura bancos que já ficaram nesse estado antes do fix no DELETE do modelo).
      else {
        api.put("/settings/default-model", { model: "" }).catch(() => {});
        setUser({ ...u, default_model: null });
      }
      return;
    }
    setCurModel(dm);
    setCurCustomId(null);
  }

  useEffect(() => {
    api
      .get<User>("/auth/me")
      .then(async (u) => {
        if (u.status !== "active") {
          router.replace("/pending");
          return;
        }
        setUser(u);
        await refreshChats();
        refreshFolders();
        const customs = await api.get<ModelConfig[]>("/models").catch(() => [] as ModelConfig[]);
        setCustomModels(customs);
        applyDefaultModel(u, customs);
        refreshExtModels();
        api.get<Tool[]>("/tools").then(setTools).catch(() => {});
        api.get<SystemTool[]>("/tools/system").then(setSystemTools).catch(() => {});
        api.get<Prompt[]>("/prompts")
          .then((ps) => setPrompts([...ps, LEARN_BUILTIN]))
          .catch(() => setPrompts([LEARN_BUILTIN]));
        refreshSkills();
      })
      .catch((e) => {
        if (e instanceof ApiError && e.status === 401) router.replace("/login");
      });
  }, [router, refreshChats, refreshFolders, refreshExtModels, refreshSkills]);

  // preserva o estado expandido/encolhido da sidebar entre sessões
  useEffect(() => {
    setCollapsed(localStorage.getItem("sidebarCollapsed") === "1");
  }, []);

  // recarrega os modelos custom ao focar a janela (pega avatar/edições recentes)
  useEffect(() => {
    const onFocus = () => { refreshModels(); refreshSkills(); };
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, [refreshModels, refreshSkills]);

  function toggleCollapse() {
    setCollapsed((v) => {
      const next = !v;
      localStorage.setItem("sidebarCollapsed", next ? "1" : "0");
      return next;
    });
  }

  // dispara uma notificação (som + toast + notificação nativa se a aba estiver
  // em segundo plano) quando a config "Notificações" da Conta está ligada.
  const notify = useCallback((title: string, body?: string) => {
    if (!(user?.profile as Record<string, unknown> | undefined)?.notifications) return;
    playChime();
    browserNotify(title, body);
    const id = Date.now() + Math.random();
    setToasts((t) => [...t, { id, title, body }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 4500);
  }, [user]);
  const dismissToast = (id: number) => setToasts((t) => t.filter((x) => x.id !== id));

  // onboarding (1º uso, por-usuário): dispara quando falta a chave do OpenRouter
  // e o usuário ainda não concluiu/pulou o wizard.
  const [showOnboarding, setShowOnboarding] = useState(false);
  useEffect(() => {
    if (!user || (user.profile as Record<string, unknown> | undefined)?.onboarded) return;
    api.get<{ openrouter: boolean }>("/settings/secrets")
      .then((s) => {
        if (!s.openrouter) setShowOnboarding(true);
        else api.put("/settings/profile", { onboarded: true }).catch(() => {}); // já tem chave → não incomoda
      })
      .catch(() => {});
  }, [user]);

  // orçamento pessoal: banner quando o usuário passa do teto do mês
  const [budget, setBudget] = useState<{ enabled: boolean; over: boolean; blocked: boolean; spent: number; cap: number; mode: string } | null>(null);
  const refreshBudget = useCallback(() => {
    api.get<typeof budget>("/settings/usage/summary").then(setBudget).catch(() => {});
  }, []);
  useEffect(() => { refreshBudget(); }, [refreshBudget]);

  // pede permissão de notificação nativa quando o usuário liga "Notificações"
  useEffect(() => {
    if ((user?.profile as Record<string, unknown> | undefined)?.notifications) requestNotifPermission();
  }, [user]);

  // "Animações" (Configurações → Geral): classe global que corta transições
  useEffect(() => {
    const off = (user?.profile as Record<string, unknown> | undefined)?.animations === false;
    document.documentElement.classList.toggle("no-animations", off);
  }, [user]);

  // formato de hora/data (Configurações → Personalização) para os utilitários
  useEffect(() => {
    setFormatPrefs(user?.profile as { time_format?: string; date_format?: string } | undefined);
  }, [user]);

  // estado volátil legível dentro de efeitos/timers sem virar dependência
  // (também usado pelo poll de 15s lá embaixo)
  const pollRef = useRef({ active, sending, streaming, atBottom });
  pollRef.current = { active, sending, streaming, atBottom };
  // espelho de mensagens/sending p/ o loop do modo voz ler o estado mais recente
  const messagesRef = useRef(messages);
  messagesRef.current = messages;

  // auto-scroll "grudento": segue o conteúdo novo SÓ se o usuário já está no fim.
  // Antes rolava SEMPRE — impossível rolar p/ ler painéis expandidos (ferramentas/
  // custo) durante o streaming ou quando o poll recarregava as mensagens: a página
  // puxava o usuário de volta pro fundo a cada evento.
  const stickToBottom = useCallback(() => {
    const forced = forceBottomAfterLandingRef.current;
    if (!pollRef.current.atBottom && !forced) return;
    if (selectingTextRef.current) return;
    // usuário selecionando texto: rolar agora arrasta o conteúdo sob o cursor e
    // desfaz a seleção (impossível copiar enquanto a IA responde) — pausa o grude
    const sel = typeof window !== "undefined" ? window.getSelection() : null;
    if (sel && !sel.isCollapsed) return;
    if (stickFrameRef.current !== null) return;
    stickFrameRef.current = requestAnimationFrame(() => {
      stickFrameRef.current = null;
      const stillForced = forceBottomAfterLandingRef.current;
      if ((!pollRef.current.atBottom && !stillForced) || selectingTextRef.current) return;
      const selection = window.getSelection();
      if (selection && !selection.isCollapsed) return;
      const el = scrollRef.current;
      if (!el) return;
      const height = el.scrollHeight;
      // Evita uma escrita no scroll (e a sincronização de layout resultante) se
      // a altura não mudou desde a última pintura acompanhada.
      if (!stillForced && height === lastStickyHeightRef.current) return;
      lastStickyHeightRef.current = height;
      el.scrollTop = height;
      forceBottomAfterLandingRef.current = false;
    });
  }, []);
  const prepareStreamLanding = useCallback(() => {
    forceBottomAfterLandingRef.current = pollRef.current.atBottom;
    if (forceBottomAfterLandingRef.current) lastStickyHeightRef.current = -1;
  }, []);
  useEffect(() => {
    stickToBottom();
  }, [messages, streaming, streamingReasoning, toolEvents, stickToBottom]);

  // Mídia que carrega DEPOIS do layout (imagem da Base de Conhecimento, anexo) muda a
  // altura da mensagem após a rolagem inicial → a última mensagem afundava atrás do
  // composer e não dava p/ alcançá-la (o bug de "não rola até o fim"). 'load' não
  // borbulha, então ouvimos na fase de captura no container e re-grudamos no fim.
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const onMedia = () => stickToBottom();
    el.addEventListener("load", onMedia, true);
    el.addEventListener("error", onMedia, true); // imagem quebrada também altera a altura
    return () => {
      el.removeEventListener("load", onMedia, true);
      el.removeEventListener("error", onMedia, true);
    };
  }, [messages.length, stickToBottom]);

  // NOTA: aqui existia uma medição da altura do composer (ResizeObserver + rAF +
  // timeout + resize) que virava o padding-bottom da área de rolagem. Foi REMOVIDA:
  // o composer voltou a ser item de fluxo, então a altura da área de rolagem é
  // responsabilidade do navegador. Não reintroduzir — era a origem do bug recorrente
  // "o scroll morre antes do fim" (qualquer atraso da medição escondia o fim).
  const hasConversation = messages.length > 0 || !!streaming || rtRunning || !!rtStreaming;
  // A cauda da conversa E as três respostas mais recentes da IA permanecem completas
  // e com layout exato. Contar só `slice(-3)` deixava, na prática, apenas uma resposta
  // da IA sem clamp porque as mensagens do usuário também ocupavam essas posições.
  const recentFullMessageIds = useMemo(
    () => {
      const ids = new Set(messages.slice(-3).map((m) => m.id));
      let assistantCount = 0;
      for (let i = messages.length - 1; i >= 0 && assistantCount < 3; i -= 1) {
        const message = messages[i];
        if (message.role !== "assistant" || message.is_summary) continue;
        ids.add(message.id);
        assistantCount += 1;
      }
      return ids;
    },
    [messages],
  );

  // detecta tela pequena (< md = 768px) p/ virar a barra lateral em drawer
  useEffect(() => {
    const mq = window.matchMedia("(max-width: 767px)");
    const on = () => setIsMobile(mq.matches);
    on();
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  // ao voltar para o desktop, fecha o drawer
  useEffect(() => {
    if (!isMobile) setMobileNav(false);
  }, [isMobile]);

  // deep-link: /chat?c=<id> abre o chat direto (ex.: vindo de uma notificação);
  // ?v=automations abre a tela de Automações (rota antiga /automations redireciona)
  useEffect(() => {
    const qs = new URLSearchParams(window.location.search);
    if (qs.get("v") === "automations") { setWorkspaceSection("Automacoes"); setWorkspaceOpen(true); }
    if (qs.get("v") === "analytics") { setWorkspaceSection("Analítica"); setWorkspaceOpen(true); }
    const cid = qs.get("c");
    if (cid) {
      selectChat(cid).catch(() => {});
      window.history.replaceState(null, "", "/chat");
    }
    // volta de um fluxo OAuth: os callbacks redirecionam para /chat?<serviço>=connected
    // (ou =error&reason=…). Sem isto o usuário autorizava e caía numa tela idêntica à
    // que deixou, sem saber se deu certo. Vai direto no toast, e não pelo `notify`,
    // porque confirmar uma ação que o usuário acabou de fazer não é "notificação"
    // opcional — tem que aparecer mesmo com as notificações desligadas.
    const SERVICOS: Record<string, string> = {
      openrouter: "OpenRouter", google: "Google", github: "GitHub",
      notion: "Notion", slack: "Slack", chatgpt: "ChatGPT",
    };
    for (const [param, nome] of Object.entries(SERVICOS)) {
      const v = qs.get(param);
      if (!v) continue;
      const ok = v === "connected";
      const id = Date.now() + Math.random();
      setToasts((t) => [...t, {
        id,
        title: ok ? `${nome} conectado` : `Falha ao conectar ${nome}`,
        body: ok ? undefined : (qs.get("reason") || undefined),
      }]);
      setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), ok ? 4500 : 9000);
      break;
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // mantém o chat ativo refletido na URL (?c=<id>) — assim um F5 reabre o mesmo
  // chat e o resumeStream re-assina uma geração em andamento. DEVE vir DEPOIS do
  // efeito de deep-link acima (senão limparia o ?c= antes dele ler). goHome=/chat.
  useEffect(() => {
    window.history.replaceState(null, "", active ? `/chat?c=${active.id}` : "/chat");
  }, [active]);

  const curCustom = useMemo(
    () => customModels.find((c) => c.id === curCustomId) ?? null,
    [customModels, curCustomId],
  );

  // agentes invocáveis com "@" na promptbox = os modelos custom do usuário
  const agentsForMention = useMemo(
    () => customModels.map((c) => ({ id: c.id, name: c.name })),
    [customModels],
  );

  // refs "#" = bases de conhecimento ACESSÍVEIS ao chat/modelo atual (mesmo gate do RAG).
  // Sem chat/modelo custom, ainda busca (o endpoint devolve as bases do PERFIL).
  useEffect(() => {
    const params = new URLSearchParams();
    if (active?.id) params.set("chat_id", active.id);
    if (curCustomId) params.set("model_config_id", curCustomId);
    const qs = params.toString();
    let alive = true;
    api.get<KnowledgeRef[]>(`/knowledge/refs${qs ? `?${qs}` : ""}`)
      .then((r) => { if (alive) setKnowledgeRefs(r); })
      .catch(() => { if (alive) setKnowledgeRefs([]); });
    return () => { alive = false; };
  }, [active?.id, active?.knowledge_config, curCustomId]);

  // ferramentas que o modelo ATIVO pode usar (embutidas + do usuário), resolvidas
  // a nome+descrição+origem para o menu da chave inglesa na promptbox.
  const modelTools = useMemo(() => {
    if (!curCustom?.tools_enabled) return [];
    const sysByPath = new Map(systemTools.map((t) => [t.path, t]));
    const userById = new Map(tools.map((t) => [t.id, t]));
    const out: { name: string; description?: string; category?: SystemTool["category"]; integration?: string }[] = [];
    for (const id of curCustom.tool_ids ?? []) {
      if (typeof id !== "string") continue;
      if (id.startsWith("builtin:")) {
        const s = sysByPath.get(id.slice(8));
        if (s) out.push({ name: s.name, description: s.description, category: s.category, integration: s.integration });
      } else {
        const t = userById.get(id);
        if (t?.enabled) out.push({ name: t.name || t.path, description: t.description });
      }
    }
    return out;
  }, [curCustom, systemTools, tools]);
  const modelLabel = useMemo(() => {
    if (curCustom) return curCustom.name;
    const ext = extModels.find((m) => m.id === curModel);
    return ext?.name ?? curModel;
  }, [curCustom, extModels, curModel]);

  // "Ler em voz alta": usa a voz do modelo que PRODUZIU a mensagem (casa o nome do
  // modelo da resposta com um modelo custom), caindo no modelo atual do chat. Sem
  // isto o botão usava só o modelo selecionado, ignorando a voz configurada.
  const voiceSettingsFor = useCallback((m: Message): { voice?: string; modelConfigId?: string; enabled: boolean } => {
    const configId = m.usage?.model_config_id;
    const byId = configId ? customModels.find((c) => c.id === configId) : undefined;
    const name = m.usage?.model_name;
    // Mensagens antigas não têm model_config_id. Só usa nome como fallback se
    // houver UMA correspondência; nomes repetidos jamais devem escolher a voz de
    // outro preset por acidente.
    const sameName = name ? customModels.filter((c) => c.name === name) : [];
    const legacyByName = sameName.length === 1 ? sameName[0] : undefined;
    const selected = byId ?? legacyByName ?? curCustom;
    const voiceConfig = selected?.filter_config?.voice as { tts_enabled?: boolean } | undefined;
    return {
      voice: selected?.tts_voice ?? undefined,
      modelConfigId: selected?.id,
      enabled: voiceConfig?.tts_enabled !== false,
    };
  }, [customModels, curCustom]);
  const stopMessageSpeech = useCallback(() => {
    const current = messageSpeechRef.current;
    messageSpeechRef.current = { id: null, run: current.run + 1 };
    stopSpeaking();
    setSpeakingMessageId(null);
  }, []);
  const toggleMessageSpeech = useCallback((m: Message) => {
    const current = messageSpeechRef.current;
    if (current.id === m.id) {
      stopMessageSpeech();
      return;
    }
    stopSpeaking();
    const run = current.run + 1;
    messageSpeechRef.current = { id: m.id, run };
    setSpeakingMessageId(m.id);
    const voiceSettings = voiceSettingsFor(m);
    if (!voiceSettings.enabled) {
      messageSpeechRef.current = { id: null, run };
      setSpeakingMessageId(null);
      return;
    }
    void speak(m.content, voiceSettings.voice, voiceSettings.modelConfigId).finally(() => {
      if (messageSpeechRef.current.run !== run) return;
      messageSpeechRef.current = { id: null, run };
      setSpeakingMessageId(null);
    });
  }, [voiceSettingsFor, stopMessageSpeech]);
  useEffect(() => subscribeSpeechProgress(setSpeechProgress), []);
  useEffect(() => () => {
    messageSpeechRef.current.run += 1;
    stopSpeaking();
  }, []);

  // ---------------------------------------------------------------------------
  // Mesa-redonda (multi-modelo): modelos conversam entre si; o usuário guia.
  // ---------------------------------------------------------------------------
  // barra da mesa visível? o botão vira um toggle on/off quando já está na mesa
  const [rtBarOpen, setRtBarOpen] = useState(true);
  // RASCUNHO da mesa: entra na mesa SEM criar o chat; só materializa no 1º envio/rodar
  const [draftRt, setDraftRt] = useState<{ participants: RoundtableParticipant[]; config: RoundtableConfig } | null>(null);
  const isRoundtable = active ? active.mode === "roundtable" : !!draftRt;
  const participants = useMemo<RoundtableParticipant[]>(
    () => (active ? (active.participants as RoundtableParticipant[]) ?? [] : draftRt?.participants ?? []),
    [active, draftRt],
  );
  const rtConfig = useMemo<RoundtableConfig>(
    () => (active ? (active.roundtable_config as RoundtableConfig) ?? {} : draftRt?.config ?? {}),
    [active, draftRt],
  );
  const rid = () => (typeof crypto !== "undefined" && crypto.randomUUID ? crypto.randomUUID() : `p-${Date.now()}-${Math.random()}`);

  // aplica um patch no chat da mesa (mode/participants/config) — otimista + PATCH
  const patchRoundtable = useCallback(async (patch: Partial<Chat>) => {
    if (!active) return;
    setActive({ ...active, ...patch } as Chat);
    try { await api.patch(`/chats/${active.id}`, patch); } catch { /* ignore */ }
  }, [active]);

  function enterRoundtable() {
    if (active) {
      // chat já existe: liga o modo mesa nele (mantém o comportamento antigo)
      const seed: RoundtableParticipant[] = active.participants && active.participants.length
        ? active.participants
        : [{ id: rid(), model: active.model || curModel, model_config_id: active.model_config_id ?? curCustomId, name: modelLabel || "Modelo 1", color: RT_COLORS[0] }];
      const cfg = active.roundtable_config ?? { turn_policy: "round_robin" as const, max_rounds: 6 };
      setActive({ ...active, mode: "roundtable", participants: seed, roundtable_config: cfg });
      api.patch(`/chats/${active.id}`, { mode: "roundtable", participants: seed, roundtable_config: cfg }).catch(() => {});
      return;
    }
    // SEM chat: só um RASCUNHO — nada é criado até o 1º envio/rodar
    if (!curModel) { alert("Selecione um modelo primeiro."); return; }
    setDraftRt({
      participants: [{ id: rid(), model: curModel, model_config_id: curCustomId, name: modelLabel || "Modelo 1", color: RT_COLORS[0] }],
      config: { turn_policy: "round_robin", max_rounds: 6 },
    });
  }

  // materializa o chat da mesa a partir do rascunho (no 1º envio/rodar)
  async function ensureRoundtableChat(): Promise<Chat | null> {
    if (active) return active;
    if (!draftRt) return null;
    if (!curModel) { alert("Selecione um modelo primeiro."); return null; }
    // mesa iniciada no modo TEMPORÁRIO → chat view_once (apagado ao sair), a
    // versão "não salva" possível p/ a mesa (que exige um chat persistido).
    const viewOnce = temporary;
    const chat = await api.post<Chat>("/chats", { title: "Mesa-redonda", model: curModel, model_config_id: curCustomId });
    const full = { ...chat, mode: "roundtable" as const, participants: draftRt.participants, roundtable_config: draftRt.config, view_once: viewOnce };
    setActive(full);
    activeIdRef.current = chat.id;
    setDraftRt(null);
    if (viewOnce) setTemporary(false); // agora é uma mesa view_once (persistida até sair)
    try {
      await api.patch(`/chats/${chat.id}`, {
        mode: "roundtable", participants: draftRt.participants, roundtable_config: draftRt.config,
        ...(viewOnce ? { view_once: true } : {}),
      });
    } catch { /* ignore */ }
    refreshChats();
    return full;
  }

  // aplica um patch de mesa no chat (se existe) OU no rascunho (se ainda não existe)
  function patchRt(next: { participants?: RoundtableParticipant[]; config?: RoundtableConfig; exit?: boolean }) {
    if (active) {
      const patch: Partial<Chat> = {};
      if (next.participants) patch.participants = next.participants;
      if (next.config) patch.roundtable_config = next.config;
      if (next.exit) patch.mode = "single";
      patchRoundtable(patch);
    } else if (next.exit) {
      setDraftRt(null);
    } else {
      setDraftRt((d) => ({
        participants: next.participants ?? d?.participants ?? [],
        config: next.config ?? d?.config ?? { turn_policy: "round_robin", max_rounds: 6 },
      }));
    }
  }

  function addParticipant(p: { model: string; model_config_id?: string | null; name: string; avatar?: string | null }) {
    const part: RoundtableParticipant = {
      id: rid(), model: p.model, model_config_id: p.model_config_id ?? null,
      name: p.name, avatar: p.avatar ?? null, color: nextColor(participants.map((x) => x.color)),
    };
    patchRt({ participants: [...participants, part] });
  }
  function removeParticipant(id: string) {
    const next = participants.filter((p) => p.id !== id);
    patchRt(next.length === 0 ? { participants: next, exit: true } : { participants: next });
  }
  function updateParticipant(id: string, patch: Partial<RoundtableParticipant>) {
    patchRt({ participants: participants.map((p) => (p.id === id ? { ...p, ...patch } : p)) });
  }
  function updateRtConfig(patch: Partial<RoundtableConfig>) {
    patchRt({ config: { ...rtConfig, ...patch } });
  }

  // Avatar do FALANTE (mesa-redonda): resolve pelo participante correspondente ao
  // speaker.id → avatar fresco do modelo custom (custom[].avatar_url) ou o avatar
  // salvo no participante. Antes usava sempre `curCustom` (o modelo do composer), por
  // isso todas as bolhas mostravam a imagem do 1º modelo.
  function speakerAvatar(sp?: Speaker | null): string | null {
    if (!sp) return null;
    const part = participants.find((p) => p.id === sp.id);
    if (part?.model_config_id) {
      const mc = customModels.find((c) => c.id === part.model_config_id);
      if (mc) return mc.avatar_url ?? null;
    }
    return part?.avatar ?? null;
  }

  function makeRtHandler() {
    const flush = () => {
      if (rtFlushTimerRef.current !== null) {
        clearTimeout(rtFlushTimerRef.current);
        rtFlushTimerRef.current = null;
      }
      setRtStreaming(rtLiveRef.current ? { ...rtLiveRef.current } : null);
    };
    const queuePaint = () => {
      if (rtFlushTimerRef.current === null) rtFlushTimerRef.current = setTimeout(flush, 70);
    };
    return (ev: any) => {
      if (ev.type === "speaker_start") {
        rtLiveRef.current = { speaker: ev.speaker, content: "", reasoning: "" };
        flush();
        setAtBottom(true);
      } else if (ev.type === "token") {
        const current = rtLiveRef.current;
        if (current) {
          current.content += ev.text || "";
          queuePaint();
        }
      } else if (ev.type === "reasoning") {
        const current = rtLiveRef.current;
        if (current) {
          current.reasoning += ev.text || "";
          queuePaint();
        }
      } else if (ev.type === "speaker_end") {
        const current = rtLiveRef.current;
        flush();
        // turno vazio (o backend não persistiu): não adiciona bolha vazia
        if (current?.content.trim()) {
          setMessages((m) => [...m, {
            id: ev.message_id || `a-${Date.now()}`, role: "assistant", content: current.content,
            reasoning: current.reasoning ? { text: current.reasoning } : null, speaker: ev.speaker,
            created_at: new Date().toISOString(),
          }]);
        }
        rtLiveRef.current = null;
        setRtStreaming(null);
      } else if (ev.type === "error") {
        rtLiveRef.current = null;
        if (rtFlushTimerRef.current !== null) clearTimeout(rtFlushTimerRef.current);
        rtFlushTimerRef.current = null;
        setRtStreaming(null);
      }
    };
  }

  async function runRoundtable(steps: "auto" | "one", content?: string) {
    if (rtRunning) return;
    // 1º rodar/enviar num rascunho de mesa → cria o chat agora (não antes)
    const chat = await ensureRoundtableChat();
    if (!chat) return;
    setRtRunning(true);
    setAtBottom(true);
    if (content && content.trim()) {
      setMessages((m) => [...m, { id: `tmp-${Date.now()}`, role: "user", content, created_at: new Date().toISOString() }]);
    }
    const ac = new AbortController();
    rtAbort.current = ac;
    try {
      await streamRoundtable(
        chat.id,
        { content: content ?? "", steps, next: rtConfig.turn_policy === "manual" ? rtConfig.next ?? null : null },
        makeRtHandler(),
        ac.signal,
      );
    } catch { /* abortado / rede */ }
    finally {
      rtLiveRef.current = null;
      if (rtFlushTimerRef.current !== null) clearTimeout(rtFlushTimerRef.current);
      rtFlushTimerRef.current = null;
      setRtRunning(false);
      setRtStreaming(null);
      rtAbort.current = null;
      reloadMessages(chat.id);
      refreshChats();
    }
  }

  async function pauseRoundtable() {
    if (!active) return;
    try { await api.post(`/chats/${active.id}/roundtable/stop`, {}); } catch { /* ignore */ }
  }

  // padrão de memória herdado (precedência: perfil → modelo), p/ o Controls mostrar
  // o estado quando o chat herda. Sistema = opt-in (desligada).
  const memoryDefault = useMemo(() => {
    const sys = { enabled: false, write: "global", read: { global: true, model: true, chat: true } };
    const prof = ((user?.profile as Record<string, any> | undefined)?.memory) ?? {};
    const mdl = ((curCustom?.capabilities as Record<string, any> | undefined)?.memory) ?? {};
    return { ...sys, ...prof, ...mdl, read: { ...sys.read, ...(prof.read ?? {}), ...(mdl.read ?? {}) } };
  }, [user, curCustom]);

  // "Duração do Chat: ao abrir" (visualização única): sair de um chat view_once o apaga
  function leaveViewOnce(nextId?: string) {
    if (active?.view_once && active.id !== nextId) {
      api.del(`/chats/${active.id}`).catch(() => {});
      setChats((cs) => cs.filter((c) => c.id !== active.id));
    }
  }

  // Solta o composer do turno de OUTRO chat ao navegar. O turno segue no servidor
  // (e o handler dele para de pintar); sem isto, o destino herdava o "gerando" e o
  // botão Parar ainda apontava para o stopRef do chat anterior.
  function releaseGeneration() {
    stopRef.current = null;
    setSending(false);
    setStreamPhase("idle");
    setGeneratingImage(false);
    setConsultingKnowledge(false);
    setTranscribingAudio(false);
    setSubagents([]);
    setGuardNote(null);
    setQueued([]);
  }

  function goHome() {
    selectChatRequestRef.current += 1; // invalida um selectChat ainda em voo
    activeIdRef.current = null;
    leaveViewOnce();
    setActive(null);
    setActiveMiniApp(null);
    setDraftRt(null);
    setMessages([]);
    releaseGeneration();
    setStreaming("");
    setStreamingReasoning("");
    setToolEvents([]);
    setChatArtifacts([]);
    setArtifactOpen(null);
    setLiveArtifact(null);
    setSuggestions(pickSuggestions(3));
    setWorkspaceOpen(false);
  }

  // abre o Espaço de Trabalho numa seção (Automações/Playground/Codespace/… viram
  // seções do Espaço; o "voltar" delas cai no hub). Remonta p/ resetar a subtela.
  function openWorkspace(section: WorkspaceSection | null) {
    selectChatRequestRef.current += 1; // não deixa um GET anterior fechar o workspace
    activeIdRef.current = active?.id ?? null;
    setEditModelTarget(null);
    setWorkspaceSection(section);
    setWorkspaceKey((k) => k + 1);
    setWorkspaceOpen(true);
    setMobileNav(false);
  }

  const reloadMessages = useCallback(async (chatId: string) => {
    const rows = await api.get<Message[]>(`/chats/${chatId}/messages`);
    // Poll/geração do chat anterior pode terminar depois de o usuário navegar.
    if (activeIdRef.current === chatId) setMessages(rows);
  }, []);

  const reloadArtifacts = useCallback(async (chatId: string) => {
    try {
      const rows = await api.get<ChatArtifact[]>(`/chats/${chatId}/artifacts`);
      if (activeIdRef.current === chatId) setChatArtifacts(rows);
    } catch {
      /* rota indisponível/erro transitório: mantém a lista atual */
    }
  }, []);

  const refreshImaginaiCampaign = useCallback(async (chatId: string) => {
    try {
      const snapshot = await api.get<ImaginaiSnapshot>(`/mini-apps/imaginai/campaigns/by-chat/${chatId}`);
      if (activeIdRef.current === chatId) setImaginaiSnapshot(snapshot);
    } catch {
      // O mini app pode ter sido fechado ou a campanha removida junto ao chat.
    }
  }, []);

  // poll leve: automações/lembretes criam chats e mensagens em background — sem
  // isto só apareceriam no F5. Lê o estado volátil via pollRef p/ não resetar o timer.
  useEffect(() => {
    const t = setInterval(() => {
      if (document.hidden) return;
      const s = pollRef.current;
      refreshChats();
      // atualiza a conversa aberta só se o usuário não está enviando nem lendo mais acima
      if (s.active && !s.sending && !s.streaming && s.atBottom) reloadMessages(s.active.id);
    }, 15000);
    return () => clearInterval(t);
  }, [refreshChats, reloadMessages]);

  // toggle "Artefatos" (Configurações → Interface → Chat). Padrão: ligado.
  const iface = ((user?.profile as Record<string, any> | undefined)?.interface as Record<string, any> | undefined) ?? {};
  const artifactsEnabled = iface.artifacts !== false;
  const showShareBtn = iface.chat_share !== false;   // botão compartilhar (topo direito)

  async function selectChat(id: string) {
    const requestId = ++selectChatRequestRef.current;
    // O ref pode apontar para outra navegação ainda em voo. Para restaurar após
    // falha, use o chat realmente renderizado neste momento, não esse ref transitório.
    const renderedActiveId = active?.id ?? null;
    // Bloqueia imediatamente handlers do chat anterior enquanto o destino carrega.
    activeIdRef.current = id;
    if (active?.id !== id) setActiveMiniApp(null);
    setTemporary(false);
    setWorkspaceOpen(false);
    leaveViewOnce(id);
    setDraftRt(null);
    let detail: Chat & { messages: Message[] };
    try {
      detail = await api.get<Chat & { messages: Message[] }>(`/chats/${id}`);
    } catch (error) {
      if (selectChatRequestRef.current === requestId) activeIdRef.current = renderedActiveId;
      throw error;
    }
    if (selectChatRequestRef.current !== requestId) return;
    setActive(detail);
    // atualiza o espelho JÁ (antes do efeito pós-render) para que qualquer handler
    // de stream do chat anterior, ainda em voo, veja na hora que não é mais o ativo.
    activeIdRef.current = id;
    setMessages(detail.messages ?? []);
    setAtBottom(true); // abrir um chat sempre começa no fim
    setStreaming("");
    setStreamingReasoning("");
    setToolEvents([]);
    // libera o composer: se o chat de destino tiver geração viva, o resumeStream
    // (abaixo) religa o "gerando"; senão, não fica preso pelo turno do chat anterior.
    releaseGeneration();
    setArtifactOpen(null);
    setLiveArtifact(null);
    setChatArtifacts([]);
    reloadArtifacts(id);
    modelChosenRef.current = true;
    setCurModel(detail.model);
    // restaura o vínculo com o modelo personalizado (define ferramentas)
    setCurCustomId(detail.model_config_id ?? null);
    // legado: chats antigos do Codespace gravaram model="custom:<id>" CRU (em vez
    // de base_model + model_config_id) — o seletor mostrava o id e o envio
    // falharia no provedor. Resolve pro par certo e conserta o registro na hora.
    // No deep-link (?c= no mount) o customModels ainda não carregou — busca na hora.
    if (detail.model?.startsWith("custom:") && !detail.model_config_id) {
      const customs = customModels.length
        ? customModels
        : await api.get<ModelConfig[]>("/models").catch(() => [] as ModelConfig[]);
      const mc = customs.find((c) => c.id === detail.model.slice(7));
      if (mc) {
        setCurModel(mc.base_model);
        setCurCustomId(mc.id);
        setActive((a) => (a ? { ...a, model: mc.base_model, model_config_id: mc.id } : a));
        api.patch(`/chats/${id}`, { model: mc.base_model, model_config_id: mc.id }).catch(() => {});
      }
    }
    // se havia uma resposta sendo gerada quando o chat foi fechado/atualizado,
    // volta a acompanhá-la ao vivo em vez de mostrar só o que ficou salvo.
    resumeStream(id);
  }

  async function newChat() {
    setTemporary(false);
    // já está na tela de novo chat: não recarrega nem re-sorteia o "Sugerido"
    if (!active && messages.length === 0 && !streaming && !workspaceOpen) return;
    goHome();
  }

  function newChatWithModel(mc: ModelConfig) {
    setTemporary(false);
    modelChosenRef.current = true;
    setCurModel(mc.base_model);
    setCurCustomId(mc.id);
    setDraftParams((current) => {
      const next = { ...current };
      delete next[CHAT_REASONING_EFFORT_PARAM];
      return next;
    });
    goHome();
  }

  // "Editar" no seletor → abre o Espaço de Trabalho direto no editor do modelo
  function editModel(mc: ModelConfig) {
    setEditModelTarget(mc);
    setWorkspaceSection(null);
    setWorkspaceOpen(true);
  }

  async function patchActive(patch: Partial<Chat>) {
    if (!active) return;
    const updated = await api.patch<Chat>(`/chats/${active.id}`, patch);
    setActive(updated);
    await refreshChats();
  }

  // seleção de modelo a partir do picker
  async function selectExternal(id: string) {
    modelChosenRef.current = true;
    setCurModel(id);
    setCurCustomId(null);
    setDraftParams((current) => {
      const next = { ...current };
      delete next[CHAT_REASONING_EFFORT_PARAM];
      return next;
    });
    if (active) await patchActive({ model: id, model_config_id: null });
  }
  async function selectCustom(mc: ModelConfig) {
    modelChosenRef.current = true;
    setCurModel(mc.base_model);
    setCurCustomId(mc.id);
    setDraftParams((current) => {
      const next = { ...current };
      delete next[CHAT_REASONING_EFFORT_PARAM];
      return next;
    });
    // Mantém um snapshot para o fallback caso o preset seja apagado, mas o
    // backend sempre usa o ModelConfig ATUAL enquanto model_config_id existir.
    if (active) await patchActive({ model: mc.base_model, system_prompt: mc.system_prompt, params: mc.params, model_config_id: mc.id });
  }

  async function setAsDefault() {
    const value = curCustomId ? `custom:${curCustomId}` : curModel;
    if (!value) return;
    await api.put("/settings/default-model", { model: value });
    if (user) setUser({ ...user, default_model: value });
  }

  const chatActions: ChatActions = {
    onSelect: selectChat,
    onRename: async (id, title) => {
      await api.patch(`/chats/${id}`, { title });
      await refreshChats();
      if (active?.id === id) setActive({ ...active, title });
    },
    onPin: async (c) => {
      await api.patch(`/chats/${c.id}`, { pinned: !c.pinned });
      await refreshChats();
    },
    onClone: async (id) => {
      const clone = await api.post<Chat>(`/chats/${id}/clone`);
      await refreshChats();
      await selectChat(clone.id);
    },
    onArchive: async (c) => {
      await api.patch(`/chats/${c.id}`, { archived: !c.archived });
      if (active?.id === c.id) goHome();
      await refreshChats();
    },
    onDelete: async (id) => {
      const c = chats.find((x) => x.id === id);
      const ok = await confirm({
        title: "Excluir chat?",
        body: <>Isso vai excluir <span className="font-medium text-ink">{c?.title || "este chat"}</span>.</>,
        confirmLabel: "Excluir",
        danger: true,
      });
      if (!ok) return;
      await api.del(`/chats/${id}`);
      if (active?.id === id) goHome();
      await refreshChats();
    },
    onDownload: async (c, format) => {
      const detail = await api.get<Chat & { messages: Message[] }>(`/chats/${c.id}`);
      const msgs = detail.messages ?? [];
      if (format === "json") downloadJSON(detail, msgs);
      else if (format === "txt") downloadTXT(detail, msgs);
      else downloadPDF(detail, msgs);
    },
    onInfo: (c) => setInfoChatId(c.id),
  };

  const createFolder = async () => {
    await api.post("/folders", { name: "Nova pasta" });
    await refreshFolders();
  };
  const renameFolder = async (id: string, name: string) => {
    await api.patch(`/folders/${id}`, { name });
    await refreshFolders();
  };
  const deleteFolder = async (id: string) => {
    await api.del(`/folders/${id}`);
    await refreshFolders();
    await refreshChats();
  };
  const moveChat = async (chatId: string, folderId: string | null) => {
    await api.patch(`/chats/${chatId}`, { folder_id: folderId });
    await refreshChats();
  };

  // ações em LOTE do menu "Conversas" (ChatManager)
  const bulkMoveChats = async (ids: string[], folderId: string | null) => {
    await Promise.all(ids.map((id) => api.patch(`/chats/${id}`, { folder_id: folderId }).catch(() => {})));
    await refreshChats();
  };
  const bulkDeleteChats = async (ids: string[]) => {
    const ok = await confirm({
      title: ids.length === 1 ? "Excluir conversa?" : "Excluir conversas?",
      body: <>Isso vai excluir <span className="font-medium text-ink">{ids.length}</span> {ids.length === 1 ? "conversa" : "conversas"}. Não dá para desfazer.</>,
      confirmLabel: "Excluir",
      danger: true,
    });
    if (!ok) return;
    await Promise.all(ids.map((id) => api.del(`/chats/${id}`).catch(() => {})));
    if (active && ids.includes(active.id)) goHome();
    await refreshChats();
  };

  async function send(textArg?: string) {
    // textArg vem dos seletores de opção (kind:"ask"); senão usa o campo de texto
    const override = typeof textArg === "string";
    // Preserve o valor EXATO para devolver ao composer se o servidor recusar a
    // request (por exemplo 413 por limite de tamanho). `text` continua normalizado
    // só para o payload/validação, sem perder espaços/quebras do rascunho do usuário.
    const draftBeforeSend = input;
    const text = (override ? textArg : draftBeforeSend).trim();
    // mesa-redonda: a mensagem do usuário GUIA a conversa; roda os participantes.
    // (funciona no rascunho: runRoundtable cria o chat no 1º envio)
    if (isRoundtable) {
      if (rtRunning) return;
      if (!override) setInput("");
      await runRoundtable("auto", text);
      return;
    }
    if ((!text && attachments.length === 0) || sending) return;
    const model = active ? active.model : curModel;
    if (!model) {
      alert("Selecione um modelo primeiro.");
      return;
    }
    if (!override) setInput("");
    setSending(true);
    setStreamPhase("preparing");
    // clique numa opção não consome skills/anexos pendentes do usuário
    const turnSkillIds = override ? [] : attachedSkillIds;
    if (!override) setAttachedSkillIds([]);
    const turnAttachments = override ? [] : attachments;
    if (!override) setAttachments([]);
    const turnAgentId = override ? null : agentId;
    if (!override) setAgentId(null);
    const turnRefDocs = override ? [] : refDocs;
    const turnRefDocIds = turnRefDocs.map((r) => r.id);
    if (!override) setRefDocs([]);
    const turnRefChats = override ? [] : refChats;
    const turnRefChatIds = turnRefChats.map((r) => r.id);
    if (!override) setRefChats([]);
    const turnMiniApp = activeMiniApp;
    const temporaryMessageId = `tmp-${Date.now()}`;
    setMessages((m) => [...m, { id: temporaryMessageId, role: "user", content: text, attachments: turnAttachments, created_at: new Date().toISOString() }]);
    setAtBottom(true); // enviar re-engata o auto-scroll (acompanhar a resposta)
    setStreaming("");
    setStreamingReasoning("");
    setToolEvents([]);
    setGuardNote(null);
    setSubagents([]);

    // chat "dono" deste turno: enquanto ele for o ativo, o stream pinta a tela;
    // se o usuário trocar de chat, o handler para de pintar (ver useGeneration).
    // Para rascunho, o id só existe após criar o chat abaixo — daí o getter.
    let ownerId: string | null = active?.id ?? null;
    const { handler: onEvent, state, dispose: disposeStream } = makeStreamHandler(() => ownerId);

    // controles definidos na home (rascunho) têm prioridade sobre o do modelo custom
    const initialSystemPrompt = draftSystemPrompt || curCustom?.system_prompt || null;
    const initialParams = { ...(curCustom?.params ?? {}), ...draftParams };

    try {
      // O nível de reasoning faz parte deste turno. Aguarda uma gravação que já
      // estava em voo para o backend não ler o valor anterior por corrida de rede.
      if (reasoningSaveRef.current) await reasoningSaveRef.current;
      if (temporary) {
        // temporário roda preso à request: "Parar" = abortar a conexão local
        const ctrl = new AbortController();
        stopRef.current = () => ctrl.abort();
        const history = messages.map((m) => ({ role: m.role, content: m.content }));
        try {
          await streamEphemeral(
            { model, content: text, history, system_prompt: initialSystemPrompt, params: initialParams, model_config_id: curCustomId, skill_ids: turnSkillIds, attachments: turnAttachments },
            onEvent,
            ctrl.signal,
          );
        } catch (e) {
          if (!(e instanceof DOMException && e.name === "AbortError")) throw e;
        }
        if ((state.acc || state.steps.length) && isActiveChat(ownerId)) {
          setMessages((m) => [...m, { id: `a-${Date.now()}`, role: "assistant", content: state.acc, reasoning: state.reason || state.steps.length ? { text: state.reason, steps: state.steps } : null, tool_events: state.tools.length ? state.tools : null, created_at: new Date().toISOString() }]);
        }
      } else {
        let chat = active;
        if (!chat) {
          chat = await api.post<Chat>("/chats", {
            title: "Novo Chat",
            model,
            system_prompt: initialSystemPrompt,
            params: initialParams,
            model_config_id: curCustomId,
          });
          setActive(chat);
          activeIdRef.current = chat.id;
          ownerId = chat.id; // rascunho virou chat real: o stream agora tem dono
        }
        // persistente: o servidor cancela a geração e salva o parcial
        const cid = chat.id;
        stopRef.current = () => { api.post(`/chats/${cid}/stop`).catch(() => {}); };
        await streamMessage(chat.id, text, onEvent, undefined, turnSkillIds, turnAttachments, turnAgentId, turnRefDocIds, turnRefChatIds, turnMiniApp);
        refreshChats();
        // só recarrega/limpa a tela se o usuário AINDA está neste chat — senão
        // sobrescreveria o chat para onde ele navegou (a resposta já ficou salva
        // no servidor e reaparece ao reabrir este chat).
        if (isActiveChat(ownerId)) {
          // Mantém o balão vivo até os registros reais chegarem. Removê-lo antes
          // fazia o scroll colapsar até a mensagem do usuário e ancorar ali.
          prepareStreamLanding();
          // recarrega as mensagens reais (ids do servidor + registro de tokens/custo)
          await reloadMessages(chat.id);
          setStreaming("");
          setStreamingReasoning("");
          await reloadArtifacts(chat.id);
          if (state.imaginaiChanged) await refreshImaginaiCampaign(chat.id);
          // as mensagens enfileiradas já "aterrissaram" (persistidas + no histórico):
          // limpa os chips. Se havia FILA (não-steer), o back disparou um turno de
          // continuação — re-assina p/ vê-lo ao vivo (best-effort; o poll é a rede).
          if (queuedRef.current.length) {
            const hadQueue = queuedRef.current.some((q) => !q.steer);
            setQueued([]);
            if (hadQueue) resumeStream(chat.id);
          }
        }
      }
      if (state.acc) notify("Resposta pronta", state.acc.replace(/\s+/g, " ").slice(0, 90));
    } catch (e) {
      // orçamento pessoal estourado (modo "pausar") ou outra falha ao iniciar o turno
      const msg = e instanceof ApiError ? e.message : "Falha ao enviar a mensagem";
      // Desfaz somente o balão deste envio. Outras mensagens otimistas podem
      // existir em chats que o usuário abriu em seguida e não devem sumir.
      setMessages((m) => m.filter((x) => x.id !== temporaryMessageId));
      if (!override && isActiveChat(ownerId)) {
        // Não atropela um novo texto/chip que o usuário tenha montado enquanto a
        // request falhava. Se o composer segue vazio, devolve tudo que foi tirado
        // dele para este turno — principalmente o rascunho grande e editável.
        setInput((current) => current || draftBeforeSend);
        setAttachedSkillIds((current) => current.length ? current : turnSkillIds);
        setAttachments((current) => current.length ? current : turnAttachments);
        setAgentId((current) => current ?? turnAgentId);
        setRefDocs((current) => current.length ? current : turnRefDocs);
        setRefChats((current) => current.length ? current : turnRefChats);
      }
      notify(e instanceof ApiError && e.status === 402 ? "Orçamento mensal atingido" : "Erro", msg);
      refreshBudget();
    } finally {
      disposeStream();
      // limpa o estado de geração só se o usuário continua neste chat; se ele
      // trocou, quem manda na tela é o chat de destino (não zere o dele).
      if (isActiveChat(ownerId)) {
        stopRef.current = null;
        setStreaming("");
        setStreamingReasoning("");
        setGeneratingImage(false);
        setLiveArtifact(null);
        setStreamPhase("idle");
        setSending(false);
      }
      refreshBudget(); // atualiza o gasto do mês (mantém o banner em dia)
    }
  }

  // Enviar DURANTE a geração: não abre um 2º turno (o back enfileira). steer=true injeta
  // no turno em curso (entre iterações); steer=false vira turno de continuação no fim.
  async function enqueue(steer: boolean) {
    const text = input.trim();
    if (!text || !active) return;
    setInput("");
    const id = `q-${Date.now()}`;
    setQueued((q) => [...q, { id, text, steer }]);
    try {
      await api.post(`/chats/${active.id}/messages`, { content: text, steer });
    } catch {
      setQueued((q) => q.filter((x) => x.id !== id)); // falhou → desfaz o chip
      setInput(text);
    }
  }

  async function editMessage(id: string, content: string) {
    if (!active) return;
    await api.patch(`/chats/${active.id}/messages/${id}`, { content });
    setMessages((m) => m.map((x) => (x.id === id ? { ...x, content } : x)));
  }

  async function deleteMessage(id: string) {
    // mensagens locais (temporário / otimista ainda não persistidas): só remove da UI
    const local = !active || id.startsWith("tmp-") || id.startsWith("a-");
    if (local) {
      setMessages((m) => m.filter((x) => x.id !== id));
      return;
    }
    if (!(await confirm({ title: "Excluir esta mensagem?", confirmLabel: "Excluir", danger: true }))) return;
    const prev = messages;
    setMessages((m) => m.filter((x) => x.id !== id));
    try {
      await api.del(`/chats/${active!.id}/messages/${id}`);
      refreshChats();
    } catch (e) {
      setMessages(prev); // reverte em caso de falha
      alert(e instanceof ApiError ? e.message : "Falha ao excluir a mensagem");
    }
  }

  // rola até uma mensagem específica (usado pelo navegador de mensagens)
  function jumpToMessage(id: string) {
    document.getElementById(`msg-${id}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  async function regenerateMessage(id: string) {
    if (!active || sending) return;
    setSending(true);
    setStreamPhase("preparing");
    setStreaming("");
    setStreamingReasoning("");
    setToolEvents([]);
    setGuardNote(null);
    // remove da UI a partir do alvo: resposta da IA sai junto; mensagem do
    // usuário ("Tentar novamente" nela) FICA — a IA pensa a partir dela.
    setMessages((m) => {
      const i = m.findIndex((x) => x.id === id);
      if (i === -1) return m;
      return m.slice(0, m[i].role === "user" ? i + 1 : i);
    });
    const cid = active.id;
    const { handler, state, dispose: disposeStream } = makeStreamHandler(() => cid);
    stopRef.current = () => { api.post(`/chats/${cid}/stop`).catch(() => {}); };
    try {
      await streamRegenerate(active.id, id, handler);
      refreshChats();
      if (isActiveChat(cid)) {
        setStreaming("");
        setStreamingReasoning("");
        await reloadMessages(cid);
        await reloadArtifacts(cid);
        if (state.imaginaiChanged) await refreshImaginaiCampaign(cid);
      }
      if (state.acc) notify("Resposta pronta", state.acc.replace(/\s+/g, " ").slice(0, 90));
    } finally {
      disposeStream();
      if (isActiveChat(cid)) {
        stopRef.current = null;
        setStreaming("");
        setStreamingReasoning("");
        setLiveArtifact(null);
        setStreamPhase("idle");
        setSending(false);
      }
    }
  }

  async function continueMessage(id: string) {
    if (!active || sending) return;
    setSending(true);
    setStreamPhase("preparing");
    setStreaming("");
    setStreamingReasoning("");
    setToolEvents([]);
    const cid = active.id;
    const { handler, state, dispose: disposeStream } = makeStreamHandler(() => cid);
    stopRef.current = () => { api.post(`/chats/${cid}/stop`).catch(() => {}); };
    try {
      await streamContinue(active.id, id, handler);
      if (isActiveChat(cid)) {
        setStreaming("");
        setStreamingReasoning("");
        await reloadMessages(cid);
        await reloadArtifacts(cid);
        if (state.imaginaiChanged) await refreshImaginaiCampaign(cid);
      }
      if (state.acc) notify("Resposta continuada", state.acc.replace(/\s+/g, " ").slice(0, 90));
    } finally {
      disposeStream();
      if (isActiveChat(cid)) {
        stopRef.current = null;
        setStreaming("");
        setStreamingReasoning("");
        setLiveArtifact(null);
        setStreamPhase("idle");
        setSending(false);
      }
    }
  }

  async function toggleMic() {
    if (recording) {
      const rec = recorderRef.current;
      const browser = browserDictRef.current;
      recorderRef.current = null;
      browserDictRef.current = null;
      setRecording(false);
      if (rec) {
        try {
          const blob = await rec.stop();
          try {
            const t = await transcribe(blob, curCustom?.id ?? active?.model_config_id);
            void browser?.stop();
            setInput((v) => (v ? v + " " : "") + t);
          } catch (e) {
            // servidor de STT indisponível (sem chave/local não faz STT): usa o
            // que o reconhecimento do NAVEGADOR captou em paralelo
            const local = (await browser?.stop()) ?? "";
            if (local) {
              setInput((v) => (v ? v + " " : "") + local);
            } else {
              alert("Falha na transcrição: " + (e as Error).message);
            }
          }
        } catch (e) {
          alert("Falha na gravação: " + (e as Error).message);
        }
      }
    } else {
      const selectedModel = curCustom
        ?? customModels.find((model) => model.id === active?.model_config_id);
      const voiceConfig = selectedModel?.filter_config?.voice as { stt_enabled?: boolean } | undefined;
      if (voiceConfig?.stt_enabled === false) {
        alert("A escuta (STT) está desativada nas configurações deste modelo.");
        return;
      }
      try {
        recorderRef.current = await startRecording();
        // melhor esforço, junto com a gravação — vira o fallback se o servidor falhar
        browserDictRef.current = startBrowserDictation();
        setRecording(true);
      } catch {
        alert("Não foi possível acessar o microfone.");
      }
    }
  }

  // ---------------------------------------------------------------------------
  // Modo voz (assistente hands-free): fala → STT → envia no chat → responde falando.
  // Reusa a captura (VAD), o STT, o send() e o TTS existentes. O `sendRef` evita
  // closure obsoleto: depois de abrir o chat de voz, o loop usa o `send` do render
  // mais novo (com o `active` já apontando para o chat certo).
  // ---------------------------------------------------------------------------
  const sendRef = useRef(send);
  sendRef.current = send;

  function stopVoiceMode() {
    voiceRef.current.active = false;
    voiceRef.current.utter?.cancel();
    voiceRef.current.utter = null;
    stopSpeaking();
    setVoicePhase("off");
    setVoiceLevel(0);
  }

  const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

  // envia `text` no chat ativo e resolve com o texto da resposta quando o stream termina
  async function voiceSendAndWait(text: string): Promise<string> {
    const before = messagesRef.current.length;
    await sendRef.current(text);
    const t0 = Date.now();
    // espera o turno começar e depois terminar (sending/streaming voltam a false)
    await sleep(150);
    while ((pollRef.current.sending || pollRef.current.streaming) && Date.now() - t0 < 180_000) {
      if (!voiceRef.current.active) return "";
      await sleep(200);
    }
    const msgs = messagesRef.current;
    // só as mensagens NOVAS deste turno (índices >= before); `before-1` cairia na
    // resposta ANTERIOR e, num turno só-tool/artefato (sem texto), a falaria de novo.
    for (let i = msgs.length - 1; i >= before; i--) {
      if (msgs[i].role === "assistant" && msgs[i].content) return msgs[i].content;
    }
    return "";
  }

  async function voiceLoop(session: VoiceSession) {
    const selectedModel = curCustom ?? customModels.find((model) => model.id === session.model_config_id);
    const voice = selectedModel?.tts_voice ?? undefined;
    const voiceConfig = selectedModel?.filter_config?.voice as { tts_enabled?: boolean } | undefined;
    const ttsEnabled = voiceConfig?.tts_enabled !== false;
    // Conversa contínua (Fase 3): reabre a escuta após cada resposta com uma
    // JANELA DE GRAÇA — o usuário fala de novo sem repetir a wake word. Se ficar
    // em silêncio na janela, a conversa ENCERRA sozinha (volta ao standby da wake
    // word), em vez de repetir para sempre como o hands_free puro.
    const continuous = !!session.continuous;
    const loops = continuous || session.hands_free;
    const followUpMs = Math.max(2, session.follow_up_secs || 8) * 1000;
    // STT no navegador (Whisper offline) quando o modelo pede, senão o servidor
    const sttLocal = !!(curCustom?.filter_config?.listen as ListenConfig | undefined)?.stt_local;
    // throttle do nível: ~12fps (senão o VAD re-renderiza a página a 60fps)
    let lastLvl = 0;
    const onLevel = (l: number) => {
      const now = Date.now();
      if (now - lastLvl > 80) { lastLvl = now; setVoiceLevel(l); }
    };
    let firstTurn = true;
    while (voiceRef.current.active) {
      setVoicePhase("listening");
      let blob: Blob | null = null;
      try {
        // no follow-up de uma conversa contínua a paciência é a janela de graça;
        // no 1º turno (após wake/atalho/botão) mantém o timeout padrão de início
        const startTimeoutMs = continuous && !firstTurn ? followUpMs : undefined;
        const utter = await captureUtterance({ onLevel, startTimeoutMs });
        voiceRef.current.utter = utter;
        blob = await utter.done;
        voiceRef.current.utter = null;
      } catch {
        alert("Não foi possível acessar o microfone.");
        break;
      }
      setVoiceLevel(0);
      if (!voiceRef.current.active) break;
      if (!blob) {
        // silêncio: em conversa contínua a janela de graça expirou → encerra
        // (não segura o mic à toa); no hands_free puro reabre; senão encerra.
        if (continuous) break;
        if (loops) continue; else break;
      }

      setVoicePhase("thinking");
      let text = "";
      try { text = (await (sttLocal ? transcribeWhisper(blob) : transcribe(blob, curCustom?.id ?? session.model_config_id))).trim(); } catch { text = ""; }
      if (!voiceRef.current.active) break;
      if (!text) { if (loops) continue; else break; }
      firstTurn = false;

      const reply = await voiceSendAndWait(text);
      if (!voiceRef.current.active) break;

      if (session.auto_speak && ttsEnabled && reply) {
        setVoicePhase("speaking");
        try { await speak(reply, voice, curCustom?.id ?? session.model_config_id); } catch { /* fallback interno */ }
      }
      if (!voiceRef.current.active || !loops) break;
    }
    stopVoiceMode();
  }

  async function startVoiceMode() {
    if (voiceRef.current.active) { stopVoiceMode(); return; }
    const mcId = curCustom?.id ?? active?.model_config_id ?? null;
    if (!mcId) {
      alert('O modo voz precisa de um modelo com "Assistente de voz" ligado (Configurações do modelo → Voz).');
      return;
    }
    const selectedModel = curCustom ?? customModels.find((model) => model.id === mcId);
    const voiceConfig = selectedModel?.filter_config?.voice as { stt_enabled?: boolean } | undefined;
    if (voiceConfig?.stt_enabled === false) {
      alert("A escuta (STT) está desativada nas configurações deste modelo.");
      return;
    }
    let session: VoiceSession;
    try {
      session = await api.post<VoiceSession>("/voice/session", { model_config_id: mcId });
    } catch (e) {
      alert(e instanceof ApiError ? e.message : "Não foi possível iniciar o modo voz.");
      return;
    }
    voiceRef.current = { active: true, utter: null, session };
    setVoicePhase("listening");
    if (pollRef.current.active?.id !== session.chat_id) {
      try { await selectChat(session.chat_id); } catch { /* segue mesmo assim */ }
      // espera o `active` refletir o chat de voz antes de enviar (evita mandar no chat errado)
      const t0 = Date.now();
      while (voiceRef.current.active && pollRef.current.active?.id !== session.chat_id && Date.now() - t0 < 5000) {
        await sleep(50);
      }
    }
    if (voiceRef.current.active) voiceLoop(session);
  }

  function toggleVoiceMode() {
    if (voiceRef.current.active) stopVoiceMode(); else startVoiceMode();
  }

  // atalho GLOBAL do desktop (Tauri) → abre o modo voz mesmo com o app em segundo plano
  const startVoiceModeRef = useRef(startVoiceMode);
  startVoiceModeRef.current = startVoiceMode;
  useEffect(() => {
    let unlisten = () => {};
    onVoiceActivate(() => startVoiceModeRef.current()).then((fn) => { unlisten = fn; });
    return () => unlisten();
  }, []);

  // engrenagem "Assistente de voz" no ModelEditor → abre Configurações no card certo.
  // Evento global (desacopla do WorkspaceView); a flag cobre quando veio da rota /workspace.
  useEffect(() => {
    const onOpen = (e: Event) => openSettings("connections", (e as CustomEvent).detail?.view);
    window.addEventListener("aiw:open-settings", onOpen);
    try {
      const flag = sessionStorage.getItem("aiw_open_settings");
      if (flag) { sessionStorage.removeItem("aiw_open_settings"); openSettings("connections", flag); }
    } catch { /* sessionStorage indisponível */ }
    return () => window.removeEventListener("aiw:open-settings", onOpen);
  }, [openSettings]);

  async function logout() {
    await api.post("/auth/logout");
    router.replace("/login");
  }

  function toggleTemporary() {
    setTemporary((v) => {
      const next = !v;
      if (next) goHome();
      return next;
    });
  }

  // Parâmetros gerais continuam vindo do ModelConfig atual (sem snapshots
  // obsoletos). Só o nível escolhido no composer pode ser sobrescrito por chat.
  const activeParams = active
    ? (active.model_config_id && curCustom ? curCustom.params ?? {} : active.params)
    : draftParams;
  const ownChatParams = active ? active.params : draftParams;
  const reasoningEffort: ReasoningEffort =
    (curCustom ? chatReasoningOverride(ownChatParams) : null)
    ?? reasoningFromParams(curCustom?.params)
    ?? reasoningFromParams(activeParams)
    ?? "off";
  function setReasoningEffort(level: ReasoningEffort) {
    if (curCustom) {
      const next = { ...(ownChatParams ?? {}), [CHAT_REASONING_EFFORT_PARAM]: level };
      if (!active) {
        setDraftParams(next);
        return;
      }

      const chatId = active.id;
      // Otimista: o botão não pisca de volta para "Desativado" enquanto salva.
      setActive((current) => current?.id === chatId ? { ...current, params: next } : current);
      // Serializa cliques rápidos (Médio → Alto): duas requests concorrentes
      // poderiam chegar ao banco fora de ordem e salvar a escolha anterior.
      const before = reasoningSaveRef.current?.catch(() => undefined) ?? Promise.resolve();
      const request = before.then(() => api.patch<Chat>(`/chats/${chatId}`, { params: next }))
        .then((updated) => {
          setActive((current) => (
            current?.id === chatId
            && current.params?.[CHAT_REASONING_EFFORT_PARAM] === level
              ? { ...current, params: updated.params }
              : current
          ));
        });
      reasoningSaveRef.current = request;
      void request.catch(() => {}).finally(() => {
        if (reasoningSaveRef.current === request) reasoningSaveRef.current = null;
      });
      return;
    }
    const base = { ...(activeParams ?? {}) };
    delete base[CHAT_REASONING_EFFORT_PARAM];
    if (level === "off") delete base.reasoning;
    else base.reasoning = { effort: level };
    if (active) {
      const before = reasoningSaveRef.current?.catch(() => undefined) ?? Promise.resolve();
      const request = before.then(() => patchActive({ params: base }));
      reasoningSaveRef.current = request;
      void request.catch(() => {}).finally(() => {
        if (reasoningSaveRef.current === request) reasoningSaveRef.current = null;
      });
    } else {
      setDraftParams(base);
    }
  }

  // favoritos e fixados do seletor de modelos (persistidos no perfil do usuário)
  const favorites: string[] = (user?.profile?.favorite_models as string[]) ?? [];
  const pinnedKeys: string[] = (user?.profile?.pinned_models as string[]) ?? [];

  async function updateProfileList(field: "favorite_models" | "pinned_models", key: string) {
    const cur: string[] = (user?.profile?.[field] as string[]) ?? [];
    const next = cur.includes(key) ? cur.filter((k) => k !== key) : [...cur, key];
    setUser((u) => (u ? { ...u, profile: { ...(u.profile ?? {}), [field]: next } } : u));
    try {
      await api.put("/settings/profile", { [field]: next });
    } catch {
      /* mantém otimista; recarrega no próximo load */
    }
  }
  const toggleFavorite = (key: string) => updateProfileList("favorite_models", key);
  const togglePin = (key: string) => updateProfileList("pinned_models", key);

  // resolve as chaves fixadas em itens exibíveis na barra lateral (ext + custom)
  const pinnedModels = useMemo(() => {
    return pinnedKeys
      .map((key) => {
        if (key.startsWith("custom:")) {
          const mc = customModels.find((c) => c.id === key.slice(7));
          return mc ? { key, name: mc.name, avatar: mc.avatar_url ?? null, custom: mc, extId: null } : null;
        }
        if (key.startsWith("ext:")) {
          const id = key.slice(4);
          const ext = extModels.find((m) => m.id === id);
          return { key, name: ext?.name ?? id, avatar: null, custom: null, extId: id };
        }
        return null;
      })
      .filter((x): x is NonNullable<typeof x> => x !== null);
  }, [pinnedKeys, customModels, extModels]);

  function pickPinned(item: { custom: ModelConfig | null; extId: string | null }) {
    if (item.custom) newChatWithModel(item.custom);
    else if (item.extId) { modelChosenRef.current = true; setCurModel(item.extId); setCurCustomId(null); goHome(); }
  }

  // uso de contexto: limite vem do modelo base; tokens usam o registro real da
  // última resposta (prompt+saída) ou uma estimativa por caracteres.
  const [compacting, setCompacting] = useState(false);
  const contextLimit = useMemo(() => {
    const baseId = curCustom ? curCustom.base_model : curModel;
    return extModels.find((m) => m.id === baseId)?.context_length ?? 0;
  }, [curCustom, curModel, extModels]);
  const contextTokens = useMemo(() => {
    // Só o que REALMENTE vai ao modelo: mensagens EM contexto (não-compactadas). Sem
    // isto, após compactar o medidor continuava lendo a usage do último turno PRÉ-
    // compactação (contexto grande) e o "dot" não caía, embora a conversa já estivesse
    // resumida. Compactar não gera um turno novo com usage menor — só marca compacted —,
    // então caímos na estimativa por chars do que sobrou em contexto (resumo + recentes).
    const inCtx = messages.filter((m) => !m.compacted);
    for (let i = inCtx.length - 1; i >= 0; i--) {
      const u = inCtx[i].usage;
      // `context_tokens` = tamanho REAL do contexto (prompt da 1ª chamada do turno). NÃO
      // usar `prompt_tokens`: é a SOMA cumulativa das N iterações do loop agêntico (os
      // tool_results são re-enviados a cada passo), então dá milhões e falseia "estourado".
      if (u?.context_tokens) {
        return u.context_tokens + (u.completion_tokens || 0);
      }
    }
    // sem context_tokens (turnos antigos): estima pelo tamanho do que sobrou em contexto
    const chars = inCtx.reduce((a, m) => a + (m.content?.length ?? 0), 0);
    return Math.round(chars / 4);
  }, [messages]);

  // compacta direto (sem popup de confirmação); notifica ao concluir
  async function compactContext() {
    if (!active || compacting) return;
    setCompacting(true);
    try {
      await api.post(`/chats/${active.id}/compact`);
      await reloadMessages(active.id);
      refreshChats();
      notify("Contexto compactado", "A conversa foi resumida para liberar espaço.");
    } catch (e) {
      alert(e instanceof ApiError ? e.message : "Falha ao compactar");
    } finally {
      setCompacting(false);
    }
  }
  const contextInfo = active && contextLimit ? { tokens: contextTokens, limit: contextLimit } : null;

  // -------------------------------------------------------------------------
  // Atalhos de teclado (personalizáveis em Configurações → Atalhos). Os handlers
  // e o mapa do perfil ficam em refs, atualizados a cada render, e o listener é
  // registrado uma única vez lendo desses refs (sem re-bind a cada mudança).
  // -------------------------------------------------------------------------
  const shortcutHandlersRef = useRef<Record<string, () => void>>({});
  shortcutHandlersRef.current = {
    new_chat: () => newChat(),
    command_palette: () => setShowPalette(true),
    toggle_sidebar: () => toggleCollapse(),
    toggle_controls: () => setShowControls((v) => !v),
    workspace: () => openWorkspace(null),
    automations: () => openWorkspace("Automacoes"),
    playground: () => openWorkspace("Playground"),
    settings: () => setShowSettings(true),
    archived: () => setShowArchived(true),
    dictate: () => toggleMic(),
    voice_mode: () => toggleVoiceMode(),
    compact: () => { if (active) compactContext(); },
    context_graph: () => { if (active) setShowCompactions(true); },
    focus_input: () => (document.querySelector<HTMLTextAreaElement>("[data-prompt-input]"))?.focus(),
  };
  const shortcutMapRef = useRef<ShortcutMap | undefined>(undefined);
  shortcutMapRef.current = (user?.profile as { shortcuts?: ShortcutMap } | undefined)?.shortcuts;

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const combo = eventToCombo(e);
      if (!combo || !comboHasModifier(combo)) return; // só atalhos com modificador
      const el = document.activeElement as HTMLElement | null;
      const editable = !!el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.isContentEditable);
      // num campo editável, só dispara se houver Ctrl/⌘/Alt (não rouba digitação)
      if (editable && !(e.ctrlKey || e.metaKey || e.altKey)) return;
      for (const a of SHORTCUTS) {
        const b = resolveBinding(shortcutMapRef.current, a);
        if (b.enabled && b.keys === combo) {
          e.preventDefault();
          shortcutHandlersRef.current[a.id]?.();
          return;
        }
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  if (!user) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src="/logo.png" alt="" className="h-10 w-10 animate-pulse rounded-xl" />
        <p className="text-sm text-muted">Carregando…</p>
      </div>
    );
  }

  // itens da paleta de comandos (Ctrl/⌘ K): ações + chats + modelos
  const paletteItems: PaletteItem[] = [
    { id: "act-new", group: "Ações", label: "Novo chat", keywords: "conversa nova", icon: <MessageSquareDashed size={16} />, run: () => newChat() },
    { id: "act-temp", group: "Ações", label: "Chat temporário", keywords: "privado incógnito não salvar", icon: <MessageSquareDashed size={16} />, run: () => { if (!temporary) toggleTemporary(); } },
    { id: "act-round", group: "Ações", label: "Mesa-redonda", keywords: "multi modelo debate", icon: <Users size={16} />, run: () => enterRoundtable() },
    { id: "act-ws", group: "Ações", label: "Espaço de Trabalho", keywords: "modelos ferramentas prompts skills", icon: <Wrench size={16} />, run: () => openWorkspace(null) },
    { id: "act-auto", group: "Ações", label: "Automações", keywords: "agendar monitor", icon: <Bell size={16} />, run: () => openWorkspace("Automacoes") },
    { id: "act-play", group: "Ações", label: "Playground", keywords: "benchmark comparar modelos debug ferramentas tools", icon: <FlaskConical size={16} />, run: () => openWorkspace("Playground") },
    { id: "act-archived", group: "Ações", label: "Chats arquivados", keywords: "arquivo", icon: <Search size={16} />, run: () => setShowArchived(true) },
    ...(user.role === "admin" ? [{ id: "act-admin", group: "Ações", label: "Painel do Admin", keywords: "usuarios rede backup", icon: <ShieldAlert size={16} />, run: () => router.push("/admin") } as PaletteItem] : []),
    { id: "act-logout", group: "Ações", label: "Sair", keywords: "logout desconectar sair", icon: <X size={16} />, run: () => logout() },
    { id: "set-general", group: "Configurações", label: "Configurações", sublabel: "Geral", icon: <SlidersHorizontal size={16} />, run: () => openSettings("general") },
    { id: "set-status", group: "Configurações", label: "Status do sistema", keywords: "saude chave conexao", icon: <SlidersHorizontal size={16} />, run: () => openSettings("status") },
    { id: "set-budget", group: "Configurações", label: "Orçamento mensal", keywords: "conta gasto limite custo", icon: <SlidersHorizontal size={16} />, run: () => openSettings("account") },
    { id: "set-conn", group: "Configurações", label: "Conexões (APIs, Web, Voz)", keywords: "openrouter chave searxng", icon: <SlidersHorizontal size={16} />, run: () => openSettings("connections") },
    { id: "set-integ", group: "Configurações", label: "Integrações (WhatsApp, Google)", keywords: "whatsapp google tuya", icon: <SlidersHorizontal size={16} />, run: () => openSettings("integrations") },
    ...customModels.map((mc): PaletteItem => ({
      id: `model-${mc.id}`, group: "Modelos", label: mc.name, sublabel: mc.base_model,
      keywords: "usar modelo trocar", icon: <Wrench size={16} />, run: () => selectCustom(mc),
    })),
    ...chats.slice(0, 60).map((c): PaletteItem => ({
      id: `chat-${c.id}`, group: "Chats", label: c.title, keywords: "conversa abrir", run: () => selectChat(c.id),
    })),
  ];

  // seletor de opções pendente: só na ÚLTIMA mensagem (do assistente), fora de envio
  const lastMsg = messages[messages.length - 1];
  const askSpec = !sending && !streaming && lastMsg?.role === "assistant" ? findAsk(lastMsg.tool_events ?? []) : null;
  const showAsk = !!askSpec && dismissedAsk !== lastMsg?.id;
  const picker = (
    <ModelPicker
      label={modelLabel}
      avatar={user.profile?.interface?.model_avatar !== false ? curCustom?.avatar_url ?? null : null}
      models={extModels}
      custom={customModels}
      value={curModel}
      activeCustomId={curCustomId}
      favorites={favorites}
      pinned={pinnedKeys}
      onSelectExternal={selectExternal}
      onSelectCustom={selectCustom}
      onToggleFavorite={toggleFavorite}
      onTogglePin={togglePin}
      onEditModel={editModel}
    />
  );

  return (
    <div className="flex h-full bg-bg">
      {/* backdrop do drawer (mobile) */}
      {mobileNav && (
        <div className="fixed inset-0 z-40 bg-black/50 md:hidden" onClick={() => setMobileNav(false)} />
      )}
      {/* barra lateral: coluna no desktop; drawer deslizante no mobile.
          `flex` faz o <aside> interno esticar até o fim da tela (altura total). */}
      <div
        ref={sbResize.ref}
        className={`fixed inset-y-0 left-0 z-50 flex shrink-0 transition-transform duration-300 md:relative md:z-auto md:translate-x-0 md:transition-none ${
          mobileNav ? "translate-x-0" : "-translate-x-full"
        }`}
      >
        {!collapsed && !isMobile && sbResize.divider}
        <Sidebar
          width={collapsed || isMobile ? undefined : sbResize.w}
          activeView={
            workspaceOpen
              ? (workspaceSection === "Codespace" ? "codespace"
                : workspaceSection === "Analítica" ? "analytics"
                : workspaceSection === "Automacoes" ? "automations"
                : workspaceSection === "Playground" ? "playground"
                : "workspace")
              : "chat"
          }
          user={user}
          chats={chats}
          folders={folders}
          pinnedModels={pinnedModels}
          onPickPinned={(item) => { pickPinned(item); setMobileNav(false); }}
          activeId={active?.id ?? null}
          collapsed={isMobile ? false : collapsed}
          onToggleCollapse={isMobile ? () => setMobileNav(false) : toggleCollapse}
          onNewChat={() => { newChat(); setMobileNav(false); }}
          onSearch={() => { setShowPalette(true); setMobileNav(false); }}
          onOpenConversations={() => { setShowChatMgr(true); setMobileNav(false); }}
          chatActions={{ ...chatActions, onSelect: (id: string) => { chatActions.onSelect(id); setMobileNav(false); } }}
          onCreateFolder={createFolder}
          onRenameFolder={renameFolder}
          onDeleteFolder={deleteFolder}
          onMoveChat={moveChat}
          onOpenSettings={() => { setShowSettings(true); setMobileNav(false); }}
          onShowArchived={() => { setShowArchived(true); setMobileNav(false); }}
          onOpenWorkspace={() => openWorkspace(null)}
          onOpenAutomations={() => openWorkspace("Automacoes")}
          onOpenCodespace={() => openWorkspace("Codespace")}
          onOpenPlayground={() => openWorkspace("Playground")}
          onOpenAnalytics={() => openWorkspace("Analítica")}
          onLogout={logout}
        />
      </div>

      <main className="flex min-w-0 flex-1 flex-col">
        {workspaceOpen ? (
          <WorkspaceView
            key={workspaceKey}
            initialEditModel={editModelTarget}
            initialSection={workspaceSection}
            onModelsChanged={setCustomModels}
            onClose={() => { setWorkspaceOpen(false); setEditModelTarget(null); setWorkspaceSection(null); refreshModels(); refreshSkills(); }}
            onOpenChat={(cid, prefill) => { selectChat(cid).then(() => { if (prefill) setInput(prefill); }).catch(() => {}); }}
          />
        ) : (
        <>
        {/* barra superior: app-bar fixa no topo (sticky + fundo sólido + shrink-0) —
            no mobile o teclado encolhe o 100dvh e alguns navegadores (Brave) rolam a
            moldura p/ revelar o composer; sem fixar, o seletor de modelo/controles
            sumia do topo. z-30 fica acima das mensagens, abaixo dos drawers (z-50). */}
        <div className="sticky top-0 z-30 flex shrink-0 items-start justify-between gap-2 bg-bg px-2 py-2.5 sm:px-4">
          <div className="flex min-w-0 flex-col">
            <div className="flex items-center gap-1">
              <button onClick={() => setMobileNav(true)} title="Menu" className="rounded-lg p-1.5 text-muted transition-colors hover:bg-hover hover:text-ink md:hidden">
                <Menu size={20} />
              </button>
              {picker}
              {/* Mesa-redonda — disponível também no modo temporário (vira uma mesa
                  view_once, apagada ao sair). */}
              <button
                onClick={() => { if (isRoundtable) setRtBarOpen((v) => !v); else { enterRoundtable(); setRtBarOpen(true); } }}
                title={isRoundtable ? (rtBarOpen ? "Ocultar a mesa" : "Mostrar a mesa") : temporary ? "Mesa-redonda temporária (não será salva)" : "Mesa-redonda: fazer os modelos conversarem entre si"}
                className={`rounded-lg p-1.5 transition-colors ${isRoundtable && rtBarOpen ? "bg-accent/15 text-accent-hover" : isRoundtable ? "text-accent-hover hover:bg-hover" : "text-muted hover:bg-hover hover:text-ink"}`}
              >
                <Users size={18} />
              </button>
            </div>
            {!active && !temporary && curModel && (
              <button onClick={setAsDefault} className="pl-2 text-left text-xs text-muted transition-colors hover:text-ink">
                {user.default_model === (curCustomId ? `custom:${curCustomId}` : curModel)
                  ? "Modelo padrão ✓"
                  : "Definir como padrão"}
              </button>
            )}
            {active?.project_id && (
              <span className="flex items-center gap-2.5 pl-2 text-xs">
                <span
                  title={csProject?.name ? `Projeto: ${csProject.name}` : "Chat vinculado a um projeto do Codespace"}
                  className="flex cursor-default items-center gap-1 text-accent-hover"
                >
                  <Code2 size={11} /> Codespace
                </span>
                {curModel && (
                  <button onClick={setAsProjectDefault} className="text-left text-muted transition-colors hover:text-ink">
                    {csProject?.default_model === (curCustomId ? `custom:${curCustomId}` : curModel)
                      ? "Padrão do projeto ✓"
                      : "Definir como padrão do projeto"}
                  </button>
                )}
              </span>
            )}
          </div>
          <div className="flex items-center gap-1">
            {active && !temporary && showShareBtn && (
              <button
                onClick={() => setShowShare(true)}
                title="Compartilhar conversa (link público)"
                className={`rounded-lg p-2 transition-colors ${active.public_id ? "bg-accent/15 text-accent-hover" : "text-muted hover:bg-hover hover:text-ink"}`}
              >
                <Share2 size={18} />
              </button>
            )}
            <button
              onClick={toggleTemporary}
              title="Chat temporário"
              className={`rounded-lg p-2 transition-colors ${temporary ? "bg-accent/15 text-accent-hover" : "text-muted hover:bg-hover hover:text-ink"}`}
            >
              <MessageSquareDashed size={18} />
            </button>
            {active?.project_id && (
              <button
                onClick={() => setCsFilesOpen((v) => !v)}
                title="Arquivos do projeto"
                className={`rounded-lg p-2 transition-colors ${csFilesOpen ? "bg-accent/15 text-accent-hover" : "text-muted hover:bg-hover hover:text-ink"}`}
              >
                <Code2 size={18} />
              </button>
            )}
            <button
              onClick={() => setShowControls((v) => !v)}
              title="Controles"
              className={`rounded-lg p-2 transition-colors ${showControls ? "bg-accent/15 text-accent-hover" : "text-muted hover:bg-hover hover:text-ink"}`}
            >
              <SlidersHorizontal size={18} />
            </button>
          </div>
        </div>
        {isRoundtable && rtBarOpen && (
          <div className="px-4 py-2">
            <Roundtable
              participants={participants}
              config={rtConfig}
              running={rtRunning}
              currentSpeakerId={rtStreaming?.speaker.id ?? null}
              models={extModels}
              custom={customModels}
              onAdd={addParticipant}
              onRemove={removeParticipant}
              onUpdate={updateParticipant}
              onConfigChange={updateRtConfig}
              onRun={() => runRoundtable("auto")}
              onStep={() => runRoundtable("one")}
              onPause={pauseRoundtable}
            />
          </div>
        )}
        {budget?.enabled && budget.over && (
          <button
            onClick={() => setShowSettings(true)}
            className={`flex w-full items-center justify-center gap-2 px-4 py-1.5 text-center text-xs transition-colors ${budget.blocked ? "bg-red-500/15 text-red-300 hover:bg-red-500/25" : "bg-amber-500/15 text-amber-300 hover:bg-amber-500/25"}`}
          >
            <ShieldAlert size={13} className="shrink-0" />
            {budget.blocked
              ? `Orçamento mensal atingido (US$ ${budget.spent.toFixed(2)} de ${budget.cap.toFixed(2)}) — novas mensagens pausadas. Ajustar →`
              : `Você passou do seu orçamento mensal (US$ ${budget.spent.toFixed(2)} de ${budget.cap.toFixed(2)}). Ajustar →`}
          </button>
        )}
        <div className="flex flex-1 overflow-hidden">
          {/* min-w-0: sem ele, conteúdo largo nas mensagens (código/tabela) empurra a
              coluna além da viewport — o pai overflow-hidden corta e o mobile "sai da tela" */}
          <div className="relative flex min-w-0 flex-1 flex-col">
            {!hasConversation ? (
              /* HOME centralizada */
              <div className="animate-fade-up flex flex-1 flex-col items-center justify-center px-4">
                <div className="mb-7 flex flex-col items-center gap-4">
                  {curCustom?.avatar_url ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={curCustom.avatar_url} alt="" className="h-20 w-20 rounded-3xl object-cover shadow-lg" />
                  ) : (
                    <span className="flex h-20 w-20 items-center justify-center rounded-3xl bg-gradient-to-br from-accent/25 to-accent/5 text-3xl font-semibold text-accent-hover shadow-lg ring-1 ring-accent/20">
                      {(modelLabel || "AI")[0]?.toUpperCase()}
                    </span>
                  )}
                  <div className="text-center leading-snug">
                    <h1 className="text-2xl font-semibold tracking-tight text-ink-soft">Good to See You!</h1>
                    <p className="text-2xl font-semibold tracking-tight text-muted">How Can I be an Assistance?</p>
                  </div>
                </div>
                <div
                  className={`w-full max-w-3xl rounded-2xl transition-shadow ${csDropOver ? "ring-2 ring-accent/50" : ""}`}
                  onDragOver={(e) => { const t = e.dataTransfer.types;
                    if (t.includes(CODESPACE_DND_MIME) || t.includes(CODESPACE_SNIPPET_MIME)) { e.preventDefault(); setCsDropOver(true); } }}
                  onDragLeave={() => setCsDropOver(false)}
                  onDrop={handleComposerFileDrop}
                >
                  <PromptBox value={input} onChange={setInput} onSend={send} onStop={handleStop} onQueue={enqueue} queued={queued} sending={sending} recording={recording} onToggleMic={toggleMic} onVoiceMode={toggleVoiceMode} modelTools={modelTools} prompts={prompts} skills={skills} attachedSkillIds={attachedSkillIds} onAttachedSkillIdsChange={setAttachedSkillIds} agents={agentsForMention} agentId={agentId} onAgentChange={setAgentId} knowledgeRefs={knowledgeRefs} refDocs={refDocs} onRefDocsChange={setRefDocs} chats={chats.filter((c) => c.id !== active?.id)} refChats={refChats} onRefChatsChange={setRefChats} capabilities={curCustom?.capabilities} attachments={attachments} onAttachmentsChange={setAttachments} reasoning={reasoningEffort} onReasoningChange={setReasoningEffort} activeMiniApp={activeMiniApp} onActiveMiniAppChange={setActiveMiniApp} temporary={temporary} />
                </div>
                {/* menu do "+" abre para baixo aqui (há espaço); na conversa abre para cima */}
                {temporary && <p className="mt-2 text-xs text-muted">Chat temporário — esta conversa não será salva.</p>}
                <div className="mt-5 w-full max-w-3xl px-4">
                  <p className="mb-2.5 flex items-center gap-1.5 text-xs font-medium uppercase tracking-wider text-muted">
                    <Sparkles size={13} /> Sugerido
                  </p>
                  <div className="grid gap-2 sm:grid-cols-3">
                    {suggestions.map((s) => (
                      <button
                        key={s.title}
                        onClick={() => typeSuggestion(`${s.title} ${s.sub}`)}
                        className="group rounded-2xl border border-border bg-surface/60 px-4 py-3.5 text-left transition-all duration-200 hover:border-accent/40 hover:bg-surface"
                      >
                        <span className="flex items-start justify-between gap-2">
                          <span className="text-sm font-medium text-ink">{s.title}</span>
                          <ArrowUpRight size={15} className="mt-0.5 shrink-0 text-muted opacity-0 transition-opacity group-hover:opacity-100" />
                        </span>
                        <span className="mt-1 block text-xs leading-5 text-muted">{s.sub}</span>
                      </button>
                    ))}
                  </div>
                </div>
              </div>
            ) : (
              <>
                {/* O composer é um item de fluxo (shrink-0) LOGO ABAIXO: a altura
                    desta área é calculada pelo NAVEGADOR, então o fim do scroll é
                    sempre alcançável. Antes o composer era `absolute` e o espaço
                    dele era simulado por um padding medido em JS — qualquer atraso
                    da medição escondia o fim do conteúdo ("o scroll morre").
                    O pb-14 só afasta a última linha do gradiente; é constante e não
                    depende de medição nenhuma. */}
                <div className="chat-game-stage relative flex min-h-0 flex-1">
                  <div
                    ref={scrollRef}
                    onScroll={onScrollArea}
                    onWheel={() => { forceBottomAfterLandingRef.current = false; }}
                    onPointerDown={(event) => {
                      forceBottomAfterLandingRef.current = false;
                      if (event.button === 0) selectingTextRef.current = true;
                    }}
                    onPointerUp={() => { selectingTextRef.current = false; }}
                    onPointerCancel={() => { selectingTextRef.current = false; }}
                    className="chat-scroll flex-1 space-y-5 overflow-y-auto px-4 pb-14 pt-6 [scroll-padding-bottom:5rem]"
                  >
                  {temporary && (
                    <div className="mx-auto w-fit rounded-full border border-border bg-surface px-4 py-1.5 text-center text-xs text-muted">
                      Chat temporário — não será salvo
                    </div>
                  )}
                  {active?.view_once && (
                    <div className="mx-auto w-fit rounded-full border border-border bg-surface px-4 py-1.5 text-center text-xs text-muted">
                      Visualização única — será apagado ao sair
                    </div>
                  )}
                  {messages.map((m) => {
                    // mesa-redonda: cada fala tem um `speaker`; o avatar é o do
                    // MODELO daquele falante (não o do composer) e vai À ESQUERDA do texto.
                    const isRt = !!m.speaker && !m.is_summary && !temporary;
                    const showAv = iface.chat_model_image !== false;
                    const av = isRt && showAv ? speakerAvatar(m.speaker) : null;
                    const item = !m.is_summary && !temporary ? (
                      <MessageItem
                        message={m}
                        busy={sending}
                        bare={isRt}
                        modelName={m.speaker?.name ?? modelLabel}
                        nameColor={isRt ? (m.speaker?.color ?? null) : null}
                        toolsEnabled={iface.chat_tools !== false}
                        modelAvatar={isRt ? null : (showAv ? (curCustom?.avatar_url ?? null) : null)}
                        chatArtifacts={chatArtifacts}
                        onOpenArtifact={(ident) => setArtifactOpen(ident)}
                        onSpeak={m.role === "assistant" && voiceSettingsFor(m).enabled ? () => toggleMessageSpeech(m) : undefined}
                        speaking={speakingMessageId === m.id}
                        clampContent={!recentFullMessageIds.has(m.id)}
                        onEdit={editMessage}
                        onRegenerate={regenerateMessage}
                        onContinue={continueMessage}
                        onDelete={deleteMessage}
                        onRemember={active ? async (text, scope) => {
                          const agentId = active.model_config_id ?? `base:${active.model}`;
                          await api.post("/memory", {
                            text, scope,
                            model_id: scope === "model" ? agentId : undefined,
                            chat_id: scope === "chat" ? active.id : undefined,
                          });
                        } : undefined}
                      />
                    ) : null;
                    return (
                      <div key={m.id} id={`msg-${m.id}`} className={recentFullMessageIds.has(m.id) ? "msg-row msg-row-recent" : "msg-row"}>
                        {m.is_summary ? (
                          <CompactionDivider onOpen={() => setShowCompactions(true)} />
                        ) : temporary ? (
                          <>
                            {m.speaker && (
                              <div className="mx-auto mb-1 flex max-w-3xl items-center gap-1.5 px-1 text-xs font-semibold">
                                <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: m.speaker.color || "#888" }} />
                                <span style={{ color: m.speaker.color || undefined }}>{m.speaker.name}</span>
                              </div>
                            )}
                            <MessageBubble
                              role={m.role}
                              content={m.content}
                              time={m.created_at}
                              name={m.role === "assistant" ? modelLabel : undefined}
                              reasoning={m.reasoning}
                              toolEvents={m.tool_events ?? undefined}
                              onSpeak={m.role === "assistant" && voiceSettingsFor(m).enabled ? () => toggleMessageSpeech(m) : undefined}
                              speaking={speakingMessageId === m.id}
                              onDelete={() => deleteMessage(m.id)}
                            />
                          </>
                        ) : isRt ? (
                          <div className="mx-auto flex max-w-3xl gap-3">
                            {showAv && (
                              av ? (
                                // eslint-disable-next-line @next/next/no-img-element
                                <img src={av} alt="" className="mt-1 h-9 w-9 shrink-0 rounded-full object-cover" style={{ boxShadow: `0 0 0 2px ${m.speaker!.color || "#888"}` }} />
                              ) : (
                                <span className="mt-1 flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-sm font-semibold text-white" style={{ background: m.speaker!.color || "#888" }}>
                                  {m.speaker!.name[0]?.toUpperCase()}
                                </span>
                              )
                            )}
                            <div className="min-w-0 flex-1">{item}</div>
                          </div>
                        ) : (
                          item
                        )}
                      </div>
                    );
                  })}
                  {sending && subagents.length > 0 && (
                    <div className="mx-auto flex max-w-3xl flex-wrap items-center gap-1.5">
                      {subagents.map((a) => (
                        <span key={a.name} className="flex items-center gap-1.5 rounded-lg border border-accent/30 bg-accent/10 px-2.5 py-1 text-xs text-accent-hover">
                          <Users size={13} className="animate-pulse" /> {a.name} trabalhando…
                          {a.ctx && <span title="Com o contexto do chat" className="rounded bg-accent/15 px-1.5 py-0.5 text-[10px] font-medium">contexto</span>}
                          {a.mem && <span title="Com memória própria" className="rounded bg-accent/15 px-1.5 py-0.5 text-[10px] font-medium">memória</span>}
                        </span>
                      ))}
                    </div>
                  )}
                  {sending && guardNote && <GuardRetry note={guardNote} />}
                  {/* mesa-redonda: fala do participante da vez, em streaming */}
                  {isRoundtable && rtStreaming && (
                    <div className="msg-row">
                      <div className="mx-auto flex max-w-3xl gap-3">
                        {iface.chat_model_image !== false && (() => {
                          const av = speakerAvatar(rtStreaming.speaker);
                          const col = rtStreaming.speaker.color || "#888";
                          return av ? (
                            // eslint-disable-next-line @next/next/no-img-element
                            <img src={av} alt="" className="mt-1 h-9 w-9 shrink-0 rounded-full object-cover" style={{ boxShadow: `0 0 0 2px ${col}` }} />
                          ) : (
                            <span className="mt-1 flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-sm font-semibold text-white" style={{ background: col }}>
                              {rtStreaming.speaker.name[0]?.toUpperCase()}
                            </span>
                          );
                        })()}
                        <div className="min-w-0 flex-1">
                          <MessageBubble
                            role="assistant"
                            content={rtStreaming.content}
                            streaming={!!rtStreaming.content}
                            bare
                            name={rtStreaming.speaker.name}
                            nameColor={rtStreaming.speaker.color ?? null}
                            reasoning={rtStreaming.reasoning ? { text: rtStreaming.reasoning } : null}
                            reasoningLive={!rtStreaming.content}
                          />
                        </div>
                      </div>
                    </div>
                  )}
                  {isRoundtable && rtRunning && !rtStreaming && <Thinking />}
                  {!isRoundtable && (streaming || streamingReasoning || generatingImage || consultingKnowledge || transcribingAudio || (sending && toolEvents.length > 0) ? (
                    <MessageBubble
                      role="assistant"
                      content={streaming}
                      streaming={!!streaming}
                      name={modelLabel}
                      reasoning={streamingReasoning || streamingSteps.length ? { text: streamingReasoning, steps: streamingSteps } : null}
                      reasoningLive={sending}
                      toolEvents={toolEvents.length ? toolEvents : undefined}
                      toolsLive={sending}
                      status={statusFor({ sending, phase: streamPhase, streaming, streamingReasoning, generatingImage, consultingKnowledge, transcribingAudio, toolEvents })}
                      footer={generatingImage ? <GeneratingImage /> : consultingKnowledge ? <ConsultingKnowledge /> : transcribingAudio ? <TranscribingAudio /> : undefined}
                    />
                  ) : (
                    sending && <Thinking />
                  ))}
                  </div>
                  {activeMiniApp === "imaginai" ? (
                    <ImaginaiDnd5eDocks
                      snapshot={imaginaiSnapshot}
                      loading={imaginaiLoading}
                      error={imaginaiError}
                      onSnapshotChange={setImaginaiSnapshot}
                    />
                  ) : null}
                </div>
                {/* Composer NO FLUXO (shrink-0): ocupa espaço de verdade, então a área
                    de rolagem acima nunca fica maior que o disponível. Cresce (anexos,
                    AskOptions, multilinha) encolhendo a área de rolagem automaticamente
                    — sem medir nada. O gradiente é `absolute` ACIMA dele (-top-12), só
                    enfeite: não entra no layout e não pode desalinhar a geometria. */}
                <div className="relative z-10 shrink-0">
                  <div className="pointer-events-none absolute -top-12 inset-x-0 h-12 bg-gradient-to-t from-bg to-transparent" />
                  <div className="chat-composer-shell bg-bg px-4 pb-3">
                    <div className="chat-composer-grid">
                      <div className="chat-composer-center relative min-w-0">
                        {speakingMessageId && (
                          <SpeechController variant="mobile" progress={speechProgress} onClose={stopMessageSpeech} />
                        )}
                        <div
                          className={`relative z-10 rounded-2xl transition-shadow ${csDropOver ? "ring-2 ring-accent/50" : ""}`}
                          onDragOver={(e) => { const t = e.dataTransfer.types;
                        if (t.includes(CODESPACE_DND_MIME) || t.includes(CODESPACE_SNIPPET_MIME)) { e.preventDefault(); setCsDropOver(true); } }}
                          onDragLeave={() => setCsDropOver(false)}
                          onDrop={handleComposerFileDrop}
                        >
                          {!atBottom && (
                            <button
                              onClick={scrollToBottom}
                              title="Ir para a última mensagem"
                              aria-label="Ir para a última mensagem"
                              className="animate-pop absolute -top-11 left-1/2 z-20 flex h-9 w-9 -translate-x-1/2 items-center justify-center rounded-full border border-border bg-surface text-ink-soft shadow-menu transition-colors hover:bg-hover hover:text-ink"
                            >
                              <ArrowDown size={18} />
                            </button>
                          )}
                          {showAsk && askSpec && (
                            <AskOptions spec={askSpec} onPick={(v) => send(v)} onDismiss={() => setDismissedAsk(lastMsg?.id ?? null)} />
                          )}
                          <PromptBox value={input} onChange={setInput} onSend={send} onStop={handleStop} onQueue={enqueue} queued={queued} sending={sending} recording={recording} onToggleMic={toggleMic} onVoiceMode={toggleVoiceMode} modelTools={modelTools} prompts={prompts} skills={skills} attachedSkillIds={attachedSkillIds} onAttachedSkillIdsChange={setAttachedSkillIds} agents={agentsForMention} agentId={agentId} onAgentChange={setAgentId} knowledgeRefs={knowledgeRefs} refDocs={refDocs} onRefDocsChange={setRefDocs} chats={chats.filter((c) => c.id !== active?.id)} refChats={refChats} onRefChatsChange={setRefChats} capabilities={curCustom?.capabilities} attachments={attachments} onAttachmentsChange={setAttachments} reasoning={reasoningEffort} onReasoningChange={setReasoningEffort} reasoningModel={curCustom ? curCustom.base_model : curModel} context={contextInfo} onCompact={compactContext} onHistory={() => setShowCompactions(true)} compacting={compacting} menuUp activeMiniApp={activeMiniApp} onActiveMiniAppChange={setActiveMiniApp} temporary={temporary} placeholder={showAsk ? "Escolha uma opção acima ou escreva sua resposta…" : undefined} />
                        </div>
                      </div>
                      {speakingMessageId && (
                        <SpeechController variant="desktop" progress={speechProgress} onClose={stopMessageSpeech} />
                      )}
                    </div>
                  </div>
                </div>
                <MessageNavigator messages={messages} onJump={jumpToMessage} />
              </>
            )}
          </div>
          {/* Artefatos: coluna ao lado da conversa; no mobile vira tela cheia.
              Desktop: largura controlada pelo DIVISOR arrastável (borda esquerda). */}
          {artifactsEnabled && hasConversation && artifactOpen != null && (liveArtifact || chatArtifacts.length > 0) && (
            <div
              ref={artResize.ref}
              style={{ "--artw": `${artResize.w}px` } as React.CSSProperties}
              className="fixed inset-0 z-50 shrink-0 bg-bg md:relative md:z-auto md:w-[var(--artw)] md:min-w-[380px] md:max-w-[70vw] md:bg-transparent"
            >
              {artResize.divider}
              <ArtifactPanel
                artifacts={chatArtifacts}
                openIdentifier={artifactOpen}
                live={liveArtifact}
                onSelect={(ident) => setArtifactOpen(ident)}
                onClose={() => { setArtifactOpen(null); setLiveArtifact(null); }}
                onChanged={async () => { if (active) await reloadArtifacts(active.id); }}
              />
            </div>
          )}
          {/* Arquivos do projeto: mesma coluna do lado, só quando o chat está vinculado.
              Desktop: largura controlada pelo DIVISOR arrastável (var CSS + estado);
              mobile: tela cheia, sem divisor. */}
          {csFilesOpen && active?.project_id && (
            <div
              ref={csFilesRef}
              style={{ "--csw": `${csFilesW}px` } as React.CSSProperties}
              className="fixed inset-0 z-50 shrink-0 bg-bg p-3 md:relative md:inset-auto md:z-auto md:w-[var(--csw)] md:min-w-[300px] md:max-w-[70vw] md:bg-transparent"
            >
              <div
                onPointerDown={startCsResize}
                title="Arraste para redimensionar"
                className="group absolute inset-y-0 -left-1 z-10 hidden w-2.5 cursor-col-resize items-stretch justify-center md:flex"
              >
                <div className="w-[3px] rounded-full bg-transparent transition-colors group-hover:bg-accent/50 group-active:bg-accent" />
              </div>
              <div className="flex h-full flex-col gap-2">
                <div className="flex items-center justify-between">
                  <span className="flex items-center gap-1.5 text-sm font-medium text-ink"><Code2 size={15} className="text-accent-hover" /> Arquivos</span>
                  <button onClick={() => setCsFilesOpen(false)} className="rounded-lg p-1.5 text-muted hover:bg-hover hover:text-ink"><X size={16} /></button>
                </div>
                <CodespaceFileBrowser
                  key={active.project_id}
                  projectId={active.project_id}
                  dense
                  useLabel="Inserir no chat"
                  onUse={(path, content) => {
                    const prefill = `Sobre o arquivo \`${path}\`:\n\n\`\`\`${extLang(path)}\n${content}\n\`\`\`\n\n`;
                    setInput((v) => (v ? `${v}\n\n${prefill}` : prefill));
                    setCsFilesOpen(false);
                  }}
                />
              </div>
            </div>
          )}
        </div>
        </>
        )}
      </main>

      {/* Controles: coluna de altura total à direita, como a barra lateral esquerda */}
      {showControls && !workspaceOpen && (
        <>
          {/* backdrop (mobile): Controles viram slide-over à direita */}
          <div className="fixed inset-0 z-40 bg-black/50 md:hidden" onClick={() => setShowControls(false)} />
          <div
            ref={ctrlResize.ref}
            style={{ "--ctrlw": `${ctrlResize.w}px` } as React.CSSProperties}
            className="fixed inset-y-0 right-0 z-50 flex shrink-0 md:relative md:z-auto md:w-[var(--ctrlw)]"
          >
            {ctrlResize.divider}
            <Controls
              key={active?.id ?? "draft"}
              systemPrompt={active
                ? (active.model_config_id && curCustom
                  ? curCustom.system_prompt ?? ""
                  : active.system_prompt ?? "")
                : draftSystemPrompt}
              params={activeParams}
              memory={active ? active.memory_config ?? null : undefined}
              memoryDefault={memoryDefault}
              hasProject={!!active?.folder_id}
              onMemoryChange={active ? (cfg) => patchActive({ memory_config: cfg }) : undefined}
              knowledge={active ? active.knowledge_config ?? null : undefined}
              onKnowledgeChange={active ? (cfg) => patchActive({ knowledge_config: cfg }) : undefined}
              brain={active ? active.brain_config ?? null : undefined}
              onBrainChange={active ? (cfg) => patchActive({ brain_config: cfg }) : undefined}
              onSave={async (sp, params) => {
                if (active?.model_config_id && curCustom) {
                  // Com modelo custom, estes controles pertencem ao preset —
                  // salvar no Chat criaria outro snapshot antigo e o próximo
                  // turno ignoraria a alteração. Atualiza o preset ativo.
                  const updated = await api.patch<ModelConfig>(`/models/${curCustom.id}`, {
                    system_prompt: sp,
                    params,
                  });
                  setCustomModels((models) => models.map((m) => m.id === updated.id ? updated : m));
                } else if (active) {
                  await patchActive({ system_prompt: sp, params });
                } else {
                  setDraftSystemPrompt(sp ?? "");
                  setDraftParams(params);
                }
              }}
              onClose={() => setShowControls(false)}
            />
          </div>
        </>
      )}

      {showShare && active && (
        <ShareModal
          chat={active}
          onClose={() => setShowShare(false)}
          onChange={(publicId) => setActive((a) => (a ? { ...a, public_id: publicId } : a))}
        />
      )}

      {showSettings && (
        <SettingsModal
          initialCat={settingsCat}
          initialView={settingsView}
          onClose={() => { setShowSettings(false); setSettingsCat(undefined); setSettingsView(undefined); }}
          onSaved={() => { api.get<User>("/auth/me").then(setUser).catch(() => {}); }}
          onConnectionsChanged={refreshExtModels}
        />
      )}
      {showOnboarding && user && (
        <OnboardingModal
          user={user}
          onClose={() => setShowOnboarding(false)}
          onDone={() => { api.get<User>("/auth/me").then(setUser).catch(() => {}); refreshExtModels(); }}
        />
      )}
      {showPalette && <CommandPalette items={paletteItems} onClose={() => setShowPalette(false)} />}
      {showArchived && <ArchivedModal onChanged={refreshChats} onClose={() => setShowArchived(false)} />}
      {showChatMgr && (
        <ChatManager
          chats={chats}
          folders={folders}
          onSelect={(id) => selectChat(id).catch(() => {})}
          onMove={bulkMoveChats}
          onDelete={bulkDeleteChats}
          onClose={() => setShowChatMgr(false)}
        />
      )}
      {infoChatId && (
        <ChatInfoModal
          chatId={infoChatId}
          onOpenArtifact={(ident) => { setInfoChatId(null); setArtifactOpen(ident); }}
          onClose={() => setInfoChatId(null)}
        />
      )}
      {showCompactions && active && (
        <CompactionHistory
          chatId={active.id}
          onClose={() => setShowCompactions(false)}
          onPinned={() => reloadMessages(active.id)}
        />
      )}

      {/* Notificações (toasts) — canto superior direito */}
      {toasts.length > 0 && (
        <div className="fixed right-4 top-[calc(1rem+env(safe-area-inset-top))] z-[100] flex w-80 max-w-[calc(100vw-2rem)] flex-col gap-2">
          {toasts.map((t) => (
            <div key={t.id} className="animate-fade-up flex items-start gap-3 rounded-xl border border-border bg-surface px-4 py-3 shadow-menu">
              <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-accent/15 text-accent-hover">
                <Bell size={15} />
              </span>
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium text-ink">{t.title}</p>
                {t.body && <p className="mt-0.5 truncate text-xs text-muted">{t.body}</p>}
              </div>
              <button onClick={() => dismissToast(t.id)} className="shrink-0 text-muted transition-colors hover:text-ink">
                <X size={14} />
              </button>
            </div>
          ))}
        </div>
      )}

      {/* Modo voz (assistente) — HUD flutuante */}
      {voicePhase !== "off" && (
        <div className="fixed bottom-[calc(1.25rem+env(safe-area-inset-bottom))] left-1/2 z-[110] -translate-x-1/2">
          <div className="animate-fade-up flex items-center gap-3 rounded-full border border-border bg-surface/95 py-2 pl-3 pr-2 shadow-menu backdrop-blur">
            <span
              className={`relative flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-white transition-colors ${voicePhase === "listening" ? "bg-accent" : voicePhase === "speaking" ? "bg-green-500" : "bg-surface2 text-muted"}`}
              style={voicePhase === "listening" ? { transform: `scale(${1 + Math.min(voiceLevel, 1) * 0.3})` } : undefined}
            >
              {voicePhase === "listening" ? <Mic size={16} /> : voicePhase === "speaking" ? <Volume2 size={16} /> : <Loader2 size={16} className="animate-spin" />}
            </span>
            <span className="min-w-[7.5rem] text-sm text-ink">
              {voicePhase === "listening" ? "Ouvindo…" : voicePhase === "thinking" ? "Processando…" : "Falando…"}
            </span>
            <button
              onClick={stopVoiceMode}
              title="Encerrar modo voz"
              className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-hover text-muted transition-colors hover:bg-red-500/15 hover:text-red-400"
            >
              <Square size={15} />
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

/** Nome amigável (pt-BR) de uma ferramenta p/ a linha de status ao vivo. */
function prettyTool(name: string): string {
  const map: Record<string, string> = {
    code__files__browse: "lendo arquivos do projeto",
    code__files__write: "editando arquivos do projeto",
    code__graph__query: "consultando o grafo de código",
    code__flow__analyze: "analisando o fluxo do código",
    code__exec__run: "rodando comandos no projeto",
    code__task__manage: "organizando tarefas do projeto",
    web__search__query: "buscando na web",
    web__page__read: "lendo uma página da web",
    web__browser__use: "navegando no navegador",
    research__deep__run: "fazendo uma pesquisa profunda",
    media__video__transcribe: "transcrevendo o vídeo",
    search_tools: "procurando a ferramenta certa",
    execute_tool: "preparando uma ferramenta",
  };
  return map[name] ?? name.replace(/__/g, ".").replace(/_/g, " ");
}

/** "O que a IA está fazendo agora" — uma linha de status estável durante a geração,
 *  para que pausas/transições nunca pareçam travamento. Os rodapés dedicados de
 *  imagem/conhecimento/áudio continuam tendo prioridade sobre a fase genérica. */
function statusFor(f: {
  sending: boolean; phase: import("./useGeneration").StreamPhase; streaming: string; streamingReasoning: string;
  generatingImage: boolean; consultingKnowledge: boolean; transcribingAudio: boolean;
  toolEvents: ToolEvent[];
}): string | null {
  if (!f.sending) return null;
  if (f.generatingImage || f.consultingKnowledge || f.transcribingAudio) return null;
  const last = f.toolEvents.length ? f.toolEvents[f.toolEvents.length - 1] : null;
  if (last && last.kind === "call") return `Executando — ${prettyTool(last.name)}…`;
  if (f.phase === "preparing") return "Preparando contexto e aguardando o provider…";
  if (f.phase === "thinking") return "Raciocinando…";
  if (f.phase === "tool") return "Executando ferramenta…";
  if (f.phase === "streaming") return "Respondendo…";
  return "Trabalhando…"; // fallback para retomada de stream sem evento classificável
}

function formatSpeechTime(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "0:00";
  const rounded = Math.floor(seconds);
  return `${Math.floor(rounded / 60)}:${String(rounded % 60).padStart(2, "0")}`;
}

function SpeechController({
  variant,
  progress,
  onClose,
}: {
  variant: "desktop" | "mobile";
  progress: SpeechProgress;
  onClose: () => void;
}) {
  const [mobileOpen, setMobileOpen] = useState(false);
  const loading = progress.phase === "loading";
  const paused = progress.phase === "paused";
  const rates = [1, 1.25, 1.5, 2];
  const currentRate = rates.findIndex((rate) => rate === progress.rate);
  const nextRate = rates[(currentRate + 1 + rates.length) % rates.length];
  const elapsed = formatSpeechTime(progress.currentTime);
  const duration = progress.duration > 0 ? formatSpeechTime(progress.duration) : null;
  const canSeek = progress.seekable && progress.duration > 0;
  const timelineMax = canSeek ? progress.duration : 1;
  const timelineValue = canSeek ? Math.min(progress.currentTime, progress.duration) : 0;
  const timelinePercent = canSeek ? Math.min(100, (timelineValue / timelineMax) * 100) : 0;

  const playButton = (
    <button
      type="button"
      onClick={toggleSpeakingPaused}
      disabled={loading}
      title={loading ? "Preparando o áudio completo" : paused ? "Continuar leitura" : "Pausar leitura"}
      aria-label={loading ? "Preparando o áudio completo" : paused ? "Continuar leitura" : "Pausar leitura"}
      className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-accent text-white transition-colors hover:bg-accent-hover disabled:cursor-wait disabled:opacity-70"
    >
      {loading ? <Loader2 size={17} className="animate-spin" /> : paused ? <Play size={17} fill="currentColor" /> : <Pause size={17} fill="currentColor" />}
    </button>
  );

  const transport = (
    <>
      <button
        type="button"
        onClick={() => setSpeakingRate(nextRate)}
        title="Alterar velocidade"
        className="h-9 min-w-10 rounded-xl px-2 text-xs font-semibold text-ink-soft transition-colors hover:bg-hover hover:text-ink"
      >
        {progress.rate}×
      </button>
      <button
        type="button"
        onClick={() => seekSpeaking(-15)}
        disabled={!progress.seekable}
        title={progress.seekable ? "Voltar 15 segundos" : "Indisponível na voz do navegador"}
        className="relative flex h-9 w-9 items-center justify-center rounded-xl text-ink-soft transition-colors hover:bg-hover hover:text-ink disabled:opacity-35"
      >
        <RotateCcw size={19} />
        <span className="absolute text-[8px] font-bold">15</span>
      </button>
      <button
        type="button"
        onClick={() => seekSpeaking(15)}
        disabled={!progress.seekable}
        title={progress.seekable ? "Avançar 15 segundos" : "Indisponível na voz do navegador"}
        className="relative flex h-9 w-9 items-center justify-center rounded-xl text-ink-soft transition-colors hover:bg-hover hover:text-ink disabled:opacity-35"
      >
        <RotateCw size={19} />
        <span className="absolute text-[8px] font-bold">15</span>
      </button>
      <button
        type="button"
        onClick={onClose}
        title="Parar e fechar"
        className="flex h-9 w-9 items-center justify-center rounded-xl text-muted transition-colors hover:bg-red-500/15 hover:text-red-400"
      >
        <X size={18} />
      </button>
    </>
  );

  const timeline = (
    <div className="flex min-w-0 items-center gap-2 px-1">
      <input
        type="range"
        min={0}
        max={timelineMax}
        step={0.1}
        value={timelineValue}
        onChange={(event) => seekSpeakingTo(Number(event.target.value))}
        disabled={!canSeek || loading}
        aria-label="Posição da leitura"
        title={canSeek ? "Arraste para mudar a posição" : "A timeline estará disponível quando o áudio terminar de carregar"}
        className="speech-progress min-w-0 flex-1"
        style={{ "--speech-progress": `${timelinePercent}%` } as React.CSSProperties}
      />
      <span aria-live="polite" className="shrink-0 font-mono text-[10px] tabular-nums text-muted">
        {elapsed} / {duration ?? "--:--"}
      </span>
    </div>
  );

  const status = loading ? "Preparando áudio…" : paused ? "Leitura pausada" : "Lendo resposta";

  if (variant === "desktop") {
    return (
      <div className="speech-desktop-player min-w-0 items-center justify-start pl-3 pr-4">
        <div role="region" aria-label="Leitura em voz alta" className="animate-pop min-w-0 w-full max-w-[22rem] rounded-2xl border border-border bg-surface p-2 shadow-prompt">
          <div className="flex min-w-0 items-center gap-1">
            {playButton}
            <span className="min-w-0 flex-1 px-1">
              <span className="block truncate text-xs font-medium text-ink">{status}</span>
            </span>
            {transport}
          </div>
          <div className="pt-1.5">{timeline}</div>
        </div>
      </div>
    );
  }

  return (
    <div className={`speech-mobile-drawer relative z-0 mx-8 transition-[height] duration-200 ${mobileOpen ? "h-[7rem]" : "h-5"}`}>
      <div className="absolute inset-x-0 bottom-[-0.75rem] rounded-t-2xl border border-border bg-surface px-2 pb-4 pt-1 shadow-prompt">
        <button
          type="button"
          onClick={() => setMobileOpen((open) => !open)}
          aria-expanded={mobileOpen}
          title={mobileOpen ? "Recolher controles de leitura" : "Mostrar controles de leitura"}
          className="flex h-6 w-full items-center justify-center gap-1.5 text-[11px] font-medium text-muted transition-colors hover:text-ink"
        >
          <span className="h-1 w-8 rounded-full bg-border" />
          <span>Leitura · {elapsed}</span>
          {mobileOpen ? <ChevronDown size={13} /> : <ChevronUp size={13} />}
        </button>
        {mobileOpen && (
          <div role="region" aria-label="Controles da leitura em voz alta" className="animate-pop pt-1 text-ink">
            <div className="flex min-w-0 items-center justify-center gap-1">
              {playButton}
              <span className="min-w-0 flex-1 px-1 text-xs font-medium text-ink">{status}</span>
              {transport}
            </div>
            <div className="pt-2">{timeline}</div>
          </div>
        )}
      </div>
    </div>
  );
}

const DND_CHARACTER_SECTIONS = [
  { id: "inventory", label: "Inventário", icon: Package },
  { id: "spells", label: "Magias", icon: Sparkles },
  { id: "sheet", label: "Ficha", icon: ScrollText },
] as const;

const DND_WORLD_SECTIONS = [
  { id: "journal", label: "Diário", icon: BookOpen },
  { id: "map", label: "Mapa", icon: MapIcon },
  { id: "codex", label: "Codex", icon: ScrollText },
] as const;

type CharacterSection = (typeof DND_CHARACTER_SECTIONS)[number]["id"];
type WorldSection = (typeof DND_WORLD_SECTIONS)[number]["id"];

/**
 * Docks do primeiro sistema do Imaginai. A composição em dois painéis permite que
 * sistemas futuros forneçam seus próprios campos sem alterar a coluna central.
 */
function ImaginaiDnd5eDocks({
  snapshot,
  loading,
  error,
  onSnapshotChange,
}: {
  snapshot: ImaginaiSnapshot | null;
  loading: boolean;
  error: string | null;
  onSnapshotChange: (snapshot: ImaginaiSnapshot) => void;
}) {
  const [characterSection, setCharacterSection] = useState<CharacterSection | null>(null);
  const [worldSection, setWorldSection] = useState<WorldSection | null>(null);
  const [system, setSystem] = useState<ImaginaiSystemDefinition | null>(null);
  const [systemError, setSystemError] = useState<string | null>(null);
  const [mobilePanel, setMobilePanel] = useState<"world" | "character" | null>(null);
  const [configOpen, setConfigOpen] = useState(false);
  const [worldBuilderOpen, setWorldBuilderOpen] = useState(false);
  const [campaignNameDraft, setCampaignNameDraft] = useState("");
  const [narrationDraft, setNarrationDraft] = useState<"balanced" | "cinematic" | "gritty">("balanced");
  const [difficultyDraft, setDifficultyDraft] = useState<"story" | "balanced" | "challenging">("balanced");
  const [premiseDraft, setPremiseDraft] = useState("");
  const [openingSceneDraft, setOpeningSceneDraft] = useState("");
  const [startingLocationNameDraft, setStartingLocationNameDraft] = useState("");
  const [startingLocationDescriptionDraft, setStartingLocationDescriptionDraft] = useState("");
  const [savingConfig, setSavingConfig] = useState(false);
  const [configError, setConfigError] = useState<string | null>(null);
  const dnd = (snapshot?.character?.state.dnd5e ?? {}) as Record<string, unknown>;
  const hp = (dnd.hp ?? {}) as Record<string, unknown>;
  const className = typeof dnd.class === "string" ? dnd.class : "Classe";
  const level = typeof dnd.level === "number" ? dnd.level : 1;
  const hpCurrent = typeof hp.current === "number" ? hp.current : null;
  const hpMax = typeof hp.max === "number" ? hp.max : null;
  const armorClass = typeof dnd.armor_class === "number" ? dnd.armor_class : null;
  const campaignName = snapshot?.campaign.name ?? "Nome da Campanha";
  const characterName = snapshot?.character?.name ?? "Nome do personagem";
  const status = loading ? "Abrindo mundo…" : error ? "Mundo indisponível" : snapshot?.location?.name;

  useEffect(() => {
    const systemKey = snapshot?.campaign.system_key;
    if (!systemKey) {
      setSystem(null);
      setSystemError(null);
      return;
    }
    let cancelled = false;
    setSystem(null);
    setSystemError(null);
    api.get<ImaginaiSystemDefinition>(`/mini-apps/imaginai/systems/${systemKey}`)
      .then((definition) => { if (!cancelled) setSystem(definition); })
      .catch((loadError: unknown) => {
        if (!cancelled) setSystemError(loadError instanceof Error ? loadError.message : "Sistema indisponível");
      });
    return () => { cancelled = true; };
  }, [snapshot?.campaign.system_key]);

  // Esc fecha as abas abertas acima dos cards (o modal de configuração tem o seu).
  useEffect(() => {
    if (configOpen || (!worldSection && !characterSection)) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setWorldSection(null);
      setCharacterSection(null);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [configOpen, worldSection, characterSection]);

  useEffect(() => {
    if (!configOpen) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setConfigOpen(false);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [configOpen]);

  function openCampaignConfig() {
    if (!snapshot) return;
    setCampaignNameDraft(snapshot.campaign.name);
    setNarrationDraft(snapshot.campaign.settings?.narration_style ?? "balanced");
    setDifficultyDraft(snapshot.campaign.settings?.difficulty ?? "balanced");
    setPremiseDraft(snapshot.campaign.settings?.premise ?? "");
    setOpeningSceneDraft(snapshot.campaign.settings?.opening_scene ?? "");
    setStartingLocationNameDraft(snapshot.location?.name ?? "Local inicial");
    setStartingLocationDescriptionDraft(snapshot.location?.description ?? "");
    setConfigError(null);
    setConfigOpen(true);
  }

  async function saveCampaignConfig(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!snapshot || !campaignNameDraft.trim()) return;
    setSavingConfig(true);
    setConfigError(null);
    try {
      const updated = await api.patch<ImaginaiSnapshot>(
        `/mini-apps/imaginai/campaigns/${snapshot.campaign.id}`,
        {
          name: campaignNameDraft.trim(),
          narration_style: narrationDraft,
          difficulty: difficultyDraft,
          premise: premiseDraft,
          opening_scene: openingSceneDraft,
          starting_location_name: startingLocationNameDraft.trim(),
          starting_location_description: startingLocationDescriptionDraft,
        },
      );
      onSnapshotChange(updated);
      setConfigOpen(false);
    } catch (saveError) {
      setConfigError(saveError instanceof Error ? saveError.message : "Não foi possível salvar a campanha");
    } finally {
      setSavingConfig(false);
    }
  }

  return (
    <>
      <div className="imaginai-docks" aria-label="Painéis do Imaginai">
        {mobilePanel ? (
          <button
            type="button"
            aria-label="Fechar painel do Imaginai"
            className="imaginai-mobile-scrim"
            onClick={() => setMobilePanel(null)}
          />
        ) : null}

        <button
          type="button"
          aria-label="Abrir Worldinfo"
          aria-expanded={mobilePanel === "world"}
          onClick={() => setMobilePanel((current) => current === "world" ? null : "world")}
          className="imaginai-edge-tab imaginai-edge-tab-left"
        >
          <BookOpen size={17} />
          <ChevronRight size={14} />
        </button>
        <button
          type="button"
          aria-label="Abrir personagem"
          aria-expanded={mobilePanel === "character"}
          onClick={() => setMobilePanel((current) => current === "character" ? null : "character")}
          className="imaginai-edge-tab imaginai-edge-tab-right"
        >
          <ChevronLeft size={14} />
          <Users size={17} />
        </button>

        <aside className="imaginai-world-dock" data-mobile-open={mobilePanel === "world"} aria-label="Worldinfo">
          {worldSection && snapshot ? (
            <section className="imaginai-dock-card imaginai-feature-sheet animate-pop" aria-label={DND_WORLD_SECTIONS.find((section) => section.id === worldSection)?.label}>
              <ImaginaiSheetHead label="Worldinfo" onClose={() => setWorldSection(null)} />
              <div className="imaginai-feature">
                {worldSection === "journal" ? <ImaginaiJournalPanel campaignId={snapshot.campaign.id} /> : null}
                {worldSection === "codex" ? <ImaginaiCodexPanel campaignId={snapshot.campaign.id} /> : null}
                {worldSection === "map" ? <ImaginaiMapPanel campaignId={snapshot.campaign.id} /> : null}
              </div>
            </section>
          ) : null}
          <section className="imaginai-dock-card">
            <div className="flex min-h-7 items-center justify-between gap-2">
              <p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-violet-300">Worldinfo</p>
              <button
                type="button"
                onClick={openCampaignConfig}
                disabled={!snapshot || loading}
                title="Configurar campanha"
                aria-label="Configurar campanha"
                className="-mr-1.5 flex h-7 w-7 cursor-pointer items-center justify-center rounded-lg text-muted transition-colors hover:bg-hover hover:text-ink disabled:cursor-not-allowed disabled:opacity-40"
              >
                <Settings size={15} />
              </button>
            </div>
            <h2 className="mt-1.5 truncate text-sm font-semibold leading-5 text-ink" title={campaignName}>{campaignName}</h2>
            <p className={`mt-0.5 truncate text-[11px] leading-4 ${error ? "text-rose-400" : "text-muted"}`} title={error ?? status ?? undefined}>
              {status || "\u00a0"}
            </p>
            <div className="mt-3 grid grid-cols-3 gap-1 border-t border-border pt-2" role="group" aria-label="Navegação da campanha">
              {DND_WORLD_SECTIONS.map((section) => {
                const Icon = section.icon;
                const selected = worldSection === section.id;
                return (
                  <button
                    key={section.id}
                    type="button"
                    disabled={!snapshot}
                    aria-pressed={selected}
                    onClick={() => setWorldSection(selected ? null : section.id)}
                    className={`imaginai-dock-action ${selected ? "imaginai-dock-action-active" : ""}`}
                  >
                    <Icon size={16} />
                    <span className="truncate">{section.label}</span>
                  </button>
                );
              })}
            </div>
          </section>
        </aside>

        <aside className="imaginai-character-dock" data-mobile-open={mobilePanel === "character"} aria-label="Personagem">
          {characterSection && (snapshot || characterSection === "sheet") ? (
            <section className="imaginai-dock-card imaginai-feature-sheet animate-pop" aria-label={DND_CHARACTER_SECTIONS.find((section) => section.id === characterSection)?.label}>
              <ImaginaiSheetHead label="Personagem" onClose={() => setCharacterSection(null)} />
              <div className="imaginai-feature">
                {characterSection === "inventory" && snapshot ? (
                  <ImaginaiInventoryPanel campaignId={snapshot.campaign.id} system={system} />
                ) : null}
                {characterSection === "sheet" ? (
                  <ImaginaiSheetPanel
                    campaignId={snapshot?.campaign.id ?? null}
                    character={snapshot?.character ?? null}
                    system={system}
                    error={systemError}
                    onSnapshotChange={onSnapshotChange}
                  />
                ) : null}
                {characterSection === "spells" && snapshot ? <ImaginaiSpellsPanel campaignId={snapshot.campaign.id} /> : null}
              </div>
            </section>
          ) : null}
          <section className="imaginai-dock-card">
            <div className="flex min-h-7 items-center">
              <p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-violet-300">Personagem</p>
            </div>
            <h2 className="mt-1.5 truncate text-sm font-semibold leading-5 text-ink" title={characterName}>{characterName}</h2>
            <p className="mt-0.5 truncate text-[11px] leading-4 text-muted">{className} · Nível {level}</p>
            <div className="mt-2.5 grid grid-cols-2 gap-1.5">
              <div className="flex min-w-0 items-center justify-between gap-2 rounded-xl bg-surface2/70 px-2.5 py-1.5">
                <span className="flex items-center gap-1.5 text-[11px] font-medium text-ink-soft"><Heart size={13} className="shrink-0 text-rose-400" /> HP</span>
                <span className="truncate font-mono text-[11px] text-ink">{hpCurrent ?? "—"}/{hpMax ?? "—"}</span>
              </div>
              <div className="flex min-w-0 items-center justify-between gap-2 rounded-xl bg-surface2/70 px-2.5 py-1.5">
                <span className="flex items-center gap-1.5 text-[11px] font-medium text-ink-soft"><Shield size={13} className="shrink-0 text-sky-300" /> CA</span>
                <span className="truncate font-mono text-[11px] text-ink">{armorClass ?? "—"}</span>
              </div>
            </div>
            <div className="mt-2.5 grid grid-cols-3 gap-1 border-t border-border pt-2" role="group" aria-label="Navegação do personagem">
              {DND_CHARACTER_SECTIONS.map((section) => {
                const Icon = section.icon;
                const selected = characterSection === section.id;
                return (
                  <button
                    key={section.id}
                    type="button"
                    aria-pressed={selected}
                    onClick={() => setCharacterSection(selected ? null : section.id)}
                    className={`imaginai-dock-action ${selected ? "imaginai-dock-action-active" : ""}`}
                  >
                    <Icon size={16} />
                    <span className="truncate">{section.label}</span>
                  </button>
                );
              })}
            </div>
          </section>
        </aside>
      </div>

      {configOpen && snapshot ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/55 p-4 backdrop-blur-sm" onMouseDown={() => setConfigOpen(false)}>
          <form
            role="dialog"
            aria-modal="true"
            aria-labelledby="imaginai-campaign-settings-title"
            onSubmit={saveCampaignConfig}
            onMouseDown={(event) => event.stopPropagation()}
            className="max-h-[calc(100dvh-2rem)] w-full max-w-lg overflow-y-auto rounded-2xl border border-border bg-surface p-4 shadow-menu"
          >
            <div className="flex items-center justify-between gap-3">
              <div>
                <p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-violet-300">Imaginai</p>
                <h2 id="imaginai-campaign-settings-title" className="mt-0.5 text-base font-semibold text-ink">Configurar campanha</h2>
              </div>
              <button type="button" onClick={() => setConfigOpen(false)} aria-label="Fechar configurações" className="flex h-10 w-10 items-center justify-center rounded-xl text-muted transition-colors hover:bg-hover hover:text-ink">
                <X size={17} />
              </button>
            </div>
            <label className="mt-4 block text-xs font-medium text-ink-soft">
              Nome da campanha
              <input
                autoFocus
                value={campaignNameDraft}
                onChange={(event) => setCampaignNameDraft(event.target.value)}
                maxLength={255}
                required
                className="mt-1.5 min-h-11 w-full rounded-xl border border-border bg-surface2 px-3 py-2.5 text-sm text-ink outline-none transition-colors focus:border-violet-400/70"
              />
            </label>
            <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
              <label className="block text-xs font-medium text-ink-soft">
                Narração
                <select value={narrationDraft} onChange={(event) => setNarrationDraft(event.target.value as typeof narrationDraft)} className="mt-1.5 min-h-11 w-full rounded-xl border border-border bg-surface2 px-3 py-2.5 text-sm text-ink outline-none focus:border-violet-400/70">
                  <option value="balanced">Equilibrada</option>
                  <option value="cinematic">Cinematográfica</option>
                  <option value="gritty">Realista</option>
                </select>
              </label>
              <label className="block text-xs font-medium text-ink-soft">
                Dificuldade
                <select value={difficultyDraft} onChange={(event) => setDifficultyDraft(event.target.value as typeof difficultyDraft)} className="mt-1.5 min-h-11 w-full rounded-xl border border-border bg-surface2 px-3 py-2.5 text-sm text-ink outline-none focus:border-violet-400/70">
                  <option value="story">Narrativa</option>
                  <option value="balanced">Equilibrada</option>
                  <option value="challenging">Desafiadora</option>
                </select>
              </label>
            </div>
            <div className="mt-4 border-t border-border pt-4">
              <p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-violet-300">Fundação do mundo</p>
              <label className="mt-2 block text-xs font-medium text-ink-soft">Premissa
                <textarea value={premiseDraft} onChange={(event) => setPremiseDraft(event.target.value)} maxLength={5000} rows={3} placeholder="O que torna esta campanha única?" className="mt-1.5 w-full resize-y rounded-xl border border-border bg-surface2 px-3 py-2.5 text-sm leading-5 text-ink outline-none focus:border-violet-400/70" />
              </label>
              <label className="mt-3 block text-xs font-medium text-ink-soft">Cena de abertura
                <textarea value={openingSceneDraft} onChange={(event) => setOpeningSceneDraft(event.target.value)} maxLength={5000} rows={3} placeholder="Onde a história começa e o que está acontecendo?" className="mt-1.5 w-full resize-y rounded-xl border border-border bg-surface2 px-3 py-2.5 text-sm leading-5 text-ink outline-none focus:border-violet-400/70" />
              </label>
              <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
                <label className="block text-xs font-medium text-ink-soft">Local inicial
                  <input value={startingLocationNameDraft} onChange={(event) => setStartingLocationNameDraft(event.target.value)} maxLength={255} required className="mt-1.5 min-h-11 w-full rounded-xl border border-border bg-surface2 px-3 py-2.5 text-sm text-ink outline-none focus:border-violet-400/70" />
                </label>
                <label className="block text-xs font-medium text-ink-soft">Descrição do local
                  <textarea value={startingLocationDescriptionDraft} onChange={(event) => setStartingLocationDescriptionDraft(event.target.value)} maxLength={5000} rows={2} placeholder="O que o personagem percebe?" className="mt-1.5 w-full resize-y rounded-xl border border-border bg-surface2 px-3 py-2.5 text-sm leading-5 text-ink outline-none focus:border-violet-400/70" />
                </label>
              </div>
            </div>
            <button type="button" onClick={() => setWorldBuilderOpen(true)} className="mt-4 flex min-h-11 w-full items-center justify-center gap-2 rounded-xl border border-violet-400/25 bg-violet-500/10 px-3 text-sm font-medium text-violet-100 transition-colors hover:bg-violet-500/20"><Sparkles size={15} /> Construir mundo e NPCs</button>
            {configError ? <p className="mt-3 text-xs text-rose-400">{configError}</p> : null}
            <div className="mt-5 flex justify-end gap-2">
              <button type="button" onClick={() => setConfigOpen(false)} className="min-h-11 cursor-pointer rounded-xl px-3 text-sm text-ink-soft transition-colors hover:bg-hover hover:text-ink">Cancelar</button>
              <button type="submit" disabled={savingConfig || !campaignNameDraft.trim()} className="flex min-h-11 cursor-pointer items-center gap-2 rounded-xl bg-violet-500 px-4 text-sm font-medium text-white transition-colors hover:bg-violet-400 disabled:cursor-not-allowed disabled:opacity-50">
                {savingConfig ? <Loader2 size={15} className="animate-spin" /> : <Check size={15} />}
                Salvar
              </button>
            </div>
          </form>
        </div>
      ) : null}
      {worldBuilderOpen && snapshot ? <ImaginaiWorldBuilder campaignId={snapshot.campaign.id} onClose={() => setWorldBuilderOpen(false)} /> : null}
    </>
  );
}

function ImaginaiSheetHead({ label, onClose }: { label: string; onClose: () => void }) {
  return (
    <div className="imaginai-feature-head">
      <p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-violet-300">{label}</p>
      <button type="button" onClick={onClose} title="Fechar" aria-label="Fechar aba" className="-mr-1.5 flex h-7 w-7 cursor-pointer items-center justify-center rounded-lg text-muted transition-colors hover:bg-hover hover:text-ink">
        <X size={15} />
      </button>
    </div>
  );
}

function ImaginaiEmptyFeature({
  icon: Icon,
  title,
  text,
}: {
  icon: LucideIcon;
  title: string;
  text: string;
}) {
  return (
    <div className="imaginai-feature-scroll flex min-h-52 flex-col items-center justify-center px-4 text-center">
      <span className="mb-3 flex h-11 w-11 items-center justify-center rounded-xl border border-violet-400/20 bg-violet-500/10 text-violet-300">
        <Icon size={20} />
      </span>
      <h3 className="text-sm font-semibold text-ink">{title}</h3>
      <p className="mt-1 max-w-52 text-xs leading-5 text-muted">{text}</p>
    </div>
  );
}

function ImaginaiWorldBuilder({ campaignId, onClose }: { campaignId: string; onClose: () => void }) {
  const [entities, setEntities] = useState<ImaginaiWorldEntity[]>([]);
  const [kind, setKind] = useState("location");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [locationId, setLocationId] = useState("");
  const [visibility, setVisibility] = useState("known");
  const [privateNotes, setPrivateNotes] = useState("");
  const [combatHp, setCombatHp] = useState("10");
  const [combatAc, setCombatAc] = useState("10");
  const [hostile, setHostile] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await api.get<{ entities: ImaginaiWorldEntity[] }>(`/mini-apps/imaginai/campaigns/${campaignId}/world/entities`);
      setEntities(result.entities);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Não foi possível carregar o mundo");
    } finally {
      setLoading(false);
    }
  }, [campaignId]);
  useEffect(() => { void load(); }, [load]);
  const locations = entities.filter((entity) => entity.kind === "location" && entity.active);
  const makeKey = () => {
    const slug = name.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 72) || kind;
    return `manual-${kind}-${slug}-${Date.now().toString(36)}`;
  };
  async function create(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!name.trim()) return;
    setSaving(true);
    setError(null);
    try {
      const state: Record<string, unknown> = visibility === "known" ? { discovered: true } : visibility === "aware" ? { discovery: "aware" } : { hidden: true };
      if (kind === "location") state.map = {};
      if (kind === "npc" || kind === "creature") {
        const hp = Math.max(1, Math.min(9999, Number.parseInt(combatHp, 10) || 10));
        const ac = Math.max(0, Math.min(99, Number.parseInt(combatAc, 10) || 10));
        state.dnd5e = { hp: { current: hp, max: hp }, armor_class: ac, conditions: [] };
        if (hostile) state.hostile = true;
      }
      await api.post(`/mini-apps/imaginai/campaigns/${campaignId}/entities`, {
        kind,
        key: makeKey(),
        name: name.trim(),
        description,
        location_id: kind === "location" || !locationId ? null : locationId,
        state,
        private_notes: privateNotes || null,
      });
      setName(""); setDescription(""); setPrivateNotes(""); setLocationId(""); setHostile(false);
      await load();
    } catch (createError) {
      setError(createError instanceof Error ? createError.message : "Não foi possível criar a entidade");
    } finally {
      setSaving(false);
    }
  }
  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/60 p-3 backdrop-blur-sm" onMouseDown={onClose}>
      <section role="dialog" aria-modal="true" aria-labelledby="imaginai-world-builder-title" onMouseDown={(event) => event.stopPropagation()} className="max-h-[calc(100dvh-1.5rem)] w-full max-w-3xl overflow-y-auto rounded-2xl border border-border bg-surface p-4 shadow-menu">
        <div className="flex items-center justify-between gap-3"><div><p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-violet-300">Imaginai · Autoria</p><h2 id="imaginai-world-builder-title" className="mt-0.5 text-base font-semibold text-ink">Construir mundo</h2><p className="mt-1 text-xs text-muted">Notas privadas alimentam a atuação do NPC, nunca o Codex do personagem.</p></div><button type="button" onClick={onClose} aria-label="Fechar construtor de mundo" className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl text-muted transition-colors hover:bg-hover hover:text-ink"><X size={17} /></button></div>
        <form onSubmit={create} className="mt-4 rounded-xl border border-violet-400/20 bg-violet-500/[.04] p-3"><div className="grid gap-3 sm:grid-cols-2"><label className="text-xs font-medium text-ink-soft">Tipo<select value={kind} onChange={(event) => setKind(event.target.value)} className="imaginai-field"><option value="location">Local</option><option value="npc">NPC</option><option value="creature">Criatura</option><option value="item">Item</option><option value="faction">Facção</option></select></label><label className="text-xs font-medium text-ink-soft">Nome<input value={name} onChange={(event) => setName(event.target.value)} maxLength={255} required placeholder="Nome da entidade" className="imaginai-field" /></label><label className="text-xs font-medium text-ink-soft">Visibilidade inicial<select value={visibility} onChange={(event) => setVisibility(event.target.value)} className="imaginai-field"><option value="known">Conhecida pelo personagem</option><option value="aware">Conhecida, detalhes ocultos</option><option value="hidden">Oculta</option></select></label>{kind !== "location" ? <label className="text-xs font-medium text-ink-soft">Localização<select value={locationId} onChange={(event) => setLocationId(event.target.value)} className="imaginai-field"><option value="">Sem localização definida</option>{locations.map((location) => <option key={location.id} value={location.id}>{location.name}</option>)}</select></label> : null}</div>{kind === "npc" || kind === "creature" ? <div className="mt-3 grid grid-cols-3 gap-3 rounded-xl border border-border/80 bg-surface2/45 p-2.5"><label className="text-[10px] font-medium text-ink-soft">HP<input type="number" min="1" max="9999" value={combatHp} onChange={(event) => setCombatHp(event.target.value)} className="imaginai-field" /></label><label className="text-[10px] font-medium text-ink-soft">CA<input type="number" min="0" max="99" value={combatAc} onChange={(event) => setCombatAc(event.target.value)} className="imaginai-field" /></label><label className="flex min-h-11 cursor-pointer items-center gap-2 self-end rounded-lg px-1 text-[10px] text-ink-soft"><input type="checkbox" checked={hostile} onChange={(event) => setHostile(event.target.checked)} className="accent-violet-400" />Hostil</label></div> : null}<label className="mt-3 block text-xs font-medium text-ink-soft">Descrição pública<textarea value={description} onChange={(event) => setDescription(event.target.value)} maxLength={20000} rows={3} placeholder="O que pode aparecer ao jogador quando descobrir isso?" className="mt-1.5 w-full resize-y rounded-xl border border-border bg-surface2 px-3 py-2.5 text-sm leading-5 text-ink outline-none focus:border-violet-400/70" /></label><label className="mt-3 block text-xs font-medium text-ink-soft">Notas privadas / persona<textarea value={privateNotes} onChange={(event) => setPrivateNotes(event.target.value)} maxLength={100000} rows={3} placeholder="Motivações, segredos, voz e conhecimento exclusivo do NPC." className="mt-1.5 w-full resize-y rounded-xl border border-border bg-surface2 px-3 py-2.5 text-sm leading-5 text-ink outline-none focus:border-violet-400/70" /></label><div className="mt-3 flex justify-end"><button type="submit" disabled={saving || !name.trim()} className="flex min-h-11 items-center gap-2 rounded-xl bg-violet-500 px-4 text-sm font-medium text-white transition-colors hover:bg-violet-400 disabled:cursor-not-allowed disabled:opacity-50">{saving ? <Loader2 size={15} className="animate-spin" /> : <Plus size={15} />}Adicionar ao mundo</button></div></form>
        {error ? <p className="mt-3 text-xs text-rose-400">{error}</p> : null}
        <div className="mt-4"><div className="flex items-center justify-between gap-2"><h3 className="text-sm font-semibold text-ink">Entidades</h3><span className="text-[10px] text-muted">{entities.length}</span></div>{loading ? <ImaginaiFeatureStatus><Loader2 size={17} className="animate-spin" /></ImaginaiFeatureStatus> : entities.length === 0 ? <ImaginaiFeatureStatus>Crie o primeiro local ou NPC da campanha.</ImaginaiFeatureStatus> : <div className="mt-2 grid gap-1.5 sm:grid-cols-2">{entities.map((entity) => <article key={entity.id} className="rounded-xl border border-border bg-surface2/55 p-2.5"><div className="flex items-start gap-2"><span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-violet-500/10 text-violet-300">{entity.kind === "location" ? <MapIcon size={14} /> : entity.kind === "item" ? <Package size={14} /> : <Users size={14} />}</span><div className="min-w-0 flex-1"><p className="truncate text-xs font-medium text-ink">{entity.name}</p><p className="mt-0.5 truncate text-[10px] capitalize text-muted">{entity.kind}{entity.active ? "" : " · inativa"}</p></div></div><p className="mt-2 line-clamp-2 text-[10px] leading-4 text-muted">{entity.description || "Sem descrição pública."}</p>{entity.private_notes ? <p className="mt-1 text-[9px] text-violet-300">Possui notas privadas</p> : null}</article>)}</div>}</div>
      </section>
    </div>
  );
}

function ImaginaiFeatureStatus({ children, error = false }: { children: React.ReactNode; error?: boolean }) {
  return (
    <div className={`flex min-h-36 items-center justify-center px-4 text-center text-xs leading-5 ${error ? "text-rose-400" : "text-muted"}`}>
      {children}
    </div>
  );
}

function ImaginaiJournalPanel({ campaignId }: { campaignId: string }) {
  const [section, setSection] = useState<"notes" | "history">("notes");
  const [entries, setEntries] = useState<ImaginaiJournalEntry[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [tags, setTags] = useState("");
  const [pinned, setPinned] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const selected = entries.find((entry) => entry.id === selectedId) ?? null;

  const loadEntries = useCallback(async (query = "") => {
    setLoading(true);
    setError(null);
    try {
      const result = await api.get<{ entries: ImaginaiJournalEntry[] }>(
        `/mini-apps/imaginai/campaigns/${campaignId}/journal?search=${encodeURIComponent(query)}`,
      );
      setEntries(result.entries);
      setSelectedId((current) => current && result.entries.some((entry) => entry.id === current) ? current : null);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Não foi possível abrir o diário");
    } finally {
      setLoading(false);
    }
  }, [campaignId]);

  useEffect(() => { void loadEntries(); }, [loadEntries]);

  function beginNew() {
    setSelectedId(null);
    setTitle("");
    setContent("");
    setTags("");
    setPinned(false);
    setEditing(true);
    setError(null);
  }

  function beginEdit(entry: ImaginaiJournalEntry) {
    setSelectedId(entry.id);
    setTitle(entry.title);
    setContent(entry.content);
    setTags(entry.tags.join(", "));
    setPinned(entry.pinned);
    setEditing(true);
    setError(null);
  }

  async function saveEntry(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!title.trim()) return;
    setSaving(true);
    setError(null);
    const body = {
      title: title.trim(),
      content,
      tags: tags.split(",").map((tag) => tag.trim()).filter(Boolean),
      pinned,
    };
    try {
      const saved = selectedId
        ? await api.patch<ImaginaiJournalEntry>(`/mini-apps/imaginai/campaigns/${campaignId}/journal/${selectedId}`, body)
        : await api.post<ImaginaiJournalEntry>(`/mini-apps/imaginai/campaigns/${campaignId}/journal`, body);
      await loadEntries(search);
      setSelectedId(saved.id);
      setEditing(false);
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : "Não foi possível salvar a anotação");
    } finally {
      setSaving(false);
    }
  }

  async function deleteEntry(entryId: string) {
    if (deletingId !== entryId) {
      setDeletingId(entryId);
      return;
    }
    setError(null);
    try {
      await api.del<void>(`/mini-apps/imaginai/campaigns/${campaignId}/journal/${entryId}`);
      setDeletingId(null);
      setSelectedId(null);
      setEditing(false);
      await loadEntries(search);
    } catch (deleteError) {
      setError(deleteError instanceof Error ? deleteError.message : "Não foi possível apagar a anotação");
    }
  }

  if (section === "history") {
    return <ImaginaiCampaignHistory campaignId={campaignId} onShowNotes={() => setSection("notes")} />;
  }

  if (editing) {
    return (
      <form onSubmit={saveEntry} className="imaginai-feature-scroll space-y-2" aria-label={selectedId ? "Editar anotação" : "Nova anotação"}>
        <div className="flex items-center justify-between gap-2">
          <h3 className="text-sm font-semibold text-ink">{selectedId ? "Editar anotação" : "Nova anotação"}</h3>
          <button type="button" onClick={() => setEditing(false)} className="imaginai-small-button">Cancelar</button>
        </div>
        <input aria-label="Título da anotação" value={title} onChange={(event) => setTitle(event.target.value)} placeholder="Título" maxLength={180} required className="imaginai-field" />
        <textarea aria-label="Conteúdo da anotação" value={content} onChange={(event) => setContent(event.target.value)} placeholder="Escreva suas anotações…" rows={9} className="imaginai-field resize-y leading-5" />
        <input aria-label="Tags da anotação" value={tags} onChange={(event) => setTags(event.target.value)} placeholder="Tags separadas por vírgula" className="imaginai-field" />
        <label className="flex min-h-11 cursor-pointer items-center gap-2 rounded-lg px-1 text-xs text-ink-soft">
          <input type="checkbox" checked={pinned} onChange={(event) => setPinned(event.target.checked)} className="accent-violet-500" />
          Fixar no topo
        </label>
        {error ? <p className="text-xs text-rose-400">{error}</p> : null}
        <button type="submit" disabled={saving || !title.trim()} className="imaginai-primary-button w-full">
          {saving ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />} Salvar anotação
        </button>
      </form>
    );
  }

  if (selected) {
    return (
      <div className="imaginai-feature-scroll">
        <div className="flex items-start justify-between gap-2">
          <button type="button" onClick={() => { setSelectedId(null); setDeletingId(null); }} className="imaginai-small-button"><ChevronLeft size={14} /> Lista</button>
          <div className="flex gap-1">
            <button type="button" onClick={() => beginEdit(selected)} className="imaginai-icon-button" title="Editar anotação" aria-label="Editar anotação"><Pencil size={14} /></button>
            <button type="button" onClick={() => void deleteEntry(selected.id)} onBlur={() => setDeletingId(null)} className={`imaginai-icon-button ${deletingId === selected.id ? "text-rose-300" : ""}`} title={deletingId === selected.id ? "Clique novamente para apagar" : "Apagar anotação"} aria-label={deletingId === selected.id ? "Confirmar exclusão" : "Apagar anotação"}><Trash2 size={14} /></button>
          </div>
        </div>
        {deletingId === selected.id ? (
          <button type="button" onMouseDown={(event) => event.preventDefault()} onClick={() => void deleteEntry(selected.id)} className="mt-2 flex min-h-11 w-full items-center justify-center gap-1.5 rounded-lg border border-rose-400/30 bg-rose-500/10 px-2 text-xs font-medium text-rose-300">
            <Trash2 size={14} /> Confirmar exclusão
          </button>
        ) : null}
        <div className="mt-3 flex items-center gap-2">
          {selected.pinned ? <Pin size={13} className="shrink-0 text-violet-300" /> : null}
          <h3 className="min-w-0 text-sm font-semibold text-ink">{selected.title}</h3>
        </div>
        <p className="mt-1 text-[10px] text-muted">Atualizada em {new Date(selected.updated_at).toLocaleDateString("pt-BR")}</p>
        <p className="mt-3 whitespace-pre-wrap break-words text-xs leading-5 text-ink-soft">{selected.content || "Sem conteúdo."}</p>
        {selected.tags.length ? <div className="mt-3 flex flex-wrap gap-1">{selected.tags.map((tag) => <span key={tag} className="rounded-full bg-violet-500/10 px-2 py-1 text-[10px] text-violet-200">{tag}</span>)}</div> : null}
        {error ? <p className="mt-3 text-xs text-rose-400">{error}</p> : null}
      </div>
    );
  }

  return (
    <div className="imaginai-feature-scroll">
      <div className="flex items-center justify-between gap-2">
        <div>
          <h3 className="text-sm font-semibold text-ink">Diário</h3>
          <p className="mt-0.5 text-[10px] text-muted">Suas anotações, separadas do que aconteceu no mundo.</p>
        </div>
        <button type="button" onClick={beginNew} className="imaginai-primary-button"><Plus size={14} /> Nova</button>
      </div>
      <div className="mt-2 grid grid-cols-2 gap-1 rounded-xl border border-border bg-surface2/45 p-1" role="tablist" aria-label="Conteúdo do diário">
        <button type="button" role="tab" aria-selected className="min-h-9 rounded-lg bg-violet-500/20 px-2 text-[10px] font-medium text-violet-100">Anotações</button>
        <button type="button" role="tab" aria-selected={false} onClick={() => setSection("history")} className="min-h-9 rounded-lg px-2 text-[10px] text-muted transition-colors hover:bg-hover hover:text-ink">Histórico</button>
      </div>
      <form onSubmit={(event) => { event.preventDefault(); void loadEntries(search); }} className="mt-2 flex gap-1.5">
        <label className="relative min-w-0 flex-1">
          <Search size={13} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-muted" />
          <span className="sr-only">Buscar anotações</span>
          <input aria-label="Buscar anotações" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Buscar anotações" className="imaginai-field imaginai-field-icon" />
        </label>
        <button type="submit" className="imaginai-icon-button" aria-label="Buscar"><Search size={14} /></button>
      </form>
      {loading ? <ImaginaiFeatureStatus><Loader2 size={17} className="animate-spin" /></ImaginaiFeatureStatus> : error ? <ImaginaiFeatureStatus error>{error}</ImaginaiFeatureStatus> : entries.length === 0 ? <ImaginaiFeatureStatus>Seu diário está vazio. Crie uma anotação para registrar pistas, planos ou acontecimentos.</ImaginaiFeatureStatus> : (
        <div className="mt-2 space-y-1">
          {entries.map((entry) => (
            <button key={entry.id} type="button" onClick={() => setSelectedId(entry.id)} className="w-full rounded-xl border border-border bg-surface2/55 p-2.5 text-left transition-colors hover:border-violet-400/30 hover:bg-hover">
              <span className="flex items-center gap-1.5 text-xs font-medium text-ink">{entry.pinned ? <Pin size={12} className="shrink-0 text-violet-300" /> : null}<span className="truncate">{entry.title}</span></span>
              <span className="mt-1 block truncate text-[10px] text-muted">{entry.content || "Sem conteúdo"}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function eventString(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function campaignEventCopy(event: ImaginaiEvent): { title: string; detail: string } {
  const payload = event.payload ?? {};
  const itemName = eventString(payload.item_name);
  const summary = eventString(payload.summary);
  const attackName = eventString(payload.attack_name) ?? eventString(payload.spell_name);
  const result = eventString(payload.result);
  const success = payload.success === true;
  const hasRoll = Array.isArray(payload.rolls);

  if (hasRoll && event.event_type !== "attack_resolved" && event.event_type !== "spell_attack_resolved") {
    const total = typeof payload.total === "number" ? ` · total ${payload.total}` : "";
    return {
      title: result === "success" || success ? "Teste bem-sucedido" : "Teste falhou",
      detail: `${eventString(payload.skill) ?? eventString(payload.ability) ?? "Teste"}${total}`,
    };
  }

  switch (event.event_type) {
    case "item_taken": return { title: "Item recolhido", detail: itemName ? `${itemName} entrou no inventário.` : "Um item foi recolhido." };
    case "item_dropped": return { title: "Item deixado", detail: itemName ? `${itemName} foi deixado no local.` : "Um item foi deixado no local." };
    case "item_equipped": return { title: "Item equipado", detail: itemName ? `${itemName} foi equipado.` : "Um item foi equipado." };
    case "item_unequipped": return { title: "Item guardado", detail: itemName ? `${itemName} deixou de estar equipado.` : "Um item deixou de estar equipado." };
    case "item_used": return { title: "Item usado", detail: itemName ? `${itemName} foi usado.` : "Um item foi usado." };
    case "attack_resolved":
    case "spell_attack_resolved": {
      const damage = typeof payload.damage === "number" ? ` · ${payload.damage} de dano` : "";
      return { title: success ? "Ataque acertou" : "Ataque falhou", detail: `${attackName ?? "Ataque"}${damage}` };
    }
    case "ability_check_resolved": {
      const total = typeof payload.total === "number" ? ` · total ${payload.total}` : "";
      return { title: result === "success" ? "Teste bem-sucedido" : "Teste falhou", detail: `${eventString(payload.skill) ?? eventString(payload.ability) ?? "Teste"}${total}` };
    }
    case "narrative_outcome": return { title: "Consequência registrada", detail: summary ?? "A cena avançou." };
    default: return { title: "Acontecimento", detail: summary ?? "Uma ação foi registrada na campanha." };
  }
}

function ImaginaiCampaignHistory({ campaignId, onShowNotes }: { campaignId: string; onShowNotes: () => void }) {
  const [events, setEvents] = useState<ImaginaiEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadEvents = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await api.get<{ events: ImaginaiEvent[] }>(`/mini-apps/imaginai/campaigns/${campaignId}/events?limit=100`);
      setEvents(result.events);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Não foi possível abrir o histórico");
    } finally {
      setLoading(false);
    }
  }, [campaignId]);

  useEffect(() => { void loadEvents(); }, [loadEvents]);

  return (
    <div className="imaginai-feature-scroll">
      <div className="flex items-center justify-between gap-2">
        <div>
          <h3 className="text-sm font-semibold text-ink">Histórico</h3>
          <p className="mt-0.5 text-[10px] text-muted">Ações confirmadas pelo mundo, em ordem cronológica.</p>
        </div>
        <button type="button" onClick={() => void loadEvents()} disabled={loading} className="imaginai-icon-button" title="Atualizar histórico" aria-label="Atualizar histórico"><RotateCw size={14} className={loading ? "animate-spin" : ""} /></button>
      </div>
      <div className="mt-2 grid grid-cols-2 gap-1 rounded-xl border border-border bg-surface2/45 p-1" role="tablist" aria-label="Conteúdo do diário">
        <button type="button" role="tab" aria-selected={false} onClick={onShowNotes} className="min-h-9 rounded-lg px-2 text-[10px] text-muted transition-colors hover:bg-hover hover:text-ink">Anotações</button>
        <button type="button" role="tab" aria-selected className="min-h-9 rounded-lg bg-violet-500/20 px-2 text-[10px] font-medium text-violet-100">Histórico</button>
      </div>
      {loading ? <ImaginaiFeatureStatus><Loader2 size={17} className="animate-spin" /></ImaginaiFeatureStatus> : error ? <ImaginaiFeatureStatus error>{error}</ImaginaiFeatureStatus> : events.length === 0 ? <ImaginaiFeatureStatus>Ainda não há ações confirmadas. Quando a aventura avançar, os acontecimentos aparecerão aqui.</ImaginaiFeatureStatus> : (
        <ol className="mt-3 space-y-2 border-l border-violet-400/25 pl-3">
          {[...events].reverse().map((event) => {
            const copy = campaignEventCopy(event);
            const at = new Date(event.created_at);
            const when = Number.isNaN(at.getTime()) ? "" : at.toLocaleString("pt-BR", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
            return <li key={event.id} className="relative rounded-xl border border-border bg-surface2/55 p-2.5"><span aria-hidden className="absolute -left-[1.06rem] top-3 h-2 w-2 rounded-full border border-violet-200/60 bg-violet-400" /><div className="flex items-start justify-between gap-2"><h4 className="text-xs font-medium text-ink">{copy.title}</h4><span className="shrink-0 text-[9px] text-muted">Turno {event.world_tick}</span></div><p className="mt-1 text-[10px] leading-4 text-ink-soft">{copy.detail}</p>{when ? <p className="mt-1.5 text-[9px] text-muted">{when}</p> : null}</li>;
          })}
        </ol>
      )}
    </div>
  );
}

function codexDescription(value: ImaginaiCodexResult["description"]): string {
  if (value == null) return "";
  if (typeof value === "string") return value;
  return Object.entries(value).map(([key, item]) => `${key.replaceAll("_", " ")}: ${String(item)}`).join(" · ");
}

function ImaginaiCodexPanel({ campaignId }: { campaignId: string }) {
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState("all");
  const [results, setResults] = useState<ImaginaiCodexResult[]>([]);
  const [selected, setSelected] = useState<ImaginaiCodexResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const searchCodex = useCallback(async (search = query, resultKind = kind) => {
    setLoading(true);
    setError(null);
    try {
      const response = await api.get<{ results: ImaginaiCodexResult[] }>(`/mini-apps/imaginai/campaigns/${campaignId}/codex?search=${encodeURIComponent(search)}&kind=${encodeURIComponent(resultKind)}`);
      setResults(response.results);
      setSelected(null);
    } catch (searchError) {
      setError(searchError instanceof Error ? searchError.message : "Não foi possível consultar o Codex");
    } finally {
      setLoading(false);
    }
  }, [campaignId, kind, query]);

  useEffect(() => { void searchCodex("", "all"); }, [campaignId]); // eslint-disable-line react-hooks/exhaustive-deps

  if (selected) {
    const hidden = selected.knowledge === "aware";
    return (
      <div className="imaginai-feature-scroll">
        <button type="button" onClick={() => setSelected(null)} className="imaginai-small-button"><ChevronLeft size={14} /> Resultados</button>
        <div className="mt-3 flex items-start justify-between gap-2">
          <div className="min-w-0">
            <p className="text-[10px] uppercase tracking-wider text-violet-300">{selected.kind}</p>
            <h3 className="mt-0.5 text-sm font-semibold text-ink">{selected.name}</h3>
          </div>
          {selected.knowledge === "rumor" ? <span className="rounded-full bg-amber-400/10 px-2 py-1 text-[9px] text-amber-300">Rumor</span> : null}
        </div>
        {selected.subject ? <p className="mt-1 text-[10px] text-muted">Sobre {selected.subject}</p> : null}
        {hidden ? (
          <div className="mt-4 rounded-xl border border-dashed border-border bg-surface2/35 p-3">
            <div className="flex items-center gap-2 text-xs text-muted"><LockKeyhole size={14} /> Informação ainda não descoberta</div>
            <div className="imaginai-redaction mt-3 w-full" /><div className="imaginai-redaction mt-2 w-4/5" /><div className="imaginai-redaction mt-2 w-2/3" />
          </div>
        ) : (
          <p className="mt-4 whitespace-pre-wrap break-words text-xs leading-5 text-ink-soft">{codexDescription(selected.description) || "Nenhum detalhe registrado."}</p>
        )}
        {selected.confidence != null ? <p className="mt-3 text-[10px] text-muted">Confiança da fonte: {Math.round(selected.confidence * 100)}%</p> : null}
      </div>
    );
  }

  const categories = [{ key: "all", label: "Tudo" }, { key: "npc", label: "NPCs" }, { key: "location", label: "Locais" }, { key: "item", label: "Itens" }, { key: "lore", label: "Lore" }];
  return (
    <div className="imaginai-feature-scroll">
      <h3 className="text-sm font-semibold text-ink">Codex</h3>
      <p className="mt-0.5 text-[10px] leading-4 text-muted">Somente conhecimento descoberto pelo personagem.</p>
      <form onSubmit={(event) => { event.preventDefault(); void searchCodex(); }} className="mt-2 flex gap-1.5">
        <label className="relative min-w-0 flex-1">
          <Search size={13} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-muted" />
          <span className="sr-only">Buscar no Codex</span>
          <input aria-label="Buscar no Codex" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Buscar no mundo" className="imaginai-field imaginai-field-icon" />
        </label>
        <button type="submit" className="imaginai-icon-button" aria-label="Buscar no Codex"><Search size={14} /></button>
      </form>
      <div className="mt-2 flex gap-1 overflow-x-auto pb-1" aria-label="Categorias do Codex">
        {categories.map((category) => <button key={category.key} type="button" onClick={() => { setKind(category.key); void searchCodex(query, category.key); }} className={`min-h-11 shrink-0 rounded-lg px-2 text-[10px] transition-colors ${kind === category.key ? "bg-violet-500/20 text-violet-100" : "text-muted hover:bg-hover hover:text-ink"}`}>{category.label}</button>)}
      </div>
      {loading ? <ImaginaiFeatureStatus><Loader2 size={17} className="animate-spin" /></ImaginaiFeatureStatus> : error ? <ImaginaiFeatureStatus error>{error}</ImaginaiFeatureStatus> : results.length === 0 ? <ImaginaiFeatureStatus>Nada conhecido corresponde à busca. Segredos do mundo não aparecem antes de serem descobertos.</ImaginaiFeatureStatus> : (
        <div className="space-y-1">
          {results.map((result) => <button key={`${result.result_type}-${result.id}`} type="button" onClick={() => setSelected(result)} className="flex w-full items-center gap-2 rounded-xl border border-border bg-surface2/55 p-2.5 text-left transition-colors hover:border-violet-400/30 hover:bg-hover">
            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-violet-500/10 text-violet-300">{result.knowledge === "aware" ? <LockKeyhole size={14} /> : result.kind === "item" ? <Package size={14} /> : result.kind === "location" ? <MapIcon size={14} /> : <BookOpen size={14} />}</span>
            <span className="min-w-0 flex-1"><span className="block truncate text-xs font-medium text-ink">{result.name}</span><span className="block truncate text-[10px] capitalize text-muted">{result.knowledge === "aware" ? "Detalhes ocultos" : result.kind}</span></span>
            <ChevronRight size={14} className="shrink-0 text-muted" />
          </button>)}
        </div>
      )}
    </div>
  );
}

function spellComponents(value: unknown): string {
  if (Array.isArray(value)) return value.map((item) => String(item)).filter(Boolean).join(", ");
  return typeof value === "string" ? value : "";
}

function ImaginaiSpellsPanel({ campaignId }: { campaignId: string }) {
  const [data, setData] = useState<ImaginaiSpells | null>(null);
  const [selected, setSelected] = useState<ImaginaiSpell | null>(null);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    api.get<ImaginaiSpells>(`/mini-apps/imaginai/campaigns/${campaignId}/spells`)
      .then((value) => { if (!cancelled) setData(value); })
      .catch((loadError: unknown) => { if (!cancelled) setError(loadError instanceof Error ? loadError.message : "Não foi possível abrir as magias"); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [campaignId]);
  if (loading) return <ImaginaiFeatureStatus><Loader2 size={17} className="animate-spin" /></ImaginaiFeatureStatus>;
  if (error || !data) return <ImaginaiFeatureStatus error>{error ?? "Magias indisponíveis"}</ImaginaiFeatureStatus>;
  if (selected) return (
    <div className="imaginai-feature-scroll">
      <button type="button" onClick={() => setSelected(null)} className="imaginai-small-button"><ChevronLeft size={14} /> Grimório</button>
      <div className="mt-3 flex items-start justify-between gap-2"><div className="min-w-0"><p className="text-[10px] uppercase tracking-wider text-violet-300">{selected.level === 0 ? "Truque" : `${selected.level}º nível`}{selected.school ? ` · ${selected.school}` : ""}</p><h3 className="mt-0.5 text-sm font-semibold text-ink">{selected.name}</h3></div><span className={`rounded-full px-2 py-1 text-[9px] ${selected.prepared ? "bg-emerald-400/10 text-emerald-300" : "bg-amber-400/10 text-amber-300"}`}>{selected.prepared ? "Preparada" : "Não preparada"}</span></div>
      <div className="mt-3 grid grid-cols-2 gap-1.5 text-[10px]">{[["Conjuração", selected.casting_time], ["Alcance", selected.range], ["Duração", selected.duration], ["Componentes", spellComponents(selected.components)]].map(([label, value]) => <div key={label} className="rounded-lg border border-border bg-surface2/55 px-2 py-1.5"><span className="block text-[8px] uppercase tracking-wide text-muted">{label}</span><span className="mt-0.5 block truncate text-ink-soft" title={value}>{value || "—"}</span></div>)}</div>
      <div className="mt-2 flex flex-wrap gap-1">{selected.concentration ? <span className="rounded-full bg-violet-500/15 px-2 py-1 text-[9px] text-violet-200">Concentração</span> : null}{selected.ritual ? <span className="rounded-full bg-sky-400/10 px-2 py-1 text-[9px] text-sky-200">Ritual</span> : null}</div>
      <p className="mt-4 whitespace-pre-wrap break-words text-xs leading-5 text-ink-soft">{selected.description || "Os detalhes desta magia ainda não foram registrados na ficha."}</p>
    </div>
  );
  const filtered = data.spells.filter((spell) => spell.name.toLocaleLowerCase("pt-BR").includes(query.trim().toLocaleLowerCase("pt-BR")));
  const groups = new Map<number, ImaginaiSpell[]>();
  for (const spell of filtered) groups.set(spell.level, [...(groups.get(spell.level) ?? []), spell]);
  return (
    <div className="imaginai-feature-scroll">
      <div className="flex items-center justify-between gap-2"><div><h3 className="text-sm font-semibold text-ink">Magias</h3><p className="mt-0.5 text-[10px] text-muted">Apenas magias da ficha autoritativa.</p></div>{data.save_dc > 0 ? <span className="rounded-lg border border-border bg-surface2/55 px-2 py-1 text-[10px] text-ink-soft">CD {data.save_dc}</span> : null}</div>
      {Object.keys(data.slots).length ? <div className="mt-2 grid grid-cols-4 gap-1">{Object.entries(data.slots).map(([level, slot]) => <div key={level} className="rounded-lg border border-border bg-surface2/55 px-1.5 py-1.5 text-center"><span className="block text-[8px] uppercase tracking-wide text-muted">{level}º nível</span><span className="mt-0.5 block font-mono text-[11px] text-ink">{slot.current}/{slot.max}</span></div>)}</div> : null}
      <label className="relative mt-2 block"><Search size={13} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-muted" /><span className="sr-only">Buscar magia</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Buscar magia" className="imaginai-field imaginai-field-icon" /></label>
      {filtered.length === 0 ? <ImaginaiFeatureStatus>O grimório está vazio. Ao definir ou aprender uma magia, ela aparecerá aqui.</ImaginaiFeatureStatus> : <div className="mt-2 space-y-3">{[...groups.entries()].map(([level, spells]) => <section key={level}><p className="mb-1 text-[9px] font-semibold uppercase tracking-[0.12em] text-violet-300">{level === 0 ? "Truques" : `${level}º nível`}</p><div className="space-y-1">{spells.map((spell) => <button key={spell.key} type="button" onClick={() => setSelected(spell)} className="flex w-full items-center gap-2 rounded-xl border border-border bg-surface2/55 p-2.5 text-left transition-colors hover:border-violet-400/30 hover:bg-hover"><span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-violet-500/10 text-violet-300"><Sparkles size={14} /></span><span className="min-w-0 flex-1"><span className="block truncate text-xs font-medium text-ink">{spell.name}</span><span className="block truncate text-[10px] text-muted">{spell.school || "Magia"}{spell.concentration ? " · concentração" : ""}</span></span>{!spell.prepared ? <span title="Não preparada" className="h-2 w-2 shrink-0 rounded-full bg-amber-300" /> : null}<ChevronRight size={14} className="shrink-0 text-muted" /></button>)}</div></section>)}</div>}
    </div>
  );
}

function ImaginaiMapPanel({ campaignId }: { campaignId: string }) {
  const [data, setData] = useState<ImaginaiMap | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    api.get<ImaginaiMap>(`/mini-apps/imaginai/campaigns/${campaignId}/map`)
      .then((value) => { if (!cancelled) { setData(value); setSelectedId(value.locations.find((location) => location.current)?.id ?? value.locations[0]?.id ?? null); } })
      .catch((loadError: unknown) => { if (!cancelled) setError(loadError instanceof Error ? loadError.message : "Não foi possível abrir o mapa"); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [campaignId]);
  if (loading) return <ImaginaiFeatureStatus><Loader2 size={17} className="animate-spin" /></ImaginaiFeatureStatus>;
  if (error || !data) return <ImaginaiFeatureStatus error>{error ?? "Mapa indisponível"}</ImaginaiFeatureStatus>;
  if (!data.locations.length) return <ImaginaiFeatureStatus>Nenhum local foi descoberto ainda.</ImaginaiFeatureStatus>;
  const positions = data.locations.map((location, index) => ({ ...location, x: location.x ?? 14 + ((index * 37) % 72), y: location.y ?? 16 + ((index * 29) % 68) }));
  const byId = new Map(positions.map((location) => [location.id, location]));
  const selected = byId.get(selectedId ?? "") ?? positions[0];
  return (
    <div className="imaginai-feature-scroll"><div className="flex items-center justify-between gap-2"><div><h3 className="text-sm font-semibold text-ink">Mapa</h3><p className="mt-0.5 text-[10px] text-muted">Somente locais e caminhos já descobertos.</p></div><span className="text-[10px] text-muted">{positions.length} local{positions.length === 1 ? "" : "is"}</span></div>
      <div className="relative mt-3 h-64 overflow-hidden rounded-xl border border-violet-400/20 bg-[radial-gradient(circle_at_50%_50%,rgba(139,92,246,0.14),transparent_65%),linear-gradient(135deg,rgba(20,20,28,0.9),rgba(12,12,17,0.96))]" aria-label="Mapa dos locais descobertos"><svg className="pointer-events-none absolute inset-0 h-full w-full" aria-hidden="true">{data.routes.map((route) => { const from = byId.get(route.from); const to = byId.get(route.to); return from && to ? <line key={`${route.from}-${route.to}`} x1={`${from.x}%`} y1={`${from.y}%`} x2={`${to.x}%`} y2={`${to.y}%`} stroke="rgba(167,139,250,.48)" strokeWidth="1.5" strokeDasharray="4 3" /> : null; })}</svg>{positions.map((location) => <button key={location.id} type="button" onClick={() => setSelectedId(location.id)} title={location.name} style={{ left: `${location.x}%`, top: `${location.y}%` }} className={`absolute -translate-x-1/2 -translate-y-1/2 rounded-full border p-1.5 shadow-lg transition-transform hover:scale-110 focus-visible:outline focus-visible:outline-2 focus-visible:outline-violet-300 ${location.current ? "border-emerald-300 bg-emerald-400/20 text-emerald-200" : selected.id === location.id ? "border-violet-200 bg-violet-500/35 text-white" : "border-violet-400/40 bg-surface text-violet-200"}`}><MapIcon size={14} /><span className="sr-only">{location.name}</span></button>)}</div>
      <div className="mt-2 rounded-xl border border-border bg-surface2/55 p-2.5"><div className="flex items-center gap-2"><MapIcon size={14} className={selected.current ? "text-emerald-300" : "text-violet-300"} /><h4 className="min-w-0 truncate text-xs font-medium text-ink">{selected.name}</h4>{selected.current ? <span className="ml-auto shrink-0 text-[9px] text-emerald-300">Você está aqui</span> : null}</div><p className="mt-1 line-clamp-3 text-[10px] leading-4 text-muted">{selected.description || "Local conhecido, sem descrição registrada."}</p></div>
    </div>
  );
}

function ImaginaiInventoryPanel({ campaignId, system }: { campaignId: string; system: ImaginaiSystemDefinition | null }) {
  const [inventory, setInventory] = useState<ImaginaiInventory | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    api.get<ImaginaiInventory>(`/mini-apps/imaginai/campaigns/${campaignId}/inventory`)
      .then((value) => { if (!cancelled) setInventory(value); })
      .catch((loadError: unknown) => { if (!cancelled) setError(loadError instanceof Error ? loadError.message : "Não foi possível abrir o inventário"); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [campaignId]);
  if (loading) return <ImaginaiFeatureStatus><Loader2 size={17} className="animate-spin" /></ImaginaiFeatureStatus>;
  if (error || !inventory) return <ImaginaiFeatureStatus error>{error ?? "Inventário indisponível"}</ImaginaiFeatureStatus>;
  const currencies = system?.inventory.currencies ?? Object.keys(inventory.currencies).map((key) => ({ key, label: key.toUpperCase(), name: key, weight: 0 }));
  return (
    <div className="imaginai-feature-scroll">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-ink">Inventário</h3>
        {inventory.weight?.enabled ? <span title={inventory.weight.currency_enabled ? `${inventory.weight.currencies.toLocaleString("pt-BR")} ${inventory.weight.unit} em moedas` : "Moedas sem peso neste sistema"} className="text-[10px] text-muted">{inventory.weight.total.toLocaleString("pt-BR")} {inventory.weight.unit}</span> : null}
      </div>
      <div className="mt-2 grid grid-cols-5 gap-1" aria-label="Moedas">
        {currencies.map((currency) => <div key={currency.key} title={`${currency.name}${inventory.weight?.currency_enabled ? ` · ${currency.weight} ${inventory.weight.unit} cada` : ""}`} className="rounded-lg border border-border bg-surface2/55 px-1 py-1.5 text-center"><span className="block text-[9px] font-semibold text-amber-300">{currency.label}</span><span className="mt-0.5 block font-mono text-[10px] text-ink">{inventory.currencies[currency.key] ?? 0}</span></div>)}
      </div>
      {inventory.items.length === 0 ? <ImaginaiFeatureStatus>Nenhum item carregado. O inventário reflete apenas itens que pertencem ao personagem.</ImaginaiFeatureStatus> : <div className="mt-2 space-y-1">
        {inventory.items.map((item) => <article key={item.id} className="rounded-xl border border-border bg-surface2/55 p-2.5">
          <div className="flex items-start justify-between gap-2"><div className="min-w-0"><h4 className="truncate text-xs font-medium text-ink">{item.name}</h4><p className="mt-0.5 truncate text-[10px] text-muted">{item.equipped ? `Equipado${item.slot ? ` · ${item.slot.replaceAll("_", " ")}` : ""}` : item.container ? `Em ${item.container}` : "Carregado"}</p></div><span className="shrink-0 font-mono text-[10px] text-ink-soft">×{item.quantity}</span></div>
          {(item.description || (system?.inventory.weight.supported && item.weight > 0)) ? <div className="mt-2 flex items-end justify-between gap-2"><p className="line-clamp-2 text-[10px] leading-4 text-muted">{item.description}</p>{system?.inventory.weight.supported && item.weight > 0 ? <span className="shrink-0 text-[9px] text-muted">{item.weight} {system.inventory.weight.unit}</span> : null}</div> : null}
        </article>)}
      </div>}
    </div>
  );
}

function numericState(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function abilityScore(value: unknown): number {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (value && typeof value === "object") {
    return numericState((value as Record<string, unknown>).score, 10);
  }
  return 10;
}

function signed(value: number): string {
  return value >= 0 ? `+${value}` : String(value);
}

function ImaginaiSheetPanel({
  campaignId,
  character,
  system,
  error,
  onSnapshotChange,
}: {
  campaignId: string | null;
  character: ImaginaiEntity | null;
  system: ImaginaiSystemDefinition | null;
  error: string | null;
  onSnapshotChange: (snapshot: ImaginaiSnapshot) => void;
}) {
  const [editing, setEditing] = useState(false);
  if (error) return <ImaginaiFeatureStatus error>{error}</ImaginaiFeatureStatus>;
  if (!character || !system) return <ImaginaiFeatureStatus><Loader2 size={17} className="animate-spin" /></ImaginaiFeatureStatus>;
  const dnd = (character.state.dnd5e ?? character.state) as Record<string, unknown>;
  const attributes = (dnd.attributes ?? {}) as Record<string, unknown>;
  const skills = (dnd.skills ?? {}) as Record<string, unknown>;
  const saves = (dnd.saving_throws ?? {}) as Record<string, unknown>;
  const proficiency = numericState(dnd.proficiency_bonus, 2);
  return (
    <div className="imaginai-feature-scroll">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-ink">Ficha</h3>
        <button type="button" onClick={() => setEditing(true)} className="flex min-h-10 items-center gap-1.5 rounded-lg px-2 text-[10px] font-medium text-violet-200 transition-colors hover:bg-violet-500/15 hover:text-white"><Pencil size={13} />Editar</button>
      </div>
      <div className="mt-2 grid grid-cols-3 gap-1">
        {system.sheet.summary.map((field) => <div key={field.key} className="rounded-lg border border-border bg-surface2/55 px-1.5 py-1.5 text-center"><span className="block truncate text-[8px] uppercase tracking-wide text-muted">{field.label}</span><span className="mt-0.5 block font-mono text-[11px] text-ink">{numericState(dnd[field.key], field.key === "speed" ? 30 : field.key === "proficiency_bonus" ? 2 : 0)}</span></div>)}
      </div>
      <div className="mt-2 space-y-1.5">
        {system.sheet.attributes.map((attribute) => {
          const attributeState = attributes[attribute.key];
          const score = abilityScore(attributeState);
          const modifier = Math.floor((score - 10) / 2);
          const saveState = saves[attribute.key] ?? (
            attributeState && typeof attributeState === "object"
              ? (attributeState as Record<string, unknown>).save_proficient
              : undefined
          );
          const saveProficient = saveState === true || (typeof saveState === "object" && saveState !== null && Boolean((saveState as Record<string, unknown>).proficient));
          return <section key={attribute.key} className="imaginai-ability-row">
            <div className="imaginai-ability-score" title={attribute.label}><span>{attribute.short}</span><strong>{score}</strong><em>{signed(modifier)}</em></div>
            <div className="min-w-0 flex-1 py-2 pl-3 pr-2.5">
              <div className="flex items-center justify-between gap-2 border-b border-border/70 pb-1.5"><span className="truncate text-[10px] font-medium text-ink-soft">Salvaguarda</span><span className="flex shrink-0 items-center gap-1.5 font-mono text-[10px] text-ink"><i className={`h-1.5 w-1.5 shrink-0 rounded-full ${saveProficient ? "bg-violet-300" : "border border-muted"}`} />{signed(modifier + (saveProficient ? proficiency : 0))}</span></div>
              <div className="mt-1.5 space-y-1">{attribute.skills.length ? attribute.skills.map((skillKey) => {
                const skillState = skills[skillKey];
                const explicit = typeof skillState === "number" ? skillState : typeof skillState === "object" && skillState !== null ? (skillState as Record<string, unknown>).value : undefined;
                const rank = skillState === true ? 1 : typeof skillState === "object" && skillState !== null ? numericState((skillState as Record<string, unknown>).proficiency, Boolean((skillState as Record<string, unknown>).proficient) ? 1 : 0) : 0;
                const value = typeof explicit === "number" ? explicit : modifier + proficiency * Math.min(2, rank);
                return <div key={skillKey} className="flex items-center justify-between gap-2 text-[10px] leading-4"><span className="truncate text-muted">{system.sheet.skills[skillKey] ?? skillKey}</span><span className="flex shrink-0 items-center gap-1.5 font-mono text-ink-soft"><i className={`h-1.5 w-1.5 shrink-0 rounded-full ${rank >= 2 ? "ring-1 ring-violet-300 bg-violet-300" : rank === 1 ? "bg-violet-300" : "border border-muted"}`} />{signed(value)}</span></div>;
              }) : <span className="text-[10px] leading-4 text-muted">Sem perícias associadas</span>}</div>
            </div>
          </section>;
        })}
      </div>
      <p className="mt-2 text-[9px] leading-4 text-muted">Ponto cheio: proficiente · aro: especialização. Os valores vêm do estado autoritativo da campanha.</p>
      {editing && campaignId ? <ImaginaiCharacterEditor campaignId={campaignId} character={character} onSnapshotChange={onSnapshotChange} onClose={() => setEditing(false)} /> : null}
    </div>
  );
}

const DND5E_CLASSES = ["Bárbaro", "Bardo", "Bruxo", "Clérigo", "Druida", "Feiticeiro", "Guerreiro", "Ladino", "Mago", "Monge", "Paladino", "Patrulheiro"];
const DND5E_ABILITY_FIELDS = [
  ["strength", "FOR"], ["dexterity", "DES"], ["constitution", "CON"],
  ["intelligence", "INT"], ["wisdom", "SAB"], ["charisma", "CAR"],
] as const;

function ImaginaiCharacterEditor({
  campaignId,
  character,
  onSnapshotChange,
  onClose,
}: {
  campaignId: string;
  character: ImaginaiEntity;
  onSnapshotChange: (snapshot: ImaginaiSnapshot) => void;
  onClose: () => void;
}) {
  const dnd = (character.state.dnd5e ?? character.state) as Record<string, unknown>;
  const hp = (dnd.hp ?? {}) as Record<string, unknown>;
  const sourceAttributes = (dnd.attributes ?? {}) as Record<string, unknown>;
  const [name, setName] = useState(character.name);
  const [characterClass, setCharacterClass] = useState(typeof dnd.class === "string" && dnd.class !== "Classe" ? dnd.class : "");
  const [level, setLevel] = useState(String(numericState(dnd.level, 1)));
  const [ancestry, setAncestry] = useState(typeof dnd.ancestry === "string" ? dnd.ancestry : "");
  const [background, setBackground] = useState(typeof dnd.background === "string" ? dnd.background : "");
  const [alignment, setAlignment] = useState(typeof dnd.alignment === "string" ? dnd.alignment : "");
  const [hpCurrent, setHpCurrent] = useState(String(numericState(hp.current, 10)));
  const [hpMax, setHpMax] = useState(String(numericState(hp.max, 10)));
  const [armorClass, setArmorClass] = useState(String(numericState(dnd.armor_class, 10)));
  const [speed, setSpeed] = useState(String(numericState(dnd.speed, 30)));
  const [attributes, setAttributes] = useState<Record<string, string>>(() => Object.fromEntries(
    DND5E_ABILITY_FIELDS.map(([key]) => [key, String(abilityScore(sourceAttributes[key]))]),
  ));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const numeric = (value: string, fallback: number) => {
    const parsed = Number.parseInt(value, 10);
    return Number.isFinite(parsed) ? parsed : fallback;
  };
  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!name.trim() || !characterClass) return;
    setSaving(true);
    setError(null);
    try {
      const snapshot = await api.patch<ImaginaiSnapshot>(`/mini-apps/imaginai/campaigns/${campaignId}/character`, {
        name: name.trim(),
        character_class: characterClass,
        level: numeric(level, 1),
        ancestry: ancestry.trim(),
        background: background.trim(),
        alignment: alignment.trim(),
        hp_current: numeric(hpCurrent, 10),
        hp_max: numeric(hpMax, 10),
        armor_class: numeric(armorClass, 10),
        speed: numeric(speed, 30),
        attributes: Object.fromEntries(DND5E_ABILITY_FIELDS.map(([key]) => [key, numeric(attributes[key] ?? "10", 10)])),
      });
      onSnapshotChange(snapshot);
      onClose();
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : "Não foi possível salvar a ficha");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/55 p-3 backdrop-blur-sm" onMouseDown={onClose}>
      <form role="dialog" aria-modal="true" aria-labelledby="imaginai-character-editor-title" onSubmit={submit} onMouseDown={(event) => event.stopPropagation()} className="max-h-[calc(100dvh-1.5rem)] w-full max-w-lg overflow-y-auto rounded-2xl border border-border bg-surface p-4 shadow-menu">
        <div className="flex items-center justify-between gap-3"><div><p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-violet-300">D&D 5e</p><h2 id="imaginai-character-editor-title" className="mt-0.5 text-base font-semibold text-ink">Configurar personagem</h2></div><button type="button" onClick={onClose} aria-label="Fechar editor de personagem" className="flex h-10 w-10 items-center justify-center rounded-xl text-muted transition-colors hover:bg-hover hover:text-ink"><X size={17} /></button></div>
        <div className="mt-4 grid gap-3 sm:grid-cols-2">
          <label className="text-xs font-medium text-ink-soft sm:col-span-2">Nome<input autoFocus required maxLength={255} value={name} onChange={(event) => setName(event.target.value)} className="imaginai-field" /></label>
          <label className="text-xs font-medium text-ink-soft">Classe<select required value={characterClass} onChange={(event) => setCharacterClass(event.target.value)} className="imaginai-field"><option value="" disabled>Selecione uma classe</option>{DND5E_CLASSES.map((item) => <option key={item} value={item}>{item}</option>)}</select></label>
          <label className="text-xs font-medium text-ink-soft">Nível<input required type="number" min="1" max="20" value={level} onChange={(event) => setLevel(event.target.value)} className="imaginai-field" /></label>
          <label className="text-xs font-medium text-ink-soft">Ancestralidade<input maxLength={120} value={ancestry} onChange={(event) => setAncestry(event.target.value)} placeholder="Ex.: Elfo" className="imaginai-field" /></label>
          <label className="text-xs font-medium text-ink-soft">Antecedente<input maxLength={120} value={background} onChange={(event) => setBackground(event.target.value)} placeholder="Ex.: Acólito" className="imaginai-field" /></label>
          <label className="text-xs font-medium text-ink-soft sm:col-span-2">Tendência<input maxLength={80} value={alignment} onChange={(event) => setAlignment(event.target.value)} placeholder="Ex.: Neutro e Bom" className="imaginai-field" /></label>
        </div>
        <div className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-4"><label className="text-[10px] font-medium text-ink-soft">HP atual<input type="number" min="0" max="9999" value={hpCurrent} onChange={(event) => setHpCurrent(event.target.value)} className="imaginai-field" /></label><label className="text-[10px] font-medium text-ink-soft">HP máximo<input type="number" min="1" max="9999" value={hpMax} onChange={(event) => setHpMax(event.target.value)} className="imaginai-field" /></label><label className="text-[10px] font-medium text-ink-soft">CA<input type="number" min="0" max="99" value={armorClass} onChange={(event) => setArmorClass(event.target.value)} className="imaginai-field" /></label><label className="text-[10px] font-medium text-ink-soft">Deslocamento<input type="number" min="0" max="999" value={speed} onChange={(event) => setSpeed(event.target.value)} className="imaginai-field" /></label></div>
        <fieldset className="mt-4"><legend className="text-xs font-medium text-ink-soft">Atributos</legend><div className="mt-2 grid grid-cols-3 gap-2 sm:grid-cols-6">{DND5E_ABILITY_FIELDS.map(([key, label]) => <label key={key} className="rounded-xl border border-border bg-surface2/55 px-2 py-1.5 text-center text-[9px] font-semibold text-muted">{label}<input aria-label={label} type="number" min="1" max="30" value={attributes[key] ?? "10"} onChange={(event) => setAttributes((current) => ({ ...current, [key]: event.target.value }))} className="mt-1 block w-full bg-transparent text-center font-mono text-sm text-ink outline-none" /></label>)}</div></fieldset>
        <p className="mt-3 text-[10px] leading-4 text-muted">Proficiência, iniciativa e percepção passiva são calculadas pela ficha. Itens, moedas, vida durante a aventura e espaços de magia continuam sob validação do mundo.</p>
        {error ? <p className="mt-3 text-xs text-rose-400">{error}</p> : null}
        <div className="mt-5 flex justify-end gap-2"><button type="button" onClick={onClose} className="min-h-11 rounded-xl px-3 text-sm text-ink-soft transition-colors hover:bg-hover hover:text-ink">Cancelar</button><button type="submit" disabled={saving || !name.trim() || !characterClass} className="flex min-h-11 items-center gap-2 rounded-xl bg-violet-500 px-4 text-sm font-medium text-white transition-colors hover:bg-violet-400 disabled:cursor-not-allowed disabled:opacity-50">{saving ? <Loader2 size={15} className="animate-spin" /> : <Check size={15} />}Salvar ficha</button></div>
      </form>
    </div>
  );
}

function MessageBubble({
  role,
  content,
  onSpeak,
  speaking = false,
  onDelete,
  time,
  streaming = false,
  name,
  nameColor = null,
  bare = false,
  reasoning,
  reasoningLive = false,
  toolEvents,
  toolsLive = false,
  status = null,
  footer,
}: {
  role: string;
  content: string;
  onSpeak?: () => void;
  speaking?: boolean;
  onDelete?: () => void;
  time?: string;
  streaming?: boolean;
  name?: string;
  nameColor?: string | null;
  bare?: boolean;
  reasoning?: Message["reasoning"];
  reasoningLive?: boolean;
  toolEvents?: ToolEvent[];
  /** geração em andamento: mostra o painel de tools já aberto + spinner na tool ativa */
  toolsLive?: boolean;
  /** linha de atividade ("o que a IA está fazendo agora") durante a geração */
  status?: string | null;
  footer?: React.ReactNode;
}) {
  const isUser = role === "user";
  const usedTools = !!toolEvents?.length;
  if (isUser) {
    return (
      <div className="mx-auto flex max-w-3xl justify-end">
        <div className="group relative max-w-[85%]">
          <div className="whitespace-pre-wrap rounded-2xl rounded-br-md bg-surface2 px-4 py-2.5 text-[15px] leading-7 text-ink [overflow-wrap:anywhere]">
            {content}
          </div>
          {(time || onDelete) && (
            <div className="mt-1 flex items-center justify-end gap-1.5 pr-1 opacity-0 transition-opacity duration-150 group-hover:opacity-100">
              {time && <span className="text-[11px] text-muted">{fmtTime(time)}</span>}
              {onDelete && (
                <button title="Excluir" onClick={onDelete} className="rounded p-1 text-muted transition-colors hover:bg-hover hover:text-red-300">
                  <Trash2 size={13} />
                </button>
              )}
            </div>
          )}
        </div>
      </div>
    );
  }
  return (
    <div className={bare ? "w-full" : "mx-auto max-w-3xl"}>
      <div className="group relative">
        {name && (
          <p className="mb-1.5 flex items-center gap-1.5 text-lg font-semibold tracking-tight text-ink" style={nameColor ? { color: nameColor } : undefined}>
            {name}
          </p>
        )}
        {status && (
          <p className="mb-2 flex items-center gap-2 text-sm text-ink-soft">
            <Loader2 size={14} className="shrink-0 animate-spin text-accent-hover" />
            <span>{status}</span>
          </p>
        )}
        {(reasoning?.text || reasoning?.steps?.length || usedTools) && (
          <ReasoningBlock text={reasoning?.text ?? ""} seconds={reasoning?.seconds} steps={reasoning?.steps} tools={toolEvents} live={reasoningLive || toolsLive} />
        )}
        {(content || !reasoning) && (
          <Markdown content={content} fast={streaming} className={streaming ? "stream-caret" : ""} />
        )}
        {footer}
        {onSpeak && (
          <button
            onClick={() => onSpeak()}
            title={speaking ? "Parar leitura" : "Ler em voz alta"}
            aria-pressed={speaking || undefined}
            className={`mt-1 flex items-center gap-1 text-xs transition-opacity hover:text-ink ${speaking ? "text-accent-hover opacity-100" : "text-muted opacity-0 group-hover:opacity-100"}`}
          >
            {speaking ? <Square size={12} fill="currentColor" /> : <Volume2 size={13} />}
            {speaking ? "Parar" : "Ler"}
          </button>
        )}
      </div>
    </div>
  );
}

/* Navegador de mensagens: trilha de "dots" na borda direita (uma por mensagem
 * do usuário) para pular por toda a conversa. No hover abre uma busca das suas
 * mensagens, com a mesma aparência do seletor de modelos. */
function MessageNavigator({ messages, onJump }: { messages: Message[]; onJump: (id: string) => void }) {
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const hideTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const userMsgs = useMemo(() => messages.filter((m) => m.role === "user"), [messages]);
  useEffect(() => () => { if (hideTimer.current) clearTimeout(hideTimer.current); }, []);
  if (userMsgs.length < 2) return null;
  // abre no hover; fecha com um pequeno atraso para dar tempo de mover o mouse
  // dos dots até a caixa de busca (evita que ela suma no caminho).
  const show = () => { if (hideTimer.current) clearTimeout(hideTimer.current); setOpen(true); };
  const hide = () => { if (hideTimer.current) clearTimeout(hideTimer.current); hideTimer.current = setTimeout(() => setOpen(false), 260); };
  const ql = q.trim().toLowerCase();
  const filtered = ql ? userMsgs.filter((m) => m.content.toLowerCase().includes(ql)) : userMsgs;
  const jump = (id: string) => { onJump(id); };
  return (
    <div
      onMouseEnter={show}
      onMouseLeave={hide}
      className="absolute right-4 top-1/2 z-30 hidden -translate-y-1/2 items-center gap-2.5 md:flex"
    >
      {/* painel de busca — à esquerda da trilha, dentro do mesmo container de hover */}
      {open && (
        <div className="animate-pop w-72 overflow-hidden rounded-2xl border border-border bg-surface shadow-menu">
          <div className="flex items-center gap-2 border-b border-border px-3 py-2.5">
            <Search size={16} className="text-muted" />
            <input
              autoFocus
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Pesquisar suas mensagens"
              className="w-full bg-transparent text-sm text-ink outline-none placeholder:text-muted"
            />
          </div>
          <div className="max-h-72 overflow-y-auto p-1.5">
            {filtered.map((m) => (
              <button
                key={m.id}
                onClick={() => jump(m.id)}
                className="flex w-full items-baseline gap-1.5 rounded-lg px-2.5 py-2 text-left transition-colors hover:bg-hover"
              >
                <span className="shrink-0 text-xs text-muted">#{userMsgs.indexOf(m) + 1}</span>
                <span className="truncate text-sm text-ink-soft">{m.content}</span>
              </button>
            ))}
            {filtered.length === 0 && <p className="px-3 py-4 text-center text-sm text-muted">Nada encontrado.</p>}
          </div>
        </div>
      )}
      {/* trilha de dots — fixa nos 5 últimos (a busca no hover mostra tudo) */}
      <div className="flex max-h-[70vh] flex-col items-center gap-0.5 overflow-hidden rounded-full border border-border/60 bg-surface/80 px-1 py-2 backdrop-blur">
        {userMsgs.slice(-5).map((m) => (
          <button
            key={m.id}
            onClick={() => jump(m.id)}
            title={m.content.slice(0, 80)}
            className="group/dot flex h-3.5 w-4 items-center justify-center"
          >
            <span className="h-1.5 w-1.5 rounded-full bg-border transition-all group-hover/dot:h-2.5 group-hover/dot:w-2.5 group-hover/dot:bg-accent-hover" />
          </button>
        ))}
      </div>
    </div>
  );
}

/* indicador de "pensando" enquanto a primeira palavra não chega */
function Thinking() {
  return (
    <div className="mx-auto max-w-3xl">
      <div className="flex items-center gap-1 py-1">
        <span className="typing-dot" />
        <span className="typing-dot" />
        <span className="typing-dot" />
      </div>
    </div>
  );
}

/** Divisor de compactação: marca visualmente onde o contexto foi compactado.
 *  As mensagens acima continuam visíveis, mas estão fora do contexto da IA — o
 *  resumo em si fica no nó do Grafo de contexto (clique para abrir). */
function CompactionDivider({ onOpen }: { onOpen: () => void }) {
  return (
    <div className="mx-auto flex max-w-3xl items-center gap-3 py-1.5 text-muted">
      <span className="h-px flex-1 bg-gradient-to-r from-transparent via-border to-border" />
      <button
        onClick={onOpen}
        title="Ver resumo no Grafo de contexto"
        className="flex shrink-0 items-center gap-1.5 rounded-full border border-border bg-surface px-3 py-1 text-xs text-ink-soft transition-colors hover:border-accent/40 hover:text-ink"
      >
        <Scissors size={12} className="text-accent-hover" />
        Contexto compactado
        <GitBranch size={12} className="opacity-60" />
      </button>
      <span className="h-px flex-1 bg-gradient-to-l from-transparent via-border to-border" />
    </div>
  );
}

/** Chip "Guarda de saída acionado" — mostrado enquanto o servidor refaz a resposta. */
function GuardRetry({ note }: { note: { name: string; action: string; fallback_model?: string | null } }) {
  const label = note.action === "fallback_model"
    ? `trocando para ${note.fallback_model || "o modelo de fallback"}`
    : "reforçando e refazendo";
  return (
    <div className="mx-auto flex max-w-3xl items-center gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-1.5 text-xs text-amber-300">
      <ShieldAlert size={14} className="shrink-0" />
      <span className="truncate"><b className="font-medium">{note.name}</b> — {label}…</span>
    </div>
  );
}

/** Indicador "Gerando imagem…" (GenImage Router em andamento). */
function GeneratingImage() {
  return (
    <div className="mt-2 inline-flex items-center gap-2 rounded-full border border-border bg-surface px-3 py-1.5 text-sm text-ink-soft">
      <ImageIcon size={14} className="animate-pulse text-accent-hover" />
      Gerando imagem…
    </div>
  );
}

/** Compartilhar conversa: cria/mostra o link público read-only e permite revogar. */
function ShareModal({
  chat, onClose, onChange,
}: {
  chat: Chat;
  onClose: () => void;
  onChange: (publicId: string | null) => void;
}) {
  const [publicId, setPublicId] = useState<string | null>(chat.public_id ?? null);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const url = publicId ? `${typeof window !== "undefined" ? window.location.origin : ""}/shared/${publicId}` : "";

  // ao abrir sem link, cria um automaticamente
  useEffect(() => {
    if (publicId) return;
    setBusy(true);
    api.post<{ public_id: string }>(`/chats/${chat.id}/share`)
      .then((r) => { setPublicId(r.public_id); onChange(r.public_id); })
      .catch(() => {})
      .finally(() => setBusy(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function copy() {
    try { await copyText(url); setCopied(true); setTimeout(() => setCopied(false), 1500); } catch {}
  }
  async function revoke() {
    setBusy(true);
    try { await api.del(`/chats/${chat.id}/share`); setPublicId(null); onChange(null); onClose(); } finally { setBusy(false); }
  }

  return (
    <div className="fixed inset-0 z-[80] flex items-center justify-center bg-black/60 p-4" onClick={onClose}>
      <div onClick={(e) => e.stopPropagation()} className="w-full max-w-md rounded-2xl border border-border bg-surface p-5 shadow-2xl">
        <div className="mb-3 flex items-center justify-between">
          <span className="flex items-center gap-2 text-sm font-semibold text-ink"><Share2 size={16} className="text-accent-hover" /> Compartilhar conversa</span>
          <button onClick={onClose} className="rounded-lg p-1 text-muted hover:bg-hover hover:text-ink"><X size={16} /></button>
        </div>
        <p className="mb-3 text-xs text-muted">
          Qualquer pessoa com o link vê esta conversa em modo leitura (título e mensagens). Novas mensagens aparecem quando a pessoa recarrega. Revogue quando quiser.
        </p>
        <div className="flex items-center gap-2 rounded-xl border border-border bg-surface2 px-3 py-2">
          <Link2 size={14} className="shrink-0 text-muted" />
          <input readOnly value={busy && !url ? "Gerando link…" : url} className="min-w-0 flex-1 bg-transparent text-xs text-ink outline-none" />
          <button onClick={copy} disabled={!url} title="Copiar" className="shrink-0 rounded-lg p-1.5 text-muted transition-colors hover:bg-hover hover:text-ink disabled:opacity-40">
            {copied ? <Check size={14} className="text-emerald-500" /> : <Copy size={14} />}
          </button>
        </div>
        <div className="mt-4 flex justify-end">
          <button onClick={revoke} disabled={busy || !publicId} className="rounded-lg px-3 py-1.5 text-sm text-rose-400 transition-colors hover:bg-hover disabled:opacity-40">
            Parar de compartilhar
          </button>
        </div>
      </div>
    </div>
  );
}

/** Indicador "Consultando base de conhecimento…" (RAG em andamento). */
function ConsultingKnowledge() {
  return (
    <div className="mt-2 inline-flex items-center gap-2 rounded-full border border-border bg-surface px-3 py-1.5 text-sm text-ink-soft">
      <BookOpen size={14} className="animate-pulse text-accent-hover" />
      Consultando base de conhecimento…
    </div>
  );
}

/** Indicador "Transcrevendo áudio…" (Audio Router em andamento). */
function TranscribingAudio() {
  return (
    <div className="mt-2 inline-flex items-center gap-2 rounded-full border border-border bg-surface px-3 py-1.5 text-sm text-ink-soft">
      <Volume2 size={14} className="animate-pulse text-accent-hover" />
      Transcrevendo áudio…
    </div>
  );
}
