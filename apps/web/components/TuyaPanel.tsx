"use client";

import { useCallback, useEffect, useState } from "react";
import { Check, ChevronDown, ChevronLeft, Home, Lightbulb, Loader2, Plug, RefreshCw, Snowflake, ToggleLeft, TriangleAlert, Wifi } from "lucide-react";
import { api, ApiError } from "@/lib/api";

interface Device { id: string; name: string; category: string; online: boolean }
interface Scene { id: string; name: string }
interface TuyaStatus {
  configured: boolean;
  base_url: string;
  access_id: string;
  has_secret: boolean;
  devices: Device[];
  aliases: Record<string, string>;
  scenes: Scene[];
}

const DEFAULT_BASE = "https://openapi.tuyaus.com";

/** Tela de detalhe "Tuya Smart Home" (aberta pelo card em Integrações): cada usuário liga a
 *  PRÓPRIA conexão Tuya Cloud, sincroniza os dispositivos automaticamente e (avançado)
 *  define apelidos. A escolha de dispositivos/ações por modelo fica na engrenagem da tool. */
export default function TuyaPanel({ onBack }: { onBack: () => void }) {
  const [st, setSt] = useState<TuyaStatus | null>(null);

  const load = useCallback(async () => {
    try {
      setSt(await api.get<TuyaStatus>("/integrations/tuya"));
    } catch {
      setSt({ configured: false, base_url: DEFAULT_BASE, access_id: "", has_secret: false, devices: [], aliases: {}, scenes: [] });
    }
  }, []);
  useEffect(() => { load(); }, [load]);

  return (
    <div className="pt-1">
      <button onClick={onBack} className="mb-3 flex items-center gap-1 text-sm text-muted transition-colors hover:text-ink">
        <ChevronLeft size={16} /> Voltar
      </button>

      <div className="flex items-center gap-2.5">
        <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-surface2 text-accent-hover">
          <Home size={17} />
        </span>
        <div>
          <p className="text-sm font-semibold text-ink">Tuya Smart Home</p>
          <p className="text-xs text-muted">Luzes, tomadas, ar-condicionado e cenas Smart Life</p>
        </div>
      </div>

      {st == null ? (
        <div className="flex justify-center py-10"><Loader2 size={18} className="animate-spin text-muted" /></div>
      ) : (
        <TuyaBody st={st} reload={load} setSt={setSt} />
      )}
    </div>
  );
}

const CAT_ICON = (cat: string) => {
  if (["dj", "dc", "xdd", "dd", "fwd", "tgq", "tyndj"].includes(cat)) return <Lightbulb size={15} />;
  if (["kt", "ktkzq", "ktqyz", "ktkg", "qn", "rs"].includes(cat)) return <Snowflake size={15} />;
  if (["cz", "pc", "xxj"].includes(cat)) return <Plug size={15} />;
  return <ToggleLeft size={15} />;
};

