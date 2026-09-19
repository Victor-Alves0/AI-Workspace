"use client";

import { useEffect, useState } from "react";
import { BookOpen, Check, ChevronLeft, ChevronRight, Heart, Loader2, Pencil, Settings, Shield, Sparkles, Users, X } from "lucide-react";
import { api } from "@/lib/api";
import type { ImaginaiSnapshot, ImaginaiSystemDefinition } from "./types";
import { DND_CHARACTER_SECTIONS, DND_WORLD_SECTIONS, ImaginaiSheetHead, type CharacterSection, type WorldSection } from "./shared";
import { ImaginaiWorldBuilder } from "./WorldBuilder";
import CombatTracker from "./CombatTracker";
import { ImaginaiJournalPanel } from "./panels/JournalPanel";
import { ImaginaiCodexPanel } from "./panels/CodexPanel";
import { ImaginaiMapPanel } from "./panels/MapPanel";
import { ImaginaiInventoryPanel } from "./panels/InventoryPanel";
import { ImaginaiSpellsPanel } from "./panels/SpellsPanel";
import { ImaginaiSheetPanel } from "./panels/SheetPanel";

/**
 * Docks do primeiro sistema do Imaginai. A composição em dois painéis permite que
 * sistemas futuros forneçam seus próprios campos sem alterar a coluna central.
 */
const EXPAND_FOCUS = ["locais", "NPCs", "facções", "criaturas", "lore", "caminhos no mapa"];

