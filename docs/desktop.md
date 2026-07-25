# App desktop (Windows)

Um aplicativo nativo que abre o AI Workspace numa janela própria, com **ícone na bandeja**,
**"rodar em segundo plano"** e **"iniciar com o Windows"**. Construído com [Tauri](https://tauri.app/).

## O que ele acrescenta

A janela carrega a **mesma interface web** que você usa no navegador (por padrão
`http://localhost:3000`), então o comportamento é idêntico — o que o shell adiciona é o que um
navegador não dá:

| Recurso | Onde configurar |
|---|---|
| Fechar esconde na bandeja (em vez de sair) | Configurações → Aplicativo, ou o menu da bandeja |
| Iniciar com o Windows | idem |
| Abrir minimizado ao iniciar com o Windows | idem |
| Endereço do servidor (apontar para outra máquina) | idem |

> **Por que carrega a UI web e não assets locais?** A sessão é um cookie `httpOnly` emitido pela
> API; servir a interface de uma origem `tauri://` tornaria toda chamada cross-origin e o login
> não colaria. Apontando para a origem real, o CORS e os cookies que já funcionam continuam
> valendo.

## Preferências são da máquina

As opções ficam em `%APPDATA%\com.aiworkspace.app\desktop-settings.json` — **não** no perfil do
usuário no banco. Guardá-las no servidor faria o celular exibir "iniciar com o Windows", e dois
PCs com a mesma conta brigariam pelo valor. Por isso a categoria **Aplicativo** só aparece nas
Configurações quando a UI roda dentro do app instalado.

## Instalar

O instalador é publicado em **[Releases](../../releases)** (compilado pelo CI, não versionado no
repositório). Nesta versão o app **não embarca o servidor** — deixe o stack no ar
(`docker compose up -d`) e abra o app.

## Compilar

O CI (`windows-latest`, workflow `.github/workflows/desktop.yml`) já tem tudo. Para compilar
localmente você precisa de:

- **Rust** (https://rustup.rs) e **Node 20+**
- **Visual Studio Build Tools** com "Desktop development with C++" — o toolchain
  `x86_64-pc-windows-msvc` precisa do `link.exe` e do Windows SDK (por isso o build oficial roda
  no CI).
- **WebView2 Runtime** — já vem no Windows 10/11 atualizado.

```bash
cd desktop
npm install
npm run build     # instalador em src-tauri/target/release/bundle/nsis/
```

Publicar uma versão dispara o CI:

```bash
git tag v0.1.0 && git push origin v0.1.0
```

## Roadmap: backend embarcado

Hoje o app precisa do stack no ar. O caminho para um instalador **autocontido** (sem Docker) já
foi validado num spike: Postgres 16 binário + pgvector rodam embarcados no Windows e as 55
migrações passam. Falta empacotar o backend Python e supervisioná-lo pelo shell.

Detalhes técnicos do shell estão em [`desktop/README.md`](../desktop/README.md).
