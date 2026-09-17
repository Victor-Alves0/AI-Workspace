"use client";

import { useCallback, useEffect, useState } from "react";
import { Loader2, Map as MapIcon, Package, Plus, Users, X } from "lucide-react";
import { api } from "@/lib/api";
import { ImaginaiFeatureStatus } from "./shared";
import type { ImaginaiWorldEntity } from "./types";

export function ImaginaiWorldBuilder({ campaignId, onClose }: { campaignId: string; onClose: () => void }) {
  const [entities, setEntities] = useState<ImaginaiWorldEntity[]>([]);
  const [kind, setKind] = useState("location");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [locationId, setLocationId] = useState("");
  const [visibility, setVisibility] = useState("known");
  const [privateNotes, setPrivateNotes] = useState("");
  const [combatHp, setCombatHp] = useState("10");
  const [combatAc, setCombatAc] = useState("10");
  const [hostile, setHostile] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await api.get<{ entities: ImaginaiWorldEntity[] }>(`/mini-apps/imaginai/campaigns/${campaignId}/world/entities`);
      setEntities(result.entities);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Não foi possível carregar o mundo");
    } finally {
      setLoading(false);
    }
  }, [campaignId]);
  useEffect(() => { void load(); }, [load]);
  const locations = entities.filter((entity) => entity.kind === "location" && entity.active);
  const makeKey = () => {
    const slug = name.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 72) || kind;
    return `manual-${kind}-${slug}-${Date.now().toString(36)}`;
  };
  async function create(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!name.trim()) return;
    setSaving(true);
    setError(null);
    try {
      const state: Record<string, unknown> = visibility === "known" ? { discovered: true } : visibility === "aware" ? { discovery: "aware" } : { hidden: true };
      if (kind === "location") state.map = {};
      if (kind === "npc" || kind === "creature") {
        const hp = Math.max(1, Math.min(9999, Number.parseInt(combatHp, 10) || 10));
        const ac = Math.max(0, Math.min(99, Number.parseInt(combatAc, 10) || 10));
        state.dnd5e = { hp: { current: hp, max: hp }, armor_class: ac, conditions: [] };
        if (hostile) state.hostile = true;
      }
      await api.post(`/mini-apps/imaginai/campaigns/${campaignId}/entities`, {
        kind,
        key: makeKey(),
        name: name.trim(),
        description,
        location_id: kind === "location" || !locationId ? null : locationId,
        state,
        private_notes: privateNotes || null,
      });
      setName(""); setDescription(""); setPrivateNotes(""); setLocationId(""); setHostile(false);
      await load();
    } catch (createError) {
      setError(createError instanceof Error ? createError.message : "Não foi possível criar a entidade");
    } finally {
      setSaving(false);
    }
  }
  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/60 p-3 backdrop-blur-sm" onMouseDown={onClose}>
      <section role="dialog" aria-modal="true" aria-labelledby="imaginai-world-builder-title" onMouseDown={(event) => event.stopPropagation()} className="max-h-[calc(100dvh-1.5rem)] w-full max-w-3xl overflow-y-auto rounded-2xl border border-border bg-surface p-4 shadow-menu">
        <div className="flex items-center justify-between gap-3"><div><p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-violet-300">Imaginai · Autoria</p><h2 id="imaginai-world-builder-title" className="mt-0.5 text-base font-semibold text-ink">Construir mundo</h2><p className="mt-1 text-xs text-muted">Notas privadas alimentam a atuação do NPC, nunca o Codex do personagem.</p></div><button type="button" onClick={onClose} aria-label="Fechar construtor de mundo" className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl text-muted transition-colors hover:bg-hover hover:text-ink"><X size={17} /></button></div>
        <form onSubmit={create} className="mt-4 rounded-xl border border-violet-400/20 bg-violet-500/[.04] p-3"><div className="grid gap-3 sm:grid-cols-2"><label className="text-xs font-medium text-ink-soft">Tipo<select value={kind} onChange={(event) => setKind(event.target.value)} className="imaginai-field"><option value="location">Local</option><option value="npc">NPC</option><option value="creature">Criatura</option><option value="item">Item</option><option value="faction">Facção</option></select></label><label className="text-xs font-medium text-ink-soft">Nome<input value={name} onChange={(event) => setName(event.target.value)} maxLength={255} required placeholder="Nome da entidade" className="imaginai-field" /></label><label className="text-xs font-medium text-ink-soft">Visibilidade inicial<select value={visibility} onChange={(event) => setVisibility(event.target.value)} className="imaginai-field"><option value="known">Conhecida pelo personagem</option><option value="aware">Conhecida, detalhes ocultos</option><option value="hidden">Oculta</option></select></label>{kind !== "location" ? <label className="text-xs font-medium text-ink-soft">Localização<select value={locationId} onChange={(event) => setLocationId(event.target.value)} className="imaginai-field"><option value="">Sem localização definida</option>{locations.map((location) => <option key={location.id} value={location.id}>{location.name}</option>)}</select></label> : null}</div>{kind === "npc" || kind === "creature" ? <div className="mt-3 grid grid-cols-3 gap-3 rounded-xl border border-border/80 bg-surface2/45 p-2.5"><label className="text-[10px] font-medium text-ink-soft">HP<input type="number" min="1" max="9999" value={combatHp} onChange={(event) => setCombatHp(event.target.value)} className="imaginai-field" /></label><label className="text-[10px] font-medium text-ink-soft">CA<input type="number" min="0" max="99" value={combatAc} onChange={(event) => setCombatAc(event.target.value)} className="imaginai-field" /></label><label className="flex min-h-11 cursor-pointer items-center gap-2 self-end rounded-lg px-1 text-[10px] text-ink-soft"><input type="checkbox" checked={hostile} onChange={(event) => setHostile(event.target.checked)} className="accent-violet-400" />Hostil</label></div> : null}<label className="mt-3 block text-xs font-medium text-ink-soft">Descrição pública<textarea value={description} onChange={(event) => setDescription(event.target.value)} maxLength={20000} rows={3} placeholder="O que pode aparecer ao jogador quando descobrir isso?" className="mt-1.5 w-full resize-y rounded-xl border border-border bg-surface2 px-3 py-2.5 text-sm leading-5 text-ink outline-none focus:border-violet-400/70" /></label><label className="mt-3 block text-xs font-medium text-ink-soft">Notas privadas / persona<textarea value={privateNotes} onChange={(event) => setPrivateNotes(event.target.value)} maxLength={100000} rows={3} placeholder="Motivações, segredos, voz e conhecimento exclusivo do NPC." className="mt-1.5 w-full resize-y rounded-xl border border-border bg-surface2 px-3 py-2.5 text-sm leading-5 text-ink outline-none focus:border-violet-400/70" /></label><div className="mt-3 flex justify-end"><button type="submit" disabled={saving || !name.trim()} className="flex min-h-11 items-center gap-2 rounded-xl bg-violet-500 px-4 text-sm font-medium text-white transition-colors hover:bg-violet-400 disabled:cursor-not-allowed disabled:opacity-50">{saving ? <Loader2 size={15} className="animate-spin" /> : <Plus size={15} />}Adicionar ao mundo</button></div></form>
        {error ? <p className="mt-3 text-xs text-rose-400">{error}</p> : null}
        <div className="mt-4"><div className="flex items-center justify-between gap-2"><h3 className="text-sm font-semibold text-ink">Entidades</h3><span className="text-[10px] text-muted">{entities.length}</span></div>{loading ? <ImaginaiFeatureStatus><Loader2 size={17} className="animate-spin" /></ImaginaiFeatureStatus> : entities.length === 0 ? <ImaginaiFeatureStatus>Crie o primeiro local ou NPC da campanha.</ImaginaiFeatureStatus> : <div className="mt-2 grid gap-1.5 sm:grid-cols-2">{entities.map((entity) => <article key={entity.id} className="rounded-xl border border-border bg-surface2/55 p-2.5"><div className="flex items-start gap-2"><span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-violet-500/10 text-violet-300">{entity.kind === "location" ? <MapIcon size={14} /> : entity.kind === "item" ? <Package size={14} /> : <Users size={14} />}</span><div className="min-w-0 flex-1"><p className="truncate text-xs font-medium text-ink">{entity.name}</p><p className="mt-0.5 truncate text-[10px] capitalize text-muted">{entity.kind}{entity.active ? "" : " · inativa"}</p></div></div><p className="mt-2 line-clamp-2 text-[10px] leading-4 text-muted">{entity.description || "Sem descrição pública."}</p>{entity.private_notes ? <p className="mt-1 text-[9px] text-violet-300">Possui notas privadas</p> : null}</article>)}</div>}</div>
      </section>
    </div>
  );
}
