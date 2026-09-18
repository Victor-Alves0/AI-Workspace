/** Contratos do mini app Imaginai (espelham os schemas do backend). */

export type ImaginaiEntity = {
  id: string;
  kind: string;
  key: string;
  name: string;
  description: string;
  location_id: string | null;
  owner_entity_id: string | null;
  state: Record<string, unknown>;
  active: boolean;
};

export type ImaginaiSnapshot = {
  campaign: {
    id: string;
    chat_id: string;
    name: string;
    system_key: string;
    system_version: string;
    status: string;
    world_tick: number;
    settings?: {
      narration_style?: "balanced" | "cinematic" | "gritty";
      difficulty?: "story" | "balanced" | "challenging";
      premise?: string;
      opening_scene?: string;
      [key: string]: unknown;
    };
  };
  character: ImaginaiEntity | null;
  location: ImaginaiEntity | null;
  /** combate em andamento (ou o último, encerrado); null fora de combate */
  encounter?: ImaginaiEncounter | null;
};

/** Combate como o JOGADOR o vê: a própria vida em números, a do inimigo como estado. */
export type ImaginaiEncounter = {
  active: boolean;
  round: number;
  outcome: "victory" | "escaped" | "defeat" | "stalled" | null;
  order: {
    id: string;
    name: string;
    side: "player" | "hostile";
    initiative: number;
    current: boolean;
    health?: "ileso" | "ferido" | "gravemente ferido" | "caído" | "desconhecido";
    hp?: number;
    hp_max?: number;
  }[];
};

export type ImaginaiSystemDefinition = {
  key: string;
  name: string;
  version: string;
  inventory: {
    weight: { supported: boolean; default_enabled: boolean; unit: string };
    currency_weight: { supported: boolean; default_enabled: boolean };
    currencies: { key: string; label: string; name: string; weight: number }[];
    equipment_slots: string[];
  };
  sheet: {
    summary: { key: string; label: string }[];
    attributes: { key: string; label: string; short: string; skills: string[] }[];
    skills: Record<string, string>;
  };
};

export type ImaginaiJournalEntry = {
  id: string;
  title: string;
  content: string;
  tags: string[];
  pinned: boolean;
  created_at: string;
  updated_at: string;
};

export type ImaginaiEvent = {
  id: string;
  sequence: number;
  world_tick: number;
  event_type: string;
  actor_id: string | null;
  target_id: string | null;
  location_id: string | null;
  payload: Record<string, unknown>;
  visibility: string;
  created_at: string;
};

export type ImaginaiInventory = {
  items: {
    id: string;
    name: string;
    description: string;
    quantity: number;
    weight: number;
    equipped: boolean;
    slot: string | null;
    container: string | null;
    charges: number | null;
  }[];
  currencies: Record<string, number>;
  total_weight: number;
  weight?: {
    enabled: boolean;
    currency_enabled: boolean;
    items: number;
    currencies: number;
    total: number;
    unit: string;
  };
};

export type ImaginaiCodexResult = {
  id: string;
  result_type: "entity" | "lore";
  kind: string;
  name: string;
  knowledge: "aware" | "rumor" | "known";
  description: string | Record<string, unknown> | null;
  subject?: string | null;
  confidence?: number;
  redacted: string[];
};

export type ImaginaiSpell = {
  key: string;
  name: string;
  level: number;
  school: string;
  prepared: boolean;
  known: boolean;
  ritual: boolean;
  concentration: boolean;
  casting_time: string;
  range: string;
  duration: string;
  components: unknown;
  description: string;
};

export type ImaginaiSpells = {
  spells: ImaginaiSpell[];
  slots: Record<string, { current: number; max: number }>;
  spellcasting_ability: string | null;
  attack_modifier: number;
  save_dc: number;
};

export type ImaginaiMap = {
  locations: { id: string; name: string; description: string; current: boolean; x: number | null; y: number | null; index: number }[];
  routes: { from: string; to: string; label: string }[];
};

export type ImaginaiWorldEntity = ImaginaiEntity & { private_notes: string };