function TuyaBody({ st, reload, setSt }: { st: TuyaStatus; reload: () => Promise<void>; setSt: (s: TuyaStatus) => void }) {
  const [baseUrl, setBaseUrl] = useState(st.base_url || DEFAULT_BASE);
  const [accessId, setAccessId] = useState(st.access_id);
  const [secret, setSecret] = useState("");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [test, setTest] = useState<"idle" | "loading" | "ok" | "fail">("idle");
  const [testMsg, setTestMsg] = useState("");
  const [syncing, setSyncing] = useState(false);
  const [advanced, setAdvanced] = useState(false);

  async function save() {
    setErr(null);
    setSaving(true);
    try {
      await api.put("/integrations/tuya", {
        base_url: baseUrl.trim() || DEFAULT_BASE,
        access_id: accessId.trim(),
        access_secret: secret.trim() || null,
        aliases: st.aliases || {},
      });
      setSecret("");
      setSaved(true);
      setTimeout(() => setSaved(false), 1500);
      await reload();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Falha ao salvar");
    } finally {
      setSaving(false);
    }
  }

  async function testConn() {
    setTest("loading");
    setTestMsg("");
    try {
      const r = await api.post<{ ok: boolean; error?: string }>("/integrations/tuya/test");
      setTest(r.ok ? "ok" : "fail");
      if (!r.ok) setTestMsg(r.error || "");
    } catch (e) {
      setTest("fail");
      setTestMsg(e instanceof ApiError ? e.message : "");
    }
  }

  async function syncDevices() {
    setErr(null);
    setSyncing(true);
    try {
      const r = await api.post<{ devices: Device[]; scenes: Scene[] }>("/integrations/tuya/sync");
      setSt({ ...st, devices: r.devices, scenes: r.scenes });
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Falha ao sincronizar");
    } finally {
      setSyncing(false);
    }
  }

  return (
    <div className="mt-4 space-y-4">
      {/* credenciais */}
      <div className="space-y-2 rounded-xl border border-border bg-surface p-3">
        <p className="flex items-center gap-1.5 text-xs font-semibold text-ink">
          Conexão
          {st.configured && <span className="inline-flex items-center gap-0.5 text-[10px] font-normal text-green-500"><Check size={11} /> configurada</span>}
        </p>
        <label className="block text-sm">
          <span className="text-ink-soft">Data center (Base URL)</span>
          <input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} placeholder={DEFAULT_BASE}
            className="mt-1 w-full rounded-lg border border-border bg-surface2 px-3 py-1.5 font-mono text-xs text-ink outline-none focus:border-accent" />
        </label>
        <label className="block text-sm">
          <span className="text-ink-soft">Access ID</span>
          <input value={accessId} onChange={(e) => setAccessId(e.target.value)} placeholder="Client ID do projeto Tuya"
            className="mt-1 w-full rounded-lg border border-border bg-surface2 px-3 py-1.5 font-mono text-xs text-ink outline-none focus:border-accent" />
        </label>
        <label className="block text-sm">
          <span className="text-ink-soft">Access Secret</span>
          <input type="password" value={secret} onChange={(e) => setSecret(e.target.value)}
            placeholder={st.has_secret ? "•••••••• (deixe em branco p/ manter)" : "Client Secret do projeto Tuya"}
            className="mt-1 w-full rounded-lg border border-border bg-surface2 px-3 py-1.5 font-mono text-xs text-ink outline-none focus:border-accent" />
        </label>
        <div className="flex items-center gap-2 pt-0.5">
          <button onClick={save} disabled={saving || !accessId.trim()}
            className="rounded-full bg-accent px-4 py-1.5 text-xs font-medium text-white transition-colors hover:bg-accent-hover disabled:opacity-60">
            {saving ? "…" : saved ? "Salvo ✓" : "Salvar"}
          </button>
          <button onClick={testConn} disabled={test === "loading" || !st.configured}
            title={st.configured ? "Testar conexão" : "Salve as credenciais primeiro"}
            className={`flex items-center gap-1.5 rounded-full border border-border px-3 py-1.5 text-xs transition-colors disabled:opacity-50 ${
              test === "ok" ? "text-green-500" : test === "fail" ? "text-red-400" : "text-ink-soft hover:bg-hover hover:text-ink"
            }`}>
            {test === "loading" ? <Loader2 size={13} className="animate-spin" />
              : test === "ok" ? <Check size={13} />
              : test === "fail" ? <TriangleAlert size={13} /> : <Wifi size={13} />}
            {test === "ok" ? "Conectado" : test === "fail" ? "Falhou" : "Testar"}
          </button>
        </div>
        {test === "fail" && testMsg && <p className="text-[11px] text-red-400/90">{testMsg}</p>}
      </div>

      {err && <p className="flex items-center gap-1.5 text-xs text-red-400"><TriangleAlert size={13} /> {err}</p>}

      {/* dispositivos (auto-descobertos) */}
      <div>
        <div className="mb-1 flex items-center justify-between">
          <p className="text-xs font-semibold text-ink">
            Dispositivos {st.devices.length > 0 && <span className="text-muted">{st.devices.length}</span>}
          </p>
          <button onClick={syncDevices} disabled={syncing || !st.configured}
            title={st.configured ? "Buscar dispositivos na conta Tuya" : "Salve as credenciais primeiro"}
            className="flex items-center gap-1.5 rounded-full border border-border px-3 py-1 text-xs text-ink-soft transition-colors hover:bg-hover hover:text-ink disabled:opacity-50">
            <RefreshCw size={12} className={syncing ? "animate-spin" : ""} /> {syncing ? "Sincronizando…" : "Sincronizar"}
          </button>
        </div>
        <div className="rounded-xl border border-border bg-surface">
          {st.devices.length === 0 ? (
            <p className="px-3 py-3 text-xs text-muted">
              {st.configured ? "Clique em Sincronizar para buscar os dispositivos da sua conta Smart Life." : "Salve as credenciais e sincronize."}
            </p>
          ) : (
            st.devices.map((d, i) => (
              <div key={d.id} className={`flex items-center gap-2.5 px-3 py-2 text-sm ${i > 0 ? "border-t border-border" : ""}`}>
                <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-surface2 text-accent-hover">{CAT_ICON(d.category)}</span>
                <span className="min-w-0 flex-1 truncate text-ink">{d.name}</span>
                <span title={d.online ? "online" : "offline"} className={`h-2 w-2 shrink-0 rounded-full ${d.online ? "bg-green-500" : "bg-surface2"}`} />
              </div>
            ))
          )}
        </div>
        {st.scenes.length > 0 && (
          <p className="mt-1.5 text-[11px] text-muted">Cenas encontradas: {st.scenes.map((s) => s.name).join(", ")}</p>
        )}
      </div>

      {/* avançado: apelidos em linguagem natural */}
      <div>
        <button onClick={() => setAdvanced((v) => !v)} className="flex items-center gap-1 text-[11px] font-medium text-muted transition-colors hover:text-ink">
          <ChevronDown size={13} className={`transition-transform ${advanced ? "" : "-rotate-90"}`} /> Avançado — apelidos
        </button>
        {advanced && <AliasEditor st={st} reload={reload} setErr={setErr} />}
      </div>

      <p className="text-[11px] leading-4 text-muted">
        Crie um projeto Cloud em iot.tuya.com, vincule sua conta Smart Life (Devices → Link App Account) e cole o Access ID/Secret. Depois é só Sincronizar — os códigos de cada aparelho são detectados automaticamente. Quais dispositivos/ações cada modelo usa fica na engrenagem da ferramenta <span className="text-ink-soft">Tuya Smart Home</span>.
      </p>
    </div>
  );
}

