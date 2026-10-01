"use client";

/**
 * "Contas conectadas" — a mesma lista em toda integração que aceita várias contas.
 *
 * Busca em cima, ~3 contas à vista (o resto rola por dentro) e o botão de conectar
 * FIXO embaixo. A 1ª conta é a principal (a que a IA usa quando o pedido não diz
 * qual). `useOAuthConnect` cuida do login: abre o provedor FORA do app (no desktop,
 * no navegador do sistema; a página atual não navega) e acompanha a tentativa no
 * servidor até o retorno chegar.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, ExternalLink, Loader2, MoreHorizontal, Plus, RefreshCw, Search, Star, Trash2, Wifi } from "lucide-react";
import { api, API_URL, ApiError } from "@/lib/api";
import { openExternal } from "@/lib/desktop";
import { AnchoredMenu, MenuDivider, MenuItem } from "./ui";
import { toast } from "./Toaster";
import { tr } from "@/lib/i18n";

export interface AccountItem {
  id: string;
  label: string;
  primary?: boolean;
  /** o provedor recusou renovar o acesso: mostra "Reconectar" */
  broken?: boolean;
}

type Attempt = { status: "pending" | "ok" | "error"; email?: string; error?: string };

/** Login OAuth fora do app. `base` = rota da integração (ex.: "/integrations/google"):
 *  POST `${base}/connect` → {url, attempt}; GET `${base}/connect/{attempt}` até sair
 *  de "pending". */
export function useOAuthConnect(base: string, onDone: (email: string) => void) {
  const [waiting, setWaiting] = useState(false);
  const [url, setUrl] = useState<string | null>(null);
  const [blocked, setBlocked] = useState(false);
  const run = useRef(0);
  const done = useRef(onDone);
  done.current = onDone;

  const cancel = useCallback(() => { run.current++; setWaiting(false); setUrl(null); setBlocked(false); }, []);
  useEffect(() => () => { run.current++; }, []);

  const start = useCallback(async () => {
    const id = ++run.current;
    setWaiting(true);
    setBlocked(false);
    let r: { url: string; attempt: string };
    try {
      r = await api.post<{ url: string; attempt: string }>(`${base}/connect`, { origin: API_URL });
    } catch (e) {
      setWaiting(false);
      toast(e instanceof ApiError ? e.message : tr("Falha ao iniciar a conexão."));
      return;
    }
    if (id !== run.current) return;
    setUrl(r.url);
    // bloqueador de pop-up (o clique "venceu" durante o fetch): fica o botão Abrir
    if (!(await openExternal(r.url))) setBlocked(true);
    const limite = Date.now() + 10 * 60_000;
    while (id === run.current && Date.now() < limite) {
      await new Promise((ok) => setTimeout(ok, 1500));
      if (id !== run.current) return;
      let a: Attempt;
      try {
        a = await api.get<Attempt>(`${base}/connect/${r.attempt}`);
      } catch {
        continue; // rede piscou: tenta de novo no próximo ciclo
      }
      if (a.status === "pending") continue;
      if (id !== run.current) return;
      setWaiting(false);
      setUrl(null);
      if (a.status === "ok") {
        toast(a.email ? `Conectada: ${a.email}` : tr("Conta conectada."), "success");
        done.current(a.email || "");
      } else {
        toast(a.error || tr("Não deu para conectar."));
      }
      return;
    }
    if (id === run.current) setWaiting(false);
  }, [base]);

  const reopen = useCallback(() => { if (url) void openExternal(url); setBlocked(false); }, [url]);
  return { waiting, blocked, start, cancel, reopen };
}

