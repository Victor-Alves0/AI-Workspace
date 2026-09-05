"use client";

import { useEffect } from "react";
import { isDesktop } from "@/lib/desktop";

/** Desliga o menu de contexto do navegador (botão direito) SÓ dentro do app desktop,
 *  para o shell Tauri se comportar como um aplicativo e não como uma página.
 *
 *  NO NAVEGADOR o menu nativo FICA. Ali ele não é "cara de página": é a única via para
 *  copiar/colar, abrir link em nova aba, salvar imagem, inspecionar, traduzir e usar as
 *  extensões — coisas que o app não reimplementa. Suprimi-lo trocava um ganho estético
 *  por uma perda real de uso (e no celular o toque longo é o próprio menu).
 *
 *  A checagem roda no efeito (nunca no render) porque `__AIW_DESKTOP__` é injetado pelo
 *  shell no `window` — no SSR ele não existe e o HTML precisa ser o mesmo dos dois lados.
 *
 *  Mesmo no desktop os campos EDITÁVEIS são exceção: sem eles o botão direito deixaria de
 *  oferecer "Colar" e as sugestões do corretor ortográfico. O `.closest` cobre também os
 *  editores que renderizam `contenteditable` (código, notas).
 *
 *  Fica no listener do documento (fase de captura) em vez de `onContextMenu` por
 *  componente: assim vale para tudo — inclusive portais e conteúdo renderizado por
 *  bibliotecas (iframes de preview não: têm documento próprio). */
export default function NoContextMenu() {
  useEffect(() => {
    if (!isDesktop()) return; // navegador: menu nativo intacto
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
