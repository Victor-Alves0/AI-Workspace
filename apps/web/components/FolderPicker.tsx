"use client";

/**
 * Seletor de pastas do chat (no composer, como no Claude/Codex): em que pasta a IA
 * lê e grava arquivos. A principal por padrão; outra do disco ("Escolher pasta…");
 * ou "Sem pasta" — sem ferramentas de arquivo, execução nem download.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowUp, Check, ChevronRight, Folder, FolderOpen, FolderPlus, FolderX, HardDrive, Home, Loader2, Search, X } from "lucide-react";
import { api } from "@/lib/api";
import { finePointer, useClickOutside } from "./ui";
import { tr } from "@/lib/i18n";

export interface WorkspaceFolder { id: string; name: string; path: string; source: string; home: boolean }
interface FoldersData { home: WorkspaceFolder; folders: WorkspaceFolder[]; browse_anywhere: boolean }
interface BrowseData { path: string; parent: string | null; home: string; dirs: { name: string; path: string }[]; roots: { name: string; path: string }[]; truncated: boolean }

/** `value`: null = pasta principal · "off" = sem pasta · id de uma pasta/projeto. */
export default function FolderPicker({ value, onChange, lockedName, menuUp }: {
  value: string | null | undefined;
  onChange: (v: string | null) => void;
  /** chat de projeto do Codespace: a pasta é a do projeto (não troca aqui) */
  lockedName?: string | null;
  menuUp?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<FoldersData | null>(null);
  const [browse, setBrowse] = useState(false);
  const [q, setQ] = useState("");
  const ref = useClickOutside<HTMLDivElement>(() => setOpen(false));
  useEffect(() => { if (!open) setQ(""); }, [open]);

  const load = useCallback(async () => {
    try { setData(await api.get<FoldersData>("/workspace/folders")); } catch { /* sem pasta disponível */ }
  }, []);
  useEffect(() => { void load(); }, [load]);

  if (lockedName) {
    return (
      <span title={tr("Projeto do Codespace: {lockedName}", { lockedName: lockedName })}
        className="flex h-8 max-w-[160px] items-center gap-1.5 rounded-full px-2 text-xs text-muted">
        <FolderOpen size={15} className="shrink-0" /><span className="truncate">{lockedName}</span>
      </span>
    );
  }
  const off = value === "off";
  const atual = off ? null : value ? data?.folders.find((f) => f.id === value) ?? null : data?.home ?? null;
  const rotulo = off ? tr("Sem pasta") : atual?.name ?? tr("Pasta principal");
  const escolher = (v: string | null) => { onChange(v); setOpen(false); };
  const termo = q.trim().toLowerCase();
  const lista = (data ? [data.home, ...data.folders] : [])
    .filter((f) => !termo || f.name.toLowerCase().includes(termo) || f.path.toLowerCase().includes(termo));

  return (
    <div ref={ref} className="relative">
      <button type="button" onClick={() => { setOpen((o) => !o); if (!open) void load(); }}
        title={off ? tr("Sem pasta: a IA não cria nem edita arquivos") : tr("Pasta de trabalho: {0}", { "0": atual?.path ?? "" })}
        aria-label={tr("Pasta de trabalho")}
        className={`flex h-8 max-w-[170px] shrink-0 items-center gap-1.5 rounded-full px-2 text-xs transition-colors hover:bg-hover ${off ? "text-muted" : "text-ink-soft"}`}>
        {off ? <FolderX size={15} className="shrink-0" /> : <Folder size={15} className="shrink-0" />}
        {/* celular: só o ícone (o nome apertava a caixa de mensagem) */}
        <span className="truncate max-md:hidden">{rotulo}</span>
      </button>
      {open && (
        <div className={`animate-pop absolute left-0 z-50 w-80 max-w-[calc(100vw-1rem)] overflow-hidden rounded-xl border border-border bg-surface shadow-menu ${menuUp ? "bottom-full mb-2" : "top-full mt-2"}`}>
          <div className="flex items-center gap-2 border-b border-border px-3 py-2">
            <Search size={14} className="text-muted" />
            <input autoFocus={finePointer()} value={q} onChange={(e) => setQ(e.target.value)}
              placeholder={tr("Buscar pastas…")} aria-label={tr("Buscar pastas")}
              className="w-full bg-transparent text-sm text-ink outline-none placeholder:text-muted" />
          </div>
          {/* ~3 pastas à vista; o resto rola aqui dentro */}
          <div className="max-h-[156px] overflow-y-auto p-1.5">
            {lista.length === 0 ? (
              <p className="px-3 py-4 text-center text-xs text-muted">{data ? tr("Nada encontrado.") : tr("Carregando…")}</p>
            ) : lista.map((f) => (
              <Opcao key={f.id} icon={f.home ? <Home size={15} /> : <Folder size={15} />} nome={f.name} sub={f.path}
                ativo={f.home ? !off && !value : value === f.id} onClick={() => escolher(f.home ? null : f.id)} />
            ))}
          </div>
          <div className="border-t border-border p-1.5">
            <button type="button" onClick={() => { setBrowse(true); setOpen(false); }}
              className="flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-sm text-ink-soft transition-colors hover:bg-hover hover:text-ink">
              <FolderOpen size={15} className="shrink-0 text-muted" />  {tr("Escolher pasta…")}
            </button>
            <Opcao icon={<FolderX size={15} />} nome={tr("Sem pasta")} sub={tr("Sem criar, editar ou baixar arquivos")}
              ativo={off} onClick={() => escolher("off")} />
          </div>
        </div>
      )}
      {browse && (
        <FolderBrowser anywhere={!!data?.browse_anywhere} onClose={() => setBrowse(false)}
          onPick={(f) => { setBrowse(false); void load(); onChange(f.home ? null : f.id); }} />
      )}
    </div>
  );
}

function Opcao({ icon, nome, sub, ativo, onClick }: { icon: React.ReactNode; nome: string; sub?: string; ativo: boolean; onClick: () => void }) {
  return (
    <button type="button" onClick={onClick}
      className={`flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left transition-colors hover:bg-hover ${ativo ? "bg-hover" : ""}`}>
      <span className="shrink-0 text-muted">{icon}</span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm text-ink">{nome}</span>
        {sub && <span className="block truncate text-[11px] text-muted" title={sub}>{sub}</span>}
      </span>
      {ativo && <Check size={14} className="shrink-0 text-accent-hover" />}
    </button>
  );
}

/** Navegador de pastas (do servidor — no app desktop, do computador da pessoa). */
export function FolderBrowser({ onPick, onClose, anywhere, start }: {
  onPick: (f: WorkspaceFolder) => void; onClose: () => void; anywhere: boolean; start?: string;
}) {
  const [d, setD] = useState<BrowseData | null>(null);
  const [erro, setErro] = useState<string | null>(null);
  const [nova, setNova] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement | null>(null);

  const ir = useCallback(async (path?: string) => {
    setErro(null);
    try {
      setD(await api.get<BrowseData>(`/workspace/browse${path ? `?path=${encodeURIComponent(path)}` : ""}`));
    } catch (e) {
      setErro(e instanceof Error ? e.message : tr("Não consegui abrir esta pasta"));
    }
  }, []);
  useEffect(() => { void ir(start); }, [ir, start]);
  useEffect(() => { if (nova !== null) input.current?.focus(); }, [nova]);
  useEffect(() => {
    const k = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", k);
    return () => document.removeEventListener("keydown", k);
  }, [onClose]);

  async function usar(path: string, create = false) {
    setBusy(true);
    setErro(null);
    try {
      onPick(await api.post<WorkspaceFolder>("/workspace/folders", { path, create }));
    } catch (e) {
      setErro(e instanceof Error ? e.message : tr("Não consegui usar esta pasta"));
    } finally {
      setBusy(false);
    }
  }
  async function criar() {
    const nome = (nova ?? "").trim();
    if (!nome || !d) return;
    const sep = d.path.includes("\\") ? "\\" : "/";
    const caminho = d.path.endsWith(sep) ? `${d.path}${nome}` : `${d.path}${sep}${nome}`;
    setBusy(true);
    try {
      await api.post<WorkspaceFolder>("/workspace/folders", { path: caminho, create: true });
      setNova(null);
      await ir(caminho);
    } catch (e) {
      setErro(e instanceof Error ? e.message : tr("Não consegui criar a pasta"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/50 p-4" onClick={onClose}>
      <div onClick={(e) => e.stopPropagation()}
        className="flex max-h-[80vh] w-full max-w-lg flex-col overflow-hidden rounded-2xl border border-border bg-bg shadow-xl">
        <div className="flex items-center gap-2 border-b border-border px-4 py-3">
          <FolderOpen size={17} className="text-accent-hover" />
          <h2 className="flex-1 text-sm font-semibold text-ink">{tr("Escolher pasta")}</h2>
          <button onClick={onClose} className="rounded-lg p-1 text-muted hover:bg-hover hover:text-ink"><X size={17} /></button>
        </div>
        <div className="flex items-center gap-1.5 border-b border-border px-3 py-2">
          <button type="button" disabled={!d?.parent} onClick={() => d?.parent && void ir(d.parent)} title={tr("Subir")}
            className="rounded-lg p-1.5 text-muted hover:bg-hover hover:text-ink disabled:opacity-40"><ArrowUp size={15} /></button>
          <button type="button" onClick={() => void ir(d?.home)} title={tr("Pasta principal")}
            className="rounded-lg p-1.5 text-muted hover:bg-hover hover:text-ink"><Home size={15} /></button>
          <span className="min-w-0 flex-1 truncate font-mono text-xs text-ink-soft" title={d?.path}>{d?.path ?? "…"}</span>
        </div>
        {anywhere && d && d.roots.length > 1 && (
          <div className="flex flex-wrap gap-1.5 border-b border-border px-3 py-2">
            {d.roots.map((r) => (
              <button key={r.path} type="button" onClick={() => void ir(r.path)}
                className="flex items-center gap-1 rounded-full border border-border px-2 py-0.5 text-[11px] text-ink-soft hover:bg-hover">
                <HardDrive size={11} /> {r.name}
              </button>
            ))}
          </div>
        )}
        <div className="min-h-[220px] flex-1 overflow-y-auto p-1.5">
          {!d && !erro ? <p className="py-8 text-center text-sm text-muted"><Loader2 size={16} className="mx-auto animate-spin" /></p>
            : d && d.dirs.length === 0 ? <p className="py-8 text-center text-xs text-muted">{tr("Pasta vazia")}</p>
            : d?.dirs.map((x) => (
              <button key={x.path} type="button" onClick={() => void ir(x.path)}
                className="flex w-full items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-left text-sm text-ink-soft transition-colors hover:bg-hover hover:text-ink">
                <Folder size={15} className="shrink-0 text-muted" />
                <span className="min-w-0 flex-1 truncate">{x.name}</span>
                <ChevronRight size={14} className="shrink-0 text-muted" />
              </button>
            ))}
          {nova !== null && (
            <div className="flex items-center gap-2 rounded-lg px-2.5 py-1.5">
              <FolderPlus size={15} className="shrink-0 text-accent-hover" />
              <input ref={input} value={nova} onChange={(e) => setNova(e.target.value)} placeholder={tr("Nome da nova pasta")}
                onKeyDown={(e) => { if (e.key === "Enter") void criar(); if (e.key === "Escape") { e.stopPropagation(); setNova(null); } }}
                className="min-w-0 flex-1 rounded-md border border-border bg-surface px-2 py-1 text-sm text-ink outline-none focus:border-accent/60" />
            </div>
          )}
        </div>
        {erro && <p className="px-4 pb-1 text-xs text-red-400">{erro}</p>}
        <div className="flex items-center gap-2 border-t border-border px-4 py-3">
          <button type="button" onClick={() => setNova((v) => (v === null ? "" : v))}
            className="flex items-center gap-1.5 rounded-lg border border-border px-2.5 py-1.5 text-xs text-ink-soft hover:bg-hover">
            <FolderPlus size={14} />  {tr("Nova pasta")}
          </button>
          <div className="flex-1" />
          <button type="button" onClick={onClose} className="rounded-lg px-3 py-1.5 text-xs text-muted hover:bg-hover hover:text-ink">{tr("Cancelar")}</button>
          <button type="button" disabled={!d || busy} onClick={() => d && void usar(d.path)}
            className="rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-white hover:bg-accent-hover disabled:opacity-50">
            {busy ? "…" : tr("Usar esta pasta")}
          </button>
        </div>
      </div>
    </div>
  );
}


/** A IA pediu para trabalhar numa pasta (tool request_folder): o usuário aprova. */
export interface FolderRequest { path: string; reason: string; create: boolean }

export function findFolderRequest(events: { kind: string; data: unknown }[]): FolderRequest | null {
  for (let i = events.length - 1; i >= 0; i--) {
    const e = events[i];
    const d = e.data as Record<string, unknown> | null;
    if (e.kind === "result" && d && typeof d === "object" && d.kind === "folder_request" && typeof d.path === "string") {
      return { path: d.path, reason: String(d.reason ?? ""), create: !!d.create };
    }
  }
  return null;
}

export function FolderRequestCard({ req, onApproved, onDecline, anywhere }: {
  req: FolderRequest; anywhere: boolean;
  onApproved: (f: WorkspaceFolder) => void; onDecline: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [erro, setErro] = useState<string | null>(null);
  const [browse, setBrowse] = useState(false);
  async function permitir() {
    setBusy(true);
    setErro(null);
    try {
      onApproved(await api.post<WorkspaceFolder>("/workspace/folders", { path: req.path, create: true }));
    } catch (e) {
      setErro(e instanceof Error ? e.message : tr("Não consegui usar esta pasta"));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="mx-auto mb-2 w-full max-w-3xl rounded-2xl border border-accent/40 bg-accent/5 px-4 py-3">
      <div className="flex items-start gap-2.5">
        <FolderOpen size={17} className="mt-0.5 shrink-0 text-accent-hover" />
        <div className="min-w-0 flex-1">
          <p className="text-sm text-ink">{tr("A IA quer trabalhar nesta pasta")}</p>
          <p className="mt-0.5 truncate font-mono text-xs text-ink-soft" title={req.path}>{req.path}</p>
          {req.reason && <p className="mt-1 text-xs text-muted">{req.reason}</p>}
          {erro && <p className="mt-1 text-xs text-red-400">{erro}</p>}
        </div>
      </div>
      <div className="mt-2.5 flex flex-wrap items-center gap-2 pl-7">
        <button type="button" disabled={busy} onClick={() => void permitir()}
          className="rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-white hover:bg-accent-hover disabled:opacity-50">
          {busy ? "…" : tr("Permitir")}
        </button>
        <button type="button" onClick={() => setBrowse(true)}
          className="rounded-lg border border-border px-3 py-1.5 text-xs text-ink-soft hover:bg-hover">{tr("Escolher outra…")}</button>
        <button type="button" onClick={onDecline} className="rounded-lg px-3 py-1.5 text-xs text-muted hover:bg-hover hover:text-ink">{tr("Agora não")}</button>
      </div>
      {browse && (
        <FolderBrowser anywhere={anywhere} onClose={() => setBrowse(false)}
          onPick={(f) => { setBrowse(false); onApproved(f); }} />
      )}
    </div>
  );
}
