// Previne abrir um console extra no Windows em release.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

//! Shell desktop do AI Workspace (instalador unico, sem Docker).
//!
//! O app EMBARCA o "motor" (Postgres + backend Python + frontend Node) como
//! recursos e o supervisiona: ao abrir, sobe o motor por baixo (via o launcher
//! PowerShell ja validado), mostra uma tela de "iniciando..." e navega para a
//! interface local (http://localhost:3000) assim que ela responde. Ao sair,
//! encerra o motor inteiro (inclusive o Postgres, que o pg_ctl desanexa).
//!
//! O shell tambem da o que um navegador nao da: icone na bandeja, "rodar em
//! segundo plano" e "iniciar com o Windows".

use std::os::windows::process::CommandExt;
use std::path::PathBuf;
use std::process::Command;
use std::sync::Mutex;
use std::time::Duration;

use serde::{Deserialize, Serialize};
use tauri::{
    menu::{CheckMenuItem, Menu, MenuItem},
    tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
    AppHandle, Manager, RunEvent, State, WebviewUrl, WebviewWindowBuilder,
};
use tauri_plugin_autostart::ManagerExt;

const SETTINGS_FILE: &str = "desktop-settings.json";
const WEB_URL: &str = "http://localhost:3000";
// esconde o console do powershell/pg/uvicorn/node (CREATE_NO_WINDOW)
const NO_WINDOW: u32 = 0x0800_0000;

/// Preferencias **da maquina**, nao da conta.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
struct DesktopSettings {
    /// Fechar a janela esconde na bandeja em vez de encerrar o app.
    minimize_to_tray: bool,
    /// Abrir junto com o Windows.
    autostart: bool,
    /// Ao iniciar com o Windows, abre ja escondido na bandeja.
    start_minimized: bool,
}

impl Default for DesktopSettings {
    fn default() -> Self {
        Self {
            minimize_to_tray: true,
            autostart: false,
            start_minimized: true,
        }
    }
}

struct SettingsState(Mutex<DesktopSettings>);
/// PID do launcher do motor (para encerrar a arvore de processos ao sair).
struct EngineState(Mutex<Option<u32>>);

fn settings_path(app: &AppHandle) -> Option<PathBuf> {
    let dir = app.path().app_config_dir().ok()?;
    std::fs::create_dir_all(&dir).ok()?;
    Some(dir.join(SETTINGS_FILE))
}

fn load_settings(app: &AppHandle) -> DesktopSettings {
    settings_path(app)
        .and_then(|p| std::fs::read_to_string(p).ok())
        .and_then(|raw| serde_json::from_str(&raw).ok())
        .unwrap_or_default()
}

fn save_settings(app: &AppHandle, s: &DesktopSettings) {
    if let Some(path) = settings_path(app) {
        if let Ok(json) = serde_json::to_string_pretty(s) {
            let _ = std::fs::write(path, json);
        }
    }
}

fn apply_autostart(app: &AppHandle, enabled: bool) {
    let manager = app.autolaunch();
    let _ = if enabled {
        manager.enable()
    } else {
        manager.disable()
    };
}

fn show_main_window(app: &AppHandle) {
    if let Some(win) = app.get_webview_window("main") {
        let _ = win.show();
        let _ = win.unminimize();
        let _ = win.set_focus();
    }
}

// --------------------------------------------------------------------------- //
// Motor embarcado (Postgres + backend + frontend)
// --------------------------------------------------------------------------- //

/// (pasta do motor nos recursos, pasta de dados gravavel do usuario)
fn engine_paths(app: &AppHandle) -> Option<(PathBuf, PathBuf)> {
    let engine = app.path().resource_dir().ok()?.join("engine");
    let data = app.path().app_data_dir().ok()?.join("data");
    Some((engine, data))
}

/// Uma porta local esta' aceitando conexao? (usado como sinal de "no ar").
fn port_open(port: u16) -> bool {
    let addr: std::net::SocketAddr = ([127, 0, 0, 1], port).into();
    std::net::TcpStream::connect_timeout(&addr, Duration::from_secs(1)).is_ok()
}

/// Sobe o motor via o launcher PowerShell (mesma sequencia validada a mao):
/// initdb -> Postgres -> migracoes -> API -> interface. Devolve o PID do launcher.
fn spawn_engine(app: &AppHandle) -> Option<u32> {
    // Ja' ha' um motor no ar (outra instancia, ou uma sobra)? Nao sobe outro nem
    // toma posse — assim uma 2a abertura NAO derruba o motor da 1a ao sair (o
    // stop_engine so' age quando este processo e' o dono, i.e. pid = Some).
    if port_open(3000) {
        return None;
    }
    let (engine, data) = engine_paths(app)?;
    let _ = std::fs::create_dir_all(&data);
    // `-File` com caminho ABSOLUTO quebra quando ha espaco no caminho de instalacao
    // ("AI Workspace"): o powershell le so' ate o espaco e reclama que "AI" nao tem
    // extensao .ps1. Rodamos com o diretorio de trabalho na pasta do motor e
    // passamos o script pelo nome relativo (sem espaco). O -DataDir fica em
    // %APPDATA%\com.aiworkspace.app (sem espaco), entao e' seguro.
    let child = Command::new("powershell.exe")
        .current_dir(&engine)
        .args([
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            "Start-AIWorkspace.ps1",
            "-DataDir",
        ])
        .arg(&data)
        .arg("-NoBrowser")
        .creation_flags(NO_WINDOW)
        .spawn()
        .ok()?;
    Some(child.id())
}

