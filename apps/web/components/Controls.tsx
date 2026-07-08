"use client";

import { useState } from "react";
import { ChevronDown, ChevronRight, SlidersHorizontal, X } from "lucide-react";
import type { MemoryConfig } from "@/lib/types";

const MEM_WRITE: { value: string; label: string }[] = [
  { value: "global", label: "Global" },
  { value: "model", label: "Do modelo" },
  { value: "chat", label: "Só este chat" },
  { value: "off", label: "Não salvar" },
];
const MEM_READ: { key: "global" | "model" | "chat"; label: string }[] = [
  { key: "global", label: "Global" },
  { key: "model", label: "Modelo" },
  { key: "chat", label: "Chat" },
];
const MEM_DEFAULT: Required<MemoryConfig> = {
  enabled: true, write: "global", read: { global: true, model: true, chat: true }, review: false,
};

// Lista de parâmetros do painel (espelha o OpenWebUI). Os numéricos comuns
// (temperature, top_p, etc.) são enviados ao OpenRouter; os demais são ignorados
// por provedores que não os suportam.
const PARAMS: { key: string; label: string }[] = [
  { key: "stream", label: "Stream Resposta do Chat" },
  { key: "stream_delta_size", label: "Tamanho do bloco delta do stream" },
  { key: "function_calling", label: "Chamada de função" },
  { key: "reasoning_tags", label: "Tags de raciocínio" },
  { key: "seed", label: "Seed" },
  { key: "stop", label: "Sequência de Parada" },
  { key: "temperature", label: "Temperatura" },
  { key: "reasoning_effort", label: "Esforço de raciocínio" },
  { key: "logit_bias", label: "logit_bias" },
  { key: "max_tokens", label: "max_tokens" },
  { key: "top_k", label: "top_k" },
  { key: "top_p", label: "top_p" },
  { key: "min_p", label: "min_p" },
  { key: "frequency_penalty", label: "frequency_penalty" },
  { key: "presence_penalty", label: "presence_penalty" },
  { key: "mirostat", label: "mirostat" },
  { key: "mirostat_eta", label: "mirostat_eta" },
  { key: "mirostat_tau", label: "mirostat_tau" },
  { key: "repeat_last_n", label: "repeat_last_n" },
  { key: "tfs_z", label: "tfs_z" },
  { key: "repeat_penalty", label: "repeat_penalty" },
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

function ParamRow({
  label,
  value,
  onChange,
}: {
  label: string;
  value: unknown;
  onChange: (v: string) => void;
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
            onChange(e.target.value);
            setEditing(false);
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter") (e.target as HTMLInputElement).blur();
          }}
          className="w-24 rounded-md border border-accent bg-surface px-2 py-0.5 text-right text-xs text-ink outline-none"
        />
      ) : (
        <button
          onClick={() => setEditing(true)}
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
  onMemoryChange,
  onSave,
  onClose,
}: {
  systemPrompt: string;
  params: Record<string, unknown>;
  /** memória do chat ativo (null = herda). Ausente = sem chat ativo (rascunho). */
  memory?: MemoryConfig | null;
  /** padrão efetivo herdado (perfil/modelo) — p/ mostrar o estado quando herda */
  memoryDefault?: MemoryConfig;
  onMemoryChange?: (cfg: MemoryConfig | null) => void;
  onSave: (systemPrompt: string | null, params: Record<string, unknown>) => void;
  onClose: () => void;
}) {
  const [systemPrompt, setSystemPrompt] = useState(initialSystemPrompt ?? "");
  const [params, setParams] = useState<Record<string, unknown>>(initialParams ?? {});
  const [open, setOpen] = useState({ system: true, advanced: true, memory: true });

  // memória do chat: memory=null → herda o padrão (memoryDefault). `mem` é a config
  // EFETIVA mostrada; ao mexer, materializa uma config explícita (com enabled:true
  // quando ligada) e aplica na hora via onMemoryChange.
  const base = memory ?? memoryDefault ?? MEM_DEFAULT;
  const mem: Required<MemoryConfig> = {
    ...MEM_DEFAULT, ...base,
    read: { ...MEM_DEFAULT.read, ...(base.read ?? {}) },
  };
  const inheriting = !memory;
  const memEnabled = mem.enabled !== false;
  const patchMem = (p: Partial<MemoryConfig>) =>
    onMemoryChange?.({ ...mem, enabled: true, ...p, read: { ...mem.read, ...(p.read ?? {}) } });

  function setParam(key: string, raw: string) {
    setParams((p) => {
      const next = { ...p };
      if (raw === "") delete next[key];
      else next[key] = isNaN(Number(raw)) ? raw : Number(raw);
      return next;
    });
  }

  return (
    <aside className="flex w-80 shrink-0 flex-col border-l border-transparent bg-sidebar transition-colors hover:border-border">
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
                    {MEM_WRITE.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                  </select>
                </div>
                <div>
                  <p className="mb-1.5 text-xs font-medium uppercase tracking-wider text-muted">Ler memórias de (união)</p>
                  <div className="flex flex-wrap gap-1.5">
                    {MEM_READ.map((r) => {
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

        <Section title="Parâmetros Avançados" open={open.advanced} onToggle={() => setOpen({ ...open, advanced: !open.advanced })}>
          <div className="divide-y divide-border/40">
            {PARAMS.map((p) => (
              <ParamRow key={p.key} label={p.label} value={params[p.key]} onChange={(v) => setParam(p.key, v)} />
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
