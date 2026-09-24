"use client";

import { useCallback, useEffect, useRef } from "react";

// Onde um arraste horizontal pertence ao CONTEÚDO, não à gaveta: blocos de código e
// tabelas que rolam de lado, campos de texto, sliders e diálogos.
const OWN_HORIZONTAL = 'pre, table, .md-table-wrap, .overflow-x-auto, input, textarea, select, [role="dialog"], [data-no-swipe]';
const SETTLE_MS = 260;

type Gesture = {
  x: number;
  y: number;
  t: number;
  mode: "open" | "close";
  locked: boolean;
  offset: number;
  width: number;
};

/**
 * Gaveta lateral que segue o dedo, como no ChatGPT: arrastar da esquerda para a
 * direita abre, da direita para a esquerda fecha. Durante o arraste o estilo é
 * aplicado direto no elemento (sem setState): re-renderizar a página do chat a cada
 * `touchmove` travaria o gesto. Só ao soltar o estado `open` é gravado.
 */
export function useDrawerSwipe({
  enabled,
  open,
  setOpen,
  rootRef,
  drawerRef,
  backdropRef,
}: {
  enabled: boolean;
  open: boolean;
  setOpen: (open: boolean) => void;
  rootRef: React.RefObject<HTMLElement | null>;
  drawerRef: React.RefObject<HTMLElement | null>;
  backdropRef: React.RefObject<HTMLElement | null>;
}) {
  const gesture = useRef<Gesture | null>(null);
  const settleTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => () => {
    if (settleTimer.current) clearTimeout(settleTimer.current);
  }, []);

  const paint = useCallback((offset: number, width: number, animate: boolean) => {
    const drawer = drawerRef.current;
    const backdrop = backdropRef.current;
    const transition = animate ? `transform ${SETTLE_MS}ms cubic-bezier(0.2, 0.8, 0.2, 1)` : "none";
    if (drawer) {
      drawer.style.transition = transition;
      drawer.style.transform = `translateX(${offset}px)`;
    }
    if (backdrop) {
      backdrop.style.transition = animate ? `opacity ${SETTLE_MS}ms ease` : "none";
      backdrop.style.opacity = String(Math.max(0, Math.min(1, 1 + offset / width)));
      backdrop.style.pointerEvents = "none";
    }
  }, [backdropRef, drawerRef]);

  // devolve o controle às classes do React (translate-x-0 / -translate-x-full)
  const release = useCallback(() => {
    for (const el of [drawerRef.current, backdropRef.current]) {
      if (!el) continue;
      el.style.transition = "";
      el.style.transform = "";
      el.style.opacity = "";
      el.style.pointerEvents = "";
    }
  }, [backdropRef, drawerRef]);

  const onTouchStart = useCallback((event: React.TouchEvent) => {
    if (!enabled || event.touches.length !== 1) return;
    const target = event.target as Element | null;
    // eventos de portais (modais) também sobem pela árvore do React: só vale o que
    // está de fato dentro da tela do chat
    if (!target || !rootRef.current?.contains(target) || target.closest(OWN_HORIZONTAL)) return;
    const touch = event.touches[0];
    gesture.current = {
      x: touch.clientX, y: touch.clientY, t: performance.now(),
      mode: open ? "close" : "open", locked: false, offset: 0,
      width: drawerRef.current?.offsetWidth || Math.min(window.innerWidth * 0.85, 340),
    };
  }, [drawerRef, enabled, open, rootRef]);

  const onTouchMove = useCallback((event: React.TouchEvent) => {
    const g = gesture.current;
    if (!g) return;
    const touch = event.touches[0];
    const dx = touch.clientX - g.x;
    const dy = touch.clientY - g.y;
    if (!g.locked) {
      if (Math.abs(dx) < 10 && Math.abs(dy) < 10) return;
      const horizontal = Math.abs(dx) > Math.abs(dy) * 1.2;
      const rightWay = g.mode === "open" ? dx > 0 : dx < 0;
      if (!horizontal || !rightWay) {
        gesture.current = null;        // rolagem vertical (ou sentido errado): não é nosso
        return;
      }
      g.locked = true;
      if (settleTimer.current) clearTimeout(settleTimer.current);
    }
    g.offset = g.mode === "open"
      ? Math.min(0, Math.max(-g.width, -g.width + dx))
      : Math.min(0, Math.max(-g.width, dx));
    paint(g.offset, g.width, false);
  }, [paint]);

  const onTouchEnd = useCallback((event: React.TouchEvent) => {
    const g = gesture.current;
    gesture.current = null;
    if (!g?.locked) return;
    const touch = event.changedTouches[0];
    const velocity = (touch.clientX - g.x) / Math.max(1, performance.now() - g.t); // px/ms
    // abre se passou da metade ou se foi um "flick" rápido no sentido do gesto
    const shouldOpen = g.mode === "open"
      ? g.offset > -g.width / 2 || velocity > 0.45
      : !(g.offset < -g.width / 3 || velocity < -0.45);
    paint(shouldOpen ? 0 : -g.width, g.width, true);
    setOpen(shouldOpen);
    settleTimer.current = setTimeout(release, SETTLE_MS + 40);
  }, [paint, release, setOpen]);

  const onTouchCancel = useCallback(() => {
    const g = gesture.current;
    gesture.current = null;
    if (!g?.locked) return;
    paint(open ? 0 : -g.width, g.width, true);
    settleTimer.current = setTimeout(release, SETTLE_MS + 40);
  }, [open, paint, release]);

  return { onTouchStart, onTouchMove, onTouchEnd, onTouchCancel };
}
