"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  Check,
  ChevronDown,
  ChevronLeft,
  Copy,
  Loader2,
  Plus,
  QrCode,
  RefreshCw,
  Trash2,
  TriangleAlert,
  X,
} from "lucide-react";
import { SiWhatsapp } from "react-icons/si";
import { api, API_URL, ApiError } from "@/lib/api";
import type { Model, ModelConfig, WhatsAppConnection, WhatsAppFilters } from "@/lib/types";
import ModelField from "./ModelField";

const inputCls =
  "mt-1 w-full rounded-lg border border-border bg-surface2 px-3 py-1.5 text-sm text-ink outline-none focus:border-accent placeholder:text-muted";

function Toggle({ on, onClick }: { on: boolean; onClick: () => void }) {
  return (
    <button type="button" onClick={onClick} className={`relative h-5 w-9 shrink-0 rounded-full transition-colors ${on ? "bg-accent" : "bg-surface2"}`}>
      <span className={`absolute top-0.5 h-4 w-4 rounded-full bg-white transition-all ${on ? "left-[18px]" : "left-0.5"}`} />
    </button>
  );
}

function statusDot(c: WhatsAppConnection): { color: string; label: string } {
  if (!c.enabled) return { color: "bg-surface2", label: "desativada" };
  const s = c.state?.status || "";
  if (c.provider === "official") return { color: "bg-green-500", label: "configurada" };
  if (s === "open") return { color: "bg-green-500", label: "conectada" };
  if (s === "connecting") return { color: "bg-yellow-500", label: "conectando" };
  return { color: "bg-red-400", label: "desconectada" };
}

/** Tela "WhatsApp" (aberta pelo card em Integrações): conecta números (QR não
 *  oficial via Evolution / Meta Cloud API oficial), associa cada número a um
 *  modelo e configura filtros + memória por conexão. */
