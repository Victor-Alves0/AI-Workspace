"use client";

import { useEffect, useState } from "react";
import { Copy, Minus, Square, X } from "lucide-react";

/** Barra de título do app DESKTOP, embutida na interface (como no VS Code): a janela
 *  não tem a moldura do Windows, então aqui ficam a área de arrastar (duplo clique
 *  maximiza) e os botões minimizar / maximizar / fechar. No navegador não aparece.
 *  Fechar segue a preferência "rodar em segundo plano" (o shell decide: esconder na
 *  bandeja ou sair). */
type Win = {
  minimize: () => Promise<void>;
  toggleMaximize: () => Promise<void>;
  close: () => Promise<void>;
  isMaximized: () => Promise<boolean>;
  onResized: (cb: () => void) => Promise<() => void>;
};

function currentWindow(): Win | null {
  const t = (window as unknown as { __TAURI__?: { window?: { getCurrentWindow?: () => Win } } }).__TAURI__;
  try {
    return t?.window?.getCurrentWindow?.() ?? null;
  } catch {
    return null;
  }
}

export default function DesktopTitleBar() {
  const [win, setWin] = useState<Win | null>(null);
  const [max, setMax] = useState(false);

  useEffect(() => {
    if (!(window as unknown as { __AIW_DESKTOP__?: unknown }).__AIW_DESKTOP__) return;
    const w = currentWindow();
    if (!w) return;
    setWin(w);
    document.documentElement.classList.add("aiw-desktop");
    let off: (() => void) | undefined;
    const sync = () => { void w.isMaximized().then(setMax).catch(() => {}); };
    sync();
    void w.onResized(sync).then((u) => { off = u; }).catch(() => {});
    return () => { off?.(); document.documentElement.classList.remove("aiw-desktop"); };
  }, []);

  if (!win) return null;
  const btn = "flex h-full w-[46px] items-center justify-center text-ink-soft transition-colors";
  return (
    <div
      data-tauri-drag-region
      className="fixed inset-x-0 top-0 z-[9999] flex h-[var(--titlebar-h)] select-none items-center border-b border-border/60 bg-bg"
      onDoubleClick={(e) => { if ((e.target as HTMLElement).closest("button")) return; void win.toggleMaximize(); }}
    >
      <div data-tauri-drag-region className="flex min-w-0 flex-1 items-center gap-2 pl-3">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img data-tauri-drag-region src="/logo.png" alt="" className="h-4 w-4 rounded" draggable={false} />
        <span data-tauri-drag-region className="truncate text-xs text-muted">AI Workspace</span>
      </div>
      <div className="flex h-full">
        <button type="button" title="Minimizar" aria-label="Minimizar" onClick={() => void win.minimize()} className={`${btn} hover:bg-hover`}>
          <Minus size={15} strokeWidth={1.5} />
        </button>
        <button type="button" title={max ? "Restaurar" : "Maximizar"} aria-label={max ? "Restaurar" : "Maximizar"} onClick={() => void win.toggleMaximize()} className={`${btn} hover:bg-hover`}>
          {max ? <Copy size={13} strokeWidth={1.5} className="-scale-x-100" /> : <Square size={12} strokeWidth={1.5} />}
        </button>
        <button type="button" title="Fechar" aria-label="Fechar" onClick={() => void win.close()} className={`${btn} hover:bg-[#c42b1c] hover:text-white`}>
          <X size={16} strokeWidth={1.5} />
        </button>
      </div>
    </div>
  );
}
