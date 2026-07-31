"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";

/** Ponteiro fino (mouse)? Em telas de toque, autoFocus abre o teclado na hora — o
 *  iOS desloca a viewport p/ revelar o input e o scroll interno de menus fica
 *  errático. Nesses casos o foco fica para quando o usuário tocar no campo. */
export const finePointer = () =>
  typeof window !== "undefined" && window.matchMedia("(hover: hover) and (pointer: fine)").matches;

/** Fecha o teclado virtual ao arrastar uma lista (padrão "dismiss on drag" do iOS):
 *  com o teclado aberto, o scroll de listas dentro de popovers trava/desalinha. */
export function dismissKeyboard() {
  const el = document.activeElement;
  if (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement) el.blur();
}

export function useClickOutside<T extends HTMLElement>(onClose: () => void) {
  const ref = useRef<T>(null);
  useEffect(() => {
    function handle(e: MouseEvent) {
      const t = e.target as Node;
      // ignora cliques dentro de menus em portal (AnchoredMenu) — eles vivem no
      // <body>, fora deste container, e fechá-los aqui desmontaria o menu antes
      // do onClick do item disparar.
      const el = t instanceof Element ? t : (t as ChildNode)?.parentElement ?? null;
      if (el?.closest("[data-portal-menu]")) return;
      if (ref.current && !ref.current.contains(t)) onClose();
    }
    function esc(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("mousedown", handle);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("mousedown", handle);
      document.removeEventListener("keydown", esc);
    };
  }, [onClose]);
  return ref;
}

export function Menu({
  children,
  onClose,
  className = "",
}: {
  children: React.ReactNode;
  onClose: () => void;
  className?: string;
}) {
  const ref = useClickOutside<HTMLDivElement>(onClose);
  return (
    <div
      ref={ref}
      className={`animate-pop z-50 min-w-[200px] rounded-xl border border-border bg-surface p-1.5 shadow-menu ${className}`}
    >
      {children}
    </div>
  );
}

/**
 * Menu ancorado renderizado via portal no <body>, com posição `fixed`.
 * Diferente do <Menu> posicionado por um wrapper `absolute`, este NÃO é
 * recortado por ancestrais com `overflow: hidden/auto` — resolve os menus de
 * "3 pontinhos" que apareciam cortados/dentro de listas roláveis. Reposiciona
 * em scroll/resize e vira para cima quando não há espaço abaixo.
 */
export function AnchoredMenu({
  anchorRef,
  onClose,
  align = "right",
  className = "",
  children,
}: {
  anchorRef: React.RefObject<HTMLElement | null>;
  onClose: () => void;
  align?: "left" | "right";
  className?: string;
  children: React.ReactNode;
}) {
  const menuRef = useRef<HTMLDivElement>(null);
  const [style, setStyle] = useState<React.CSSProperties>({ position: "fixed", visibility: "hidden", top: 0, left: 0 });

  useLayoutEffect(() => {
    function place() {
      const a = anchorRef.current;
      if (!a) return;
      const r = a.getBoundingClientRect();
      const gap = 6;
      const menuH = menuRef.current?.offsetHeight ?? 0;
      const menuW = menuRef.current?.offsetWidth ?? 0;
      const spaceBelow = window.innerHeight - r.bottom;
      const openUp = spaceBelow < menuH + gap && r.top > spaceBelow;
      const s: React.CSSProperties = { position: "fixed", visibility: "visible", zIndex: 80 };
      // vertical: abaixo por padrão; acima só se não couber embaixo
      if (openUp) s.bottom = window.innerHeight - r.top + gap;
      else s.top = r.bottom + gap;
      // horizontal: align="left" abre para a DIREITA (canto inferior-direito do
      // botão); "right" abre para a esquerda. Faz flip se estourar a viewport.
      if (align === "left") {
        if (r.left + menuW + 8 <= window.innerWidth) s.left = r.left;
        else s.right = Math.max(8, window.innerWidth - r.right);
      } else {
        if (r.right - menuW - 8 >= 0) s.right = window.innerWidth - r.right;
        else s.left = Math.max(8, r.left);
      }
      setStyle(s);
    }
    place();
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true); // captura scroll de qualquer ancestral
    return () => {
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
    };
  }, [anchorRef, align]);

  useEffect(() => {
    function down(e: MouseEvent) {
      const t = e.target as Node;
      if (menuRef.current?.contains(t) || anchorRef.current?.contains(t)) return;
      onClose();
    }
    function esc(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("mousedown", down);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("mousedown", down);
      document.removeEventListener("keydown", esc);
    };
  }, [anchorRef, onClose]);

  return createPortal(
    <div
      ref={menuRef}
      data-portal-menu
      style={style}
      className={`animate-pop min-w-[200px] rounded-xl border border-border bg-surface p-1.5 shadow-menu ${className}`}
    >
      {children}
    </div>,
    document.body,
  );
}

