"use client";

import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { Check, ChevronLeft, ChevronRight, Loader2, Pencil, Sparkles, Trash2, X } from "lucide-react";
import { api } from "@/lib/api";
import type { ImaginaiSpell, ImaginaiSpells } from "../types";
import { ImaginaiFeatureStatus, ImaginaiToolbar } from "../shared";

export function spellComponents(value: unknown): string {
  if (Array.isArray(value)) return value.map((item) => String(item)).filter(Boolean).join(", ");
  return typeof value === "string" ? value : "";
}

function levelLabel(level: number): string {
  return level === 0 ? "Truque" : `${level}º nível`;
}

export function ImaginaiSpellsPanel({ campaignId }: { campaignId: string }) {
  const [data, setData] = useState<ImaginaiSpells | null>(null);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [editing, setEditing] = useState<ImaginaiSpell | "new" | null>(null);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    api.get<ImaginaiSpells>(`/mini-apps/imaginai/campaigns/${campaignId}/spells`)
      .then((value) => { if (!cancelled) setData(value); })
      .catch((loadError: unknown) => { if (!cancelled) setError(loadError instanceof Error ? loadError.message : "Não foi possível abrir as magias"); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [campaignId]);

  async function saveAll(spells: Partial<ImaginaiSpell>[]) {
    const updated = await api.put<ImaginaiSpells>(`/mini-apps/imaginai/campaigns/${campaignId}/character/spells`, { spells });
    setData(updated);
    return updated;
  }

  if (loading) return <ImaginaiFeatureStatus><Loader2 size={17} className="animate-spin" /></ImaginaiFeatureStatus>;
  if (error || !data) return <ImaginaiFeatureStatus error>{error ?? "Magias indisponíveis"}</ImaginaiFeatureStatus>;

  const editor = editing ? (
    <SpellEditor
      spell={editing === "new" ? null : editing}
      onClose={() => setEditing(null)}
      onSave={async (spell) => {
        const original = editing === "new" ? null : editing.key;
        const rest = data.spells.filter((item) => item.key !== original);
        const updated = await saveAll([...rest, spell]);
        const saved = updated.spells.find((item) => item.name.toLocaleLowerCase("pt-BR") === (spell.name ?? "").toLocaleLowerCase("pt-BR"));
        setSelectedKey(saved?.key ?? null);
        setEditing(null);
      }}
    />
  ) : null;

  const selected = data.spells.find((spell) => spell.key === selectedKey) ?? null;
  if (selected) return (
    <div className="imaginai-feature-scroll">
      <div className="flex items-center justify-between">
        <button type="button" onClick={() => setSelectedKey(null)} className="imaginai-small-button -ml-1.5"><ChevronLeft size={14} /> Grimório</button>
        <div className="flex gap-0.5">
          <button type="button" onClick={() => setEditing(selected)} title="Editar magia" aria-label="Editar magia" className="flex h-7 w-7 items-center justify-center rounded-lg text-muted transition-colors hover:bg-hover hover:text-ink"><Pencil size={13} /></button>
          <button type="button" onClick={() => { if (window.confirm(`Remover ${selected.name} da ficha?`)) void saveAll(data.spells.filter((item) => item.key !== selected.key)).then(() => setSelectedKey(null)); }} title="Remover magia" aria-label="Remover magia" className="flex h-7 w-7 items-center justify-center rounded-lg text-muted transition-colors hover:bg-rose-500/10 hover:text-rose-300"><Trash2 size={13} /></button>
        </div>
      </div>
      <div className="mt-2 flex items-start justify-between gap-2"><div className="min-w-0"><p className="text-[10px] uppercase tracking-wider text-violet-300">{levelLabel(selected.level)}{selected.school ? ` · ${selected.school}` : ""}</p><h3 className="mt-0.5 text-sm font-semibold text-ink">{selected.name}</h3></div><span className={`shrink-0 rounded-full px-2 py-1 text-[9px] ${selected.prepared ? "bg-emerald-400/10 text-emerald-300" : "bg-amber-400/10 text-amber-300"}`}>{selected.prepared ? "Preparada" : "Não preparada"}</span></div>
      <div className="mt-3 grid grid-cols-2 gap-1.5 text-[10px]">{[["Conjuração", selected.casting_time], ["Alcance", selected.range], ["Duração", selected.duration], ["Componentes", spellComponents(selected.components)]].map(([label, value]) => <div key={label} className="rounded-lg border border-border bg-surface2/55 px-2 py-1.5"><span className="block text-[8px] uppercase tracking-wide text-muted">{label}</span><span className="mt-0.5 block truncate text-ink-soft" title={value}>{value || "—"}</span></div>)}</div>
      <div className="mt-2 flex flex-wrap gap-1">
        {selected.damage ? <span className="rounded-full bg-rose-400/10 px-2 py-1 text-[9px] text-rose-200">{selected.damage}{selected.damage_type ? ` ${selected.damage_type}` : ""}{selected.attack ? ` · ataque (+${data.attack_modifier})` : ""}</span> : null}
        {selected.save ? <span className="rounded-full bg-amber-400/10 px-2 py-1 text-[9px] text-amber-200">Salvaguarda {selected.save}{data.save_dc ? ` · CD ${data.save_dc}` : ""}</span> : null}
        {selected.healing ? <span className="rounded-full bg-emerald-400/10 px-2 py-1 text-[9px] text-emerald-200">Cura {selected.healing}</span> : null}
        {selected.concentration ? <span className="rounded-full bg-violet-500/15 px-2 py-1 text-[9px] text-violet-200">Concentração</span> : null}
        {selected.ritual ? <span className="rounded-full bg-sky-400/10 px-2 py-1 text-[9px] text-sky-200">Ritual</span> : null}
      </div>
      <p className="mt-3 whitespace-pre-wrap break-words text-xs leading-5 text-ink-soft">{selected.description || "Sem descrição."}</p>
      {editor}
    </div>
  );

  const filtered = data.spells.filter((spell) => spell.name.toLocaleLowerCase("pt-BR").includes(query.trim().toLocaleLowerCase("pt-BR")));
  const groups = new Map<number, ImaginaiSpell[]>();
  for (const spell of filtered) groups.set(spell.level, [...(groups.get(spell.level) ?? []), spell]);
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <ImaginaiToolbar value={query} onChange={setQuery} placeholder="Buscar magia" onAdd={() => setEditing("new")} addLabel="Nova magia" />
      {data.save_dc > 0 || data.attack_modifier ? (
        <div className="mt-2 flex shrink-0 items-center gap-1.5">
          {data.save_dc > 0 ? <span className="rounded-lg border border-border px-2 py-1 text-[10px] text-ink-soft">CD {data.save_dc}</span> : null}
          {data.attack_modifier ? <span className="rounded-lg border border-border px-2 py-1 text-[10px] text-ink-soft">Ataque +{data.attack_modifier}</span> : null}
        </div>
      ) : null}
      {Object.keys(data.slots).length ? <div className="mt-2 grid shrink-0 grid-cols-4 gap-1">{Object.entries(data.slots).map(([level, slot]) => <div key={level} className="min-w-0 rounded-lg border border-border px-1.5 py-1.5 text-center"><span className="block text-[8px] uppercase tracking-wide text-muted">{level}º nível</span><span className="mt-0.5 block font-mono text-[11px] text-ink">{slot.current}/{slot.max}</span></div>)}</div> : null}
      <div className="imaginai-feature-scroll mt-2">
        {filtered.length === 0 ? <ImaginaiFeatureStatus>Nenhuma magia.</ImaginaiFeatureStatus> : <div className="space-y-3">{[...groups.entries()].map(([level, spells]) => <section key={level}><p className="mb-0.5 px-1.5 text-[9px] font-semibold uppercase tracking-[0.12em] text-violet-300">{level === 0 ? "Truques" : `${level}º nível`}</p><div className="space-y-0.5">{spells.map((spell) => <button key={spell.key} type="button" onClick={() => setSelectedKey(spell.key)} className="imaginai-list-row"><span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-violet-500/10 text-violet-300"><Sparkles size={13} /></span><span className="min-w-0 flex-1"><span className="block truncate text-xs font-medium text-ink">{spell.name}</span><span className="block truncate text-[10px] text-muted">{[spell.school, spell.damage, spell.concentration ? "concentração" : ""].filter(Boolean).join(" · ") || "Magia"}</span></span>{!spell.prepared ? <span title="Não preparada" className="h-2 w-2 shrink-0 rounded-full bg-amber-300" /> : null}<ChevronRight size={14} className="shrink-0 text-muted" /></button>)}</div></section>)}</div>}
      </div>
      {editor}
    </div>
  );
}

function SpellEditor({ spell, onSave, onClose }: {
  spell: ImaginaiSpell | null;
  onSave: (spell: Partial<ImaginaiSpell>) => Promise<void>;
  onClose: () => void;
}) {
  const [draft, setDraft] = useState({
    name: spell?.name ?? "",
    level: String(spell?.level ?? 0),
    school: spell?.school ?? "",
    casting_time: spell?.casting_time ?? "",
    range: spell?.range ?? "",
    duration: spell?.duration ?? "",
    components: spellComponents(spell?.components),
    damage: spell?.damage ?? "",
    damage_type: spell?.damage_type ?? "",
    save: spell?.save ?? "",
    healing: spell?.healing ? String(spell.healing) : "",
    description: spell?.description ?? "",
  });
  const [flags, setFlags] = useState({
    concentration: spell?.concentration ?? false,
    ritual: spell?.ritual ?? false,
    prepared: spell?.prepared ?? true,
  });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const set = (key: keyof typeof draft) => (event: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) => setDraft((current) => ({ ...current, [key]: event.target.value }));

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!draft.name.trim()) return;
    setSaving(true);
    setError(null);
    try {
      await onSave({
        ...draft,
        name: draft.name.trim(),
        level: Number.parseInt(draft.level, 10) || 0,
        healing: Number.parseInt(draft.healing, 10) || 0,
        ...flags,
      });
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : "Não foi possível salvar a magia");
      setSaving(false);
    }
  }

  const field = "mt-1 w-full rounded-xl border border-border bg-surface2 px-3 py-2 text-sm text-ink outline-none focus:border-violet-400/70";
  return createPortal(
    <div className="fixed inset-0 z-[80] flex items-center justify-center bg-black/55 p-4 backdrop-blur-sm" onMouseDown={onClose}>
      <form role="dialog" aria-modal="true" aria-labelledby="imaginai-spell-editor-title" onSubmit={submit} onMouseDown={(event) => event.stopPropagation()} className="max-h-[calc(100dvh-2rem)] w-full max-w-2xl overflow-y-auto rounded-2xl border border-border bg-surface p-5 shadow-menu">
        <div className="flex items-center justify-between gap-3">
          <h2 id="imaginai-spell-editor-title" className="text-base font-semibold text-ink">{spell ? "Editar magia" : "Nova magia"}</h2>
          <button type="button" onClick={onClose} aria-label="Fechar" className="flex h-9 w-9 items-center justify-center rounded-xl text-muted transition-colors hover:bg-hover hover:text-ink"><X size={16} /></button>
        </div>
        <div className="mt-4 grid gap-3 sm:grid-cols-4">
          <label className="text-xs font-medium text-ink-soft sm:col-span-3">Nome<input autoFocus required maxLength={120} value={draft.name} onChange={set("name")} className={field} /></label>
          <label className="text-xs font-medium text-ink-soft">Nível<select value={draft.level} onChange={set("level")} className={field}>{Array.from({ length: 10 }, (_, level) => <option key={level} value={level}>{levelLabel(level)}</option>)}</select></label>
          <label className="text-xs font-medium text-ink-soft sm:col-span-2">Escola<input maxLength={80} value={draft.school} onChange={set("school")} placeholder="Evocação" className={field} /></label>
          <label className="text-xs font-medium text-ink-soft sm:col-span-2">Tempo de conjuração<input maxLength={80} value={draft.casting_time} onChange={set("casting_time")} placeholder="1 ação" className={field} /></label>
          <label className="text-xs font-medium text-ink-soft sm:col-span-2">Alcance<input maxLength={80} value={draft.range} onChange={set("range")} placeholder="36 m" className={field} /></label>
          <label className="text-xs font-medium text-ink-soft sm:col-span-2">Duração<input maxLength={80} value={draft.duration} onChange={set("duration")} placeholder="Instantânea" className={field} /></label>
          <label className="text-xs font-medium text-ink-soft sm:col-span-4">Componentes<input maxLength={400} value={draft.components} onChange={set("components")} placeholder="V, S, M (uma pitada de cinza)" className={field} /></label>
          <label className="text-xs font-medium text-ink-soft">Dano<input maxLength={20} value={draft.damage} onChange={set("damage")} placeholder="1d10" className={field} /></label>
          <label className="text-xs font-medium text-ink-soft">Tipo de dano<input maxLength={40} value={draft.damage_type} onChange={set("damage_type")} placeholder="fogo" className={field} /></label>
          <label className="text-xs font-medium text-ink-soft">Salvaguarda<select value={draft.save} onChange={set("save")} className={field}><option value="">Ataque / nenhuma</option>{["FOR", "DES", "CON", "INT", "SAB", "CAR"].map((ability) => <option key={ability} value={ability}>{ability}</option>)}</select></label>
          <label className="text-xs font-medium text-ink-soft">Cura<input type="number" min="0" max="999" value={draft.healing} onChange={set("healing")} className={field} /></label>
          <label className="text-xs font-medium text-ink-soft sm:col-span-4">Descrição<textarea rows={6} maxLength={4000} value={draft.description} onChange={set("description")} className={`${field} resize-y leading-5`} /></label>
        </div>
        <div className="imaginai-chips mt-3 flex-wrap">
          {([["concentration", "Concentração"], ["ritual", "Ritual"], ["prepared", "Preparada"]] as const).map(([key, label]) => <button key={key} type="button" aria-pressed={flags[key]} onClick={() => setFlags((current) => ({ ...current, [key]: !current[key] }))} className="imaginai-chip">{label}</button>)}
        </div>
        {error ? <p className="mt-3 text-xs text-rose-400">{error}</p> : null}
        <div className="mt-5 flex justify-end gap-2">
          <button type="button" onClick={onClose} className="min-h-10 rounded-xl px-3 text-sm text-ink-soft transition-colors hover:bg-hover hover:text-ink">Cancelar</button>
          <button type="submit" disabled={saving || !draft.name.trim()} className="flex min-h-10 items-center gap-2 rounded-xl bg-violet-500 px-4 text-sm font-medium text-white transition-colors hover:bg-violet-400 disabled:cursor-not-allowed disabled:opacity-50">{saving ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />}Salvar</button>
        </div>
      </form>
    </div>,
    document.body,
  );
}
