"use client";

import { useEffect, useState } from "react";
import { Check, Globe, Loader2, Monitor, X } from "lucide-react";
import { api } from "@/lib/api";
import type { Model } from "@/lib/types";
import ModelField from "./ModelField";

/* Botão "Testar conexão": chama um endpoint e mostra ok/erro inline. */
type TestResult = { ok: boolean; error?: string | null; count?: number };
function TestButton({ run, label = "Testar conexão" }: { run: () => Promise<TestResult>; label?: string }) {
  const [state, setState] = useState<"idle" | "busy" | "ok" | "fail">("idle");
  const [msg, setMsg] = useState("");
  async function go() {
    setState("busy"); setMsg("");
    try {
      const r = await run();
      if (r.ok) { setState("ok"); setMsg(typeof r.count === "number" ? `${r.count} resultado(s)` : "Conectado"); }
      else { setState("fail"); setMsg(r.error || "Falhou"); }
    } catch (e: any) {
      setState("fail"); setMsg(e?.message || "Falhou");
    }
  }
  return (
    <div className="flex items-center gap-2">
      <button onClick={go} disabled={state === "busy"}
        className="flex items-center gap-1.5 rounded-lg border border-border bg-surface2 px-3 py-1.5 text-xs text-ink-soft transition-colors hover:bg-hover disabled:opacity-60">
        {state === "busy" ? <Loader2 size={13} className="animate-spin" /> : <Globe size={13} />} {label}
      </button>
      {state === "ok" && <span className="flex items-center gap-1 text-xs text-green-400"><Check size={13} /> {msg}</span>}
      {state === "fail" && <span className="flex items-center gap-1 text-xs text-red-400"><X size={13} /> <span className="max-w-[220px] truncate" title={msg}>{msg}</span></span>}
    </div>
  );
}

/* Painéis de configuração POR-MODELO das ferramentas internas (Pesquisa na Web,
 * Deep Search, Extração de Texto, Finanças). Usados no editor de modelos, ao
 * clicar na engrenagem de cada ferramenta em "Ferramentas Ativas" (abre em modal).
 * As CHAVES de API não moram aqui — ficam em Conexões → APIs; estes painéis só
 * mostram se a chave já está configurada. Interface: value + onChange. */

type Secrets = Record<string, boolean> | null;
type PanelProps = { value: Record<string, any>; onChange: (v: Record<string, any>) => void; status?: Secrets; reloadSecrets?: () => void; models?: Model[] };

/* ------------------------------- helpers UI ------------------------------- */
function Heading({ children }: { children: React.ReactNode }) {
  return <p className="mb-2 mt-5 border-b border-border pb-1.5 text-xs font-semibold text-ink first:mt-0">{children}</p>;
}
function Row({ label, sub, children }: { label: string; sub?: string; children?: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-4 py-2 text-sm">
      <div className="min-w-0">
        <p className="font-medium text-ink">{label}</p>
        {sub && <p className="text-xs text-muted">{sub}</p>}
      </div>
      {children}
    </div>
  );
}
function Toggle({ on, onClick }: { on: boolean; onClick: () => void }) {
  return (
    <button onClick={onClick} className={`relative h-6 w-11 shrink-0 rounded-full transition-colors ${on ? "bg-green-500" : "bg-surface2"}`}>
      <span className={`absolute top-0.5 h-5 w-5 rounded-full bg-white transition-all ${on ? "left-[22px]" : "left-0.5"}`} />
    </button>
  );
}
function NumberField({ label, value, onChange, suffix }: { label: string; value: number | ""; onChange: (v: number | "") => void; suffix?: string }) {
  return (
    <label className="flex items-center justify-between gap-4 py-2 text-sm">
      <span className="text-ink-soft">{label}</span>
      <span className="flex items-center gap-1.5">
        <input type="number" value={value} onChange={(e) => onChange(e.target.value === "" ? "" : Math.max(0, Number(e.target.value)))}
          className="w-24 rounded-lg border border-border bg-surface2 px-3 py-1.5 text-right text-sm text-ink outline-none focus:border-accent" />
        {suffix && <span className="text-xs text-muted">{suffix}</span>}
      </span>
    </label>
  );
}
/** Só INDICA se a chave já foi configurada — a chave em si é definida em
 *  Conexões → APIs (nível do usuário, não do modelo). */
function KeyStatus({ label, configured, hint }: { label: string; configured: boolean; hint?: string }) {
  return (
    <div className="space-y-0.5 py-2">
      <div className="flex items-center justify-between gap-3 text-sm">
        <span className="text-ink-soft">{label}</span>
        {configured ? (
          <span className="shrink-0 text-xs text-green-400">configurada ✓</span>
        ) : (
          <span className="shrink-0 text-xs text-amber-400">defina em Conexões → APIs</span>
        )}
      </div>
      {hint && <p className="text-xs text-muted">{hint}</p>}
    </div>
  );
}

/* ----------------------------- Pesquisa na Web ---------------------------- */
const ENGINES: { key: string; label: string; keyed: boolean; note: string }[] = [
  { key: "duckduckgo", label: "DuckDuckGo", keyed: false, note: "Sem chave" },
  { key: "searxng", label: "SearXNG", keyed: false, note: "Self-hosted" },
  { key: "tavily", label: "Tavily", keyed: true, note: "Requer chave" },
  { key: "brave", label: "Brave Search", keyed: true, note: "Requer chave" },
];

