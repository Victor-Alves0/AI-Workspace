"use client";

import { ArrowLeft } from "lucide-react";

/** Volta para a tela de onde a pessoa veio (login, configurações…); aberta direto por
 *  link, sem histórico, vai para o início. */
export default function BackButton() {
  return (
    <button
      type="button"
      onClick={() => { if (window.history.length > 1) window.history.back(); else window.location.href = "/"; }}
      className="flex items-center gap-1.5 rounded-lg px-2 py-1.5 text-sm text-muted transition-colors hover:bg-hover hover:text-ink"
    >
      <ArrowLeft size={16} /> Voltar
    </button>
  );
}
