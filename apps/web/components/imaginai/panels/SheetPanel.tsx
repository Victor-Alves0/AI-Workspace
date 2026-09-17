"use client";

import { useState } from "react";
import { Check, Loader2, Pencil, X } from "lucide-react";
import { api } from "@/lib/api";
import type { ImaginaiEntity, ImaginaiSnapshot, ImaginaiSystemDefinition } from "../types";
import { ImaginaiFeatureStatus, abilityScore, numericState, signed } from "../shared";

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
        <h3 className="text-sm font-semibold text-ink">Ficha</h3>
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
      <p className="mt-2 text-[9px] leading-4 text-muted">Ponto cheio: proficiente · aro: especialização. Os valores vêm do estado autoritativo da campanha.</p>
      {editing && campaignId ? <ImaginaiCharacterEditor campaignId={campaignId} character={character} onSnapshotChange={onSnapshotChange} onClose={() => setEditing(false)} /> : null}
    </div>
  );
}

export const DND5E_CLASSES = ["Bárbaro", "Bardo", "Bruxo", "Clérigo", "Druida", "Feiticeiro", "Guerreiro", "Ladino", "Mago", "Monge", "Paladino", "Patrulheiro"];

export const DND5E_ABILITY_FIELDS = [
  ["strength", "FOR"], ["dexterity", "DES"], ["constitution", "CON"],
  ["intelligence", "INT"], ["wisdom", "SAB"], ["charisma", "CAR"],
] as const;

export function ImaginaiCharacterEditor({
  campaignId,
  character,
  onSnapshotChange,
  onClose,
}: {
  campaignId: string;
  character: ImaginaiEntity;
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
  const [hpCurrent, setHpCurrent] = useState(String(numericState(hp.current, 10)));
  const [hpMax, setHpMax] = useState(String(numericState(hp.max, 10)));
  const [armorClass, setArmorClass] = useState(String(numericState(dnd.armor_class, 10)));
  const [speed, setSpeed] = useState(String(numericState(dnd.speed, 30)));
  const [attributes, setAttributes] = useState<Record<string, string>>(() => Object.fromEntries(
    DND5E_ABILITY_FIELDS.map(([key]) => [key, String(abilityScore(sourceAttributes[key]))]),
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
        hp_current: numeric(hpCurrent, 10),
        hp_max: numeric(hpMax, 10),
        armor_class: numeric(armorClass, 10),
        speed: numeric(speed, 30),
        attributes: Object.fromEntries(DND5E_ABILITY_FIELDS.map(([key]) => [key, numeric(attributes[key] ?? "10", 10)])),
      });
      onSnapshotChange(snapshot);
      onClose();
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : "Não foi possível salvar a ficha");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/55 p-3 backdrop-blur-sm" onMouseDown={onClose}>
      <form role="dialog" aria-modal="true" aria-labelledby="imaginai-character-editor-title" onSubmit={submit} onMouseDown={(event) => event.stopPropagation()} className="max-h-[calc(100dvh-1.5rem)] w-full max-w-lg overflow-y-auto rounded-2xl border border-border bg-surface p-4 shadow-menu">
        <div className="flex items-center justify-between gap-3"><div><p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-violet-300">D&D 5e</p><h2 id="imaginai-character-editor-title" className="mt-0.5 text-base font-semibold text-ink">Configurar personagem</h2></div><button type="button" onClick={onClose} aria-label="Fechar editor de personagem" className="flex h-10 w-10 items-center justify-center rounded-xl text-muted transition-colors hover:bg-hover hover:text-ink"><X size={17} /></button></div>
        <div className="mt-4 grid gap-3 sm:grid-cols-2">
          <label className="text-xs font-medium text-ink-soft sm:col-span-2">Nome<input autoFocus required maxLength={255} value={name} onChange={(event) => setName(event.target.value)} className="imaginai-field" /></label>
          <label className="text-xs font-medium text-ink-soft">Classe<select required value={characterClass} onChange={(event) => setCharacterClass(event.target.value)} className="imaginai-field"><option value="" disabled>Selecione uma classe</option>{DND5E_CLASSES.map((item) => <option key={item} value={item}>{item}</option>)}</select></label>
          <label className="text-xs font-medium text-ink-soft">Nível<input required type="number" min="1" max="20" value={level} onChange={(event) => setLevel(event.target.value)} className="imaginai-field" /></label>
          <label className="text-xs font-medium text-ink-soft">Ancestralidade<input maxLength={120} value={ancestry} onChange={(event) => setAncestry(event.target.value)} placeholder="Ex.: Elfo" className="imaginai-field" /></label>
          <label className="text-xs font-medium text-ink-soft">Antecedente<input maxLength={120} value={background} onChange={(event) => setBackground(event.target.value)} placeholder="Ex.: Acólito" className="imaginai-field" /></label>
          <label className="text-xs font-medium text-ink-soft sm:col-span-2">Tendência<input maxLength={80} value={alignment} onChange={(event) => setAlignment(event.target.value)} placeholder="Ex.: Neutro e Bom" className="imaginai-field" /></label>
        </div>
        <div className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-4"><label className="text-[10px] font-medium text-ink-soft">HP atual<input type="number" min="0" max="9999" value={hpCurrent} onChange={(event) => setHpCurrent(event.target.value)} className="imaginai-field" /></label><label className="text-[10px] font-medium text-ink-soft">HP máximo<input type="number" min="1" max="9999" value={hpMax} onChange={(event) => setHpMax(event.target.value)} className="imaginai-field" /></label><label className="text-[10px] font-medium text-ink-soft">CA<input type="number" min="0" max="99" value={armorClass} onChange={(event) => setArmorClass(event.target.value)} className="imaginai-field" /></label><label className="text-[10px] font-medium text-ink-soft">Deslocamento<input type="number" min="0" max="999" value={speed} onChange={(event) => setSpeed(event.target.value)} className="imaginai-field" /></label></div>
        <fieldset className="mt-4"><legend className="text-xs font-medium text-ink-soft">Atributos</legend><div className="mt-2 grid grid-cols-3 gap-2 sm:grid-cols-6">{DND5E_ABILITY_FIELDS.map(([key, label]) => <label key={key} className="rounded-xl border border-border bg-surface2/55 px-2 py-1.5 text-center text-[9px] font-semibold text-muted">{label}<input aria-label={label} type="number" min="1" max="30" value={attributes[key] ?? "10"} onChange={(event) => setAttributes((current) => ({ ...current, [key]: event.target.value }))} className="mt-1 block w-full bg-transparent text-center font-mono text-sm text-ink outline-none" /></label>)}</div></fieldset>
        <p className="mt-3 text-[10px] leading-4 text-muted">Proficiência, iniciativa e percepção passiva são calculadas pela ficha. Itens, moedas, vida durante a aventura e espaços de magia continuam sob validação do mundo.</p>
        {error ? <p className="mt-3 text-xs text-rose-400">{error}</p> : null}
        <div className="mt-5 flex justify-end gap-2"><button type="button" onClick={onClose} className="min-h-11 rounded-xl px-3 text-sm text-ink-soft transition-colors hover:bg-hover hover:text-ink">Cancelar</button><button type="submit" disabled={saving || !name.trim() || !characterClass} className="flex min-h-11 items-center gap-2 rounded-xl bg-violet-500 px-4 text-sm font-medium text-white transition-colors hover:bg-violet-400 disabled:cursor-not-allowed disabled:opacity-50">{saving ? <Loader2 size={15} className="animate-spin" /> : <Check size={15} />}Salvar ficha</button></div>
      </form>
    </div>
  );
}
