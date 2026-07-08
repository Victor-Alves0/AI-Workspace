// Previne abrir um console extra no Windows em release.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use tauri_plugin_shell::process::CommandEvent;
use tauri_plugin_shell::ShellExt;

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .setup(|app| {
            // Sobe o backend Python (empacotado via PyInstaller) como sidecar.
            // O binário deve estar em src-tauri/binaries/aiworkspace-server<-target-triple>.
            let sidecar = app.shell().sidecar("aiworkspace-server")?;
            let (mut rx, _child) = sidecar.spawn()?;

            tauri::async_runtime::spawn(async move {
                while let Some(event) = rx.recv().await {
                    if let CommandEvent::Stdout(line) = event {
                        println!("[server] {}", String::from_utf8_lossy(&line));
                    }
                }
            });
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("erro ao iniciar o app Tauri");
}