export function WebSearchPanel({ value, onChange, status, scope = "model" }: PanelProps & { scope?: "model" | "user" }) {
  const ws = value ?? {};
  const wsSet = (k: string, v: any) => onChange({ ...ws, [k]: v });
  const primary: string = ws.primary ?? "duckduckgo";
  const multi = !!ws.multi;
  const providers: string[] = ws.providers ?? [primary];
  const toggleProv = (k: string) => wsSet("providers", providers.includes(k) ? providers.filter((x) => x !== k) : [...providers, k]);
  const active = multi ? ENGINES.filter((e) => providers.includes(e.key)) : ENGINES.filter((e) => e.key === primary);
  const maxResults = ws.max_results === "" ? "" : ws.max_results ?? 5;
  return (
    <div>
      <p className="text-xs leading-5 text-muted">
        {scope === "user"
          ? "Mecanismo padrão de pesquisa da sua conta (vale para todos os modelos). Um modelo pode sobrepor na engrenagem da ferramenta Pesquisa na Web."
          : "Mecanismo que este modelo usa na busca. Configure a chave (quando exigida) e filtros."}
      </p>
      <Heading>Mecanismo</Heading>
      <div className="rounded-xl border border-border bg-surface px-3">
        <Row label="Mecanismo principal">
          <select value={primary} onChange={(e) => wsSet("primary", e.target.value)} className="rounded-lg bg-surface2 px-3 py-1.5 text-sm text-ink outline-none">
            {ENGINES.map((e) => <option key={e.key} value={e.key}>{e.label}</option>)}
          </select>
        </Row>
        <div className="border-t border-border">
          <Row label="Permitir múltiplos mecanismos" sub="Consulta vários e mescla (dedup por URL)">
            <Toggle on={multi} onClick={() => wsSet("multi", !multi)} />
          </Row>
        </div>
        {multi && (
          <div className="flex flex-wrap gap-1.5 border-t border-border py-3">
            {ENGINES.map((e) => {
              const sel = providers.includes(e.key);
              return (
                <button key={e.key} onClick={() => toggleProv(e.key)}
                  className={`rounded-full border px-3 py-1 text-xs transition-colors ${sel ? "border-accent/50 bg-accent/15 text-accent-hover" : "border-border text-muted hover:text-ink"}`}>{e.label}</button>
              );
            })}
          </div>
        )}
      </div>

      <Heading>Configuração dos mecanismos</Heading>
      <div className="space-y-2">
        {active.length === 0 && <p className="text-xs text-muted">Selecione ao menos um mecanismo acima.</p>}
        {active.map((e) => (
          <div key={e.key} className="rounded-xl border border-border bg-surface px-3.5 py-2.5">
            <div className="flex items-center justify-between">
              <span className="flex items-center gap-2 text-sm font-medium text-ink"><Globe size={14} className="text-accent-hover" /> {e.label}</span>
              <span className="text-[11px] text-muted">{e.note}</span>
            </div>
            {e.key === "searxng" && (
              <div className="pt-2">
                <p className="mb-1 text-xs text-muted">URL do SearXNG</p>
                <input value={ws.searxng_url ?? ""} onChange={(ev) => wsSet("searxng_url", ev.target.value)} placeholder="http://searxng:8080"
                  className="w-full rounded-lg border border-border bg-surface2 px-3 py-1.5 font-mono text-xs text-ink outline-none focus:border-accent" />
              </div>
            )}
            {e.key === "tavily" && <KeyStatus label="Chave Tavily" configured={status?.tavily ?? false} />}
            {e.key === "brave" && <KeyStatus label="Chave Brave Search" configured={status?.brave ?? false} />}
            {e.key === "duckduckgo" && <p className="pt-1 text-xs text-muted">Não requer configuração.</p>}
            <div className="pt-2">
              <TestButton run={() => api.post<TestResult>("/settings/test/web", { provider: e.key, searxng_url: ws.searxng_url ?? "" })} />
            </div>
          </div>
        ))}
      </div>

      <Heading>Filtros</Heading>
      <div className="rounded-xl border border-border bg-surface px-3 py-1">
        <NumberField label="Resultados por busca" value={maxResults} onChange={(v) => wsSet("max_results", v)} suffix="itens" />
        <div className="py-2">
          <p className="mb-1 text-sm text-ink-soft">Excluir domínios</p>
          <input value={ws.domain_filter ?? ""} onChange={(e) => wsSet("domain_filter", e.target.value)} placeholder="ex.: pinterest.com, exemplo.org"
            className="w-full rounded-lg border border-border bg-surface2 px-3 py-1.5 text-sm text-ink outline-none focus:border-accent" />
        </div>
      </div>
    </div>
  );
}

