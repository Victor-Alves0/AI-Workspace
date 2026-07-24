"use client";

/**
 * Painel da API: chaves, limites, consumo e documentação.
 *
 * O segredo da chave só existe no instante da criação — o servidor guarda hash.
 * Por isso o fluxo tem um passo dedicado de "copie agora": depois dele não há
 * como recuperar, só regenerar.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Activity, AlertTriangle, BookOpen, Check, Copy, Eye, KeyRound, Pencil,
  Play, Plus, RefreshCw, ShieldOff, Trash2, X,
} from "lucide-react";
import { api, API_URL } from "@/lib/api";
import { copyText } from "@/lib/clipboard";
import { Toggle } from "@/components/ui";
import type { ApiKey, ApiKeyMeta, ApiKeyRequest } from "@/lib/types";

const SCOPE_LABELS: Record<string, string> = {
  chat: "Conversar (usar modelos)",
  "models:read": "Listar modelos",
  "memory:read": "Ler memória",
  "memory:write": "Gravar memória",
  "files:read": "Listar arquivos",
  "files:write": "Enviar e apagar arquivos",
  "usage:read": "Ver consumo",
};

const MEMORY_LABELS: Record<string, string> = {
  none: "Sem memória",
  request: "Temporária (só na requisição)",
  persistent: "Persistente (memória do app)",
  shared: "Compartilhada entre aplicações",
  key: "Isolada por chave",
  end_user: "Isolada por usuário final",
};

const STATE_STYLE: Record<string, string> = {
  active: "bg-emerald-500/15 text-emerald-500",
  disabled: "bg-amber-500/15 text-amber-500",
  revoked: "bg-red-500/15 text-red-500",
  expired: "bg-red-500/15 text-red-500",
};

const STATE_LABEL: Record<string, string> = {
  active: "Ativa", disabled: "Desativada", revoked: "Revogada", expired: "Expirada",
};

const LIMIT_FIELDS: { key: string; label: string; hint: string }[] = [
  { key: "rpm", label: "Requisições por minuto", hint: "0 = sem limite" },
  { key: "rpd", label: "Requisições por dia", hint: "0 = sem limite" },
  { key: "monthly_requests", label: "Requisições por mês", hint: "0 = sem limite" },
  { key: "tokens_in", label: "Tokens de entrada / mês", hint: "0 = sem limite" },
  { key: "tokens_out", label: "Tokens de saída / mês", hint: "0 = sem limite" },
  { key: "concurrency", label: "Requisições simultâneas", hint: "0 = sem limite" },
];

const BTN_PRIMARY =
  "flex items-center gap-1.5 whitespace-nowrap rounded-full bg-accent px-4 py-1.5 text-sm font-medium text-white transition-colors hover:bg-accent-hover disabled:opacity-50";
const BTN_GHOST =
  "whitespace-nowrap rounded-full border border-border bg-surface px-4 py-1.5 text-sm text-ink-soft transition-colors hover:bg-surface2";
const INPUT =
  "w-full rounded-xl border border-border bg-surface px-3 py-2 text-sm text-ink outline-none transition-colors focus:border-accent/60";

function fmtUsd(v: number): string {
  return v >= 0.01 || v === 0 ? `US$ ${v.toFixed(2)}` : `US$ ${v.toFixed(5)}`;
}

function fmtDate(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" });
}

function Field({ label, hint, children }: {
  label: string; hint?: string; children: React.ReactNode;
}) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-medium text-ink-soft">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-[11px] text-muted">{hint}</span>}
    </label>
  );
}

function CopyButton({ text, label = "Copiar" }: { text: string; label?: string }) {
  const [done, setDone] = useState(false);
  return (
    <button
      onClick={async () => {
        if (await copyText(text)) {
          setDone(true);
          setTimeout(() => setDone(false), 1600);
        }
      }}
      className="flex items-center gap-1.5 rounded-lg border border-border px-2.5 py-1 text-xs text-ink-soft transition-colors hover:bg-hover"
    >
      {done ? <Check size={13} className="text-emerald-500" /> : <Copy size={13} />}
      {done ? "Copiado" : label}
    </button>
  );
}

/* --------------------------------------------------------------------------- */
/* Formulário de chave (criar / editar)                                        */
/* --------------------------------------------------------------------------- */

