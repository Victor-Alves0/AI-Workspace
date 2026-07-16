// Formato de hora/data escolhido pelo usuário (Configurações → Personalização).
// As preferências ficam num módulo simples (não em contexto React) porque os
// formatadores são chamados de funções utilitárias fora da árvore de componentes.
// `setFormatPrefs` é chamado quando o perfil carrega.

type TimeFmt = "24h" | "12h";
type DateFmt = "dmy" | "mdy" | "ymd";

let timeFmt: TimeFmt = "24h";
let dateFmt: DateFmt = "dmy";

export function setFormatPrefs(p: { time_format?: string; date_format?: string } | undefined | null): void {
  timeFmt = (p?.time_format as TimeFmt) === "12h" ? "12h" : "24h";
  const d = p?.date_format as DateFmt;
  dateFmt = d === "mdy" || d === "ymd" ? d : "dmy";
}

/** HH:MM no formato escolhido (24h → "14:30"; 12h → "2:30 PM"). */
export function fmtHM(d: Date): string {
  return d.toLocaleTimeString(timeFmt === "12h" ? "en-US" : "pt-BR", {
    hour: timeFmt === "12h" ? "numeric" : "2-digit",
    minute: "2-digit",
    hour12: timeFmt === "12h",
  });
}

/** Data curta no formato escolhido. */
export function fmtDay(d: Date): string {
  const dd = String(d.getDate()).padStart(2, "0");
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const yyyy = d.getFullYear();
  if (dateFmt === "mdy") return `${mm}/${dd}/${yyyy}`;
  if (dateFmt === "ymd") return `${yyyy}-${mm}-${dd}`;
  return `${dd}/${mm}/${yyyy}`;
}

/** Data curta sem o ano (dia+mês), respeitando a ordem escolhida. */
export function fmtDayShort(d: Date): string {
  const dd = String(d.getDate()).padStart(2, "0");
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  return dateFmt === "mdy" ? `${mm}/${dd}` : `${dd}/${mm}`;
}
