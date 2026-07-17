"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowDown, ArrowUpRight, Bell, BookOpen, Check, Copy, FlaskConical, GitBranch, Image as ImageIcon, Link2, Menu, MessageSquareDashed, Search, Scissors, Share2, ShieldAlert, SlidersHorizontal, Sparkles, Trash2, Users, Volume2, Wrench, X } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { copyText } from "@/lib/clipboard";
import { streamContinue, streamEphemeral, streamMessage, streamRegenerate, streamRoundtable } from "@/lib/sse";
import { speak, startBrowserDictation, startRecording, transcribe } from "@/lib/voice";
import { browserNotify, playChime, requestNotifPermission } from "@/lib/notify";
import { downloadJSON, downloadPDF, downloadTXT } from "@/lib/download";
import { pickSuggestions, type Suggestion } from "@/lib/suggestions";
import type { AskSpec, Attachment, Chat, ChatArtifact, Folder, KnowledgeRef, Message, Model, ModelConfig, Prompt, RoundtableConfig, RoundtableParticipant, Skill, Speaker, SystemTool, Tool, ToolEvent, User } from "@/lib/types";
import ArtifactPanel from "@/components/ArtifactPanel";
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
import AutomationsView from "@/components/AutomationsView";
import PlaygroundView from "@/components/PlaygroundView";
import type { ChatActions } from "@/components/ChatItem";
import { SHORTCUTS, eventToCombo, resolveBinding, comboHasModifier, type ShortcutMap } from "@/lib/shortcuts";
import { useGeneration } from "./useGeneration";

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
  // árvore de refs acessíveis (bases acopladas ao modelo/chat) p/ o menu "#"
  const [knowledgeRefs, setKnowledgeRefs] = useState<KnowledgeRef[]>([]);
  // anexos (imagens/arquivos) do próximo envio
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [active, setActive] = useState<Chat | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  // Subsistema de GERAÇÃO (streaming/tools/imagem/conhecimento/áudio/subagentes/
  // guardas/artefato ao vivo + parser SSE + retomada + parar) — extraído p/ um hook.
  // getDeps é lido pós-render, então as deps podem ser declaradas mais abaixo.
  const gen = useGeneration(() => ({
    artifactsEnabled, temporary, setArtifactOpen, setActive,
    reloadMessages, reloadArtifacts, refreshChats,
  }));
  const {
    streaming, setStreaming, streamingReasoning, setStreamingReasoning,
    toolEvents, setToolEvents, generatingImage, setGeneratingImage,
    consultingKnowledge, setConsultingKnowledge, transcribingAudio, setTranscribingAudio,
    subagents, setSubagents, guardNote, setGuardNote, liveArtifact, setLiveArtifact,
    sending, setSending, stopRef, makeStreamHandler, resumeStream, handleStop,
  } = gen;
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
  const [rtStreaming, setRtStreaming] = useState<{ speaker: Speaker; content: string; reasoning: string } | null>(null);
  const rtAbort = useRef<AbortController | null>(null);

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
  // tela de Automações embutida (mantém a barra lateral visível)
  const [automationsOpen, setAutomationsOpen] = useState(false);
  // tela de Playground embutida (benchmarks / comparações / debug de tools)
  const [playgroundOpen, setPlaygroundOpen] = useState(false);
  const [playgroundKey, setPlaygroundKey] = useState(0);
  // quando != null, o Espaço de Trabalho abre direto no editor deste modelo
  const [editModelTarget, setEditModelTarget] = useState<ModelConfig | null>(null);
  // quando != null, o Espaço de Trabalho abre direto nesta seção (ex.: Analítica)
  const [workspaceSection, setWorkspaceSection] = useState<WorkspaceSection | null>(null);
  const [collapsed, setCollapsed] = useState(false);
  const [showControls, setShowControls] = useState(false);
  const [showShare, setShowShare] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  // mobile: drawer da barra lateral + detecção de tela pequena (< md)
  const [mobileNav, setMobileNav] = useState(false);
  const [isMobile, setIsMobile] = useState(false);
  const [showPalette, setShowPalette] = useState(false);
  const [settingsCat, setSettingsCat] = useState<string | undefined>(undefined);
  const openSettings = useCallback((cat?: string) => { setSettingsCat(cat); setShowSettings(true); }, []);
  const [showArchived, setShowArchived] = useState(false);
  const [showChatMgr, setShowChatMgr] = useState(false);
  const [showCompactions, setShowCompactions] = useState(false);

  const [recording, setRecording] = useState(false);
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  // notificações (toast + som), controladas pela config "Notificações" da Conta
  const [toasts, setToasts] = useState<{ id: number; title: string; body?: string }[]>([]);
  const recorderRef = useRef<{ stop: () => Promise<Blob> } | null>(null);
  const browserDictRef = useRef<{ stop: () => Promise<string> } | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  // botão "ir até o fim": visível só quando o usuário rolou p/ cima
  const [atBottom, setAtBottom] = useState(true);
  // id da última mensagem cujo seletor de opções (kind:ask) foi dispensado
  const [dismissedAsk, setDismissedAsk] = useState<string | null>(null);
  // altura do composer flutuante → padding-bottom da área de rolagem (p/ a última
  // mensagem não ficar escondida sob a promptbox)
  const composerRef = useRef<HTMLDivElement>(null);
  const [composerH, setComposerH] = useState(96);
  const typeTimer = useRef<ReturnType<typeof setInterval> | null>(null);

  const onScrollArea = () => {
    const el = scrollRef.current;
    if (!el) return;
    setAtBottom(el.scrollHeight - el.scrollTop - el.clientHeight < 80);
  };
  const scrollToBottom = () => {
    const el = scrollRef.current;
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  };

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
  // modelos externos = OpenRouter + locais do Ollama (mesclados no seletor). Cada
  // fetch é independente: sem chave OpenRouter ainda mostra os locais, e vice-versa.
  const refreshExtModels = useCallback(async () => {
    const [ext, local] = await Promise.all([
      api.get<Model[]>("/settings/models").catch(() => [] as Model[]),
      api.get<Model[]>("/integrations/ollama/models").catch(() => [] as Model[]),
    ]);
    setExtModels([...ext, ...local]);
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
        return;
      }
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
        api.get<Skill[]>("/skills").then(setSkills).catch(() => {});
      })
      .catch((e) => {
        if (e instanceof ApiError && e.status === 401) router.replace("/login");
      });
  }, [router, refreshChats, refreshFolders, refreshExtModels]);

  // preserva o estado expandido/encolhido da sidebar entre sessões
  useEffect(() => {
    setCollapsed(localStorage.getItem("sidebarCollapsed") === "1");
  }, []);

  // recarrega os modelos custom ao focar a janela (pega avatar/edições recentes)
  useEffect(() => {
    const onFocus = () => refreshModels();
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, [refreshModels]);

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

  // auto-scroll "grudento": segue o conteúdo novo SÓ se o usuário já está no fim.
  // Antes rolava SEMPRE — impossível rolar p/ ler painéis expandidos (ferramentas/
  // custo) durante o streaming ou quando o poll recarregava as mensagens: a página
  // puxava o usuário de volta pro fundo a cada evento.
  const stickToBottom = useCallback(() => {
    if (!pollRef.current.atBottom) return;
    // usuário selecionando texto: rolar agora arrasta o conteúdo sob o cursor e
    // desfaz a seleção (impossível copiar enquanto a IA responde) — pausa o grude
    const sel = typeof window !== "undefined" ? window.getSelection() : null;
    if (sel && !sel.isCollapsed) return;
    const el = scrollRef.current;
    if (el) el.scrollTo({ top: el.scrollHeight });
  }, []);
  // `composerH` entra nas dependências: quando o composer cresce (anexos, AskOptions,
  // multilinha) o padding inferior aumenta — se estávamos no fim, re-gruda p/ a última
  // mensagem não ficar presa atrás do composer.
  useEffect(() => {
    stickToBottom();
  }, [messages, streaming, streamingReasoning, toolEvents, composerH, stickToBottom]);

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

  // mede a altura do composer flutuante (muda com opções/anexos/linhas). O padding
  // inferior da área de rolagem = essa altura; se ela for medida CEDO demais (antes de
  // fontes/chips assentarem) e nada mais redimensionar, o padding fica curto e a última
  // mensagem trava atrás do composer — dava o bug de "não consigo rolar até o fim, só
  // um F5 corrige". Por isso re-medimos após o layout assentar (rAF + timeout) e no
  // resize da janela, além do ResizeObserver.
  const hasConversation = messages.length > 0 || !!streaming || rtRunning || !!rtStreaming;
  useEffect(() => {
    const el = composerRef.current;
    if (!el) return;
    const measure = () => setComposerH(el.offsetHeight);
    measure();
    const r1 = requestAnimationFrame(measure);
    const r2 = requestAnimationFrame(() => requestAnimationFrame(measure));
    const t = setTimeout(measure, 300);
    let ro: ResizeObserver | undefined;
    if (typeof ResizeObserver !== "undefined") {
      ro = new ResizeObserver(measure);
      ro.observe(el);
    }
    window.addEventListener("resize", measure);
    return () => {
      cancelAnimationFrame(r1); cancelAnimationFrame(r2); clearTimeout(t);
      ro?.disconnect(); window.removeEventListener("resize", measure);
    };
  }, [hasConversation]);


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
    if (qs.get("v") === "automations") setAutomationsOpen(true);
    if (qs.get("v") === "analytics") { setWorkspaceSection("Analítica"); setWorkspaceOpen(true); }
    const cid = qs.get("c");
    if (cid) {
      selectChat(cid).catch(() => {});
      window.history.replaceState(null, "", "/chat");
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
  // a nome+descrição para o menu da chave inglesa na promptbox.
  const modelTools = useMemo(() => {
    if (!curCustom?.tools_enabled) return [];
    const sysByPath = new Map(systemTools.map((t) => [t.path, t]));
    const userById = new Map(tools.map((t) => [t.id, t]));
    const out: { name: string; description?: string }[] = [];
    for (const id of curCustom.tool_ids ?? []) {
      if (typeof id !== "string") continue;
      if (id.startsWith("builtin:")) {
        const s = sysByPath.get(id.slice(8));
        if (s) out.push({ name: s.name, description: s.description });
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
    const name = m.usage?.model_name;
    const byMsg = name ? customModels.find((c) => c.name === name) : undefined;
    return (byMsg ?? curCustom)?.tts_voice ?? undefined;
  }, [customModels, curCustom]);

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
    const chat = await api.post<Chat>("/chats", { title: "Mesa-redonda", model: curModel, model_config_id: curCustomId });
    const full = { ...chat, mode: "roundtable" as const, participants: draftRt.participants, roundtable_config: draftRt.config };
    setActive(full);
    setDraftRt(null);
    try { await api.patch(`/chats/${chat.id}`, { mode: "roundtable", participants: draftRt.participants, roundtable_config: draftRt.config }); } catch { /* ignore */ }
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

  function makeRtHandler() {
    return (ev: any) => {
      if (ev.type === "speaker_start") {
        setRtStreaming({ speaker: ev.speaker, content: "", reasoning: "" });
        setAtBottom(true);
      } else if (ev.type === "token") {
        setRtStreaming((s) => (s ? { ...s, content: s.content + (ev.text || "") } : s));
      } else if (ev.type === "reasoning") {
        setRtStreaming((s) => (s ? { ...s, reasoning: s.reasoning + (ev.text || "") } : s));
      } else if (ev.type === "speaker_end") {
        setRtStreaming((s) => {
          // turno vazio (o backend não persistiu): não adiciona bolha vazia
          if (s && s.content.trim()) {
            setMessages((m) => [...m, {
              id: ev.message_id || `a-${Date.now()}`, role: "assistant", content: s.content,
              reasoning: s.reasoning ? { text: s.reasoning } : null, speaker: ev.speaker,
              created_at: new Date().toISOString(),
            }]);
          }
          return null;
        });
      } else if (ev.type === "error") {
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
    setAutomationsOpen(false);
    setPlaygroundOpen(false);
  }

  const reloadMessages = useCallback(async (chatId: string) => {
    const rows = await api.get<Message[]>(`/chats/${chatId}/messages`);
    setMessages(rows);
  }, []);

  const reloadArtifacts = useCallback(async (chatId: string) => {
    try {
      setChatArtifacts(await api.get<ChatArtifact[]>(`/chats/${chatId}/artifacts`));
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
    setTemporary(false);
    setWorkspaceOpen(false);
    setAutomationsOpen(false);
    setPlaygroundOpen(false);
    leaveViewOnce(id);
    setDraftRt(null);
    const detail = await api.get<Chat & { messages: Message[] }>(`/chats/${id}`);
    setActive(detail);
    setMessages(detail.messages ?? []);
    setAtBottom(true); // abrir um chat sempre começa no fim
    setStreaming("");
    setStreamingReasoning("");
    setToolEvents([]);
    setArtifactOpen(null);
    setLiveArtifact(null);
    setChatArtifacts([]);
    reloadArtifacts(id);
    modelChosenRef.current = true;
    setCurModel(detail.model);
    // restaura o vínculo com o modelo personalizado (define ferramentas)
    setCurCustomId(detail.model_config_id ?? null);
    // se havia uma resposta sendo gerada quando o chat foi fechado/atualizado,
    // volta a acompanhá-la ao vivo em vez de mostrar só o que ficou salvo.
    resumeStream(id);
  }

  async function newChat() {
    setTemporary(false);
    // já está na tela de novo chat: não recarrega nem re-sorteia o "Sugerido"
    if (!active && messages.length === 0 && !streaming && !workspaceOpen && !automationsOpen) return;
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
    const text = (override ? textArg : input).trim();
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
    // clique numa opção não consome skills/anexos pendentes do usuário
    const turnSkillIds = override ? [] : attachedSkillIds;
    if (!override) setAttachedSkillIds([]);
    const turnAttachments = override ? [] : attachments;
    if (!override) setAttachments([]);
    const turnAgentId = override ? null : agentId;
    if (!override) setAgentId(null);
    const turnRefDocIds = override ? [] : refDocs.map((r) => r.id);
    if (!override) setRefDocs([]);
    setMessages((m) => [...m, { id: `tmp-${Date.now()}`, role: "user", content: text, attachments: turnAttachments, created_at: new Date().toISOString() }]);
    setAtBottom(true); // enviar re-engata o auto-scroll (acompanhar a resposta)
    setStreaming("");
    setStreamingReasoning("");
    setToolEvents([]);
    setGuardNote(null);
    setSubagents([]);

    const { handler: onEvent, state } = makeStreamHandler();

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
        if (state.acc) {
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
        }
        // persistente: o servidor cancela a geração e salva o parcial
        const cid = chat.id;
        stopRef.current = () => { api.post(`/chats/${cid}/stop`).catch(() => {}); };
        await streamMessage(chat.id, text, onEvent, undefined, turnSkillIds, turnAttachments, turnAgentId, turnRefDocIds);
        setStreaming("");
        setStreamingReasoning("");
        refreshChats();
        // recarrega as mensagens reais (ids do servidor + registro de tokens/custo)
        await reloadMessages(chat.id);
        await reloadArtifacts(chat.id);
      }
      if (state.acc) notify("Resposta pronta", state.acc.replace(/\s+/g, " ").slice(0, 90));
    } catch (e) {
      // orçamento pessoal estourado (modo "pausar") ou outra falha ao iniciar o turno
      const msg = e instanceof ApiError ? e.message : "Falha ao enviar a mensagem";
      setMessages((m) => m.filter((x) => !x.id.startsWith("tmp-"))); // desfaz o balão otimista
      if (!override) setInput(text); // devolve o texto pro composer
      notify(e instanceof ApiError && e.status === 402 ? "Orçamento mensal atingido" : "Erro", msg);
      refreshBudget();
    } finally {
      stopRef.current = null;
      setStreaming("");
      setStreamingReasoning("");
      setGeneratingImage(false);
      setLiveArtifact(null);
      setSending(false);
      refreshBudget(); // atualiza o gasto do mês (mantém o banner em dia)
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
    const { handler, state } = makeStreamHandler();
    const cid = active.id;
    stopRef.current = () => { api.post(`/chats/${cid}/stop`).catch(() => {}); };
    try {
      await streamRegenerate(active.id, id, handler);
      setStreaming("");
      setStreamingReasoning("");
      await reloadMessages(active.id);
      await reloadArtifacts(active.id);
      refreshChats();
      if (state.acc) notify("Resposta pronta", state.acc.replace(/\s+/g, " ").slice(0, 90));
    } finally {
      stopRef.current = null;
      setStreaming("");
      setStreamingReasoning("");
      setLiveArtifact(null);
      setSending(false);
    }
  }

  async function continueMessage(id: string) {
    if (!active || sending) return;
    setSending(true);
    setStreaming("");
    setStreamingReasoning("");
    setToolEvents([]);
    const { handler, state } = makeStreamHandler();
    const cid = active.id;
    stopRef.current = () => { api.post(`/chats/${cid}/stop`).catch(() => {}); };
    try {
      await streamContinue(active.id, id, handler);
      setStreaming("");
      setStreamingReasoning("");
      await reloadMessages(active.id);
      await reloadArtifacts(active.id);
      if (state.acc) notify("Resposta continuada", state.acc.replace(/\s+/g, " ").slice(0, 90));
    } finally {
      stopRef.current = null;
      setStreaming("");
      setStreamingReasoning("");
      setLiveArtifact(null);
      setSending(false);
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
  const activeParams = active ? active.params : draftParams;
  const reasoningEffort: ReasoningEffort =
    ((activeParams?.reasoning as { effort?: ReasoningEffort } | undefined)?.effort) ?? "off";
  function setReasoningEffort(level: ReasoningEffort) {
    const base = { ...(activeParams ?? {}) };
    if (level === "off") delete base.reasoning;
    else base.reasoning = { effort: level };
    if (active) patchActive({ params: base });
    else setDraftParams(base);
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
    for (let i = messages.length - 1; i >= 0; i--) {
      const u = messages[i].usage;
      if (u?.total_tokens) return (u.prompt_tokens || 0) + (u.completion_tokens || 0);
    }
    const chars = messages.reduce((a, m) => a + (m.content?.length ?? 0), 0);
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
    workspace: () => { setWorkspaceSection(null); setWorkspaceOpen(true); setAutomationsOpen(false); setPlaygroundOpen(false); },
    automations: () => { setAutomationsOpen(true); setWorkspaceOpen(false); setPlaygroundOpen(false); },
    playground: () => { setPlaygroundKey((k) => k + 1); setPlaygroundOpen(true); setWorkspaceOpen(false); setAutomationsOpen(false); },
    settings: () => setShowSettings(true),
    archived: () => setShowArchived(true),
    dictate: () => toggleMic(),
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
    { id: "act-ws", group: "Ações", label: "Espaço de Trabalho", keywords: "modelos ferramentas prompts skills", icon: <Wrench size={16} />, run: () => { setWorkspaceSection(null); setWorkspaceOpen(true); setAutomationsOpen(false); } },
    { id: "act-auto", group: "Ações", label: "Automações", keywords: "agendar monitor", icon: <Bell size={16} />, run: () => { setAutomationsOpen(true); setWorkspaceOpen(false); setPlaygroundOpen(false); } },
    { id: "act-play", group: "Ações", label: "Playground", keywords: "benchmark comparar modelos debug ferramentas tools", icon: <FlaskConical size={16} />, run: () => { setPlaygroundKey((k) => k + 1); setPlaygroundOpen(true); setWorkspaceOpen(false); setAutomationsOpen(false); } },
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
        className={`fixed inset-y-0 left-0 z-50 flex shrink-0 transition-transform duration-300 md:static md:z-auto md:translate-x-0 md:transition-none ${
          mobileNav ? "translate-x-0" : "-translate-x-full"
        }`}
      >
        <Sidebar
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
          onOpenWorkspace={() => { setEditModelTarget(null); setWorkspaceSection(null); setWorkspaceKey((k) => k + 1); setWorkspaceOpen(true); setAutomationsOpen(false); setPlaygroundOpen(false); setMobileNav(false); }}
          onOpenAutomations={() => { setAutomationsOpen(true); setWorkspaceOpen(false); setPlaygroundOpen(false); setMobileNav(false); }}
          onOpenPlayground={() => { setPlaygroundKey((k) => k + 1); setPlaygroundOpen(true); setWorkspaceOpen(false); setAutomationsOpen(false); setMobileNav(false); }}
          onOpenAnalytics={() => { setEditModelTarget(null); setWorkspaceSection("Analítica"); setWorkspaceKey((k) => k + 1); setWorkspaceOpen(true); setAutomationsOpen(false); setPlaygroundOpen(false); setMobileNav(false); }}
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
            onClose={() => { setWorkspaceOpen(false); setEditModelTarget(null); setWorkspaceSection(null); refreshModels(); }}
          />
        ) : automationsOpen ? (
          <AutomationsView
            onOpenChat={(cid) => { setAutomationsOpen(false); selectChat(cid).catch(() => {}); }}
            onBack={() => setAutomationsOpen(false)}
          />
        ) : playgroundOpen ? (
          <PlaygroundView key={playgroundKey} onClose={() => setPlaygroundOpen(false)} />
        ) : (
        <>
        {/* barra superior */}
        <div className="flex items-start justify-between gap-2 px-2 py-2.5 sm:px-4">
          <div className="flex min-w-0 flex-col">
            <div className="flex items-center gap-1">
              <button onClick={() => setMobileNav(true)} title="Menu" className="rounded-lg p-1.5 text-muted transition-colors hover:bg-hover hover:text-ink md:hidden">
                <Menu size={20} />
              </button>
              {picker}
              {!temporary && (
                <button
                  onClick={() => { if (isRoundtable) setRtBarOpen((v) => !v); else { enterRoundtable(); setRtBarOpen(true); } }}
                  title={isRoundtable ? (rtBarOpen ? "Ocultar a mesa" : "Mostrar a mesa") : "Mesa-redonda: fazer os modelos conversarem entre si"}
                  className={`rounded-lg p-1.5 transition-colors ${isRoundtable && rtBarOpen ? "bg-accent/15 text-accent-hover" : isRoundtable ? "text-accent-hover hover:bg-hover" : "text-muted hover:bg-hover hover:text-ink"}`}
                >
                  <Users size={18} />
                </button>
              )}
            </div>
            {!active && !temporary && curModel && (
              <button onClick={setAsDefault} className="pl-2 text-left text-xs text-muted transition-colors hover:text-ink">
                {user.default_model === (curCustomId ? `custom:${curCustomId}` : curModel)
                  ? "Modelo padrão ✓"
                  : "Definir como padrão"}
              </button>
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
                <div className="w-full max-w-3xl">
                  <PromptBox value={input} onChange={setInput} onSend={send} onStop={handleStop} sending={sending} recording={recording} onToggleMic={toggleMic} modelTools={modelTools} prompts={prompts} skills={skills} attachedSkillIds={attachedSkillIds} onAttachedSkillIdsChange={setAttachedSkillIds} agents={agentsForMention} agentId={agentId} onAgentChange={setAgentId} knowledgeRefs={knowledgeRefs} refDocs={refDocs} onRefDocsChange={setRefDocs} capabilities={curCustom?.capabilities} attachments={attachments} onAttachmentsChange={setAttachments} reasoning={reasoningEffort} onReasoningChange={setReasoningEffort} temporary={temporary} />
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
                {/* scrollPaddingBottom: o scrollIntoView dos painéis expandidos
                    (ferramentas/custo) mira ACIMA do composer flutuante */}
                <div ref={scrollRef} onScroll={onScrollArea} className="flex-1 space-y-5 overflow-y-auto px-4 pt-6" style={{ paddingBottom: composerH + 40, scrollPaddingBottom: composerH + 56 }}>
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
                  {messages.map((m) => (
                    <div key={m.id} id={`msg-${m.id}`} className="msg-row">
                      {m.speaker && !m.is_summary && (
                        <div className="mx-auto mb-1 flex max-w-3xl items-center gap-1.5 px-1 text-xs font-semibold">
                          <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: m.speaker.color || "#888" }} />
                          <span style={{ color: m.speaker.color || undefined }}>{m.speaker.name}</span>
                        </div>
                      )}
                      {m.is_summary ? (
                        <CompactionDivider onOpen={() => setShowCompactions(true)} />
                      ) : temporary ? (
                        <MessageBubble
                          role={m.role}
                          content={m.content}
                          time={m.created_at}
                          name={m.role === "assistant" ? modelLabel : undefined}
                          reasoning={m.reasoning}
                          toolEvents={m.tool_events ?? undefined}
                          onSpeak={m.role === "assistant" ? () => speak(m.content, voiceFor(m)) : undefined}
                          onDelete={() => deleteMessage(m.id)}
                        />
                      ) : (
                        <MessageItem
                          message={m}
                          busy={sending}
                          modelName={m.speaker?.name ?? modelLabel}
                          toolsEnabled={iface.chat_tools !== false}
                          modelAvatar={iface.chat_model_image !== false ? (curCustom?.avatar_url ?? null) : null}
                          chatArtifacts={chatArtifacts}
                          onOpenArtifact={(ident) => setArtifactOpen(ident)}
                          onSpeak={(c) => speak(c, voiceFor(m))}
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
                      )}
                    </div>
                  ))}
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
                      <div className="mx-auto mb-1 flex max-w-3xl items-center gap-1.5 px-1 text-xs font-semibold">
                        <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: rtStreaming.speaker.color || "#888" }} />
                        <span style={{ color: rtStreaming.speaker.color || undefined }}>{rtStreaming.speaker.name}</span>
                      </div>
                      <MessageBubble
                        role="assistant"
                        content={rtStreaming.content}
                        streaming={!!rtStreaming.content}
                        name={rtStreaming.speaker.name}
                        reasoning={rtStreaming.reasoning ? { text: rtStreaming.reasoning } : null}
                        reasoningLive={!rtStreaming.content}
                      />
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
                      footer={generatingImage ? <GeneratingImage /> : consultingKnowledge ? <ConsultingKnowledge /> : transcribingAudio ? <TranscribingAudio /> : undefined}
                    />
                  ) : (
                    sending && <Thinking />
                  ))}
                </div>
                {/* composer flutuante: sobrepõe as mensagens; elas somem num fade
                    ao rolar por baixo (o fundo sólido oculta, o gradiente suaviza) */}
                <div ref={composerRef} className="pointer-events-none absolute inset-x-0 bottom-0 z-10">
                  <div className="pointer-events-none h-12 bg-gradient-to-t from-bg to-transparent" />
                  <div className="pointer-events-auto bg-bg px-4 pb-3">
                    <div className="relative">
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
                      <PromptBox value={input} onChange={setInput} onSend={send} onStop={handleStop} sending={sending} recording={recording} onToggleMic={toggleMic} modelTools={modelTools} prompts={prompts} skills={skills} attachedSkillIds={attachedSkillIds} onAttachedSkillIdsChange={setAttachedSkillIds} agents={agentsForMention} agentId={agentId} onAgentChange={setAgentId} knowledgeRefs={knowledgeRefs} refDocs={refDocs} onRefDocsChange={setRefDocs} capabilities={curCustom?.capabilities} attachments={attachments} onAttachmentsChange={setAttachments} reasoning={reasoningEffort} onReasoningChange={setReasoningEffort} context={contextInfo} onCompact={compactContext} onHistory={() => setShowCompactions(true)} compacting={compacting} menuUp temporary={temporary} placeholder={showAsk ? "Escolha uma opção acima ou escreva sua resposta…" : undefined} />
                    </div>
                  </div>
                </div>
                <MessageNavigator messages={messages} onJump={jumpToMessage} />
              </>
            )}
          </div>
          {/* Artefatos: coluna ao lado da conversa; no mobile vira tela cheia */}
          {artifactsEnabled && hasConversation && artifactOpen != null && (liveArtifact || chatArtifacts.length > 0) && (
            <div className="fixed inset-0 z-50 shrink-0 bg-bg md:static md:z-auto md:w-[46%] md:min-w-[380px] md:max-w-[760px] md:bg-transparent">
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
        </div>
        </>
        )}
      </main>

      {/* Controles: coluna de altura total à direita, como a barra lateral esquerda */}
      {showControls && !workspaceOpen && (
        <>
          {/* backdrop (mobile): Controles viram slide-over à direita */}
          <div className="fixed inset-0 z-40 bg-black/50 md:hidden" onClick={() => setShowControls(false)} />
          <div className="fixed inset-y-0 right-0 z-50 flex shrink-0 md:static md:z-auto">
            <Controls
              key={active?.id ?? "draft"}
              systemPrompt={active ? active.system_prompt ?? "" : draftSystemPrompt}
              params={active ? active.params : draftParams}
              memory={active ? active.memory_config ?? null : undefined}
              memoryDefault={memoryDefault}
              hasProject={!!active?.folder_id}
              onMemoryChange={active ? (cfg) => patchActive({ memory_config: cfg }) : undefined}
              knowledge={active ? active.knowledge_config ?? null : undefined}
              onKnowledgeChange={active ? (cfg) => patchActive({ knowledge_config: cfg }) : undefined}
              brain={active ? active.brain_config ?? null : undefined}
              onBrainChange={active ? (cfg) => patchActive({ brain_config: cfg }) : undefined}
              onSave={(sp, params) => {
                if (active) patchActive({ system_prompt: sp, params });
                else {
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
          onClose={() => { setShowSettings(false); setSettingsCat(undefined); }}
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
    </div>
  );
}

function MessageBubble({
  role,
  content,
  onSpeak,
  onDelete,
  time,
  streaming = false,
  name,
  reasoning,
  reasoningLive = false,
  toolEvents,
  footer,
}: {
  role: string;
  content: string;
  onSpeak?: () => void;
  onDelete?: () => void;
  time?: string;
  streaming?: boolean;
  name?: string;
  reasoning?: { text: string; seconds?: number } | null;
  reasoningLive?: boolean;
  toolEvents?: ToolEvent[];
  footer?: React.ReactNode;
}) {
  const [showTools, setShowTools] = useState(false);
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
    <div className="mx-auto max-w-3xl">
      <div className="group relative">
        {name && (
          <p className="mb-1.5 flex items-center gap-1.5 text-lg font-semibold tracking-tight text-ink">
            {name}
            {usedTools && (
              <button
                onClick={() => setShowTools((v) => !v)}
                title="Ferramentas usadas neste segmento"
                className={`transition-colors hover:text-ink ${showTools ? "text-accent-hover" : "text-muted"}`}
              >
                <Wrench size={15} />
              </button>
            )}
          </p>
        )}
        {reasoning?.text && (
          <ReasoningBlock text={reasoning.text} seconds={reasoning.seconds} live={reasoningLive} />
        )}
        {(content || !reasoning) && (
          <Markdown content={content} fast={streaming} clamp={streaming} className={streaming ? "stream-caret" : ""} />
        )}
        {showTools && usedTools && <ToolEventsPanel events={toolEvents!} />}
        {footer}
        {onSpeak && (
          <button onClick={() => onSpeak()} title="Ler em voz alta" className="mt-1 flex items-center gap-1 text-xs text-muted opacity-0 transition-opacity hover:text-ink group-hover:opacity-100">
            <Volume2 size={13} /> Ler
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
