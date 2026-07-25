// Previne abrir um console extra no Windows em release.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

//! Shell desktop do AI Workspace.
//!
//! A janela carrega a MESMA interface web que roda no navegador (por padrão
//! `http://localhost:3000`) em vez de assets locais. Isso é deliberado: a sessão é
//! um cookie httpOnly emitido pela API, e servir a UI de uma origem `tauri://`
//! tornaria toda chamada cross-origin — o login simplesmente não colaria. Apontando
//! para a origem real, o comportamento é idêntico ao do navegador (o CORS e os
//! cookies que já funcionam hoje continuam valendo).
//!
//! O que este shell acrescenta é o que um navegador não dá: ícone na bandeja,
//! "fechar esconde em vez de sair" e iniciar junto com o Windows.

use std::sync::Mutex;

use serde::{Deserialize, Serialize};
use tauri::{
    menu::{CheckMenuItem, Menu, MenuItem},
    tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
    Manager, State, WebviewUrl, WebviewWindowBuilder,
};
use tauri_plugin_autostart::ManagerExt;

const DEFAULT_SERVER_URL: &str = "http://localhost:3000";
const SETTINGS_FILE: &str = "desktop-settings.json";

/// Preferências **da máquina**, não da conta.
///
/// Moram num arquivo local de propósito: "iniciar com o Windows" não faz sentido
/// no perfil do usuário no banco — o celular dele passaria a exibir a opção, e dois
/// PCs com a mesma conta brigariam pelo mesmo valor.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
struct DesktopSettings {
    /// De onde a janela carrega a interface.
    server_url: String,
    /// Fechar a janela esconde na bandeja em vez de encerrar o app.
    minimize_to_tray: bool,
    /// Abrir junto com o Windows.
    autostart: bool,
    /// Ao iniciar com o Windows, abre já escondido na bandeja.
    start_minimized: bool,
}

impl Default for DesktopSettings {
    fn default() -> Self {
        Self {
            server_url: DEFAULT_SERVER_URL.to_string(),
            minimize_to_tray: true,
            autostart: false,
            start_minimized: true,
        }
    }
}

struct SettingsState(Mutex<DesktopSettings>);

fn settings_path(app: &tauri::AppHandle) -> Option<std::path::PathBuf> {
    let dir = app.path().app_config_dir().ok()?;
    std::fs::create_dir_all(&dir).ok()?;
    Some(dir.join(SETTINGS_FILE))
}

fn load_settings(app: &tauri::AppHandle) -> DesktopSettings {
    settings_path(app)
        .and_then(|p| std::fs::read_to_string(p).ok())
        .and_then(|raw| serde_json::from_str(&raw).ok())
        .unwrap_or_default()
}

fn save_settings(app: &tauri::AppHandle, s: &DesktopSettings) {
    if let Some(path) = settings_path(app) {
        if let Ok(json) = serde_json::to_string_pretty(s) {
            let _ = std::fs::write(path, json);
        }
    }
}

/// Aplica o estado de autostart no SO. Idempotente: liga/desliga conforme pedido e
/// ignora o erro de "já está no estado desejado".
fn apply_autostart(app: &tauri::AppHandle, enabled: bool) {
    let manager = app.autolaunch();
    let _ = if enabled {
        manager.enable()
    } else {
        manager.disable()
    };
}

fn show_main_window(app: &tauri::AppHandle) {
    if let Some(win) = app.get_webview_window("main") {
        let _ = win.show();
        let _ = win.unminimize();
        let _ = win.set_focus();
    }
}

// --------------------------------------------------------------------------- //
// Comandos expostos à interface web
// --------------------------------------------------------------------------- //

#[tauri::command]
fn desktop_get_settings(state: State<'_, SettingsState>) -> DesktopSettings {
    state.0.lock().map(|s| s.clone()).unwrap_or_default()
}