/* ----------------------- Navegador (headless Chromium) -------------------- */
export function BrowserPanel({ value, onChange }: { value: Record<string, any>; onChange: (v: Record<string, any>) => void }) {
  const b = value ?? {};
  const set = (k: string, v: any) => onChange({ ...b, [k]: v });
  const enabled = b.enabled !== false; // padrão: ligado (usa o env se sem ws_url)
  return (
    <div className="mt-6">
      <Heading>Navegador (Browser)</Heading>
      <p className="mb-2 text-xs leading-5 text-muted">
        Um Chromium headless que a IA controla (navega com JS, clica, digita, tira screenshot).
        Requer o serviço <code className="rounded bg-surface2 px-1">browser</code> (browserless).
        Deixe a URL em branco para usar a configuração do servidor (BROWSER_WS_URL).
      </p>
      <div className="rounded-xl border border-border bg-surface px-3">
        <Row label="Ativado" sub="Desligue para bloquear a ferramenta do navegador">
          <Toggle on={enabled} onClick={() => set("enabled", !enabled)} />
        </Row>
        <div className="border-t border-border py-2.5">
          <div className="flex items-center gap-2 text-sm font-medium text-ink"><Monitor size={14} className="text-accent-hover" /> Endpoint CDP</div>
          <p className="mb-1 mt-2 text-xs text-muted">URL WebSocket (ws://host:porta)</p>
          <input value={b.ws_url ?? ""} onChange={(e) => set("ws_url", e.target.value)} placeholder="ws://browser:3000"
            className="w-full rounded-lg border border-border bg-surface2 px-3 py-1.5 font-mono text-xs text-ink outline-none focus:border-accent" />
          <p className="mb-1 mt-2 text-xs text-muted">Token (opcional — protege o endpoint)</p>
          <input value={b.token ?? ""} onChange={(e) => set("token", e.target.value)} type="password" placeholder="BROWSER_TOKEN"
            className="w-full rounded-lg border border-border bg-surface2 px-3 py-1.5 font-mono text-xs text-ink outline-none focus:border-accent" />
        </div>
        <div className="border-t border-border py-2.5">
          <TestButton run={() => api.post<TestResult>("/settings/test/browser", { ws_url: b.ws_url ?? "", token: b.token ?? "" })} />
        </div>
      </div>
    </div>
  );
}

/* -------------------------------- Finanças -------------------------------- */
const FIN_PROVIDERS = [
  { key: "yahoo", label: "Yahoo Finance", note: "Sem chave · preço + gráfico" },
  { key: "finnhub", label: "Finnhub", note: "Requer chave · preço" },
  { key: "alphavantage", label: "Alpha Vantage", note: "Requer chave · limite baixo" },
  { key: "web", label: "Pesquisa na Web", note: "Fallback · sem gráfico" },
];
const FIN_CANON = ["yahoo", "finnhub", "alphavantage", "web"];

export function FinancePanel({ value, onChange, status }: PanelProps) {
  const fin = value ?? {};
  const providers: string[] = fin.providers ?? ["yahoo"];
  const enabled = new Set(providers);
  const toggle = (k: string) => {
    const s = new Set(providers);
    if (s.has(k)) s.delete(k); else s.add(k);
    onChange({ ...fin, providers: FIN_CANON.filter((x) => s.has(x)) });
  };
  return (
    <div>
      <p className="text-xs leading-5 text-muted">Fonte das cotações. Tentados nesta ordem até um responder. Yahoo funciona sem chave.</p>
      <Heading>Modo do card</Heading>
      <div className="rounded-xl border border-border bg-surface px-3">
        <Row label="Card visual (mini-gráfico)" sub="Quando o modelo não especifica">
          <select value={fin.card_mode ?? "on_request"} onChange={(e) => onChange({ ...fin, card_mode: e.target.value })}
            className="rounded-lg bg-surface2 px-3 py-1.5 text-sm text-ink outline-none">
            <option value="on_request">Só quando pedido</option>
            <option value="always">Sempre mostrar</option>
          </select>
        </Row>
      </div>
      <Heading>Provedores</Heading>
      <div className="rounded-xl border border-border bg-surface px-3">
        {FIN_PROVIDERS.map((p, i) => (
          <div key={p.key} className={i > 0 ? "border-t border-border" : ""}>
            <Row label={p.label} sub={p.note}><Toggle on={enabled.has(p.key)} onClick={() => toggle(p.key)} /></Row>
            {enabled.has(p.key) && p.key === "finnhub" && (
              <div className="pb-2"><KeyStatus label="Chave Finnhub" configured={status?.finnhub ?? false} hint="finnhub.io — free tier." /></div>
            )}
            {enabled.has(p.key) && p.key === "alphavantage" && (
              <div className="pb-2"><KeyStatus label="Chave Alpha Vantage" configured={status?.alphavantage ?? false} hint="alphavantage.co — 25 req/dia." /></div>
            )}
          </div>
        ))}
      </div>
      <p className="mt-2 text-xs text-muted">Sem nenhum marcado, usa o Yahoo.</p>
    </div>
  );
}

/* ----------------------------- Extração de Texto -------------------------- */
const EX_FORMATS = [
  { key: "pdf", label: "PDF", desc: "Documentos .pdf" },
  { key: "docx", label: "Word", desc: "Documentos .docx" },
  { key: "xlsx", label: "Excel", desc: "Planilhas .xlsx/.xlsm" },
  { key: "pptx", label: "PowerPoint", desc: "Apresentações .pptx" },
  { key: "csv", label: "CSV", desc: "Tabelas .csv" },
];

export function TextExtractionPanel({ value, onChange }: PanelProps) {
  const te = value ?? {};
  const teSet = (k: string, v: any) => onChange({ ...te, [k]: v });
  const on = (k: string) => te[k] !== false;
  const num = (k: string, d: number): number | "" => (te[k] === "" ? "" : te[k] == null ? d : te[k]);
  return (
    <div>
      <p className="text-xs leading-5 text-muted">Lê o texto de documentos anexados (só o texto vai ao modelo, já enxugado). Limites controlam o consumo de tokens.</p>
      <Heading>Formatos</Heading>
      <div className="rounded-xl border border-border bg-surface px-3">
        {EX_FORMATS.map((f, i) => (
          <div key={f.key} className={i > 0 ? "border-t border-border" : ""}>
            <Row label={f.label} sub={f.desc}><Toggle on={on(f.key)} onClick={() => teSet(f.key, !on(f.key))} /></Row>
          </div>
        ))}
      </div>
      <Heading>Filtros (economia de tokens)</Heading>
      <div className="rounded-xl border border-border bg-surface px-3 py-1">
        <NumberField label="Máx. de caracteres" value={num("max_chars", 20000)} onChange={(v) => teSet("max_chars", v)} suffix="chars" />
        <NumberField label="Máx. de páginas (PDF)" value={num("pdf_max_pages", 30)} onChange={(v) => teSet("pdf_max_pages", v)} suffix="págs" />
        <NumberField label="Máx. de linhas (Excel/CSV)" value={num("xlsx_max_rows", 200)} onChange={(v) => teSet("xlsx_max_rows", v)} suffix="linhas" />
        <Row label="Colapsar espaços em branco"><Toggle on={te.collapse_whitespace !== false} onClick={() => teSet("collapse_whitespace", te.collapse_whitespace === false)} /></Row>
      </div>
      <Heading>OCR (imagens e PDFs escaneados)</Heading>
      <div className="rounded-xl border border-border bg-surface px-3 py-1">
        <Row label="Ativar OCR" sub="Lê texto de imagens e PDFs sem texto"><Toggle on={on("ocr")} onClick={() => teSet("ocr", !on("ocr"))} /></Row>
        {on("ocr") && (
          <>
            <div className="border-t border-border">
              <Row label="Motor" sub="Tesseract é local/grátis; Visão usa o Vision Router">
                <select value={te.ocr_engine ?? "tesseract"} onChange={(e) => teSet("ocr_engine", e.target.value)} className="rounded-lg bg-surface2 px-3 py-1.5 text-sm text-ink outline-none">
                  <option value="tesseract">Tesseract (local)</option>
                  <option value="vision">Modelo de visão</option>
                </select>
              </Row>
            </div>
            {(te.ocr_engine ?? "tesseract") === "tesseract" && (
              <>
                <div className="flex items-center justify-between gap-4 border-t border-border py-2 text-sm">
                  <span className="text-ink-soft">Idiomas</span>
                  <input value={te.ocr_lang ?? "por+eng"} onChange={(e) => teSet("ocr_lang", e.target.value)} placeholder="por+eng"
                    className="w-40 rounded-lg border border-border bg-surface2 px-3 py-1.5 text-right font-mono text-xs text-ink outline-none focus:border-accent" />
                </div>
                <div className="border-t border-border">
                  <NumberField label="Máx. de páginas p/ OCR (PDF)" value={num("ocr_max_pages", 10)} onChange={(v) => teSet("ocr_max_pages", v)} suffix="págs" />
                </div>
              </>
            )}
          </>
        )}
      </div>
    </div>
  );
}

/* ------------------------------- Deep Search ------------------------------ */
export function DeepSearchPanel({ value, onChange, models = [] }: PanelProps) {
  const ds = value ?? {};
  const dsSet = (k: string, v: any) => onChange({ ...ds, [k]: v });
  const num = (k: string, d: number): number | "" => (ds[k] === "" ? "" : ds[k] == null ? d : ds[k]);
  const readPages = ds.read_pages !== false;
  return (
    <div>
      <p className="text-xs leading-5 text-muted">Pesquisa profunda: planeja, busca, lê, resume e cita fontes. O modelo só usa quando você pede uma pesquisa profunda.</p>
      <Heading>Modelo interno</Heading>
      <div className="rounded-xl border border-border bg-surface px-3 py-2">
        <ModelField models={models} value={ds.model ?? ""} onChange={(v) => dsSet("model", v)} placeholder="Modelo padrão (barato)" />
        <p className="mt-1 text-xs text-muted">Modelo do OpenRouter que planeja e resume (use um barato). Vazio = padrão.</p>
      </div>
      <Heading>Amplitude e profundidade</Heading>
      <div className="rounded-xl border border-border bg-surface px-3 py-1">
        <NumberField label="Sub-perguntas por rodada" value={num("max_subqueries", 3)} onChange={(v) => dsSet("max_subqueries", v)} suffix="máx." />
        <NumberField label="Rodadas (iterações)" value={num("max_rounds", 2)} onChange={(v) => dsSet("max_rounds", v)} suffix="máx." />
        <NumberField label="Resultados por busca" value={num("max_results_per_query", 4)} onChange={(v) => dsSet("max_results_per_query", v)} suffix="itens" />
      </div>
      <Heading>Leitura de páginas</Heading>
      <div className="rounded-xl border border-border bg-surface px-3 py-1">
        <Row label="Ler o conteúdo das páginas" sub="Mais completo; desligado usa só os trechos"><Toggle on={readPages} onClick={() => dsSet("read_pages", !readPages)} /></Row>
        {readPages && (
          <>
            <div className="border-t border-border"><NumberField label="Páginas por rodada" value={num("max_pages", 4)} onChange={(v) => dsSet("max_pages", v)} suffix="máx." /></div>
            <div className="border-t border-border"><NumberField label="Caracteres por página" value={num("max_page_chars", 4000)} onChange={(v) => dsSet("max_page_chars", v)} suffix="chars" /></div>
          </>
        )}
      </div>
    </div>
  );
}

/* --------------------------- Google (Gmail + Agenda) ---------------------- */
export function GooglePanel({ value, onChange }: PanelProps) {
  const g = value ?? {};
  const gSet = (k: string, v: any) => onChange({ ...g, [k]: v });
  const confirm = g.require_confirm !== false;
  const max = g.max_results === "" ? "" : g.max_results ?? 10;
  // ativação por operação (ausente = ligada); é o que "fragmenta" sem virar N tools
  const ops: Record<string, boolean> = g.ops && typeof g.ops === "object" ? g.ops : {};
  const opOn = (cap: string) => ops[cap] !== false;
  const toggleOp = (cap: string) => gSet("ops", { ...ops, [cap]: !opOn(cap) });

  const [accounts, setAccounts] = useState<{ id: string; email: string }[]>([]);
  useEffect(() => {
    api.get<{ accounts: { id: string; email: string }[] }>("/integrations/google")
      .then((s) => setAccounts(s.accounts || []))
      .catch(() => {});
  }, []);
  // seleção de contas liberadas: [] = todas. Marcar todas colapsa de volta p/ [].
  const allIds = accounts.map((a) => a.id);
  const sel: string[] = Array.isArray(g.accounts) ? g.accounts : [];
  const effective = sel.length === 0 ? allIds : sel;
  const toggleAccount = (id: string) => {
    const cur = sel.length === 0 ? allIds : sel;
    const next = cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id];
    gSet("accounts", next.length === allIds.length ? [] : next);
  };

  return (
    <div>
      <p className="text-xs leading-5 text-muted">Contas, ativação e limites deste modelo para o Google. A conexão das contas fica em Configurações → Integrações.</p>
      <Heading>Contas liberadas</Heading>
      <div className="rounded-xl border border-border bg-surface px-3 py-1">
        {accounts.length === 0 ? (
          <p className="py-2 text-xs text-muted">Nenhuma conta conectada. Conecte em Configurações → Integrações → Google Workspace.</p>
        ) : (
          <>
            {accounts.map((a, i) => (
              <label key={a.id} className={`flex cursor-pointer items-center gap-2.5 py-2 text-sm ${i > 0 ? "border-t border-border" : ""}`}>
                <input type="checkbox" checked={effective.includes(a.id)} onChange={() => toggleAccount(a.id)}
                  className="h-4 w-4 shrink-0 accent-accent" />
                <span className="min-w-0 flex-1 truncate text-ink">{a.email}</span>
              </label>
            ))}
            <p className="border-t border-border py-2 text-xs text-muted">Todas marcadas = este modelo pode usar qualquer conta.</p>
          </>
        )}
      </div>
      <Heading>Ativação — Gmail</Heading>
      <div className="rounded-xl border border-border bg-surface px-3 py-1">
        <Row label="Buscar e ler"><Toggle on={opOn("gmail_search")} onClick={() => toggleOp("gmail_search")} /></Row>
        <div className="border-t border-border"><Row label="Enviar e-mails"><Toggle on={opOn("gmail_send")} onClick={() => toggleOp("gmail_send")} /></Row></div>
        <div className="border-t border-border"><Row label="Organizar" sub="Arquivar, lixeira, marcar lido/não lido"><Toggle on={opOn("gmail_organize")} onClick={() => toggleOp("gmail_organize")} /></Row></div>
      </div>
      <Heading>Ativação — Agenda</Heading>
      <div className="rounded-xl border border-border bg-surface px-3 py-1">
        <Row label="Ver e buscar"><Toggle on={opOn("cal_view")} onClick={() => toggleOp("cal_view")} /></Row>
        <div className="border-t border-border"><Row label="Criar e editar"><Toggle on={opOn("cal_create")} onClick={() => toggleOp("cal_create")} /></Row></div>
        <div className="border-t border-border"><Row label="Excluir eventos"><Toggle on={opOn("cal_delete")} onClick={() => toggleOp("cal_delete")} /></Row></div>
      </div>
      <Heading>Segurança</Heading>
      <div className="rounded-xl border border-border bg-surface px-3 py-1">
        <Row label="Pedir confirmação antes de escrever" sub="E-mail: abre um rascunho editável p/ revisar e enviar. Agenda: Confirmar/Cancelar. Desligado = age direto.">
          <Toggle on={confirm} onClick={() => gSet("require_confirm", !confirm)} />
        </Row>
      </div>
      <Heading>Limites</Heading>
      <div className="rounded-xl border border-border bg-surface px-3 py-1">
        <NumberField label="Máx. de resultados" value={max} onChange={(v) => gSet("max_results", v)} suffix="itens" />
        <label className="flex items-center justify-between gap-4 border-t border-border py-2 text-sm">
          <span className="text-ink-soft">Agenda padrão</span>
          <input value={g.default_calendar ?? ""} onChange={(e) => gSet("default_calendar", e.target.value)} placeholder="primary"
            className="w-40 rounded-lg border border-border bg-surface2 px-3 py-1.5 text-sm text-ink outline-none focus:border-accent" />
        </label>
      </div>
    </div>
  );
}

