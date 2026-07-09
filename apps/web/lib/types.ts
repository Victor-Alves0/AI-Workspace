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
}

export interface Chat {
  id: string;
  title: string;
  model: string;
  system_prompt: string | null;
  params: Record<string, unknown>;
  archived: boolean;
  pinned: boolean;
  /** "Duração do Chat" (automações): apagado quando o usuário abre e sai */
  view_once?: boolean;
  folder_id: string | null;
  model_config_id: string | null;
  /** memória por-chat (null = herda do modelo/perfil) */
  memory_config?: MemoryConfig | null;
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
  write?: "global" | "model" | "chat" | "off";
  read?: { global?: boolean; model?: boolean; chat?: boolean };
  /** revisar antes de salvar (padrão do perfil): novas memórias ficam pendentes */
  review?: boolean;
}

export interface MemoryItem {
  id: string;
  text: string;
  scope: "global" | "model" | "chat";
  disabled?: boolean;
  model_id?: string | null;
  model_name?: string | null;
  chat_id?: string | null;
  chat_title?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface MemoryScopes {
  global: number;
  total: number;
  models: { id: string; name: string; count: number }[];
  chats: { id: string; title: string; count: number }[];
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
    user: number;
    context: number;
    file: number;
    system: number;
    memory: number;
    tools: number;
    tool_results: number;
  };
  output_breakdown?: { output: number; thinking: number };
}

export interface ToolEvent {
  kind: "call" | "result";
  name: string;
  data: unknown;
}

// anexo de mensagem: imagem (data URL), arquivo de texto, ou doc p/ extração
export interface Attachment {
  type: "image" | "file";
  name: string;
  url?: string; // imagem: data URL
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

// ferramenta de sistema (embutida) exposta em /tools/system
export interface SystemTool {
  path: string;
  name: string;
  description: string;
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
}

// agendamento de uma automação por tempo. Modos:
//   interval (default/legado): {every, unit}
//   daily:   {time "HH:MM", tz_offset}
//   weekly:  {days [0-6 dom..sáb], time, tz_offset}
//   monthly: {day 1-31, time, tz_offset}
export interface AutomationSchedule {
  mode?: "interval" | "daily" | "weekly" | "monthly";
  every?: number;
  unit?: "minutes" | "hours" | "days";
  time?: string;
  days?: number[];
  day?: number;
  tz_offset?: number;
  at?: string;
  tz?: string;
}

// opções do turno/chat da automação
export interface AutomationOptions {
  use_context?: boolean;
  /** horas (número) ou "view_once" (apaga ao abrir e sair) */
  chat_ttl?: number | "view_once" | null;
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
  | { type: "done"; content: string; usage?: MessageUsage | null }
  | { type: "reasoning"; text: string }
  | { type: "title"; title: string }
  | { type: "image_gen"; status: "start" | "error"; prompt?: string }
  // mesa-redonda: início/fim da fala de um participante + fim da rodada
  | { type: "speaker_start"; speaker: Speaker }
  | { type: "speaker_end"; speaker: Speaker; message_id: string }
  | { type: "roundtable_done"; reason?: string }
  | { type: "roundtable_paused" }
  | { type: "idle" }
  | { type: "error"; message: string };
