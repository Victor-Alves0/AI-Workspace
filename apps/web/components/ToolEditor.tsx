"use client";

import { useState } from "react";
import {
  CheckCircle2,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Code2,
  Info,
  Loader2,
  Plug,
  Tag,
  X,
  XCircle,
} from "lucide-react";
import { api, ApiError } from "@/lib/api";
import type { Tool } from "@/lib/types";
import CodeEditor from "./CodeEditor";

const NEW_TEMPLATE = `VALVES = {
    # Configurações ajustáveis pela engrenagem (opcional). Ex.:
    # "api_key": "",
}


def run(**params):
    """Descreva o que a ferramenta faz (o modelo usa isto para decidir)."""
    # 'valves' está disponível com os valores definidos na engrenagem.
    return {"echo": params}
`;

function slugify(s: string) {
  return s.toLowerCase().trim().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
}

const inputCls =
  "w-full rounded-xl border border-border bg-surface px-3 py-2 text-sm text-ink outline-none transition-colors placeholder:text-muted focus:border-accent/60";

function FieldLabel({ children }: { children: React.ReactNode }) {
  return (
    <span className="mb-1.5 block text-[11px] font-medium uppercase tracking-wider text-muted">
      {children}
    </span>
  );
}

/* seletor Código / Integração */
function TypeCard({
  active,
  icon,
  title,
  desc,
  onClick,
}: {
  active: boolean;
  icon: React.ReactNode;
  title: string;
  desc: string;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={`rounded-xl border p-3 text-left transition-all duration-150 ${
        active
          ? "border-accent/60 bg-accent/10"
          : "border-border bg-surface hover:border-accent/30"
      }`}
    >
      <span className={`flex items-center gap-2 text-sm font-medium ${active ? "text-accent-hover" : "text-ink"}`}>
        {icon} {title}
      </span>
      <span className="mt-1 block text-xs leading-5 text-muted">{desc}</span>
    </button>
  );
}

/* input de tags em chips: Enter/vírgula adiciona, Backspace remove a última */
function TagsInput({ tags, onChange }: { tags: string[]; onChange: (t: string[]) => void }) {
  const [draft, setDraft] = useState("");

  function add(raw: string) {
    const t = raw.trim().toLowerCase().replace(/,+$/, "");
    if (t && !tags.includes(t)) onChange([...tags, t]);
    setDraft("");
  }

  return (
    <div className="flex flex-wrap items-center gap-1.5 rounded-xl border border-border bg-surface px-2.5 py-2 transition-colors focus-within:border-accent/60">
      {tags.map((t) => (
        <span key={t} className="flex items-center gap-1 rounded-full bg-accent/15 px-2.5 py-0.5 text-xs text-accent-hover">
          {t}
          <button onClick={() => onChange(tags.filter((x) => x !== t))} className="text-accent-hover/70 transition-colors hover:text-accent-hover">
            <X size={11} />
          </button>
        </span>
      ))}
      <input
        value={draft}
        onChange={(e) => {
          if (e.target.value.endsWith(",")) add(e.target.value);
          else setDraft(e.target.value);
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            add(draft);
          } else if (e.key === "Backspace" && !draft && tags.length) {
            onChange(tags.slice(0, -1));
          }
        }}
        placeholder={tags.length ? "" : "adicionar tag…"}
        className="min-w-[90px] flex-1 bg-transparent text-sm text-ink outline-none placeholder:text-muted"
      />
    </div>
  );
}

