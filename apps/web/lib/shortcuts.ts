import { tr } from "@/lib/i18n";
// Atalhos de teclado do sistema — registro central de ações + helpers de captura
// e exibição. Os bindings padrão podem ser personalizados pelo usuário (salvos em
// profile.shortcuts) na aba Configurações → Atalhos.

export type ShortcutAction = { id: string; label: string; desc?: string; default: string };

// grupos só p/ organizar a UI das configurações
export type ShortcutGroup = { title: string; actions: ShortcutAction[] };

export const SHORTCUT_GROUPS: ShortcutGroup[] = [
  {
    title: tr("Navegação"),
    actions: [
      { id: "command_palette", label: tr("Paleta de comandos"), default: "mod+k" },
      { id: "new_chat", label: tr("Novo chat"), default: "mod+shift+o" },
      { id: "toggle_sidebar", label: tr("Mostrar/ocultar barra lateral"), default: "mod+b" },
      { id: "toggle_controls", label: tr("Mostrar/ocultar painel de controles"), default: "mod+j" },
      { id: "workspace", label: tr("Abrir Espaço de Trabalho"), default: "mod+shift+e" },
      { id: "automations", label: tr("Abrir Automações"), default: "mod+shift+u" },
      { id: "settings", label: tr("Abrir Configurações"), default: "mod+," },
      { id: "archived", label: tr("Chats arquivados"), default: "mod+shift+a" },
    ],
  },
  {
    title: tr("Chat"),
    actions: [
      { id: "focus_input", label: tr("Focar no campo de mensagem"), default: "mod+/" },
      { id: "dictate", label: "Ditar (microfone)", default: "mod+shift+m" },
      { id: "voice_mode", label: tr("Modo voz (assistente)"), default: "mod+shift+v" },
      { id: "compact", label: tr("Compactar contexto"), default: "mod+shift+k" },
      { id: "context_graph", label: tr("Grafo de contexto"), default: "mod+shift+g" },
    ],
  },
];

export const SHORTCUTS: ShortcutAction[] = SHORTCUT_GROUPS.flatMap((g) => g.actions);

export type ShortcutBinding = { keys: string; enabled: boolean };
export type ShortcutMap = Record<string, ShortcutBinding>;

const MOD_KEYS = new Set(["Control", "Meta", "Shift", "Alt"]);

/** Normaliza um KeyboardEvent para um combo canônico, ex.: "mod+shift+k".
 *  `mod` = Ctrl (ou ⌘ no Mac). Retorna null se só houver tecla modificadora. */
export function eventToCombo(e: KeyboardEvent): string | null {
  const k = e.key;
  if (!k || MOD_KEYS.has(k)) return null;
  const parts: string[] = [];
  if (e.ctrlKey || e.metaKey) parts.push("mod");
  if (e.altKey) parts.push("alt");
  if (e.shiftKey) parts.push("shift");
  parts.push(k === " " ? "space" : k.toLowerCase());
  return parts.join("+");
}

/** Precisa de pelo menos um modificador p/ ser um atalho válido (evita capturar
 *  digitação normal). */
export function comboHasModifier(combo: string): boolean {
  return /(^|\+)(mod|alt)(\+|$)/.test(combo);
}

/** Combo efetivo de uma ação: custom (profile) > default, respeitando enabled. */
export function resolveBinding(map: ShortcutMap | undefined, a: ShortcutAction): ShortcutBinding {
  const c = map?.[a.id];
  if (c) return { keys: (c.keys || a.default), enabled: c.enabled !== false };
  return { keys: a.default, enabled: true };
}

const IS_MAC = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform || "");

const SPECIAL: Record<string, string> = {
  mod: IS_MAC ? "⌘" : "Ctrl",
  shift: IS_MAC ? "⇧" : "Shift",
  alt: IS_MAC ? "⌥" : "Alt",
  space: tr("Espaço"),
  escape: "Esc",
  arrowup: "↑", arrowdown: "↓", arrowleft: "←", arrowright: "→",
  enter: "Enter", backspace: "⌫", delete: "Del", tab: "Tab",
};

/** Divide um combo em rótulos bonitos p/ renderizar como teclas (kbd). */
export function prettyCombo(combo: string): string[] {
  if (!combo) return [];
  return combo.split("+").map((p) => SPECIAL[p] ?? (p.length === 1 ? p.toUpperCase() : p.charAt(0).toUpperCase() + p.slice(1)));
}
