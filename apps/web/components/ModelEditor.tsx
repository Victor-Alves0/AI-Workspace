"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { ArrowLeft, BookOpen, Box, Brain, Camera, ChevronDown, ChevronRight, FileText, Gauge, Info, Pin, Plus, Search, Settings, ShieldAlert, Sliders, Sparkles, Trash2, Users, Volume2, Wrench, X } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { fileToAvatarDataUrl } from "@/lib/image";
import type { KnowledgeBase, MemoryBank, Model, ModelConfig, Skill, SystemTool, Tool } from "@/lib/types";
import TransferModal, { type TransferItem } from "./TransferModal";
import ModelField from "./ModelField";
import { Toggle } from "./ui";
import { WebSearchPanel, FinancePanel, TextExtractionPanel, DeepSearchPanel, GooglePanel, TuyaToolPanel, GithubToolPanel, MessagingToolPanel } from "./toolPanels";

// ferramentas internas com painel de config (engrenagem em "Ferramentas Ativas")
const TOOL_CFG: Record<string, { key: string; Panel: (p: any) => JSX.Element; needsStatus: boolean }> = {
  "builtin:web.search.query": { key: "web_search", Panel: WebSearchPanel, needsStatus: true },
  "builtin:finance.quote.get": { key: "finance", Panel: FinancePanel, needsStatus: true },
  "builtin:research.deep.run": { key: "deep_search", Panel: DeepSearchPanel, needsStatus: false },
  // Gmail e Agenda compartilham o MESMO painel (contas + ativação + prefs), key "google"
  "builtin:google.gmail.mailbox": { key: "google", Panel: GooglePanel, needsStatus: false },
  "builtin:google.calendar.events": { key: "google", Panel: GooglePanel, needsStatus: false },
  "builtin:smartlife.tuya.devices": { key: "tuya", Panel: TuyaToolPanel, needsStatus: false },
  "builtin:github.repo.manage": { key: "github", Panel: GithubToolPanel, needsStatus: false },
  "builtin:messaging.chat.manage": { key: "messaging", Panel: MessagingToolPanel, needsStatus: false },
};

/** Ícone de info com tooltip no hover — ao lado dos títulos de configuração.
 *  (Os textos são placeholders; ajuste conforme necessário.) */
function InfoHint({ text }: { text: string }) {
  return (
    <span className="group/hint relative inline-flex align-middle">
      <Info size={13} className="cursor-help text-muted transition-colors group-hover/hint:text-ink" />
      <span className="pointer-events-none absolute left-1/2 top-5 z-50 w-64 -translate-x-1/2 rounded-lg border border-border bg-surface2 px-3 py-2 text-xs font-normal normal-case leading-5 tracking-normal text-ink-soft opacity-0 shadow-menu transition-opacity duration-150 group-hover/hint:opacity-100">
        {text}
      </span>
    </span>
  );
}

// ---------------------------------------------------------------------------
// Guardas de saída (filtro output_guard): LISTA de guardas, cada um com nome,
// detecção (recusa / vazia / regex) e reação (reforçar system prompt ou trocar de
// modelo). Guardado em filter_config.output_guard.guards.
// ---------------------------------------------------------------------------
type Guard = {
  id: string;
  name: string;
  enabled: boolean;
  detect: "refusal" | "empty" | "regex" | "judge";
  pattern?: string;
  min_len?: number;
  judge_model?: string;
  criterion?: string;
  action: "reinforce" | "fallback_model";
  inject_text?: string;
  fallback_model?: string;
  max_retries: number;
};

