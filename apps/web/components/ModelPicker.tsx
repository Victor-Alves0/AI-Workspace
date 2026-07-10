"use client";

import { useMemo, useRef, useState } from "react";
import {
  Check,
  ChevronDown,
  Copy,
  Cpu,
  Link2,
  MoreHorizontal,
  Pencil,
  Pin,
  PinOff,
  Search,
  Star,
} from "lucide-react";
import type { Model, ModelConfig } from "@/lib/types";
import { AnchoredMenu, MenuItem, useClickOutside } from "./ui";

interface Row {
  key: string; // "ext:<id>" | "custom:<id>"
  name: string;
  avatar?: string | null;
  external: boolean;
  modelId: string;
  local?: boolean; // modelo local do Ollama
  custom?: ModelConfig;
}

export default function ModelPicker({
  label,
  avatar,
  models,
  custom,
  value,
  activeCustomId,
  favorites = [],
  pinned = [],
  onSelectExternal,
  onSelectCustom,
  onToggleFavorite,
  onTogglePin,
  onEditModel,
}: {
  label: string;
  /** foto do modelo ativo, mostrada à esquerda do nome (opcional em Interface → Chat) */
  avatar?: string | null;
  models: Model[];
  custom: ModelConfig[];
  value: string;
  activeCustomId: string | null;
  favorites?: string[];
  pinned?: string[];
  onSelectExternal: (id: string) => void;
  onSelectCustom: (mc: ModelConfig) => void;
  onToggleFavorite?: (key: string) => void;
  onTogglePin?: (key: string) => void;
  /** abre o editor do modelo custom no Espaço de Trabalho */
  onEditModel?: (mc: ModelConfig) => void;
}) {
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<"favorites" | "all" | "openrouter" | "custom" | "local">("all");
  const [q, setQ] = useState("");
  const [itemMenu, setItemMenu] = useState<string | null>(null);
  const itemBtnRef = useRef<HTMLButtonElement>(null);
  const ref = useClickOutside<HTMLDivElement>(() => {
    setOpen(false);
    setItemMenu(null);
  });

  const favSet = useMemo(() => new Set(favorites), [favorites]);
  const pinSet = useMemo(() => new Set(pinned), [pinned]);

  const rows: Row[] = useMemo(() => {
    const customRows: Row[] = custom.map((c) => ({
      key: `custom:${c.id}`,
      name: c.name,
      avatar: c.avatar_url,
      external: false,
      modelId: c.base_model,
      custom: c,
    }));
    const extRows: Row[] = models
      .filter((m) => !m.local)
      .map((m) => ({ key: `ext:${m.id}`, name: m.name, external: true, modelId: m.id }));
    const localRows: Row[] = models
      .filter((m) => m.local)
      .map((m) => ({ key: `ext:${m.id}`, name: m.name, external: true, local: true, modelId: m.id }));
    const all = [...customRows, ...extRows, ...localRows];
    const base =
      tab === "favorites"
        ? all.filter((r) => favSet.has(r.key))
        : tab === "openrouter"
          ? extRows
          : tab === "custom"
            ? customRows
            : tab === "local"
              ? localRows
              : all;
    const f = q.trim().toLowerCase();
    return f ? base.filter((r) => r.name.toLowerCase().includes(f)) : base;
  }, [custom, models, tab, q, favSet]);

  function isSelected(r: Row) {
    return r.external ? !activeCustomId && r.modelId === value : activeCustomId === r.custom?.id;
  }

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-1.5 rounded-lg px-2 py-1 text-lg font-semibold tracking-tight text-ink transition-colors hover:bg-hover"
      >
        {avatar && (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={avatar} alt="" className="h-6 w-6 shrink-0 rounded-full object-cover" />
        )}
        <span className="max-w-[140px] truncate sm:max-w-[240px]">{label || "Selecionar modelo"}</span>
        <ChevronDown size={18} className="shrink-0 text-muted" />
      </button>

      {open && (
        <div className="absolute left-0 top-11 z-50 w-[380px] max-w-[calc(100vw-1.5rem)] overflow-hidden rounded-2xl border border-border bg-surface shadow-menu animate-pop">
          <div className="flex items-center gap-2 border-b border-border px-3 py-2.5">
            <Search size={16} className="text-muted" />
            <input
              autoFocus
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Pesquisar um modelo"
              className="w-full bg-transparent text-sm text-ink outline-none placeholder:text-muted"
            />
          </div>
          <div className="flex items-center gap-1 border-b border-border px-2 py-1.5 text-sm">
            <button
              onClick={() => setTab("favorites")}
              title="Favoritos"
              className={`flex items-center rounded-lg px-2 py-1 transition-colors ${
                tab === "favorites" ? "bg-surface2 text-accent-hover" : "text-muted hover:text-ink-soft"
              }`}
            >
              <Star size={15} className={tab === "favorites" ? "fill-accent-hover" : ""} />
            </button>
            {([["all", "Tudo"], ["openrouter", "Openrouter"], ["custom", "Custom"], ["local", "Local"]] as const).map(([key, lbl]) => (
              <button
                key={key}
                onClick={() => setTab(key)}
                className={`rounded-lg px-3 py-1 transition-colors ${
                  tab === key ? "bg-surface2 font-medium text-ink" : "text-muted hover:text-ink-soft"
                }`}
              >
                {lbl}
              </button>
            ))}
          </div>
          <div className="max-h-80 overflow-y-auto p-1.5">
            {rows.map((r) => (
              <div key={r.key} className="group relative flex items-center gap-2 rounded-lg px-2 py-2 transition-colors hover:bg-hover">
                <button
                  onClick={() => {
                    if (r.external) onSelectExternal(r.modelId);
                    else if (r.custom) onSelectCustom(r.custom);
                    setOpen(false);
                  }}
                  className="flex flex-1 items-center gap-2.5 truncate text-left"
                >
                  {r.avatar ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={r.avatar} alt="" className="h-6 w-6 shrink-0 rounded-full object-cover" />
                  ) : (
                    <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-surface2 text-[10px] text-ink">
                      {r.name[0]?.toUpperCase()}
                    </span>
                  )}
                  <span className="truncate text-sm text-ink">{r.name}</span>
                  {favSet.has(r.key) && <Star size={12} className="shrink-0 fill-accent-hover text-accent-hover" />}
                  {pinSet.has(r.key) && <Pin size={11} className="shrink-0 text-muted" />}
                  {r.local ? (
                    <Cpu size={13} className="shrink-0 text-accent-hover" />
                  ) : r.external ? (
                    <Link2 size={13} className="shrink-0 text-muted" />
                  ) : null}
                </button>

                {isSelected(r) && <Check size={16} className="shrink-0 text-accent" />}

                <button
                  ref={itemMenu === r.key ? itemBtnRef : undefined}
                  onClick={() => setItemMenu(itemMenu === r.key ? null : r.key)}
                  className={`shrink-0 rounded p-0.5 text-muted hover:bg-surface2 hover:text-ink ${
                    itemMenu === r.key ? "opacity-100" : "opacity-0 group-hover:opacity-100"
                  }`}
                >
                  <MoreHorizontal size={16} />
                </button>

                {itemMenu === r.key && (
                  <AnchoredMenu anchorRef={itemBtnRef} onClose={() => setItemMenu(null)} align="left">
                    <MenuItem
                      icon={<Star size={15} className={favSet.has(r.key) ? "fill-accent-hover text-accent-hover" : ""} />}
                      onClick={() => { onToggleFavorite?.(r.key); setItemMenu(null); }}
                    >
                      {favSet.has(r.key) ? "Remover dos Favoritos" : "Adicionar aos Favoritos"}
                    </MenuItem>
                    <MenuItem
                      icon={pinSet.has(r.key) ? <PinOff size={15} /> : <Pin size={15} />}
                      onClick={() => { onTogglePin?.(r.key); setItemMenu(null); }}
                    >
                      {pinSet.has(r.key) ? "Desafixar da barra lateral" : "Fixar na barra lateral"}
                    </MenuItem>
                    {r.custom && (
                      <>
                        <MenuItem icon={<Pencil size={15} />} onClick={() => { onEditModel?.(r.custom!); setItemMenu(null); setOpen(false); }}>
                          Editar
                        </MenuItem>
                        <MenuItem
                          icon={<Copy size={15} />}
                          onClick={() => {
                            navigator.clipboard?.writeText(`${location.origin}/chat?model=${r.custom!.id}`);
                            setItemMenu(null);
                          }}
                        >
                          Copiar Link
                        </MenuItem>
                      </>
                    )}
                  </AnchoredMenu>
                )}
              </div>
            ))}
            {rows.length === 0 && (
              <p className="px-3 py-6 text-center text-sm text-muted">
                {tab === "local" ? "Nenhum modelo local. Configure o Ollama em Configurações → Conexões." : "Nenhum modelo."}
              </p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
