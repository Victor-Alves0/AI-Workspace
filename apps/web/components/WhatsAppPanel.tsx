"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Check,
  ChevronLeft,
  Copy,
  Loader2,
  Plus,
  QrCode,
  RefreshCw,
  Search,
  Settings,
  Trash2,
  TriangleAlert,
  X,
} from "lucide-react";
import { SiWhatsapp } from "react-icons/si";
import { api, API_URL, ApiError } from "@/lib/api";
import { copyText } from "@/lib/clipboard";
import type { MemoryBank, Model, ModelConfig, WhatsAppConnection, WhatsAppFilters, WhatsAppMemory } from "@/lib/types";
import ModelField from "./ModelField";
import ContextWindowSelect from "./ContextWindowSelect";
import { InfoDot, Select, Toggle } from "@/components/ui";
import { tr } from "@/lib/i18n";

const inputCls =
  "mt-1 w-full rounded-lg border border-border bg-surface2 px-3 py-1.5 text-sm text-ink outline-none focus:border-accent placeholder:text-muted";
const ctrlCls =
  "w-44 shrink-0 rounded-lg border border-border bg-surface2 px-3 py-1.5 text-sm text-ink outline-none focus:border-accent";

const infoUnofficial = () => tr("Viola os termos do WhatsApp e pode levar ao banimento do número; prefira um número secundário.");
const infoOfficial = () => tr("Crie um app WhatsApp Business no Meta for Developers e registre o webhook mostrado na conexão (o servidor precisa de uma URL pública https).");
const infoAutoReply = () => tr("Desligado: ninguém responde sozinho. O número fica disponível para a IA do chat ver o que chegou, ler e enviar (ferramenta Mensagens).");

function statusDot(c: WhatsAppConnection): { color: string; label: string } {
  if (!c.enabled) return { color: "bg-surface2", label: tr("desativada") };
  const s = c.state?.status || "";
  if (c.provider === "official") return { color: "bg-green-500", label: tr("configurada") };
  if (s === "open") return { color: "bg-green-500", label: tr("conectada") };
  if (s === "connecting") return { color: "bg-yellow-500", label: tr("conectando") };
  return { color: "bg-red-400", label: tr("desconectada") };
}

/** Tela "WhatsApp" (aberta pelo card em Integrações): números conectados (QR não
 *  oficial / Meta Cloud API). Cada número pode ser atendido por um modelo ou ficar
 *  só à disposição da IA do chat; os ajustes ficam na engrenagem. */
export default function WhatsAppPanel({ onBack }: { onBack: () => void }) {
  const [conns, setConns] = useState<WhatsAppConnection[] | null>(null);
  const [evoAvailable, setEvoAvailable] = useState(false);
  const [models, setModels] = useState<ModelConfig[]>([]);
  const [extModels, setExtModels] = useState<Model[]>([]);
  const [adding, setAdding] = useState<"" | "evolution" | "official">("");
  const [qrConn, setQrConn] = useState<WhatsAppConnection | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [q, setQ] = useState("");

  const load = useCallback(async () => {
    try {
      const r = await api.get<{ evolution_available: boolean; connections: WhatsAppConnection[] }>(
        "/integrations/whatsapp",
      );
      setEvoAvailable(r.evolution_available);
      setConns(r.connections);
    } catch {
      setConns([]);
    }
  }, []);
  useEffect(() => {
    load();
    api.get<ModelConfig[]>("/models").then(setModels).catch(() => {});
    Promise.all([
      api.get<Model[]>("/settings/models").catch(() => [] as Model[]),
      api.get<Model[]>("/integrations/ollama/models").catch(() => [] as Model[]),
      api.get<Model[]>("/integrations/subscriptions/chatgpt/models").catch(() => [] as Model[]),
    ]).then(([ext, local, subs]) => setExtModels([...ext, ...local, ...subs])).catch(() => {});
  }, [load]);

  const lista = useMemo(() => {
    const t = q.trim().toLowerCase();
    const digits = t.replace(/\D/g, "");
    if (!conns || !t) return conns ?? [];
    return conns.filter((c) =>
      (c.label || "").toLowerCase().includes(t) || (digits && (c.phone || "").includes(digits)),
    );
  }, [conns, q]);

  const modelName = (c: WhatsAppConnection) => {
    if (c.model_config_id) return models.find((m) => m.id === c.model_config_id)?.name || c.model;
    return extModels.find((m) => m.id === c.model)?.name || c.model;
  };
  const editingConn = conns?.find((c) => c.id === editing) ?? null;

  return (
    <div className="pt-1">
      <button onClick={onBack} className="mb-3 flex items-center gap-1 text-sm text-muted transition-colors hover:text-ink">
        <ChevronLeft size={16} />  {tr("Voltar")}
      </button>

      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2.5">
          <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-surface2 text-accent-hover">
            <SiWhatsapp size={17} />
          </span>
          <p className="text-sm font-semibold text-ink">WhatsApp</p>
        </div>
        <AddMenu evoAvailable={evoAvailable} onPick={(p) => setAdding(p)} />
      </div>

      {err && <p className="mt-3 flex items-center gap-1.5 text-xs text-red-400"><TriangleAlert size={13} /> {err}</p>}

      {conns == null ? (
        <div className="flex justify-center py-10"><Loader2 size={18} className="animate-spin text-muted" /></div>
      ) : conns.length === 0 ? (
        <div className="mt-4 rounded-xl border border-dashed border-border px-4 py-8 text-center text-sm text-muted">
          {tr("Nenhum número conectado.")}
        </div>
      ) : (
        <div className="mt-4 overflow-hidden rounded-xl border border-border bg-surface">
          <div className="flex items-center gap-2 border-b border-border px-3 py-2">
            <Search size={14} className="shrink-0 text-muted" />
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={tr("Buscar número ou nome…")} aria-label={tr("Buscar número ou nome…")}
              className="w-full bg-transparent text-sm text-ink outline-none placeholder:text-muted" />
          </div>
          {/* ~4 números à vista; o resto rola aqui dentro */}
          <div className="max-h-[232px] overflow-y-auto p-1.5">
            {lista.length === 0 ? (
              <p className="px-3 py-3.5 text-center text-xs text-muted">{tr("Nada encontrado.")}</p>
            ) : lista.map((c) => (
              <ConnectionRow
                key={c.id}
                conn={c}
                modelName={modelName(c)}
                onToggle={async () => {
                  setErr(null);
                  try {
                    await api.patch(`/integrations/whatsapp/${c.id}`, { enabled: !c.enabled });
                    await load();
                  } catch (e) {
                    setErr(e instanceof ApiError ? e.message : tr("Falha ao salvar"));
                  }
                }}
                onQr={() => setQrConn(c)}
                onSettings={() => setEditing(c.id)}
              />
            ))}
          </div>
        </div>
      )}

      {adding === "evolution" && (
        <NewQrForm
          models={models} extModels={extModels}
          onClose={() => setAdding("")}
          onCreated={(c) => { setAdding(""); load(); setQrConn(c); }}
        />
      )}
      {adding === "official" && (
        <NewOfficialForm
          models={models} extModels={extModels}
          onClose={() => setAdding("")}
          onCreated={() => { setAdding(""); load(); }}
        />
      )}
      {qrConn && <QrModal conn={qrConn} onClose={() => { setQrConn(null); load(); }} />}
      {editingConn && (
        <ConnectionSettings
          conn={editingConn} models={models} extModels={extModels}
          onChanged={load}
          onClose={() => setEditing(null)}
        />
      )}
    </div>
  );
}