type FormState = {
  name: string;
  scopes: string[];
  memoryMode: string;
  memoryMax: string;
  modelMode: "all" | "allow";
  modelIds: string[];
  defaultModel: string;
  limits: Record<string, string>;
  budget: string;
  ips: string;
  webhookUrl: string;
  webhookSecret: string;
  webhookEvents: string[];
  ttlDays: string;
};

function emptyForm(meta: ApiKeyMeta | null): FormState {
  return {
    name: "",
    scopes: meta?.default_scopes ?? ["chat", "models:read", "usage:read"],
    memoryMode: "none",
    memoryMax: "",
    modelMode: "all",
    modelIds: [],
    defaultModel: "",
    limits: {},
    budget: "",
    ips: "",
    webhookUrl: "",
    webhookSecret: "",
    webhookEvents: [],
    ttlDays: "",
  };
}

function formFromKey(k: ApiKey): FormState {
  const lim = k.limits ?? {};
  return {
    name: k.name,
    scopes: k.scopes ?? [],
    memoryMode: k.memory?.mode ?? "none",
    memoryMax: k.memory?.max_items ? String(k.memory.max_items) : "",
    modelMode: k.model_policy?.mode === "allow" ? "allow" : "all",
    modelIds: k.model_policy?.ids ?? [],
    defaultModel: k.model_policy?.default ?? "",
    limits: Object.fromEntries(
      LIMIT_FIELDS.map((f) => [f.key, lim[f.key] ? String(lim[f.key]) : ""]),
    ),
    budget: lim.budget_usd ? String(lim.budget_usd) : "",
    ips: (k.ip_allowlist ?? []).join(", "),
    webhookUrl: k.webhook?.url ?? "",
    webhookSecret: "",
    webhookEvents: k.webhook?.events ?? [],
    ttlDays: "",
  };
}

function toPayload(f: FormState) {
  const limits: Record<string, number> = {};
  for (const { key } of LIMIT_FIELDS) {
    const n = parseInt(f.limits[key] ?? "", 10);
    if (n > 0) limits[key] = n;
  }
  const budget = parseFloat(f.budget);
  if (budget > 0) limits.budget_usd = budget;

  const webhook = f.webhookUrl.trim()
    ? { url: f.webhookUrl.trim(), secret: f.webhookSecret, events: f.webhookEvents }
    : {};

  const memory: Record<string, unknown> = { mode: f.memoryMode };
  const maxItems = parseInt(f.memoryMax, 10);
  if (maxItems > 0) memory.max_items = maxItems;

  const ttl = parseInt(f.ttlDays, 10);

  return {
    name: f.name.trim(),
    scopes: f.scopes,
    memory,
    limits,
    webhook,
    model_policy: {
      mode: f.modelMode,
      ids: f.modelMode === "allow" ? f.modelIds : [],
      default: f.defaultModel || undefined,
    },
    ip_allowlist: f.ips.split(",").map((s) => s.trim()).filter(Boolean),
    ...(ttl > 0 ? { ttl_days: ttl } : {}),
  };
}

