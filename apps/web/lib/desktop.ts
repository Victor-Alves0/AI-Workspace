/** Ponte com o shell desktop (Tauri). No navegador, tudo aqui vira no-op.
 *
 * As preferências abaixo são DA MÁQUINA, não da conta: ficam num arquivo local do
 * app, e não no `profile` do usuário no banco. Guardá-las no servidor faria o
 * celular exibir "iniciar com o Windows", e dois PCs com a mesma conta brigariam
 * pelo mesmo valor. */

export interface DesktopSettings {
  server_url: string;
  minimize_to_tray: boolean;
  autostart: boolean;
  start_minimized: boolean;
}

/** Campos aceitos num patch. Em camelCase porque é assim que o Tauri v2 mapeia os
 *  argumentos de JS para os parâmetros snake_case do comando em Rust. */
export interface DesktopPatch {
  minimizeToTray?: boolean;
  autostart?: boolean;
  startMinimized?: boolean;
  serverUrl?: string;
}

interface TauriBridge {
  core?: { invoke?: (cmd: string, args?: unknown) => Promise<unknown> };
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
