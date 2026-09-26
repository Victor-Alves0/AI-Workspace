"use client";

import { useState } from "react";
import { createPortal } from "react-dom";
import { Check, Loader2, Pencil, X } from "lucide-react";
import { api } from "@/lib/api";
import type { ImaginaiEntity, ImaginaiSnapshot, ImaginaiSystemDefinition } from "../types";
import { EntityImage, ImaginaiFeatureStatus, abilityScore, numericState, signed } from "../shared";
import { Select } from "@/components/ui";

export function ImaginaiSheetPanel({
  campaignId,
  character,
  system,
  error,
  onSnapshotChange,
}: {
  campaignId: string | null;
  character: ImaginaiEntity | null;
  system: ImaginaiSystemDefinition | null;
  error: string | null;
  onSnapshotChange: (snapshot: ImaginaiSnapshot) => void;
}) {
  const [editing, setEditing] = useState(false);
  if (error) return <ImaginaiFeatureStatus error>{error}</ImaginaiFeatureStatus>;
  if (!character || !system) return <ImaginaiFeatureStatus><Loader2 size={17} className="animate-spin" /></ImaginaiFeatureStatus>;
  const dnd = (character.state.dnd5e ?? character.state) as Record<string, unknown>;
  const attributes = (dnd.attributes ?? {}) as Record<string, unknown>;
  const skills = (dnd.skills ?? {}) as Record<string, unknown>;
  const saves = (dnd.saving_throws ?? {}) as Record<string, unknown>;
  const proficiency = numericState(dnd.proficiency_bonus, 2);
  return (
    <div className="imaginai-feature-scroll">
      <div className="flex items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          {character.image_url ? <EntityImage url={character.image_url} alt={character.name} className="h-9 w-9 shrink-0 rounded-full" /> : null}
          <span className="truncate text-xs text-ink-soft">{character.name}</span>
        </div>
        <button type="button" onClick={() => setEditing(true)} className="flex min-h-10 items-center gap-1.5 rounded-lg px-2 text-[10px] font-medium text-violet-200 transition-colors hover:bg-violet-500/15 hover:text-white"><Pencil size={13} />Editar</button>
      </div>
      <div className="mt-2 grid grid-cols-3 gap-1">
        {system.sheet.summary.map((field) => <div key={field.key} className="rounded-lg border border-border bg-surface2/55 px-1.5 py-1.5 text-center"><span className="block truncate text-[8px] uppercase tracking-wide text-muted">{field.label}</span><span className="mt-0.5 block font-mono text-[11px] text-ink">{numericState(dnd[field.key], field.key === "speed" ? 30 : field.key === "proficiency_bonus" ? 2 : 0)}</span></div>)}
      </div>
      <div className="mt-2 space-y-1.5">
        {system.sheet.attributes.map((attribute) => {
          const attributeState = attributes[attribute.key];
          const score = abilityScore(attributeState);
          const modifier = Math.floor((score - 10) / 2);
          const saveState = saves[attribute.key] ?? (
            attributeState && typeof attributeState === "object"
              ? (attributeState as Record<string, unknown>).save_proficient
              : undefined
          );
          const saveProficient = saveState === true || (typeof saveState === "object" && saveState !== null && Boolean((saveState as Record<string, unknown>).proficient));
          return <section key={attribute.key} className="imaginai-ability-row">
            <div className="imaginai-ability-score" title={attribute.label}><span>{attribute.short}</span><strong>{score}</strong><em>{signed(modifier)}</em></div>
            <div className="min-w-0 flex-1 py-2 pl-3 pr-2.5">
              <div className="flex items-center justify-between gap-2 border-b border-border/70 pb-1.5"><span className="truncate text-[10px] font-medium text-ink-soft">Salvaguarda</span><span className="flex shrink-0 items-center gap-1.5 font-mono text-[10px] text-ink"><i className={`h-1.5 w-1.5 shrink-0 rounded-full ${saveProficient ? "bg-violet-300" : "border border-muted"}`} />{signed(modifier + (saveProficient ? proficiency : 0))}</span></div>
              <div className="mt-1.5 space-y-1">{attribute.skills.length ? attribute.skills.map((skillKey) => {
                const skillState = skills[skillKey];
                const explicit = typeof skillState === "number" ? skillState : typeof skillState === "object" && skillState !== null ? (skillState as Record<string, unknown>).value : undefined;
                const rank = skillState === true ? 1 : typeof skillState === "object" && skillState !== null ? numericState((skillState as Record<string, unknown>).proficiency, Boolean((skillState as Record<string, unknown>).proficient) ? 1 : 0) : 0;
                const value = typeof explicit === "number" ? explicit : modifier + proficiency * Math.min(2, rank);
                return <div key={skillKey} className="flex items-center justify-between gap-2 text-[10px] leading-4"><span className="truncate text-muted">{system.sheet.skills[skillKey] ?? skillKey}</span><span className="flex shrink-0 items-center gap-1.5 font-mono text-ink-soft"><i className={`h-1.5 w-1.5 shrink-0 rounded-full ${rank >= 2 ? "ring-1 ring-violet-300 bg-violet-300" : rank === 1 ? "bg-violet-300" : "border border-muted"}`} />{signed(value)}</span></div>;
              }) : <span className="text-[10px] leading-4 text-muted">Sem perícias associadas</span>}</div>
            </div>
          </section>;
        })}
      </div>
      {editing && campaignId ? <ImaginaiCharacterEditor campaignId={campaignId} character={character} system={system} onSnapshotChange={onSnapshotChange} onClose={() => setEditing(false)} /> : null}
    </div>
  );
}

