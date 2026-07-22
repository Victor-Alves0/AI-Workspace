"use client";

import { useEffect, useState } from "react";
import { ChevronDown, ChevronRight, SlidersHorizontal, X } from "lucide-react";
import { api } from "@/lib/api";
import type { BrainConfig, KnowledgeBase, KnowledgeConfig, MemoryConfig } from "@/lib/types";

const MEM_WRITE: { value: string; label: string; project?: boolean }[] = [
  { value: "global", label: "Global" },
  { value: "model", label: "Do modelo" },
  { value: "project", label: "Do projeto (pasta)", project: true },
  { value: "chat", label: "Só este chat" },
  { value: "off", label: "Não salvar" },
];
const MEM_READ: { key: "global" | "model" | "chat" | "project"; label: string; project?: boolean }[] = [
  { key: "global", label: "Global" },
  { key: "model", label: "Modelo" },
  { key: "project", label: "Projeto", project: true },
  { key: "chat", label: "Chat" },
];
const MEM_DEFAULT: Required<MemoryConfig> = {
  enabled: true, write: "global",
  read: { global: true, model: true, chat: true, project: true }, banks: [], review: false,
};

// Lista de parâmetros do painel (espelha o OpenWebUI). Cada um vira o controle certo:
// faixa numérica → slider (com valor custom), escolha fixa → seletor, ligado/desligado →
// bool. Os de geração comuns (temperature, top_p, seed, penalties, reasoning_effort…) são
// enviados ao OpenRouter; os legados do Ollama (mirostat/tfs_z/…) são ignorados por quem
// não os suporta. Em toda opção, "Padrão" = não enviamos o parâmetro (o modelo usa o dele).
type ParamSpec =
  | { key: string; label: string; kind: "slider"; min: number; max: number; step: number; def: number }
  | { key: string; label: string; kind: "select"; options: { value: string; label: string }[] }
  | { key: string; label: string; kind: "bool" }
  | { key: string; label: string; kind: "text" };

const PARAMS: ParamSpec[] = [
  { key: "temperature", label: "Temperatura", kind: "slider", min: 0, max: 2, step: 0.05, def: 1 },
  { key: "top_p", label: "top_p", kind: "slider", min: 0, max: 1, step: 0.05, def: 1 },
  { key: "top_k", label: "top_k", kind: "slider", min: 0, max: 100, step: 1, def: 40 },
  { key: "min_p", label: "min_p", kind: "slider", min: 0, max: 1, step: 0.01, def: 0 },
  { key: "frequency_penalty", label: "Penalidade de frequência", kind: "slider", min: -2, max: 2, step: 0.1, def: 0 },
  { key: "presence_penalty", label: "Penalidade de presença", kind: "slider", min: -2, max: 2, step: 0.1, def: 0 },
  { key: "reasoning_effort", label: "Esforço de raciocínio", kind: "select", options: [
    { value: "minimal", label: "Mínimo" }, { value: "low", label: "Baixo" },
    { value: "medium", label: "Médio" }, { value: "high", label: "Alto" },
  ] },
  { key: "max_tokens", label: "max_tokens", kind: "text" },
  { key: "seed", label: "Seed", kind: "text" },
  { key: "stop", label: "Sequência de parada", kind: "text" },
  { key: "logit_bias", label: "logit_bias", kind: "text" },
  { key: "stream", label: "Stream da resposta", kind: "bool" },
  { key: "function_calling", label: "Chamada de função", kind: "bool" },
  { key: "reasoning_tags", label: "Tags de raciocínio", kind: "bool" },
  { key: "stream_delta_size", label: "Tamanho do bloco delta", kind: "text" },
  { key: "repeat_penalty", label: "repeat_penalty", kind: "slider", min: 0, max: 2, step: 0.05, def: 1 },
  { key: "repeat_last_n", label: "repeat_last_n", kind: "text" },
  { key: "mirostat", label: "mirostat", kind: "text" },
  { key: "mirostat_eta", label: "mirostat_eta", kind: "text" },
  { key: "mirostat_tau", label: "mirostat_tau", kind: "text" },
  { key: "tfs_z", label: "tfs_z", kind: "text" },
];

