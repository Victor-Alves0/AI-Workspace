export interface User {
  id: string;
  email: string;
  role: string;
  is_active: boolean;
  status: "active" | "pending" | "rejected";
  default_model: string | null;
  profile?: Record<string, any>;
}

export interface AdminUser {
  id: string;
  email: string;
  role: string;
  status: string;
  is_active: boolean;
  created_at: string;
}

export interface Model {
  id: string;
  name: string;
  context_length?: number;
  pricing?: Record<string, unknown>;
  /** modelo local do Ollama (id = "ollama/<nome>") — usa o servidor local do usuário */
  local?: boolean;
  parameter_size?: string;
  /** nome do provedor de origem (OpenRouter, Ollama, ChatGPT, ou um provedor customizado) */
  provider?: string;
}

export interface Chat {
  id: string;
  title: string;
  model: string;
  system_prompt: string | null;
  params: Record<string, unknown>;
  archived: boolean;
  pinned: boolean;
  /** etiquetas livres p/ organizar/filtrar conversas */
  tags?: string[];
  /** link público read-only (/shared/<public_id>); null = privado */
  public_id?: string | null;
  /** "Duração do Chat" (automações): apagado quando o usuário abre e sai */
  view_once?: boolean;
  folder_id: string | null;
  model_config_id: string | null;
  /** Codespace: projeto vinculado (habilita code.graph.query/code.files.browse) */
  project_id?: string | null;
  /** memória por-chat (null = herda do modelo/perfil) */
  memory_config?: MemoryConfig | null;
  /** base de conhecimento por-chat (null = herda do modelo/perfil) */
  knowledge_config?: KnowledgeConfig | null;
  /** cérebro (second brain) por-chat; null = herda do modelo/perfil */
  brain_config?: BrainConfig | null;
  /** modo do chat: "single" (normal) | "roundtable" (mesa-redonda multi-modelo) */
  mode?: "single" | "roundtable";
  /** participantes da mesa-redonda */
  participants?: RoundtableParticipant[];
  /** config da mesa-redonda (política de turno, moderador, limites) */
  roundtable_config?: RoundtableConfig | null;
  created_at: string;
  updated_at: string;
}

/** um participante da mesa-redonda: um modelo base ou um ModelConfig custom */
export interface RoundtableParticipant {
  id: string;
  model: string;
  model_config_id?: string | null;
  name: string;
  avatar?: string | null;
  color?: string | null;
  /** papel/instrução escrita direto na mesa (além do system do modelo) */
  persona?: string | null;
}

export interface RoundtableConfig {
  turn_policy?: "round_robin" | "manual" | "moderator";
  moderator?: { model?: string; model_config_id?: string | null };
  max_rounds?: number;
  /** modo manual: quem fala em seguida */
  next?: string | null;
}

/** quem produziu uma fala na mesa-redonda (assistant multi-modelo) */
export interface Speaker {
  id: string;
  name: string;
  model?: string;
  color?: string | null;
}

/** config de memória (perfil/modelo/chat). read = união dos escopos ligados. */
export interface MemoryConfig {
  enabled?: boolean;
  /** "project" grava na pasta (projeto); "bank:<id>" grava num banco compartilhado */
  write?: "global" | "model" | "chat" | "project" | "off" | string;
  read?: { global?: boolean; model?: boolean; chat?: boolean; project?: boolean };
  /** ids dos bancos de memória acoplados (lidos em união) */
  banks?: string[];
  /** revisar antes de salvar (padrão do perfil): novas memórias ficam pendentes */
  review?: boolean;
}

/** config da Base de Conhecimento (perfil/modelo/chat). bases = união das camadas. */
export interface KnowledgeConfig {
  enabled?: boolean;
  /** ids das bases de conhecimento acopladas */
  bases?: string[];
  /** "auto" = injeta trechos + cita; "tool" = o modelo busca via search_knowledge */
  mode?: "auto" | "tool";
  /** nº de trechos recuperados por turno */
  k?: number;
}