export function MenuItem({
  icon,
  children,
  onClick,
  danger,
}: {
  icon?: React.ReactNode;
  children: React.ReactNode;
  onClick?: () => void;
  danger?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      className={`flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-sm transition-colors hover:bg-hover ${
        danger ? "text-red-400 hover:text-red-300" : "text-ink"
      }`}
    >
      {icon && <span className="shrink-0 text-muted">{icon}</span>}
      <span className="truncate">{children}</span>
    </button>
  );
}

export function MenuDivider() {
  return <div className="my-1 h-px bg-border" />;
}

/** Editor de etiquetas: chips removíveis + input (Enter/vírgula adiciona). */
export function TagInput({
  tags,
  onChange,
  placeholder = "Adicionar etiqueta…",
}: {
  tags: string[];
  onChange: (t: string[]) => void;
  placeholder?: string;
}) {
  const [v, setV] = useState("");
  function add(raw: string) {
    const t = raw.trim().slice(0, 40);
    if (t && !tags.includes(t)) onChange([...tags, t].slice(0, 20));
    setV("");
  }
  return (
    <div className="flex flex-wrap items-center gap-1.5 rounded-lg border border-border bg-surface px-2 py-1.5">
      {tags.map((t) => (
        <span key={t} className="flex items-center gap-1 rounded-full bg-surface2 px-2 py-0.5 text-xs text-ink">
          {t}
          <button onClick={() => onChange(tags.filter((x) => x !== t))} className="text-muted transition-colors hover:text-red-400">
            <X size={11} />
          </button>
        </span>
      ))}
      <input
        value={v}
        onChange={(e) => setV(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === ",") { e.preventDefault(); add(v); }
          if (e.key === "Backspace" && !v && tags.length) onChange(tags.slice(0, -1));
        }}
        onBlur={() => v.trim() && add(v)}
        placeholder={tags.length ? "" : placeholder}
        className="min-w-[100px] flex-1 bg-transparent text-sm text-ink outline-none placeholder:text-muted"
      />
    </div>
  );
}

/** Toggle pequeno e elegante (accent quando ligado). */
export function Toggle({
  on,
  onChange,
  disabled,
}: {
  on: boolean;
  onChange: (v: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      disabled={disabled}
      onClick={() => onChange(!on)}
      className={`relative h-5 w-9 shrink-0 rounded-full transition-colors duration-200 disabled:opacity-40 ${
        on ? "bg-accent" : "bg-surface2"
      }`}
    >
      <span
        className={`absolute top-0.5 h-4 w-4 rounded-full bg-white shadow-sm transition-all duration-200 ${
          on ? "left-[18px]" : "left-0.5"
        }`}
      />
    </button>
  );
}

/** Bolinha "i" com dica: hover abre; clicar fixa (fecha ao clicar fora). Mesmo padrão
 *  usado nas Configurações — para descrições que não devem poluir a UI. */
export function InfoDot({ text }: { text: string }) {
  const [hover, setHover] = useState(false);
  const [pinned, setPinned] = useState(false);
  const ref = useRef<HTMLSpanElement>(null);
  const open = hover || pinned;
  useEffect(() => {
    if (!pinned) return;
    const onDoc = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setPinned(false); };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [pinned]);
  return (
    <span ref={ref} className="relative inline-flex">
      <span
        role="button"
        aria-label={text}
        tabIndex={0}
        onMouseEnter={() => setHover(true)}
        onMouseLeave={() => setHover(false)}
        onFocus={() => setHover(true)}
        onBlur={() => setHover(false)}
        onClick={(e) => { e.stopPropagation(); setPinned((v) => !v); }}
        onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setPinned((v) => !v); } }}
        className="inline-flex h-4 w-4 shrink-0 cursor-help items-center justify-center rounded-full border border-border text-[10px] font-semibold leading-none text-muted transition-colors hover:border-accent hover:text-ink"
      >
        i
      </span>
      {open && (
        <span
          role="tooltip"
          className="absolute left-0 top-6 z-50 w-60 max-w-[min(80vw,15rem)] rounded-lg border border-border bg-surface px-3 py-2 text-left text-xs font-normal leading-snug text-ink-soft shadow-lg"
        >
          {text}
        </span>
      )}
    </span>
  );
}
