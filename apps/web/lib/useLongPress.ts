"use client";

import { useCallback, useEffect, useRef } from "react";

/**
 * Toque longo (≈450ms) — o "clique direito" do celular, como no ChatGPT: segurar um
 * chat abre o menu dele. Só dispara com TOQUE (mouse segue com hover/botões), e
 * cancela se o dedo andar (rolagem) ou soltar antes. `consumeClick` diz ao `onClick`
 * do mesmo elemento que aquele toque já virou toque longo — senão, ao soltar, o chat
 * também seria aberto.
 */
export function useLongPress(onLongPress: () => void, delay = 450) {
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const start = useRef<{ x: number; y: number } | null>(null);
  const fired = useRef(false);

  const cancel = useCallback(() => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
    start.current = null;
  }, []);

  useEffect(() => cancel, [cancel]);

  const onPointerDown = useCallback((event: React.PointerEvent) => {
    if (event.pointerType !== "touch") return;
    fired.current = false;
    start.current = { x: event.clientX, y: event.clientY };
    timer.current = setTimeout(() => {
      fired.current = true;
      timer.current = null;
      navigator.vibrate?.(10);
      onLongPress();
    }, delay);
  }, [delay, onLongPress]);

  const onPointerMove = useCallback((event: React.PointerEvent) => {
    const origin = start.current;
    if (!origin) return;
    // o dedo andou: é rolagem, não toque longo
    if (Math.abs(event.clientX - origin.x) > 8 || Math.abs(event.clientY - origin.y) > 8) cancel();
  }, [cancel]);

  // true = este clique veio do toque longo e deve ser ignorado
  const consumeClick = useCallback(() => {
    if (!fired.current) return false;
    fired.current = false;
    return true;
  }, []);

  return {
    handlers: {
      onPointerDown,
      onPointerMove,
      onPointerUp: cancel,
      onPointerCancel: cancel,
      // o menu nativo do iOS/Android (copiar/compartilhar) não compete com o nosso
      onContextMenu: (event: React.MouseEvent) => {
        if (fired.current || timer.current) event.preventDefault();
      },
    },
    consumeClick,
  };
}
