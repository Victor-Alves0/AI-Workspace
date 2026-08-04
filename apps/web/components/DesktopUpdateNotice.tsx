"use client";

import { useEffect, useState } from "react";
import { Download, Loader2, RefreshCw } from "lucide-react";
import { checkDesktopUpdate, installDesktopUpdate, isDesktop, openExternal } from "@/lib/desktop";

/** Aviso de nova versão em Configurações → Sobre.
 *
 *  Dois caminhos, nesta ordem:
 *  1. APP DESKTOP com o updater no build -> "Atualizar agora": baixa o instalador
 *     assinado, aplica por cima e reinicia. Os dados ficam em app_data_dir (fora da
 *     pasta de instalação), então a atualização NÃO mexe no banco.
 *  2. Qualquer outro caso (navegador, ou desktop sem o plugin) -> "Baixar instalador",
 *     que abre a página da release. openExternal é obrigatório: no webview do Tauri um
 *     <a target="_blank"> não faz nada.
 *
 *  No deploy em servidor quem atualiza é o `./update.sh` no host — lá o aviso serve só
 *  para o admin saber que existe versão nova. */
export default function DesktopUpdateNotice({
  releaseUrl, version,
}: { releaseUrl: string; version: string | null }) {
  const [canAutoUpdate, setCanAutoUpdate] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    // só desenha "Atualizar agora" se o plugin responder DE FATO — sem isto o botão
    // prometeria algo que o build talvez não tenha.
    checkDesktopUpdate().then((u) => setCanAutoUpdate(!!u)).catch(() => setCanAutoUpdate(false));
  }, []);

  async function autoUpdate() {
    setBusy(true);
    setErr(null);
    const problem = await installDesktopUpdate();
    setBusy(false);
    if (problem) setErr(problem);   // cai no botão de baixar manualmente, abaixo
  }

  return (
    <div className="mt-2 space-y-1.5">
      <div className="flex flex-wrap items-center gap-2">
        {canAutoUpdate && (
          <button
            onClick={autoUpdate}
            disabled={busy}
            className="inline-flex items-center gap-1.5 rounded-full bg-accent px-3.5 py-1.5 text-xs font-medium text-white transition-colors hover:bg-accent-hover disabled:opacity-60"
          >
            {busy ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />}
            {busy ? "Atualizando…" : `Atualizar para ${version ?? "a nova versão"}`}
          </button>
        )}
        <button
          onClick={() => openExternal(releaseUrl)}
          className="inline-flex items-center gap-1.5 rounded-full border border-border px-3 py-1.5 text-xs text-ink-soft transition-colors hover:bg-hover hover:text-ink"
        >
          <Download size={13} /> {canAutoUpdate ? "Baixar manualmente" : "Baixar instalador"}
        </button>
      </div>
      {isDesktop() && !canAutoUpdate && (
        <p className="text-xs text-muted">
          Rode o instalador por cima da instalação atual — ele atualiza no lugar e mantém
          seus dados.
        </p>
      )}
      {err && <p className="text-xs text-red-400">Não consegui atualizar sozinho ({err}). Use “Baixar”.</p>}
    </div>
  );
}