function KeyForm({ meta, initial, editing, onCancel, onSubmit }: {
  meta: ApiKeyMeta | null;
  initial: FormState;
  editing: boolean;
  onCancel: () => void;
  onSubmit: (payload: ReturnType<typeof toPayload>) => Promise<void>;
}) {
  const [f, setF] = useState<FormState>(initial);
  const [busy, setBusy] = useState(false);
  const set = <K extends keyof FormState>(k: K, v: FormState[K]) =>
    setF((p) => ({ ...p, [k]: v }));

  const toggleIn = (list: string[], v: string) =>
    list.includes(v) ? list.filter((x) => x !== v) : [...list, v];

  return (
    <div className="space-y-5">
      <Field label="Nome">
        <input
          value={f.name}
          onChange={(e) => set("name", e.target.value)}
          placeholder="Ex.: App do site, n8n, bot interno"
          className={INPUT}
          autoFocus
        />
      </Field>

      <div>
        <p className="mb-2 text-xs font-medium text-ink-soft">Permissões</p>
        <div className="grid gap-1.5 sm:grid-cols-2">
          {(meta?.scopes ?? []).map((s) => (
            <label key={s} className="flex items-center gap-2 rounded-lg px-2 py-1.5 text-sm text-ink transition-colors hover:bg-hover">
              <input
                type="checkbox"
                checked={f.scopes.includes(s)}
                onChange={() => set("scopes", toggleIn(f.scopes, s))}
                className="accent-[var(--accent)]"
              />
              {SCOPE_LABELS[s] ?? s}
            </label>
          ))}
        </div>
      </div>

      <div className="h-px bg-border" />

      <div>
        <p className="mb-2 text-xs font-medium text-ink-soft">Modelos</p>
        <div className="mb-2 flex gap-2">
          {(["all", "allow"] as const).map((m) => (
            <button
              key={m}
              onClick={() => set("modelMode", m)}
              className={`rounded-full px-3 py-1 text-xs transition-colors ${
                f.modelMode === m ? "bg-accent text-white" : "border border-border text-ink-soft hover:bg-hover"
              }`}
            >
              {m === "all" ? "Todos os modelos" : "Somente os escolhidos"}
            </button>
          ))}
        </div>
        {f.modelMode === "allow" && (
          <div className="max-h-52 space-y-1 overflow-y-auto rounded-xl border border-border p-2">
            {(meta?.models ?? []).map((m) => (
              <label key={m.id} className="flex items-center gap-2 rounded-lg px-2 py-1.5 text-sm text-ink transition-colors hover:bg-hover">
                <input
                  type="checkbox"
                  checked={f.modelIds.includes(m.id)}
                  onChange={() => set("modelIds", toggleIn(f.modelIds, m.id))}
                  className="accent-[var(--accent)]"
                />
                <span className="truncate">{m.name}</span>
                <span className="ml-auto shrink-0 text-[11px] text-muted">{m.base_model}</span>
              </label>
            ))}
          </div>
        )}
        <div className="mt-2">
          <Field label="Modelo padrão" hint="Usado quando a requisição não informa 'model'.">
            <select
              value={f.defaultModel}
              onChange={(e) => set("defaultModel", e.target.value)}
              className={INPUT}
            >
              <option value="">Nenhum</option>
              {(meta?.models ?? []).map((m) => (
                <option key={m.id} value={m.id}>{m.name}</option>
              ))}
            </select>
          </Field>
        </div>
      </div>

      <div className="h-px bg-border" />

      <div>
        <p className="mb-2 text-xs font-medium text-ink-soft">Limites de uso</p>
        <div className="grid gap-3 sm:grid-cols-2">
          {LIMIT_FIELDS.map((lf) => (
            <Field key={lf.key} label={lf.label} hint={lf.hint}>
              <input
                type="number"
                min={0}
                value={f.limits[lf.key] ?? ""}
                onChange={(e) => set("limits", { ...f.limits, [lf.key]: e.target.value })}
                placeholder="0"
                className={INPUT}
              />
            </Field>
          ))}
          <Field label="Orçamento mensal (US$)" hint="Ao atingir, a chave é bloqueada até o mês virar.">
            <input
              type="number"
              min={0}
              step="0.01"
              value={f.budget}
              onChange={(e) => set("budget", e.target.value)}
              placeholder="0"
              className={INPUT}
            />
          </Field>
        </div>
      </div>

      <div className="h-px bg-border" />

      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Memória">
          <select
            value={f.memoryMode}
            onChange={(e) => set("memoryMode", e.target.value)}
            className={INPUT}
          >
            {(meta?.memory_modes ?? []).map((m) => (
              <option key={m} value={m}>{MEMORY_LABELS[m] ?? m}</option>
            ))}
          </select>
        </Field>
        <Field label="Máximo de memórias" hint="0 = sem teto.">
          <input
            type="number"
            min={0}
            value={f.memoryMax}
            onChange={(e) => set("memoryMax", e.target.value)}
            placeholder="0"
            className={INPUT}
          />
        </Field>
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="IPs autorizados" hint="Separados por vírgula. Aceita CIDR (10.0.0.0/8). Vazio = qualquer origem.">
          <input
            value={f.ips}
            onChange={(e) => set("ips", e.target.value)}
            placeholder="203.0.113.7, 10.0.0.0/8"
            className={INPUT}
          />
        </Field>
        {!editing && (
          <Field label="Expira em (dias)" hint="Vazio = não expira.">
            <input
              type="number"
              min={0}
              value={f.ttlDays}
              onChange={(e) => set("ttlDays", e.target.value)}
              placeholder="90"
              className={INPUT}
            />
          </Field>
        )}
      </div>

      <div className="h-px bg-border" />

      <div className="space-y-3">
        <Field label="Webhook" hint="Recebe eventos assinados com HMAC-SHA256.">
          <input
            value={f.webhookUrl}
            onChange={(e) => set("webhookUrl", e.target.value)}
            placeholder="https://seuapp.com/hooks/aiworkspace"
            className={INPUT}
          />
        </Field>
        {f.webhookUrl.trim() && (
          <>
            <Field
              label="Segredo do webhook"
              hint={editing ? "Vazio mantém o segredo atual." : "Usado para assinar o corpo."}
            >
              <input
                value={f.webhookSecret}
                onChange={(e) => set("webhookSecret", e.target.value)}
                placeholder="••••••"
                className={INPUT}
              />
            </Field>
            <div className="flex flex-wrap gap-1.5">
              {(meta?.webhook_events ?? []).map((ev) => (
                <button
                  key={ev}
                  onClick={() => set("webhookEvents", toggleIn(f.webhookEvents, ev))}
                  className={`rounded-full px-2.5 py-1 text-[11px] transition-colors ${
                    f.webhookEvents.includes(ev)
                      ? "bg-accent text-white"
                      : "border border-border text-ink-soft hover:bg-hover"
                  }`}
                >
                  {ev}
                </button>
              ))}
            </div>
          </>
        )}
      </div>

      <div className="flex justify-end gap-2 pt-1">
        <button onClick={onCancel} className={BTN_GHOST}>Cancelar</button>
        <button
          disabled={busy || !f.name.trim()}
          onClick={async () => {
            setBusy(true);
            try {
              await onSubmit(toPayload(f));
            } finally {
              setBusy(false);
            }
          }}
          className={BTN_PRIMARY}
        >
          {editing ? "Salvar" : "Criar chave"}
        </button>
      </div>
    </div>
  );
}

