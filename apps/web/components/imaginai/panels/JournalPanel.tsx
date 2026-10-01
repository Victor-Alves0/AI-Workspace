"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Check, ChevronLeft, Loader2, Pencil, Pin, RotateCw, Trash2 } from "lucide-react";
import { api } from "@/lib/api";
import type { ImaginaiEvent, ImaginaiJournalEntry } from "../types";
import { ImaginaiFeatureStatus, ImaginaiToolbar } from "../shared";
import { dateLocale, tr } from "@/lib/i18n";

export function ImaginaiJournalPanel({ campaignId }: { campaignId: string }) {
  const [section, setSection] = useState<"notes" | "history">("notes");
  const [entries, setEntries] = useState<ImaginaiJournalEntry[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [tags, setTags] = useState("");
  const [pinned, setPinned] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const selected = entries.find((entry) => entry.id === selectedId) ?? null;

  const loadEntries = useCallback(async (query = "") => {
    setLoading(true);
    setError(null);
    try {
      const result = await api.get<{ entries: ImaginaiJournalEntry[] }>(
        `/mini-apps/imaginai/campaigns/${campaignId}/journal?search=${encodeURIComponent(query)}`,
      );
      setEntries(result.entries);
      setSelectedId((current) => current && result.entries.some((entry) => entry.id === current) ? current : null);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : tr("Não foi possível abrir o diário"));
    } finally {
      setLoading(false);
    }
  }, [campaignId]);

  // busca ao digitar (com respiro), sem botão de buscar
  const firstLoad = useRef(true);
  useEffect(() => {
    const timer = window.setTimeout(() => { void loadEntries(search); }, firstLoad.current ? 0 : 250);
    firstLoad.current = false;
    return () => window.clearTimeout(timer);
  }, [loadEntries, search]);

  function beginNew() {
    setSelectedId(null);
    setTitle("");
    setContent("");
    setTags("");
    setPinned(false);
    setEditing(true);
    setError(null);
  }

  function beginEdit(entry: ImaginaiJournalEntry) {
    setSelectedId(entry.id);
    setTitle(entry.title);
    setContent(entry.content);
    setTags(entry.tags.join(", "));
    setPinned(entry.pinned);
    setEditing(true);
    setError(null);
  }

  async function saveEntry(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!title.trim()) return;
    setSaving(true);
    setError(null);
    const body = {
      title: title.trim(),
      content,
      tags: tags.split(",").map((tag) => tag.trim()).filter(Boolean),
      pinned,
    };
    try {
      const saved = selectedId
        ? await api.patch<ImaginaiJournalEntry>(`/mini-apps/imaginai/campaigns/${campaignId}/journal/${selectedId}`, body)
        : await api.post<ImaginaiJournalEntry>(`/mini-apps/imaginai/campaigns/${campaignId}/journal`, body);
      await loadEntries(search);
      setSelectedId(saved.id);
      setEditing(false);
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : tr("Não foi possível salvar a anotação"));
    } finally {
      setSaving(false);
    }
  }

  async function deleteEntry(entryId: string) {
    if (deletingId !== entryId) {
      setDeletingId(entryId);
      return;
    }
    setError(null);
    try {
      await api.del<void>(`/mini-apps/imaginai/campaigns/${campaignId}/journal/${entryId}`);
      setDeletingId(null);
      setSelectedId(null);
      setEditing(false);
      await loadEntries(search);
    } catch (deleteError) {
      setError(deleteError instanceof Error ? deleteError.message : tr("Não foi possível apagar a anotação"));
    }
  }

  if (section === "history") {
    return <ImaginaiCampaignHistory campaignId={campaignId} onShowNotes={() => setSection("notes")} />;
  }

  if (editing) {
    return (
      <form onSubmit={saveEntry} className="imaginai-feature-scroll space-y-2" aria-label={selectedId ? tr("Editar anotação") : tr("Nova anotação")}>
        <div className="flex items-center justify-between gap-2">
          <h3 className="text-sm font-semibold text-ink">{selectedId ? tr("Editar anotação") : tr("Nova anotação")}</h3>
          <button type="button" onClick={() => setEditing(false)} className="imaginai-small-button">{tr("Cancelar")}</button>
        </div>
        <input aria-label={tr("Título da anotação")} value={title} onChange={(event) => setTitle(event.target.value)} placeholder={tr("Título")} maxLength={180} required className="imaginai-field" />
        <textarea aria-label={tr("Conteúdo da anotação")} value={content} onChange={(event) => setContent(event.target.value)} placeholder={tr("Escreva suas anotações…")} rows={9} className="imaginai-field resize-y leading-5" />
        <input aria-label={tr("Tags da anotação")} value={tags} onChange={(event) => setTags(event.target.value)} placeholder={tr("Tags separadas por vírgula")} className="imaginai-field" />
        <label className="flex min-h-11 cursor-pointer items-center gap-2 rounded-lg px-1 text-xs text-ink-soft">
          <input type="checkbox" checked={pinned} onChange={(event) => setPinned(event.target.checked)} className="accent-violet-500" />
          
          {tr("Fixar no topo")}
        </label>
        {error ? <p className="text-xs text-rose-400">{error}</p> : null}
        <button type="submit" disabled={saving || !title.trim()} className="imaginai-primary-button w-full">
          {saving ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />}  {tr("Salvar anotação")}
        </button>
      </form>
    );
  }

  if (selected) {
    return (
      <div className="imaginai-feature-scroll">
        <div className="flex items-start justify-between gap-2">
          <button type="button" onClick={() => { setSelectedId(null); setDeletingId(null); }} className="imaginai-small-button"><ChevronLeft size={14} />  {tr("Lista")}</button>
          <div className="flex gap-1">
            <button type="button" onClick={() => beginEdit(selected)} className="imaginai-icon-button" title={tr("Editar anotação")} aria-label={tr("Editar anotação")}><Pencil size={14} /></button>
            <button type="button" onClick={() => void deleteEntry(selected.id)} onBlur={() => setDeletingId(null)} className={`imaginai-icon-button ${deletingId === selected.id ? "text-rose-300" : ""}`} title={deletingId === selected.id ? tr("Clique novamente para apagar") : tr("Apagar anotação")} aria-label={deletingId === selected.id ? tr("Confirmar exclusão") : tr("Apagar anotação")}><Trash2 size={14} /></button>
          </div>
        </div>
        {deletingId === selected.id ? (
          <button type="button" onMouseDown={(event) => event.preventDefault()} onClick={() => void deleteEntry(selected.id)} className="mt-2 flex min-h-11 w-full items-center justify-center gap-1.5 rounded-lg border border-rose-400/30 bg-rose-500/10 px-2 text-xs font-medium text-rose-300">
            <Trash2 size={14} />  {tr("Confirmar exclusão")}
          </button>
        ) : null}
        <div className="mt-3 flex items-center gap-2">
          {selected.pinned ? <Pin size={13} className="shrink-0 text-violet-300" /> : null}
          <h3 className="min-w-0 text-sm font-semibold text-ink">{selected.title}</h3>
        </div>
        <p className="mt-1 text-[10px] text-muted">{tr("Atualizada em")} {new Date(selected.updated_at).toLocaleDateString(dateLocale())}</p>
        <p className="mt-3 whitespace-pre-wrap break-words text-xs leading-5 text-ink-soft">{selected.content || tr("Sem conteúdo.")}</p>
        {selected.tags.length ? <div className="mt-3 flex flex-wrap gap-1">{selected.tags.map((tag) => <span key={tag} className="rounded-full bg-violet-500/10 px-2 py-1 text-[10px] text-violet-200">{tag}</span>)}</div> : null}
        {error ? <p className="mt-3 text-xs text-rose-400">{error}</p> : null}
      </div>
    );
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="shrink-0"><JournalTabs active="notes" onChange={setSection} /></div>
      <div className="mt-2"><ImaginaiToolbar value={search} onChange={setSearch} placeholder={tr("Buscar anotações")} onAdd={beginNew} addLabel={tr("Nova anotação")} /></div>
      <div className="imaginai-feature-scroll mt-2">
        {loading && !entries.length ? <ImaginaiFeatureStatus><Loader2 size={17} className="animate-spin" /></ImaginaiFeatureStatus> : error ? <ImaginaiFeatureStatus error>{error}</ImaginaiFeatureStatus> : entries.length === 0 ? <ImaginaiFeatureStatus>{search ? tr("Nada encontrado.") : tr("Nenhuma anotação ainda.")}</ImaginaiFeatureStatus> : (
          <div className="space-y-0.5">
            {entries.map((entry) => (
              <button key={entry.id} type="button" onClick={() => setSelectedId(entry.id)} className="imaginai-list-row">
                <span className="min-w-0 flex-1">
                  <span className="flex items-center gap-1.5 text-xs font-medium text-ink">{entry.pinned ? <Pin size={12} className="shrink-0 text-violet-300" /> : null}<span className="truncate">{entry.title}</span></span>
                  <span className="mt-0.5 block truncate text-[10px] text-muted">{entry.content || tr("Sem conteúdo")}</span>
                </span>
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function JournalTabs({ active, onChange }: { active: "notes" | "history"; onChange: (section: "notes" | "history") => void }) {
  return (
    <div className="imaginai-chips" role="tablist" aria-label={tr("Diário")}>
      <button type="button" role="tab" aria-selected={active === "notes"} onClick={() => onChange("notes")} className="imaginai-chip">{tr("Anotações")}</button>
      <button type="button" role="tab" aria-selected={active === "history"} onClick={() => onChange("history")} className="imaginai-chip">{tr("Histórico")}</button>
    </div>
  );
}

export function eventString(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

export function campaignEventCopy(event: ImaginaiEvent): { title: string; detail: string } {
  const payload = event.payload ?? {};
  const itemName = eventString(payload.item_name);
  const summary = eventString(payload.summary);
  const attackName = eventString(payload.attack_name) ?? eventString(payload.spell_name);
  const result = eventString(payload.result);
  const success = payload.success === true;
  const hasRoll = Array.isArray(payload.rolls);

  if (hasRoll && event.event_type !== "attack_resolved" && event.event_type !== "spell_attack_resolved") {
    const total = typeof payload.total === "number" ? ` · total ${payload.total}` : "";
    return {
      title: result === "success" || success ? "Teste bem-sucedido" : tr("Teste falhou"),
      detail: `${eventString(payload.skill) ?? eventString(payload.ability) ?? "Teste"}${total}`,
    };
  }

  switch (event.event_type) {
    case "item_taken": return { title: tr("Item recolhido"), detail: itemName ? tr("{itemName} entrou no inventário.", { itemName: itemName }) : tr("Um item foi recolhido.") };
    case "item_dropped": return { title: tr("Item deixado"), detail: itemName ? tr("{itemName} foi deixado no local.", { itemName: itemName }) : tr("Um item foi deixado no local.") };
    case "item_equipped": return { title: tr("Item equipado"), detail: itemName ? tr("{itemName} foi equipado.", { itemName: itemName }) : tr("Um item foi equipado.") };
    case "item_unequipped": return { title: tr("Item guardado"), detail: itemName ? tr("{itemName} deixou de estar equipado.", { itemName: itemName }) : tr("Um item deixou de estar equipado.") };
    case "item_used": return { title: tr("Item usado"), detail: itemName ? tr("{itemName} foi usado.", { itemName: itemName }) : tr("Um item foi usado.") };
    case "attack_resolved":
    case "spell_attack_resolved": {
      const damage = typeof payload.damage === "number" ? tr(" · {damage} de dano", { damage: payload.damage }) : "";
      return { title: success ? tr("Ataque acertou") : tr("Ataque falhou"), detail: `${attackName ?? "Ataque"}${damage}` };
    }
    case "ability_check_resolved": {
      const total = typeof payload.total === "number" ? ` · total ${payload.total}` : "";
      return { title: result === "success" ? "Teste bem-sucedido" : tr("Teste falhou"), detail: `${eventString(payload.skill) ?? eventString(payload.ability) ?? "Teste"}${total}` };
    }
    case "narrative_outcome": return { title: tr("Consequência registrada"), detail: summary ?? tr("A cena avançou.") };
    default: return { title: tr("Acontecimento"), detail: summary ?? tr("Uma ação foi registrada na campanha.") };
  }
}

export function ImaginaiCampaignHistory({ campaignId, onShowNotes }: { campaignId: string; onShowNotes: () => void }) {
  const [events, setEvents] = useState<ImaginaiEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadEvents = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await api.get<{ events: ImaginaiEvent[] }>(`/mini-apps/imaginai/campaigns/${campaignId}/events?limit=100`);
      setEvents(result.events);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : tr("Não foi possível abrir o histórico"));
    } finally {
      setLoading(false);
    }
  }, [campaignId]);

  useEffect(() => { void loadEvents(); }, [loadEvents]);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex shrink-0 items-center justify-between gap-2">
        <JournalTabs active="history" onChange={(section) => { if (section === "notes") onShowNotes(); }} />
        <button type="button" onClick={() => void loadEvents()} disabled={loading} className="flex h-7 w-7 items-center justify-center rounded-lg text-muted transition-colors hover:bg-hover hover:text-ink" title={tr("Atualizar histórico")} aria-label={tr("Atualizar histórico")}><RotateCw size={13} className={loading ? "animate-spin" : ""} /></button>
      </div>
      <div className="imaginai-feature-scroll mt-2">
      {loading && !events.length ? <ImaginaiFeatureStatus><Loader2 size={17} className="animate-spin" /></ImaginaiFeatureStatus> : error ? <ImaginaiFeatureStatus error>{error}</ImaginaiFeatureStatus> : events.length === 0 ? <ImaginaiFeatureStatus>{tr("Nenhum acontecimento ainda.")}</ImaginaiFeatureStatus> : (
        <ol className="ml-1 space-y-2 border-l border-violet-400/25 pl-3">
          {[...events].reverse().map((event) => {
            const copy = campaignEventCopy(event);
            const at = new Date(event.created_at);
            const when = Number.isNaN(at.getTime()) ? "" : at.toLocaleString(dateLocale(), { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
            return <li key={event.id} className="relative rounded-xl border border-border bg-surface2/55 p-2.5"><span aria-hidden className="absolute -left-[1.06rem] top-3 h-2 w-2 rounded-full border border-violet-200/60 bg-violet-400" /><div className="flex items-start justify-between gap-2"><h4 className="text-xs font-medium text-ink">{copy.title}</h4><span className="shrink-0 text-[9px] text-muted">{tr("Turno")} {event.world_tick}</span></div><p className="mt-1 text-[10px] leading-4 text-ink-soft">{copy.detail}</p>{when ? <p className="mt-1.5 text-[9px] text-muted">{when}</p> : null}</li>;
          })}
        </ol>
      )}
      </div>
    </div>
  );
}
