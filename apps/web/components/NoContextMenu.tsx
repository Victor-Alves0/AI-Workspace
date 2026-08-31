"use client";

import { useEffect } from "react";

/** Desliga o menu de contexto do navegador (botão direito) na aplicação inteira,
 *  para a UI se comportar como um aplicativo e não como uma página.
 *
 *  A exceção são os campos EDITÁVEIS. Sem ela o botão direito deixaria de oferecer
 *  "Colar" e as sugestões do corretor ortográfico — num app de chat, onde colar
 *  texto no composer é ação de rotina, isso trocaria um ganho estético por uma
 *  perda real de uso (e no celular não há Ctrl+V). O `.closest` cobre também os
 *  editores que renderizam `contenteditable` (código, notas).
 *
 *  Fica no listener do documento (fase de captura) em vez de `onContextMenu` por
 *  componente: assim vale para tudo — inclusive portais, iframes de preview não
 *  (esses têm documento próprio) e conteúdo renderizado por bibliotecas. */
export default function NoContextMenu() {
  useEffect(() => {
    function onContextMenu(e: MouseEvent) {
      const alvo = e.target as HTMLElement | null;
      if (alvo?.closest?.("input, textarea, [contenteditable]:not([contenteditable='false'])")) {
        return; // campo editável: deixa o menu nativo (Colar, corretor)
      }
      e.preventDefault();
    }
    document.addEventListener("contextmenu", onContextMenu);
    return () => document.removeEventListener("contextmenu", onContextMenu);
  }, []);
  return null;
}
