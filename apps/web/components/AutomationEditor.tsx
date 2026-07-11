"use client";

import { useEffect, useMemo, useState } from "react";
import { Wrench, X } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import type { Automation, AutomationOptions, Chat, Model, ModelConfig, SystemTool, TelegramConnection, Tool, WhatsAppConnection } from "@/lib/types";
import ModelField from "./ModelField";
import TransferModal, { type TransferItem } from "./TransferModal";

type Draft = Pick<
  Automation,
  "title" | "kind" | "model_config_id" | "model" | "instructions" | "tool_ids" | "pinned_tool_ids" | "target" | "options" | "schedule" | "watcher_type" | "watcher_config" | "interval_seconds"
>;

const UNITS: { key: "minutes" | "hours" | "days"; label: string }[] = [
  { key: "minutes", label: "minuto(s)" },
  { key: "hours", label: "hora(s)" },
  { key: "days", label: "dia(s)" },
];

const FREQ_MODES: { key: string; label: string }[] = [
  { key: "interval", label: "A cada intervalo" },
  { key: "daily", label: "Diariamente" },
  { key: "weekly", label: "Dias da semana" },
  { key: "monthly", label: "Mensal (dia do mês)" },
];

// getDay() do JS: 0=domingo … 6=sábado
const WEEKDAYS = ["Dom", "Seg", "Ter", "Qua", "Qui", "Sex", "Sáb"];

const TTL_OPTS: { key: string; label: string }[] = [
  { key: "1", label: "1 hora" },
  { key: "12", label: "12 horas" },
  { key: "24", label: "24 horas" },
  { key: "168", label: "7 dias" },
  { key: "view_once", label: "Ao abrir (visualização única)" },
];

const REASONING_OPTS: { key: string; label: string }[] = [
  { key: "", label: "Padrão do modelo" },
  { key: "off", label: "Desligado" },
  { key: "low", label: "Baixo" },
  { key: "medium", label: "Médio" },
  { key: "high", label: "Alto" },
];

const WATCHERS: { key: string; label: string }[] = [
  { key: "page", label: "Mudança em página" },
  { key: "web_search", label: "Busca na web + condição" },
  { key: "price", label: "Preço de ativo" },
  { key: "rss", label: "RSS / notícias" },
];

function blank(): Draft {
  return {
    title: "",
    kind: "scheduled",
    model_config_id: null,
    model: "",
    instructions: "",
    tool_ids: [],
    pinned_tool_ids: [],
    target: { mode: "reuse" },
    options: {},
    schedule: { mode: "interval", every: 1, unit: "hours" },
    watcher_type: "page",
    watcher_config: {},
    interval_seconds: 300,
  };
}

function Toggle({ on, onClick }: { on: boolean; onClick: () => void }) {
  return (
    <button type="button" onClick={onClick} className={`relative h-6 w-11 shrink-0 rounded-full transition-colors ${on ? "bg-accent" : "bg-surface2"}`}>
      <span className={`absolute top-0.5 h-5 w-5 rounded-full bg-white transition-all ${on ? "left-[22px]" : "left-0.5"}`} />
    </button>
  );
}

const WA_INPUT = "mt-1 w-full rounded-lg border border-border bg-surface2 px-3 py-2 text-sm text-ink outline-none focus:border-accent placeholder:text-muted";

/* Entrega o resultado da automação por um número do WhatsApp: escolhe a conexão
 * (quem envia) e o destino (número específico / contatos cadastrados / todas as
 * conversas existentes). Guardado em target.whatsapp. */
type WaDelivery = { enabled?: boolean; connection_id?: string | null; to?: "number" | "contacts" | "threads"; number?: string };