/* ----------------------------- Tuya Smart Home ---------------------------- */
export function TuyaToolPanel({ value, onChange }: PanelProps) {
  const t = value ?? {};
  const tSet = (k: string, v: any) => onChange({ ...t, [k]: v });
  const confirm = t.require_confirm !== false;
  const ops: Record<string, boolean> = t.ops && typeof t.ops === "object" ? t.ops : {};
  const opOn = (cap: string) => ops[cap] !== false;
  const toggleOp = (cap: string) => tSet("ops", { ...ops, [cap]: !opOn(cap) });

  const [st, setSt] = useState<{ configured: boolean; devices: { id: string; name: string }[] } | null>(null);
  useEffect(() => {
    api.get<{ configured: boolean; devices: { id: string; name: string }[] }>("/integrations/tuya")
      .then((s) => setSt({ configured: !!s.configured, devices: Array.isArray(s.devices) ? s.devices : [] }))
      .catch(() => setSt({ configured: false, devices: [] }));
  }, []);
  const deviceNames = st ? st.devices.map((d) => d.name) : [];

  // seleção de dispositivos liberados: [] = todos. Marcar todos colapsa p/ [].
  const sel: string[] = Array.isArray(t.devices) ? t.devices : [];
  const effective = sel.length === 0 ? deviceNames : sel;
  const toggleDevice = (name: string) => {
    const cur = sel.length === 0 ? deviceNames : sel;
    const next = cur.includes(name) ? cur.filter((x) => x !== name) : [...cur, name];
    tSet("devices", next.length === deviceNames.length ? [] : next);
  };

  return (
    <div>
      <p className="text-xs leading-5 text-muted">Dispositivos e ações que este modelo pode controlar na sua casa. A conexão Tuya e o catálogo ficam em Configurações → Integrações.</p>
      <Heading>Dispositivos liberados</Heading>
      <div className="rounded-xl border border-border bg-surface px-3 py-1">
        {!st?.configured ? (
          <p className="py-2 text-xs text-muted">Tuya não conectado. Configure em Configurações → Integrações → Tuya Smart Home.</p>
        ) : deviceNames.length === 0 ? (
          <p className="py-2 text-xs text-muted">Nenhum dispositivo no catálogo. Adicione dispositivos na integração Tuya.</p>
        ) : (
          <>
            {deviceNames.map((name, i) => (
              <label key={name} className={`flex cursor-pointer items-center gap-2.5 py-2 text-sm ${i > 0 ? "border-t border-border" : ""}`}>
                <input type="checkbox" checked={effective.includes(name)} onChange={() => toggleDevice(name)}
                  className="h-4 w-4 shrink-0 accent-accent" />
                <span className="min-w-0 flex-1 truncate text-ink">{name}</span>
              </label>
            ))}
            <p className="border-t border-border py-2 text-xs text-muted">Todos marcados = este modelo pode usar qualquer dispositivo.</p>
          </>
        )}
      </div>
      <Heading>Ações permitidas</Heading>
      <div className="rounded-xl border border-border bg-surface px-3 py-1">
        <Row label="Consultar" sub="Listar dispositivos e ver status/códigos"><Toggle on={opOn("tuya_query")} onClick={() => toggleOp("tuya_query")} /></Row>
        <div className="border-t border-border"><Row label="Ligar / desligar" sub="Luzes, tomadas, interruptores"><Toggle on={opOn("tuya_switch")} onClick={() => toggleOp("tuya_switch")} /></Row></div>
        <div className="border-t border-border"><Row label="Ar-condicionado" sub="Ligar e ajustar temperatura/modo"><Toggle on={opOn("tuya_ac")} onClick={() => toggleOp("tuya_ac")} /></Row></div>
        <div className="border-t border-border"><Row label="Disparar cenas" sub="Tap-to-run configuradas"><Toggle on={opOn("tuya_scene")} onClick={() => toggleOp("tuya_scene")} /></Row></div>
      </div>
      <Heading>Segurança</Heading>
      <div className="rounded-xl border border-border bg-surface px-3 py-1">
        <Row label="Pedir confirmação antes de agir" sub="Mostra Confirmar/Cancelar antes de ligar, desligar ou disparar cena">
          <Toggle on={confirm} onClick={() => tSet("require_confirm", !confirm)} />
        </Row>
      </div>
    </div>
  );
}

