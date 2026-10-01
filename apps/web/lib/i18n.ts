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

/* ------------------------------------------------------------------ */
/* Mensagens que vêm do SERVIDOR (erros da API, eventos de erro do stream).
 * O servidor fala português; as traduções moram no mesmo en.json. Mensagem com
 * parte variável ("Token inválido: 401") casa com o modelo "Token inválido: {0}". */
type Modelo = { re: RegExp; dst: string; nomes: string[]; peso: number };
let modelos: Modelo[] | null = null;

function compilar(d: Record<string, string>): Modelo[] {
  const out: Modelo[] = [];
  for (const [src, dst] of Object.entries(d)) {
    if (!/\{\w+\}/.test(src)) continue;
    const nomes: string[] = [];
    const partes = src.split(/\{(\w+)\}/);
    let literal = "";
    let re = "";
    partes.forEach((p, i) => {
      if (i % 2) { nomes.push(p); re += "([\\s\\S]+?)"; }
      else { literal += p; re += p.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"); }
    });
    // modelo quase só de variável casaria qualquer coisa
    if (literal.trim().length < 4) continue;
    out.push({ re: new RegExp(`^${re}$`), dst, nomes, peso: literal.length });
  }
  return out.sort((a, b) => b.peso - a.peso);
}

/** Traduz uma mensagem vinda do servidor; sem tradução, devolve como veio. */
/** Ferramentas do sistema vêm do servidor com nome/descrição em PT (BUILTIN_TOOLS). */
export function trTools<T extends { name: string; description: string; integration?: string }>(rows: T[]): T[] {
  if (current === "pt") return rows;
  return rows.map((t) => ({
    ...t,
    name: trServer(t.name),
    description: trServer(t.description),
    ...(t.integration ? { integration: trServer(t.integration) } : {}),
  }));
}

export function trServer(msg: string): string {
  if (current === "pt" || !msg) return msg;
  const d = DICTS[current];
  const exata = d[msg] ?? d[msg.trim()];
  if (exata) return exata;
  modelos ??= compilar(d);
  for (const m of modelos) {
    const g = msg.match(m.re);
    if (g) return m.dst.replace(/\{(\w+)\}/g, (x, k: string) => {
      const i = m.nomes.indexOf(k);
      return i >= 0 ? g[i + 1] : x;
    });
  }
  return msg;
}