function MemToggle({ on, onClick }: { on: boolean; onClick: () => void }) {
  return (
    <button onClick={onClick} className={`relative h-5 w-9 shrink-0 rounded-full transition-colors ${on ? "bg-accent" : "bg-surface2"}`}>
      <span className={`absolute top-0.5 h-4 w-4 rounded-full bg-white transition-all ${on ? "left-[18px]" : "left-0.5"}`} />
    </button>
  );
}

function Section({
  title,
  open,
  onToggle,
  children,
}: {
  title: string;
  open: boolean;
  onToggle: () => void;
  children: React.ReactNode;
}) {
  return (
    <div className="border-b border-border py-3">
      <button onClick={onToggle} className="flex w-full items-center justify-between px-4 text-sm font-semibold text-ink">
        {title}
        {open ? <ChevronDown size={16} className="text-muted" /> : <ChevronRight size={16} className="text-muted" />}
      </button>
      {open && <div className="px-4 pt-3">{children}</div>}
    </div>
  );
}

// "Padrão" (não enviado ao modelo) = valor ausente. Cada tipo de linha materializa
// um valor tipado ao ser mexido; o "×"/opção Padrão volta a não enviar.
const PADRAO_HINT = "\"Padrão\" usa o valor do próprio modelo — o parâmetro não é enviado.";

function ParamControl({
  spec,
  value,
  onChange,
}: {
  spec: ParamSpec;
  value: unknown;
  /** valor tipado; undefined = voltar ao Padrão (remove do envio) */
  onChange: (v: number | string | boolean | undefined) => void;
}) {
  const has = value !== undefined && value !== "";

  if (spec.kind === "slider") {
    const num = has ? Number(value) : spec.def;
    return (
      <div className="py-2 text-sm">
        <div className="flex items-center justify-between">
          <span className="text-ink">{spec.label}</span>
          {has ? (
            <div className="flex items-center gap-1.5">
              <input
                type="number"
                value={Number.isFinite(num) ? num : ""}
                step={spec.step}
                onChange={(e) => onChange(e.target.value === "" ? undefined : Number(e.target.value))}
                className="w-16 rounded-md border border-border bg-surface px-1.5 py-0.5 text-right text-xs text-ink outline-none focus:border-accent"
              />
              <button onClick={() => onChange(undefined)} title="Voltar ao Padrão" className="text-muted hover:text-ink">
                <X size={13} />
              </button>
            </div>
          ) : (
            <button onClick={() => onChange(spec.def)} title={PADRAO_HINT} className="text-xs text-muted hover:text-ink">
              Padrão
            </button>
          )}
        </div>
        {has && (
          <input
            type="range"
            min={spec.min}
            max={spec.max}
            step={spec.step}
            value={Number.isFinite(num) ? Math.min(Math.max(num, spec.min), spec.max) : spec.def}
            onChange={(e) => onChange(Number(e.target.value))}
            className="mt-1.5 w-full accent-accent"
          />
        )}
      </div>
    );
  }

  if (spec.kind === "select" || spec.kind === "bool") {
    const options =
      spec.kind === "bool"
        ? [{ value: "true", label: "Ligado" }, { value: "false", label: "Desligado" }]
        : spec.options;
    const cur = has ? String(value) : "";
    return (
      <div className="flex items-center justify-between py-1.5 text-sm">
        <span className="text-ink">{spec.label}</span>
        <select
          value={cur}
          onChange={(e) => {
            const v = e.target.value;
            if (v === "") onChange(undefined);
            else if (spec.kind === "bool") onChange(v === "true");
            else onChange(v);
          }}
          title={PADRAO_HINT}
          className="rounded-md border border-border bg-surface px-2 py-1 text-xs text-ink outline-none focus:border-accent"
        >
          <option value="">Padrão (do modelo)</option>
          {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
      </div>
    );
  }

  // text (freeform): número quando numérico, senão string
  return <ParamTextRow label={spec.label} value={value} onChange={onChange} />;
}

function ParamTextRow({
  label,
  value,
  onChange,
}: {
  label: string;
  value: unknown;
  onChange: (v: number | string | undefined) => void;
}) {
  const [editing, setEditing] = useState(false);
  const has = value !== undefined && value !== "";
  return (
    <div className="flex items-center justify-between py-1.5 text-sm">
      <span className="text-ink">{label}</span>
      {editing ? (
        <input
          autoFocus
          defaultValue={has ? String(value) : ""}
          onBlur={(e) => {
            const raw = e.target.value;
            onChange(raw === "" ? undefined : (isNaN(Number(raw)) ? raw : Number(raw)));
            setEditing(false);
          }}
          onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }}
          className="w-24 rounded-md border border-accent bg-surface px-2 py-0.5 text-right text-xs text-ink outline-none"
        />
      ) : (
        <button
          onClick={() => setEditing(true)}
          title={has ? undefined : PADRAO_HINT}
          className={has ? "text-sm text-ink" : "text-sm text-muted hover:text-ink"}
        >
          {has ? String(value) : "Padrão"}
        </button>
      )}
    </div>
  );
}

