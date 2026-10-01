"use client";

import { useEffect, useSyncExternalStore } from "react";
import { getLocale } from "@/lib/i18n";

const nada = () => () => {};

/** Só desenha a interface no navegador: a pré-renderização (sempre em português)
 *  não pode ser hidratada com textos em outro idioma. Quase toda tela já espera o
 *  login antes de mostrar algo, então o custo é nenhum. */
export default function I18nRoot({ children }: { children: React.ReactNode }) {
  const montado = useSyncExternalStore(nada, () => true, () => false);
  useEffect(() => {
    document.documentElement.lang = getLocale() === "pt" ? "pt-BR" : "en";
  }, []);
  return montado ? <>{children}</> : null;
}
