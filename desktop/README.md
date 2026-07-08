# AI Workspace — Desktop (Tauri)

Empacota o app como aplicativo desktop. O frontend (Next.js) é carregado pelo webview
nativo e o backend FastAPI roda como **sidecar** (binário gerado com PyInstaller).

> Status: **scaffold**. Requer toolchains locais (Rust + Node) para compilar — não foi
> compilado neste ambiente. Os arquivos abaixo são a base pronta para `tauri build`.

## Pré-requisitos

- Rust (https://rustup.rs)
- Node 20+ e `@tauri-apps/cli`
- Python 3.12 + PyInstaller (para gerar o sidecar do backend)

## Passos

1. **Gerar o sidecar do backend** (binário único):

   ```bash
   cd ../apps/server
   pip install pyinstaller .
   pyinstaller --onefile --name aiworkspace-server -m uvicorn -- aiworkspace.main:app
   # copie o binário para desktop/src-tauri/binaries/ com o sufixo do target triple, ex.:
   #   aiworkspace-server-x86_64-pc-windows-msvc.exe
   ```

2. **Exportar o frontend estático** (Tauri usa `apps/web/out`):

   ```bash
   cd ../web
   # habilite `output: "export"` temporariamente OU sirva via devUrl em dev
   npm run build
   ```

3. **Rodar / empacotar**:

   ```bash
   cd ../../desktop
   npm install
   npm run dev      # desenvolvimento (usa devUrl http://localhost:3000)
   npm run build    # gera o instalador
   ```

## Decisão em aberto: banco no desktop

No modo servidor usamos Postgres + pgvector. No desktop (single-user) há duas opções:

- **Postgres embarcado** (mesma stack; mais pesado de empacotar).
- **SQLite + extensão de vetores** (mais leve; exige uma camada de abstração no backend).

Recomendação: começar apontando o sidecar para um Postgres local (via Docker ou binário
embarcado) e avaliar SQLite depois, conforme o tamanho do instalador desejado.

## Ícones

Coloque um `icon.png` (1024×1024) em `src-tauri/icons/` e gere os demais com
`npm run tauri icon src-tauri/icons/icon.png`.
