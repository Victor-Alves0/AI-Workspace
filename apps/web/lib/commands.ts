/**
 * Comandos do compositor: começam com "//" (o "/" sozinho é a biblioteca de prompts).
 * Valem em QUALQUER chat — não só no Imaginai.
 *
 *   //roll 1d20+2 [rótulo]   (ou //r)   rola dados no servidor, estilo roll20
 *   //compact                           compacta o contexto da conversa
 *   //help                              lista os comandos
 */

export type ChatCommand = {
  name: string;
  aliases: string[];
  usage: string;
  description: string;
};

export const CHAT_COMMANDS: ChatCommand[] = [
  { name: "roll", aliases: ["r"], usage: "//roll 1d20+2 furtividade", description: "Rola dados (1d20+2, 2d6+3, 4d6kh3, 2d20kh1)" },
  { name: "compact", aliases: [], usage: "//compact", description: "Compacta o contexto da conversa" },
  { name: "help", aliases: ["?"], usage: "//help", description: "Lista os comandos" },
];

export type ParsedCommand = { command: ChatCommand; args: string };

/** "//r 1d20+2 ataque" → { command: roll, args: "1d20+2 ataque" }. Não é comando: null. */
export function parseCommand(text: string): ParsedCommand | null {
  const m = /^\/\/([\w?]+)(?:\s+([\s\S]*))?$/.exec(text.trim());
  if (!m) return null;
  const name = m[1].toLowerCase();
  const command = CHAT_COMMANDS.find((c) => c.name === name || c.aliases.includes(name));
  return command ? { command, args: (m[2] ?? "").trim() } : null;
}

/** "1d20+2 ataque furtivo" → expressão (sem espaços internos até o rótulo) + rótulo. */
export function splitRollArgs(args: string): { expression: string; label: string } {
  const m = /^([0-9dDkKhHlL+\-\s]+?)(?:\s+([^\s0-9+\-].*))?$/.exec(args.trim());
  if (!m) return { expression: args.trim(), label: "" };
  return { expression: m[1].replace(/\s+/g, ""), label: (m[2] ?? "").trim() };
}

/** Comandos que casam com o que foi digitado depois de "//" (para o menu). */
export function matchCommands(query: string): ChatCommand[] {
  const q = query.toLowerCase();
  return CHAT_COMMANDS.filter((c) => c.name.startsWith(q) || c.aliases.some((a) => a.startsWith(q)));
}
