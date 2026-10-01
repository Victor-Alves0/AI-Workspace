/**
 * Tradução da interface (estilo gettext): o TEXTO EM PORTUGUÊS é a chave.
 *
 *   tr("Conectar conta Google")            → "Connect Google account" (en)
 *   tr("Conectada: {0}", { 0: email })     → interpolação por nome/posição
 *
 * Sem tradução no dicionário, sai o português — nunca uma chave crua na tela.
 *
 * O idioma é decidido NO NAVEGADOR, uma vez, quando este módulo carrega (o desktop
 * usa o front exportado como estático: não há servidor para ler cookie). Por isso
 * `tr()` pode ser chamado até no topo de módulos, e trocar de idioma recarrega a
 * página. O <I18nRoot> (no layout) só desenha a interface depois de montar: a
 * pré-renderização sai em português e não pode ser "hidratada" com outro idioma.
 */

import en from "@/locales/en.json";

export type Locale = "pt" | "en";
export const LOCALES: { id: Locale; label: string }[] = [
  { id: "pt", label: "Português (Brasil)" },
  { id: "en", label: "English" },
];

const KEY = "aiw_locale";
const DICTS: Record<Exclude<Locale, "pt">, Record<string, string>> = { en };

function detect(): Locale {
  if (typeof window === "undefined") return "pt";
  try {
    const salvo = window.localStorage.getItem(KEY);
    if (salvo === "pt" || salvo === "en") return salvo;
  } catch { /* storage bloqueado: segue para o navegador */ }
  const nav = (navigator.languages?.[0] || navigator.language || "pt").toLowerCase();
  return nav.startsWith("pt") ? "pt" : "en";
}

let current: Locale = detect();

export function getLocale(): Locale {
  return current;
}

/** Locale BCP 47 para datas e números (`toLocaleString(dateLocale())`). */
export function dateLocale(): string {
  return current === "pt" ? "pt-BR" : "en-US";
}

/** Grava o idioma e recarrega (os textos são resolvidos na carga). */
export function setLocale(l: Locale) {
  try { window.localStorage.setItem(KEY, l); } catch { /* sem storage: vale só nesta carga */ }
  current = l;
  window.location.reload();
}

type Vars = Record<string, unknown>;

function fill(s: string, vars?: Vars): string {
  if (!vars) return s;
  return s.replace(/\{(\w+)\}/g, (m, k: string) => (k in vars ? String(vars[k] ?? "") : m));
}

/** Texto da interface no idioma atual. `src` é o texto em português. */
export function tr(src: string, vars?: Vars): string {
  if (current === "pt") return fill(src, vars);
  const d = DICTS[current];
  return fill(d[src] ?? src, vars);
}

/** Idioma do PERFIL (`profile.language`: "pt-BR" | "en") → locale da interface. */
export function localeOf(language: unknown): Locale | null {
  if (typeof language !== "string" || !language) return null;
  return language.toLowerCase().startsWith("pt") ? "pt" : "en";
}

/** Num aparelho sem escolha própria, segue o idioma do perfil (vale entre aparelhos). */
export function syncLocaleWithProfile(language: unknown) {
  const l = localeOf(language);
  if (!l || l === current) return;
  try { if (window.localStorage.getItem(KEY)) return; } catch { return; }
  setLocale(l);
}