function newGuard(): Guard {
  const rid = typeof crypto !== "undefined" && crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`;
  return { id: rid, name: "Guarda de saída", enabled: true, detect: "refusal", action: "reinforce", inject_text: "", max_retries: 1 };
}

const selCls = "w-full rounded-lg border border-border bg-surface2 px-2 py-1.5 text-sm text-ink outline-none transition-colors focus:border-accent";
const inpCls = "w-full rounded-lg border border-border bg-surface2 px-3 py-1.5 text-sm text-ink outline-none transition-colors focus:border-accent";

/** Bloco de uma categoria: divisor no topo + título + (opcional) ação à direita.
 *  Dá ritmo visual claro ao editor, separando cada categoria de config. */
function Section({
  title, icon, hint, right, children, first,
}: {
  title: string;
  icon?: React.ReactNode;
  hint?: string;
  right?: React.ReactNode;
  children: React.ReactNode;
  first?: boolean;
}) {
  return (
    <section className={first ? "" : "mt-8 border-t border-border pt-7"}>
      <div className="mb-3 flex items-center justify-between gap-3">
        <h2 className="flex items-center gap-2 text-sm font-semibold text-ink">
          {icon && <span className="text-muted">{icon}</span>}
          {title}
          {hint && <InfoHint text={hint} />}
        </h2>
        {right}
      </div>
      {children}
    </section>
  );
}

/** Botão "Gerenciar/Configurar" padrão no canto direito de uma Section. */
function ManageBtn({ icon, label, onClick }: { icon: React.ReactNode; label: string; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className="flex shrink-0 items-center gap-1.5 rounded-full border border-border px-3 py-1 text-xs text-ink-soft transition-colors hover:bg-hover hover:text-ink"
    >
      {icon} {label}
    </button>
  );
}

/** Seletor de voz: mostra o provedor ativo (Voz Local ou global) e deixa escolher
 *  a voz — dropdown quando há vozes da conexão local, ou texto livre (alloy…). O
 *  campo de mistura (avançado) permite combinar vozes com pesos. */
function VoicePicker({
  voices, provider, value, onChange,
}: {
  voices: string[];
  provider: { configured: boolean; enabled: boolean; base_url?: string } | null;
  value: string;
  onChange: (v: string) => void;
}) {
  const hasLocal = !!(provider?.configured && provider?.enabled && voices.length);
  const isMix = /[+()]/.test(value); // ex.: af_bella(2)+af_sky(1)
  const [mixOpen, setMixOpen] = useState(isMix);
  const [q, setQ] = useState("");
  const shown = q ? voices.filter((v) => v.toLowerCase().includes(q.toLowerCase())) : voices;

  return (
    <div className="space-y-2.5 rounded-xl border border-border bg-surface p-3">
      {/* linha de status do provedor */}
      <div className="flex items-center gap-2 text-xs">
        <span className={`h-2 w-2 shrink-0 rounded-full ${hasLocal ? "bg-emerald-400" : provider?.configured ? "bg-amber-400" : "bg-muted"}`} />
        <span className="text-ink-soft">
          {hasLocal
            ? `Voz Local conectada · ${voices.length} vozes`
            : provider?.configured
              ? "Voz Local configurada (desativada)"
              : "Provedor global (OpenAI)"}
        </span>
        <span className="ml-auto text-muted">Provedor em Configurações → Voz</span>
      </div>

      {hasLocal && !mixOpen ? (
        <>
          {voices.length > 8 && (
            <div className="relative">
              <Search size={13} className="pointer-events-none absolute left-2.5 top-2 text-muted" />
              <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Buscar voz…" className="w-full rounded-lg border border-border bg-surface2 py-1.5 pl-8 pr-3 text-xs text-ink outline-none focus:border-accent placeholder:text-muted" />
            </div>
          )}
          <div className="flex max-h-40 flex-wrap gap-1.5 overflow-y-auto pr-1">
            {shown.map((v) => (
              <button
                key={v}
                onClick={() => onChange(v)}
                className={`rounded-full border px-3 py-1 text-xs transition-colors ${value === v ? "border-accent/40 bg-accent/15 text-accent-hover" : "border-border bg-surface2 text-muted hover:text-ink"}`}
              >
                {v}
              </button>
            ))}
            {shown.length === 0 && <p className="px-1 py-1 text-xs text-muted">Nenhuma voz corresponde à busca.</p>}
          </div>
        </>
      ) : (
        <input
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={hasLocal ? "ex.: af_bella(2)+af_sky(1)" : "ex.: alloy, echo, shimmer"}
          className={inpCls}
        />
      )}

      {hasLocal && (
        <button onClick={() => setMixOpen((v) => !v)} className="text-xs text-muted transition-colors hover:text-ink">
          {mixOpen ? "← Escolher da lista" : "Misturar vozes (avançado)"}
        </button>
      )}
    </div>
  );
}

function OutputGuards({ value, onChange, baseModels }: { value: any; onChange: (g: Guard[]) => void; baseModels: Model[] }) {
  const guards: Guard[] = Array.isArray(value) ? value : [];
  const update = (id: string, patch: Partial<Guard>) => onChange(guards.map((g) => (g.id === id ? { ...g, ...patch } : g)));
  const remove = (id: string) => onChange(guards.filter((g) => g.id !== id));

  return (
    <div className="space-y-2">
      <p className="text-xs text-muted">
        Inspeciona a resposta e, se ela casar com uma condição (recusa, vazia, ou um padrão), <span className="text-ink-soft">reage</span>:
        reforça o system prompt e refaz, ou cai para outro modelo. Dá para ter vários, um para cada caso.
      </p>
      {guards.map((g) => (
        <div key={g.id} className="space-y-2 rounded-lg border border-border bg-surface p-2.5">
          <div className="flex items-center gap-2">
            <ShieldAlert size={13} className="shrink-0 text-accent-hover" />
            <input
              value={g.name}
              onChange={(e) => update(g.id, { name: e.target.value })}
              placeholder="Nome (para você identificar)"
              className="min-w-0 flex-1 rounded-md border border-transparent bg-transparent px-1 py-0.5 text-sm font-medium text-ink outline-none hover:border-border focus:border-accent"
            />
            <Toggle on={g.enabled} onChange={(v) => update(g.id, { enabled: v })} />
            <button onClick={() => remove(g.id)} title="Remover guarda" className="rounded-md p-1 text-muted transition-colors hover:text-red-300">
              <Trash2 size={13} />
            </button>
          </div>

          <div className="grid grid-cols-[1fr_auto] gap-2">
            <label className="space-y-1">
              <span className="text-[11px] text-muted">Quando</span>
              <select value={g.detect} onChange={(e) => update(g.id, { detect: e.target.value as Guard["detect"] })} className={selCls}>
                <option value="refusal">O modelo recusar</option>
                <option value="empty">Resposta vazia</option>
                <option value="regex">Casar um padrão (regex)</option>
                <option value="judge">Juiz (LLM) avaliar</option>
              </select>
            </label>
            <label className="space-y-1">
              <span className="text-[11px] text-muted">Tentativas</span>
              <input
                type="number" min={1} max={3} value={g.max_retries}
                onChange={(e) => update(g.id, { max_retries: Math.max(1, Math.min(3, Number(e.target.value) || 1)) })}
                className="w-20 rounded-lg border border-border bg-surface2 px-2 py-1.5 text-sm text-ink outline-none focus:border-accent"
              />
            </label>
          </div>
          {g.detect === "regex" && (
            <input value={g.pattern || ""} onChange={(e) => update(g.id, { pattern: e.target.value })} placeholder="expressão regular (ex.: não posso|i can't)" className={inpCls} />
          )}
          {g.detect === "empty" && (
            <input type="number" min={0} value={g.min_len ?? 0} onChange={(e) => update(g.id, { min_len: Math.max(0, Number(e.target.value) || 0) })} placeholder="tamanho mínimo (0 = qualquer resposta vazia)" className={inpCls} />
          )}
          {g.detect === "judge" && (
            <div className="space-y-1.5">
              <ModelField models={baseModels} value={g.judge_model || ""} onChange={(v) => update(g.id, { judge_model: v })} placeholder="Modelo que avalia (juiz)…" />
              <textarea
                value={g.criterion || ""}
                onChange={(e) => update(g.id, { criterion: e.target.value })}
                placeholder="Critério: descreva quando o guarda deve agir (ex.: 'se a resposta recusar, fugir do tema, ou vier em outro idioma'). O juiz responde SIM/NÃO."
                rows={2}
                className="w-full resize-y rounded-lg border border-border bg-surface2 px-3 py-1.5 text-sm text-ink outline-none focus:border-accent"
              />
              <p className="text-[11px] text-muted">O juiz é um modelo chamado a cada resposta — mais robusto, mas gasta tokens e adiciona latência.</p>
            </div>
          )}

          <label className="block space-y-1">
            <span className="text-[11px] text-muted">Reação</span>
            <select value={g.action} onChange={(e) => update(g.id, { action: e.target.value as Guard["action"] })} className={selCls}>
              <option value="reinforce">Reforçar o system prompt e refazer</option>
              <option value="fallback_model">Trocar para outro modelo</option>
            </select>
          </label>
          {g.action === "reinforce" ? (
            <textarea
              value={g.inject_text || ""}
              onChange={(e) => update(g.id, { inject_text: e.target.value })}
              placeholder="Texto injetado no fim do system prompt na re-tentativa (ex.: instruções/permissões que reforcem o comportamento desejado)"
              rows={2}
              className="w-full resize-y rounded-lg border border-border bg-surface2 px-3 py-1.5 text-sm text-ink outline-none focus:border-accent"
            />
          ) : (
            <ModelField models={baseModels} value={g.fallback_model || ""} onChange={(v) => update(g.id, { fallback_model: v })} placeholder="Modelo de fallback…" />
          )}
        </div>
      ))}
      <button onClick={() => onChange([...guards, newGuard()])} className="flex w-full items-center justify-center gap-1.5 rounded-lg border border-dashed border-border py-2 text-xs text-muted transition-colors hover:border-accent/40 hover:text-ink">
        <Plus size={13} /> Adicionar guarda
      </button>
    </div>
  );
}

// Prompt "quando usar" padrão do SIFT (modo "prompt"). Curto e barato — em vez
// de despejar o catálogo. Mantido em sincronia com o backend (orchestrator).
const DEFAULT_TOOL_PROMPT =
  "To avoid hallucinations: for ANY current/exact/private information " +
  "(user's data, time, weather, email, calendar, real-time data), ALWAYS use a " +
  "tool — never guess. If unsure whether a tool exists, call search_tools. Only " +
  "answer from your own knowledge for general/creative questions.";

// filtros disponíveis (espelham as caixas do OpenWebUI). Mais podem ser adicionados.
const FILTERS: { key: string; label: string }[] = [
  { key: "vision_router", label: "Vision Router" },
  { key: "audio_router", label: "Audio Router" },
  { key: "genimage_router", label: "GenImage Router" },
  { key: "output_guard", label: "Guarda de saída" },
];

const CAPS: { key: string; label: string }[] = [
  { key: "vision", label: "Visão" },
  { key: "file_upload", label: "Upload de Arquivos" },
  { key: "image_generation", label: "Geração de Imagens" },
  { key: "chat_context", label: "Contexto do Chat" },
  { key: "skill_learning", label: "Aprender skills (/learn)" },
];

// capacidades que vêm LIGADAS por padrão (ausência = ligada). Para desligá-las é
// preciso gravar explicitamente `false` (o orchestrator respeita chat_context).
const CAPS_DEFAULT_ON = new Set<string>(["chat_context", "skill_learning"]);

// memória por-modelo (guardada em capabilities.memory; null = herda do perfil).
// Ao personalizar, materializa com enabled:true (liga a memória p/ os chats deste
// modelo mesmo que o padrão do perfil esteja desligado).
type MemoryCfg = { enabled?: boolean; write?: string; read?: { global?: boolean; model?: boolean; chat?: boolean }; banks?: string[] };
const MEM_WRITE_OPTS = [
  { value: "global", label: "Global" },
  { value: "model", label: "Do modelo" },
  { value: "chat", label: "Só o chat" },
  { value: "off", label: "Não salvar" },
];
const MEM_READ_OPTS: { key: "global" | "model" | "chat"; label: string }[] = [
  { key: "global", label: "Global" },
  { key: "model", label: "Modelo" },
  { key: "chat", label: "Chat" },
];
const MEM_CFG_DEFAULT: Required<MemoryCfg> = {
  enabled: true, write: "global", read: { global: true, model: true, chat: true }, banks: [],
};

// Base de Conhecimento por-modelo (capabilities.knowledge). bases vazio = desligado.
type KnowledgeCfg = { bases?: string[]; mode?: "auto" | "tool"; k?: number };

// Cérebro por-modelo (capabilities.brain). brains vazio = desligado.
type BrainCfg = { brains?: string[]; write?: boolean; k?: number };

function slugify(s: string) {
  return s.toLowerCase().trim().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
}

/** Campo de seleção padrão: chips do selecionado + botão "Gerenciar" que abre
 *  um popup de transferência. Usado p/ Ferramentas, Capacidades e Filtros. */
function SelectorField({
  label,
  icon,
  selected,
  labelOf,
  onRemove,
  onManage,
  empty,
  hint,
}: {
  label: string;
  icon: React.ReactNode;
  selected: string[];
  labelOf: (k: string) => string;
  onRemove: (k: string) => void;
  onManage: () => void;
  empty: string;
  hint?: string;
}) {
  return (
    <div className="space-y-2">
      <div className="mb-3 flex items-center justify-between gap-3">
        <h2 className="flex items-center gap-2 text-sm font-semibold text-ink">
          {icon && <span className="text-muted">{icon}</span>}
          {label}
          {hint && <InfoHint text={hint} />}
        </h2>
        <button
          onClick={onManage}
          className="flex shrink-0 items-center gap-1.5 rounded-full border border-border px-3 py-1 text-xs text-ink-soft transition-colors hover:bg-hover hover:text-ink"
        >
          {icon} Gerenciar
        </button>
      </div>
      {selected.length === 0 ? (
        <p className="text-xs text-muted">{empty}</p>
      ) : (
        <div className="flex flex-wrap gap-1.5">
          {selected.map((k) => (
            <span key={k} className="flex items-center gap-1 rounded-full bg-surface2 px-2.5 py-0.5 text-xs text-ink">
              {labelOf(k)}
              <button onClick={() => onRemove(k)} className="text-muted transition-colors hover:text-ink">
                <X size={11} />
              </button>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

/** Janela (modal) centralizada para configurar uma ferramenta. Fecha no X, no
 *  Esc ou ao clicar no fundo. O conteúdo (Panel) rola internamente. */
function CfgModal({ title, onClose, children }: { title: string; onClose: () => void; children: React.ReactNode }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/60 p-4" onClick={onClose}>
      <div
        className="flex max-h-[85vh] w-full max-w-xl flex-col overflow-hidden rounded-2xl border border-border bg-bg shadow-menu"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-border px-5 py-3">
          <p className="truncate text-sm font-semibold text-ink">{title}</p>
          <button onClick={onClose} className="shrink-0 rounded-lg p-1 text-muted hover:bg-hover hover:text-ink">
            <X size={18} />
          </button>
        </div>
        <div className="overflow-y-auto px-5 py-4">{children}</div>
      </div>
    </div>
  );
}

export default function ModelEditor({
  model,
  onClose,
  onSaved,
}: {
  model: ModelConfig | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const isNew = model === null;
  const [baseModels, setBaseModels] = useState<Model[]>([]);
  const [tools, setTools] = useState<Tool[]>([]);
  const [systemTools, setSystemTools] = useState<SystemTool[]>([]);
  const [skills, setSkills] = useState<Skill[]>([]);

  const [name, setName] = useState(model?.name ?? "");
  // ID do modelo: editável. Vazio = derivado do nome (slugify). Ao editar, "trava".
  const [slug, setSlug] = useState(model?.slug ?? "");
  const [slugEdited, setSlugEdited] = useState(!!model?.slug);
  const [baseModel, setBaseModel] = useState(model?.base_model ?? "");
  const [description, setDescription] = useState(model?.description ?? "");
  const [systemPrompt, setSystemPrompt] = useState(model?.system_prompt ?? "");
  const [avatarUrl, setAvatarUrl] = useState(model?.avatar_url ?? "");
  const [paramsStr, setParamsStr] = useState(JSON.stringify(model?.params ?? {}, null, 2));
  const [caps, setCaps] = useState<Record<string, boolean>>(model?.capabilities ?? {});
  // memória POR-MODELO (padrão dos chats deste modelo): null = usa o padrão do perfil
  const [mem, setMem] = useState<MemoryCfg | null>(
    ((model?.capabilities as Record<string, unknown> | undefined)?.memory as MemoryCfg) ?? null,
  );
  const [tokenWarn, setTokenWarn] = useState<number | "">(
    (((model?.capabilities as Record<string, unknown> | undefined)?.token_warn) as number) ?? "",
  );
  // Subagentes: permissão de delegar (capability) + config (time/modo/limites em filter_config)
  const [subOn, setSubOn] = useState<boolean>((model?.capabilities as Record<string, unknown> | undefined)?.subagents === true);
  const [myModels, setMyModels] = useState<ModelConfig[]>([]);
  const [memBanks, setMemBanks] = useState<MemoryBank[]>([]);
  // Base de Conhecimento POR-MODELO (capabilities.knowledge): bases acopladas + modo
  const [kb, setKb] = useState<KnowledgeCfg>(
    ((model?.capabilities as Record<string, unknown> | undefined)?.knowledge as KnowledgeCfg) ?? { bases: [], mode: "auto", k: 6 },
  );
  const [kbBases, setKbBases] = useState<KnowledgeBase[]>([]);
  // Cérebro POR-MODELO (capabilities.brain): cérebros acoplados + escrita pela IA
  const [brainCfg, setBrainCfg] = useState<BrainCfg>(
    ((model?.capabilities as Record<string, unknown> | undefined)?.brain as BrainCfg) ?? { brains: [], write: true, k: 6 },
  );
  const [brainBases, setBrainBases] = useState<KnowledgeBase[]>([]);
  useEffect(() => { api.get<ModelConfig[]>("/models").then(setMyModels).catch(() => {}); }, []);
  useEffect(() => { api.get<MemoryBank[]>("/memory/banks").then(setMemBanks).catch(() => {}); }, []);
  useEffect(() => { api.get<KnowledgeBase[]>("/knowledge/bases").then(setKbBases).catch(() => {}); }, []);
  useEffect(() => { api.get<KnowledgeBase[]>("/knowledge/bases?kind=brain").then(setBrainBases).catch(() => {}); }, []);
  const [toolsEnabled, setToolsEnabled] = useState(model?.tools_enabled ?? false);
  const [toolIds, setToolIds] = useState<string[]>(model?.tool_ids ?? []);
  const [codeMode, setCodeMode] = useState(model?.code_mode ?? false);
  // config do SIFT: como expor as tools (modo) + prompt "quando usar" + fixadas
  const [siftMode, setSiftMode] = useState<"prompt" | "list">(model?.sift_config?.mode ?? "prompt");
  const [siftPrompt, setSiftPrompt] = useState(model?.sift_config?.prompt ?? DEFAULT_TOOL_PROMPT);
  const [pinnedIds, setPinnedIds] = useState<string[]>(model?.sift_config?.pinned ?? []);
  const [showSiftConfig, setShowSiftConfig] = useState(false);
  const [skillIds, setSkillIds] = useState<string[]>(model?.skill_ids ?? []);
  const [skillsModal, setSkillsModal] = useState(false);
  const [toolsModal, setToolsModal] = useState(false);
  const [capsModal, setCapsModal] = useState(false);
  const [filtersModal, setFiltersModal] = useState(false);
  // filtros vivem dentro de `capabilities` como chaves "filter:<key>" (dict[str,bool])
  const [filters, setFilters] = useState<string[]>(
    Object.keys(model?.capabilities ?? {})
      .filter((k) => k.startsWith("filter:") && (model!.capabilities as any)[k])
      .map((k) => k.slice("filter:".length)),
  );
  // parâmetros de cada filtro, ex.: { vision_router: { model: "<id>" } }
  const [filterConfig, setFilterConfig] = useState<Record<string, any>>(model?.filter_config ?? {});
  // qual filtro está com o painel de config (engrenagem) aberto
  const [openFilterCfg, setOpenFilterCfg] = useState<string | null>(null);
  // qual ferramenta interna está com o painel de config aberto + status dos segredos
  const [openToolCfg, setOpenToolCfg] = useState<string | null>(null);
  const [openExtraction, setOpenExtraction] = useState(false);
  // busca dentro das listas de itens (ferramentas / skills equipadas)
  const [toolSearch, setToolSearch] = useState("");
  const [skillSearch, setSkillSearch] = useState("");
  const [secretStatus, setSecretStatus] = useState<Record<string, boolean> | null>(null);
  // config POR-MODELO das ferramentas internas, guardada em filter_config.tools
  const toolsCfg: Record<string, any> = filterConfig.tools ?? {};
  const setToolCfg = (key: string, val: any) =>
    setFilterConfig((fc) => ({ ...fc, tools: { ...(fc.tools ?? {}), [key]: val } }));
  // config dos Subagentes (time/modo/limites) guardada em filter_config.subagents
  const subCfg: Record<string, any> = filterConfig.subagents ?? {};
  const setSubCfg = (patch: Record<string, any>) =>
    setFilterConfig((fc) => ({ ...fc, subagents: { ...(fc.subagents ?? {}), ...patch } }));
  const team: string[] = Array.isArray(subCfg.team) ? subCfg.team : [];
  const teamCandidates = useMemo(() => myModels.filter((m) => m.id !== model?.id), [myModels, model]);
  const [teamModal, setTeamModal] = useState(false);
  // itens do seletor de operários (TransferModal): nome + modelo-base como sublabel
  const teamItems: TransferItem[] = useMemo(
    () => teamCandidates.map((m) => ({ key: m.id, label: m.name, sublabel: m.base_model })),
    [teamCandidates],
  );
  const teamLabel = (id: string) => teamCandidates.find((m) => m.id === id)?.name ?? id;
  const [ttsVoice, setTtsVoice] = useState(model?.tts_voice ?? "");
  // vozes + status da conexão de Voz Local (Kokoro/clonagem) p/ o seletor de voz
  const [voices, setVoices] = useState<string[]>([]);
  const [voiceInfo, setVoiceInfo] = useState<{ configured: boolean; enabled: boolean; base_url?: string } | null>(null);
  useEffect(() => {
    api.get<{ configured: boolean; enabled: boolean; base_url?: string; voices?: string[] }>("/voice/config")
      .then((r) => { setVoiceInfo({ configured: r.configured, enabled: r.enabled, base_url: r.base_url }); setVoices(r.voices ?? []); })
      .catch(() => {});
  }, []);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const avatarFileRef = useRef<HTMLInputElement>(null);
  const [avatarErr, setAvatarErr] = useState<string | null>(null);

  async function pickAvatar(e: React.ChangeEvent<HTMLInputElement>) {
    setAvatarErr(null);
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    try {
      setAvatarUrl(await fileToAvatarDataUrl(file, 320));
    } catch (err) {
      setAvatarErr(err instanceof Error ? err.message : "Falha ao carregar imagem");
    }
  }

  const reloadSecrets = () => api.get<Record<string, boolean>>("/settings/secrets").then(setSecretStatus).catch(() => {});
  useEffect(() => {
    Promise.all([
      api.get<Model[]>("/settings/models").catch(() => [] as Model[]),
      api.get<Model[]>("/integrations/ollama/models").catch(() => [] as Model[]),
    ]).then(([ext, local]) => setBaseModels([...ext, ...local])).catch(() => {});
    api.get<Tool[]>("/tools").then(setTools).catch(() => {});
    api.get<SystemTool[]>("/tools/system").then(setSystemTools).catch(() => {});
    api.get<Skill[]>("/skills").then(setSkills).catch(() => {});
    reloadSecrets();
  }, []);

  const derivedId = slugify(name) || "modelo";
  // id efetivo: o que o usuário escreveu (se editou) ou o derivado do nome
  const id = slugEdited && slug.trim() ? slugify(slug) : derivedId;

  function toggleTool(tid: string) {
    setToolIds((ids) => (ids.includes(tid) ? ids.filter((x) => x !== tid) : [...ids, tid]));
  }
  function togglePin(tid: string) {
    setPinnedIds((ids) => (ids.includes(tid) ? ids.filter((x) => x !== tid) : [...ids, tid]));
  }
  // ao remover uma ferramenta (aqui ou no popup), tira o pin dela também
  useEffect(() => {
    setPinnedIds((ids) => ids.filter((id) => toolIds.includes(id)));
  }, [toolIds]);
  // ferramentas de sistema são gravadas como "builtin:<path>"
  const builtinKey = (path: string) => `builtin:${path}`;

  // itens do popup de transferência: sistema (builtin:<path>) + do usuário (uuid)
  const transferItems: TransferItem[] = useMemo(
    () => [
      ...systemTools.map((st) => ({
        key: builtinKey(st.path),
        label: st.name,
        sublabel: st.description,
        group: "Sistema",
        system: true,
      })),
      ...tools.map((t) => ({
        key: t.id,
        label: t.name || t.path,
        sublabel: t.path,
        group: "Usuário",
      })),
    ],
    [systemTools, tools],
  );
  const toolLabel = (key: string) =>
    transferItems.find((i) => i.key === key)?.label ?? key;
  // ferramentas ativas visíveis (após a busca da lista)
  const toolQuery = toolSearch.trim().toLowerCase();
  const shownToolIds = toolQuery
    ? toolIds.filter((tid) => toolLabel(tid).toLowerCase().includes(toolQuery))
    : toolIds;

  // skills equipadas (uuid) — mesmo padrão de transferência das ferramentas
  const skillItems: TransferItem[] = useMemo(
    () =>
      skills.map((s) => ({
        key: s.id,
        label: s.name,
        sublabel: `$${s.slug}${s.description ? ` — ${s.description}` : ""}`,
      })),
    [skills],
  );
  const skillLabel = (key: string) => skillItems.find((i) => i.key === key)?.label ?? key;
  // skills equipadas visíveis (após a busca da lista)
  const skillQuery = skillSearch.trim().toLowerCase();
  const shownSkillIds = skillQuery
    ? skillIds.filter((sid) => skillLabel(sid).toLowerCase().includes(skillQuery))
    : skillIds;
  // remove skills que deixaram de existir/estar disponíveis
  useEffect(() => {
    if (!skills.length) return;
    setSkillIds((ids) => ids.filter((id) => skills.some((s) => s.id === id)));
  }, [skills]);

  // capacidades (real caps, sem as chaves "filter:") como itens de transferência
  const capItems: TransferItem[] = CAPS.map((c) => ({ key: c.key, label: c.label }));
  const capSelected = useMemo(() => {
    const sel = Object.keys(caps).filter((k) => caps[k] === true && !k.startsWith("filter:"));
    // default-on (ex.: chat_context): marcado quando ausente ou true; só some com false explícito
    for (const k of CAPS_DEFAULT_ON) {
      if (caps[k] !== false && !sel.includes(k)) sel.push(k);
    }
    return sel;
  }, [caps]);
  function setCapSelected(keys: string[]) {
    const next: Record<string, boolean> = {};
    for (const k of keys) next[k] = true;
    // default-on desmarcada → grava false explícito (p/ o toggle de fato desligar)
    for (const k of CAPS_DEFAULT_ON) if (!keys.includes(k)) next[k] = false;
    // preserva os filtros (chaves filter:) já marcados
    for (const [k, v] of Object.entries(caps)) if (k.startsWith("filter:") && v) next[k] = true;
    setCaps(next);
  }
  const filterItems: TransferItem[] = FILTERS.map((f) => ({ key: f.key, label: f.label }));

  async function save() {
    setErr(null);
    if (!baseModel) {
      setErr("Selecione um modelo base");
      return;
    }
    let params: unknown;
    try {
      params = JSON.parse(paramsStr || "{}");
    } catch {
      setErr("Parâmetros avançados devem ser JSON válido");
      return;
    }
    // junta capacidades reais + filtros (como chaves "filter:<key>") em um só dict
    const capabilities: Record<string, unknown> = {};
    for (const k of capSelected) capabilities[k] = true;
    // default-on desmarcada → false explícito (senão "ausente" seria tratado como on)
    for (const k of CAPS_DEFAULT_ON) if (!capSelected.includes(k)) capabilities[k] = false;
    // skill_learning marcada = modo AUTO (ausente): o servidor injeta a tool só
    // quando o turno já anuncia outras tools (True explícito forçaria `tools` num
    // modelo sem tool-calling e quebraria o request)
    if (capabilities.skill_learning === true) delete capabilities.skill_learning;
    for (const f of filters) capabilities[`filter:${f}`] = true;
    // memória por-modelo (objeto aninhado): só grava quando personalizada
    if (mem) capabilities.memory = mem;
    // base de conhecimento por-modelo: só grava quando há base(s) acoplada(s)
    if (kb.bases && kb.bases.length > 0) {
      capabilities.knowledge = { bases: kb.bases, mode: kb.mode || "auto", k: Number(kb.k) || 6 };
    }
    // cérebro por-modelo: só grava quando há cérebro(s) acoplado(s)
    if (brainCfg.brains && brainCfg.brains.length > 0) {
      capabilities.brain = {
        brains: brainCfg.brains, write: brainCfg.write !== false, k: Number(brainCfg.k) || 6,
      };
    }
    // aviso de uso alto por-modelo (override do perfil); vazio/0 = herda o perfil
    if (tokenWarn !== "" && Number(tokenWarn) > 0) capabilities.token_warn = Number(tokenWarn);
    // permissão de delegar a subagentes (capability)
    capabilities.subagents = subOn;
    // config dos filtros: só mantém a de filtros ativos (ex.: vision_router)
    const cleanFilterConfig: Record<string, unknown> = {};
    for (const f of filters) if (filterConfig[f]) cleanFilterConfig[f] = filterConfig[f];
    // preserva a config POR-MODELO das ferramentas internas (web/finance/deep/extração)
    if (filterConfig.tools) cleanFilterConfig.tools = filterConfig.tools;
    // config dos subagentes (time/modo/limites) — só quando a permissão está ligada
    if (subOn) {
      const sc = filterConfig.subagents ?? {};
      cleanFilterConfig.subagents = {
        team: Array.isArray(sc.team) ? sc.team : [],
        mode: sc.mode === "parallel" ? "parallel" : "sequential",
        max_calls: Math.max(1, Math.min(10, Number(sc.max_calls) || 4)),
        max_depth: Math.max(1, Math.min(3, Number(sc.max_depth) || 2)),
        pass_context: !!sc.pass_context,
        worker_memory: !!sc.worker_memory,
      };
    }

    const body = {
      base_model: baseModel,
      name: name || id,
      slug: slugEdited && slug.trim() ? slugify(slug) : null,
      description: description || null,
      avatar_url: avatarUrl || null,
      system_prompt: systemPrompt || null,
      params,
      capabilities,
      filter_config: cleanFilterConfig,
      tools_enabled: toolsEnabled,
      tool_ids: toolsEnabled ? toolIds : [],
      code_mode: toolsEnabled ? codeMode : false,
      sift_config: {
        mode: siftMode,
        prompt: siftMode === "prompt" ? siftPrompt : "",
        pinned: pinnedIds.filter((id) => toolIds.includes(id)),
      },
      skill_ids: skillIds,
      prompt_suggestions: [],
      tts_voice: ttsVoice || null,
      enabled: model?.enabled ?? true,
    };
    setSaving(true);
    try {
      if (isNew) await api.post("/models", body);
      else await api.patch(`/models/${model!.id}`, body);
      onSaved();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Falha ao salvar");
    } finally {
      setSaving(false);
    }
  }

  function Label({ children }: { children: React.ReactNode }) {
    return <p className="text-xs text-muted">{children}</p>;
  }

  return (
    <div className="flex h-full flex-1 flex-col bg-bg">
      {/* topo — barra fixa com voltar + título */}
      <div className="flex shrink-0 items-center gap-3 border-b border-border px-4 py-3 md:px-6">
        <button
          onClick={onClose}
          className="flex items-center gap-1.5 rounded-lg px-2 py-1 text-sm text-muted transition-colors hover:bg-hover hover:text-ink"
        >
          <ArrowLeft size={17} /> Voltar
        </button>
        <span className="text-sm font-medium text-ink">{isNew ? "Novo modelo" : "Editar modelo"}</span>
      </div>

      <div className="flex-1 overflow-y-auto px-4 py-5 md:px-6 md:py-6">
        <div className="mx-auto max-w-4xl">
          {/* cabeçalho: avatar + nome/id/base/descrição (mobile: empilhado) */}
          <div className="flex flex-col gap-5 sm:flex-row sm:gap-6">
            <div className="flex shrink-0 flex-col items-center gap-2">
              <input ref={avatarFileRef} type="file" accept="image/*" className="hidden" onChange={pickAvatar} />
              <button
                onClick={() => avatarFileRef.current?.click()}
                title="Enviar uma imagem"
                className="group relative flex h-28 w-28 items-center justify-center overflow-hidden rounded-2xl bg-surface2"
              >
                {avatarUrl ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={avatarUrl} alt="" className="h-full w-full object-cover" />
                ) : (
                  <span className="text-3xl font-bold text-muted">{(name || "M")[0]?.toUpperCase()}</span>
                )}
                <span className="absolute inset-0 flex items-center justify-center bg-black/50 opacity-0 transition-opacity group-hover:opacity-100">
                  <Camera size={20} className="text-white" />
                </span>
              </button>
              {avatarUrl && (
                <button onClick={() => setAvatarUrl("")} className="text-xs text-muted transition-colors hover:text-ink">
                  Remover imagem
                </button>
              )}
              {avatarErr && <p className="max-w-28 text-center text-[11px] text-red-400">{avatarErr}</p>}
            </div>

            <div className="min-w-0 flex-1 space-y-3">
              <div className="space-y-1">
                <Label>Nome do modelo</Label>
                <input
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="ex.: Mario Assistente"
                  className="w-full rounded-lg border border-border bg-surface px-3 py-2 text-xl font-semibold text-ink outline-none transition-colors focus:border-accent placeholder:font-normal placeholder:text-muted"
                />
              </div>

              <div className="space-y-1">
                <div className="flex items-center justify-between">
                  <Label>ID do modelo</Label>
                  {slugEdited && (
                    <button onClick={() => { setSlug(""); setSlugEdited(false); }} title="Voltar a derivar do nome" className="text-[11px] text-muted transition-colors hover:text-ink">
                      derivar do nome
                    </button>
                  )}
                </div>
                <div className="flex items-center gap-1.5 rounded-lg border border-border bg-surface px-3 py-2 transition-colors focus-within:border-accent">
                  <span className="font-mono text-sm text-muted">@</span>
                  <input
                    value={slugEdited ? slug : derivedId}
                    onChange={(e) => { setSlug(e.target.value); setSlugEdited(true); }}
                    placeholder={derivedId}
                    className="min-w-0 flex-1 bg-transparent font-mono text-sm text-ink-soft outline-none placeholder:text-muted"
                  />
                </div>
              </div>

              <div className="space-y-1">
                <Label>Modelo base</Label>
                <ModelField
                  models={baseModels}
                  value={baseModel}
                  onChange={setBaseModel}
                  placeholder="Selecione um modelo base"
                  className="flex w-full items-center justify-between gap-2 rounded-lg border border-border bg-surface px-3 py-2 text-sm text-ink outline-none transition-colors hover:border-accent/60"
                />
              </div>

              <div className="space-y-1">
                <Label>Descrição</Label>
                <input
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  placeholder="Uma frase curta sobre o que este modelo faz"
                  className="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm text-ink-soft outline-none transition-colors focus:border-accent placeholder:text-muted"
                />
              </div>
            </div>
          </div>

          {/* Comportamento: system prompt + parâmetros de geração */}
          <Section
            title="Comportamento"
            icon={<Sliders size={15} />}
            hint="Prompt do sistema e parâmetros de geração (temperature, top_p, max_tokens…) aplicados a este modelo em todos os chats."
          >
            <div className="space-y-2">
              <Label>Prompt do sistema</Label>
              <textarea
                rows={4}
                value={systemPrompt}
                onChange={(e) => setSystemPrompt(e.target.value)}
                placeholder="Como este modelo deve se comportar.&#10;ex.: Você é o Mario do Super Mario Bros e atua como assistente."
                className="w-full resize-y rounded-lg border border-border bg-surface px-3 py-2 text-sm text-ink outline-none transition-colors focus:border-accent placeholder:text-muted"
              />
              <button onClick={() => setShowAdvanced((v) => !v)} className="flex items-center gap-1 pt-1 text-sm text-muted transition-colors hover:text-ink">
                {showAdvanced ? <ChevronDown size={14} /> : <ChevronRight size={14} />} Parâmetros de geração (JSON)
              </button>
              {showAdvanced && (
                <textarea
                  rows={4}
                  value={paramsStr}
                  onChange={(e) => setParamsStr(e.target.value)}
                  placeholder='{ "temperature": 0.7, "top_p": 0.9, "max_tokens": 2048 }'
                  className="w-full rounded-lg border border-border bg-surface px-3 py-2 font-mono text-xs text-ink outline-none transition-colors focus:border-accent placeholder:text-muted"
                />
              )}
            </div>
          </Section>

          {/* Voz — provedor (Voz Local ou global) + voz usada no TTS */}
          <Section
            title="Voz"
            icon={<Volume2 size={15} />}
            hint="Voz usada quando este modelo fala (TTS). O provedor vem da sua conexão de Voz Local (Kokoro/clonagem) ou, na ausência dela, do provedor global. Configure a conexão em Configurações → Voz."
          >
            <VoicePicker voices={voices} provider={voiceInfo} value={ttsVoice} onChange={setTtsVoice} />
          </Section>

          {/* Ferramentas */}
          <div className="mt-8 space-y-3 border-t border-border pt-7">
            <h2 className="flex items-center gap-2 text-sm font-semibold text-ink">
              <span className="text-muted"><Wrench size={15} /></span>
              Ferramentas
              <InfoHint text="A chave-mestra SIFT libera o uso de ferramentas. O modelo descobre e executa as ferramentas ativas via as meta-ferramentas do SIFT (o 'como usar')." />
            </h2>

            {/* SIFT: chave-mestra. Sem ela, o modelo não usa nenhuma ferramenta. */}
            <div className="flex items-center justify-between gap-3 rounded-xl border border-border bg-surface px-4 py-3">
              <span>
                <span className="block text-sm font-medium text-ink">SIFT</span>
                <span className="block text-xs text-muted">
                  Ative para permitir que este modelo use ferramentas (busca via meta-tools do SIFT).
                  Sem seleção, nenhuma ferramenta é usada.
                </span>
              </span>
              <div className="flex shrink-0 items-center gap-1.5">
                {toolsEnabled && (
                  <button
                    onClick={() => setShowSiftConfig((v) => !v)}
                    title="Configurar como o SIFT é usado"
                    className={`rounded-lg p-1.5 transition-colors ${showSiftConfig ? "bg-hover text-ink" : "text-muted hover:bg-hover hover:text-ink"}`}
                  >
                    <Settings size={16} />
                  </button>
                )}
                <Toggle on={toolsEnabled} onChange={setToolsEnabled} />
              </div>
            </div>

            {toolsEnabled && (
              <div className="space-y-3 pl-1">
                {/* Configuração de como o SIFT é apresentado ao modelo (engrenagem) */}
                {showSiftConfig && (
                  <div className="space-y-3 rounded-xl border border-border bg-surface px-4 py-3">
                    <p className="text-xs font-semibold uppercase tracking-wide text-muted">Como o SIFT é usado</p>
                    <p className="text-xs text-muted">
                      O SIFT resolve <span className="text-ink-soft">como</span> usar (descoberta via
                      meta-tools). Aqui você define <span className="text-ink-soft">quando</span> usar.
                    </p>
                    <div className="space-y-2.5">
                      <label className="flex cursor-pointer items-start gap-2.5">
                        <input type="radio" name="siftmode" checked={siftMode === "prompt"} onChange={() => setSiftMode("prompt")} className="mt-0.5 accent-accent" />
                        <span>
                          <span className="block text-sm text-ink">
                            Prompt de ferramentas (quando usar) <span className="text-accent-hover">· recomendado</span>
                          </span>
                          <span className="block text-xs text-muted">Injeta uma instrução curta de quando recorrer a ferramentas — barato.</span>
                        </span>
                      </label>
                      <label className="flex cursor-pointer items-start gap-2.5">
                        <input type="radio" name="siftmode" checked={siftMode === "list"} onChange={() => setSiftMode("list")} className="mt-0.5 accent-accent" />
                        <span>
                          <span className="block text-sm text-ink">Injetar lista de ferramentas</span>
                          <span className="block text-xs text-muted">Coloca nomes/descrições das tools no prompt — explícito, porém mais caro.</span>
                        </span>
                      </label>
                    </div>
                    {siftMode === "prompt" && (
                      <div className="space-y-1">
                        <div className="flex items-center justify-between">
                          <p className="text-xs text-muted">Prompt &quot;quando usar&quot;</p>
                          <button onClick={() => setSiftPrompt(DEFAULT_TOOL_PROMPT)} className="text-xs text-muted transition-colors hover:text-ink">
                            Restaurar padrão
                          </button>
                        </div>
                        <textarea
                          rows={5}
                          value={siftPrompt}
                          onChange={(e) => setSiftPrompt(e.target.value)}
                          className="w-full resize-y rounded-lg border border-border bg-bg px-3 py-2 text-xs text-ink outline-none focus:border-accent"
                        />
                      </div>
                    )}
                  </div>
                )}

                {/* Code mode: o modelo orquestra as ferramentas escrevendo Python */}
                <div className="flex items-center justify-between gap-3 rounded-xl border border-border bg-surface px-4 py-3">
                  <span>
                    <span className="block text-sm font-medium text-ink">Modo Código</span>
                    <span className="block text-xs text-muted">
                      Permite o modelo orquestrar várias ferramentas em uma única chamada
                      (sandbox). Ideal p/ modelos fortes em código.
                    </span>
                  </span>
                  <Toggle on={codeMode} onChange={setCodeMode} />
                </div>

                {/* Ferramentas do modelo. Fixar (pin) fica dentro do "Gerenciar":
                    a fixada vira spec de 1ª classe (o modelo chama direto, sem busca). */}
                <div className="space-y-2">
                  <div className="flex items-center justify-between">
                    <p className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-muted">
                      Ferramentas ativas
                      <InfoHint text="Ferramentas que este modelo pode usar. Dentro de “Gerenciar”, fixe (pin) as mais quentes para virarem chamadas diretas, sem round-trip de descoberta." />
                    </p>
                    <button
                      onClick={() => setToolsModal(true)}
                      className="flex items-center gap-1.5 rounded-full border border-border px-3 py-1 text-xs text-ink transition-colors hover:bg-hover"
                    >
                      <Wrench size={13} /> Gerenciar
                    </button>
                  </div>
                  {toolIds.length === 0 ? (
                    <p className="text-xs text-muted">Nenhuma ferramenta selecionada — o modelo não usará ferramentas.</p>
                  ) : (
                    <div className="space-y-1.5">
                      {toolIds.length > 4 && (
                        <div className="relative">
                          <Search size={13} className="pointer-events-none absolute left-2.5 top-2 text-muted" />
                          <input
                            value={toolSearch}
                            onChange={(e) => setToolSearch(e.target.value)}
                            placeholder="Buscar ferramentas ativas…"
                            className="w-full rounded-lg border border-border bg-surface py-1.5 pl-8 pr-3 text-xs text-ink outline-none focus:border-accent placeholder:text-muted"
                          />
                        </div>
                      )}
                      {/* exatamente 4 linhas visíveis (~35px cada + gaps); o resto rola */}
                      <div className="max-h-[158px] space-y-1.5 overflow-y-auto pr-1">
                        {shownToolIds.map((tid) => {
                          const pinned = !codeMode && pinnedIds.includes(tid);
                          const cfg = TOOL_CFG[tid];
                          return (
                            <div key={tid} className="flex items-center gap-2 rounded-lg border border-border bg-surface px-3 py-1.5">
                              {pinned && (
                                <span title="Fixada (pin)" className="flex shrink-0 items-center text-accent-hover">
                                  <Pin size={12} className="fill-accent-hover" />
                                </span>
                              )}
                              <span className="flex-1 truncate text-sm text-ink">{toolLabel(tid)}</span>
                              {cfg && (
                                <button
                                  onClick={() => setOpenToolCfg(tid)}
                                  title="Configurar esta ferramenta"
                                  className="rounded-md p-1 text-muted transition-colors hover:text-ink"
                                >
                                  <Settings size={14} />
                                </button>
                              )}
                              <button onClick={() => { toggleTool(tid); setOpenToolCfg((o) => (o === tid ? null : o)); }} title="Remover" className="rounded-md p-1 text-muted transition-colors hover:text-ink">
                                <X size={14} />
                              </button>
                            </div>
                          );
                        })}
                        {shownToolIds.length === 0 && (
                          <p className="px-1 py-2 text-xs text-muted">Nenhuma ferramenta corresponde à busca.</p>
                        )}
                      </div>
                    </div>
                  )}
                  {toolIds.length > 0 && !codeMode && (
                    <p className="text-[11px] text-muted">Use “Gerenciar” para fixar (pin) as ferramentas mais usadas.</p>
                  )}
                </div>
              </div>
            )}
          </div>

          {/* Skills — equipadas neste modelo (carregadas sob demanda via view_skill) */}
          <Section
            title="Skills"
            icon={<Sparkles size={15} />}
            hint="O modelo vê só nome + descrição de cada skill; quando precisa, chama view_skill e carrega o conteúdo completo. Também dá para invocá-las com $ no chat. Equipar muitas não encarece os turnos em que não são usadas."
            right={<ManageBtn icon={<Sparkles size={13} />} label="Gerenciar" onClick={() => setSkillsModal(true)} />}
          >
            {skillIds.length === 0 ? (
              <p className="text-xs text-muted">Nenhuma skill equipada. Crie skills na aba Skills.</p>
            ) : (
              <div className="space-y-1.5">
                {skillIds.length > 4 && (
                  <div className="relative">
                    <Search size={13} className="pointer-events-none absolute left-2.5 top-2 text-muted" />
                    <input
                      value={skillSearch}
                      onChange={(e) => setSkillSearch(e.target.value)}
                      placeholder="Buscar skills equipadas…"
                      className="w-full rounded-lg border border-border bg-surface py-1.5 pl-8 pr-3 text-xs text-ink outline-none transition-colors focus:border-accent placeholder:text-muted"
                    />
                  </div>
                )}
                <div className="max-h-[184px] space-y-1.5 overflow-y-auto pr-1">
                  {shownSkillIds.map((sid) => (
                    <div key={sid} className="flex items-center gap-2 rounded-lg border border-border bg-surface px-3 py-1.5">
                      <Sparkles size={13} className="shrink-0 text-accent-hover" />
                      <span className="flex-1 truncate text-sm text-ink">{skillLabel(sid)}</span>
                      <button onClick={() => setSkillIds((ids) => ids.filter((x) => x !== sid))} title="Remover" className="rounded-md p-1 text-muted transition-colors hover:text-ink">
                        <X size={14} />
                      </button>
                    </div>
                  ))}
                  {shownSkillIds.length === 0 && (
                    <p className="px-1 py-2 text-xs text-muted">Nenhuma skill corresponde à busca.</p>
                  )}
                </div>
              </div>
            )}
          </Section>

          {/* Capacidades — o que o modelo PODE fazer (config base) */}
          <div className="mt-8 border-t border-border pt-7">
            <SelectorField
              label="Capacidades"
              icon={<Sparkles size={13} />}
              selected={capSelected}
              labelOf={(k) => CAPS.find((c) => c.key === k)?.label ?? k}
              onRemove={(k) => setCapSelected(capSelected.filter((x) => x !== k))}
              onManage={() => setCapsModal(true)}
              empty="Nenhuma capacidade marcada."
              hint="O que o modelo pode fazer: Visão (enxerga imagens — se ligada, usa a visão do próprio modelo; o filtro Vision Router se sobrepõe), Upload de Arquivos, Geração de Imagens e Contexto do Chat (envia o histórico da conversa; desligada = turno sem memória)."
            />
          </div>

          {/* Memória — padrão POR-MODELO (novos chats deste modelo) */}
          <div className="mt-8 space-y-2 border-t border-border pt-7">
            <div className="mb-3 flex items-center justify-between gap-3">
              <h2 className="flex items-center gap-2 text-sm font-semibold text-ink">
                <span className="text-muted"><Brain size={15} /></span>
                Memória
                <InfoHint text="Padrão de memória dos chats que usam este modelo. Cada chat ainda pode sobrescrever. Desativado = usa o padrão do seu perfil (Espaço → Memória)." />
              </h2>
              <button
                onClick={() => setMem(mem ? null : { ...MEM_CFG_DEFAULT })}
                className={`rounded-full border px-3 py-1 text-xs transition-colors ${mem ? "border-accent/40 bg-accent/15 text-accent-hover" : "border-border text-muted hover:text-ink"}`}
              >
                {mem ? "Personalizada" : "Padrão do perfil"}
              </button>
            </div>
            {mem && (
              <div className="grid gap-3 rounded-xl border border-border bg-surface2/40 p-3 sm:grid-cols-2">
                <div>
                  <p className="mb-1.5 text-[11px] font-medium uppercase tracking-wider text-muted">Salvar em</p>
                  <select
                    value={mem.write ?? "global"}
                    onChange={(e) => setMem({ ...mem, write: e.target.value })}
                    className="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm text-ink outline-none focus:border-accent"
                  >
                    {MEM_WRITE_OPTS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                    {memBanks.length > 0 && (
                      <optgroup label="Bancos">
                        {memBanks.map((b) => <option key={b.id} value={`bank:${b.id}`}>Banco: {b.name}</option>)}
                      </optgroup>
                    )}
                  </select>
                </div>
                <div>
                  <p className="mb-1.5 text-[11px] font-medium uppercase tracking-wider text-muted">Ler de (união)</p>
                  <div className="flex flex-wrap gap-1.5">
                    {MEM_READ_OPTS.map((r) => {
                      const on = (mem.read ?? MEM_CFG_DEFAULT.read)[r.key] !== false;
                      return (
                        <button
                          key={r.key}
                          onClick={() => setMem({ ...mem, read: { ...(mem.read ?? MEM_CFG_DEFAULT.read), [r.key]: !on } })}
                          className={`rounded-full border px-3 py-1 text-xs transition-colors ${on ? "border-accent/40 bg-accent/15 text-accent-hover" : "border-border text-muted hover:text-ink"}`}
                        >
                          {r.label}
                        </button>
                      );
                    })}
                  </div>
                </div>
                {/* Bancos acoplados: memória compartilhada entre modelos */}
                <div className="sm:col-span-2">
                  <p className="mb-1.5 flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wider text-muted">
                    Bancos acoplados
                    <InfoHint text="Bancos de memória compartilhados entre modelos. Acoplar um banco faz este modelo LER dele (em união com os escopos acima). Vários modelos no mesmo banco compartilham memórias sem usar o escopo global. Crie/gerencie bancos em Espaço → Memória → Bancos." />
                  </p>
                  {memBanks.length === 0 ? (
                    <p className="text-xs text-muted">Nenhum banco criado. Crie em <span className="text-ink-soft">Espaço → Memória → Bancos</span>.</p>
                  ) : (
                    <div className="flex flex-wrap gap-1.5">
                      {memBanks.map((b) => {
                        const on = (mem.banks ?? []).includes(b.id);
                        return (
                          <button
                            key={b.id}
                            onClick={() => {
                              const cur = mem.banks ?? [];
                              setMem({ ...mem, banks: on ? cur.filter((x) => x !== b.id) : [...cur, b.id] });
                            }}
                            className={`rounded-full border px-3 py-1 text-xs transition-colors ${on ? "border-accent/40 bg-accent/15 text-accent-hover" : "border-border text-muted hover:text-ink"}`}
                          >
                            {b.name}
                          </button>
                        );
                      })}
                    </div>
                  )}
                </div>
              </div>
            )}
          </div>

          {/* Base de Conhecimento — documentos que este modelo consulta (RAG) */}
          <div className="mt-8 border-t border-border pt-7">
            <h2 className="flex items-center gap-2 text-sm font-semibold text-ink">
              <span className="text-muted"><BookOpen size={15} /></span>
              Conhecimento
              <InfoHint text="Acople Bases de Conhecimento (documentos) a este modelo. Ele passa a consultá-las nas conversas. Crie/suba documentos em Espaço → Conhecimento." />
            </h2>
            <div className="mt-3 space-y-4">
              {kbBases.length === 0 ? (
                <p className="text-xs text-muted">Nenhuma base criada. Crie em <span className="text-ink-soft">Espaço → Conhecimento</span>.</p>
              ) : (
                <>
                  <div>
                    <p className="mb-1.5 text-[11px] font-medium uppercase tracking-wider text-muted">Bases acopladas</p>
                    <div className="flex flex-wrap gap-1.5">
                      {kbBases.map((b) => {
                        const on = (kb.bases ?? []).includes(b.id);
                        return (
                          <button
                            key={b.id}
                            onClick={() => {
                              const cur = kb.bases ?? [];
                              setKb({ ...kb, bases: on ? cur.filter((x) => x !== b.id) : [...cur, b.id] });
                            }}
                            className={`rounded-full border px-3 py-1 text-xs transition-colors ${on ? "border-accent/40 bg-accent/15 text-accent-hover" : "border-border text-muted hover:text-ink"}`}
                          >
                            {b.name}
                          </button>
                        );
                      })}
                    </div>
                  </div>
                  {(kb.bases ?? []).length > 0 && (
                    <div className="flex flex-wrap items-center gap-4">
                      <label className="flex items-center gap-2 text-xs text-muted">
                        Modo
                        <select
                          value={kb.mode || "auto"}
                          onChange={(e) => setKb({ ...kb, mode: e.target.value as "auto" | "tool" })}
                          className="rounded-lg border border-border bg-surface px-2.5 py-1.5 text-sm text-ink outline-none focus:border-accent"
                        >
                          <option value="auto">Automático (injeta + cita)</option>
                          <option value="tool">Ferramenta (a IA busca)</option>
                        </select>
                        <InfoHint text="Automático: a cada mensagem busco os trechos relevantes e injeto no contexto, com citações. Ferramenta: a IA decide quando buscar (chama search_knowledge)." />
                      </label>
                      <label className="flex items-center gap-2 text-xs text-muted">
                        Trechos
                        <input
                          type="number" min={1} max={20} value={kb.k ?? 6}
                          onChange={(e) => setKb({ ...kb, k: Math.max(1, Math.min(20, Number(e.target.value) || 6)) })}
                          className="w-16 rounded-lg border border-border bg-surface px-2.5 py-1.5 text-right text-sm text-ink outline-none focus:border-accent"
                        />
                      </label>
                    </div>
                  )}
                </>
              )}
            </div>
          </div>

          {/* Cérebro — notas [[interligadas]] que este modelo lê/escreve */}
          <div className="mt-8 border-t border-border pt-7">
            <h2 className="flex items-center gap-2 text-sm font-semibold text-ink">
              <span className="text-muted"><Brain size={15} /></span>
              Cérebro
              <InfoHint text="Acople cérebros (notas interligadas) a este modelo. Ele lê/busca as notas e, com a escrita ligada, cria e atualiza notas sozinho durante as conversas. Crie cérebros em Espaço → Cérebros." />
            </h2>
            <div className="mt-3 space-y-4">
              {brainBases.length === 0 ? (
                <p className="text-xs text-muted">Nenhum cérebro criado. Crie em <span className="text-ink-soft">Espaço → Cérebros</span>.</p>
              ) : (
                <>
                  <div>
                    <p className="mb-1.5 text-[11px] font-medium uppercase tracking-wider text-muted">Cérebros acoplados</p>
                    <div className="flex flex-wrap gap-1.5">
                      {brainBases.map((b) => {
                        const on = (brainCfg.brains ?? []).includes(b.id);
                        return (
                          <button
                            key={b.id}
                            onClick={() => {
                              const cur = brainCfg.brains ?? [];
                              setBrainCfg({ ...brainCfg, brains: on ? cur.filter((x) => x !== b.id) : [...cur, b.id] });
                            }}
                            className={`rounded-full border px-3 py-1 text-xs transition-colors ${on ? "border-accent/40 bg-accent/15 text-accent-hover" : "border-border text-muted hover:text-ink"}`}
                          >
                            {b.name}
                          </button>
                        );
                      })}
                    </div>
                  </div>
                  {(brainCfg.brains ?? []).length > 0 && (
                    <label className="flex items-center gap-2 text-xs text-muted">
                      <input
                        type="checkbox"
                        checked={brainCfg.write !== false}
                        onChange={(e) => setBrainCfg({ ...brainCfg, write: e.target.checked })}
                        className="h-3.5 w-3.5 accent-accent"
                      />
                      Escrita pela IA (criar/atualizar notas)
                      <InfoHint text="Com a escrita ligada, a IA grava notas direto (um card no chat mostra o que foi escrito). Desligada, o cérebro fica somente leitura." />
                    </label>
                  )}
                </>
              )}
            </div>
          </div>

          {/* Guarda de tokens — aviso de uso alto POR-MODELO (override do perfil) */}
          <div className="mt-8 flex items-center justify-between gap-3 border-t border-border pt-7">
            <h2 className="flex items-center gap-2 text-sm font-semibold text-ink">
              <span className="text-muted"><Gauge size={15} /></span>
              Aviso de uso alto
              <InfoHint text="Marca respostas deste modelo que passarem deste nº de tokens. Vazio = usa o padrão do seu perfil. Só avisa, não bloqueia." />
            </h2>
            <input
              type="number"
              min={0}
              step={1000}
              value={tokenWarn}
              onChange={(e) => setTokenWarn(e.target.value === "" ? "" : Number(e.target.value))}
              placeholder="Padrão do perfil"
              className="w-40 rounded-lg border border-border bg-surface px-3 py-1.5 text-right text-sm text-ink outline-none transition-colors focus:border-accent placeholder:text-muted"
            />
          </div>

          {/* Subagentes — este modelo (orquestrador) pode delegar a outros (operários) */}
          <div className="mt-8 border-t border-border pt-7">
            <div className="flex items-center justify-between gap-3">
              <h2 className="flex items-center gap-2 text-sm font-semibold text-ink">
                <span className="text-muted"><Users size={15} /></span>
                Subagentes
                <InfoHint text="Permite que este modelo delegue sub-tarefas a outros modelos custom (operários) via a ferramenta 'delegate'. Cada operário roda com o próprio prompt/ferramentas e devolve o resultado para este modelo sintetizar." />
              </h2>
              <Toggle on={subOn} onChange={setSubOn} />
            </div>
            {subOn && (
              <div className="mt-3 space-y-3 rounded-xl border border-border bg-surface p-3">
                <div className="space-y-2">
                  <div className="flex items-center justify-between gap-3">
                    <p className="text-[11px] font-medium uppercase tracking-wider text-muted">Operários (time)</p>
                    <button
                      onClick={() => setTeamModal(true)}
                      disabled={teamCandidates.length === 0}
                      className="flex shrink-0 items-center gap-1.5 rounded-full border border-border px-3 py-1 text-xs text-ink-soft transition-colors hover:bg-hover hover:text-ink disabled:opacity-50"
                    >
                      <Users size={13} /> Selecionar
                    </button>
                  </div>
                  {teamCandidates.length === 0 ? (
                    <p className="text-xs text-muted">Crie outros modelos custom para usá-los como operários.</p>
                  ) : team.length === 0 ? (
                    <p className="text-xs text-muted">Nenhum operário no time. Clique em “Selecionar”.</p>
                  ) : (
                    <div className="max-h-44 space-y-1.5 overflow-y-auto pr-1">
                      {team.map((tid) => (
                        <div key={tid} className="flex items-center gap-2 rounded-lg border border-border bg-surface2 px-3 py-1.5">
                          <Box size={13} className="shrink-0 text-accent-hover" />
                          <span className="flex-1 truncate text-sm text-ink">{teamLabel(tid)}</span>
                          <button
                            onClick={() => setSubCfg({ team: team.filter((x) => x !== tid) })}
                            title="Remover do time"
                            className="rounded-md p-1 text-muted transition-colors hover:text-red-300"
                          >
                            <X size={14} />
                          </button>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
                <div className="grid gap-3 sm:grid-cols-3">
                  <label className="space-y-1">
                    <span className="text-[11px] text-muted">Execução</span>
                    <select value={subCfg.mode === "parallel" ? "parallel" : "sequential"} onChange={(e) => setSubCfg({ mode: e.target.value })} className={selCls}>
                      <option value="sequential">Sequencial</option>
                      <option value="parallel">Paralela</option>
                    </select>
                  </label>
                  <label className="space-y-1">
                    <span className="text-[11px] text-muted">Máx. chamadas/turno</span>
                    <input type="number" min={1} max={10} value={subCfg.max_calls ?? 4} onChange={(e) => setSubCfg({ max_calls: Math.max(1, Math.min(10, Number(e.target.value) || 4)) })} className={inpCls} />
                  </label>
                  <label className="space-y-1">
                    <span className="text-[11px] text-muted">Profundidade</span>
                    <input type="number" min={1} max={3} value={subCfg.max_depth ?? 2} onChange={(e) => setSubCfg({ max_depth: Math.max(1, Math.min(3, Number(e.target.value) || 2)) })} className={inpCls} />
                  </label>
                </div>
                <div className="space-y-2 border-t border-border pt-2.5">
                  <div className="flex items-center justify-between gap-3">
                    <div>
                      <p className="text-sm text-ink">Operários veem o contexto do chat</p>
                      <p className="text-[11px] text-muted">Recebem o histórico recente da conversa (senão, só a tarefa).</p>
                    </div>
                    <Toggle on={!!subCfg.pass_context} onChange={(v) => setSubCfg({ pass_context: v })} />
                  </div>
                  <div className="flex items-center justify-between gap-3">
                    <div>
                      <p className="text-sm text-ink">Operários usam memória própria</p>
                      <p className="text-[11px] text-muted">Cada operário lê/escreve na memória do próprio modelo (config em Memória).</p>
                    </div>
                    <Toggle on={!!subCfg.worker_memory} onChange={(v) => setSubCfg({ worker_memory: v })} />
                  </div>
                </div>
                <p className="text-[11px] text-muted">
                  Profundidade limita cadeias (operário chamando operário). Na conversa, você também chama um agente direto digitando <span className="font-mono text-ink-soft">@</span>.
                </p>
              </div>
            )}
          </div>

          {/* Extração de texto — config POR-MODELO, aplicável quando há Upload de Arquivos */}
          {capSelected.includes("file_upload") && (
            <div className="mt-8 border-t border-border pt-7">
              <div className="flex items-center justify-between gap-3">
                <h2 className="flex items-center gap-2 text-sm font-semibold text-ink">
                  <span className="text-muted"><FileText size={15} /></span>
                  Extração de texto
                  <InfoHint text="Como este modelo lê documentos anexados (PDF/Word/Excel/PPT/CSV) e faz OCR. É configuração deste modelo." />
                </h2>
                <ManageBtn icon={<Settings size={13} />} label="Configurar" onClick={() => setOpenExtraction(true)} />
              </div>
            </div>
          )}

          {/* Filtros — pré/pós-processadores; alguns têm config (engrenagem) */}
          <div className="mt-8 space-y-2 border-t border-border pt-7">
            <div className="mb-3 flex items-center justify-between gap-3">
              <h2 className="flex items-center gap-2 text-sm font-semibold text-ink">
                <span className="text-muted"><Sliders size={15} /></span>
                Filtros
                <InfoHint text="Filtros do sistema aplicados ao turno. O Vision Router redireciona as imagens para um modelo com visão, que as descreve para o modelo em uso (mesmo que ele não enxergue). O Audio Router transcreve áudios enviados (voz→texto) para o modelo 'ouvir'." />
              </h2>
              <ManageBtn icon={<Sliders size={13} />} label="Gerenciar" onClick={() => setFiltersModal(true)} />
            </div>
            {filters.length === 0 ? (
              <p className="text-xs text-muted">Nenhum filtro ativo.</p>
            ) : (
              <div className="space-y-1.5">
                {filters.map((f) => {
                  const cfgOpen = openFilterCfg === f;
                  const isVisionRouter = f === "vision_router";
                  const isAudioRouter = f === "audio_router";
                  const isGenImage = f === "genimage_router";
                  const isGuard = f === "output_guard";
                  const hasCfg = isVisionRouter || isAudioRouter || isGenImage || isGuard;
                  const fc = filterConfig[f] || {};
                  const target = fc.model || "";
                  const guardCount = isGuard ? (Array.isArray(fc.guards) ? fc.guards.length : 0) : 0;
                  const provider = fc.provider || "openrouter";
                  const setCfg = (patch: Record<string, any>) =>
                    setFilterConfig((prev) => ({ ...prev, [f]: { ...(prev[f] || {}), ...patch } }));
                  return (
                    <div key={f} className="rounded-lg border border-border bg-surface">
                      <div className="flex items-center gap-2 px-3 py-1.5">
                        <Sliders size={13} className="shrink-0 text-accent-hover" />
                        <span className="flex-1 truncate text-sm text-ink">
                          {FILTERS.find((x) => x.key === f)?.label ?? f}
                          {!isGuard && hasCfg && target && (
                            <span className="ml-2 text-xs text-muted">→ {baseModels.find((b) => b.id === target)?.name ?? target}</span>
                          )}
                          {isGuard && guardCount > 0 && (
                            <span className="ml-2 text-xs text-muted">· {guardCount} guarda{guardCount > 1 ? "s" : ""}</span>
                          )}
                        </span>
                        {hasCfg && (
                          <button
                            onClick={() => setOpenFilterCfg(cfgOpen ? null : f)}
                            title="Configurar"
                            className={`rounded-md p-1 transition-colors ${cfgOpen ? "bg-hover text-ink" : "text-muted hover:text-ink"}`}
                          >
                            <Settings size={14} />
                          </button>
                        )}
                        <button onClick={() => { setFilters(filters.filter((x) => x !== f)); setOpenFilterCfg((o) => (o === f ? null : o)); }} title="Remover" className="rounded-md p-1 text-muted transition-colors hover:text-ink">
                          <X size={14} />
                        </button>
                      </div>
                      {isVisionRouter && cfgOpen && (
                        <div className="space-y-1.5 border-t border-border px-3 py-2.5">
                          <p className="text-xs text-muted">
                            Modelo de visão que vai <span className="text-ink-soft">enxergar as imagens</span> e descrevê-las para
                            o modelo em uso (útil quando o modelo base não tem visão).
                          </p>
                          <ModelField
                            models={baseModels}
                            value={target}
                            onChange={(v) => setCfg({ model: v })}
                            placeholder="Selecione um modelo com visão…"
                          />
                        </div>
                      )}
                      {isAudioRouter && cfgOpen && (
                        <div className="space-y-2 border-t border-border px-3 py-2.5">
                          <p className="text-xs text-muted">
                            Transcreve os <span className="text-ink-soft">áudios enviados</span> (chat, WhatsApp, Telegram) para o
                            modelo em uso &quot;ouvir&quot; em texto.
                          </p>
                          <div className="flex items-center gap-2 text-sm">
                            <span className="shrink-0 text-ink-soft">Motor</span>
                            <select
                              value={fc.engine || "stt"}
                              onChange={(e) => setCfg({ engine: e.target.value })}
                              className="rounded-lg border border-border bg-surface2 px-2 py-1 text-sm text-ink outline-none focus:border-accent"
                            >
                              <option value="stt">Provedor de voz (Whisper)</option>
                              <option value="model">Modelo multimodal (OpenRouter)</option>
                            </select>
                          </div>
                          {(fc.engine || "stt") === "model" ? (
                            <ModelField
                              models={baseModels}
                              value={target}
                              onChange={(v) => setCfg({ model: v })}
                              placeholder="Modelo que aceita áudio (ex.: gemini-2.5-flash)…"
                            />
                          ) : (
                            <p className="text-[11px] text-muted">Usa a chave do provedor de voz (Configurações → Conexões).</p>
                          )}
                        </div>
                      )}
                      {isGenImage && cfgOpen && (
                        <div className="space-y-2 border-t border-border px-3 py-2.5">
                          <p className="text-xs text-muted">
                            Modelo que <span className="text-ink-soft">gera as imagens</span> quando o usuário pedir — o modelo em uso
                            delega a geração para ele.
                          </p>
                          <div className="flex items-center gap-2 text-sm">
                            <span className="shrink-0 text-ink-soft">Provedor</span>
                            <select
                              value={provider}
                              onChange={(e) => setCfg({ provider: e.target.value })}
                              className="rounded-lg border border-border bg-surface2 px-2 py-1 text-sm text-ink outline-none focus:border-accent"
                            >
                              <option value="openrouter">OpenRouter</option>
                              <option value="openai_compat">Compatível OpenAI</option>
                            </select>
                          </div>
                          {provider === "openrouter" ? (
                            <ModelField
                              models={baseModels}
                              value={target}
                              onChange={(v) => setCfg({ model: v })}
                              placeholder="Modelo de imagem do OpenRouter…"
                            />
                          ) : (
                            <>
                              <input
                                value={fc.base_url || ""}
                                onChange={(e) => setCfg({ base_url: e.target.value })}
                                placeholder="Base URL (ex.: https://api.openai.com/v1)"
                                className="w-full rounded-lg border border-border bg-surface2 px-3 py-1.5 text-sm text-ink outline-none focus:border-accent"
                              />
                              <input
                                value={target}
                                onChange={(e) => setCfg({ model: e.target.value })}
                                placeholder="ID do modelo (ex.: dall-e-3)"
                                className="w-full rounded-lg border border-border bg-surface2 px-3 py-1.5 text-sm text-ink outline-none focus:border-accent"
                              />
                              <p className="text-[11px] text-muted">A chave do provedor fica em Conexões → APIs.</p>
                            </>
                          )}
                        </div>
                      )}
                      {isGuard && cfgOpen && (
                        <div className="border-t border-border px-3 py-2.5">
                          <OutputGuards
                            value={fc.guards}
                            onChange={(guards) => setCfg({ guards })}
                            baseModels={baseModels}
                          />
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* rodapé */}
      <div className="flex shrink-0 items-center justify-end gap-3 border-t border-border px-4 py-3 md:px-6">
        {err && <span className="text-xs text-red-400">{err}</span>}
        <button onClick={save} disabled={saving} className="rounded-full bg-accent px-6 py-2 text-sm font-medium text-white transition-colors hover:bg-accent-hover disabled:opacity-60">
          {saving ? "…" : "Salvar e Atualizar"}
        </button>
      </div>

      {toolsModal && (
        <TransferModal
          title="Ferramentas do modelo"
          items={transferItems}
          selected={toolIds}
          onChange={setToolIds}
          onClose={() => setToolsModal(false)}
          availableLabel="Disponíveis"
          selectedLabel="Ativadas"
          searchPlaceholder="Buscar ferramentas…"
          pinnedKeys={codeMode ? undefined : pinnedIds}
          onTogglePin={codeMode ? undefined : togglePin}
          pinHint="Fixar: vira ferramenta de 1ª classe (o modelo chama direto, sem busca). Ideal p/ tools quentes e de schema pequeno."
        />
      )}
      {skillsModal && (
        <TransferModal
          title="Skills do modelo"
          items={skillItems}
          selected={skillIds}
          onChange={setSkillIds}
          onClose={() => setSkillsModal(false)}
          availableLabel="Disponíveis"
          selectedLabel="Ativadas"
          searchPlaceholder="Buscar skills…"
        />
      )}
      {capsModal && (
        <TransferModal
          title="Capacidades do modelo"
          items={capItems}
          selected={capSelected}
          onChange={setCapSelected}
          onClose={() => setCapsModal(false)}
          availableLabel="Disponíveis"
          selectedLabel="Ativadas"
        />
      )}
      {filtersModal && (
        <TransferModal
          title="Filtros do modelo"
          items={filterItems}
          selected={filters}
          onChange={setFilters}
          onClose={() => setFiltersModal(false)}
          availableLabel="Disponíveis"
          selectedLabel="Ativados"
        />
      )}
      {teamModal && (
        <TransferModal
          title="Operários (time)"
          items={teamItems}
          selected={team}
          onChange={(ids) => setSubCfg({ team: ids })}
          onClose={() => setTeamModal(false)}
          availableLabel="Disponíveis"
          selectedLabel="No time"
          searchPlaceholder="Buscar modelos…"
        />
      )}

      {/* Config de ferramenta (Pesquisa na Web / Finanças / Deep Search) em janela */}
      {openToolCfg && TOOL_CFG[openToolCfg] && (() => {
        const cfg = TOOL_CFG[openToolCfg]!;
        const Panel = cfg.Panel;
        return (
          <CfgModal title={`Configurar — ${toolLabel(openToolCfg)}`} onClose={() => setOpenToolCfg(null)}>
            <Panel
              value={toolsCfg[cfg.key] ?? {}}
              onChange={(v: any) => setToolCfg(cfg.key, v)}
              status={cfg.needsStatus ? secretStatus : undefined}
              reloadSecrets={cfg.needsStatus ? reloadSecrets : undefined}
              models={baseModels}
            />
          </CfgModal>
        );
      })()}

      {/* Config da extração de texto em janela */}
      {openExtraction && (
        <CfgModal title="Extração de texto" onClose={() => setOpenExtraction(false)}>
          <TextExtractionPanel value={toolsCfg.text_extraction ?? {}} onChange={(v) => setToolCfg("text_extraction", v)} />
        </CfgModal>
      )}
    </div>
  );
}
