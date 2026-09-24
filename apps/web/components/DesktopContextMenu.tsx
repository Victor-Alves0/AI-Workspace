"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Clipboard, Copy, ExternalLink, Link2, Scissors, TextSelect } from "lucide-react";
import { copyText } from "@/lib/clipboard";
import { isDesktop, openExternal, readClipboard } from "@/lib/desktop";

type Editavel = HTMLInputElement | HTMLTextAreaElement | HTMLElement;

interface Alvo {
  x: number;
  y: number;
  selecao: string;
  editavel: Editavel | null;
  // seleção do campo no momento do clique (o menu tira o foco dele)
  inicio: number | null;
  fim: number | null;
  range: Range | null;
  link: string | null;
  imagem: string | null;
}

const CAMPO = "input, textarea, [contenteditable]:not([contenteditable='false'])";
const TIPOS_DE_TEXTO = new Set(["text", "search", "url", "email", "tel", "password", "number", ""]);

function ehInput(el: Editavel): el is HTMLInputElement | HTMLTextAreaElement {
  return el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement;
}

function httpUrl(u: string | null | undefined): string | null {
  if (!u) return null;
  try {
    const url = new URL(u, window.location.href);
    return url.protocol === "http:" || url.protocol === "https:" ? url.href : null;
  } catch {
    return null;
  }
}

/** Menu do botão direito do APP DESKTOP: o do webview tem cara de navegador (voltar,
 *  inspecionar…), então no Tauri ele sai e entra este, só com o que faz sentido num
 *  aplicativo: recortar/copiar/colar/selecionar tudo em campos, copiar a seleção e
 *  abrir links e imagens no navegador do sistema.
 *
 *  NO NAVEGADOR nada muda: o menu nativo fica (é por ele que se traduz, salva imagem,
 *  usa extensões…). O listener é do documento, em captura, para valer também em
 *  portais e conteúdo de bibliotecas (iframes de preview têm documento próprio). */