export default function ToolEditor({
  tool,
  onClose,
  onSaved,
}: {
  tool: Tool | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const isNew = tool === null;
  const [toolType, setToolType] = useState<"code" | "mcp">(tool?.tool_type ?? "code");
  const [name, setName] = useState(tool?.name ?? "");
  const [path, setPath] = useState(tool?.path ?? "");
  const [description, setDescription] = useState(tool?.description ?? "");
  const [tags, setTags] = useState<string[]>(tool?.tags ?? []);
  const [code, setCode] = useState(tool?.code ?? (isNew ? NEW_TEMPLATE : ""));
  const [paramsStr, setParamsStr] = useState(JSON.stringify(tool?.params ?? {}, null, 2));
  const [returnsStr, setReturnsStr] = useState(JSON.stringify(tool?.returns ?? []));
  const [mcpUrl, setMcpUrl] = useState(tool?.mcp_config?.url ?? "");
  const [mcpTransport, setMcpTransport] = useState(tool?.mcp_config?.transport ?? "http");
  const [mcpHeadersStr, setMcpHeadersStr] = useState(
    JSON.stringify(tool?.mcp_config?.headers ?? {}, null, 2),
  );
  const [advanced, setAdvanced] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  // teste de conexão MCP
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<
    | { ok: true; server?: { name?: string; version?: string }; tools?: string[]; tools_count?: number; detail?: string }
    | { ok: false; error: string }
    | null
  >(null);

  async function testMcp() {
    setTestResult(null);
    let headers: unknown;
    try {
      headers = JSON.parse(mcpHeadersStr || "{}");
    } catch {
      setTestResult({ ok: false, error: "Headers devem ser JSON válido" });
      return;
    }
    if (!mcpUrl.trim()) {
      setTestResult({ ok: false, error: "Informe a URL do servidor MCP" });
      return;
    }
    setTesting(true);
    try {
      const res = await api.post<
        | { ok: true; server?: { name?: string; version?: string }; tools?: string[]; tools_count?: number; detail?: string }
        | { ok: false; error: string }
      >("/tools/mcp/test", { url: mcpUrl.trim(), transport: mcpTransport, headers });
      setTestResult(res);
    } catch (e) {
      setTestResult({ ok: false, error: e instanceof ApiError ? e.message : "Falha ao testar" });
    } finally {
      setTesting(false);
    }
  }

  async function save() {
    setErr(null);
    let params: unknown, returns: unknown, mcpHeaders: unknown;
    try {
      params = JSON.parse(paramsStr || "{}");
      returns = JSON.parse(returnsStr || "[]");
    } catch {
      setErr("params/returns devem ser JSON válido (seção Avançado)");
      return;
    }
    try {
      mcpHeaders = JSON.parse(mcpHeadersStr || "{}");
    } catch {
      setErr("Headers da integração devem ser JSON válido");
      return;
    }
    const id = (path || slugify(name)).trim();
    if (!id) {
      setErr("Defina um ID para a ferramenta");
      return;
    }
    if (toolType === "mcp" && !mcpUrl.trim()) {
      setErr("Informe a URL do servidor MCP");
      return;
    }
    const body = {
      path: id,
      name: name || id,
      description,
      code,
      params,
      returns,
      enabled: tool?.enabled ?? true,
      tags,
      tool_type: toolType,
      mcp_config:
        toolType === "mcp"
          ? { url: mcpUrl.trim(), transport: mcpTransport, headers: mcpHeaders }
          : {},
    };
    setSaving(true);
    try {
      if (isNew) await api.post("/tools", body);
      else await api.patch(`/tools/${tool!.id}`, body);
      onSaved();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Falha ao salvar");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="flex h-full flex-1 flex-col bg-bg">
      {/* header */}
      <div className="flex items-center gap-3 border-b border-border px-5 py-3">
        <button onClick={onClose} className="rounded-lg p-1.5 text-muted transition-colors hover:bg-hover hover:text-ink">
          <ChevronLeft size={20} />
        </button>
        <input
          value={name}
          onChange={(e) => {
            setName(e.target.value);
            if (isNew && !path) setPath(slugify(e.target.value));
          }}
          placeholder={isNew ? "Nova ferramenta" : "Nome da ferramenta"}
          className="min-w-0 flex-1 bg-transparent text-xl font-semibold tracking-tight text-ink outline-none placeholder:text-muted"
        />
        {err && <span className="max-w-[320px] truncate text-xs text-red-400">{err}</span>}
        <button
          onClick={save}
          disabled={saving}
          className="rounded-full bg-accent px-6 py-2 text-sm font-medium text-white transition-colors hover:bg-accent-hover disabled:opacity-60"
        >
          {saving ? "…" : "Salvar"}
        </button>
      </div>

      <div className="flex min-h-0 flex-1">
        {/* coluna do formulário */}
        <aside className="w-[340px] shrink-0 space-y-5 overflow-y-auto border-r border-border p-5">
          <div>
            <FieldLabel>Tipo</FieldLabel>
            <div className="grid grid-cols-2 gap-2">
              <TypeCard
                active={toolType === "code"}
                icon={<Code2 size={16} />}
                title="Código"
                desc="Python executado em sandbox local"
                onClick={() => setToolType("code")}
              />
              <TypeCard
                active={toolType === "mcp"}
                icon={<Plug size={16} />}
                title="Integração"
                desc="Conecta a um servidor MCP externo"
                onClick={() => setToolType("mcp")}
              />
            </div>
          </div>

          <div>
            <FieldLabel>ID</FieldLabel>
            <input
              value={path}
              onChange={(e) => setPath(e.target.value.toLowerCase())}
              placeholder="id_da_ferramenta"
              className={`${inputCls} font-mono`}
            />
          </div>

          <div>
            <FieldLabel>Descrição</FieldLabel>
            <textarea
              rows={3}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="O que a ferramenta faz — o modelo usa isto para decidir quando chamá-la"
              className={`${inputCls} resize-y leading-5`}
            />
          </div>

          <div>
            <FieldLabel>
              <span className="flex items-center gap-1"><Tag size={11} /> Tags</span>
            </FieldLabel>
            <TagsInput tags={tags} onChange={setTags} />
            <p className="mt-1.5 text-xs leading-5 text-muted">
              Organize suas ferramentas — as tags viram filtros na aba Ferramentas.
            </p>
          </div>

          {toolType === "code" && (
            <div>
              <button onClick={() => setAdvanced((v) => !v)} className="flex items-center gap-1 text-xs font-medium text-muted transition-colors hover:text-ink">
                {advanced ? <ChevronDown size={13} /> : <ChevronRight size={13} />} Avançado (parâmetros)
              </button>
              {advanced && (
                <div className="mt-3 space-y-3">
                  <label className="block">
                    <FieldLabel>params — JSON no formato SIFT &quot;tipo:o:default:desc&quot;</FieldLabel>
                    <textarea rows={4} value={paramsStr} onChange={(e) => setParamsStr(e.target.value)} className={`${inputCls} resize-y font-mono text-xs leading-5`} />
                  </label>
                  <label className="block">
                    <FieldLabel>returns — JSON array de campos</FieldLabel>
                    <textarea rows={2} value={returnsStr} onChange={(e) => setReturnsStr(e.target.value)} className={`${inputCls} resize-y font-mono text-xs leading-5`} />
                  </label>
                </div>
              )}
            </div>
          )}

          {toolType === "code" && (
            <p className="border-t border-border pt-4 text-xs leading-5 text-muted">
              Ferramentas executam código no servidor — não cole código de origens
              nas quais você não confia.
            </p>
          )}
        </aside>

        {/* área principal: código ou config da integração */}
        <main className="min-w-0 flex-1 p-5">
          {toolType === "code" ? (
            <CodeEditor value={code} onChange={setCode} placeholder="# seu código Python…" />
          ) : (
            <div className="mx-auto max-w-xl space-y-5">
              <div className="rounded-2xl border border-border bg-surface p-5">
                <h2 className="mb-4 flex items-center gap-2 text-sm font-semibold text-ink">
                  <Plug size={15} className="text-accent-hover" /> Servidor MCP
                </h2>
                <div className="space-y-4">
                  <div>
                    <FieldLabel>URL do servidor</FieldLabel>
                    <input
                      value={mcpUrl}
                      onChange={(e) => setMcpUrl(e.target.value)}
                      placeholder="https://exemplo.com/mcp"
                      className={`${inputCls} font-mono`}
                    />
                  </div>
                  <div>
                    <FieldLabel>Transporte</FieldLabel>
                    <select
                      value={mcpTransport}
                      onChange={(e) => setMcpTransport(e.target.value)}
                      className={inputCls}
                    >
                      <option value="http">Streamable HTTP</option>
                      <option value="sse">SSE (legado)</option>
                    </select>
                  </div>
                  <div>
                    <FieldLabel>Headers (opcional, JSON)</FieldLabel>
                    <textarea
                      rows={4}
                      value={mcpHeadersStr}
                      onChange={(e) => setMcpHeadersStr(e.target.value)}
                      placeholder='{"Authorization": "Bearer …"}'
                      className={`${inputCls} resize-y font-mono text-xs leading-5`}
                    />
                  </div>

                  {/* testar conexão */}
                  <div className="flex items-center gap-3 border-t border-border pt-4">
                    <button
                      onClick={testMcp}
                      disabled={testing}
                      className="flex items-center gap-2 rounded-full border border-border px-4 py-2 text-sm font-medium text-ink transition-colors hover:bg-hover disabled:opacity-60"
                    >
                      {testing ? <Loader2 size={15} className="animate-spin" /> : <Plug size={15} />}
                      {testing ? "Testando…" : "Testar conexão"}
                    </button>
                  </div>

                  {testResult && (
                    <div
                      className={`animate-pop rounded-xl border px-4 py-3 text-sm ${
                        testResult.ok
                          ? "border-green-500/30 bg-green-500/10 text-ink-soft"
                          : "border-red-500/30 bg-red-500/10 text-ink-soft"
                      }`}
                    >
                      {testResult.ok ? (
                        <div className="space-y-1.5">
                          <p className="flex items-center gap-2 font-medium text-green-400">
                            <CheckCircle2 size={15} /> Conexão bem-sucedida
                          </p>
                          {testResult.server?.name && (
                            <p className="text-xs">
                              Servidor: <span className="text-ink">{testResult.server.name}</span>
                              {testResult.server.version ? ` v${testResult.server.version}` : ""}
                            </p>
                          )}
                          {typeof testResult.tools_count === "number" && (
                            <p className="text-xs">
                              {testResult.tools_count} ferramenta{testResult.tools_count === 1 ? "" : "s"} disponível
                              {testResult.tools_count === 1 ? "" : "is"}
                              {testResult.tools?.length ? ": " : ""}
                              <span className="font-mono text-muted">{(testResult.tools ?? []).join(", ")}</span>
                            </p>
                          )}
                          {testResult.detail && <p className="text-xs text-muted">{testResult.detail}</p>}
                        </div>
                      ) : (
                        <p className="flex items-center gap-2 text-red-400">
                          <XCircle size={15} className="shrink-0" /> {testResult.error}
                        </p>
                      )}
                    </div>
                  )}
                </div>
              </div>

              <div className="flex items-start gap-2.5 rounded-xl border border-accent/25 bg-accent/10 px-4 py-3 text-xs leading-5 text-ink-soft">
                <Info size={14} className="mt-0.5 shrink-0 text-accent-hover" />
                <span>
                  A configuração fica salva desde já. A conexão com o servidor e a
                  importação das ferramentas dele serão ativadas em uma próxima
                  atualização — até lá, integrações não aparecem para o modelo.
                </span>
              </div>
            </div>
          )}
        </main>
      </div>
    </div>
  );
}