#[tauri::command]
fn desktop_set_settings(
    app: tauri::AppHandle,
    state: State<'_, SettingsState>,
    minimize_to_tray: Option<bool>,
    autostart: Option<bool>,
    start_minimized: Option<bool>,
    server_url: Option<String>,
) -> Result<DesktopSettings, String> {
    let mut guard = state.0.lock().map_err(|e| e.to_string())?;
    if let Some(v) = minimize_to_tray {
        guard.minimize_to_tray = v;
    }
    if let Some(v) = start_minimized {
        guard.start_minimized = v;
    }
    if let Some(v) = server_url {
        let v = v.trim().trim_end_matches('/').to_string();
        if !v.is_empty() {
            guard.server_url = v;
        }
    }
    if let Some(v) = autostart {
        guard.autostart = v;
        apply_autostart(&app, v);
    }
    let out = guard.clone();
    drop(guard);
    save_settings(&app, &out);
    Ok(out)
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_autostart::init(
            tauri_plugin_autostart::MacosLauncher::LaunchAgent,
            // `--hidden` deixa o app saber que quem abriu foi o Windows, não o usuário
            Some(vec!["--hidden"]),
        ))
        // Segunda instância: em vez de abrir outra janela, traz a existente à frente.
        .plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
            show_main_window(app);
        }))
        .invoke_handler(tauri::generate_handler![
            desktop_get_settings,
            desktop_set_settings
        ])
        .setup(|app| {
            let handle = app.handle().clone();
            let settings = load_settings(&handle);

            // mantém o registro do SO em dia com o que está salvo (o usuário pode ter
            // removido o app do autostart por fora)
            apply_autostart(&handle, settings.autostart);

            // Aberto pelo Windows (`--hidden`) + preferência de iniciar minimizado
            // = sobe direto para a bandeja, sem roubar o foco de quem ligou o PC.
            let launched_hidden = std::env::args().any(|a| a == "--hidden");
            let start_hidden = launched_hidden && settings.start_minimized;

            let url = settings
                .server_url
                .parse()
                .unwrap_or_else(|_| DEFAULT_SERVER_URL.parse().unwrap());

            WebviewWindowBuilder::new(app, "main", WebviewUrl::External(url))
                .title("AI Workspace")
                .inner_size(1280.0, 800.0)
                .min_inner_size(380.0, 480.0)
                .center()
                .visible(!start_hidden)
                // Marca a página para a UI web saber que está no desktop e poder
                // exibir a seção de preferências da máquina.
                .initialization_script(
                    "window.__AIW_DESKTOP__ = { platform: 'windows', version: '0.1.1' };",
                )
                .build()?;

            // ---- bandeja ----
            let open_i = MenuItem::with_id(app, "open", "Abrir", true, None::<&str>)?;
            let tray_i = CheckMenuItem::with_id(
                app,
                "tray",
                "Rodar em segundo plano",
                true,
                settings.minimize_to_tray,
                None::<&str>,
            )?;
            let boot_i = CheckMenuItem::with_id(
                app,
                "boot",
                "Iniciar com o Windows",
                true,
                settings.autostart,
                None::<&str>,
            )?;
            let quit_i = MenuItem::with_id(app, "quit", "Sair", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&open_i, &tray_i, &boot_i, &quit_i])?;

            TrayIconBuilder::with_id("main")
                .icon(app.default_window_icon().unwrap().clone())
                .tooltip("AI Workspace")
                .menu(&menu)
                // no Windows o clique esquerdo abre a janela; o direito abre o menu
                .show_menu_on_left_click(false)
                .on_tray_icon_event(|tray, event| {
                    if let TrayIconEvent::Click {
                        button: MouseButton::Left,
                        button_state: MouseButtonState::Up,
                        ..
                    } = event
                    {
                        show_main_window(tray.app_handle());
                    }
                })
                .on_menu_event(move |app, event| match event.id().as_ref() {
                    "open" => show_main_window(app),
                    "quit" => app.exit(0),
                    "tray" => {
                        let st = app.state::<SettingsState>();
                        let mut g = match st.0.lock() {
                            Ok(g) => g,
                            Err(_) => return,
                        };
                        g.minimize_to_tray = !g.minimize_to_tray;
                        let out = g.clone();
                        drop(g);
                        let _ = tray_i.set_checked(out.minimize_to_tray);
                        save_settings(app, &out);
                    }
                    "boot" => {
                        let st = app.state::<SettingsState>();
                        let mut g = match st.0.lock() {
                            Ok(g) => g,
                            Err(_) => return,
                        };
                        g.autostart = !g.autostart;
                        let out = g.clone();
                        drop(g);
                        let _ = boot_i.set_checked(out.autostart);
                        apply_autostart(app, out.autostart);
                        save_settings(app, &out);
                    }
                    _ => {}
                })
                .build(app)?;

            app.manage(SettingsState(Mutex::new(settings)));
            Ok(())
        })
        .on_window_event(|window, event| {
            // "Rodar em segundo plano": o X esconde na bandeja em vez de encerrar.
            // Sair de verdade só pelo menu da bandeja.
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                let app = window.app_handle();
                let hide = app
                    .try_state::<SettingsState>()
                    .and_then(|s| s.0.lock().ok().map(|g| g.minimize_to_tray))
                    .unwrap_or(true);
                if hide {
                    api.prevent_close();
                    let _ = window.hide();
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("erro ao iniciar o app Tauri");
}