export default function Controls({
  systemPrompt: initialSystemPrompt,
  params: initialParams,
  memory,
  memoryDefault,
  hasProject,
  onMemoryChange,
  knowledge,
  onKnowledgeChange,
  brain,
  onBrainChange,
  onSave,
  onClose,
}: {
  systemPrompt: string;
  params: Record<string, unknown>;
  /** memória do chat ativo (null = herda). Ausente = sem chat ativo (rascunho). */
  memory?: MemoryConfig | null;
  /** padrão efetivo herdado (perfil/modelo) — p/ mostrar o estado quando herda */
  memoryDefault?: MemoryConfig;
  /** o chat está numa pasta? habilita o escopo de memória "Projeto" */
  hasProject?: boolean;
  onMemoryChange?: (cfg: MemoryConfig | null) => void;
  /** conhecimento do chat ativo (null = herda do modelo/perfil) */
  knowledge?: KnowledgeConfig | null;
  onKnowledgeChange?: (cfg: KnowledgeConfig | null) => void;
  /** cérebro do chat ativo (null = herda do modelo/perfil) */
  brain?: BrainConfig | null;
  onBrainChange?: (cfg: BrainConfig | null) => void;
  onSave: (systemPrompt: string | null, params: Record<string, unknown>) => void;
  onClose: () => void;
}) {
  const [systemPrompt, setSystemPrompt] = useState(initialSystemPrompt ?? "");
  const [params, setParams] = useState<Record<string, unknown>>(initialParams ?? {});
  const [open, setOpen] = useState({ system: true, advanced: true, memory: true, knowledge: true, brain: true });

  // Base de Conhecimento por-chat: acopla bases extras (união com as do modelo).
  const [kbBases, setKbBases] = useState<KnowledgeBase[]>([]);
  useEffect(() => {
    if (onKnowledgeChange) api.get<KnowledgeBase[]>("/knowledge/bases").then(setKbBases).catch(() => {});
  }, [onKnowledgeChange]);
  const kb: KnowledgeConfig = { mode: "auto", k: 6, bases: [], ...(knowledge ?? {}) };
  const kbBasesSel = kb.bases ?? [];
  const patchKb = (p: Partial<KnowledgeConfig>) => onKnowledgeChange?.({ ...kb, ...p });

  // Cérebro por-chat: acopla cérebros extras (união com os do modelo).
  const [brains, setBrains] = useState<KnowledgeBase[]>([]);
  useEffect(() => {
    if (onBrainChange) api.get<KnowledgeBase[]>("/knowledge/bases?kind=brain").then(setBrains).catch(() => {});
  }, [onBrainChange]);
  const br: BrainConfig = { write: true, k: 6, brains: [], ...(brain ?? {}) };
  const brSel = br.brains ?? [];
  const patchBrain = (p: Partial<BrainConfig>) => onBrainChange?.({ ...br, ...p });

  // memória do chat: memory=null → herda o padrão (memoryDefault). `mem` é a config
  // EFETIVA mostrada; ao mexer, materializa uma config explícita (com enabled:true
  // quando ligada) e aplica na hora via onMemoryChange.
  // EMPILHA padrão → chat (igual ao _resolve_memory do servidor): o memory_config
  // pode ser PARCIAL (ex.: chats de projeto do Codespace nascem só com {banks}) —
  // as chaves ausentes herdam do padrão do usuário, nunca de MEM_DEFAULT direto
  // (que tem enabled:true e mostraria "ligada" com o padrão do usuário desligado).
  const mem: Required<MemoryConfig> = {
    ...MEM_DEFAULT, ...(memoryDefault ?? {}), ...(memory ?? {}),
    read: { ...MEM_DEFAULT.read, ...(memoryDefault?.read ?? {}), ...(memory?.read ?? {}) },
  };
  const inheriting = !memory;
  const memEnabled = mem.enabled !== false;
  const patchMem = (p: Partial<MemoryConfig>) =>
    onMemoryChange?.({ ...mem, enabled: true, ...p, read: { ...mem.read, ...(p.read ?? {}) } });

  function setParam(key: string, v: number | string | boolean | undefined) {
    setParams((p) => {
      const next = { ...p };
      if (v === undefined || v === "") delete next[key];
      else next[key] = v;
      return next;
    });
  }

  return (
    <aside className="pt-safe pb-safe flex w-80 shrink-0 flex-col border-l border-transparent bg-sidebar transition-colors hover:border-border">
      <div className="flex items-center justify-between px-4 py-3">
        <span className="flex items-center gap-2 text-sm font-semibold text-ink">
          <SlidersHorizontal size={16} className="text-muted" /> Controles
        </span>
        <button onClick={onClose} className="rounded-lg p-1 text-muted hover:bg-hover hover:text-ink">
          <X size={16} />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto">
        <Section title="Prompt do Sistema" open={open.system} onToggle={() => setOpen({ ...open, system: !open.system })}>
          <textarea
            rows={4}
            value={systemPrompt}
            onChange={(e) => setSystemPrompt(e.target.value)}
            placeholder="Digite o prompt do sistema"
            className="w-full resize-y rounded-lg border border-border bg-surface px-3 py-2 text-sm text-ink outline-none focus:border-accent placeholder:text-muted"
          />
        </Section>

        {onMemoryChange && (
          <Section title="Memória" open={open.memory} onToggle={() => setOpen({ ...open, memory: !open.memory })}>
            <div className="mb-3 flex items-center justify-between">
              <div className="min-w-0">
                <p className="text-sm text-ink">Memória neste chat</p>
                <p className="text-xs text-muted">
                  {inheriting ? `Herdando o padrão (${memEnabled ? "ligada" : "desligada"})` : "Personalizado para este chat"}
                </p>
              </div>
              <div className="flex items-center gap-2">
                {!inheriting && (
                  <button onClick={() => onMemoryChange(null)} className="text-xs text-muted hover:text-ink">Redefinir</button>
                )}
                <MemToggle on={memEnabled} onClick={() => onMemoryChange({ ...mem, enabled: !memEnabled })} />
              </div>
            </div>
            {memEnabled && (
              <div className="space-y-3 border-t border-border pt-3">
                <div>
                  <p className="mb-1.5 text-xs font-medium uppercase tracking-wider text-muted">Salvar novas memórias em</p>
                  <select
                    value={mem.write}
                    onChange={(e) => patchMem({ write: e.target.value as MemoryConfig["write"] })}
                    className="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm text-ink outline-none focus:border-accent"
                  >
                    {MEM_WRITE.filter((o) => !o.project || hasProject).map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                  </select>
                  {!hasProject && (
                    <p className="mt-1 text-[11px] text-muted">Mova este chat para uma pasta para compartilhar memória de projeto entre os chats dela.</p>
                  )}
                </div>
                <div>
                  <p className="mb-1.5 text-xs font-medium uppercase tracking-wider text-muted">Ler memórias de (união)</p>
                  <div className="flex flex-wrap gap-1.5">
                    {MEM_READ.filter((r) => !r.project || hasProject).map((r) => {
                      const on = mem.read[r.key] !== false;
                      return (
                        <button
                          key={r.key}
                          onClick={() => patchMem({ read: { [r.key]: !on } })}
                          className={`rounded-full border px-3 py-1 text-xs transition-colors ${on ? "border-accent/40 bg-accent/15 text-accent-hover" : "border-border bg-surface text-muted hover:text-ink"}`}
                        >
                          {r.label}
                        </button>
                      );
                    })}
                  </div>
                </div>
              </div>
            )}
          </Section>
        )}

        {onKnowledgeChange && (
          <Section title="Conhecimento" open={open.knowledge} onToggle={() => setOpen({ ...open, knowledge: !open.knowledge })}>
            {kbBases.length === 0 ? (
              <p className="text-xs text-muted">Nenhuma base criada. Suba documentos em <span className="text-ink-soft">Espaço → Conhecimento</span>.</p>
            ) : (
              <div className="space-y-3">
                <div className="flex items-center justify-between">
                  <p className="text-xs text-muted">Bases consultadas só neste chat (além das do modelo).</p>
                  {kbBasesSel.length > 0 && (
                    <button onClick={() => onKnowledgeChange(null)} className="text-xs text-muted hover:text-ink">Limpar</button>
                  )}
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {kbBases.map((b) => {
                    const on = kbBasesSel.includes(b.id);
                    return (
                      <button
                        key={b.id}
                        onClick={() => patchKb({ bases: on ? kbBasesSel.filter((x) => x !== b.id) : [...kbBasesSel, b.id] })}
                        className={`rounded-full border px-3 py-1 text-xs transition-colors ${on ? "border-accent/40 bg-accent/15 text-accent-hover" : "border-border bg-surface text-muted hover:text-ink"}`}
                      >
                        {b.name}
                      </button>
                    );
                  })}
                </div>
                {kbBasesSel.length > 0 && (
                  <div>
                    <p className="mb-1.5 text-xs font-medium uppercase tracking-wider text-muted">Modo</p>
                    <select
                      value={kb.mode || "auto"}
                      onChange={(e) => patchKb({ mode: e.target.value as "auto" | "tool" })}
                      className="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm text-ink outline-none focus:border-accent"
                    >
                      <option value="auto">Automático (injeta + cita)</option>
                      <option value="tool">Ferramenta (a IA busca)</option>
                    </select>
                  </div>
                )}
              </div>
            )}
          </Section>
        )}

        {onBrainChange && (
          <Section title="Cérebro" open={open.brain} onToggle={() => setOpen({ ...open, brain: !open.brain })}>
            {brains.length === 0 ? (
              <p className="text-xs text-muted">Nenhum cérebro criado. Crie um em <span className="text-ink-soft">Espaço → Cérebros</span>.</p>
            ) : (
              <div className="space-y-3">
                <div className="flex items-center justify-between">
                  <p className="text-xs text-muted">Cérebros usados só neste chat (além dos do modelo).</p>
                  {brSel.length > 0 && (
                    <button onClick={() => onBrainChange(null)} className="text-xs text-muted hover:text-ink">Limpar</button>
                  )}
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {brains.map((b) => {
                    const on = brSel.includes(b.id);
                    return (
                      <button
                        key={b.id}
                        onClick={() => patchBrain({ brains: on ? brSel.filter((x) => x !== b.id) : [...brSel, b.id] })}
                        className={`rounded-full border px-3 py-1 text-xs transition-colors ${on ? "border-accent/40 bg-accent/15 text-accent-hover" : "border-border bg-surface text-muted hover:text-ink"}`}
                      >
                        {b.name}
                      </button>
                    );
                  })}
                </div>
                {brSel.length > 0 && (
                  <div className="flex items-center justify-between">
                    <p className="text-sm text-ink">Escrita pela IA</p>
                    <MemToggle on={br.write !== false} onClick={() => patchBrain({ write: br.write === false })} />
                  </div>
                )}
              </div>
            )}
          </Section>
        )}

        <Section title="Parâmetros Avançados" open={open.advanced} onToggle={() => setOpen({ ...open, advanced: !open.advanced })}>
          <div className="divide-y divide-border/40">
            {PARAMS.map((p) => (
              <ParamControl key={p.key} spec={p} value={params[p.key]} onChange={(v) => setParam(p.key, v)} />
            ))}
          </div>
        </Section>
      </div>

      <div className="border-t border-border p-3">
        <button
          onClick={() => onSave(systemPrompt || null, params)}
          className="w-full rounded-xl bg-accent py-2 text-sm font-medium text-ink transition-colors hover:bg-accent-hover"
        >
          Salvar controles
        </button>
      </div>
    </aside>
  );
}
