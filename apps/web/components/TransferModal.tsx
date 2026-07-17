"use client";

import { useMemo, useState } from "react";
import {
  ChevronsLeft,
  ChevronsRight,
  ChevronLeft,
  ChevronRight,
  Pin,
  Search,
  Wrench,
  X,
} from "lucide-react";
import { useClickOutside } from "./ui";

export interface TransferItem {
  key: string;
  label: string;
  sublabel?: string;
  group?: string;
  /** item embutido do sistema (mostra ícone de chave + tooltip "AI Workspace") */
  system?: boolean;
  /** título do ícone de chave (default "AI Workspace"; ex.: "Nativa do modelo") */
  iconTitle?: string;
}

/**
 * Uma coluna da lista. Definida em ESCOPO DE MÓDULO (não dentro do TransferModal):
 * se ficasse aninhada, cada re-render criaria um novo tipo de componente, o React
 * remontaria a `<div overflow-y-auto>` e o scroll voltaria ao topo a cada clique.
 */
function TransferPane({
  heading,
  list,
  side,
  marks,
  onToggleMark,
  onMove,
  onTogglePin,
  pinnedKeys,
  pinHint,
  hasQuery,
}: {
  heading: string;
  list: TransferItem[];
  side: "left" | "right";
  marks: Set<string>;
  onToggleMark: (side: "left" | "right", key: string) => void;
  onMove: (side: "left" | "right", key: string) => void;
  onTogglePin?: (key: string) => void;
  pinnedKeys?: string[];
  pinHint?: string;
  hasQuery: boolean;
}) {
  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-xl border border-border bg-bg">
      <div className="flex items-center justify-between border-b border-border px-3 py-2 text-xs font-medium uppercase tracking-wider text-muted">
        <span>{heading}</span>
        <span className="rounded-full bg-surface2 px-2 py-0.5 text-[10px] normal-case text-muted">
          {list.length}
        </span>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-1.5">
        {list.map((it) => {
          const marked = marks.has(it.key);
          const showPin = side === "right" && !!onTogglePin;
          const pinned = showPin && (pinnedKeys ?? []).includes(it.key);
          return (
            <div
              key={it.key}
              className={`flex items-center gap-1 rounded-lg transition-colors ${
                marked ? "bg-accent/15 ring-1 ring-accent/40" : "hover:bg-hover"
              }`}
            >
              <button
                onClick={() => onToggleMark(side, it.key)}
                onDoubleClick={() => onMove(side, it.key)}
                className="flex min-w-0 flex-1 flex-col px-2.5 py-1.5 text-left"
              >
                <span className="flex items-center gap-1.5">
                  {it.system && (
                    <span title={it.iconTitle ?? "AI Workspace"} className="shrink-0 text-muted">
                      <Wrench size={12} />
                    </span>
                  )}
                  {pinned && <Pin size={11} className="shrink-0 fill-accent-hover text-accent-hover" />}
                  <span className="truncate text-sm text-ink">{it.label}</span>
                </span>
                {it.sublabel && <span className="truncate font-mono text-[11px] text-muted">{it.sublabel}</span>}
              </button>
              {showPin && (
                <button
                  onClick={() => onTogglePin!(it.key)}
                  title={pinned
                    ? "Fixada: sempre visível ao modelo, sem round-trip de busca. Clique p/ desafixar."
                    : (pinHint || "Fixar: vira ferramenta de 1ª classe (o modelo chama direto, sem busca).")}
                  className={`mr-1 shrink-0 rounded-md p-1 transition-colors ${
                    pinned ? "text-accent-hover" : "text-muted hover:text-ink"
                  }`}
                >
                  <Pin size={13} className={pinned ? "fill-accent-hover" : ""} />
                </button>
              )}
            </div>
          );
        })}
        {list.length === 0 && (
          <p className="px-2.5 py-6 text-center text-xs text-muted">
            {hasQuery ? "Nada encontrado." : side === "left" ? "Tudo selecionado." : "Nada selecionado."}
          </p>
        )}
      </div>
    </div>
  );
}

/**
 * Popup de transferência (dual-list): itens disponíveis à esquerda, selecionados
 * à direita. Clicar destaca; as setas movem os destacados (ou clique duplo move
 * na hora). Busca no topo filtra os dois lados. Reutilizável para qualquer
 * seleção de conjunto (ferramentas do modelo, no futuro tools de sistema, etc.).
 */