function WhatsAppDelivery({ value, connections, onChange }: {
  value: WaDelivery;
  connections: WhatsAppConnection[];
  onChange: (v: WaDelivery) => void;
}) {
  const v = value ?? {};
  const on = !!v.enabled;
  const set = (p: Partial<WaDelivery>) => onChange({ ...v, ...p });
  const to = v.to ?? "number";
  const conn = connections.find((c) => c.id === v.connection_id);
  return (
    <div className="space-y-2 rounded-xl border border-border bg-surface px-3 py-2.5">
      <div className="flex items-center justify-between gap-3">
        <div>
          <p className="text-sm text-ink">Enviar para o WhatsApp</p>
          <p className="text-xs text-muted">Entrega o resultado por um número conectado</p>
        </div>
        <Toggle on={on} onClick={() => set({ enabled: !on })} />
      </div>
      {on && (
        <div className="space-y-2 border-t border-border pt-2.5">
          {connections.length === 0 ? (
            <p className="text-xs text-amber-400/80">Nenhum número conectado. Conecte em Configurações → Integrações → WhatsApp.</p>
          ) : (
            <>
              <label className="block text-sm">
                <span className="text-xs text-muted">Número que envia (conexão)</span>
                <select value={v.connection_id ?? ""} onChange={(e) => set({ connection_id: e.target.value || null })} className={WA_INPUT}>
                  <option value="">Selecione…</option>
                  {connections.map((c) => (
                    <option key={c.id} value={c.id}>{c.label || c.phone || "Sem nome"}{c.phone ? ` (+${c.phone})` : ""}</option>
                  ))}
                </select>
              </label>
              <label className="block text-sm">
                <span className="text-xs text-muted">Enviar para</span>
                <select value={to} onChange={(e) => set({ to: e.target.value as WaDelivery["to"] })} className={WA_INPUT}>
                  <option value="number">Um número específico</option>
                  <option value="contacts">Todos os contatos cadastrados{conn ? ` (${conn.contacts?.length ?? 0})` : ""}</option>
                  <option value="threads">Todas as conversas existentes{conn ? ` (${conn.threads ?? 0})` : ""}</option>
                </select>
              </label>
              {to === "number" && (
                <label className="block text-sm">
                  <span className="text-xs text-muted">Número (com DDI + DDD)</span>
                  <input value={v.number ?? ""} onChange={(e) => set({ number: e.target.value })} placeholder="5583999999999" className={`${WA_INPUT} font-mono text-xs`} />
                </label>
              )}
              {to === "threads" && (
                <p className="text-[11px] leading-4 text-amber-400/70">Envia a MESMA mensagem a todos que já conversaram com esse número. Use com cuidado (evite spam/bloqueio).</p>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}

/** Entrega do resultado ao Telegram: bot conectado + destino (todas as conversas
 * ou um chat_id específico). Guardado em target.telegram. */
type TgDelivery = { enabled?: boolean; connection_id?: string | null; mode?: "threads" | "chat"; chat_id?: string };

function TelegramDelivery({ value, connections, onChange }: {
  value: TgDelivery;
  connections: TelegramConnection[];
  onChange: (v: TgDelivery) => void;
}) {
  const v = value ?? {};
  const on = !!v.enabled;
  const set = (p: Partial<TgDelivery>) => onChange({ ...v, ...p });
  const mode = v.mode ?? "threads";
  const conn = connections.find((c) => c.id === v.connection_id);
  return (
    <div className="space-y-2 rounded-xl border border-border bg-surface px-3 py-2.5">
      <div className="flex items-center justify-between gap-3">
        <div>
          <p className="text-sm text-ink">Enviar para o Telegram</p>
          <p className="text-xs text-muted">Entrega o resultado por um bot conectado</p>
        </div>
        <Toggle on={on} onClick={() => set({ enabled: !on })} />
      </div>
      {on && (
        <div className="space-y-2 border-t border-border pt-2.5">
          {connections.length === 0 ? (
            <p className="text-xs text-amber-400/80">Nenhum bot conectado. Conecte em Configurações → Integrações → Telegram.</p>
          ) : (
            <>
              <label className="block text-sm">
                <span className="text-xs text-muted">Bot que envia</span>
                <select value={v.connection_id ?? ""} onChange={(e) => set({ connection_id: e.target.value || null })} className={WA_INPUT}>
                  <option value="">Selecione…</option>
                  {connections.map((c) => (
                    <option key={c.id} value={c.id}>{c.label || `@${c.bot_username}`}</option>
                  ))}
                </select>
              </label>
              <label className="block text-sm">
                <span className="text-xs text-muted">Enviar para</span>
                <select value={mode} onChange={(e) => set({ mode: e.target.value as TgDelivery["mode"] })} className={WA_INPUT}>
                  <option value="threads">Todas as conversas do bot{conn ? ` (${conn.threads ?? 0})` : ""}</option>
                  <option value="chat">Um chat específico (id)</option>
                </select>
              </label>
              {mode === "chat" && (
                <label className="block text-sm">
                  <span className="text-xs text-muted">chat_id do Telegram</span>
                  <input value={v.chat_id ?? ""} onChange={(e) => set({ chat_id: e.target.value })} placeholder="123456789" className={`${WA_INPUT} font-mono text-xs`} />
                </label>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}

export default function AutomationEditor({
  automation,
  onClose,
  onSaved,
}: {
  automation: Automation | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const isNew = automation === null;
  const [d, setD] = useState<Draft>(() =>
    automation
      ? {
          title: automation.title,
          kind: automation.kind,
          model_config_id: automation.model_config_id,
          model: automation.model,
          instructions: automation.instructions,
          tool_ids: automation.tool_ids ?? [],
          pinned_tool_ids: automation.pinned_tool_ids ?? [],
          target: automation.target ?? { mode: "reuse" },
          options: automation.options ?? {},
          schedule: automation.schedule ?? { mode: "interval", every: 1, unit: "hours" },
          watcher_type: automation.watcher_type ?? "page",
          watcher_config: automation.watcher_config ?? {},
          interval_seconds: automation.interval_seconds ?? 300,
        }
      : blank(),
  );
  const [models, setModels] = useState<ModelConfig[]>([]);
  const [extModels, setExtModels] = useState<Model[]>([]);
  const [chats, setChats] = useState<Chat[]>([]);
  const [waConns, setWaConns] = useState<WhatsAppConnection[]>([]);
  const [tgConns, setTgConns] = useState<TelegramConnection[]>([]);
  const [tools, setTools] = useState<Tool[]>([]);
  const [systemTools, setSystemTools] = useState<SystemTool[]>([]);
  const [toolsModal, setToolsModal] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const isMonitor = d.kind === "monitor";

  // ESC fecha do "de dentro pra fora": modal de ferramentas primeiro, editor depois
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      if (toolsModal) setToolsModal(false);
      else onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose, toolsModal]);

  useEffect(() => {
    api.get<ModelConfig[]>("/models").then(setModels).catch(() => {});
    Promise.all([
      api.get<Model[]>("/settings/models").catch(() => [] as Model[]),
      api.get<Model[]>("/integrations/ollama/models").catch(() => [] as Model[]),
    ]).then(([ext, local]) => setExtModels([...ext, ...local])).catch(() => {});
    api.get<Chat[]>("/chats").then(setChats).catch(() => {});
    api.get<{ connections: WhatsAppConnection[] }>("/integrations/whatsapp").then((r) => setWaConns(r.connections ?? [])).catch(() => {});
    api.get<TelegramConnection[]>("/integrations/telegram/connections").then(setTgConns).catch(() => {});
    api.get<Tool[]>("/tools").then(setTools).catch(() => {});
    api.get<SystemTool[]>("/tools/system").then(setSystemTools).catch(() => {});
  }, []);

  const set = <K extends keyof Draft>(k: K, v: Draft[K]) => setD((p) => ({ ...p, [k]: v }));
  const wc = (k: string, v: any) => set("watcher_config", { ...(d.watcher_config ?? {}), [k]: v });
  const setOpt = (k: keyof AutomationOptions, v: any) => set("options", { ...(d.options ?? {}), [k]: v });
  const sched = (patch: Record<string, any>) => set("schedule", { ...d.schedule, ...patch });

  const transferItems: TransferItem[] = useMemo(
    () => [
      ...systemTools.map((st) => ({ key: `builtin:${st.path}`, label: st.name, sublabel: st.description, group: "Sistema", system: true })),
      ...tools.map((t) => ({ key: t.id, label: t.name || t.path, sublabel: t.path, group: "Usuário" })),
    ],
    [systemTools, tools],
  );
  const toolLabel = (key: string) => transferItems.find((i) => i.key === key)?.label ?? key;

  // valor do ModelField: modelo custom => "custom:<id>"; externo (OpenRouter) => o id
  const modelValue = d.model_config_id ? `custom:${d.model_config_id}` : d.model;
  function pickModel(value: string) {
    if (value.startsWith("custom:")) {
      const mc = models.find((m) => m.id === value.slice(7));
      set("model_config_id", value.slice(7));
      set("model", mc?.base_model ?? "");
    } else {
      set("model_config_id", null);
      set("model", value);
    }
  }

  function validateMonitor(): string | null {
    const c = d.watcher_config ?? {};
    if (d.watcher_type === "price" && !(c.symbol || "").trim()) return "Informe o ticker do ativo";
    if (d.watcher_type === "web_search" && !(c.query || "").trim()) return "Informe a busca";
    if ((d.watcher_type === "page" || d.watcher_type === "rss") && !(c.url || "").trim()) return "Informe a URL";
    return null;
  }

  async function save() {
    setErr(null);
    if (!isMonitor && !d.model_config_id && !d.model) return setErr("Selecione um modelo");
    if (!isMonitor && !d.instructions.trim()) return setErr("Escreva a instrução da automação");
    if (isMonitor) {
      const v = validateMonitor();
      if (v) return setErr(v);
    }
    const mode = d.schedule.mode ?? "interval";
    if (!isMonitor && mode === "weekly" && !(d.schedule.days ?? []).length)
      return setErr("Escolha ao menos um dia da semana");
    // fuso do navegador acompanha o horário escolhido (o servidor converte p/ UTC)
    const schedule = mode === "interval"
      ? { mode, every: d.schedule.every ?? 1, unit: d.schedule.unit ?? "hours" }
      : { ...d.schedule, mode, time: d.schedule.time || "09:00", tz_offset: new Date().getTimezoneOffset() };
    const body = {
      title: d.title || (isMonitor ? "Novo monitor" : "Nova automação"),
      kind: d.kind,
      model_config_id: d.model_config_id,
      model: d.model,
      instructions: d.instructions,
      tool_ids: d.tool_ids,
      pinned_tool_ids: d.pinned_tool_ids.filter((p) => d.tool_ids.includes(p)),
      target: d.target,
      options: d.options ?? {},
      schedule,
      watcher_type: isMonitor ? d.watcher_type : null,
      watcher_config: isMonitor ? d.watcher_config : {},
      interval_seconds: isMonitor ? d.interval_seconds : null,
    };
    setSaving(true);
    try {
      if (isNew) await api.post("/automations", body);
      else await api.patch(`/automations/${automation!.id}`, body);
      onSaved();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Falha ao salvar");
    } finally {
      setSaving(false);
    }
  }

  const inputCls = "w-full rounded-lg border border-border bg-surface2 px-3 py-2 text-sm text-ink outline-none focus:border-accent placeholder:text-muted";
  const c = d.watcher_config ?? {};
  const freqMode = d.schedule.mode ?? "interval";
  const ttl = d.options?.chat_ttl;
  const ttlOn = ttl != null && ttl !== undefined && String(ttl) !== "";

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/60 p-4" onClick={onClose}>
      <div className="flex max-h-[88vh] w-full max-w-xl flex-col overflow-hidden rounded-2xl border border-border bg-bg shadow-menu" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between border-b border-border px-5 py-3">
          <p className="text-sm font-semibold text-ink">{isNew ? "Nova automação" : "Editar automação"}</p>
          <button onClick={onClose} className="rounded-lg p-1 text-muted hover:bg-hover hover:text-ink"><X size={18} /></button>
        </div>

        <div className="space-y-4 overflow-y-auto px-5 py-4">
          <div className="space-y-1">
            <p className="text-xs text-muted">Título</p>
            <input value={d.title} onChange={(e) => set("title", e.target.value)} placeholder={isMonitor ? "Ex.: PETR4 acima de R$40" : "Ex.: Resumo diário de notícias"} className={inputCls} />
          </div>

          {/* tipo */}
          <div className="grid grid-cols-2 gap-2">
            {[
              { k: "scheduled", label: "Agendada (por tempo)" },
              { k: "monitor", label: "Monitor (por evento)" },
            ].map((o) => (
              <button
                key={o.k}
                onClick={() => set("kind", o.k as any)}
                className={`rounded-xl border px-3 py-2 text-sm transition-colors ${d.kind === o.k ? "border-accent/60 bg-accent/15 text-accent-hover" : "border-border text-muted hover:text-ink"}`}
              >
                {o.label}
              </button>
            ))}
          </div>

          {/* modelo (obrigatório p/ agendada; opcional p/ monitor — só redige o aviso) */}
          <div className="space-y-1">
            <p className="text-xs text-muted">Modelo{isMonitor ? " (opcional — redige o aviso)" : ""}</p>
            <ModelField
              models={extModels}
              custom={models}
              includeCustom
              value={modelValue}
              onChange={pickModel}
              placeholder={isMonitor ? "Nenhum (mensagem direta)" : "Selecionar modelo"}
            />
          </div>

          {/* instrução */}
          <div className="space-y-1">
            <p className="text-xs text-muted">{isMonitor ? "Instrução para o aviso (opcional)" : "Instruções"}</p>
            <textarea
              rows={isMonitor ? 2 : 4}
              value={d.instructions}
              onChange={(e) => set("instructions", e.target.value)}
              placeholder={isMonitor ? "Ex.: Me avise de forma curta e direta." : "O que o modelo deve fazer a cada disparo. Ex.: Pesquise as principais notícias de tecnologia de hoje e resuma em tópicos."}
              className={`${inputCls} resize-y`}
            />
          </div>

          {/* ===== AGENDADA: raciocínio + ferramentas + frequência ===== */}
          {!isMonitor && (
            <>
              <div className="space-y-1">
                <p className="text-xs text-muted">Raciocínio (thinking)</p>
                <select
                  value={d.options?.reasoning ?? ""}
                  onChange={(e) => setOpt("reasoning", e.target.value || null)}
                  className={inputCls}
                >
                  {REASONING_OPTS.map((o) => <option key={o.key} value={o.key}>{o.label}</option>)}
                </select>
              </div>

              <div className="space-y-1.5">
                <div className="flex items-center justify-between">
                  <p className="text-xs text-muted">Ferramentas (opcional — vazio usa as do modelo)</p>
                  <button onClick={() => setToolsModal(true)} className="flex items-center gap-1.5 rounded-full border border-border px-3 py-1 text-xs text-ink transition-colors hover:bg-hover">
                    <Wrench size={13} /> Gerenciar
                  </button>
                </div>
                {d.tool_ids.length > 0 && (
                  <div className="flex flex-wrap gap-1.5">
                    {d.tool_ids.map((tid) => {
                      const pinned = d.pinned_tool_ids.includes(tid);
                      return (
                        <button
                          key={tid}
                          onClick={() => set("pinned_tool_ids", pinned ? d.pinned_tool_ids.filter((x) => x !== tid) : [...d.pinned_tool_ids, tid])}
                          title={pinned ? "Fixada (clique para soltar)" : "Clique para fixar (pin)"}
                          className={`rounded-full border px-2.5 py-0.5 text-xs transition-colors ${pinned ? "border-accent/60 bg-accent/15 text-accent-hover" : "border-border text-muted hover:text-ink"}`}
                        >
                          {pinned ? "📌 " : ""}{toolLabel(tid)}
                        </button>
                      );
                    })}
                  </div>
                )}
              </div>

              <div className="space-y-1.5">
                <p className="text-xs text-muted">Frequência</p>
                <select value={freqMode} onChange={(e) => sched({ mode: e.target.value })} className={inputCls}>
                  {FREQ_MODES.map((m) => <option key={m.key} value={m.key}>{m.label}</option>)}
                </select>

                {freqMode === "interval" && (
                  <div className="flex items-center gap-2 pt-1">
                    <span className="text-sm text-ink-soft">A cada</span>
                    <input type="number" min={1} value={d.schedule.every ?? 1} onChange={(e) => sched({ every: Math.max(1, Number(e.target.value) || 1) })} className="w-20 rounded-lg border border-border bg-surface2 px-3 py-1.5 text-right text-sm text-ink outline-none focus:border-accent" />
                    <select value={d.schedule.unit ?? "hours"} onChange={(e) => sched({ unit: e.target.value })} className="rounded-lg border border-border bg-surface2 px-3 py-1.5 text-sm text-ink outline-none focus:border-accent">
                      {UNITS.map((u) => <option key={u.key} value={u.key}>{u.label}</option>)}
                    </select>
                  </div>
                )}

                {freqMode === "weekly" && (
                  <div className="flex flex-wrap gap-1.5 pt-1">
                    {WEEKDAYS.map((label, i) => {
                      const on = (d.schedule.days ?? []).includes(i);
                      return (
                        <button
                          key={i}
                          type="button"
                          onClick={() => sched({ days: on ? (d.schedule.days ?? []).filter((x) => x !== i) : [...(d.schedule.days ?? []), i].sort() })}
                          className={`rounded-full border px-3 py-1 text-xs transition-colors ${on ? "border-accent/60 bg-accent/15 font-medium text-accent-hover" : "border-border text-muted hover:text-ink"}`}
                        >
                          {label}
                        </button>
                      );
                    })}
                  </div>
                )}

                {freqMode === "monthly" && (
                  <div className="flex items-center gap-2 pt-1">
                    <span className="text-sm text-ink-soft">Dia</span>
                    <input type="number" min={1} max={31} value={d.schedule.day ?? 1} onChange={(e) => sched({ day: Math.max(1, Math.min(31, Number(e.target.value) || 1)) })} className="w-20 rounded-lg border border-border bg-surface2 px-3 py-1.5 text-right text-sm text-ink outline-none focus:border-accent" />
                    <span className="text-xs text-muted">do mês (29-31 caem no último dia em meses curtos)</span>
                  </div>
                )}

                {freqMode !== "interval" && (
                  <div className="flex items-center gap-2 pt-1">
                    <span className="text-sm text-ink-soft">às</span>
                    <input type="time" value={d.schedule.time ?? "09:00"} onChange={(e) => sched({ time: e.target.value })} className="rounded-lg border border-border bg-surface2 px-3 py-1.5 text-sm text-ink outline-none focus:border-accent" />
                    <span className="text-xs text-muted">no seu fuso horário</span>
                  </div>
                )}
              </div>
            </>
          )}

          {/* ===== MONITOR: tipo de watcher + config + intervalo ===== */}
          {isMonitor && (
            <>
              <div className="space-y-1">
                <p className="text-xs text-muted">Tipo de monitor</p>
                <select value={d.watcher_type ?? "page"} onChange={(e) => { set("watcher_type", e.target.value); set("watcher_config", {}); }} className={inputCls}>
                  {WATCHERS.map((w) => <option key={w.key} value={w.key}>{w.label}</option>)}
                </select>
              </div>

              {d.watcher_type === "page" && (
                <>
                  <div className="space-y-1"><p className="text-xs text-muted">URL da página</p>
                    <input value={c.url ?? ""} onChange={(e) => wc("url", e.target.value)} placeholder="https://exemplo.com/inscricoes" className={inputCls} /></div>
                  <div className="space-y-1"><p className="text-xs text-muted">Texto-chave (opcional — dispara quando aparece/some)</p>
                    <input value={c.contains ?? ""} onChange={(e) => wc("contains", e.target.value)} placeholder='ex.: "inscrições abertas"' className={inputCls} /></div>
                </>
              )}
              {d.watcher_type === "web_search" && (
                <>
                  <div className="space-y-1"><p className="text-xs text-muted">Busca</p>
                    <input value={c.query ?? ""} onChange={(e) => wc("query", e.target.value)} placeholder="ex.: placar Brasil jogo hoje" className={inputCls} /></div>
                  <div className="space-y-1"><p className="text-xs text-muted">Condição (opcional — a IA confirma antes de avisar)</p>
                    <input value={c.condition ?? ""} onChange={(e) => wc("condition", e.target.value)} placeholder="ex.: o jogo terminou / saiu o placar final" className={inputCls} /></div>
                </>
              )}
              {d.watcher_type === "price" && (
                <>
                  <div className="space-y-1"><p className="text-xs text-muted">Ativo (ticker Yahoo)</p>
                    <input value={c.symbol ?? ""} onChange={(e) => wc("symbol", e.target.value)} placeholder="ex.: PETR4.SA, AAPL, BTC-USD" className={inputCls} /></div>
                  <div className="flex items-center gap-2">
                    <select value={c.op ?? "above"} onChange={(e) => wc("op", e.target.value)} className="rounded-lg border border-border bg-surface2 px-3 py-2 text-sm text-ink outline-none focus:border-accent">
                      <option value="above">Preço acima de</option>
                      <option value="below">Preço abaixo de</option>
                      <option value="pct">Variação % (±) de</option>
                    </select>
                    <input type="number" value={c.value ?? ""} onChange={(e) => wc("value", Number(e.target.value))} placeholder="valor" className="w-32 rounded-lg border border-border bg-surface2 px-3 py-2 text-right text-sm text-ink outline-none focus:border-accent" />
                  </div>
                </>
              )}
              {d.watcher_type === "rss" && (
                <>
                  <div className="space-y-1"><p className="text-xs text-muted">URL do feed (RSS/Atom)</p>
                    <input value={c.url ?? ""} onChange={(e) => wc("url", e.target.value)} placeholder="https://exemplo.com/feed.xml" className={inputCls} /></div>
                  <div className="space-y-1"><p className="text-xs text-muted">Palavras-chave (opcional, separadas por vírgula)</p>
                    <input
                      value={(c.keywords ?? []).join(", ")}
                      onChange={(e) => wc("keywords", e.target.value.split(",").map((s) => s.trim()).filter(Boolean))}
                      placeholder="ex.: python, inteligência artificial"
                      className={inputCls}
                    /></div>
                </>
              )}

              <div className="space-y-1">
                <p className="text-xs text-muted">Verificar a cada</p>
                <div className="flex items-center gap-2">
                  <input
                    type="number"
                    min={1}
                    value={Math.max(1, Math.round((d.interval_seconds ?? 300) / 60))}
                    onChange={(e) => set("interval_seconds", Math.max(1, Number(e.target.value) || 1) * 60)}
                    className="w-20 rounded-lg border border-border bg-surface2 px-3 py-1.5 text-right text-sm text-ink outline-none focus:border-accent"
                  />
                  <span className="text-sm text-ink-soft">minuto(s)</span>
                </div>
              </div>
            </>
          )}

          {/* local de resposta */}
          <div className="space-y-1">
            <p className="text-xs text-muted">Local de Resposta</p>
            <select
              value={d.target.mode ?? "reuse"}
              onChange={(e) => set("target", { ...d.target, mode: e.target.value as any })}
              className={inputCls}
            >
              <option value="reuse">Sempre no mesmo chat (criado uma vez)</option>
              <option value="new_each">Um chat novo a cada disparo</option>
              <option value="existing">Em um chat existente</option>
            </select>
            {d.target.mode === "existing" && (
              <select value={d.target.chat_id ?? ""} onChange={(e) => set("target", { ...d.target, chat_id: e.target.value || null })} className={inputCls}>
                <option value="">Selecione um chat…</option>
                {chats.map((ch) => <option key={ch.id} value={ch.id}>{ch.title}</option>)}
              </select>
            )}
          </div>

          {/* enviar o resultado para o WhatsApp */}
          <WhatsAppDelivery
            value={d.target.whatsapp ?? {}}
            connections={waConns}
            onChange={(wa) => set("target", { ...d.target, whatsapp: wa })}
          />

          {/* enviar o resultado para o Telegram */}
          <TelegramDelivery
            value={d.target.telegram ?? {}}
            connections={tgConns}
            onChange={(tg) => set("target", { ...d.target, telegram: tg })}
          />

          {/* opções do chat */}
          <div className="space-y-2.5 rounded-xl border border-border bg-surface px-3 py-2.5">
            <div className="flex items-center justify-between gap-3">
              <div>
                <p className="text-sm text-ink">Contexto do Chat</p>
                <p className="text-xs text-muted">O modelo vê as últimas mensagens do chat (disparos anteriores)</p>
              </div>
              <Toggle on={!!d.options?.use_context} onClick={() => setOpt("use_context", !d.options?.use_context)} />
            </div>
            {d.target.mode !== "existing" && (
              <div className="border-t border-border pt-2.5">
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <p className="text-sm text-ink">Duração do Chat</p>
                    <p className="text-xs text-muted">Apaga o chat da automação após o tempo escolhido</p>
                  </div>
                  <Toggle on={ttlOn} onClick={() => setOpt("chat_ttl", ttlOn ? null : 24)} />
                </div>
                {ttlOn && (
                  <select
                    value={String(ttl)}
                    onChange={(e) => setOpt("chat_ttl", e.target.value === "view_once" ? "view_once" : Number(e.target.value))}
                    className={`${inputCls} mt-2`}
                  >
                    {TTL_OPTS.map((o) => <option key={o.key} value={o.key}>{o.label}</option>)}
                  </select>
                )}
              </div>
            )}
          </div>

          {err && <p className="text-sm text-red-400">{err}</p>}
        </div>

        <div className="flex items-center justify-end gap-3 border-t border-border px-5 py-3">
          <button onClick={onClose} className="rounded-full border border-border px-4 py-2 text-sm text-muted hover:text-ink">Cancelar</button>
          <button onClick={save} disabled={saving} className="rounded-full bg-accent px-6 py-2 text-sm font-medium text-white transition-colors hover:bg-accent-hover disabled:opacity-60">
            {saving ? "…" : "Salvar"}
          </button>
        </div>
      </div>

      {/* stopPropagation: sem isto, cliques dentro do modal borbulhavam até o
          overlay do editor (onClick=onClose) e selecionar uma ferramenta fechava tudo */}
      {toolsModal && (
        <div onClick={(e) => e.stopPropagation()}>
          <TransferModal
            title="Ferramentas da automação"
            items={transferItems}
            selected={d.tool_ids}
            onChange={(ids) => set("tool_ids", ids)}
            onClose={() => setToolsModal(false)}
            availableLabel="Disponíveis"
            selectedLabel="Ativadas"
            searchPlaceholder="Buscar ferramentas…"
            pinnedKeys={d.pinned_tool_ids}
            onTogglePin={(tid) => set("pinned_tool_ids", d.pinned_tool_ids.includes(tid) ? d.pinned_tool_ids.filter((x) => x !== tid) : [...d.pinned_tool_ids, tid])}
            pinHint="Fixar: vira ferramenta de 1ª classe (o modelo chama direto), dando mais assertividade à automação."
          />
        </div>
      )}
    </div>
  );
}
