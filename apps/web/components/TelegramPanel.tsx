"use client";

import { useCallback, useEffect, useState } from "react";
import { ChevronDown, ChevronLeft, ChevronRight, Loader2, Plus, Send, Trash2, TriangleAlert } from "lucide-react";
import { SiTelegram } from "react-icons/si";
import { api, ApiError } from "@/lib/api";
import type { Model, ModelConfig, TelegramConnection } from "@/lib/types";
import ModelField from "./ModelField";
import ContextWindowSelect from "./ContextWindowSelect";
import { useConfirm } from "@/components/ConfirmDialog";
import { Select } from "@/components/ui";
import { toast } from "@/components/Toaster";
import { tr } from "@/lib/i18n";

const inputCls = "mt-1 w-full rounded-lg border border-border bg-surface2 px-3 py-1.5 text-sm text-ink outline-none focus:border-accent placeholder:text-muted";

function Toggle({ on, onClick }: { on: boolean; onClick: () => void }) {
  return (
    <button type="button" onClick={onClick} className={`relative h-5 w-9 shrink-0 rounded-full transition-colors ${on ? "bg-accent" : "bg-surface2"}`}>
      <span className={`absolute top-0.5 h-4 w-4 rounded-full bg-white transition-all ${on ? "left-[18px]" : "left-0.5"}`} />
    </button>
  );
}

function joinModel(c: { model_config_id: string | null; model: string }): string {
  return c.model_config_id ? `custom:${c.model_config_id}` : c.model;
}
function splitModel(v: string): { model_config_id: string | null; model: string } {
  return v.startsWith("custom:") ? { model_config_id: v.slice(7), model: "" } : { model_config_id: null, model: v };
}

/** Tela "Telegram" (Integrações): conecta um bot (@BotFather), associa a um modelo
 *  e configura filtros/memória/humanizador. Long-polling roda no servidor. */