/* --------------------------------------------------------------------------- */
/* Modal genérico                                                              */
/* --------------------------------------------------------------------------- */

function Modal({ title, wide, onClose, children }: {
  title: string; wide?: boolean; onClose: () => void; children: React.ReactNode;
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" onClick={onClose}>
      <div
        onClick={(e) => e.stopPropagation()}
        className={`flex max-h-[88vh] w-full flex-col overflow-hidden rounded-2xl border border-border bg-bg shadow-xl ${wide ? "max-w-3xl" : "max-w-xl"}`}
      >
        <div className="flex items-center justify-between border-b border-border px-5 py-3.5">
          <h2 className="text-base font-semibold text-ink">{title}</h2>
          <button onClick={onClose} className="rounded-lg p-1 text-muted transition-colors hover:bg-hover hover:text-ink">
            <X size={18} />
          </button>
        </div>
        <div className="flex-1 overflow-y-auto px-5 py-5">{children}</div>
      </div>
    </div>
  );
}

/* --------------------------------------------------------------------------- */
/* Documentação                                                                */
/* --------------------------------------------------------------------------- */

function Docs() {
  const base = `${API_URL}/v1`;
  const snippets: { lang: string; code: string }[] = [
    {
      lang: "cURL",
      code: `curl ${base}/chat/completions \\
  -H "Authorization: Bearer $AIW_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{
    "model": "seu-modelo",
    "messages": [{"role": "user", "content": "Olá!"}]
  }'`,
    },
    {
      lang: "Python",
      code: `from openai import OpenAI

client = OpenAI(api_key="$AIW_KEY", base_url="${base}")

resp = client.chat.completions.create(
    model="seu-modelo",
    messages=[{"role": "user", "content": "Olá!"}],
)
print(resp.choices[0].message.content)`,
    },
    {
      lang: "Node",
      code: `import OpenAI from "openai";

const client = new OpenAI({
  apiKey: process.env.AIW_KEY,
  baseURL: "${base}",
});

const resp = await client.chat.completions.create({
  model: "seu-modelo",
  messages: [{ role: "user", content: "Olá!" }],
});
console.log(resp.choices[0].message.content);`,
    },
  ];

  const endpoints = [
    ["POST", "/v1/chat/completions", "Conversa. Aceita stream:true e background:true."],
    ["GET", "/v1/chat/completions/{id}", "Resultado de uma chamada assíncrona."],
    ["GET", "/v1/models", "Modelos disponíveis para a chave."],
    ["GET", "/v1/models/{id}", "Detalhes e capacidades de um modelo."],
    ["GET", "/v1/memories", "Memórias no escopo da chave."],
    ["POST", "/v1/memories", "Grava uma memória."],
    ["DELETE", "/v1/memories/{id}", "Apaga uma memória."],
    ["POST", "/v1/memories/clear", "Limpa o escopo inteiro."],
    ["GET", "/v1/memories/export", "Exporta a memória."],
    ["POST", "/v1/memories/import", "Importa memórias."],
    ["GET", "/v1/files", "Documentos indexados."],
    ["POST", "/v1/files", "Envia um documento (multipart)."],
    ["DELETE", "/v1/files/{id}", "Remove um documento."],
    ["GET", "/v1/usage", "Consumo da chave."],
    ["GET", "/v1/account", "Limites, consumo e saldo restante."],
  ];

  const [tab, setTab] = useState(0);

  return (
    <div className="space-y-6">
      <div className="rounded-xl border border-border bg-surface p-4">
        <p className="text-xs font-medium text-ink-soft">URL base</p>
        <div className="mt-1.5 flex items-center gap-2">
          <code className="flex-1 truncate rounded-lg bg-surface2 px-3 py-2 text-sm text-ink">{base}</code>
          <CopyButton text={base} />
        </div>
        <p className="mt-2 text-xs text-muted">
          Compatível com a API da OpenAI: qualquer SDK oficial funciona trocando só a
          URL base e a chave.
        </p>
      </div>

      <div>
        <div className="mb-2 flex gap-1.5">
          {snippets.map((s, i) => (
            <button
              key={s.lang}
              onClick={() => setTab(i)}
              className={`rounded-full px-3 py-1 text-xs transition-colors ${
                tab === i ? "bg-accent text-white" : "border border-border text-ink-soft hover:bg-hover"
              }`}
            >
              {s.lang}
            </button>
          ))}
          <span className="ml-auto"><CopyButton text={snippets[tab].code} /></span>
        </div>
        <pre className="overflow-x-auto rounded-xl border border-border bg-surface p-4 text-xs leading-relaxed text-ink-soft">
          {snippets[tab].code}
        </pre>
      </div>

      <div>
        <p className="mb-2 text-xs font-medium text-ink-soft">Endpoints</p>
        <div className="overflow-x-auto rounded-xl border border-border">
          <table className="w-full text-left text-sm">
            <tbody>
              {endpoints.map(([m, p, d]) => (
                <tr key={p + m} className="border-b border-border last:border-0">
                  <td className="whitespace-nowrap px-3 py-2 font-mono text-[11px] text-accent">{m}</td>
                  <td className="whitespace-nowrap px-3 py-2 font-mono text-xs text-ink">{p}</td>
                  <td className="px-3 py-2 text-xs text-muted">{d}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

/* --------------------------------------------------------------------------- */
/* Playground                                                                  */
/* --------------------------------------------------------------------------- */

function Playground({ meta }: { meta: ApiKeyMeta | null }) {
  const [key, setKey] = useState("");
  const [model, setModel] = useState(meta?.models?.[0]?.id ?? "");
  const [prompt, setPrompt] = useState("Olá! Quem é você?");
  const [out, setOut] = useState("");
  const [busy, setBusy] = useState(false);

  const run = async () => {
    setBusy(true);
    setOut("");
    const started = performance.now();
    try {
      const res = await fetch(`${API_URL}/v1/chat/completions`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${key.trim()}` },
        body: JSON.stringify({ model, messages: [{ role: "user", content: prompt }] }),
      });
      const json = await res.json();
      const ms = Math.round(performance.now() - started);
      setOut(`HTTP ${res.status} — ${ms} ms\n\n${JSON.stringify(json, null, 2)}`);
    } catch (e) {
      setOut(String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-4">
      <Field label="Chave" hint="Cole a chave completa. Ela não é salva.">
        <input
          value={key}
          onChange={(e) => setKey(e.target.value)}
          placeholder="aw-..."
          className={`${INPUT} font-mono`}
        />
      </Field>
      <Field label="Modelo">
        <select value={model} onChange={(e) => setModel(e.target.value)} className={INPUT}>
          {(meta?.models ?? []).map((m) => (
            <option key={m.id} value={m.id}>{m.name}</option>
          ))}
        </select>
      </Field>
      <Field label="Mensagem">
        <textarea
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          rows={3}
          className={`${INPUT} resize-y`}
        />
      </Field>
      <button disabled={busy || !key.trim() || !model} onClick={run} className={BTN_PRIMARY}>
        <Play size={15} /> {busy ? "Enviando…" : "Enviar"}
      </button>
      {out && (
        <pre className="max-h-80 overflow-auto rounded-xl border border-border bg-surface p-4 text-xs leading-relaxed text-ink-soft">
          {out}
        </pre>
      )}
    </div>
  );
}

/* --------------------------------------------------------------------------- */
/* Detalhe da chave                                                            */
/* --------------------------------------------------------------------------- */

function KeyDetail({ apiKey }: { apiKey: ApiKey }) {
  const [reqs, setReqs] = useState<ApiKeyRequest[]>([]);
  const [onlyErrors, setOnlyErrors] = useState(false);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    api
      .get<ApiKeyRequest[]>(`/api-keys/${apiKey.id}/requests?limit=50&only_errors=${onlyErrors}`)
      .then((r) => alive && setReqs(r))
      .catch(() => alive && setReqs([]))
      .finally(() => alive && setLoading(false));
    return () => { alive = false; };
  }, [apiKey.id, onlyErrors]);

  const stats = useMemo(() => {
    const total = reqs.length;
    const errors = reqs.filter((r) => r.status >= 400).length;
    const tokens = reqs.reduce((a, r) => a + r.total_tokens, 0);
    const latency = total ? Math.round(reqs.reduce((a, r) => a + r.latency_ms, 0) / total) : 0;
    return { total, errors, tokens, latency };
  }, [reqs]);

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {[
          ["Requisições (mês)", String(apiKey.requests_month)],
          ["Custo (mês)", fmtUsd(apiKey.cost_month)],
          ["Latência média", `${stats.latency} ms`],
          ["Erros recentes", String(stats.errors)],
        ].map(([label, value]) => (
          <div key={label} className="rounded-xl border border-border bg-surface p-3">
            <p className="text-[11px] text-muted">{label}</p>
            <p className="mt-0.5 text-lg font-semibold text-ink">{value}</p>
          </div>
        ))}
      </div>

      <div className="flex items-center justify-between">
        <p className="text-xs font-medium text-ink-soft">Requisições recentes</p>
        <label className="flex items-center gap-2 text-xs text-ink-soft">
          Só erros <Toggle on={onlyErrors} onChange={setOnlyErrors} />
        </label>
      </div>

      {loading ? (
        <p className="py-6 text-center text-sm text-muted">Carregando…</p>
      ) : reqs.length === 0 ? (
        <p className="py-6 text-center text-sm text-muted">Nenhuma requisição registrada.</p>
      ) : (
        <div className="overflow-x-auto rounded-xl border border-border">
          <table className="w-full text-left text-xs">
            <thead className="bg-surface2 text-muted">
              <tr>
                <th className="px-3 py-2 font-medium">Quando</th>
                <th className="px-3 py-2 font-medium">Endpoint</th>
                <th className="px-3 py-2 font-medium">Status</th>
                <th className="px-3 py-2 font-medium">Tokens</th>
                <th className="px-3 py-2 font-medium">Custo</th>
                <th className="px-3 py-2 font-medium">Latência</th>
              </tr>
            </thead>
            <tbody>
              {reqs.map((r) => (
                <tr key={r.id} className="border-t border-border">
                  <td className="whitespace-nowrap px-3 py-2 text-muted">{fmtDate(r.created_at)}</td>
                  <td className="whitespace-nowrap px-3 py-2 text-ink-soft">{r.endpoint}</td>
                  <td className="px-3 py-2">
                    <span className={r.status >= 400 ? "text-red-500" : "text-emerald-500"}>
                      {r.status}
                    </span>
                    {r.error && <span className="ml-1.5 text-muted" title={r.error}>{r.error.slice(0, 40)}</span>}
                  </td>
                  <td className="whitespace-nowrap px-3 py-2 text-ink-soft">{r.total_tokens}</td>
                  <td className="whitespace-nowrap px-3 py-2 text-ink-soft">{fmtUsd(r.cost)}</td>
                  <td className="whitespace-nowrap px-3 py-2 text-muted">{r.latency_ms} ms</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

/* --------------------------------------------------------------------------- */
/* Painel                                                                      */
/* --------------------------------------------------------------------------- */

type Tab = "keys" | "docs" | "playground";

export default function ApiView() {
  const [keys, setKeys] = useState<ApiKey[]>([]);
  const [meta, setMeta] = useState<ApiKeyMeta | null>(null);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState<Tab>("keys");
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<ApiKey | null>(null);
  const [detail, setDetail] = useState<ApiKey | null>(null);
  const [freshKey, setFreshKey] = useState<{ name: string; key: string } | null>(null);
  const [confirmRevoke, setConfirmRevoke] = useState<ApiKey | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [k, m] = await Promise.all([
        api.get<ApiKey[]>("/api-keys"),
        api.get<ApiKeyMeta>("/api-keys/meta"),
      ]);
      setKeys(k);
      setMeta(m);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const create = async (payload: ReturnType<typeof toPayload>) => {
    const created = await api.post<ApiKey & { key: string }>("/api-keys", payload);
    setCreating(false);
    setFreshKey({ name: created.name, key: created.key });
    await load();
  };

  const update = async (payload: ReturnType<typeof toPayload>) => {
    if (!editing) return;
    await api.patch<ApiKey>(`/api-keys/${editing.id}`, payload);
    setEditing(null);
    await load();
  };

  const regenerate = async (k: ApiKey) => {
    const res = await api.post<ApiKey & { key: string }>(`/api-keys/${k.id}/regenerate`, {});
    setFreshKey({ name: res.name, key: res.key });
    await load();
  };

  const revoke = async (k: ApiKey) => {
    await api.post(`/api-keys/${k.id}/revoke`, {});
    setConfirmRevoke(null);
    await load();
  };

  const remove = async (k: ApiKey) => {
    await api.del(`/api-keys/${k.id}`);
    await load();
  };

  const totals = useMemo(() => ({
    active: keys.filter((k) => k.state === "active").length,
    requests: keys.reduce((a, k) => a + k.requests_month, 0),
    cost: keys.reduce((a, k) => a + k.cost_month, 0),
  }), [keys]);

  return (
    <div className="space-y-5">
      <div className="flex gap-1.5">
        {([
          ["keys", "Chaves", <KeyRound key="i" size={14} />],
          ["docs", "Documentação", <BookOpen key="i" size={14} />],
          ["playground", "Playground", <Play key="i" size={14} />],
        ] as const).map(([id, label, icon]) => (
          <button
            key={id}
            onClick={() => setTab(id)}
            className={`flex items-center gap-1.5 rounded-full px-3.5 py-1.5 text-sm transition-colors ${
              tab === id ? "bg-accent text-white" : "border border-border text-ink-soft hover:bg-hover"
            }`}
          >
            {icon} {label}
          </button>
        ))}
        {tab === "keys" && (
          <button onClick={() => setCreating(true)} className={`${BTN_PRIMARY} ml-auto`}>
            <Plus size={15} /> Nova chave
          </button>
        )}
      </div>

      {tab === "keys" && (
        <>
          <div className="grid grid-cols-3 gap-3">
            {[
              ["Chaves ativas", String(totals.active)],
              ["Requisições no mês", String(totals.requests)],
              ["Custo no mês", fmtUsd(totals.cost)],
            ].map(([label, value]) => (
              <div key={label} className="rounded-xl border border-border bg-surface p-3.5">
                <p className="text-[11px] text-muted">{label}</p>
                <p className="mt-0.5 text-xl font-semibold text-ink">{value}</p>
              </div>
            ))}
          </div>

          {loading ? (
            <p className="py-10 text-center text-sm text-muted">Carregando…</p>
          ) : keys.length === 0 ? (
            <div className="rounded-2xl border border-dashed border-border py-14 text-center">
              <KeyRound size={26} className="mx-auto text-muted" />
              <p className="mt-3 text-sm font-medium text-ink">Nenhuma chave ainda</p>
              <p className="mt-1 text-xs text-muted">
                Crie uma chave para usar seus modelos a partir de qualquer aplicação.
              </p>
              <button onClick={() => setCreating(true)} className={`${BTN_PRIMARY} mx-auto mt-4`}>
                <Plus size={15} /> Nova chave
              </button>
            </div>
          ) : (
            <div className="space-y-2">
              {keys.map((k) => (
                <div key={k.id} className="rounded-xl border border-border bg-surface p-4">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <p className="truncate text-sm font-semibold text-ink">{k.name}</p>
                        <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${STATE_STYLE[k.state] ?? ""}`}>
                          {STATE_LABEL[k.state] ?? k.state}
                        </span>
                        {k.memory?.mode && k.memory.mode !== "none" && (
                          <span className="rounded-full bg-surface2 px-2 py-0.5 text-[10px] text-muted">
                            {MEMORY_LABELS[k.memory.mode]}
                          </span>
                        )}
                      </div>
                      <code className="mt-1 block font-mono text-xs text-muted">{k.masked}</code>
                      <p className="mt-1.5 text-[11px] text-muted">
                        {k.requests_month} req · {fmtUsd(k.cost_month)} no mês · último uso {fmtDate(k.last_used_at)}
                        {k.expires_at && ` · expira ${fmtDate(k.expires_at)}`}
                      </p>
                    </div>
                    <div className="flex shrink-0 items-center gap-1">
                      <button onClick={() => setDetail(k)} title="Detalhes e histórico"
                        className="rounded-lg p-1.5 text-muted transition-colors hover:bg-hover hover:text-ink">
                        <Activity size={15} />
                      </button>
                      <button onClick={() => setEditing(k)} title="Editar"
                        className="rounded-lg p-1.5 text-muted transition-colors hover:bg-hover hover:text-ink">
                        <Pencil size={15} />
                      </button>
                      <button onClick={() => void regenerate(k)} title="Regenerar segredo"
                        className="rounded-lg p-1.5 text-muted transition-colors hover:bg-hover hover:text-ink">
                        <RefreshCw size={15} />
                      </button>
                      {k.state !== "revoked" && (
                        <button onClick={() => setConfirmRevoke(k)} title="Revogar"
                          className="rounded-lg p-1.5 text-muted transition-colors hover:bg-hover hover:text-red-500">
                          <ShieldOff size={15} />
                        </button>
                      )}
                      <button onClick={() => void remove(k)} title="Excluir"
                        className="rounded-lg p-1.5 text-muted transition-colors hover:bg-hover hover:text-red-500">
                        <Trash2 size={15} />
                      </button>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </>
      )}

      {tab === "docs" && <Docs />}
      {tab === "playground" && <Playground meta={meta} />}

      {creating && (
        <Modal title="Nova chave de API" wide onClose={() => setCreating(false)}>
          <KeyForm meta={meta} initial={emptyForm(meta)} editing={false}
            onCancel={() => setCreating(false)} onSubmit={create} />
        </Modal>
      )}

      {editing && (
        <Modal title={`Editar “${editing.name}”`} wide onClose={() => setEditing(null)}>
          <KeyForm meta={meta} initial={formFromKey(editing)} editing
            onCancel={() => setEditing(null)} onSubmit={update} />
        </Modal>
      )}

      {detail && (
        <Modal title={detail.name} wide onClose={() => setDetail(null)}>
          <KeyDetail apiKey={detail} />
        </Modal>
      )}

      {freshKey && (
        <Modal title="Chave criada" onClose={() => setFreshKey(null)}>
          <div className="space-y-4">
            <div className="flex gap-2.5 rounded-xl border border-amber-500/30 bg-amber-500/10 p-3">
              <AlertTriangle size={16} className="mt-0.5 shrink-0 text-amber-500" />
              <p className="text-xs leading-relaxed text-ink-soft">
                Copie agora: esta é a única vez que a chave aparece. O servidor guarda
                apenas um hash — se você perdê-la, será preciso regenerar.
              </p>
            </div>
            <div className="flex items-center gap-2">
              <code className="flex-1 break-all rounded-lg bg-surface2 px-3 py-2.5 font-mono text-xs text-ink">
                {freshKey.key}
              </code>
              <CopyButton text={freshKey.key} />
            </div>
            <div className="flex justify-end">
              <button onClick={() => setFreshKey(null)} className={BTN_PRIMARY}>
                <Eye size={15} /> Já copiei
              </button>
            </div>
          </div>
        </Modal>
      )}

      {confirmRevoke && (
        <Modal title="Revogar chave" onClose={() => setConfirmRevoke(null)}>
          <p className="text-sm leading-relaxed text-ink-soft">
            A chave <strong className="text-ink">{confirmRevoke.name}</strong> para de
            funcionar imediatamente e não pode ser reativada. As aplicações que a usam
            passarão a receber erro 401.
          </p>
          <div className="mt-5 flex justify-end gap-2">
            <button onClick={() => setConfirmRevoke(null)} className={BTN_GHOST}>Cancelar</button>
            <button
              onClick={() => void revoke(confirmRevoke)}
              className="rounded-full bg-red-500 px-4 py-1.5 text-sm font-medium text-white transition-colors hover:bg-red-600"
            >
              Revogar
            </button>
          </div>
        </Modal>
      )}
    </div>
  );
}