function AddMenu({ evoAvailable, onPick }: { evoAvailable: boolean; onPick: (p: "evolution" | "official") => void }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const h = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, []);
  return (
    <div className="relative" ref={ref}>
      <button onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-1.5 rounded-full bg-accent px-3.5 py-1.5 text-xs font-medium text-white transition-colors hover:bg-accent-hover">
        <Plus size={14} />  {tr("Conectar número")}
      </button>
      {open && (
        <div className="absolute right-0 top-9 z-30 w-56 rounded-xl border border-border bg-surface p-1 shadow-menu">
          <div className="flex items-center gap-1 rounded-lg pr-2 transition-colors hover:bg-hover">
            <button
              onClick={() => { setOpen(false); onPick("evolution"); }}
              disabled={!evoAvailable}
              title={evoAvailable ? undefined : tr("Indisponível nesta instalação")}
              className="flex min-w-0 flex-1 items-center gap-2.5 px-2.5 py-2 text-left text-sm text-ink disabled:cursor-not-allowed disabled:opacity-50"
            >
              <QrCode size={15} className="shrink-0 text-accent-hover" />
              {tr("Não oficial")}
            </button>
            <InfoDot text={infoUnofficial()} />
          </div>
          <div className="flex items-center gap-1 rounded-lg pr-2 transition-colors hover:bg-hover">
            <button
              onClick={() => { setOpen(false); onPick("official"); }}
              className="flex min-w-0 flex-1 items-center gap-2.5 px-2.5 py-2 text-left text-sm text-ink"
            >
              <SiWhatsapp size={14} className="shrink-0 text-accent-hover" />
              {tr("API Oficial")}
            </button>
            <InfoDot text={infoOfficial()} />
          </div>
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------------- */
/* Linha de um número                                                         */
/* ------------------------------------------------------------------------- */
function ConnectionRow({
  conn, modelName, onToggle, onQr, onSettings,
}: {
  conn: WhatsAppConnection;
  modelName: string;
  onToggle: () => void;
  onQr: () => void;
  onSettings: () => void;
}) {
  const dot = statusDot(conn);
  const connected = conn.provider === "official" || conn.state?.status === "open";
  return (
    <div className="flex items-center gap-2.5 rounded-lg px-2 py-2 transition-colors hover:bg-hover">
      <span title={dot.label} className={`h-2.5 w-2.5 shrink-0 rounded-full ${dot.color}`} />
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium text-ink">
          {conn.label || (conn.phone ? `+${conn.phone}` : tr("Sem nome"))}
          {conn.phone && conn.label && <span className="ml-1.5 font-normal text-muted">+{conn.phone}</span>}
        </p>
        <p className="truncate text-[11px] text-muted">
          {conn.provider === "official" ? tr("API Oficial") : tr("Não oficial")}
          {" · "}
          {conn.auto_reply ? (modelName || tr("Sem modelo")) : tr("Só a IA do chat")}
          {conn.threads > 0 && ` · ${conn.threads === 1 ? tr("1 conversa") : tr("{n} conversas", { n: conn.threads })}`}
          {conn.state?.last_error && <span className="text-red-400" title={String(conn.state.last_error)}> · {tr("erro")}</span>}
        </p>
      </div>
      {conn.provider === "evolution" && !connected && conn.enabled && (
        <button onClick={onQr} className="flex items-center gap-1 rounded-full border border-border px-2.5 py-1 text-xs text-ink-soft transition-colors hover:bg-hover hover:text-ink">
          <QrCode size={13} />  {tr("Conectar")}
        </button>
      )}
      <button onClick={onSettings} title={tr("Configurações")} aria-label={tr("Configurações")}
        className="rounded-lg p-1 text-muted transition-colors hover:bg-hover hover:text-ink">
        <Settings size={15} />
      </button>
      <Toggle on={conn.enabled} onChange={onToggle} />
    </div>
  );
}

/* ------------------------------------------------------------------------- */
/* Configurações de um número (engrenagem)                                    */
/* ------------------------------------------------------------------------- */
type Tab = "general" | "conversation" | "filters" | "contacts" | "humanize" | "webhook";

/** Linha de ajuste: rótulo (+ ⓘ) à esquerda, controle à direita. */
function Row({ label, info, children }: { label: string; info?: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3 py-1.5">
      <span className="flex min-w-0 items-center gap-1.5 text-sm text-ink-soft">
        <span className="truncate">{label}</span>
        {info && <InfoDot text={info} />}
      </span>
      {children}
    </div>
  );
}

function Label({ text, info }: { text: string; info?: string }) {
  return (
    <span className="flex items-center gap-1.5 text-xs text-muted">
      {text}
      {info && <InfoDot text={info} />}
    </span>
  );
}

const MEM_DEFAULT: WhatsAppMemory = { enabled: true, write: "chat", read: { global: false, model: false, chat: true }, banks: [] };

/** Memória efetiva: a configurada, ou a escolha antiga (local/global) traduzida. */
function memOf(c: WhatsAppConnection): WhatsAppMemory {
  if (c.memory_config) return { ...MEM_DEFAULT, ...c.memory_config };
  return c.memory === "global"
    ? { enabled: true, write: "model", read: { global: true, model: true, chat: true }, banks: [] }
    : MEM_DEFAULT;
}

function ConnectionSettings({
  conn, models, extModels, onChanged, onClose,
}: {
  conn: WhatsAppConnection;
  models: ModelConfig[];
  extModels: Model[];
  onChanged: () => Promise<void>;
  onClose: () => void;
}) {
  const [tab, setTab] = useState<Tab>("general");
  const [err, setErr] = useState<string | null>(null);
  const [confirmDel, setConfirmDel] = useState(false);
  const f = conn.filters;

  async function patch(body: Record<string, unknown>) {
    setErr(null);
    try {
      await api.patch(`/integrations/whatsapp/${conn.id}`, body);
      await onChanged();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : tr("Falha ao salvar"));
    }
  }
  const setFilters = (p: Partial<WhatsAppFilters>) => patch({ filters: { ...f, ...p } });

  async function remove() {
    setErr(null);
    try {
      await api.del(`/integrations/whatsapp/${conn.id}`);
      onClose();
      await onChanged();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : tr("Falha ao excluir"));
    }
  }

  const tabs: { id: Tab; label: string }[] = [
    { id: "general", label: tr("Geral") },
    ...(conn.auto_reply
      ? ([
          { id: "conversation", label: tr("Conversa") },
          { id: "filters", label: tr("Filtros") },
          { id: "contacts", label: tr("Contatos") },
          { id: "humanize", label: tr("Humanizador") },
        ] as { id: Tab; label: string }[])
      : []),
    ...(conn.provider === "official" ? [{ id: "webhook" as Tab, label: tr("Webhook") }] : []),
  ];
  const cur = tabs.some((t) => t.id === tab) ? tab : "general";
  const modelValue = conn.model_config_id ? `custom:${conn.model_config_id}` : conn.model;

  return (
    <ModalShell
      title={conn.label || (conn.phone ? `+${conn.phone}` : tr("Sem nome"))}
      onClose={onClose}
      wide
    >
      <div className="-mx-5 -mt-4 mb-1 flex gap-1 overflow-x-auto border-b border-border px-4">
        {tabs.map((t) => (
          <button key={t.id} onClick={() => setTab(t.id)}
            className={`shrink-0 border-b-2 px-2.5 py-2 text-xs transition-colors ${cur === t.id ? "border-accent text-ink" : "border-transparent text-muted hover:text-ink"}`}>
            {t.label}
          </button>
        ))}
      </div>

      {err && <p className="flex items-center gap-1.5 text-xs text-red-400"><TriangleAlert size={13} /> {err}</p>}
      {conn.state?.last_error && (
        <p className="flex items-start gap-1.5 text-[11px] leading-4 text-red-400"><TriangleAlert size={12} className="mt-0.5 shrink-0" /> {String(conn.state.last_error).slice(0, 240)}</p>
      )}

      <div className="max-h-[60vh] min-h-[220px] overflow-y-auto pr-1">
        {cur === "general" && (
          <div className="space-y-1">
            <label className="block text-sm">
              <Label text={tr("Nome")} />
              <input key={`l-${conn.id}`} defaultValue={conn.label} onBlur={(e) => { if (e.target.value !== conn.label) patch({ label: e.target.value }); }} placeholder={tr("Ex.: Atendimento")} className={inputCls} />
            </label>
            <div className="pt-1">
              <Row label={tr("Atender automaticamente")} info={infoAutoReply()}>
                <Toggle on={conn.auto_reply} onChange={(v) => patch({ auto_reply: v })} />
              </Row>
            </div>
            {conn.auto_reply && (
              <>
                <div>
                  <Label text={tr("Modelo")} />
                  <div className="mt-1">
                    <ModelField
                      models={extModels} custom={models} includeCustom
                      value={modelValue}
                      onChange={(v) => {
                        if (v.startsWith("custom:")) {
                          const mc = models.find((m) => m.id === v.slice(7));
                          patch({ model_config_id: v.slice(7), model: mc?.base_model ?? "" });
                        } else {
                          patch({ clear_model_config: true, model: v });
                        }
                      }}
                    />
                  </div>
                </div>
                <label className="block pt-2 text-sm">
                  <Label text={tr("Prompt adicional")} info={tr("Junta-se ao System Prompt do modelo, só neste número.")} />
                  <textarea
                    rows={3}
                    defaultValue={conn.system_prompt}
                    onBlur={(e) => { if (e.target.value !== conn.system_prompt) patch({ system_prompt: e.target.value }); }}
                    placeholder={tr("Ex.: \"Responda curto, informal, sem Markdown.\"")}
                    className={`${inputCls} resize-y`}
                  />
                </label>
              </>
            )}
          </div>
        )}

        {cur === "conversation" && (
          <div className="divide-y divide-border">
            <div className="pb-2">
              <Row label={tr("Contexto")} info={tr("Quantas mensagens anteriores da conversa a IA enxerga.")}>
                <ContextWindowSelect value={conn.context_window} onChange={(v) => patch({ context_window: v })} className={ctrlCls} />
              </Row>
              <Row label={tr("Compactação automática")} info={tr("Resume o começo das conversas longas antes de responder, como nos chats.")}>
                <Toggle on={!!conn.compaction} onChange={(v) => patch({ compaction: v })} />
              </Row>
              <Row label={tr("Agrupar mensagens seguidas")} info={tr("Espera o contato parar de digitar e responde tudo de uma vez.")}>
                <Select value={conn.debounce_seconds ?? 0} onChange={(e) => patch({ debounce_seconds: Number(e.target.value) })} className={ctrlCls}>
                  <option value={0}>{tr("Desligado")}</option>
                  <option value={3}>{tr("3s de silêncio")}</option>
                  <option value={5}>{tr("5s de silêncio")}</option>
                  <option value={8}>{tr("8s de silêncio")}</option>
                  <option value={15}>{tr("15s de silêncio")}</option>
                </Select>
              </Row>
            </div>
            <MemorySection mem={memOf(conn)} onChange={(m) => patch({ memory_config: m })} />
            <div className="pt-2">
              <Row label={tr("Limites por contato")} info={tr("Mensagens por contato; 0 = sem limite. Ao atingir, o contato é ignorado até a janela renovar (o permanente zera apagando o chat da conversa).")}>
                <span />
              </Row>
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                {([["total", tr("Permanente")], ["per_hour", tr("Por hora")], ["per_day", tr("Por dia")], ["per_month", tr("Por mês")]] as const).map(([k, lbl]) => (
                  <label key={k} className="block text-sm">
                    <Label text={lbl} />
                    <input
                      type="number" min={0}
                      defaultValue={conn.limits?.[k] ?? 0}
                      onBlur={(e) => {
                        const v = Math.max(0, parseInt(e.target.value || "0", 10) || 0);
                        if (v !== (conn.limits?.[k] ?? 0)) patch({ limits: { ...conn.limits, [k]: v } });
                      }}
                      className={inputCls}
                    />
                  </label>
                ))}
              </div>
            </div>
          </div>
        )}

        {cur === "filters" && (
          <div className="space-y-1">
            <Row label={tr("Quem pode interagir")}>
              <Select value={f.policy} onChange={(e) => setFilters({ policy: e.target.value as WhatsAppFilters["policy"] })} className={ctrlCls}>
                <option value="all">{tr("Todos")}</option>
                <option value="allow">{tr("Só os permitidos")}</option>
                <option value="block">{tr("Todos, exceto bloqueados")}</option>
              </Select>
            </Row>
            {f.policy !== "all" && (
              <label className="block pb-1 text-sm">
                <Label text={f.policy === "allow" ? tr("Números permitidos") : tr("Números bloqueados")} info={tr("Um por linha ou separados por vírgula.")} />
                <textarea
                  key={`${conn.id}-${f.policy}`}
                  rows={3}
                  defaultValue={(f.policy === "allow" ? f.allow : f.block).join("\n")}
                  onBlur={(e) => {
                    const list = e.target.value.split(/[\n,;]+/).map((s) => s.trim()).filter(Boolean);
                    setFilters(f.policy === "allow" ? { allow: list } : { block: list });
                  }}
                  placeholder="5511999999999"
                  className={`${inputCls} resize-y font-mono text-xs`}
                />
              </label>
            )}
            <Row label={tr("Prefixo-gatilho")} info={tr("Só responde mensagens que começam com ele (ex.: \"!ia\"). Vazio = todas.")}>
              <input defaultValue={f.trigger} onBlur={(e) => { if (e.target.value !== f.trigger) setFilters({ trigger: e.target.value }); }} placeholder="!ia" className={ctrlCls} />
            </Row>
            <Row label={tr("Responder em grupos")}>
              <Toggle on={!!f.groups} onChange={(v) => setFilters({ groups: v })} />
            </Row>
          </div>
        )}

        {cur === "contacts" && (
          <ContactRoles contacts={conn.contacts ?? []} onChange={(contacts) => patch({ contacts })} />
        )}

        {cur === "humanize" && (
          <Humanizer humanize={conn.humanize ?? {}} onChange={(humanize) => patch({ humanize })} />
        )}

        {cur === "webhook" && <OfficialWebhookInfo conn={conn} />}
      </div>

      <div className="flex items-center justify-end border-t border-border pt-3">
        {confirmDel ? (
          <span className="flex items-center gap-2 text-xs">
            <span className="text-muted">{tr("Excluir esta conexão?")}</span>
            <button onClick={remove} className="rounded-full bg-red-500/90 px-3 py-1 font-medium text-white hover:bg-red-500">{tr("Excluir")}</button>
            <button onClick={() => setConfirmDel(false)} className="rounded-full border border-border px-3 py-1 text-muted hover:text-ink">{tr("Cancelar")}</button>
          </span>
        ) : (
          <button onClick={() => setConfirmDel(true)} className="flex items-center gap-1 text-xs text-muted transition-colors hover:text-red-400">
            <Trash2 size={13} />  {tr("Excluir conexão")}
          </button>
        )}
      </div>
    </ModalShell>
  );
}

/* Memória no mesmo formato da do modelo: onde salvar, de onde ler e bancos. */
function MemorySection({ mem, onChange }: { mem: WhatsAppMemory; onChange: (m: WhatsAppMemory) => void }) {
  const [banks, setBanks] = useState<MemoryBank[]>([]);
  useEffect(() => { api.get<MemoryBank[]>("/memory/banks").then(setBanks).catch(() => {}); }, []);
  const on = mem.enabled !== false;
  const read = { ...MEM_DEFAULT.read, ...(mem.read ?? {}) };
  const chip = (active: boolean) =>
    `rounded-full border px-3 py-1 text-xs transition-colors ${active ? "border-accent/40 bg-accent/15 text-accent-hover" : "border-border text-muted hover:text-ink"}`;
  return (
    <div className="space-y-2 py-2">
      <Row label={tr("Memória")} info={tr("Fatos que a IA guarda das conversas e usa depois.")}>
        <Toggle on={on} onChange={(v) => onChange({ ...mem, enabled: v })} />
      </Row>
      {on && (
        <>
          <Row label={tr("Salvar em")} info={tr("Onde as memórias novas ficam guardadas.")}>
            <Select value={mem.write ?? "chat"} onChange={(e) => onChange({ ...mem, write: e.target.value })} className={ctrlCls}>
              <option value="chat">{tr("Só a conversa")}</option>
              <option value="model">{tr("Do modelo")}</option>
              <option value="global">{tr("Global")}</option>
              <option value="off">{tr("Não salvar")}</option>
              {banks.length > 0 && (
                <optgroup label={tr("Bancos")}>
                  {banks.map((b) => <option key={b.id} value={`bank:${b.id}`}>{tr("Banco:")} {b.name}</option>)}
                </optgroup>
              )}
            </Select>
          </Row>
          <div className="flex items-center justify-between gap-3">
            <Label text={tr("Ler de")} info={tr("De onde a IA lê memórias (junta todas as marcadas).")} />
            <div className="flex flex-wrap justify-end gap-1.5">
              {([["chat", tr("Conversa")], ["model", tr("Modelo")], ["global", tr("Global")]] as const).map(([k, lbl]) => (
                <button key={k} onClick={() => onChange({ ...mem, read: { ...read, [k]: !read[k] } })} className={chip(!!read[k])}>{lbl}</button>
              ))}
            </div>
          </div>
          {banks.length > 0 && (
            <div className="flex items-center justify-between gap-3">
              <Label text={tr("Bancos")} info={tr("Memória compartilhada entre modelos. Lê dos bancos marcados.")} />
              <div className="flex flex-wrap justify-end gap-1.5">
                {banks.map((b) => {
                  const sel = (mem.banks ?? []).includes(b.id);
                  return (
                    <button key={b.id} className={chip(sel)}
                      onClick={() => {
                        const cur = mem.banks ?? [];
                        onChange({ ...mem, banks: sel ? cur.filter((x) => x !== b.id) : [...cur, b.id] });
                      }}>
                      {b.name}
                    </button>
                  );
                })}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}

/* Contexto/roles por número: lista de contatos com papel + contexto que o
 * modelo recebe quando aquele número conversa. */
type ContactRole = WhatsAppConnection["contacts"][number];

function ContactRoles({ contacts, onChange }: {
  contacts: ContactRole[];
  onChange: (list: ContactRole[]) => void;
}) {
  const [draft, setDraft] = useState<ContactRole | null>(null);
  const [q, setQ] = useState("");

  const save = (idx: number, patch: Partial<ContactRole>) => {
    const next = contacts.map((c, i) => (i === idx ? { ...c, ...patch } : c));
    onChange(next);
  };
  const t = q.trim().toLowerCase();
  const shown = contacts
    .map((c, i) => ({ c, i }))
    .filter(({ c }) => !t || c.number.includes(t.replace(/\D/g, "") || t) || `${c.name} ${c.role}`.toLowerCase().includes(t));

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <div className="flex flex-1 items-center gap-2 rounded-lg border border-border bg-surface2 px-2.5 py-1.5">
          <Search size={13} className="shrink-0 text-muted" />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={tr("Buscar contato…")}
            className="w-full bg-transparent text-sm text-ink outline-none placeholder:text-muted" />
        </div>
        <InfoDot text={tr("O que a IA sabe de cada número (papel e contexto); vai junto sempre que ele conversa.")} />
        {!draft && (
          <button
            onClick={() => setDraft({ number: "", name: "", role: "", context: "" })}
            className="flex shrink-0 items-center gap-1 rounded-full border border-border px-2.5 py-1 text-xs text-ink-soft transition-colors hover:bg-hover hover:text-ink"
          >
            <Plus size={12} />  {tr("Adicionar")}
          </button>
        )}
      </div>

      {draft && (
        <div className="space-y-1.5 rounded-lg border border-accent/40 bg-surface px-2.5 py-2">
          <div className="grid grid-cols-2 gap-1.5 sm:grid-cols-3">
            <input autoFocus value={draft.number} onChange={(e) => setDraft({ ...draft, number: e.target.value })} placeholder={tr("Número (ex.: 5583999999999)")} className={`${inputCls} mt-0 font-mono text-xs`} />
            <input value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} placeholder={tr("Nome (opcional)")} className={`${inputCls} mt-0`} />
            <input value={draft.role} onChange={(e) => setDraft({ ...draft, role: e.target.value })} placeholder={tr("Role/Função")} className={`${inputCls} mt-0`} />
          </div>
          <textarea rows={2} value={draft.context} onChange={(e) => setDraft({ ...draft, context: e.target.value })} placeholder={tr("Contexto/prompt adicional")} className={`${inputCls} mt-0 resize-y`} />
          <div className="flex items-center justify-end gap-2">
            <button onClick={() => setDraft(null)} className="rounded-full border border-border px-3 py-1 text-xs text-muted transition-colors hover:text-ink">{tr("Cancelar")}</button>
            <button
              disabled={!draft.number.trim()}
              onClick={() => { onChange([...contacts, draft]); setDraft(null); }}
              className="rounded-full bg-accent px-3 py-1 text-xs font-medium text-white transition-colors hover:bg-accent-hover disabled:opacity-50"
            >
              {tr("Salvar contato")}
            </button>
          </div>
        </div>
      )}

      {contacts.length === 0 && !draft ? (
        <p className="py-4 text-center text-xs text-muted">{tr("Nenhum contato.")}</p>
      ) : shown.length === 0 && !draft ? (
        <p className="py-4 text-center text-xs text-muted">{tr("Nada encontrado.")}</p>
      ) : (
        <div className="max-h-[300px] space-y-1.5 overflow-y-auto">
          {shown.map(({ c, i }) => (
            <div key={`${c.number}-${i}`} className="space-y-1.5 rounded-lg border border-border bg-surface px-2.5 py-2">
              <div className="grid grid-cols-2 gap-1.5 sm:grid-cols-3">
                <input defaultValue={c.number} onBlur={(e) => { if (e.target.value !== c.number) save(i, { number: e.target.value }); }} placeholder={tr("Número")} className={`${inputCls} mt-0 font-mono text-xs`} />
                <input defaultValue={c.name} onBlur={(e) => { if (e.target.value !== c.name) save(i, { name: e.target.value }); }} placeholder={tr("Nome (opcional)")} className={`${inputCls} mt-0`} />
                <input defaultValue={c.role} onBlur={(e) => { if (e.target.value !== c.role) save(i, { role: e.target.value }); }} placeholder={tr("Role/Função")} className={`${inputCls} mt-0`} />
              </div>
              <div className="flex items-start gap-1.5">
                <textarea rows={1} defaultValue={c.context} onBlur={(e) => { if (e.target.value !== c.context) save(i, { context: e.target.value }); }} placeholder={tr("Contexto/prompt adicional")} className={`${inputCls} mt-0 flex-1 resize-y`} />
                <button onClick={() => onChange(contacts.filter((_, j) => j !== i))} title={tr("Remover")} className="mt-1 shrink-0 rounded p-1 text-muted transition-colors hover:text-red-400">
                  <Trash2 size={14} />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/* Modo humanizador: "digitando…", atraso e quebra de mensagens. */
type Humanize = WhatsAppConnection["humanize"];

function Humanizer({ humanize, onChange }: { humanize: Humanize; onChange: (h: Humanize) => void }) {
  const h = humanize ?? {};
  const on = !!h.enabled;
  const set = (p: Partial<Humanize>) => onChange({ ...h, ...p });
  const minS = h.min_seconds ?? 1;
  const maxS = h.max_seconds ?? 6;
  const sec = (v: string) => Math.max(0, Math.min(120, parseInt(v || "0", 10) || 0));
  return (
    <div className="space-y-1">
      <Row label={tr("Modo humanizador")} info={tr("Simula uma pessoa: mostra “digitando…”, espera um tempo e pode responder em várias mensagens.")}>
        <Toggle on={on} onChange={(v) => set({ enabled: v })} />
      </Row>
      {on && (
        <>
          <Row label={tr("Mostrar “digitando…”")}>
            <Toggle on={h.typing !== false} onChange={(v) => set({ typing: v })} />
          </Row>
          <Row label={tr("Cortar em várias mensagens")} info={tr("Quebra respostas longas em mensagens naturais.")}>
            <Toggle on={!!h.split} onChange={(v) => set({ split: v })} />
          </Row>
          <Row label={tr("Atraso (s)")} info={tr("O tempo real varia com o tamanho da mensagem, entre o mínimo e o máximo.")}>
            <span className="flex items-center gap-1.5">
              <input type="number" min={0} max={120} defaultValue={minS} aria-label={tr("Atraso mínimo (s)")}
                onBlur={(e) => { const v = sec(e.target.value); if (v !== minS) set({ min_seconds: v }); }}
                className="w-16 rounded-lg border border-border bg-surface2 px-2 py-1.5 text-sm text-ink outline-none focus:border-accent" />
              <span className="text-xs text-muted">–</span>
              <input type="number" min={0} max={120} defaultValue={maxS} aria-label={tr("Atraso máximo (s)")}
                onBlur={(e) => { const v = sec(e.target.value); if (v !== maxS) set({ max_seconds: v }); }}
                className="w-16 rounded-lg border border-border bg-surface2 px-2 py-1.5 text-sm text-ink outline-none focus:border-accent" />
            </span>
          </Row>
        </>
      )}
    </div>
  );
}

function CopyField({ label, value }: { label: string; value: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="text-sm">
      <span className="text-xs text-muted">{label}</span>
      <div className="mt-1 flex items-center gap-1.5">
        <code className="min-w-0 flex-1 truncate rounded-lg border border-border bg-surface2 px-2.5 py-1.5 font-mono text-[11px] text-ink">{value}</code>
        <button
          onClick={async () => { try { await copyText(value); setCopied(true); setTimeout(() => setCopied(false), 1200); } catch { /* ignore */ } }}
          className="rounded-lg border border-border p-1.5 text-muted transition-colors hover:bg-hover hover:text-ink"
        >
          {copied ? <Check size={13} className="text-green-400" /> : <Copy size={13} />}
        </button>
      </div>
    </div>
  );
}

function OfficialWebhookInfo({ conn }: { conn: WhatsAppConnection }) {
  return (
    <div className="space-y-2">
      <Label text={tr("Meta for Developers")} info={tr("Registre em WhatsApp → Configuration → Webhook e assine o campo messages. A URL precisa ser pública (https); atrás de túnel/proxy, defina WHATSAPP_WEBHOOK_BASE.")} />
      <CopyField label={tr("URL de callback")} value={`${API_URL}${conn.webhook_path}`} />
      <CopyField label={tr("Verify token")} value={conn.verify_token} />
    </div>
  );
}

/* ------------------------------------------------------------------------- */
/* Criação                                                                    */
/* ------------------------------------------------------------------------- */
function ModalShell({ title, onClose, children, wide }: { title: string; onClose: () => void; children: React.ReactNode; wide?: boolean }) {
  // sem overflow-hidden/auto: o dropdown do seletor de modelo precisa "vazar"
  // para fora do modal (senão fica clipado dentro dele)
  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/60 p-4" onClick={onClose}>
      <div className={`w-full ${wide ? "max-w-lg" : "max-w-md"} rounded-2xl border border-border bg-bg shadow-menu`} onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between border-b border-border px-5 py-3">
          <p className="truncate text-sm font-semibold text-ink">{title}</p>
          <button onClick={onClose} className="rounded-lg p-1 text-muted hover:bg-hover hover:text-ink"><X size={18} /></button>
        </div>
        <div className="space-y-3 px-5 py-4">{children}</div>
      </div>
    </div>
  );
}

function useModelPick(models: ModelConfig[]) {
  const [modelConfigId, setModelConfigId] = useState<string | null>(null);
  const [model, setModel] = useState("");
  const value = modelConfigId ? `custom:${modelConfigId}` : model;
  function pick(v: string) {
    if (v.startsWith("custom:")) {
      const mc = models.find((m) => m.id === v.slice(7));
      setModelConfigId(v.slice(7));
      setModel(mc?.base_model ?? "");
    } else {
      setModelConfigId(null);
      setModel(v);
    }
  }
  return { modelConfigId, model, value, pick };
}

/** "Atender automaticamente" + modelo (só quando atende). */
function ReplyPick({ m, auto, setAuto, models, extModels }: {
  m: ReturnType<typeof useModelPick>;
  auto: boolean;
  setAuto: (v: boolean) => void;
  models: ModelConfig[];
  extModels: Model[];
}) {
  return (
    <>
      <Row label={tr("Atender automaticamente")} info={infoAutoReply()}>
        <Toggle on={auto} onChange={setAuto} />
      </Row>
      {auto && (
        <div>
          <Label text={tr("Modelo")} />
          <div className="mt-1"><ModelField models={extModels} custom={models} includeCustom value={m.value} onChange={m.pick} /></div>
        </div>
      )}
    </>
  );
}

function NewQrForm({
  models, extModels, onClose, onCreated,
}: {
  models: ModelConfig[]; extModels: Model[];
  onClose: () => void; onCreated: (c: WhatsAppConnection) => void;
}) {
  const [label, setLabel] = useState("");
  const [auto, setAuto] = useState(true);
  const m = useModelPick(models);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function create() {
    if (auto && !m.value) return setErr(tr("Selecione o modelo que vai atender este número"));
    setSaving(true);
    setErr(null);
    try {
      const c = await api.post<WhatsAppConnection>("/integrations/whatsapp", {
        label, provider: "evolution", auto_reply: auto,
        model_config_id: auto ? m.modelConfigId : null, model: auto ? m.model : "",
      });
      onCreated(c);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : tr("Falha ao criar a conexão"));
      setSaving(false);
    }
  }

  return (
    <ModalShell title={tr("Conectar número (não oficial)")} onClose={onClose}>
      <label className="block text-sm">
        <Label text={tr("Nome (opcional)")} />
        <input value={label} onChange={(e) => setLabel(e.target.value)} placeholder={tr("Ex.: Meu número secundário")} className={inputCls} />
      </label>
      <ReplyPick m={m} auto={auto} setAuto={setAuto} models={models} extModels={extModels} />
      {err && <p className="text-xs text-red-400">{err}</p>}
      <div className="flex items-center justify-between gap-2 pt-1">
        <span className="flex items-center gap-1.5 text-[11px] text-yellow-500/90">
          <TriangleAlert size={12} /> {tr("Não oficial")} <InfoDot text={infoUnofficial()} />
        </span>
        <span className="flex gap-2">
          <button onClick={onClose} className="rounded-full border border-border px-4 py-1.5 text-xs text-muted hover:text-ink">{tr("Cancelar")}</button>
          <button onClick={create} disabled={saving} className="rounded-full bg-accent px-5 py-1.5 text-xs font-medium text-white hover:bg-accent-hover disabled:opacity-60">
            {saving ? "…" : tr("Criar e gerar QR")}
          </button>
        </span>
      </div>
    </ModalShell>
  );
}

function NewOfficialForm({
  models, extModels, onClose, onCreated,
}: {
  models: ModelConfig[]; extModels: Model[];
  onClose: () => void; onCreated: () => void;
}) {
  const [label, setLabel] = useState("");
  const [phone, setPhone] = useState("");
  const [pnid, setPnid] = useState("");
  const [token, setToken] = useState("");
  const [secret, setSecret] = useState("");
  const [auto, setAuto] = useState(true);
  const m = useModelPick(models);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function create() {
    if (!pnid.trim() || !token.trim()) return setErr(tr("Phone Number ID e Access Token são obrigatórios"));
    if (auto && !m.value) return setErr(tr("Selecione o modelo que vai atender este número"));
    setSaving(true);
    setErr(null);
    try {
      await api.post("/integrations/whatsapp", {
        label, provider: "official", auto_reply: auto,
        model_config_id: auto ? m.modelConfigId : null, model: auto ? m.model : "",
        phone, phone_number_id: pnid, access_token: token, app_secret: secret,
      });
      onCreated();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : tr("Falha ao criar a conexão"));
      setSaving(false);
    }
  }

  return (
    <ModalShell title={tr("Conectar via API Oficial")} onClose={onClose}>
      <div className="grid grid-cols-2 gap-2">
        <label className="block text-sm">
          <Label text={tr("Nome (opcional)")} />
          <input value={label} onChange={(e) => setLabel(e.target.value)} placeholder={tr("Ex.: Atendimento")} className={inputCls} />
        </label>
        <label className="block text-sm">
          <Label text={tr("Número (opcional)")} />
          <input value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="5511999999999" className={inputCls} />
        </label>
      </div>
      <label className="block text-sm">
        <Label text={tr("Phone Number ID")} info={tr("No painel do app: WhatsApp → API Setup.")} />
        <input value={pnid} onChange={(e) => setPnid(e.target.value)} className={`${inputCls} font-mono text-xs`} />
      </label>
      <label className="block text-sm">
        <Label text={tr("Access Token (permanente)")} />
        <input type="password" value={token} onChange={(e) => setToken(e.target.value)} placeholder="EAAG…" className={`${inputCls} font-mono text-xs`} />
      </label>
      <label className="block text-sm">
        <Label text={tr("App Secret (opcional)")} info={tr("Valida a assinatura dos webhooks.")} />
        <input type="password" value={secret} onChange={(e) => setSecret(e.target.value)} className={`${inputCls} font-mono text-xs`} />
      </label>
      <ReplyPick m={m} auto={auto} setAuto={setAuto} models={models} extModels={extModels} />
      {err && <p className="text-xs text-red-400">{err}</p>}
      <div className="flex items-center justify-between gap-2 pt-1">
        <InfoDot text={infoOfficial()} />
        <span className="flex gap-2">
          <button onClick={onClose} className="rounded-full border border-border px-4 py-1.5 text-xs text-muted hover:text-ink">{tr("Cancelar")}</button>
          <button onClick={create} disabled={saving} className="rounded-full bg-accent px-5 py-1.5 text-xs font-medium text-white hover:bg-accent-hover disabled:opacity-60">
            {saving ? "…" : tr("Conectar")}
          </button>
        </span>
      </div>
    </ModalShell>
  );
}

/* ------------------------------------------------------------------------- */
/* Modal do QR (poll até conectar)                                            */
/* ------------------------------------------------------------------------- */
function QrModal({ conn, onClose }: { conn: WhatsAppConnection; onClose: () => void }) {
  const [qr, setQr] = useState<string>("");
  const [status, setStatus] = useState<string>("connecting");
  const [phone, setPhone] = useState<string>(conn.phone);
  const [err, setErr] = useState<string | null>(null);

  const fetchQr = useCallback(async () => {
    try {
      const r = await api.get<{ base64: string; code: string }>(`/integrations/whatsapp/${conn.id}/qr`);
      if (r.base64) setQr(r.base64);
      setErr(null);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : tr("Falha ao obter o QR"));
    }
  }, [conn.id]);

  useEffect(() => {
    let alive = true;
    fetchQr();
    const t = setInterval(async () => {
      try {
        const s = await api.get<{ status: string; phone: string }>(`/integrations/whatsapp/${conn.id}/status`);
        if (!alive) return;
        setStatus(s.status);
        if (s.phone) setPhone(s.phone);
        if (s.status !== "open") fetchQr(); // QR expira (~40s) — renova junto do poll
      } catch { /* mantém o último estado */ }
    }, 3500);
    return () => { alive = false; clearInterval(t); };
  }, [conn.id, fetchQr]);

  const connected = status === "open";
  return (
    <ModalShell title={connected ? tr("Número conectado") : tr("Escaneie o QR Code")} onClose={onClose}>
      {connected ? (
        <div className="flex flex-col items-center gap-2 py-4 text-center">
          <span className="flex h-12 w-12 items-center justify-center rounded-full bg-green-500/15 text-green-500"><Check size={24} /></span>
          <p className="text-sm text-ink">{phone ? `+${phone}` : tr("Sessão aberta")}</p>
        </div>
      ) : (
        <div className="flex flex-col items-center gap-3 py-2">
          {qr ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={qr} alt={tr("QR Code do WhatsApp")} className="h-56 w-56 rounded-xl border border-border bg-white p-2" />
          ) : (
            <div className="flex h-56 w-56 items-center justify-center rounded-xl border border-border bg-surface">
              {err ? <TriangleAlert size={20} className="text-red-400" /> : <Loader2 size={20} className="animate-spin text-muted" />}
            </div>
          )}
          {err && <p className="max-w-xs text-center text-xs text-red-400">{err}</p>}
          <p className="max-w-xs text-center text-xs text-muted">
            {tr("WhatsApp → Configurações → Aparelhos conectados → Conectar aparelho")}
          </p>
          <button onClick={fetchQr} className="flex items-center gap-1.5 rounded-full border border-border px-3 py-1 text-xs text-ink-soft transition-colors hover:bg-hover hover:text-ink">
            <RefreshCw size={12} />  {tr("Gerar novo QR")}
          </button>
        </div>
      )}
    </ModalShell>
  );
}