export default function ImaginaiDocks({
  snapshot,
  loading,
  error,
  onSnapshotChange,
  onSendMessage,
  busy = false,
}: {
  snapshot: ImaginaiSnapshot | null;
  loading: boolean;
  error: string | null;
  onSnapshotChange: (snapshot: ImaginaiSnapshot) => void;
  /** manda uma mensagem no chat da campanha (a IA age sobre ela) */
  onSendMessage?: (text: string) => void;
  busy?: boolean;
}) {
  const [characterSection, setCharacterSection] = useState<CharacterSection | null>(null);
  const [worldSection, setWorldSection] = useState<WorldSection | null>(null);
  const [system, setSystem] = useState<ImaginaiSystemDefinition | null>(null);
  const [systemError, setSystemError] = useState<string | null>(null);
  const [mobilePanel, setMobilePanel] = useState<"world" | "character" | null>(null);
  const [configOpen, setConfigOpen] = useState(false);
  const [worldBuilderOpen, setWorldBuilderOpen] = useState(false);
  const [campaignNameDraft, setCampaignNameDraft] = useState("");
  const [narrationDraft, setNarrationDraft] = useState<"balanced" | "cinematic" | "gritty">("balanced");
  const [difficultyDraft, setDifficultyDraft] = useState<"story" | "balanced" | "challenging">("balanced");
  const [premiseDraft, setPremiseDraft] = useState("");
  const [openingSceneDraft, setOpeningSceneDraft] = useState("");
  const [startingLocationNameDraft, setStartingLocationNameDraft] = useState("");
  const [startingLocationDescriptionDraft, setStartingLocationDescriptionDraft] = useState("");
  const [savingConfig, setSavingConfig] = useState(false);
  const [expandOpen, setExpandOpen] = useState(false);
  const [expandFocus, setExpandFocus] = useState<string[]>(["locais", "NPCs", "caminhos no mapa"]);
  const [expandDirection, setExpandDirection] = useState("");
  const [configError, setConfigError] = useState<string | null>(null);
  const dnd = (snapshot?.character?.state.dnd5e ?? {}) as Record<string, unknown>;
  const hp = (dnd.hp ?? {}) as Record<string, unknown>;
  const className = typeof dnd.class === "string" ? dnd.class : "Classe";
  const level = typeof dnd.level === "number" ? dnd.level : 1;
  const hpCurrent = typeof hp.current === "number" ? hp.current : null;
  const hpMax = typeof hp.max === "number" ? hp.max : null;
  const armorClass = typeof dnd.armor_class === "number" ? dnd.armor_class : null;
  const campaignName = snapshot?.campaign.name ?? "Nome da Campanha";
  const characterName = snapshot?.character?.name ?? "Nome do personagem";
  const stage = snapshot?.campaign.setup_stage;
  const status = loading
    ? "Abrindo mundo…"
    : error
      ? "Mundo indisponível"
      : stage === "concept"
        ? "Sessão zero · criando a campanha"
        : stage === "character"
          ? "Sessão zero · criando o personagem"
          : snapshot?.location?.name;

  useEffect(() => {
    const systemKey = snapshot?.campaign.system_key;
    if (!systemKey) {
      setSystem(null);
      setSystemError(null);
      return;
    }
    let cancelled = false;
    setSystem(null);
    setSystemError(null);
    api.get<ImaginaiSystemDefinition>(`/mini-apps/imaginai/systems/${systemKey}`)
      .then((definition) => { if (!cancelled) setSystem(definition); })
      .catch((loadError: unknown) => {
        if (!cancelled) setSystemError(loadError instanceof Error ? loadError.message : "Sistema indisponível");
      });
    return () => { cancelled = true; };
  }, [snapshot?.campaign.system_key]);

  // Esc fecha as abas abertas acima dos cards (o modal de configuração tem o seu).
  useEffect(() => {
    if (configOpen || (!worldSection && !characterSection)) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setWorldSection(null);
      setCharacterSection(null);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [configOpen, worldSection, characterSection]);

  useEffect(() => {
    if (!configOpen) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setConfigOpen(false);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [configOpen]);

  function openCampaignConfig() {
    if (!snapshot) return;
    setCampaignNameDraft(snapshot.campaign.name);
    setNarrationDraft(snapshot.campaign.settings?.narration_style ?? "balanced");
    setDifficultyDraft(snapshot.campaign.settings?.difficulty ?? "balanced");
    setPremiseDraft(snapshot.campaign.settings?.premise ?? "");
    setOpeningSceneDraft(snapshot.campaign.settings?.opening_scene ?? "");
    setStartingLocationNameDraft(snapshot.location?.name ?? "Local inicial");
    setStartingLocationDescriptionDraft(snapshot.location?.description ?? "");
    setConfigError(null);
    setConfigOpen(true);
  }

  function requestExpansion() {
    if (!onSendMessage || busy || expandFocus.length === 0) return;
    const direcao = expandDirection.trim();
    onSendMessage(
      `Expanda a campanha com ${expandFocus.join(", ")}, coerentes e ligados ao que já existe.`
      + (direcao ? ` Direção: ${direcao}` : ""),
    );
    setExpandOpen(false);
    setExpandDirection("");
    setConfigOpen(false);
  }

  async function saveCampaignConfig(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!snapshot || !campaignNameDraft.trim()) return;
    setSavingConfig(true);
    setConfigError(null);
    try {
      const updated = await api.patch<ImaginaiSnapshot>(
        `/mini-apps/imaginai/campaigns/${snapshot.campaign.id}`,
        {
          name: campaignNameDraft.trim(),
          narration_style: narrationDraft,
          difficulty: difficultyDraft,
          premise: premiseDraft,
          opening_scene: openingSceneDraft,
          starting_location_name: startingLocationNameDraft.trim(),
          starting_location_description: startingLocationDescriptionDraft,
        },
      );
      onSnapshotChange(updated);
      setConfigOpen(false);
    } catch (saveError) {
      setConfigError(saveError instanceof Error ? saveError.message : "Não foi possível salvar a campanha");
    } finally {
      setSavingConfig(false);
    }
  }

  return (
    <>
      <div className="imaginai-docks" aria-label="Painéis do Imaginai">
        {mobilePanel ? (
          <button
            type="button"
            aria-label="Fechar painel do Imaginai"
            className="imaginai-mobile-scrim"
            onClick={() => setMobilePanel(null)}
          />
        ) : null}

        <button
          type="button"
          aria-label="Abrir Worldinfo"
          aria-expanded={mobilePanel === "world"}
          onClick={() => setMobilePanel((current) => current === "world" ? null : "world")}
          className="imaginai-edge-tab imaginai-edge-tab-left"
        >
          <BookOpen size={17} />
          <ChevronRight size={14} />
        </button>
        <button
          type="button"
          aria-label="Abrir personagem"
          aria-expanded={mobilePanel === "character"}
          onClick={() => setMobilePanel((current) => current === "character" ? null : "character")}
          className="imaginai-edge-tab imaginai-edge-tab-right"
        >
          <ChevronLeft size={14} />
          <Users size={17} />
        </button>

        <aside className="imaginai-world-dock" data-mobile-open={mobilePanel === "world"} aria-label="Worldinfo">
          {worldSection && snapshot ? (
            <section className="imaginai-dock-card imaginai-feature-sheet animate-pop" aria-label={DND_WORLD_SECTIONS.find((section) => section.id === worldSection)?.label}>
              <ImaginaiSheetHead label="Worldinfo" onClose={() => setWorldSection(null)} />
              <div className="imaginai-feature">
                {worldSection === "journal" ? <ImaginaiJournalPanel campaignId={snapshot.campaign.id} /> : null}
                {worldSection === "codex" ? <ImaginaiCodexPanel campaignId={snapshot.campaign.id} /> : null}
                {worldSection === "map" ? <ImaginaiMapPanel campaignId={snapshot.campaign.id} /> : null}
              </div>
            </section>
          ) : null}
          <section className="imaginai-dock-card">
            <div className="flex min-h-7 items-center justify-between gap-2">
              <p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-violet-300">Worldinfo</p>
              <button
                type="button"
                onClick={openCampaignConfig}
                disabled={!snapshot || loading}
                title="Configurar campanha"
                aria-label="Configurar campanha"
                className="-mr-1.5 flex h-7 w-7 cursor-pointer items-center justify-center rounded-lg text-muted transition-colors hover:bg-hover hover:text-ink disabled:cursor-not-allowed disabled:opacity-40"
              >
                <Settings size={15} />
              </button>
            </div>
            <h2 className="mt-1.5 truncate text-sm font-semibold leading-5 text-ink" title={campaignName}>{campaignName}</h2>
            <p className={`mt-0.5 truncate text-[11px] leading-4 ${error ? "text-rose-400" : "text-muted"}`} title={error ?? status ?? undefined}>
              {status || "\u00a0"}
            </p>
            <div className="mt-3 grid grid-cols-3 gap-1 border-t border-border pt-2" role="group" aria-label="Navegação da campanha">
              {DND_WORLD_SECTIONS.map((section) => {
                const Icon = section.icon;
                const selected = worldSection === section.id;
                return (
                  <button
                    key={section.id}
                    type="button"
                    disabled={!snapshot}
                    aria-pressed={selected}
                    onClick={() => setWorldSection(selected ? null : section.id)}
                    className={`imaginai-dock-action ${selected ? "imaginai-dock-action-active" : ""}`}
                  >
                    <Icon size={16} />
                    <span className="truncate">{section.label}</span>
                  </button>
                );
              })}
            </div>
          </section>
        </aside>

        <aside className="imaginai-character-dock" data-mobile-open={mobilePanel === "character"} aria-label="Personagem">
          {characterSection && (snapshot || characterSection === "sheet") ? (
            <section className="imaginai-dock-card imaginai-feature-sheet animate-pop" aria-label={DND_CHARACTER_SECTIONS.find((section) => section.id === characterSection)?.label}>
              <ImaginaiSheetHead label="Personagem" onClose={() => setCharacterSection(null)} />
              <div className="imaginai-feature">
                {characterSection === "inventory" && snapshot ? (
                  <ImaginaiInventoryPanel campaignId={snapshot.campaign.id} system={system} />
                ) : null}
                {characterSection === "sheet" ? (
                  <ImaginaiSheetPanel
                    campaignId={snapshot?.campaign.id ?? null}
                    character={snapshot?.character ?? null}
                    system={system}
                    error={systemError}
                    onSnapshotChange={onSnapshotChange}
                  />
                ) : null}
                {characterSection === "spells" && snapshot ? <ImaginaiSpellsPanel campaignId={snapshot.campaign.id} /> : null}
              </div>
            </section>
          ) : null}
          {snapshot?.encounter?.active ? <CombatTracker encounter={snapshot.encounter} /> : null}
          <section className="imaginai-dock-card">
            <div className="flex min-h-7 items-center">
              <p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-violet-300">Personagem</p>
            </div>
            <h2 className="mt-1.5 truncate text-sm font-semibold leading-5 text-ink" title={characterName}>{characterName}</h2>
            <p className="mt-0.5 truncate text-[11px] leading-4 text-muted">{className} · Nível {level}</p>
            <div className="mt-2.5 grid grid-cols-2 gap-1.5">
              <div className="flex min-w-0 items-center justify-between gap-2 rounded-xl bg-surface2/70 px-2.5 py-1.5">
                <span className="flex items-center gap-1.5 text-[11px] font-medium text-ink-soft"><Heart size={13} className="shrink-0 text-rose-400" /> HP</span>
                <span className="truncate font-mono text-[11px] text-ink">{hpCurrent ?? "—"}/{hpMax ?? "—"}</span>
              </div>
              <div className="flex min-w-0 items-center justify-between gap-2 rounded-xl bg-surface2/70 px-2.5 py-1.5">
                <span className="flex items-center gap-1.5 text-[11px] font-medium text-ink-soft"><Shield size={13} className="shrink-0 text-sky-300" /> CA</span>
                <span className="truncate font-mono text-[11px] text-ink">{armorClass ?? "—"}</span>
              </div>
            </div>
            <div className="mt-2.5 grid grid-cols-3 gap-1 border-t border-border pt-2" role="group" aria-label="Navegação do personagem">
              {DND_CHARACTER_SECTIONS.map((section) => {
                const Icon = section.icon;
                const selected = characterSection === section.id;
                return (
                  <button
                    key={section.id}
                    type="button"
                    aria-pressed={selected}
                    onClick={() => setCharacterSection(selected ? null : section.id)}
                    className={`imaginai-dock-action ${selected ? "imaginai-dock-action-active" : ""}`}
                  >
                    <Icon size={16} />
                    <span className="truncate">{section.label}</span>
                  </button>
                );
              })}
            </div>
          </section>
        </aside>
      </div>

      {configOpen && snapshot ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/55 p-4 backdrop-blur-sm" onMouseDown={() => setConfigOpen(false)}>
          <form
            role="dialog"
            aria-modal="true"
            aria-labelledby="imaginai-campaign-settings-title"
            onSubmit={saveCampaignConfig}
            onMouseDown={(event) => event.stopPropagation()}
            className="max-h-[calc(100dvh-2rem)] w-full max-w-4xl overflow-y-auto rounded-2xl border border-border bg-surface p-5 shadow-menu"
          >
            <div className="flex items-center justify-between gap-3">
              <div>
                <p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-violet-300">Imaginai</p>
                <h2 id="imaginai-campaign-settings-title" className="mt-0.5 text-base font-semibold text-ink">Configurar campanha</h2>
              </div>
              <button type="button" onClick={() => setConfigOpen(false)} aria-label="Fechar configurações" className="flex h-10 w-10 items-center justify-center rounded-xl text-muted transition-colors hover:bg-hover hover:text-ink">
                <X size={17} />
              </button>
            </div>
            <div className="mt-4 grid gap-5 md:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
              <div className="space-y-3">
                <label className="block text-xs font-medium text-ink-soft">
                  Nome da campanha
                  <input
                    autoFocus
                    value={campaignNameDraft}
                    onChange={(event) => setCampaignNameDraft(event.target.value)}
                    maxLength={255}
                    required
                    className="mt-1.5 min-h-11 w-full rounded-xl border border-border bg-surface2 px-3 py-2.5 text-sm text-ink outline-none transition-colors focus:border-violet-400/70"
                  />
                </label>
                <div className="grid grid-cols-2 gap-3">
                  <label className="block text-xs font-medium text-ink-soft">
                    Narração
                    <select value={narrationDraft} onChange={(event) => setNarrationDraft(event.target.value as typeof narrationDraft)} className="mt-1.5 min-h-11 w-full rounded-xl border border-border bg-surface2 px-3 py-2.5 text-sm text-ink outline-none focus:border-violet-400/70">
                      <option value="balanced">Equilibrada</option>
                      <option value="cinematic">Cinematográfica</option>
                      <option value="gritty">Realista</option>
                    </select>
                  </label>
                  <label className="block text-xs font-medium text-ink-soft">
                    Dificuldade
                    <select value={difficultyDraft} onChange={(event) => setDifficultyDraft(event.target.value as typeof difficultyDraft)} className="mt-1.5 min-h-11 w-full rounded-xl border border-border bg-surface2 px-3 py-2.5 text-sm text-ink outline-none focus:border-violet-400/70">
                      <option value="story">Narrativa</option>
                      <option value="balanced">Equilibrada</option>
                      <option value="challenging">Desafiadora</option>
                    </select>
                  </label>
                </div>
                <label className="block text-xs font-medium text-ink-soft">Local inicial
                  <input value={startingLocationNameDraft} onChange={(event) => setStartingLocationNameDraft(event.target.value)} maxLength={255} required className="mt-1.5 min-h-11 w-full rounded-xl border border-border bg-surface2 px-3 py-2.5 text-sm text-ink outline-none focus:border-violet-400/70" />
                </label>
                <label className="block text-xs font-medium text-ink-soft">Descrição do local
                  <textarea value={startingLocationDescriptionDraft} onChange={(event) => setStartingLocationDescriptionDraft(event.target.value)} maxLength={5000} rows={5} placeholder="O que o personagem percebe?" className="mt-1.5 w-full resize-y rounded-xl border border-border bg-surface2 px-3 py-2.5 text-sm leading-5 text-ink outline-none focus:border-violet-400/70" />
                </label>
              </div>
              <div className="space-y-3">
                <label className="block text-xs font-medium text-ink-soft">Premissa
                  <textarea value={premiseDraft} onChange={(event) => setPremiseDraft(event.target.value)} maxLength={5000} rows={7} placeholder="O que torna esta campanha única?" className="mt-1.5 w-full resize-y rounded-xl border border-border bg-surface2 px-3 py-2.5 text-sm leading-5 text-ink outline-none focus:border-violet-400/70" />
                </label>
                <label className="block text-xs font-medium text-ink-soft">Cena de abertura
                  <textarea value={openingSceneDraft} onChange={(event) => setOpeningSceneDraft(event.target.value)} maxLength={5000} rows={7} placeholder="Onde a história começa e o que está acontecendo?" className="mt-1.5 w-full resize-y rounded-xl border border-border bg-surface2 px-3 py-2.5 text-sm leading-5 text-ink outline-none focus:border-violet-400/70" />
                </label>
              </div>
            </div>
            <div className="mt-5 grid gap-2 border-t border-border pt-4 sm:grid-cols-2">
              <button type="button" onClick={() => setWorldBuilderOpen(true)} className="flex min-h-11 items-center justify-center gap-2 rounded-xl border border-border bg-surface2/60 px-3 text-sm font-medium text-ink-soft transition-colors hover:bg-hover hover:text-ink"><Pencil size={15} /> Construir mundo e NPCs</button>
              <button type="button" aria-expanded={expandOpen} disabled={!onSendMessage} onClick={() => setExpandOpen((open) => !open)} className="flex min-h-11 items-center justify-center gap-2 rounded-xl border border-violet-400/25 bg-violet-500/10 px-3 text-sm font-medium text-violet-100 transition-colors hover:bg-violet-500/20 disabled:cursor-not-allowed disabled:opacity-40"><Sparkles size={15} /> Expandir campanha</button>
            </div>
            {expandOpen ? (
              <div className="mt-3 rounded-xl border border-violet-400/20 bg-violet-500/[0.06] p-3">
                <div className="imaginai-chips flex-wrap" role="group" aria-label="O que expandir">
                  {EXPAND_FOCUS.map((focus) => (
                    <button key={focus} type="button" aria-pressed={expandFocus.includes(focus)} onClick={() => setExpandFocus((current) => current.includes(focus) ? current.filter((item) => item !== focus) : [...current, focus])} className="imaginai-chip">{focus}</button>
                  ))}
                </div>
                <textarea value={expandDirection} onChange={(event) => setExpandDirection(event.target.value)} maxLength={1500} rows={2} placeholder="Direção (opcional): ex. mais sobre a Igreja do Osso, uma cidade portuária ao sul…" className="mt-2 w-full resize-y rounded-xl border border-border bg-surface2 px-3 py-2.5 text-sm leading-5 text-ink outline-none focus:border-violet-400/70" />
                <div className="mt-2 flex justify-end">
                  <button type="button" disabled={busy || expandFocus.length === 0} onClick={requestExpansion} className="flex min-h-10 items-center gap-2 rounded-xl bg-violet-500 px-4 text-sm font-medium text-white transition-colors hover:bg-violet-400 disabled:cursor-not-allowed disabled:opacity-50"><Sparkles size={14} /> Gerar</button>
                </div>
              </div>
            ) : null}
            {configError ? <p className="mt-3 text-xs text-rose-400">{configError}</p> : null}
            <div className="mt-5 flex justify-end gap-2">
              <button type="button" onClick={() => setConfigOpen(false)} className="min-h-11 cursor-pointer rounded-xl px-3 text-sm text-ink-soft transition-colors hover:bg-hover hover:text-ink">Cancelar</button>
              <button type="submit" disabled={savingConfig || !campaignNameDraft.trim()} className="flex min-h-11 cursor-pointer items-center gap-2 rounded-xl bg-violet-500 px-4 text-sm font-medium text-white transition-colors hover:bg-violet-400 disabled:cursor-not-allowed disabled:opacity-50">
                {savingConfig ? <Loader2 size={15} className="animate-spin" /> : <Check size={15} />}
                Salvar
              </button>
            </div>
          </form>
        </div>
      ) : null}
      {worldBuilderOpen && snapshot ? <ImaginaiWorldBuilder campaignId={snapshot.campaign.id} onClose={() => setWorldBuilderOpen(false)} /> : null}
    </>
  );
}