/** config do cérebro (second brain) — perfil/modelo/chat; brains = união das camadas */
export interface BrainConfig {
  enabled?: boolean;
  /** ids dos cérebros (knowledge_bases kind="brain") acoplados */
  brains?: string[];
  /** a IA pode criar/atualizar notas (action write da tool brain) */
  write?: boolean;
  /** nº de trechos recuperados na busca */
  k?: number;
}

/** uma Base de Conhecimento (coleção de documentos indexados) */
export interface KnowledgeBase {
  id: string;
  name: string;
  description: string;
  tags?: string[];
  /** "kb" = RAG de documentos | "brain" = cérebro de notas [[interligadas]] */
  kind?: "kb" | "brain";
  doc_count: number;
  chunk_count: number;
}

/** grafo de notas de um cérebro (nós + arestas de [[wikilinks]]) */
export interface BrainGraphData {
  nodes: {
    id: string;
    title: string;
    links_out: number;
    links_in: number;
    /** nota "fantasma": [[link]] citado que ainda não existe */
    ghost: boolean;
  }[];
  edges: { source: string; target: string }[];
}

/** proposta de skill do /learn (card editável; aprovar = POST /skills) */
export interface SkillProposal {
  proposal_id: string;
  slug: string;
  name: string;
  description: string;
  content: string;
  tags: string[];
}

/** nota escrita no cérebro pela IA durante o turno (card no chat) */
export interface BrainNoteEvent {
  doc_id: string;
  base_id: string;
  title: string;
  action: "created" | "updated";
  preview: string;
  url: string;
}

/** uma pasta dentro de uma base (explorador) */
export interface KnowledgeFolder {
  id: string;
  base_id: string;
  name: string;
  parent_id: string | null;
}

/** metadados de um documento (melhoram a busca; prefixados no índice) */
export interface KnowledgeDocMeta {
  title?: string;
  description?: string;
  tags?: string[];
}

/** um documento dentro de uma base */
export interface KnowledgeDoc {
  id: string;
  base_id: string;
  folder_id?: string | null;
  filename: string;
  mime: string;
  size: number;
  status: "pending" | "indexing" | "ready" | "error";
  error?: string | null;
  chunk_count: number;
  meta?: KnowledgeDocMeta | null;
  created_at?: string | null;
}

/** árvore de referências ("#") — bases acessíveis com pastas + docs prontos */
export interface KnowledgeRef {
  id: string;
  name: string;
  folders: { id: string; name: string; parent_id: string | null }[];
  docs: { id: string; filename: string; folder_id: string | null }[];
}