export default function TelegramPanel({ onBack }: { onBack: () => void }) {
  const confirm = useConfirm();
  const [conns, setConns] = useState<TelegramConnection[] | null>(null);
  const [models, setModels] = useState<ModelConfig[]>([]);
  const [extModels, setExtModels] = useState<Model[]>([]);
  const [adding, setAdding] = useState(false);
  const [newToken, setNewToken] = useState("");
  const [newLabel, setNewLabel] = useState("");
  const [newModel, setNewModel] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);

  const load = useCallback(async () => {
    try { setConns(await api.get<TelegramConnection[]>("/integrations/telegram/connections")); }
    catch { setConns([]); }
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

  async function connect() {
    setErr(null); setBusy(true);
    try {
      const m = splitModel(newModel);
      await api.post("/integrations/telegram/connections", {
        bot_token: newToken.trim(), label: newLabel.trim(),
        model: m.model, model_config_id: m.model_config_id,
      });
      setAdding(false); setNewToken(""); setNewLabel(""); setNewModel("");
      load();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : tr("Falha ao conectar o bot"));
    } finally { setBusy(false); }
  }

  async function patch(id: string, body: Partial<TelegramConnection> & { model_config_id?: string | null }) {
    await api.patch(`/integrations/telegram/connections/${id}`, body).catch(() => {});
    load();
  }
  async function toggle(c: TelegramConnection) { await api.post(`/integrations/telegram/connections/${c.id}/toggle`).catch(() => {}); load(); }
  async function remove(c: TelegramConnection) {
    if (!(await confirm({ title: tr("Remover bot?"), body: <>@{c.bot_username || c.label}  {tr("deixará de responder.")}</>, confirmLabel: tr("Remover"), danger: true }))) return;
    await api.del(`/integrations/telegram/connections/${c.id}`).catch(() => {});
    load();
  }
  async function test(c: TelegramConnection) {
    try {
      const r = await api.post<{ ok: boolean; chat_id?: string }>(`/integrations/telegram/connections/${c.id}/test`, {});
      setErr(r.ok ? null : tr("Falha no teste"));
      if (r.ok) toast(tr("Mensagem de teste enviada."), "success");
    } catch (e) { setErr(e instanceof ApiError ? e.message : tr("Falha ao testar")); }
  }

  return (
    <div className="pt-1">
      <button onClick={onBack} className="mb-3 flex items-center gap-1 text-sm text-muted transition-colors hover:text-ink">
        <ChevronLeft size={16} />  {tr("Voltar")}
      </button>

      <div className="flex items-center justify-between">
        <h3 className="flex items-center gap-2 text-base font-semibold text-ink"><SiTelegram size={18} className="text-accent-hover" />  {tr("Telegram")}</h3>
        {!adding && (
          <button onClick={() => setAdding(true)} className="flex items-center gap-1.5 rounded-lg bg-accent px-3 py-1.5 text-sm font-medium text-white transition-colors hover:bg-accent-hover">
            <Plus size={15} />  {tr("Conectar bot")}
          </button>
        )}
      </div>
      <p className="mt-1 text-xs text-muted">{tr("Crie um bot com o")} <span className="text-ink-soft">@BotFather</span>{tr(", cole o token aqui e escolha o modelo que vai atender.")}</p>

      {err && <p className="mt-2 flex items-center gap-1.5 text-xs text-red-400"><TriangleAlert size={13} /> {err}</p>}

      {adding && (
        <div className="mt-3 space-y-2 rounded-xl border border-border bg-surface p-3">
          <label className="block text-xs text-muted">{tr("Token do bot (@BotFather)")}
            <input value={newToken} onChange={(e) => setNewToken(e.target.value)} placeholder="123456:ABC-DEF..." className={inputCls} />
          </label>
          <label className="block text-xs text-muted">{tr("Nome (opcional)")}
            <input value={newLabel} onChange={(e) => setNewLabel(e.target.value)} placeholder={tr("Ex.: Assistente pessoal")} className={inputCls} />
          </label>
          <div className="text-xs text-muted">{tr("Modelo")}
            <ModelField models={extModels} custom={models} includeCustom value={newModel} onChange={setNewModel} className="mt-1" />
          </div>
          <div className="flex justify-end gap-2 pt-1">
            <button onClick={() => setAdding(false)} className="rounded-lg px-3 py-1.5 text-sm text-muted hover:text-ink">{tr("Cancelar")}</button>
            <button onClick={connect} disabled={busy || !newToken.trim()} className="flex items-center gap-1.5 rounded-lg bg-accent px-3 py-1.5 text-sm font-medium text-white hover:bg-accent-hover disabled:opacity-50">
              {busy && <Loader2 size={14} className="animate-spin" />}  {tr("Conectar")}
            </button>
          </div>
        </div>
      )}

      <div className="mt-4 space-y-2">
        {conns === null ? (
          <p className="py-6 text-center text-sm text-muted">{tr("Carregando…")}</p>
        ) : conns.length === 0 && !adding ? (
          <p className="py-8 text-center text-sm text-muted">{tr("Nenhum bot conectado ainda.")}</p>
        ) : (
          conns.map((c) => (
            <div key={c.id} className="rounded-xl border border-border bg-surface">
              <div className="flex items-center gap-3 p-3">
                <span className={`h-2 w-2 shrink-0 rounded-full ${c.enabled ? (c.state?.last_error ? "bg-red-400" : "bg-green-500") : "bg-surface2"}`} />
                <button onClick={() => setOpenId(openId === c.id ? null : c.id)} className="flex min-w-0 flex-1 items-center gap-2 text-left">
                  {openId === c.id ? <ChevronDown size={15} className="text-muted" /> : <ChevronRight size={15} className="text-muted" />}
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium text-ink">{c.label || `@${c.bot_username}`}</p>
                    <p className="truncate text-xs text-muted">@{c.bot_username} · {tr("{n} conversa(s)", { n: c.threads })}{c.state?.last_error ? ` · ${tr("erro:")} ${c.state.last_error}` : ""}</p>
                  </div>
                </button>
                <button onClick={() => test(c)} title={tr("Enviar teste")} className="rounded-lg p-1.5 text-muted hover:bg-hover hover:text-ink"><Send size={14} /></button>
                <Toggle on={c.enabled} onClick={() => toggle(c)} />
                <button onClick={() => remove(c)} title={tr("Remover")} className="rounded-lg p-1.5 text-muted hover:bg-hover hover:text-red-400"><Trash2 size={14} /></button>
              </div>

              {openId === c.id && (
                <div className="space-y-3 border-t border-border p-3">
                  <div className="text-xs text-muted">{tr("Modelo")}
                    <ModelField models={extModels} custom={models} includeCustom value={joinModel(c)} onChange={(v) => patch(c.id, splitModel(v))} className="mt-1" />
                  </div>
                  <div className="text-xs text-muted">{tr("Memória")}
                    <Select value={c.memory} onChange={(e) => patch(c.id, { memory: e.target.value as "local" | "global" })} className={inputCls}>
                      <option value="local">{tr("Local (isolada por conversa)")}</option>
                      <option value="global">{tr("Global (memória do modelo)")}</option>
                    </Select>
                  </div>
                  <div className="text-xs text-muted">{tr("Contexto (mensagens que a IA enxerga)")}
                    <ContextWindowSelect value={c.context_window} onChange={(v) => patch(c.id, { context_window: v })} className={inputCls} />
                  </div>
                  <label className="block text-xs text-muted">{tr("Prompt adicional deste bot")}
                    <textarea defaultValue={c.system_prompt} onBlur={(e) => e.target.value !== c.system_prompt && patch(c.id, { system_prompt: e.target.value })} rows={2} placeholder={tr("Ex.: responda curto e informal")} className={inputCls} />
                  </label>
                  <div className="flex items-center justify-between">
                    <span className="text-xs text-ink">{tr("Responder em grupos")}</span>
                    <Toggle on={!!c.filters?.groups} onClick={() => patch(c.id, { filters: { ...(c.filters || {}), groups: !c.filters?.groups } })} />
                  </div>
                  <label className="block text-xs text-muted">{tr("Prefixo-gatilho (opcional — só responde se a mensagem começar com ele)")}
                    <input defaultValue={c.filters?.trigger || ""} onBlur={(e) => (e.target.value !== (c.filters?.trigger || "")) && patch(c.id, { filters: { ...(c.filters || {}), trigger: e.target.value } })} placeholder={tr("Ex.: /ia")} className={inputCls} />
                  </label>
                  <label className="block text-xs text-muted">{tr("Agrupar mensagens seguidas")}
                    <Select value={c.debounce_seconds ?? 0} onChange={(e) => patch(c.id, { debounce_seconds: Number(e.target.value) })} className={inputCls}>
                      <option value={0}>{tr("Desligado (responde cada mensagem)")}</option>
                      <option value={3}>{tr("Esperar 3s de silêncio")}</option>
                      <option value={5}>{tr("Esperar 5s de silêncio")}</option>
                      <option value={8}>{tr("Esperar 8s de silêncio")}</option>
                      <option value={15}>{tr("Esperar 15s de silêncio")}</option>
                    </Select>
                  </label>
                  <div className="flex items-center justify-between">
                    <span className="text-xs text-ink">{tr("Modo humanizador (digitando… + atraso)")}</span>
                    <Toggle on={!!c.humanize?.enabled} onClick={() => patch(c.id, { humanize: { ...(c.humanize || {}), enabled: !c.humanize?.enabled } })} />
                  </div>
                  {c.humanize?.enabled && (
                    <div className="flex items-center justify-between pl-1">
                      <span className="text-xs text-muted">{tr("Quebrar em várias mensagens")}</span>
                      <Toggle on={!!c.humanize?.split} onClick={() => patch(c.id, { humanize: { ...(c.humanize || {}), split: !c.humanize?.split } })} />
                    </div>
                  )}
                </div>
              )}
            </div>
          ))
        )}
      </div>
    </div>
  );
}
