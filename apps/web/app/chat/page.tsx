"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowDown, ArrowUpRight, Bell, BookOpen, Check, Code2, Copy, Ear, FlaskConical, GitBranch, Image as ImageIcon, Link2, Loader2, Menu, MessageSquareDashed, Mic, Search, Scissors, Share2, ShieldAlert, SlidersHorizontal, Sparkles, Square, Trash2, Users, Volume2, Wrench, X } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { copyText } from "@/lib/clipboard";
import { streamContinue, streamEphemeral, streamMessage, streamRegenerate, streamRoundtable } from "@/lib/sse";
import { speak, startBrowserDictation, startRecording, stopSpeaking, transcribe } from "@/lib/voice";
import { captureUtterance } from "@/lib/voiceSession";
import { startWakeWord, transcribeWhisper, type WakeHandle } from "@/lib/wakeword";
import { onVoiceActivate } from "@/lib/desktop";
import { browserNotify, playChime, requestNotifPermission } from "@/lib/notify";
import { downloadJSON, downloadPDF, downloadTXT } from "@/lib/download";
import { pickSuggestions, type Suggestion } from "@/lib/suggestions";
import type { AskSpec, Attachment, Chat, ChatArtifact, CodespaceProject, Folder, KnowledgeRef, ListenConfig, Message, Model, ModelConfig, Prompt, RoundtableConfig, RoundtableParticipant, Skill, Speaker, SystemTool, Tool, ToolEvent, User, VoiceSession, WakeCreds } from "@/lib/types";
import ArtifactPanel from "@/components/ArtifactPanel";
import CodespaceFileBrowser, { CODESPACE_DND_MIME, CODESPACE_SNIPPET_MIME, extLang, stripLineNumbers } from "@/components/CodespaceFileBrowser";
import type { CodespaceDragPayload, CodespaceSnippetPayload } from "@/components/CodespaceFileBrowser";
import Roundtable, { nextColor, RT_COLORS } from "@/components/Roundtable";
import Markdown from "@/components/Markdown";
import { ReasoningBlock, ToolEventsPanel, fmtTime } from "@/components/MessageItem";
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
import PromptBox, { type ReasoningEffort, type RefDoc } from "@/components/PromptBox";
import MessageItem from "@/components/MessageItem";
import WorkspaceView, { type Section as WorkspaceSection } from "@/components/WorkspaceView";
import type { ChatActions } from "@/components/ChatItem";
import { SHORTCUTS, eventToCombo, resolveBinding, comboHasModifier, type ShortcutMap } from "@/lib/shortcuts";
import { useGeneration } from "./useGeneration";