export const DND5E_CLASSES = ["Bárbaro", "Bardo", "Bruxo", "Clérigo", "Druida", "Feiticeiro", "Guerreiro", "Ladino", "Mago", "Monge", "Paladino", "Patrulheiro"];

export const DND5E_ABILITY_FIELDS = [
  ["strength", "FOR"], ["dexterity", "DES"], ["constitution", "CON"],
  ["intelligence", "INT"], ["wisdom", "SAB"], ["charisma", "CAR"],
] as const;

/** 0 = sem proficiência · 1 = proficiente · 2 = especialização (bônus dobrado). */
export function skillRank(state: unknown): number {
  if (state === true) return 1;
  if (state && typeof state === "object") {
    const record = state as Record<string, unknown>;
    return numericState(record.proficiency, record.proficient ? 1 : 0);
  }
  return 0;
}

export function ImaginaiCharacterEditor({
  campaignId,
  character,
  system,
  onSnapshotChange,
  onClose,
}: {
  campaignId: string;
  character: ImaginaiEntity;
  system: ImaginaiSystemDefinition | null;
  onSnapshotChange: (snapshot: ImaginaiSnapshot) => void;
  onClose: () => void;
}) {
  const dnd = (character.state.dnd5e ?? character.state) as Record<string, unknown>;
  const hp = (dnd.hp ?? {}) as Record<string, unknown>;
  const sourceAttributes = (dnd.attributes ?? {}) as Record<string, unknown>;
  const [name, setName] = useState(character.name);
  const [characterClass, setCharacterClass] = useState(typeof dnd.class === "string" && dnd.class !== "Classe" ? dnd.class : "");
  const [level, setLevel] = useState(String(numericState(dnd.level, 1)));
  const [ancestry, setAncestry] = useState(typeof dnd.ancestry === "string" ? dnd.ancestry : "");
  const [background, setBackground] = useState(typeof dnd.background === "string" ? dnd.background : "");
  const [alignment, setAlignment] = useState(typeof dnd.alignment === "string" ? dnd.alignment : "");
  // a história é o que o narrador usa para inserir o personagem no mundo
  const [backstory, setBackstory] = useState(typeof dnd.backstory === "string" ? dnd.backstory : "");
  const [hpCurrent, setHpCurrent] = useState(String(numericState(hp.current, 10)));
  const [hpMax, setHpMax] = useState(String(numericState(hp.max, 10)));
  const [armorClass, setArmorClass] = useState(String(numericState(dnd.armor_class, 10)));
  const [speed, setSpeed] = useState(String(numericState(dnd.speed, 30)));
  const [attributes, setAttributes] = useState<Record<string, string>>(() => Object.fromEntries(
    DND5E_ABILITY_FIELDS.map(([key]) => [key, String(abilityScore(sourceAttributes[key]))]),
  ));
  const sourceSkills = (dnd.skills ?? {}) as Record<string, unknown>;
  const sourceSaves = (dnd.saving_throws ?? {}) as Record<string, unknown>;
  const [skillRanks, setSkillRanks] = useState<Record<string, number>>(() => Object.fromEntries(
    Object.entries(sourceSkills).map(([key, value]) => [key, skillRank(value)]),
  ));
  const [saveProfs, setSaveProfs] = useState<Record<string, boolean>>(() => Object.fromEntries(
    DND5E_ABILITY_FIELDS.map(([key]) => [key, skillRank(sourceSaves[key]) > 0]),
  ));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const numeric = (value: string, fallback: number) => {
    const parsed = Number.parseInt(value, 10);
    return Number.isFinite(parsed) ? parsed : fallback;
  };
  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!name.trim() || !characterClass) return;
    setSaving(true);
    setError(null);
    try {
      const snapshot = await api.patch<ImaginaiSnapshot>(`/mini-apps/imaginai/campaigns/${campaignId}/character`, {
        name: name.trim(),
        character_class: characterClass,
        level: numeric(level, 1),
        ancestry: ancestry.trim(),
        background: background.trim(),
        alignment: alignment.trim(),
        backstory: backstory.trim(),
        hp_current: numeric(hpCurrent, 10),
        hp_max: numeric(hpMax, 10),
        armor_class: numeric(armorClass, 10),
        speed: numeric(speed, 30),
        attributes: Object.fromEntries(DND5E_ABILITY_FIELDS.map(([key]) => [key, numeric(attributes[key] ?? "10", 10)])),
        save_proficiencies: Object.entries(saveProfs).filter(([, on]) => on).map(([key]) => key),
        skill_proficiencies: Object.entries(skillRanks).filter(([, rank]) => rank > 0).map(([key]) => key),
        skill_expertise: Object.entries(skillRanks).filter(([, rank]) => rank >= 2).map(([key]) => key),
      });
      onSnapshotChange(snapshot);
      onClose();
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : "Não foi possível salvar a ficha");
    } finally {
      setSaving(false);
    }
  }

  const field = "mt-1 w-full min-h-10 rounded-xl border border-border bg-surface2 px-3 py-2 text-sm text-ink outline-none transition-colors focus:border-violet-400/70";
  const section = "text-[10px] font-semibold uppercase tracking-[0.14em] text-violet-300";
  // portal: o card do dock tem backdrop-filter/transform, que prendem um `fixed` DENTRO
  // dele — o editor saía cortado no alto e no pé do card
  return createPortal(
    <div className="fixed inset-0 z-[80] flex items-center justify-center bg-black/55 p-3 backdrop-blur-sm sm:p-6" onMouseDown={onClose}>
      <form role="dialog" aria-modal="true" aria-labelledby="imaginai-character-editor-title" onSubmit={submit} onMouseDown={(event) => event.stopPropagation()} className="flex max-h-full w-full max-w-3xl flex-col overflow-hidden rounded-2xl border border-border bg-surface shadow-menu">
        <header className="flex shrink-0 items-center justify-between gap-3 border-b border-border px-5 py-3.5">
          <div><p className={section}>D&amp;D 5e</p><h2 id="imaginai-character-editor-title" className="mt-0.5 text-base font-semibold text-ink">Editar ficha</h2></div>
          <button type="button" onClick={onClose} aria-label="Fechar editor de personagem" className="flex h-9 w-9 items-center justify-center rounded-xl text-muted transition-colors hover:bg-hover hover:text-ink"><X size={17} /></button>
        </header>

        <div className="min-h-0 flex-1 space-y-6 overflow-y-auto px-5 py-4">
          <section>
            <p className={section}>Identidade</p>
            <div className="mt-2 grid gap-3 sm:grid-cols-6">
              <label className="text-xs font-medium text-ink-soft sm:col-span-4">Nome<input autoFocus required maxLength={255} value={name} onChange={(event) => setName(event.target.value)} className={field} /></label>
              <label className="text-xs font-medium text-ink-soft sm:col-span-2">Nível<input required type="number" min="1" max="20" value={level} onChange={(event) => setLevel(event.target.value)} className={field} /></label>
              <label className="text-xs font-medium text-ink-soft sm:col-span-2">Classe<Select required value={characterClass} onChange={(event) => setCharacterClass(event.target.value)} className={field}><option value="" disabled>Selecione</option>{DND5E_CLASSES.map((item) => <option key={item} value={item}>{item}</option>)}</Select></label>
              <label className="text-xs font-medium text-ink-soft sm:col-span-2">Ancestralidade<input maxLength={120} value={ancestry} onChange={(event) => setAncestry(event.target.value)} placeholder="Humano" className={field} /></label>
              <label className="text-xs font-medium text-ink-soft sm:col-span-2">Antecedente<input maxLength={120} value={background} onChange={(event) => setBackground(event.target.value)} placeholder="Acólito" className={field} /></label>
              <label className="text-xs font-medium text-ink-soft sm:col-span-6">Tendência<input maxLength={80} value={alignment} onChange={(event) => setAlignment(event.target.value)} placeholder="Neutro e Bom" className={field} /></label>
            </div>
          </section>

          <section>
            <p className={section}>Atributos</p>
            <div className="mt-2 grid grid-cols-3 gap-2 sm:grid-cols-6">
              {DND5E_ABILITY_FIELDS.map(([key, label]) => {
                const value = Number.parseInt(attributes[key] ?? "10", 10);
                const mod = Number.isFinite(value) ? Math.floor((value - 10) / 2) : 0;
                return <label key={key} className="flex flex-col items-center rounded-xl border border-border bg-surface2/60 px-2 py-2 transition-colors focus-within:border-violet-400/60">
                  <span className="text-[10px] font-semibold tracking-wide text-muted">{label}</span>
                  <input aria-label={label} type="number" min="1" max="30" value={attributes[key] ?? "10"} onChange={(event) => setAttributes((current) => ({ ...current, [key]: event.target.value }))} className="mt-0.5 w-full bg-transparent text-center font-mono text-lg text-ink outline-none [appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none" />
                  <span className="font-mono text-[11px] text-violet-300">{signed(mod)}</span>
                </label>;
              })}
            </div>
          </section>

          <section>
            <p className={section}>Combate</p>
            <div className="mt-2 grid grid-cols-2 gap-3 sm:grid-cols-4">
              <label className="text-xs font-medium text-ink-soft">PV atuais<input type="number" min="0" max="9999" value={hpCurrent} onChange={(event) => setHpCurrent(event.target.value)} className={field} /></label>
              <label className="text-xs font-medium text-ink-soft">PV máximos<input type="number" min="1" max="9999" value={hpMax} onChange={(event) => setHpMax(event.target.value)} className={field} /></label>
              <label className="text-xs font-medium text-ink-soft">CA<input type="number" min="0" max="99" value={armorClass} onChange={(event) => setArmorClass(event.target.value)} className={field} /></label>
              <label className="text-xs font-medium text-ink-soft">Deslocamento<input type="number" min="0" max="999" value={speed} onChange={(event) => setSpeed(event.target.value)} className={field} /></label>
            </div>
          </section>

          <section>
            <p className={section}>Proficiências</p>
            <p className="mt-2 text-xs font-medium text-ink-soft">Salvaguardas</p>
            <div className="imaginai-chips mt-1.5 flex-wrap">
              {DND5E_ABILITY_FIELDS.map(([key, label]) => <button key={key} type="button" aria-pressed={Boolean(saveProfs[key])} onClick={() => setSaveProfs((current) => ({ ...current, [key]: !current[key] }))} className="imaginai-chip">{label}</button>)}
            </div>
            {system ? <>
              <p className="mt-3 text-xs font-medium text-ink-soft">Perícias <span className="font-normal text-muted">· toque de novo para especialização ★</span></p>
              <div className="imaginai-chips mt-1.5 flex-wrap">
                {Object.entries(system.sheet.skills).map(([key, label]) => {
                  const rank = skillRanks[key] ?? 0;
                  return <button key={key} type="button" aria-pressed={rank > 0} onClick={() => setSkillRanks((current) => ({ ...current, [key]: ((current[key] ?? 0) + 1) % 3 }))} className="imaginai-chip">{label}{rank >= 2 ? " ★" : ""}</button>;
                })}
              </div>
            </> : null}
          </section>

          <section>
            <p className={section}>História</p>
            <textarea maxLength={8000} rows={6} value={backstory} onChange={(event) => setBackstory(event.target.value)} placeholder="De onde vem, o que perdeu, o que procura, quem deixou para trás…" className={`${field} resize-y leading-6`} />
          </section>
        </div>

        <footer className="flex shrink-0 items-center justify-end gap-2 border-t border-border px-5 py-3">
          {error ? <p className="mr-auto text-xs text-rose-400">{error}</p> : null}
          <button type="button" onClick={onClose} className="min-h-10 rounded-xl px-3 text-sm text-ink-soft transition-colors hover:bg-hover hover:text-ink">Cancelar</button>
          <button type="submit" disabled={saving || !name.trim() || !characterClass} className="flex min-h-10 items-center gap-2 rounded-xl bg-violet-500 px-4 text-sm font-medium text-white transition-colors hover:bg-violet-400 disabled:cursor-not-allowed disabled:opacity-50">{saving ? <Loader2 size={15} className="animate-spin" /> : <Check size={15} />}Salvar ficha</button>
        </footer>
      </form>
    </div>,
    document.body,
  );
}