/** painel "Informações" do chat */
export interface ChatInfo {
  id: string;
  title: string;
  model: string;
  tags: string[];
  message_count: number;
  tokens_in: number;
  tokens_out: number;
  cost: number;
  artifacts: { id: string; identifier: string; title: string; kind: string; version: number }[];
  memory_count: number;
  /** Codespace: projeto vinculado ao chat (nome resolvido no servidor) */
  project_id?: string | null;
  project_name?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface MemoryItem {
  id: string;
  text: string;
  scope: "global" | "model" | "chat" | "bank" | "project";
  disabled?: boolean;
  model_id?: string | null;
  model_name?: string | null;
  chat_id?: string | null;
  chat_title?: string | null;
  bank_id?: string | null;
  bank_name?: string | null;
  project_id?: string | null;
  project_name?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface MemoryScopes {
  global: number;
  total: number;
  models: { id: string; name: string; count: number }[];
  chats: { id: string; title: string; count: number }[];
  banks: { id: string; name: string; count: number }[];
  projects: { id: string; name: string; count: number }[];
}

export interface MemoryBank {
  id: string;
  name: string;
  description: string;
  count: number;
}

export interface Folder {
  id: string;
  name: string;
  parent_id: string | null;
  created_at: string;
}

// checkpoint de compactação de contexto (grafo/árvore de contexto)
export interface Compaction {
  id: string;
  summary: string;
  message_count: number;
  pinned: boolean;
  parent_id: string | null;
  name: string | null;
  last_message: string | null;
  created_at: string;
}

export interface MessageUsage {
  model: string;
  model_config_id: string | null;
  model_name: string;
  provider: string;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  reasoning_tokens?: number;
  cached_tokens?: number;
  /** limite de aviso (tokens) que este turno ultrapassou; ausente = dentro do limite */
  over_budget?: number;
  cost: number;
  // detalhamento por categoria (proporcional ao tamanho de cada bloco)
  input_breakdown?: {
    user?: number;
    context?: number;
    file?: number;
    system?: number;
    /** instruções extras do canal/guardas/artefatos (extra_system) */
    extra?: number;
    memory?: number;
    tools?: number;
    skills?: number;
    tool_results?: number;
  };
  output_breakdown?: { output: number; thinking: number };
  /** tokens de resultado POR ferramenta (soma ≈ input_breakdown.tool_results) */
  tools_breakdown?: Record<string, number>;
  /** detalhe do `extra` por origem: artifacts | channel | guards (soma ≈ extra) */
  extra_breakdown?: Record<string, number>;
  /** de onde vem o prompt do sistema: do modelo/agente vs. injetado por nós (soma ≈ system) */
  system_breakdown?: { model_prompt?: number; datetime?: number };
  /** o que compõe o bloco de ferramentas no prompt (soma ≈ tools) */
  tools_prompt_breakdown?: { instructions?: number; schemas?: number; brain?: number };
  /** ferramentas REALMENTE executadas, inclusive as chamadas dentro do run_code
   *  (no Modo Código o `tools_breakdown` só mostra "run_code" — isto abre a caixa-preta) */
  called_tools_breakdown?: Record<string, number>;
}

export interface ToolEvent {
  kind: "call" | "result" | "guard";
  name: string;
  data: unknown;
  chars?: number;   // tamanho do bloco no contexto
  tokens?: number;  // custo em tokens deste evento (taxa do turno × chars)
}

// anexo de mensagem: imagem (data URL), arquivo de texto, ou doc p/ extração
export interface Attachment {
  type: "image" | "file" | "audio";
  name: string;
  url?: string; // imagem/áudio: data URL
  text?: string; // arquivo: conteúdo textual (já extraído/lido)
  data?: string; // doc binário (base64) p/ extração server-side
  mime?: string;
}

// cotação de ação (tool finance.quote.get + rota /finance/quote) → StockCard
export interface StockQuote {
  kind?: "stock_card";
  symbol: string;
  name?: string;
  exchange?: string;
  currency?: string;
  price?: number;
  prev_close?: number;
  change?: number;
  change_pct?: number;
  series?: { t: number; v: number }[];
  stats?: Record<string, number>;
  source?: string;
  range?: string;
  web_results?: { title: string; url: string; snippet: string }[];
}

// pesquisa profunda (tool research.deep.run) → DeepResearchCard
export interface DeepResearch {
  kind?: "deep_research";
  query: string;
  brief: string;
  sources: { n: number; title: string; url: string }[];
  rounds?: number;
}

// gráfico genérico (tool chart.render.plot) → ChartView
export interface ChartSpec {
  kind?: "chart";
  type: "line" | "bar" | "area" | "pie";
  title?: string;
  labels?: string[];
  series: { name: string; data: number[] }[];
}

export interface Message {
  id: string;
  role: "system" | "user" | "assistant" | "tool";
  content: string;
  tool_calls?: unknown[] | null;
  tool_call_id?: string | null;
  tokens?: number | null;
  cost?: number | null;
  usage?: MessageUsage | null;
  reasoning?: { text: string; seconds?: number } | null;
  tool_events?: ToolEvent[] | null;
  /** memórias (mem0) injetadas nesta resposta */
  memories_used?: { id: string; text: string; scope?: string }[] | null;
  attachments?: Attachment[] | null;
  /** divisor de compactação: renderizado como uma linha "Contexto compactado" */
  is_summary?: boolean;
  /** mensagem visível ao usuário mas fora do contexto da IA (compactada) */
  compacted?: boolean;
  /** mesa-redonda: quem falou (assistant multi-modelo); ausente = humano */
  speaker?: Speaker | null;
  created_at: string;
}

export interface Tool {
  id: string;
  path: string;
  name: string;
  description: string;
  params: Record<string, string>;
  returns: string[];
  code: string;
  valves: Record<string, unknown>;
  enabled: boolean;
  tags: string[];
  tool_type: "code" | "mcp";
  mcp_config: { url?: string; transport?: string; headers?: Record<string, string> };
  created_at: string;
  updated_at: string;
}

export interface Skill {
  id: string;
  slug: string;
  name: string;
  description: string;
  content: string;
  tags: string[];
  enabled: boolean;
  created_at: string;
  updated_at: string;
}

/** proposta de skill PERSISTIDA pelo Aprendizado Proativo (Curator), aguardando
 *  aprovação na aba Skills. (Diferente de `SkillProposal`, que é o card efêmero
 *  do /learn no chat.) */
export interface SkillSuggestion {
  id: string;
  chat_id: string | null;
  chat_title?: string | null;   // conversa de origem ("de qual conversa veio")
  name: string;
  slug: string;
  description: string;
  content: string;
  rationale?: string;           // por que a IA sugeriu (o gatilho na conversa)
  tags: string[];
  source: string;
  created_at: string;
}

export interface ModelConfig {
  id: string;
  base_model: string;
  name: string;
  // identificador editável (vanity id); ausente = derivado do nome
  slug?: string | null;
  description: string | null;
  avatar_url: string | null;
  system_prompt: string | null;
  params: Record<string, unknown>;
  capabilities: Record<string, boolean>;
  // parâmetros dos filtros, ex.: { vision_router: { model: "<base_model_id>" } }
  filter_config?: Record<string, any>;
  tools_enabled: boolean;
  tool_ids: string[];
  code_mode: boolean;
  // como o SIFT é apresentado ao modelo (o "quando usar") + ferramentas fixadas
  sift_config?: { mode?: "prompt" | "list"; prompt?: string; pinned?: string[] };
  // skills equipadas: o modelo vê nome+descrição e carrega o resto via view_skill
  skill_ids: string[];
  prompt_suggestions: string[];
  tts_voice: string | null;
  enabled: boolean;
  created_at: string;
  updated_at: string;
}

// Assistente de voz por-modelo (filter_config.listen)
export interface ListenConfig {
  enabled?: boolean;
  call_name?: string;
  chat_mode?: "fixed" | "new";
  auto_speak?: boolean;
  hands_free?: boolean;
  // transcreve a fala no NAVEGADOR com Whisper (offline, sem provedor de STT)
  stt_local?: boolean;
  // conversa contínua: após a resposta reabre a escuta por uma janela de graça;
  // silêncio na janela encerra a conversa (volta ao standby da wake word)
  continuous?: boolean;
  follow_up_secs?: number;
  // wake word ("hey nome") — escolhas POR-MODELO; as credenciais (chave/URL) são
  // do usuário e vivem em profile.wake (WakeCreds), não aqui.
  wake_enabled?: boolean;
  wake_engine?: "porcupine" | "vosk" | "whisper" | "openwakeword";
  // Porcupine: nome da palavra embutida ("Jarvis"…) ou "__custom__" (usa o .ppn do usuário)
  porcupine_keyword?: string;
  // OpenWakeWord: limiar de disparo do score (0..1; padrão 0.5)
  oww_threshold?: number;
}

// Credenciais da wake word — DO USUÁRIO (profile.wake), como chave de API.
// Compartilhadas por todos os modelos; o modelo só escolhe engine/palavra.
export interface WakeCreds {
  picovoice_key?: string;   // AccessKey grátis da Picovoice (usada no WASM do navegador)
  ppn_url?: string;         // .ppn custom (para "hey <nome>" no Porcupine)
  vosk_model_url?: string;  // URL do modelo Vosk (.tar.gz) — offline, sem chave
  // OpenWakeWord (on-device, ONNX): modelo treinado pelo usuário + os 2 modelos
  // compartilhados (melspectrograma + embedding). URLs precisam de CORS liberado.
  oww_model_url?: string;
  oww_melspec_url?: string;
  oww_embedding_url?: string;
}

// resposta de POST /voice/session (chat que o modo voz usa)
export interface VoiceSession {
  chat_id: string;
  model: string;
  model_config_id: string;
  auto_speak: boolean;
  hands_free: boolean;
  continuous: boolean;
  follow_up_secs: number;
  call_name: string;
}

// ferramenta de sistema (embutida) exposta em /tools/system
export interface SystemTool {
  path: string;
  name: string;
  description: string;
  /** origem da ferramenta: nativa do app, do Codespace ou de uma integração */
  category?: "native" | "codespace" | "integration";
  /** nome da integração quando category === "integration" (ex.: "Google") */
  integration?: string;
}

export interface Prompt {
  id: string;
  command: string;
  title: string;
  content: string;
  enabled: boolean;
  created_at: string;
  updated_at: string;
}

// destino do resultado de uma automação
export interface AutomationTarget {
  mode: "new_each" | "reuse" | "existing";
  chat_id?: string | null;
  /** entrega do resultado ao WhatsApp (opcional) */
  whatsapp?: {
    enabled?: boolean;
    connection_id?: string | null;
    to?: "number" | "contacts" | "threads";
    number?: string;
  };
  /** entrega do resultado ao Telegram (opcional) */
  telegram?: {
    enabled?: boolean;
    connection_id?: string | null;
    mode?: "threads" | "chat";
    chat_id?: string;
  };
}

// agendamento de uma automação por tempo. Modos:
//   interval (default/legado): {every, unit}
//   between: {min, max, unit} — sorteia um intervalo aleatório na faixa a cada disparo
//   daily:   {time "HH:MM", tz_offset}
//   weekly:  {days [0-6 dom..sáb], time, tz_offset}
//   monthly: {day 1-31, time, tz_offset}
export interface AutomationSchedule {
  mode?: "interval" | "between" | "daily" | "weekly" | "monthly";
  every?: number;
  unit?: "minutes" | "hours" | "days";
  min?: number;
  max?: number;
  time?: string;
  days?: number[];
  day?: number;
  tz_offset?: number;
  at?: string;
  tz?: string;
}

// ------------------------------- WhatsApp ---------------------------------
export interface WhatsAppFilters {
  policy: "all" | "allow" | "block";
  allow: string[];
  block: string[];
  groups: boolean;
  trigger: string;
}

export interface TelegramConnection {
  id: string;
  label: string;
  bot_username: string;
  model_config_id: string | null;
  model: string;
  filters: { allow?: string[]; block?: string[]; groups?: boolean; trigger?: string };
  memory: "local" | "global";
  system_prompt: string;
  humanize: { enabled?: boolean; typing?: boolean; split?: boolean; min_seconds?: number; max_seconds?: number };
  /** janela de silencio (s) p/ juntar mensagens fragmentadas num turno; 0 = off */
  debounce_seconds?: number;
  /** quantas mensagens anteriores a IA enxerga; 0 = Tudo, ausente = padrao (40) */
  context_window?: number;
  enabled: boolean;
  state: { status?: string; last_error?: string | null };
  threads: number;
}

export interface DiscordConnection {
  id: string;
  label: string;
  bot_username: string;
  model_config_id: string | null;
  model: string;
  filters: { allow?: string[]; block?: string[]; guilds?: boolean; mention_only?: boolean; trigger?: string };
  memory: "local" | "global";
  system_prompt: string;
  humanize: { enabled?: boolean; typing?: boolean; split?: boolean; min_seconds?: number; max_seconds?: number };
  /** janela de silencio (s) p/ juntar mensagens fragmentadas num turno; 0 = off */
  debounce_seconds?: number;
  /** quantas mensagens anteriores a IA enxerga; 0 = Tudo, ausente = padrao (40) */
  context_window?: number;
  enabled: boolean;
  state: { status?: string; last_error?: string | null };
  threads: number;
}

export interface WhatsAppConnection {
  id: string;
  label: string;
  provider: "evolution" | "official";
  phone: string;
  model_config_id: string | null;
  model: string;
  filters: WhatsAppFilters;
  memory: "local" | "global";
  /** prompt adicional deste número, concatenado ao system prompt do modelo */
  system_prompt: string;
  /** limites de mensagens por contato (0/ausente = sem limite) */
  limits: { total?: number; per_hour?: number; per_day?: number; per_month?: number };
  /** contexto/roles por número */
  contacts: { number: string; name: string; role: string; context: string }[];
  /** Modo humanizador: digitação simulada + quebra de mensagens */
  humanize: { enabled?: boolean; typing?: boolean; split?: boolean; min_seconds?: number; max_seconds?: number };
  /** janela de silencio (s) p/ juntar mensagens fragmentadas num turno; 0 = off */
  debounce_seconds?: number;
  /** quantas mensagens anteriores a IA enxerga; 0 = Tudo, ausente = padrao (40) */
  context_window?: number;
  enabled: boolean;
  state: { status?: string; last_error?: string | null };
  threads: number;
  phone_number_id: string;
  has_token: boolean;
  verify_token: string;
  webhook_path: string;
  created_at: string;
}

// opções do turno/chat da automação
export interface AutomationOptions {
  use_context?: boolean;
  /** horas (número) ou "view_once" (apaga ao abrir e sair) */
  chat_ttl?: number | "view_once" | null;
  /** nível de raciocínio (thinking) do turno; ausente = padrão do modelo */
  reasoning?: "off" | "low" | "medium" | "high" | null;
}

export interface Automation {
  id: string;
  title: string;
  kind: "scheduled" | "monitor" | "reminder";
  enabled: boolean;
  model_config_id: string | null;
  model: string;
  instructions: string;
  tool_ids: string[];
  pinned_tool_ids: string[];
  target: AutomationTarget;
  options: AutomationOptions;
  schedule: AutomationSchedule;
  watcher_type: string | null;
  watcher_config: Record<string, any>;
  interval_seconds: number | null;
  next_run_at: string | null;
  last_run_at: string | null;
  run_count: number;
  fail_count: number;
  last_error: string | null;
  created_at: string;
  updated_at: string;
}

/** uma execução do histórico de uma automação */
export interface AutomationRun {
  id: string;
  status: "ok" | "error" | "no_change" | "skipped";
  trigger: "scheduled" | "manual";
  text: string | null;
  error: string | null;
  chat_id: string | null;
  message_id: string | null;
  cost: number | null;
  created_at: string;
}

export interface AppNotification {
  id: string;
  automation_id: string | null;
  title: string;
  body: string;
  chat_id: string | null;
  message_id: string | null;
  read: boolean;
  created_at: string;
}

// primitiva "kind:ask": qualquer ferramenta retorna isto e a UI mostra um seletor
export interface AskOption {
  label: string;
  value: string;
  hint?: string;
}
export interface AskSpec {
  question: string;
  options: AskOption[];
  allow_custom: boolean;
  custom_label?: string;
}

// eventos SSE emitidos pelo orchestrator do backend
export type ChatEvent =
  | { type: "memory"; count: number }
  | { type: "token"; text: string }
  | { type: "tool_call"; name: string; arguments: Record<string, unknown> }
  | { type: "tool_result"; name: string; result: unknown }
  | { type: "usage"; usage: Record<string, unknown> }
  | { type: "done"; content: string; usage?: MessageUsage | null; tool_events?: ToolEvent[] | null }
  | { type: "reasoning"; text: string }
  // provider recusou o nível de raciocínio pedido; o backend rebaixou (o seletor reflete)
  | { type: "reasoning_effort"; effort: string }
  | { type: "title"; title: string }
  | { type: "image_gen"; status: "start" | "error"; prompt?: string }
  | { type: "knowledge"; status: "start"; query?: string }
  | { type: "audio_router"; status: "start" | "done"; engine?: string; count?: number }
  // mesa-redonda: início/fim da fala de um participante + fim da rodada
  | { type: "speaker_start"; speaker: Speaker }
  | { type: "speaker_end"; speaker: Speaker; message_id: string | null }
  | { type: "roundtable_done"; reason?: string }
  | { type: "roundtable_paused" }
  | { type: "idle" }
  // geração interrompida pelo botão "Parar" (o parcial é persistido)
  | { type: "stopped" }
  // artefatos criados/atualizados ao persistir a resposta
  | { type: "artifacts"; ids: string[] }
  | { type: "error"; message: string };

// ------------------------------- Artefatos --------------------------------
export type ChatArtifactKind =
  | "code" | "markdown" | "html" | "svg" | "mermaid" | "json" | "csv" | "text";

export interface ChatArtifact {
  id: string;
  chat_id: string;
  identifier: string;
  title: string;
  kind: ChatArtifactKind;
  language: string;
  content: string;
  version: number;
  updated_at: string | null;
}

export interface ChatArtifactVersion {
  version: number;
  label: "ai" | "user" | "restore";
  size: number;
  created_at: string | null;
}

// Codespace: projeto (repositório clonado + grafo de código)
export interface CodespaceStats {
  files?: number;
  symbols?: number;
  edges?: number;
  edges_resolved?: number;
  edges_dangling?: number;
  index_seconds?: number;
  refined?: boolean;
  refine_promoted?: number;
  by_language?: Record<string, number>;
  /** só quando a indexação deu ZERO arquivos: extensões achadas que o grafo não lê */
  unsupported_ext?: string[];
}

export interface CodespaceProject {
  id: string;
  name: string;
  source: "git" | "git-ssh" | "local" | "folder";
  repo_url: string;
  branch: string;
  github_account_id: string | null;
  ssh_public_key: string | null;
  memory_bank_id: string | null;
  /** modelo padrão dos novos chats do projeto ("custom:<id>" ou modelo base); nulo = padrão do usuário */
  default_model: string | null;
  scope: { allow?: string[]; deny?: string[] };
  index_status: "pending" | "cloning" | "indexing" | "ready" | "error";
  error_message: string | null;
  stats: CodespaceStats | null;
  last_indexed_at: string | null;
  /** sandbox de execução (tool code.exec.run) */
  setup_command?: string;
  test_command?: string;
  exec_enabled?: boolean;
  /** source="folder": diretório existente no host */
  local_path?: string | null;
}

/** Tarefa isolada (worktree) — trabalho de um agente numa branch própria, a revisar */
export interface CodespaceTask {
  id: string;
  title: string;
  agent: string;
  branch: string;
  base_branch: string;
  status: "running" | "awaiting_review" | "merged" | "discarded" | "error";
  diff_stat: { files?: number; insertions?: number; deletions?: number };
  test_status: "pass" | "fail" | null;
  error: string | null;
  created_at: string | null;
}

export interface CodespaceChatLite {
  id: string;
  title: string;
  updated_at: string;
  archived: boolean;
}

export interface CodespaceFileEntry {
  path: string;
  kind: "dir" | "file";
}

export interface CodespaceFileList {
  entries?: CodespaceFileEntry[];
  error?: string;
}

export interface CodespaceFileSearch {
  entries?: CodespaceFileEntry[];
  truncated?: boolean;
  error?: string;
}

export interface CodespaceFileContent {
  path?: string;
  total_lines?: number;
  start_line?: number;
  end_line?: number;
  content?: string;
  error?: string;
}

export interface CodespaceSymbol {
  fqn: string | null;
  kind: string | null;
  path: string | null;
  line: number | null;
  end_line?: number | null;
  signature: string | null;
  doc: string | null;
}

export interface CodespaceFindResult {
  symbols: CodespaceSymbol[];
  warnings: string[];
}

export interface CodespaceEgoEdge {
  fqn: string | null;
  path: string | null;
  line: number | null;
  confidence: string | null;
}

export interface CodespaceEgoChild {
  name: string;
  kind: string | null;
  line: number | null;
}

export interface CodespaceEgo {
  symbol: CodespaceSymbol | null;
  children: CodespaceEgoChild[];
  calls: CodespaceEgoEdge[];
  called_by: CodespaceEgoEdge[];
  warnings: string[];
  error?: string;
}

// Grafo do projeto inteiro (visão geral estilo Obsidian) — GET /graph/visualize
export interface CodespaceVizNode {
  id: number;
  label: string;
  domain: number | null;
  weight: number;
  n: number;
}

export interface CodespaceVizLink {
  source: number;
  target: number;
  w: number;
}

export interface CodespaceViz {
  level: string;
  nodes: CodespaceVizNode[];
  links: CodespaceVizLink[];
  domains: { id: number; size: number; label: string | null }[];
  warnings: string[];
}

// ---- API pública (chaves, uso) -------------------------------------------- #
export interface ApiKey {
  id: string;
  name: string;
  masked: string;
  state: "active" | "disabled" | "revoked" | "expired";
  scopes: string[];
  model_policy: { mode?: "all" | "allow"; ids?: string[]; default?: string };
  limits: Record<string, number>;
  memory: { mode?: string; ttl_days?: number; max_items?: number };
  ip_allowlist: string[];
  webhook: { url: string; events: string[]; has_secret: boolean };
  enabled: boolean;
  expires_at: string | null;
  last_used_at: string | null;
  last_used_ip: string;
  created_at: string | null;
  cost_month: number;
  requests_month: number;
}

export interface ApiKeyMeta {
  scopes: string[];
  default_scopes: string[];
  memory_modes: string[];
  webhook_events: string[];
  models: { id: string; name: string; base_model: string }[];
}

export interface ApiKeyRequest {
  id: string;
  endpoint: string;
  model: string;
  status: number;
  error: string;
  latency_ms: number;
  total_tokens: number;
  cost: number;
  ip: string;
  created_at: string | null;
}

export interface ApiKeyUsage {
  totals: {
    requests: number;
    prompt_tokens: number;
    completion_tokens: number;
    cost: number;
    avg_latency_ms: number;
    errors: number;
  };
  daily: { date: string; requests: number; cost: number; tokens: number }[];
  by_model: { model: string; requests: number; tokens: number; cost: number }[];
}

// ---- Observabilidade (traces/spans) --------------------------------------- #
export interface ObsTrace {
  id: string;
  name: string;
  kind: string;
  method: string;
  path: string;
  status: "ok" | "error";
  status_code: number;
  error: string;
  started_at: string | null;
  duration_ms: number;
  span_count: number;
  db_ms: number;
  db_queries: number;
  http_ms: number;
  llm_ms: number;
  user_id: string | null;
  attrs: Record<string, unknown>;
}

export interface ObsSpan {
  id: string;
  parent_id: string | null;
  name: string;
  kind: string;
  offset_ms: number;
  duration_ms: number;
  status: "ok" | "error";
  error: string;
  db_reads: number;
  db_writes: number;
  db_ms: number;
  http_ms: number;
  attrs: Record<string, unknown>;
}

export interface ObsSummary {
  period_hours: number;
  totals: {
    traces: number; errors: number; avg_ms: number;
    p50_ms: number; p95_ms: number; p99_ms: number;
    db_queries: number; avg_db_ms: number; llm_ms: number;
  };
  routes: {
    kind: string; method: string; path: string; count: number; errors: number;
    p95_ms: number; avg_ms: number; avg_db_ms: number; avg_queries: number;
  }[];
  series: { hour: string; count: number; errors: number; avg_ms: number }[];
}

export interface ObsConfig {
  enabled: boolean;
  sample_rate: number;
  retention_days: number;
  max_traces: number;
  capture_content: boolean;
  slow_ms: number;
  sink: { queued: number; dropped: number; written: number; running: boolean };
}
