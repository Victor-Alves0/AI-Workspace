"use client";

import { useCallback, useEffect, useState } from "react";
import {
  Ban, Box, Boxes, Check, CheckSquare, Clock, FolderOpen, Globe, Loader2, MessagesSquare, Pencil, Plus,
  RotateCcw, Search, Square, Trash2, X,
} from "lucide-react";
import { api } from "@/lib/api";
import type { MemoryBank, MemoryConfig, MemoryItem, MemoryScopes } from "@/lib/types";
import { useConfirm } from "@/components/ConfirmDialog";

type Tab = "global" | "model" | "chat" | "project" | "bank";

/** Controlador de Memória (mem0): configurações + visão por escopo (Global / Por
 *  modelo / Por chat) com editar, adicionar e excluir. Fica em Espaço → Memória. */
export default function MemoryView() {
  const confirm = useConfirm();
  const [settings, setSettings] = useState<MemoryConfig | null>(null);
  const [scopes, setScopes] = useState<MemoryScopes | null>(null);
  const [tab, setTab] = useState<Tab>("global");
  const [selModel, setSelModel] = useState<string>("");
  const [selChat, setSelChat] = useState<string>("");
  const [selProject, setSelProject] = useState<string>("");
  const [banks, setBanks] = useState<MemoryBank[]>([]);
  const [selBank, setSelBank] = useState<string>("");
  const [newBank, setNewBank] = useState<{ name: string; description: string } | null>(null);
  const [items, setItems] = useState<MemoryItem[] | null>(null);
  const [q, setQ] = useState("");
  const [editing, setEditing] = useState<string | null>(null);
  const [editText, setEditText] = useState("");
  const [adding, setAdding] = useState(false);
  const [newText, setNewText] = useState("");
  const [busy, setBusy] = useState(false);
  const [sel, setSel] = useState<Set<string>>(new Set());
  const [pending, setPending] = useState<MemoryItem[]>([]);

  const loadBanks = useCallback(async () => {
    try { setBanks(await api.get<MemoryBank[]>("/memory/banks")); } catch {}
  }, []);

  const loadScopes = useCallback(async () => {
    try { setScopes(await api.get<MemoryScopes>("/memory/scopes")); } catch {}
    try { setPending(await api.get<MemoryItem[]>("/memory/pending")); } catch {}
    loadBanks();
  }, [loadBanks]);

  useEffect(() => {
    api.get<MemoryConfig>("/memory/settings").then(setSettings).catch(() => {});
    loadScopes();
  }, [loadScopes]);

  // seleciona automaticamente o 1º modelo/chat/banco da aba, se nenhum escolhido
  useEffect(() => {
    if (tab === "model" && !selModel && scopes?.models.length) setSelModel(scopes.models[0].id);
    if (tab === "chat" && !selChat && scopes?.chats.length) setSelChat(scopes.chats[0].id);
    if (tab === "project" && !selProject && scopes?.projects.length) setSelProject(scopes.projects[0].id);
    if (tab === "bank" && !selBank && banks.length) setSelBank(banks[0].id);
  }, [tab, scopes, banks, selModel, selChat, selProject, selBank]);

  const activeModelId = tab === "model" ? selModel : "";
  const activeChatId = tab === "chat" ? selChat : "";
  const activeProjectId = tab === "project" ? selProject : "";
  const activeBankId = tab === "bank" ? selBank : "";

  const loadItems = useCallback(async () => {
    if (tab === "model" && !activeModelId) { setItems([]); return; }
    if (tab === "chat" && !activeChatId) { setItems([]); return; }
    if (tab === "project" && !activeProjectId) { setItems([]); return; }
    if (tab === "bank" && !activeBankId) { setItems([]); return; }
    setItems(null);
    const p = new URLSearchParams({ scope: tab });
    if (activeModelId) p.set("model_id", activeModelId);
    if (activeChatId) p.set("chat_id", activeChatId);
    if (activeProjectId) p.set("project_id", activeProjectId);
    if (activeBankId) p.set("bank_id", activeBankId);
    if (q.trim()) p.set("q", q.trim());
    setSel(new Set());
    try { setItems(await api.get<MemoryItem[]>(`/memory?${p}`)); } catch { setItems([]); }
  }, [tab, activeModelId, activeChatId, activeProjectId, activeBankId, q]);

  useEffect(() => { loadItems(); }, [loadItems]);

  async function saveEdit(id: string) {
    if (!editText.trim()) return;
    setBusy(true);
    try {
      await api.put(`/memory/${id}`, { text: editText.trim() });
      setEditing(null);
    } catch {
      // o servidor pode recusar (memória inexistente/de outro dono, mem0 fora do ar).
      // Recarrega p/ mostrar o estado REAL em vez de morrer numa promise rejeitada.
      setEditing(null);
    } finally {
      await loadItems();
      setBusy(false);
    }
  }

  async function remove(m: MemoryItem) {
    const ok = await confirm({
      title: "Excluir memória?",
      body: <span className="text-muted">“{m.text.slice(0, 120)}”</span>,
      confirmLabel: "Excluir", danger: true,
    });
    if (!ok) return;
    // idem saveEdit: uma recusa do servidor não pode virar promise rejeitada silenciosa
    try { await api.del(`/memory/${m.id}`); } catch { /* recarrega abaixo */ }
    await Promise.all([loadItems(), loadScopes()]);
  }

  async function add() {
    if (!newText.trim()) return;
    setBusy(true);
    try {
      await api.post("/memory", {
        text: newText.trim(), scope: tab,
        model_id: activeModelId || undefined, chat_id: activeChatId || undefined,
        project_id: activeProjectId || undefined, bank_id: activeBankId || undefined,
      });
      setNewText(""); setAdding(false);
      await Promise.all([loadItems(), loadScopes()]);
    } finally { setBusy(false); }
  }

  async function createBank() {
    if (!newBank?.name.trim()) return;
    setBusy(true);
    try {
      const b = await api.post<MemoryBank>("/memory/banks", {
        name: newBank.name.trim(), description: newBank.description.trim(),
      });
      setNewBank(null);
      await loadBanks();
      setTab("bank");
      setSelBank(b.id);
    } finally { setBusy(false); }
  }

  async function deleteBank(b: MemoryBank) {
    const ok = await confirm({
      title: `Excluir o banco "${b.name}"?`,
      body: <span className="text-muted">Isso apaga o banco e suas {b.count} memória(s). Modelos acoplados param de compartilhá-lo. Não dá para desfazer.</span>,
      confirmLabel: "Excluir", danger: true,
    });
    if (!ok) return;
    await api.del(`/memory/banks/${b.id}`);
    if (selBank === b.id) setSelBank("");
    await Promise.all([loadBanks(), loadScopes(), loadItems()]);
  }

  const toggleSel = (id: string) =>
    setSel((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });
  const allSelected = !!items?.length && items.every((m) => sel.has(m.id));
  const toggleAll = () =>
    setSel(allSelected ? new Set() : new Set((items ?? []).map((m) => m.id)));

  async function apply(action: "delete" | "disable" | "enable", ids: string[]) {
    if (!ids.length) return;
    setBusy(true);
    try {
      await api.post("/memory/bulk", { action, ids });
      await Promise.all([loadItems(), loadScopes()]);
    } finally { setBusy(false); }
  }

  async function bulk(action: "delete" | "disable" | "enable") {
    const ids = [...sel];
    if (!ids.length) return;
    if (action === "delete") {
      const ok = await confirm({
        title: "Excluir memórias?",
        body: <>Isso exclui <span className="font-medium text-ink">{ids.length}</span> {ids.length === 1 ? "memória" : "memórias"}. Não dá para desfazer.</>,
        confirmLabel: "Excluir", danger: true,
      });
      if (!ok) return;
    }
    await apply(action, ids);
  }

  const enabled = settings?.enabled !== false;

  return (
    <div className="space-y-5">
      {!enabled && (
        <p className="rounded-2xl border border-border bg-surface p-4 text-xs text-muted">
          A memória está <span className="text-ink-soft">desativada</span>. Ative e ajuste os padrões em{" "}
          <span className="text-ink-soft">Configurações → Controle de Dados → Memória</span> (engrenagem).
        </p>
      )}

      {/* Fila de revisão (pendentes) */}
      {pending.length > 0 && (
        <div className="rounded-2xl border border-amber-500/40 bg-amber-500/5 p-4">
          <div className="mb-2 flex items-center justify-between">
            <p className="flex items-center gap-1.5 text-sm font-semibold text-ink">
              <Clock size={15} className="text-amber-500" /> Pendentes de revisão ({pending.length})
            </p>
            <div className="flex items-center gap-1.5">
              <button onClick={() => apply("enable", pending.map((m) => m.id))} disabled={busy} className="rounded-full border border-border px-3 py-1 text-xs text-ink-soft transition-colors hover:bg-hover disabled:opacity-50">Aprovar todas</button>
              <button onClick={() => apply("delete", pending.map((m) => m.id))} disabled={busy} className="rounded-full border border-border px-3 py-1 text-xs text-muted transition-colors hover:border-red-400/40 hover:text-red-400 disabled:opacity-50">Recusar todas</button>
            </div>
          </div>
          <div className="space-y-1.5">
            {pending.map((m) => (
              <div key={m.id} className="flex items-start gap-2 rounded-lg border border-border/70 bg-bg px-3 py-2 text-sm">
                <span className="min-w-0 flex-1 text-ink-soft">{m.text}</span>
                <div className="flex shrink-0 items-center gap-1">
                  <button onClick={() => apply("enable", [m.id])} title="Aprovar" className="rounded-lg p-1.5 text-muted transition-colors hover:bg-hover hover:text-green-400"><Check size={14} /></button>
                  <button onClick={() => apply("delete", [m.id])} title="Recusar" className="rounded-lg p-1.5 text-muted transition-colors hover:bg-hover hover:text-red-400"><X size={14} /></button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Segmentado de escopo */}
      <div className="flex flex-wrap items-center gap-2">
        {([["global", "Global", <Globe key="g" size={15} />], ["model", "Por modelo", <Box key="m" size={15} />], ["chat", "Por chat", <MessagesSquare key="c" size={15} />], ["project", "Por projeto", <FolderOpen key="p" size={15} />], ["bank", "Bancos", <Boxes key="b" size={15} />]] as const).map(
          ([k, label, icon]) => (
            <button
              key={k}
              onClick={() => { setTab(k); setAdding(false); setEditing(null); }}
              className={`flex items-center gap-1.5 rounded-full border px-3.5 py-1.5 text-sm transition-colors ${tab === k ? "border-accent/40 bg-accent/15 text-accent-hover" : "border-border bg-surface text-muted hover:text-ink"}`}
            >
              {icon} {label}
            </button>
          ),
        )}
        {tab === "model" && (
          <select
            value={selModel}
            onChange={(e) => setSelModel(e.target.value)}
            className="rounded-full border border-border bg-surface px-3 py-1.5 text-sm text-ink outline-none focus:border-accent"
          >
            {scopes?.models.length ? scopes.models.map((m) => (
              <option key={m.id} value={m.id}>{m.name} ({m.count})</option>
            )) : <option value="">Nenhum modelo com memória</option>}
          </select>
        )}
        {tab === "chat" && (
          <select
            value={selChat}
            onChange={(e) => setSelChat(e.target.value)}
            className="max-w-[240px] rounded-full border border-border bg-surface px-3 py-1.5 text-sm text-ink outline-none focus:border-accent"
          >
            {scopes?.chats.length ? scopes.chats.map((c) => (
              <option key={c.id} value={c.id}>{c.title} ({c.count})</option>
            )) : <option value="">Nenhum chat com memória</option>}
          </select>
        )}
        {tab === "project" && (
          <select
            value={selProject}
            onChange={(e) => setSelProject(e.target.value)}
            className="max-w-[240px] rounded-full border border-border bg-surface px-3 py-1.5 text-sm text-ink outline-none focus:border-accent"
          >
            {scopes?.projects.length ? scopes.projects.map((p) => (
              <option key={p.id} value={p.id}>{p.name} ({p.count})</option>
            )) : <option value="">Nenhum projeto com memória</option>}
          </select>
        )}
        {tab === "bank" && (
          <>
            {banks.length > 0 && (
              <select
                value={selBank}
                onChange={(e) => setSelBank(e.target.value)}
                className="max-w-[240px] rounded-full border border-border bg-surface px-3 py-1.5 text-sm text-ink outline-none focus:border-accent"
              >
                {banks.map((b) => <option key={b.id} value={b.id}>{b.name} ({b.count})</option>)}
              </select>
            )}
            <button
              onClick={() => setNewBank({ name: "", description: "" })}
              className="flex items-center gap-1.5 rounded-full border border-border bg-surface px-3 py-1.5 text-sm text-ink-soft transition-colors hover:bg-hover hover:text-ink"
            >
              <Plus size={14} /> Novo banco
            </button>
            {selBank && banks.find((b) => b.id === selBank) && (
              <button
                onClick={() => deleteBank(banks.find((b) => b.id === selBank)!)}
                className="flex items-center gap-1.5 rounded-full border border-border bg-surface px-3 py-1.5 text-sm text-muted transition-colors hover:border-red-400/40 hover:text-red-400"
              >
                <Trash2 size={14} /> Excluir banco
              </button>
            )}
          </>
        )}
      </div>

      {/* Explicação da aba Bancos + form de criação */}
      {tab === "bank" && (
        <div className="rounded-xl border border-border bg-surface px-3 py-2.5 text-xs text-muted">
          Bancos são coleções de memória <span className="text-ink-soft">compartilháveis entre modelos</span>. Acople o mesmo banco a vários modelos (no editor do modelo, seção Memória) e eles passam a ler/escrever nele — sem depender do escopo global.
        </div>
      )}
      {newBank && (
        <div className="space-y-2 rounded-xl border border-accent/40 bg-surface p-3">
          <input
            autoFocus
            value={newBank.name}
            onChange={(e) => setNewBank({ ...newBank, name: e.target.value })}
            placeholder="Nome do banco (ex.: Projeto X, Pessoal)"
            className="w-full rounded-lg border border-border bg-surface2 px-3 py-2 text-sm text-ink outline-none focus:border-accent placeholder:text-muted"
          />
          <input
            value={newBank.description}
            onChange={(e) => setNewBank({ ...newBank, description: e.target.value })}
            placeholder="Descrição (opcional)"
            className="w-full rounded-lg border border-border bg-surface2 px-3 py-2 text-sm text-ink outline-none focus:border-accent placeholder:text-muted"
          />
          <div className="flex justify-end gap-2">
            <button onClick={() => setNewBank(null)} className="rounded-full px-3 py-1.5 text-sm text-muted hover:text-ink">Cancelar</button>
            <button onClick={createBank} disabled={busy || !newBank.name.trim()} className="flex items-center gap-1.5 rounded-full bg-accent px-4 py-1.5 text-sm font-medium text-white hover:bg-accent-hover disabled:opacity-50">
              {busy ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />} Criar banco
            </button>
          </div>
        </div>
      )}

      {/* Busca + ações */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex flex-1 items-center gap-2 rounded-xl border border-border bg-surface px-3 py-2">
          <Search size={15} className="text-muted" />
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Buscar nas memórias…"
            className="w-full bg-transparent text-sm text-ink outline-none placeholder:text-muted"
          />
        </div>
        <button
          onClick={() => { setAdding((v) => !v); setNewText(""); }}
          disabled={(tab === "bank" && !activeBankId) || (tab === "project" && !activeProjectId)}
          className="flex items-center gap-1.5 rounded-full bg-accent px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-accent-hover disabled:opacity-50"
        >
          <Plus size={15} /> Adicionar
        </button>
      </div>

      {/* barra de seleção em lote */}
      {items && items.length > 0 && (
        <div className="flex flex-wrap items-center gap-2 rounded-xl border border-border bg-surface px-3 py-2">
          <button onClick={toggleAll} className="flex items-center gap-1.5 text-sm text-muted transition-colors hover:text-ink">
            {allSelected ? <CheckSquare size={16} className="text-accent-hover" /> : <Square size={16} />}
            {sel.size > 0 ? `${sel.size} selecionada${sel.size === 1 ? "" : "s"}` : "Selecionar tudo"}
          </button>
          {sel.size > 0 && (
            <div className="ml-auto flex items-center gap-1.5">
              <button onClick={() => bulk("enable")} disabled={busy} className="flex items-center gap-1.5 rounded-full border border-border px-3 py-1.5 text-sm text-ink-soft transition-colors hover:bg-hover disabled:opacity-50">
                <RotateCcw size={14} /> Ativar
              </button>
              <button onClick={() => bulk("disable")} disabled={busy} className="flex items-center gap-1.5 rounded-full border border-border px-3 py-1.5 text-sm text-ink-soft transition-colors hover:bg-hover disabled:opacity-50">
                <Ban size={14} /> Desativar
              </button>
              <button onClick={() => bulk("delete")} disabled={busy} className="flex items-center gap-1.5 rounded-full border border-border px-3 py-1.5 text-sm text-muted transition-colors hover:border-red-400/40 hover:text-red-400 disabled:opacity-50">
                <Trash2 size={14} /> Excluir
              </button>
            </div>
          )}
        </div>
      )}

      {/* form de adicionar */}
      {adding && (
        <div className="rounded-xl border border-accent/40 bg-surface p-3">
          <textarea
            autoFocus
            value={newText}
            onChange={(e) => setNewText(e.target.value)}
            rows={2}
            placeholder="Escreva um fato para a IA lembrar…"
            className="w-full resize-y rounded-lg border border-border bg-surface2 px-3 py-2 text-sm text-ink outline-none focus:border-accent placeholder:text-muted"
          />
          <div className="mt-2 flex justify-end gap-2">
            <button onClick={() => setAdding(false)} className="rounded-full px-3 py-1.5 text-sm text-muted hover:text-ink">Cancelar</button>
            <button onClick={add} disabled={busy || !newText.trim()} className="flex items-center gap-1.5 rounded-full bg-accent px-4 py-1.5 text-sm font-medium text-white hover:bg-accent-hover disabled:opacity-50">
              {busy ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />} Salvar
            </button>
          </div>
        </div>
      )}

      {/* lista */}
      {items === null ? (
        <div className="flex justify-center py-16"><Loader2 size={20} className="animate-spin text-muted" /></div>
      ) : items.length === 0 ? (
        <p className="rounded-2xl border border-dashed border-border px-4 py-14 text-center text-sm text-muted">
          {tab === "model" && !activeModelId ? "Selecione um modelo."
            : tab === "chat" && !activeChatId ? "Selecione um chat."
            : tab === "project" && !activeProjectId ? "Nenhum projeto (pasta) com memória ainda."
            : tab === "bank" && !activeBankId ? "Crie um banco para começar."
            : "Nenhuma memória neste escopo ainda."}
        </p>
      ) : (
        <div className="space-y-2">
          {items.map((m) => {
            const picked = sel.has(m.id);
            return (
            <div key={m.id} className={`group rounded-xl border p-3 transition-colors ${picked ? "border-accent/50 bg-accent/5" : "border-border bg-surface"}`}>
              {editing === m.id ? (
                <div>
                  <textarea
                    autoFocus
                    value={editText}
                    onChange={(e) => setEditText(e.target.value)}
                    rows={2}
                    className="w-full resize-y rounded-lg border border-border bg-surface2 px-3 py-2 text-sm text-ink outline-none focus:border-accent"
                  />
                  <div className="mt-2 flex justify-end gap-2">
                    <button onClick={() => setEditing(null)} className="rounded-full px-3 py-1 text-sm text-muted hover:text-ink"><X size={14} /></button>
                    <button onClick={() => saveEdit(m.id)} disabled={busy} className="flex items-center gap-1.5 rounded-full bg-accent px-3 py-1 text-sm font-medium text-white hover:bg-accent-hover disabled:opacity-50">
                      {busy ? <Loader2 size={13} className="animate-spin" /> : <Check size={13} />} Salvar
                    </button>
                  </div>
                </div>
              ) : (
                <div className="flex items-start gap-3">
                  <button onClick={() => toggleSel(m.id)} className="mt-0.5 shrink-0 text-muted transition-colors hover:text-ink" title="Selecionar">
                    {picked ? <CheckSquare size={16} className="text-accent-hover" /> : <Square size={16} />}
                  </button>
                  <div className="min-w-0 flex-1">
                    <p className={`text-sm leading-5 ${m.disabled ? "text-muted line-through decoration-muted/50" : "text-ink"}`}>{m.text}</p>
                    <div className="mt-1 flex flex-wrap items-center gap-2">
                      {m.disabled && (
                        <span className="rounded-full bg-surface2 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-muted">Desativada</span>
                      )}
                      {(m.model_name || m.chat_title || m.project_name) && (
                        <span className="text-[11px] text-muted">{m.model_name ?? m.chat_title ?? m.project_name}</span>
                      )}
                    </div>
                  </div>
                  <div className="flex shrink-0 items-center gap-1 opacity-0 transition-opacity group-hover:opacity-100">
                    <button onClick={() => apply(m.disabled ? "enable" : "disable", [m.id])} title={m.disabled ? "Ativar" : "Desativar"} className="rounded-lg p-1.5 text-muted transition-colors hover:bg-hover hover:text-ink">
                      {m.disabled ? <RotateCcw size={14} /> : <Ban size={14} />}
                    </button>
                    <button onClick={() => { setEditing(m.id); setEditText(m.text); }} title="Editar" className="rounded-lg p-1.5 text-muted transition-colors hover:bg-hover hover:text-ink">
                      <Pencil size={14} />
                    </button>
                    <button onClick={() => remove(m)} title="Excluir" className="rounded-lg p-1.5 text-muted transition-colors hover:bg-hover hover:text-red-400">
                      <Trash2 size={14} />
                    </button>
                  </div>
                </div>
              )}
            </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
