/** Ponte com o shell desktop (Tauri). No navegador, tudo aqui vira no-op.
 *
 * As preferências abaixo são DA MÁQUINA, não da conta: ficam num arquivo local do
 * app, e não no `profile` do usuário no banco. Guardá-las no servidor faria o
 * celular exibir "iniciar com o Windows", e dois PCs com a mesma conta brigariam
 * pelo mesmo valor. */

export interface DesktopSettings {
  minimize_to_tray: boolean;
  autostart: boolean;
  start_minimized: boolean;
  /** atalho GLOBAL do modo voz (formato do Tauri, ex.: "CommandOrControl+Shift+Space") */
  voice_hotkey: string;
}

/** Campos aceitos num patch. Em camelCase porque é assim que o Tauri v2 mapeia os
 *  argumentos de JS para os parâmetros snake_case do comando em Rust. */
export interface DesktopPatch {
  minimizeToTray?: boolean;
  autostart?: boolean;
  startMinimized?: boolean;
  voiceHotkey?: string;
}

interface TauriBridge {
  core?: { invoke?: (cmd: string, args?: unknown) => Promise<unknown> };
  event?: { listen?: (event: string, handler: (e: unknown) => void) => Promise<() => void> };
}

function bridge(): TauriBridge | null {
  if (typeof window === "undefined") return null;
  return (window as unknown as { __TAURI__?: TauriBridge }).__TAURI__ ?? null;
}

/** true só quando a UI está rodando dentro do app desktop (o shell injeta a marca
 *  antes da página carregar). É o gate para exibir a seção nas Configurações. */
export function isDesktop(): boolean {
  if (typeof window === "undefined") return false;
  return !!(window as unknown as { __AIW_DESKTOP__?: unknown }).__AIW_DESKTOP__;
}

async function invoke<T>(cmd: string, args?: Record<string, unknown>): Promise<T | null> {
  const inv = bridge()?.core?.invoke;
  if (!inv) return null;
  try {
    return (await inv(cmd, args)) as T;
  } catch {
    return null; // shell antigo ou comando indisponível: a UI só não mostra a seção
  }
}

export const getDesktopSettings = () => invoke<DesktopSettings>("desktop_get_settings");

export const setDesktopSettings = (patch: DesktopPatch) =>
  invoke<DesktopSettings>("desktop_set_settings", patch as Record<string, unknown>);

/** Abre uma URL externa no navegador do sistema.
 *
 *  No navegador é só `window.open`. No app desktop isso é OBRIGATÓRIO: no webview do
 *  Tauri um `<a target="_blank">` (e `window.open`) não faz NADA — não há handler de
 *  nova janela nem plugin de shell. Sem passar por aqui, o link fica morto e o clique
 *  parece "não acontecer" (foi o que quebrou o login por assinatura ChatGPT/Codex).
 *
 *  Use SEMPRE que o destino for fora do app. Devolve false se não deu para abrir —
 *  aí a UI deve oferecer copiar o link. */
export async function openExternal(url: string): Promise<boolean> {
  if (!url) return false;
  if (isDesktop()) {
    const inv = bridge()?.core?.invoke;
    if (inv) {
      try {
        await inv("desktop_open_external", { url });
        return true;
      } catch {
        return false; // shell antigo (sem o comando) ou o SO recusou
      }
    }
    return false;
  }
  return !!window.open(url, "_blank", "noopener,noreferrer");
}

/** Assina o evento "voice-activate" que o atalho GLOBAL do desktop dispara.
 *  Retorna uma função para cancelar a assinatura (no-op fora do desktop). */
export async function onVoiceActivate(cb: () => void): Promise<() => void> {
  const listen = bridge()?.event?.listen;
  if (!listen) return () => {};
  try {
    return await listen("voice-activate", () => cb());
  } catch {
    return () => {};
  }
}