export default function ConnectedAccounts({
  items, connect, connectLabel, connectIcon, disabledReason,
  onMakePrimary, onTest, onRemove, searchPlaceholder = tr("Buscar contas…"),
}: {
  items: AccountItem[];
  connect: ReturnType<typeof useOAuthConnect>;
  connectLabel: string;
  connectIcon?: React.ReactNode;
  /** conectar indisponível (ex.: falta o app OAuth) — o motivo aparece no rodapé */
  disabledReason?: string;
  onMakePrimary?: (id: string) => void | Promise<void>;
  onTest?: (id: string) => Promise<boolean>;
  onRemove: (item: AccountItem) => void | Promise<void>;
  searchPlaceholder?: string;
}) {
  const [q, setQ] = useState("");
  const lista = useMemo(() => {
    const t = q.trim().toLowerCase();
    return t ? items.filter((i) => i.label.toLowerCase().includes(t)) : items;
  }, [items, q]);

  return (
    <div className="overflow-hidden rounded-xl border border-border bg-surface">
      <div className="flex items-center gap-2 border-b border-border px-3 py-2">
        <Search size={14} className="shrink-0 text-muted" />
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={searchPlaceholder} aria-label={searchPlaceholder}
          className="w-full bg-transparent text-sm text-ink outline-none placeholder:text-muted" />
      </div>
      {/* ~3 contas à vista; o resto rola aqui dentro */}
      <div className="max-h-[144px] overflow-y-auto p-1.5">
        {lista.length === 0 ? (
          <p className="px-3 py-3.5 text-center text-xs text-muted">{items.length ? tr("Nada encontrado.") : tr("Nenhuma conta.")}</p>
        ) : lista.map((a) => (
          <Linha key={a.id} a={a} connect={connect} onMakePrimary={onMakePrimary} onTest={onTest} onRemove={onRemove} />
        ))}
      </div>
      <div className="border-t border-border p-1.5">
        {connect.waiting ? (
          <div className="flex h-9 items-center gap-2 px-2.5 text-sm text-ink-soft">
            <Loader2 size={15} className="shrink-0 animate-spin text-muted" />
            <span className="min-w-0 flex-1 truncate">{connect.blocked ? tr("Abra o login para continuar") : "Aguardando o login…"}</span>
            <button type="button" onClick={connect.reopen}
              className={`flex shrink-0 items-center gap-1 rounded-lg px-2 py-1 text-xs transition-colors ${connect.blocked ? "bg-accent text-white hover:bg-accent-hover" : "text-muted hover:bg-hover hover:text-ink"}`}>
              <ExternalLink size={13} />  {tr("Abrir")}
            </button>
            <button type="button" onClick={connect.cancel}
              className="shrink-0 rounded-lg px-2 py-1 text-xs text-muted transition-colors hover:bg-hover hover:text-ink">
              
              {tr("Cancelar")}
            </button>
          </div>
        ) : (
          <button type="button" onClick={() => void connect.start()} disabled={!!disabledReason}
            title={disabledReason}
            className="flex h-9 w-full items-center gap-2.5 rounded-lg px-2.5 text-left text-sm text-ink-soft transition-colors hover:bg-hover hover:text-ink disabled:cursor-not-allowed disabled:opacity-60 disabled:hover:bg-transparent">
            <span className="shrink-0 text-muted">{connectIcon ?? <Plus size={15} />}</span>
            <span className="min-w-0 flex-1 truncate">{disabledReason || connectLabel}</span>
          </button>
        )}
      </div>
    </div>
  );
}

function Linha({ a, connect, onMakePrimary, onTest, onRemove }: {
  a: AccountItem;
  connect: ReturnType<typeof useOAuthConnect>;
  onMakePrimary?: (id: string) => void | Promise<void>;
  onTest?: (id: string) => Promise<boolean>;
  onRemove: (item: AccountItem) => void | Promise<void>;
}) {
  const [menu, setMenu] = useState(false);
  const [testando, setTestando] = useState(false);
  const btn = useRef<HTMLButtonElement>(null);
  const fechar = useCallback(() => setMenu(false), []);

  async function testar() {
    setMenu(false);
    if (!onTest) return;
    setTestando(true);
    try {
      const ok = await onTest(a.id);
      toast(ok ? tr("{label}: acesso OK.", { label: a.label }) : tr("{label}: sem acesso. Reconecte.", { label: a.label }), ok ? "success" : "error");
    } finally {
      setTestando(false);
    }
  }

  return (
    <div className="group flex h-11 items-center gap-2.5 rounded-lg px-2 transition-colors hover:bg-hover">
      <span className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-[11px] font-semibold ${a.broken ? "bg-amber-500/15 text-amber-400" : "bg-surface2 text-ink"}`}>
        {a.broken ? <AlertTriangle size={13} /> : (a.label[0] || "?").toUpperCase()}
      </span>
      <span className="min-w-0 flex-1 truncate text-sm text-ink">{a.label}</span>
      {a.primary && (
        <span className="shrink-0 rounded-full bg-accent/15 px-2 py-0.5 text-[10px] font-medium text-accent-hover">{tr("Principal")}</span>
      )}
      {a.broken && (
        <button type="button" onClick={() => void connect.start()} disabled={connect.waiting}
          className="flex shrink-0 items-center gap-1 rounded-full bg-amber-500/15 px-2 py-0.5 text-[11px] font-medium text-amber-400 transition-colors hover:bg-amber-500/25 disabled:opacity-60">
          <RefreshCw size={11} />  {tr("Reconectar")}
        </button>
      )}
      {testando ? (
        <Loader2 size={15} className="shrink-0 animate-spin text-muted" />
      ) : (
        <button ref={btn} type="button" onClick={() => setMenu((m) => !m)} aria-label={tr("Opções de {label}", { label: a.label })}
          className="shrink-0 rounded-md p-1 text-muted transition-colors hover:bg-surface2 hover:text-ink">
          <MoreHorizontal size={15} />
        </button>
      )}
      {menu && (
        <AnchoredMenu anchorRef={btn} onClose={fechar}>
          {onMakePrimary && !a.primary && (
            <MenuItem icon={<Star size={14} />} onClick={() => { setMenu(false); void onMakePrimary(a.id); }}>{tr("Tornar principal")}</MenuItem>
          )}
          {onTest && <MenuItem icon={<Wifi size={14} />} onClick={() => void testar()}>{tr("Testar acesso")}</MenuItem>}
          <MenuDivider />
          <MenuItem icon={<Trash2 size={14} />} danger onClick={() => { setMenu(false); void onRemove(a); }}>{tr("Remover")}</MenuItem>
        </AnchoredMenu>
      )}
    </div>
  );
}

/** Erro de API → texto para o aviso. */
export function errText(e: unknown, fallback: string) {
  return e instanceof ApiError ? e.message : fallback;
}