/* ---------------------------------- GitHub -------------------------------- */
export function GithubToolPanel({ value, onChange }: PanelProps) {
  const g = value ?? {};
  const gSet = (k: string, v: any) => onChange({ ...g, [k]: v });
  const confirm = g.require_confirm !== false;
  const ops: Record<string, boolean> = g.ops && typeof g.ops === "object" ? g.ops : {};
  const opOn = (cap: string) => ops[cap] !== false;
  const toggleOp = (cap: string) => gSet("ops", { ...ops, [cap]: !opOn(cap) });

  const [accounts, setAccounts] = useState<{ id: string; login: string }[]>([]);
  useEffect(() => {
    api.get<{ accounts: { id: string; login: string }[] }>("/integrations/github")
      .then((s) => setAccounts(s.accounts || []))
      .catch(() => {});
  }, []);
  const allIds = accounts.map((a) => a.id);
  const sel: string[] = Array.isArray(g.accounts) ? g.accounts : [];
  const effective = sel.length === 0 ? allIds : sel;
  const toggleAccount = (id: string) => {
    const cur = sel.length === 0 ? allIds : sel;
    const next = cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id];
    gSet("accounts", next.length === allIds.length ? [] : next);
  };

  return (
    <div>
      <p className="text-xs leading-5 text-muted">Contas e ações deste modelo no GitHub. A conexão das contas fica em Configurações → Integrações.</p>
      <Heading>Contas liberadas</Heading>
      <div className="rounded-xl border border-border bg-surface px-3 py-1">
        {accounts.length === 0 ? (
          <p className="py-2 text-xs text-muted">Nenhuma conta conectada. Conecte em Configurações → Integrações → GitHub.</p>
        ) : (
          <>
            {accounts.map((a, i) => (
              <label key={a.id} className={`flex cursor-pointer items-center gap-2.5 py-2 text-sm ${i > 0 ? "border-t border-border" : ""}`}>
                <input type="checkbox" checked={effective.includes(a.id)} onChange={() => toggleAccount(a.id)}
                  className="h-4 w-4 shrink-0 accent-accent" />
                <span className="min-w-0 flex-1 truncate text-ink">{a.login}</span>
              </label>
            ))}
            <p className="border-t border-border py-2 text-xs text-muted">Todas marcadas = este modelo pode usar qualquer conta.</p>
          </>
        )}
      </div>
      <Heading>Leitura</Heading>
      <div className="rounded-xl border border-border bg-surface px-3 py-1">
        <Row label="Ler repos, arquivos, issues e PRs" sub="Listar repositórios, ler arquivos, buscar código, ver issues/PRs"><Toggle on={opOn("gh_read")} onClick={() => toggleOp("gh_read")} /></Row>
      </div>
      <Heading>Escrita</Heading>
      <div className="rounded-xl border border-border bg-surface px-3 py-1">
        <Row label="Criar issues"><Toggle on={opOn("gh_issue")} onClick={() => toggleOp("gh_issue")} /></Row>
        <div className="border-t border-border"><Row label="Comentar em issues/PRs"><Toggle on={opOn("gh_comment")} onClick={() => toggleOp("gh_comment")} /></Row></div>
        <div className="border-t border-border"><Row label="Abrir pull requests"><Toggle on={opOn("gh_pr")} onClick={() => toggleOp("gh_pr")} /></Row></div>
        <div className="border-t border-border"><Row label="Commitar arquivos" sub="Criar/atualizar arquivos (commit direto)"><Toggle on={opOn("gh_commit")} onClick={() => toggleOp("gh_commit")} /></Row></div>
      </div>
      <Heading>Segurança</Heading>
      <div className="rounded-xl border border-border bg-surface px-3 py-1">
        <Row label="Pedir confirmação antes de escrever" sub="Mostra Confirmar/Cancelar antes de criar issue/PR/comentário/commit. Desligado = age direto.">
          <Toggle on={confirm} onClick={() => gSet("require_confirm", !confirm)} />
        </Row>
      </div>
    </div>
  );
}