/// Espera a interface local responder e entao navega a janela para ela. Roda numa
/// thread para nao travar a UI: a janela ja mostra a tela de "iniciando..." (o
/// index.html dos assets) enquanto isso.
fn wait_and_show(app: AppHandle) {
    std::thread::spawn(move || {
        // Espera AS DUAS pontas: interface (3000) E API (8000). So' navegar quando
        // a interface sobe deixa a pagina carregar antes da API responder — no 1o
        // boot a API demora (baixa o modelo de embeddings), e chamadas como
        // /auth/config falham, escondendo ate' o "criar conta". A porta 8000 so'
        // abre depois do "startup complete" do uvicorn, entao e' um bom sinal de
        // "API pronta". Ate' ~15 min no primeiro boot.
        for _ in 0..900 {
            if port_open(3000) && port_open(8000) {
                std::thread::sleep(Duration::from_millis(800));
                if let Some(win) = app.get_webview_window("main") {
                    let _ = win.eval(&format!("window.location.replace('{WEB_URL}')"));
                }
                return;
            }
            std::thread::sleep(Duration::from_secs(1));
        }
    });
}

/// Encerra o motor ao sair: mata a arvore do launcher (powershell + uvicorn + node)
/// e para o Postgres explicitamente (o pg_ctl o desanexa, entao taskkill /T nao o
/// alcanca).
fn stop_engine(app: &AppHandle) {
    // So' encerra o motor se ESTE processo e' o dono (spawn_engine devolveu um pid).
    // Se nao somos donos (ex.: uma 2a instancia que reusou o motor da 1a), sair NAO
    // pode derrubar o Postgres compartilhado.
    let Some(pid) = app.state::<EngineState>().0.lock().ok().and_then(|g| *g) else {
        return;
    };
    let _ = Command::new("taskkill")
        .args(["/F", "/T", "/PID", &pid.to_string()])
        .creation_flags(NO_WINDOW)
        .status();
    if let Some((engine, data)) = engine_paths(app) {
        let pgctl = engine.join("pgsql").join("bin").join("pg_ctl.exe");
        if pgctl.exists() {
            let _ = Command::new(pgctl)
                .arg("-D")
                .arg(data.join("pgdata"))
                .args(["-w", "stop"])
                .creation_flags(NO_WINDOW)
                .status();
        }
    }
}

// --------------------------------------------------------------------------- //
// Comandos expostos a interface web
// --------------------------------------------------------------------------- //

#[tauri::command]
fn desktop_get_settings(state: State<'_, SettingsState>) -> DesktopSettings {
    state.0.lock().map(|s| s.clone()).unwrap_or_default()
}

#[tauri::command]
fn desktop_set_settings(
    app: AppHandle,
    state: State<'_, SettingsState>,
    minimize_to_tray: Option<bool>,
    autostart: Option<bool>,
    start_minimized: Option<bool>,
) -> Result<DesktopSettings, String> {
    let mut guard = state.0.lock().map_err(|e| e.to_string())?;
    if let Some(v) = minimize_to_tray {
        guard.minimize_to_tray = v;
    }
    if let Some(v) = start_minimized {
        guard.start_minimized = v;
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
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_autostart::init(
            tauri_plugin_autostart::MacosLauncher::LaunchAgent,
            Some(vec!["--hidden"]),
        ))
        // Segunda instancia: em vez de abrir outra janela (e outro motor!), traz a
        // existente a frente.
        .plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
            show_main_window(app);
        }))
        .manage(EngineState(Mutex::new(None)))
        .invoke_handler(tauri::generate_handler![
            desktop_get_settings,
            desktop_set_settings
        ])
        .setup(|app| {
            let handle = app.handle().clone();
            let settings = load_settings(&handle);
            apply_autostart(&handle, settings.autostart);

            // sobe o motor embarcado e guarda o PID para encerrar ao sair
            if let Some(pid) = spawn_engine(&handle) {
                *app.state::<EngineState>().0.lock().unwrap() = Some(pid);
            }

            let launched_hidden = std::env::args().any(|a| a == "--hidden");
            let start_hidden = launched_hidden && settings.start_minimized;

            // A janela "main" nasce AQUI (nao no tauri.conf.json, senao o Tauri cria
            // uma segunda "main" e o build colide no label). Abre na tela de
            // "iniciando..." (index.html dos assets); wait_and_show navega para a
            // interface local quando ela responde.
            WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
                .title("AI Workspace")
                .inner_size(1280.0, 800.0)
                .min_inner_size(380.0, 480.0)
                .center()
                .visible(!start_hidden)
                .initialization_script(
                    "window.__AIW_DESKTOP__ = { platform: 'windows', version: '0.2.2' };",
                )
                .build()?;

            wait_and_show(handle.clone());

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
            // Sair de verdade (e desligar o motor) so' pelo menu da bandeja.
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
        .build(tauri::generate_context!())
        .expect("erro ao iniciar o app Tauri");

    // Ao encerrar de verdade, desliga o motor embarcado.
    app.run(|handle, event| {
        if let RunEvent::Exit = event {
            stop_engine(handle);
        }
    });
}