/** Editor de apelidos (JSON): "quarto" → nome do dispositivo. Opcional. */
function AliasEditor({ st, reload, setErr }: { st: TuyaStatus; reload: () => Promise<void>; setErr: (e: string | null) => void }) {
  const [text, setText] = useState(() => JSON.stringify(st.aliases ?? {}, null, 2));
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  async function save() {
    let aliases: Record<string, string>;
    try {
      const v = JSON.parse(text || "{}");
      if (typeof v !== "object" || v === null || Array.isArray(v)) throw new Error();
      aliases = v;
    } catch {
      setErr('JSON de apelidos inválido.');
      return;
    }
    setErr(null);
    setSaving(true);
    try {
      await api.put("/integrations/tuya", { base_url: st.base_url, access_id: st.access_id, access_secret: null, aliases });
      setSaved(true);
      setTimeout(() => setSaved(false), 1500);
      await reload();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Falha ao salvar apelidos");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="mt-2 space-y-2">
      <textarea value={text} onChange={(e) => setText(e.target.value)} rows={4} spellCheck={false}
        className="w-full resize-y rounded-lg border border-border bg-surface2 px-3 py-2 font-mono text-[11px] leading-4 text-ink outline-none focus:border-accent" />
      <div className="flex items-center gap-2">
        <button onClick={save} disabled={saving || !st.configured}
          className="rounded-full border border-border px-3 py-1 text-xs text-ink-soft transition-colors hover:bg-hover hover:text-ink disabled:opacity-50">
          {saving ? "…" : saved ? "Salvo ✓" : "Salvar apelidos"}
        </button>
        <span className="text-[11px] text-muted">Ex.: {`{"quarto": "Luz do Quarto", "servidor": "Tomada PC"}`}</span>
      </div>
    </div>
  );
}