const MSG_PLAT_PT: Record<string, string> = { whatsapp: "WhatsApp", telegram: "Telegram", discord: "Discord" };

export function MessagingToolPanel({ value, onChange }: PanelProps) {
  const g = value ?? {};
  const gSet = (k: string, v: any) => onChange({ ...g, [k]: v });
  const confirm = g.require_confirm !== false;
  const ops: Record<string, boolean> = g.ops && typeof g.ops === "object" ? g.ops : {};
  const opOn = (cap: string) => ops[cap] !== false;
  const toggleOp = (cap: string) => gSet("ops", { ...ops, [cap]: !opOn(cap) });

  const [accounts, setAccounts] = useState<{ id: string; platform: string; label: string }[]>([]);
  useEffect(() => {
    api.get<{ accounts: { id: string; platform: string; label: string }[] }>("/integrations/messaging/connections")
      .then((s) => setAccounts(s.accounts || []))
      .catch(() => {});
  }, []);
  const allIds = accounts.map((a) => a.id);
  const sel: string[] = Array.isArray(g.accounts) ? g.accounts : [];
  const effective = sel.length === 0 ? allIds : sel;
  const toggleAccount = (id: string) => {
    const cur = sel.length === 0 ? allIds : sel;
    const next = cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id];
    gSet("accounts", next.length === allIds.length ? [] : next);
  };

  return (
    <div>
      <p className="text-xs leading-5 text-muted">A IA age nas suas conexões de chat a seu pedido (responder, avisar em grupo, ver o que disseram). Conecte-as em Configurações → Integrações.</p>
      <p className="mt-1 text-xs leading-5 text-muted">Limites por rede: <span className="text-ink-soft">WhatsApp</span> age como você (ler/enviar completo). <span className="text-ink-soft">Telegram/Discord</span> agem como o bot — só as conversas onde o bot está; o Telegram-bot não lê histórico.</p>
      <Heading>Conexões liberadas</Heading>
      <div className="rounded-xl border border-border bg-surface px-3 py-1">
        {accounts.length === 0 ? (
          <p className="py-2 text-xs text-muted">Nenhuma conexão ativa. Conecte um WhatsApp, Telegram ou Discord em Configurações → Integrações.</p>
        ) : (
          <>
            {accounts.map((a, i) => (
              <label key={a.id} className={`flex cursor-pointer items-center gap-2.5 py-2 text-sm ${i > 0 ? "border-t border-border" : ""}`}>
                <input type="checkbox" checked={effective.includes(a.id)} onChange={() => toggleAccount(a.id)}
                  className="h-4 w-4 shrink-0 accent-accent" />
                <span className="min-w-0 flex-1 truncate text-ink">{a.label}</span>
                <span className="shrink-0 text-xs text-muted">{MSG_PLAT_PT[a.platform] || a.platform}</span>
              </label>
            ))}
            <p className="border-t border-border py-2 text-xs text-muted">Todas marcadas = este modelo pode agir por qualquer conexão.</p>
          </>
        )}
      </div>
      <Heading>Ações</Heading>
      <div className="rounded-xl border border-border bg-surface px-3 py-1">
        <Row label="Listar conversas" sub="Encontrar contatos e grupos"><Toggle on={opOn("msg_list")} onClick={() => toggleOp("msg_list")} /></Row>
        <div className="border-t border-border"><Row label="Ler mensagens" sub="Ver o histórico de uma conversa (WhatsApp/Discord)"><Toggle on={opOn("msg_read")} onClick={() => toggleOp("msg_read")} /></Row></div>
        <div className="border-t border-border"><Row label="Enviar mensagens" sub="Mandar mensagem por você"><Toggle on={opOn("msg_send")} onClick={() => toggleOp("msg_send")} /></Row></div>
      </div>
      <Heading>Segurança</Heading>
      <div className="rounded-xl border border-border bg-surface px-3 py-1">
        <Row label="Pedir confirmação antes de enviar" sub="Mostra Enviar/Cancelar antes de mandar uma mensagem. Desligado = envia direto.">
          <Toggle on={confirm} onClick={() => gSet("require_confirm", !confirm)} />
        </Row>
      </div>
    </div>
  );
}

