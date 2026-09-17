"use client";

import { BookOpen, Map as MapIcon, Package, ScrollText, Sparkles, X } from "lucide-react";
import type { LucideIcon } from "lucide-react";

export const DND_CHARACTER_SECTIONS = [
  { id: "inventory", label: "Inventário", icon: Package },
  { id: "spells", label: "Magias", icon: Sparkles },
  { id: "sheet", label: "Ficha", icon: ScrollText },
] as const;

export const DND_WORLD_SECTIONS = [
  { id: "journal", label: "Diário", icon: BookOpen },
  { id: "map", label: "Mapa", icon: MapIcon },
  { id: "codex", label: "Codex", icon: ScrollText },
] as const;

export type CharacterSection = (typeof DND_CHARACTER_SECTIONS)[number]["id"];

export type WorldSection = (typeof DND_WORLD_SECTIONS)[number]["id"];

/**
 * Docks do primeiro sistema do Imaginai. A composição em dois painéis permite que
 * sistemas futuros forneçam seus próprios campos sem alterar a coluna central.
 */

export function ImaginaiFeatureStatus({ children, error = false }: { children: React.ReactNode; error?: boolean }) {
  return (
    <div className={`flex min-h-36 items-center justify-center px-4 text-center text-xs leading-5 ${error ? "text-rose-400" : "text-muted"}`}>
      {children}
    </div>
  );
}

export function ImaginaiEmptyFeature({
  icon: Icon,
  title,
  text,
}: {
  icon: LucideIcon;
  title: string;
  text: string;
}) {
  return (
    <div className="imaginai-feature-scroll flex min-h-52 flex-col items-center justify-center px-4 text-center">
      <span className="mb-3 flex h-11 w-11 items-center justify-center rounded-xl border border-violet-400/20 bg-violet-500/10 text-violet-300">
        <Icon size={20} />
      </span>
      <h3 className="text-sm font-semibold text-ink">{title}</h3>
      <p className="mt-1 max-w-52 text-xs leading-5 text-muted">{text}</p>
    </div>
  );
}

export function ImaginaiSheetHead({ label, onClose }: { label: string; onClose: () => void }) {
  return (
    <div className="imaginai-feature-head">
      <p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-violet-300">{label}</p>
      <button type="button" onClick={onClose} title="Fechar" aria-label="Fechar aba" className="-mr-1.5 flex h-7 w-7 cursor-pointer items-center justify-center rounded-lg text-muted transition-colors hover:bg-hover hover:text-ink">
        <X size={15} />
      </button>
    </div>
  );
}

export function numericState(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

export function abilityScore(value: unknown): number {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (value && typeof value === "object") {
    return numericState((value as Record<string, unknown>).score, 10);
  }
  return 10;
}

export function signed(value: number): string {
  return value >= 0 ? `+${value}` : String(value);
}