export default function DesktopContextMenu() {
  const [alvo, setAlvo] = useState<Alvo | null>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<{ left: number; top: number } | null>(null);

  useEffect(() => {
    if (!isDesktop()) return;
    function onContextMenu(e: MouseEvent) {
      e.preventDefault();
      const t = e.target as HTMLElement | null;
      let editavel = (t?.closest?.(CAMPO) as Editavel | null) ?? null;
      if (editavel instanceof HTMLInputElement && !TIPOS_DE_TEXTO.has(editavel.type)) editavel = null;
      let inicio: number | null = null;
      let fim: number | null = null;
      let selecao = "";
      let range: Range | null = null;
      if (editavel && ehInput(editavel)) {
        inicio = editavel.selectionStart;
        fim = editavel.selectionEnd;
        if (inicio != null && fim != null && editavel.type !== "password") selecao = editavel.value.slice(inicio, fim);
      } else {
        const sel = window.getSelection();
        selecao = sel?.toString() ?? "";
        range = sel && sel.rangeCount ? sel.getRangeAt(0).cloneRange() : null;
      }
      const a = t?.closest?.("a[href]") as HTMLAnchorElement | null;
      const img = t?.closest?.("img") as HTMLImageElement | null;
      const info: Alvo = {
        x: e.clientX, y: e.clientY, selecao, editavel, inicio, fim, range,
        link: httpUrl(a?.getAttribute("href")), imagem: httpUrl(img?.currentSrc || img?.src),
      };
      // nada a oferecer (clique em área vazia, sem seleção): nenhum menu
      if (!info.editavel && !info.selecao && !info.link && !info.imagem) {
        setAlvo(null);
        return;
      }
      setAlvo(info);
    }
    document.addEventListener("contextmenu", onContextMenu, true);
    return () => document.removeEventListener("contextmenu", onContextMenu, true);
  }, []);

  const fechar = useCallback(() => { setAlvo(null); setPos(null); }, []);

  useEffect(() => {
    if (!alvo) return;
    const fora = (e: MouseEvent) => { if (!menuRef.current?.contains(e.target as Node)) fechar(); };
    const tecla = (e: KeyboardEvent) => { if (e.key === "Escape") fechar(); };
    document.addEventListener("mousedown", fora, true);
    document.addEventListener("keydown", tecla, true);
    window.addEventListener("blur", fechar);
    window.addEventListener("resize", fechar);
    document.addEventListener("scroll", fechar, true);
    return () => {
      document.removeEventListener("mousedown", fora, true);
      document.removeEventListener("keydown", tecla, true);
      window.removeEventListener("blur", fechar);
      window.removeEventListener("resize", fechar);
      document.removeEventListener("scroll", fechar, true);
    };
  }, [alvo, fechar]);

  // posiciona depois de medir: o menu não pode sair da janela
  useEffect(() => {
    if (!alvo || !menuRef.current) return;
    const r = menuRef.current.getBoundingClientRect();
    setPos({
      left: Math.max(4, Math.min(alvo.x, window.innerWidth - r.width - 4)),
      top: Math.max(4, Math.min(alvo.y, window.innerHeight - r.height - 4)),
    });
  }, [alvo]);

  if (!alvo) return null;

  /** devolve o foco e a seleção ao campo, para o comando agir onde o usuário clicou */
  function restaurar(el: Editavel) {
    el.focus();
    if (ehInput(el) && alvo?.inicio != null && alvo.fim != null) el.setSelectionRange(alvo.inicio, alvo.fim);
    else if (alvo?.range) {
      const sel = window.getSelection();
      sel?.removeAllRanges();
      sel?.addRange(alvo.range);
    }
  }

  const acoes = {
    copiar: async () => { await copyText(alvo.selecao); },
    recortar: async () => {
      if (!alvo.editavel) return;
      await copyText(alvo.selecao);
      restaurar(alvo.editavel);
      document.execCommand("delete");
    },
    colar: async () => {
      if (!alvo.editavel) return;
      const texto = await readClipboard();
      if (texto == null) return;
      restaurar(alvo.editavel);
      // insertText dispara o `input` do campo: o estado do React acompanha
      document.execCommand("insertText", false, texto);
    },
    selecionarTudo: () => {
      const el = alvo.editavel;
      if (!el) return;
      el.focus();
      if (ehInput(el)) el.select();
      else {
        const r = document.createRange();
        r.selectNodeContents(el);
        const sel = window.getSelection();
        sel?.removeAllRanges();
        sel?.addRange(r);
      }
    },
  };

  const itens: { icon: React.ReactNode; label: string; atalho?: string; onClick: () => void; disabled?: boolean; sep?: boolean }[] = [];
  if (alvo.editavel) {
    const somenteLeitura = ehInput(alvo.editavel) && (alvo.editavel.readOnly || alvo.editavel.disabled);
    itens.push(
      { icon: <Scissors size={14} />, label: "Recortar", atalho: "Ctrl+X", onClick: acoes.recortar, disabled: !alvo.selecao || somenteLeitura },
      { icon: <Copy size={14} />, label: "Copiar", atalho: "Ctrl+C", onClick: acoes.copiar, disabled: !alvo.selecao },
      { icon: <Clipboard size={14} />, label: "Colar", atalho: "Ctrl+V", onClick: acoes.colar, disabled: somenteLeitura },
      { icon: <TextSelect size={14} />, label: "Selecionar tudo", atalho: "Ctrl+A", onClick: acoes.selecionarTudo },
    );
  } else if (alvo.selecao) {
    itens.push({ icon: <Copy size={14} />, label: "Copiar", atalho: "Ctrl+C", onClick: acoes.copiar });
  }
  if (alvo.link) {
    itens.push(
      { icon: <ExternalLink size={14} />, label: "Abrir no navegador", onClick: () => { void openExternal(alvo.link!); }, sep: itens.length > 0 },
      { icon: <Link2 size={14} />, label: "Copiar link", onClick: () => { void copyText(alvo.link!); } },
    );
  }
  if (alvo.imagem && alvo.imagem !== alvo.link) {
    itens.push(
      { icon: <ExternalLink size={14} />, label: "Abrir imagem no navegador", onClick: () => { void openExternal(alvo.imagem!); }, sep: itens.length > 0 },
      { icon: <Link2 size={14} />, label: "Copiar endereço da imagem", onClick: () => { void copyText(alvo.imagem!); } },
    );
  }

  return (
    <div
      ref={menuRef}
      role="menu"
      // o menu não toma o foco: a seleção do campo continua lá para o comando
      onMouseDown={(e) => e.preventDefault()}
      style={{ left: pos?.left ?? alvo.x, top: pos?.top ?? alvo.y, visibility: pos ? "visible" : "hidden" }}
      className="fixed z-[1000] min-w-[210px] rounded-xl border border-border bg-surface2 p-1 shadow-menu"
    >
      {itens.map((it, i) => (
        <div key={i}>
          {it.sep && <div className="my-1 h-px bg-border" />}
          <button
            type="button"
            role="menuitem"
            disabled={it.disabled}
            onClick={() => { fechar(); it.onClick(); }}
            className="flex w-full items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-left text-sm text-ink transition-colors hover:bg-hover disabled:pointer-events-none disabled:opacity-40"
          >
            <span className="text-muted">{it.icon}</span>
            <span className="flex-1">{it.label}</span>
            {it.atalho && <span className="text-[11px] text-muted">{it.atalho}</span>}
          </button>
        </div>
      ))}
    </div>
  );
}