/* ------------------------------ Remote Terminal --------------------------- */
/** Config POR-MODELO do Remote Terminal: quais máquinas este modelo alcança e o que
 *  pode fazer nelas. A confirmação de verdade é POR-MÁQUINA (em Integrações → Remote
 *  Terminal); o toggle daqui é um piso adicional — um modelo pode exigir aval mesmo
 *  numa máquina marcada como "não perguntar". */
export function RemoteTerminalToolPanel({ value, onChange }: PanelProps) {
  const g = value ?? {};
  const gSet = (k: string, v: any) => onChange({ ...g, [k]: v });
  const confirm = g.require_confirm !== false;
  const ops: Record<string, boolean> = g.ops && typeof g.ops === "object" ? g.ops : {};
  const opOn = (cap: string) => ops[cap] !== false;
  const toggleOp = (cap: string) => gSet("ops", { ...ops, [cap]: !opOn(cap) });

  const [hosts, setHosts] = useState<{ id: string; name: string; slug: string; status: string; egress: { mode: string } }[]>([]);
  useEffect(() => {
    api.get<{ hosts: any[] }>("/remote/hosts").then((s) => setHosts(s.hosts || [])).catch(() => {});
  }, []);
  const allIds = hosts.map((h) => h.id);
  const sel: string[] = Array.isArray(g.hosts) ? g.hosts : [];
  const effective = sel.length === 0 ? allIds : sel;
  const toggleHost = (id: string) => {
    const cur = sel.length === 0 ? allIds : sel;
    const next = cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id];
    gSet("hosts", next.length === allIds.length ? [] : next);
  };

  return (
    <div>
      <p className="text-xs leading-5 text-muted">
        Este modelo roda comandos no terminal das suas máquinas remotas. Não é o sandbox do servidor: é a
        máquina de verdade, com a rede dela. Conecte e configure a saída de rede em Configurações →
        Integrações → Remote Terminal.
      </p>
      <Heading>Máquinas liberadas</Heading>
      <div className="rounded-xl border border-border bg-surface px-3 py-1">
        {hosts.length === 0 ? (
          <p className="py-2 text-xs text-muted">Nenhuma máquina conectada. Adicione uma em Configurações → Integrações → Remote Terminal.</p>
        ) : (
          <>
            {hosts.map((h, i) => (
              <label key={h.id} className={`flex cursor-pointer items-center gap-2.5 py-2 text-sm ${i > 0 ? "border-t border-border" : ""}`}>
                <input type="checkbox" checked={effective.includes(h.id)} onChange={() => toggleHost(h.id)}
                  className="h-4 w-4 shrink-0 accent-accent" />
                <span className="min-w-0 flex-1 truncate text-ink">{h.name} <span className="text-muted">({h.slug})</span></span>
                <span className={`shrink-0 text-xs ${h.status === "online" ? "text-green-400" : h.status === "blocked" ? "text-amber-400" : "text-muted"}`}>{h.status}</span>
              </label>
            ))}
            <p className="border-t border-border py-2 text-xs text-muted">Todas marcadas = este modelo pode usar qualquer máquina.</p>
          </>
        )}
      </div>
      <Heading>Ações</Heading>
      <div className="rounded-xl border border-border bg-surface px-3 py-1">
        <Row label="Rodar comandos" sub="Executar e esperar a saída"><Toggle on={opOn("run")} onClick={() => toggleOp("run")} /></Row>
        <div className="border-t border-border"><Row label="Comandos em segundo plano" sub="Instalações e builds longos (job_id)"><Toggle on={opOn("start")} onClick={() => toggleOp("start")} /></Row></div>
        <div className="border-t border-border"><Row label="Consultar jobs e política de rede" sub="Status de jobs, matar job e teste de vazamento"><Toggle on={opOn("manage")} onClick={() => toggleOp("manage")} /></Row></div>
      </div>
      <Heading>Segurança</Heading>
      <div className="rounded-xl border border-border bg-surface px-3 py-1">
        <Row label="Sempre pedir confirmação" sub="Vale mesmo nas máquinas configuradas para não perguntar. Cada máquina tem seu próprio pedido de confirmação.">
          <Toggle on={confirm} onClick={() => gSet("require_confirm", !confirm)} />
        </Row>
      </div>
    </div>
  );
}