type RoundtableStream = { speaker: Speaker; content: string; reasoning: string };

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
  }));
  const {
    streaming, setStreaming, streamingReasoning, setStreamingReasoning,
    toolEvents, setToolEvents, generatingImage, setGeneratingImage,
    consultingKnowledge, setConsultingKnowledge, transcribingAudio, setTranscribingAudio,
    subagents, setSubagents, guardNote, setGuardNote, liveArtifact, setLiveArtifact,
    sending, setSending, streamPhase, setStreamPhase, stopRef, makeStreamHandler, resumeStream, handleStop,
  } = gen;
  // mantém o espelho do chat ativo em dia (cobre todos os setActive de uma vez)
  useEffect(() => { activeIdRef.current = active?.id ?? null; }, [active?.id]);
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
  // Id/run são transitórios: ficam em ref para trocar/parar a leitura sem fazer
  // callbacks de todas as mensagens dependerem do estado que muda a cada clique.
  const messageSpeechRef = useRef<{ id: string | null; run: number }>({ id: null, run: 0 });
  // Wake word ("hey nome"): escuta sempre-ativa opt-in.
  const [wakeOn, setWakeOn] = useState(false);
  const [wakeStatus, setWakeStatus] = useState<"off" | "starting" | "on" | "error">("off");
  const wakeRef = useRef<{ handle: WakeHandle | null; on: boolean }>({ handle: null, on: false });
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
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
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
  // As três mensagens mais recentes permanecem completas e com layout exato. O
  // clamp/content-visibility serve para o histórico antigo, não para a conversa atual.
  const recentFullMessageIds = useMemo(
    () => new Set(messages.slice(-3).map((m) => m.id)),
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
      notion: "Notion", slack: "Slack",
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
  const voiceFor = useCallback((m: Message): string | undefined => {
    const configId = m.usage?.model_config_id;
    const byId = configId ? customModels.find((c) => c.id === configId) : undefined;
    const name = m.usage?.model_name;
    // Mensagens antigas não têm model_config_id. Só usa nome como fallback se
    // houver UMA correspondência; nomes repetidos jamais devem escolher a voz de
    // outro preset por acidente.
    const sameName = name ? customModels.filter((c) => c.name === name) : [];
    const legacyByName = sameName.length === 1 ? sameName[0] : undefined;
    return (byId ?? legacyByName ?? curCustom)?.tts_voice ?? undefined;
  }, [customModels, curCustom]);
  const toggleMessageSpeech = useCallback((m: Message) => {
    const current = messageSpeechRef.current;
    if (current.id === m.id) {
      messageSpeechRef.current = { id: null, run: current.run + 1 };
      stopSpeaking();
      setSpeakingMessageId(null);
      return;
    }
    stopSpeaking();
    const run = current.run + 1;
    messageSpeechRef.current = { id: m.id, run };
    setSpeakingMessageId(m.id);
    void speak(m.content, voiceFor(m)).finally(() => {
      if (messageSpeechRef.current.run !== run) return;
      messageSpeechRef.current = { id: null, run };
      setSpeakingMessageId(null);
    });
  }, [voiceFor]);
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

  function goHome() {
    selectChatRequestRef.current += 1; // invalida um selectChat ainda em voo
    activeIdRef.current = null;
    leaveViewOnce();
    setActive(null);
    setDraftRt(null);
    setMessages([]);
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
    setSending(false);
    setGeneratingImage(false);
    setSubagents([]);
    setGuardNote(null);
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
    if (active) await patchActive({ model: id, model_config_id: null });
  }
  async function selectCustom(mc: ModelConfig) {
    modelChosenRef.current = true;
    setCurModel(mc.base_model);
    setCurCustomId(mc.id);
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
        if (state.acc && isActiveChat(ownerId)) {
          setMessages((m) => [...m, { id: `a-${Date.now()}`, role: "assistant", content: state.acc, reasoning: state.reason ? { text: state.reason } : null, tool_events: state.tools.length ? state.tools : null, created_at: new Date().toISOString() }]);
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
        await streamMessage(chat.id, text, onEvent, undefined, turnSkillIds, turnAttachments, turnAgentId, turnRefDocIds, turnRefChatIds);
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
            const t = await transcribe(blob);
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
    // retoma a wake word se a escuta continua ligada (o modo voz "pausou" o mic dela)
    const w = wakeRef.current;
    if (w.on && w.handle) w.handle.resume().catch(() => {});
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
    const voice = curCustom?.tts_voice ?? undefined;
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
      try { text = (await (sttLocal ? transcribeWhisper(blob) : transcribe(blob))).trim(); } catch { text = ""; }
      if (!voiceRef.current.active) break;
      if (!text) { if (loops) continue; else break; }
      firstTurn = false;

      const reply = await voiceSendAndWait(text);
      if (!voiceRef.current.active) break;

      if (session.auto_speak && reply) {
        setVoicePhase("speaking");
        try { await speak(reply, voice); } catch { /* fallback interno */ }
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
    let session: VoiceSession;
    try {
      session = await api.post<VoiceSession>("/voice/session", { model_config_id: mcId });
    } catch (e) {
      alert(e instanceof ApiError ? e.message : "Não foi possível iniciar o modo voz.");
      return;
    }
    voiceRef.current = { active: true, utter: null, session };
    // solta o mic da wake word enquanto o modo voz grava (idempotente: se veio de
    // onWakeTriggered já está pausada; cobre também o start manual/atalho global).
    if (wakeRef.current.on && wakeRef.current.handle) {
      try { await wakeRef.current.handle.pause(); } catch { /* segue: o mic é multi-stream */ }
    }
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

  // ---- Wake word ("hey nome"): escuta sempre-ativa opt-in ----
  async function onWakeTriggered() {
    if (voiceRef.current.active) return; // já dentro do modo voz
    const h = wakeRef.current.handle;
    if (h) { try { await h.pause(); } catch { /* solta o mic p/ o modo voz */ } }
    void startVoiceModeRef.current();
  }

  async function startWake() {
    const lc = (curCustom?.filter_config?.listen ?? {}) as ListenConfig;
    if (!lc.wake_enabled) { alert("Ative a wake word nas Configurações do modelo → Voz."); return; }
    const engine =
      lc.wake_engine === "vosk" ? "vosk"
      : lc.wake_engine === "whisper" ? "whisper"
      : lc.wake_engine === "openwakeword" ? "openwakeword"
      : "porcupine";
    // Whisper não tem chave; os demais buscam creds (Porcupine=key, Vosk/OWW=URL) do servidor.
    let wake: WakeCreds = {};
    if (engine !== "whisper") {
      try { wake = (await api.get<WakeCreds>("/voice/wake")) ?? {}; } catch { /* segue com vazio → valida abaixo */ }
    }
    // Porcupine "__custom__" usa o .ppn do usuário; senão a palavra embutida
    const custom = lc.porcupine_keyword === "__custom__";
    const kw = custom ? (wake.ppn_url || "") : (lc.porcupine_keyword || "Jarvis");
    // valida cedo com mensagem que aponta o lugar certo
    if (engine === "porcupine" && !wake.picovoice_key) {
      alert("Configure a AccessKey da Picovoice em Configurações → Conexões → Assistente de voz."); return;
    }
    if (engine === "porcupine" && custom && !wake.ppn_url) {
      alert("Palavra 'Personalizada' selecionada, mas nenhum .ppn cadastrado em Conexões → Assistente de voz."); return;
    }
    if (engine === "openwakeword" && !(wake.oww_model_url || "").trim()) {
      alert("Cadastre a URL do seu modelo OpenWakeWord (.onnx) em Conexões → Assistente."); return;
    }
    // Vosk/Whisper casam a 'Palavra de ativação'; OpenWakeWord/Porcupine não precisam dela.
    if ((engine === "vosk" || engine === "whisper") && !(lc.call_name || "").trim()) {
      alert("Defina a 'Palavra de ativação' nas Configurações do modelo → Assistente."); return;
    }
    setWakeStatus("starting");
    try {
      const handle = await startWakeWord({
        engine,
        callName: lc.call_name,
        accessKey: wake.picovoice_key,
        porcupineKeyword: kw,
        voskModelUrl: wake.vosk_model_url,
        owwModelUrl: wake.oww_model_url,
        owwMelspecUrl: wake.oww_melspec_url,
        owwEmbeddingUrl: wake.oww_embedding_url,
        owwThreshold: lc.oww_threshold,
        onError: () => setWakeStatus("error"),
      }, () => { void onWakeTriggered(); });
      wakeRef.current = { handle, on: true };
      setWakeOn(true);
      setWakeStatus("on");
    } catch (e) {
      setWakeStatus("error");
      alert(e instanceof Error ? e.message : "Falha ao iniciar a escuta (verifique as chaves em Conexões → Assistente de voz).");
    }
  }

  async function stopWake() {
    const h = wakeRef.current.handle;
    wakeRef.current = { handle: null, on: false };
    setWakeOn(false);
    setWakeStatus("off");
    if (h) { try { await h.stop(); } catch { /* noop */ } }
  }

  function toggleWake() { if (wakeRef.current.on) stopWake(); else startWake(); }

  // some a escuta ao desmontar (não deixa o mic ligado)
  useEffect(() => () => { void wakeRef.current.handle?.stop(); }, []);

  const wakeAvailable = !!(curCustom?.filter_config?.listen as ListenConfig | undefined)?.wake_enabled;
  const wakeName = ((curCustom?.filter_config?.listen as ListenConfig | undefined)?.call_name || "").trim();

  // Trocar para um modelo SEM wake esconde o chip; sem isto o handle (e o mic)
  // continuariam vivos sem UI para parar. Desliga a escuta nessa transição.
  useEffect(() => {
    if (!wakeAvailable && wakeRef.current.on) void stopWake();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wakeAvailable]);

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

  // nível de raciocínio (thinking) — lido/gravado nos params do modelo
  // Em chats vinculados, o ModelConfig é a fonte de verdade. Assim, voltar de
  // "Modelos" já mostra os parâmetros editados sem precisar recriar o chat.
  const activeParams = active
    ? (active.model_config_id && curCustom ? curCustom.params ?? {} : active.params)
    : draftParams;
  const reasoningEffort: ReasoningEffort =
    ((activeParams?.reasoning as { effort?: ReasoningEffort } | undefined)?.effort) ?? "off";
  function setReasoningEffort(level: ReasoningEffort) {
    const base = { ...(activeParams ?? {}) };
    if (level === "off") delete base.reasoning;
    else base.reasoning = { effort: level };
    if (active?.model_config_id && curCustom) {
      api.patch<ModelConfig>(`/models/${curCustom.id}`, { params: base })
        .then((updated) => setCustomModels((models) => models.map((m) => m.id === updated.id ? updated : m)))
        .catch(() => {});
    } else if (active) {
      patchActive({ params: base });
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
                  <PromptBox value={input} onChange={setInput} onSend={send} onStop={handleStop} onQueue={enqueue} queued={queued} sending={sending} recording={recording} onToggleMic={toggleMic} onVoiceMode={toggleVoiceMode} modelTools={modelTools} prompts={prompts} skills={skills} attachedSkillIds={attachedSkillIds} onAttachedSkillIdsChange={setAttachedSkillIds} agents={agentsForMention} agentId={agentId} onAgentChange={setAgentId} knowledgeRefs={knowledgeRefs} refDocs={refDocs} onRefDocsChange={setRefDocs} chats={chats.filter((c) => c.id !== active?.id)} refChats={refChats} onRefChatsChange={setRefChats} capabilities={curCustom?.capabilities} attachments={attachments} onAttachmentsChange={setAttachments} reasoning={reasoningEffort} onReasoningChange={setReasoningEffort} temporary={temporary} />
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
                  className="flex-1 space-y-5 overflow-y-auto px-4 pb-14 pt-6 [scroll-padding-bottom:5rem]"
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
                        onSpeak={() => toggleMessageSpeech(m)}
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
                              onSpeak={m.role === "assistant" ? () => toggleMessageSpeech(m) : undefined}
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
                      reasoning={streamingReasoning ? { text: streamingReasoning } : null}
                      reasoningLive={!streaming}
                      toolEvents={toolEvents.length ? toolEvents : undefined}
                      toolsLive={sending}
                      status={statusFor({ sending, phase: streamPhase, streaming, streamingReasoning, generatingImage, consultingKnowledge, transcribingAudio, toolEvents })}
                      footer={generatingImage ? <GeneratingImage /> : consultingKnowledge ? <ConsultingKnowledge /> : transcribingAudio ? <TranscribingAudio /> : undefined}
                    />
                  ) : (
                    sending && <Thinking />
                  ))}
                </div>
                {/* Composer NO FLUXO (shrink-0): ocupa espaço de verdade, então a área
                    de rolagem acima nunca fica maior que o disponível. Cresce (anexos,
                    AskOptions, multilinha) encolhendo a área de rolagem automaticamente
                    — sem medir nada. O gradiente é `absolute` ACIMA dele (-top-12), só
                    enfeite: não entra no layout e não pode desalinhar a geometria. */}
                <div className="relative z-10 shrink-0">
                  <div className="pointer-events-none absolute -top-12 inset-x-0 h-12 bg-gradient-to-t from-bg to-transparent" />
                  <div className="bg-bg px-4 pb-3">
                    <div
                      className={`relative rounded-2xl transition-shadow ${csDropOver ? "ring-2 ring-accent/50" : ""}`}
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
                      <PromptBox value={input} onChange={setInput} onSend={send} onStop={handleStop} onQueue={enqueue} queued={queued} sending={sending} recording={recording} onToggleMic={toggleMic} onVoiceMode={toggleVoiceMode} modelTools={modelTools} prompts={prompts} skills={skills} attachedSkillIds={attachedSkillIds} onAttachedSkillIdsChange={setAttachedSkillIds} agents={agentsForMention} agentId={agentId} onAgentChange={setAgentId} knowledgeRefs={knowledgeRefs} refDocs={refDocs} onRefDocsChange={setRefDocs} chats={chats.filter((c) => c.id !== active?.id)} refChats={refChats} onRefChatsChange={setRefChats} capabilities={curCustom?.capabilities} attachments={attachments} onAttachmentsChange={setAttachments} reasoning={reasoningEffort} onReasoningChange={setReasoningEffort} reasoningModel={curCustom ? curCustom.base_model : curModel} context={contextInfo} onCompact={compactContext} onHistory={() => setShowCompactions(true)} compacting={compacting} menuUp temporary={temporary} placeholder={showAsk ? "Escolha uma opção acima ou escreva sua resposta…" : undefined} />
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

      {/* Wake word — botão/indicador de escuta (só quando o modelo tem wake ligado) */}
      {wakeAvailable && voicePhase === "off" && (
        <button
          onClick={toggleWake}
          title={wakeOn ? "Escuta ativa — clique para parar" : "Ativar escuta (wake word)"}
          className={`fixed bottom-[calc(1.25rem+env(safe-area-inset-bottom))] left-4 z-[105] flex items-center gap-2 rounded-full border py-2 pl-2.5 pr-3 text-xs font-medium shadow-menu backdrop-blur transition-colors ${
            wakeOn ? "border-accent/40 bg-accent/15 text-accent-hover" : "border-border bg-surface/95 text-muted hover:text-ink"
          }`}
        >
          <Ear size={15} className={wakeOn ? "animate-pulse" : ""} />
          {wakeStatus === "starting" ? "iniciando…" : wakeOn ? (wakeName ? `escutando "${wakeName}"` : "escutando") : "escuta"}
        </button>
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
  reasoning?: { text: string; seconds?: number } | null;
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
        {reasoning?.text && (
          <ReasoningBlock text={reasoning.text} seconds={reasoning.seconds} live={reasoningLive} />
        )}
        {(content || !reasoning) && (
          <Markdown content={content} fast={streaming} className={streaming ? "stream-caret" : ""} />
        )}
        {usedTools && <ToolEventsPanel events={toolEvents!} live={toolsLive} />}
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