export default function WhatsAppPanel({ onBack }: { onBack: () => void }) {
  const [conns, setConns] = useState<WhatsAppConnection[] | null>(null);
  const [evoAvailable, setEvoAvailable] = useState(false);
  const [models, setModels] = useState<ModelConfig[]>([]);
  const [extModels, setExtModels] = useState<Model[]>([]);
  const [adding, setAdding] = useState<"" | "evolution" | "official">("");
  const [qrConn, setQrConn] = useState<WhatsAppConnection | null>(null);
  const [err, setErr] = useState<string | null>(null);

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
    ]).then(([ext, local]) => setExtModels([...ext, ...local])).catch(() => {});
  }, [load]);

  return (
    <div className="pt-1">
      <button onClick={onBack} className="mb-3 flex items-center gap-1 text-sm text-muted transition-colors hover:text-ink">
        <ChevronLeft size={16} /> Voltar
      </button>

      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2.5">
          <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-surface2 text-accent-hover">
            <SiWhatsapp size={17} />
          </span>
          <div>
            <p className="text-sm font-semibold text-ink">WhatsApp</p>
            <p className="text-xs text-muted">Cada número conectado é atendido por um modelo de IA</p>
          </div>
        </div>
        <div className="relative">
          <AddMenu evoAvailable={evoAvailable} onPick={(p) => setAdding(p)} />
        </div>
      </div>

      {err && <p className="mt-3 flex items-center gap-1.5 text-xs text-red-400"><TriangleAlert size={13} /> {err}</p>}

      {conns == null ? (
        <div className="flex justify-center py-10"><Loader2 size={18} className="animate-spin text-muted" /></div>
      ) : conns.length === 0 && !adding ? (
        <div className="mt-4 rounded-xl border border-dashed border-border px-4 py-8 text-center text-sm text-muted">
          Nenhum número conectado.
        </div>
      ) : (
        <div className="mt-4 space-y-3">
          {conns.map((c) => (
            <ConnectionCard
              key={c.id}
              conn={c}
              models={models}
              extModels={extModels}
              onChanged={load}
              onQr={() => setQrConn(c)}
              setErr={setErr}
            />
          ))}
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

      <p className="mt-4 text-[11px] leading-4 text-muted">
        <span className="text-yellow-500/90">Número pessoal (QR Code)</span>: caminho não oficial —
        viola os termos do WhatsApp e pode levar ao banimento do número; prefira um número
        secundário. <span className="text-ink-soft">API Oficial (Meta)</span>: crie um app WhatsApp
        Business no Meta for Developers e registre o webhook mostrado na conexão (o servidor precisa
        de uma URL pública https).
      </p>
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
        <Plus size={14} /> Conectar número
      </button>
      {open && (
        <div className="absolute right-0 top-9 z-30 w-64 overflow-hidden rounded-xl border border-border bg-surface shadow-menu">
          <button
            onClick={() => { setOpen(false); onPick("evolution"); }}
            disabled={!evoAvailable}
            className="flex w-full items-start gap-2.5 px-3 py-2.5 text-left transition-colors hover:bg-hover disabled:cursor-not-allowed disabled:opacity-50"
          >
            <QrCode size={16} className="mt-0.5 shrink-0 text-accent-hover" />
            <span>
              <span className="block text-sm text-ink">Número pessoal (QR Code)</span>
              <span className="block text-[11px] leading-4 text-muted">
                {evoAvailable ? "Escaneie como no WhatsApp Web (não oficial)" : "Suba o serviço: docker compose --profile whatsapp up -d"}
              </span>
            </span>
          </button>
          <button
            onClick={() => { setOpen(false); onPick("official"); }}
            className="flex w-full items-start gap-2.5 border-t border-border px-3 py-2.5 text-left transition-colors hover:bg-hover"
          >
            <SiWhatsapp size={15} className="mt-0.5 shrink-0 text-accent-hover" />
            <span>
              <span className="block text-sm text-ink">API Oficial (Meta)</span>
              <span className="block text-[11px] leading-4 text-muted">WhatsApp Business Cloud API</span>
            </span>
          </button>
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------------- */
/* Card de uma conexão                                                        */
/* ------------------------------------------------------------------------- */
function ConnectionCard({
  conn, models, extModels, onChanged, onQr, setErr,
}: {
  conn: WhatsAppConnection;
  models: ModelConfig[];
  extModels: Model[];
  onChanged: () => Promise<void>;
  onQr: () => void;
  setErr: (e: string | null) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [confirmDel, setConfirmDel] = useState(false);
  const dot = statusDot(conn);
  const f = conn.filters;

  async function patch(body: Record<string, unknown>) {
    setErr(null);
    try {
      await api.patch(`/integrations/whatsapp/${conn.id}`, body);
      await onChanged();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Falha ao salvar");
    }
  }
  const setFilters = (p: Partial<WhatsAppFilters>) => patch({ filters: { ...f, ...p } });

  async function remove() {
    setErr(null);
    try {
      await api.del(`/integrations/whatsapp/${conn.id}`);
      await onChanged();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Falha ao excluir");
    }
  }

  const modelValue = conn.model_config_id ? `custom:${conn.model_config_id}` : conn.model;

  return (
    <div className="rounded-xl border border-border bg-surface">
      {/* cabeçalho */}
      <div className="flex items-center gap-2.5 px-3 py-2.5">
        <span title={dot.label} className={`h-2.5 w-2.5 shrink-0 rounded-full ${dot.color}`} />
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-medium text-ink">
            {conn.label || conn.phone || "Sem nome"}
            {conn.phone && conn.label && <span className="ml-1.5 font-normal text-muted">+{conn.phone}</span>}
          </p>
          <p className="text-[11px] text-muted">
            {conn.provider === "official" ? "API Oficial (Meta)" : "Não oficial (QR)"} · {conn.threads} conversa{conn.threads === 1 ? "" : "s"}
            {conn.state?.last_error && <span className="text-red-400"> · {String(conn.state.last_error).slice(0, 80)}</span>}
          </p>
        </div>
        {conn.provider === "evolution" && dot.label !== "conectada" && conn.enabled && (
          <button onClick={onQr} className="flex items-center gap-1 rounded-full border border-border px-2.5 py-1 text-xs text-ink-soft transition-colors hover:bg-hover hover:text-ink">
            <QrCode size={13} /> Conectar
          </button>
        )}
        <Toggle on={conn.enabled} onClick={() => patch({ enabled: !conn.enabled })} />
        <button onClick={() => setExpanded((v) => !v)} className="rounded-lg p-1 text-muted transition-colors hover:bg-hover hover:text-ink">
          <ChevronDown size={15} className={`transition-transform ${expanded ? "rotate-180" : ""}`} />
        </button>
      </div>

      {expanded && (
        <div className="space-y-3 border-t border-border px-3 py-3">
          <div className="grid grid-cols-2 gap-2">
            <label className="block text-sm">
              <span className="text-xs text-muted">Nome</span>
              <input defaultValue={conn.label} onBlur={(e) => { if (e.target.value !== conn.label) patch({ label: e.target.value }); }} placeholder="Ex.: Atendimento" className={inputCls} />
            </label>
            <label className="block text-sm">
              <span className="text-xs text-muted">Memória</span>
              <select value={conn.memory} onChange={(e) => patch({ memory: e.target.value })} className={inputCls}>
                <option value="local">Local (isolada por conversa)</option>
                <option value="global">Global (compartilhada do modelo)</option>
              </select>
            </label>
          </div>

          <div>
            <span className="text-xs text-muted">Modelo que atende este número</span>
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

          {/* filtros */}
          <div className="space-y-2 rounded-lg border border-border bg-surface2/40 px-2.5 py-2">
            <p className="text-xs font-semibold text-ink">Filtros</p>
            <div className="grid grid-cols-2 gap-2">
              <label className="block text-sm">
                <span className="text-xs text-muted">Contatos</span>
                <select value={f.policy} onChange={(e) => setFilters({ policy: e.target.value as WhatsAppFilters["policy"] })} className={inputCls}>
                  <option value="all">Todos podem interagir</option>
                  <option value="allow">Somente lista de permissão</option>
                  <option value="block">Todos, exceto bloqueados</option>
                </select>
              </label>
              <label className="block text-sm">
                <span className="text-xs text-muted">Prefixo-gatilho (opcional)</span>
                <input defaultValue={f.trigger} onBlur={(e) => { if (e.target.value !== f.trigger) setFilters({ trigger: e.target.value }); }} placeholder='Ex.: "!ia" — só responde se começar assim' className={inputCls} />
              </label>
            </div>
            {f.policy !== "all" && (
              <label className="block text-sm">
                <span className="text-xs text-muted">
                  {f.policy === "allow" ? "Números permitidos" : "Números bloqueados"} (um por linha ou separados por vírgula)
                </span>
                <textarea
                  rows={2}
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
            <div className="flex items-center justify-between">
              <span className="text-sm text-ink-soft">Responder em grupos</span>
              <Toggle on={!!f.groups} onClick={() => setFilters({ groups: !f.groups })} />
            </div>
          </div>

          {/* prompt adicional deste número (concatenado ao do modelo) */}
          <label className="block text-sm">
            <span className="text-xs text-muted">Prompt adicional deste número (junta-se ao System Prompt do modelo)</span>
            <textarea
              rows={3}
              defaultValue={conn.system_prompt}
              onBlur={(e) => { if (e.target.value !== conn.system_prompt) patch({ system_prompt: e.target.value }); }}
              placeholder={'Ex.: "Responda sempre curto, como mensagem de WhatsApp. Nunca use Markdown. Seja informal."'}
              className={`${inputCls} resize-y`}
            />
          </label>

          {/* limites de uso por contato */}
          <div className="space-y-2 rounded-lg border border-border bg-surface2/40 px-2.5 py-2">
            <p className="text-xs font-semibold text-ink">Limites de uso <span className="font-normal text-muted">— mensagens por contato (0 = sem limite)</span></p>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
              {([["total", "Permanente"], ["per_hour", "Por hora"], ["per_day", "Por dia"], ["per_month", "Por mês"]] as const).map(([k, lbl]) => (
                <label key={k} className="block text-sm">
                  <span className="text-xs text-muted">{lbl}</span>
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
            <p className="text-[11px] leading-4 text-muted">Ao atingir o limite, novas mensagens do contato são ignoradas até a janela renovar (o permanente zera apagando o chat da conversa).</p>
          </div>

          {/* contexto/roles por número */}
          <ContactRoles contacts={conn.contacts ?? []} onChange={(contacts) => patch({ contacts })} />

          {conn.provider === "official" && <OfficialWebhookInfo conn={conn} />}

          <div className="flex items-center justify-end">
            {confirmDel ? (
              <span className="flex items-center gap-2 text-xs">
                <span className="text-muted">Excluir esta conexão?</span>
                <button onClick={remove} className="rounded-full bg-red-500/90 px-3 py-1 font-medium text-white hover:bg-red-500">Excluir</button>
                <button onClick={() => setConfirmDel(false)} className="rounded-full border border-border px-3 py-1 text-muted hover:text-ink">Cancelar</button>
              </span>
            ) : (
              <button onClick={() => setConfirmDel(true)} className="flex items-center gap-1 text-xs text-muted transition-colors hover:text-red-400">
                <Trash2 size={13} /> Excluir conexão
              </button>
            )}
          </div>
        </div>
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

  const save = (idx: number, patch: Partial<ContactRole>) => {
    const next = contacts.map((c, i) => (i === idx ? { ...c, ...patch } : c));
    onChange(next);
  };

  return (
    <div className="space-y-2 rounded-lg border border-border bg-surface2/40 px-2.5 py-2">
      <div className="flex items-center justify-between">
        <p className="text-xs font-semibold text-ink">Contatos <span className="font-normal text-muted">— contexto/role por número</span></p>
        {!draft && (
          <button
            onClick={() => setDraft({ number: "", name: "", role: "", context: "" })}
            className="flex items-center gap-1 rounded-full border border-border px-2 py-0.5 text-[11px] text-ink-soft transition-colors hover:bg-hover hover:text-ink"
          >
            <Plus size={12} /> Adicionar
          </button>
        )}
      </div>

      {contacts.length === 0 && !draft && (
        <p className="text-[11px] leading-4 text-muted">
          Ex.: &quot;+55 83 9…&quot; → &quot;Este número é o dono da empresa.&quot; O modelo recebe isso sempre que o número conversar.
        </p>
      )}

      {contacts.map((c, i) => (
        <div key={`${c.number}-${i}`} className="space-y-1.5 rounded-lg border border-border bg-surface px-2.5 py-2">
          <div className="grid grid-cols-2 gap-1.5 sm:grid-cols-3">
            <input defaultValue={c.number} onBlur={(e) => { if (e.target.value !== c.number) save(i, { number: e.target.value }); }} placeholder="Número" className={`${inputCls} mt-0 font-mono text-xs`} />
            <input defaultValue={c.name} onBlur={(e) => { if (e.target.value !== c.name) save(i, { name: e.target.value }); }} placeholder="Nome (opcional)" className={`${inputCls} mt-0`} />
            <input defaultValue={c.role} onBlur={(e) => { if (e.target.value !== c.role) save(i, { role: e.target.value }); }} placeholder="Role/Função" className={`${inputCls} mt-0`} />
          </div>
          <div className="flex items-start gap-1.5">
            <textarea rows={1} defaultValue={c.context} onBlur={(e) => { if (e.target.value !== c.context) save(i, { context: e.target.value }); }} placeholder="Contexto/prompt adicional (ex.: este número é o dono da empresa)" className={`${inputCls} mt-0 flex-1 resize-y`} />
            <button onClick={() => onChange(contacts.filter((_, j) => j !== i))} title="Remover" className="mt-1 shrink-0 rounded p-1 text-muted transition-colors hover:text-red-400">
              <Trash2 size={14} />
            </button>
          </div>
        </div>
      ))}

      {draft && (
        <div className="space-y-1.5 rounded-lg border border-accent/40 bg-surface px-2.5 py-2">
          <div className="grid grid-cols-2 gap-1.5 sm:grid-cols-3">
            <input autoFocus value={draft.number} onChange={(e) => setDraft({ ...draft, number: e.target.value })} placeholder="Número (ex.: 5583999999999)" className={`${inputCls} mt-0 font-mono text-xs`} />
            <input value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} placeholder="Nome (opcional)" className={`${inputCls} mt-0`} />
            <input value={draft.role} onChange={(e) => setDraft({ ...draft, role: e.target.value })} placeholder="Role/Função" className={`${inputCls} mt-0`} />
          </div>
          <textarea rows={2} value={draft.context} onChange={(e) => setDraft({ ...draft, context: e.target.value })} placeholder="Contexto/prompt adicional" className={`${inputCls} mt-0 resize-y`} />
          <div className="flex items-center justify-end gap-2">
            <button onClick={() => setDraft(null)} className="rounded-full border border-border px-3 py-1 text-xs text-muted transition-colors hover:text-ink">Cancelar</button>
            <button
              disabled={!draft.number.trim()}
              onClick={() => { onChange([...contacts, draft]); setDraft(null); }}
              className="rounded-full bg-accent px-3 py-1 text-xs font-medium text-white transition-colors hover:bg-accent-hover disabled:opacity-50"
            >
              Salvar contato
            </button>
          </div>
        </div>
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
          onClick={async () => { try { await navigator.clipboard.writeText(value); setCopied(true); setTimeout(() => setCopied(false), 1200); } catch { /* ignore */ } }}
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
    <div className="space-y-2 rounded-lg border border-border bg-surface2/40 px-2.5 py-2">
      <p className="text-xs font-semibold text-ink">Webhook (Meta for Developers)</p>
      <CopyField label="URL de callback" value={`${API_URL}${conn.webhook_path}`} />
      <CopyField label="Verify token" value={conn.verify_token} />
      <p className="text-[11px] leading-4 text-muted">
        Registre em WhatsApp → Configuration → Webhook e assine o campo <code>messages</code>. A URL
        precisa ser pública (https) — atrás de túnel/proxy, defina WHATSAPP_WEBHOOK_BASE no .env.
      </p>
    </div>
  );
}

/* ------------------------------------------------------------------------- */
/* Criação                                                                    */
/* ------------------------------------------------------------------------- */
function ModalShell({ title, onClose, children }: { title: string; onClose: () => void; children: React.ReactNode }) {
  // sem overflow-hidden/auto: o dropdown do seletor de modelo precisa "vazar"
  // para fora do modal (senão fica clipado dentro dele)
  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/60 p-4" onClick={onClose}>
      <div className="w-full max-w-md rounded-2xl border border-border bg-bg shadow-menu" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between border-b border-border px-5 py-3">
          <p className="text-sm font-semibold text-ink">{title}</p>
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

function NewQrForm({
  models, extModels, onClose, onCreated,
}: {
  models: ModelConfig[]; extModels: Model[];
  onClose: () => void; onCreated: (c: WhatsAppConnection) => void;
}) {
  const [label, setLabel] = useState("");
  const m = useModelPick(models);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function create() {
    if (!m.value) return setErr("Selecione o modelo que vai atender este número");
    setSaving(true);
    setErr(null);
    try {
      const c = await api.post<WhatsAppConnection>("/integrations/whatsapp", {
        label, provider: "evolution", model_config_id: m.modelConfigId, model: m.model,
      });
      onCreated(c);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Falha ao criar a conexão");
      setSaving(false);
    }
  }

  return (
    <ModalShell title="Conectar número pessoal (QR Code)" onClose={onClose}>
      <label className="block text-sm">
        <span className="text-xs text-muted">Nome (opcional)</span>
        <input value={label} onChange={(e) => setLabel(e.target.value)} placeholder="Ex.: Meu número secundário" className={inputCls} />
      </label>
      <div>
        <span className="text-xs text-muted">Modelo que atende este número</span>
        <div className="mt-1"><ModelField models={extModels} custom={models} includeCustom value={m.value} onChange={m.pick} /></div>
      </div>
      <p className="flex items-start gap-1.5 text-[11px] leading-4 text-yellow-500/90">
        <TriangleAlert size={13} className="mt-0.5 shrink-0" />
        Caminho não oficial: viola os termos do WhatsApp e pode banir o número. Use por sua conta e risco.
      </p>
      {err && <p className="text-xs text-red-400">{err}</p>}
      <div className="flex justify-end gap-2 pt-1">
        <button onClick={onClose} className="rounded-full border border-border px-4 py-1.5 text-xs text-muted hover:text-ink">Cancelar</button>
        <button onClick={create} disabled={saving} className="rounded-full bg-accent px-5 py-1.5 text-xs font-medium text-white hover:bg-accent-hover disabled:opacity-60">
          {saving ? "…" : "Criar e gerar QR"}
        </button>
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
  const m = useModelPick(models);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function create() {
    if (!pnid.trim() || !token.trim()) return setErr("Phone Number ID e Access Token são obrigatórios");
    if (!m.value) return setErr("Selecione o modelo que vai atender este número");
    setSaving(true);
    setErr(null);
    try {
      await api.post("/integrations/whatsapp", {
        label, provider: "official", model_config_id: m.modelConfigId, model: m.model,
        phone, phone_number_id: pnid, access_token: token, app_secret: secret,
      });
      onCreated();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Falha ao criar a conexão");
      setSaving(false);
    }
  }

  return (
    <ModalShell title="Conectar via API Oficial (Meta)" onClose={onClose}>
      <div className="grid grid-cols-2 gap-2">
        <label className="block text-sm">
          <span className="text-xs text-muted">Nome (opcional)</span>
          <input value={label} onChange={(e) => setLabel(e.target.value)} placeholder="Ex.: Atendimento" className={inputCls} />
        </label>
        <label className="block text-sm">
          <span className="text-xs text-muted">Número (opcional)</span>
          <input value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="5511999999999" className={inputCls} />
        </label>
      </div>
      <label className="block text-sm">
        <span className="text-xs text-muted">Phone Number ID</span>
        <input value={pnid} onChange={(e) => setPnid(e.target.value)} placeholder="do painel WhatsApp → API Setup" className={`${inputCls} font-mono text-xs`} />
      </label>
      <label className="block text-sm">
        <span className="text-xs text-muted">Access Token (permanente)</span>
        <input type="password" value={token} onChange={(e) => setToken(e.target.value)} placeholder="EAAG…" className={`${inputCls} font-mono text-xs`} />
      </label>
      <label className="block text-sm">
        <span className="text-xs text-muted">App Secret (opcional — valida a assinatura dos webhooks)</span>
        <input type="password" value={secret} onChange={(e) => setSecret(e.target.value)} className={`${inputCls} font-mono text-xs`} />
      </label>
      <div>
        <span className="text-xs text-muted">Modelo que atende este número</span>
        <div className="mt-1"><ModelField models={extModels} custom={models} includeCustom value={m.value} onChange={m.pick} /></div>
      </div>
      {err && <p className="text-xs text-red-400">{err}</p>}
      <div className="flex justify-end gap-2 pt-1">
        <button onClick={onClose} className="rounded-full border border-border px-4 py-1.5 text-xs text-muted hover:text-ink">Cancelar</button>
        <button onClick={create} disabled={saving} className="rounded-full bg-accent px-5 py-1.5 text-xs font-medium text-white hover:bg-accent-hover disabled:opacity-60">
          {saving ? "…" : "Conectar"}
        </button>
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
      setErr(e instanceof ApiError ? e.message : "Falha ao obter o QR");
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
    <ModalShell title={connected ? "Número conectado" : "Escaneie o QR Code"} onClose={onClose}>
      {connected ? (
        <div className="flex flex-col items-center gap-2 py-4 text-center">
          <span className="flex h-12 w-12 items-center justify-center rounded-full bg-green-500/15 text-green-500"><Check size={24} /></span>
          <p className="text-sm text-ink">{phone ? `+${phone}` : "Sessão aberta"}</p>
          <p className="text-xs text-muted">Este número agora é atendido pelo modelo configurado.</p>
        </div>
      ) : (
        <div className="flex flex-col items-center gap-3 py-2">
          {qr ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={qr} alt="QR Code do WhatsApp" className="h-56 w-56 rounded-xl border border-border bg-white p-2" />
          ) : (
            <div className="flex h-56 w-56 items-center justify-center rounded-xl border border-border bg-surface">
              {err ? <TriangleAlert size={20} className="text-red-400" /> : <Loader2 size={20} className="animate-spin text-muted" />}
            </div>
          )}
          {err && <p className="max-w-xs text-center text-xs text-red-400">{err}</p>}
          <p className="max-w-xs text-center text-xs text-muted">
            WhatsApp → Configurações → Aparelhos conectados → Conectar aparelho
          </p>
          <button onClick={fetchQr} className="flex items-center gap-1.5 rounded-full border border-border px-3 py-1 text-xs text-ink-soft transition-colors hover:bg-hover hover:text-ink">
            <RefreshCw size={12} /> Gerar novo QR
          </button>
        </div>
      )}
    </ModalShell>
  );
}