export default function TransferModal({
  title,
  items,
  selected,
  onChange,
  onClose,
  availableLabel = "Disponíveis",
  selectedLabel = "Selecionadas",
  searchPlaceholder = "Buscar…",
  pinnedKeys,
  onTogglePin,
  pinHint,
}: {
  title: string;
  items: TransferItem[];
  selected: string[];
  onChange: (keys: string[]) => void;
  onClose: () => void;
  availableLabel?: string;
  selectedLabel?: string;
  searchPlaceholder?: string;
  /** quando definido, itens ATIVADOS ganham um botão de fixar (pin) */
  pinnedKeys?: string[];
  onTogglePin?: (key: string) => void;
  pinHint?: string;
}) {
  const ref = useClickOutside<HTMLDivElement>(onClose);
  const [q, setQ] = useState("");
  const [markLeft, setMarkLeft] = useState<Set<string>>(new Set());
  const [markRight, setMarkRight] = useState<Set<string>>(new Set());

  const selectedSet = useMemo(() => new Set(selected), [selected]);
  const byKey = useMemo(() => new Map(items.map((i) => [i.key, i])), [items]);

  const filter = (list: TransferItem[]) => {
    const f = q.trim().toLowerCase();
    if (!f) return list;
    return list.filter(
      (i) => i.label.toLowerCase().includes(f) || (i.sublabel ?? "").toLowerCase().includes(f),
    );
  };

  const available = filter(items.filter((i) => !selectedSet.has(i.key)));
  const chosen = filter(
    selected.map((k) => byKey.get(k)).filter((x): x is TransferItem => !!x),
  );

  function moveToSelected(keys: string[]) {
    if (!keys.length) return;
    const add = keys.filter((k) => !selectedSet.has(k));
    onChange([...selected, ...add]);
    setMarkLeft(new Set());
  }
  function moveToAvailable(keys: string[]) {
    if (!keys.length) return;
    const rm = new Set(keys);
    onChange(selected.filter((k) => !rm.has(k)));
    setMarkRight(new Set());
  }

  function toggleMark(side: "left" | "right", key: string) {
    const [set, setSet] = side === "left" ? [markLeft, setMarkLeft] : [markRight, setMarkRight];
    const next = new Set(set);
    next.has(key) ? next.delete(key) : next.add(key);
    setSet(next as Set<string>);
  }

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/60 px-3 pt-[calc(1rem+env(safe-area-inset-top))] pb-[calc(1rem+env(safe-area-inset-bottom))] backdrop-blur-sm md:px-4">
      <div ref={ref} className="animate-pop flex h-full max-h-[640px] w-full max-w-3xl flex-col overflow-hidden rounded-2xl border border-border bg-surface shadow-modal md:h-[560px]">
        {/* header + busca */}
        <div className="flex items-center justify-between gap-3 border-b border-border px-5 py-3">
          <h2 className="text-base font-semibold tracking-tight text-ink">{title}</h2>
          <button onClick={onClose} className="rounded-lg p-1.5 text-muted transition-colors hover:bg-hover hover:text-ink">
            <X size={18} />
          </button>
        </div>
        <div className="flex items-center gap-2 border-b border-border px-5 py-2.5">
          <Search size={16} className="text-muted" />
          {/* autoFocus só com mouse: no celular abriria o teclado por cima da lista */}
          <input
            autoFocus={typeof window !== "undefined" && !window.matchMedia?.("(pointer: coarse)").matches}
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder={searchPlaceholder}
            className="w-full bg-transparent text-sm text-ink outline-none placeholder:text-muted"
          />
        </div>

        {/* dual list — mobile: colunas empilhadas com as setas na horizontal */}
        <div className="flex min-h-0 flex-1 flex-col items-stretch gap-2.5 p-3 md:flex-row md:gap-3 md:p-5">
          <TransferPane
            heading={availableLabel}
            list={available}
            side="left"
            marks={markLeft}
            onToggleMark={toggleMark}
            onMove={(side, key) => (side === "left" ? moveToSelected([key]) : moveToAvailable([key]))}
            hasQuery={!!q.trim()}
          />

          {/* setas (mobile: giradas 90° — adicionar desce, remover sobe) */}
          <div className="flex shrink-0 flex-row items-center justify-center gap-2 md:flex-col">
            <button
              onClick={() => moveToSelected(available.map((i) => i.key))}
              title="Adicionar todos"
              className="rounded-lg border border-border p-1.5 text-muted transition-colors hover:bg-hover hover:text-ink"
            >
              <ChevronsRight size={16} className="rotate-90 md:rotate-0" />
            </button>
            <button
              onClick={() => moveToSelected([...markLeft])}
              title="Adicionar selecionados"
              className="rounded-lg border border-border bg-surface2 p-1.5 text-ink transition-colors hover:bg-accent hover:text-white"
            >
              <ChevronRight size={16} className="rotate-90 md:rotate-0" />
            </button>
            <button
              onClick={() => moveToAvailable([...markRight])}
              title="Remover selecionados"
              className="rounded-lg border border-border bg-surface2 p-1.5 text-ink transition-colors hover:bg-accent hover:text-white"
            >
              <ChevronLeft size={16} className="rotate-90 md:rotate-0" />
            </button>
            <button
              onClick={() => moveToAvailable(chosen.map((i) => i.key))}
              title="Remover todos"
              className="rounded-lg border border-border p-1.5 text-muted transition-colors hover:bg-hover hover:text-ink"
            >
              <ChevronsLeft size={16} className="rotate-90 md:rotate-0" />
            </button>
          </div>

          <TransferPane
            heading={selectedLabel}
            list={chosen}
            side="right"
            marks={markRight}
            onToggleMark={toggleMark}
            onMove={(side, key) => (side === "left" ? moveToSelected([key]) : moveToAvailable([key]))}
            onTogglePin={onTogglePin}
            pinnedKeys={pinnedKeys}
            pinHint={pinHint}
            hasQuery={!!q.trim()}
          />
        </div>

        <div className="flex items-center justify-between border-t border-border px-5 py-3 text-xs text-muted">
          <span className="hidden md:inline">Clique para marcar • clique duplo para mover • use as setas</span>
          <span className="md:hidden">Toque para marcar • use as setas</span>
          <button
            onClick={onClose}
            className="rounded-full bg-accent px-5 py-1.5 text-sm font-medium text-white transition-colors hover:bg-accent-hover"
          >
            Concluído
          </button>
        </div>
      </div>
    </div>
  );
}
