# Singularity AI — Desktop (Tauri)

Aplicativo Windows que abre o Singularity AI numa janela nativa, com **ícone na
bandeja**, **"rodar em segundo plano"** e **"iniciar com o Windows"**.

## Como funciona

A janela carrega a **mesma interface web** que você usa no navegador (por padrão
`http://localhost:3000`) em vez de assets empacotados. Isso é deliberado: a sessão é
um cookie `httpOnly` emitido pela API, e servir a UI de uma origem `tauri://` tornaria
toda chamada cross-origin — o login não colaria. Apontando para a origem real, o
comportamento é idêntico ao do navegador, e o CORS/cookies que já funcionam continuam
valendo.

O shell acrescenta o que um navegador não dá:

| Recurso | Onde configurar |
|---|---|
| Fechar esconde na bandeja (em vez de sair) | Configurações → Aplicativo, ou o menu da bandeja |
| Iniciar com o Windows | idem |
| Abrir minimizado ao iniciar com o Windows | idem |
| Endereço do servidor (apontar p/ outra máquina) | idem |

As preferências são **da máquina**, não da conta: ficam em
`%APPDATA%\com.aiworkspace.app\desktop-settings.json`, não no perfil do usuário no
banco. Guardá-las no servidor faria o celular exibir "iniciar com o Windows", e dois
PCs com a mesma conta brigariam pelo mesmo valor. Por isso a categoria "Aplicativo"
só aparece nas Configurações quando a UI está rodando dentro do app instalado.

## Baixar

O instalador é publicado em [Releases](../../releases) — não é versionado no
repositório (binário em git fica no histórico para sempre). Quem compila é o CI, em
`.github/workflows/desktop.yml`.

## Compilar

O runner `windows-latest` do CI já tem tudo. Para compilar localmente você precisa de:

- **Rust** (https://rustup.rs)
- **Node 20+**
- **Visual Studio Build Tools** com "Desktop development with C++" — o toolchain
  `x86_64-pc-windows-msvc` precisa do `link.exe` e do Windows SDK. Sem isso o build
  falha no link (é justamente por isso que o build oficial roda no CI).
- **WebView2 Runtime** — já vem no Windows 10/11 atualizado.

```bash
cd desktop
npm install
npm run dev      # desenvolvimento
npm run build    # gera o instalador em src-tauri/target/release/bundle/nsis/
```

Publicar uma versão:

```bash
git tag v0.1.0 && git push origin v0.1.0   # o CI compila e cria a Release
```

## Segurança

A interface roda numa **origem remota** (`localhost`), então o acesso ao IPC do Tauri
é restrito por `src-tauri/capabilities/default.json`: só as URLs de localhost listadas
ali podem chamar comandos, e a única permissão concedida é `core:default`. Os dois
comandos expostos (`desktop_get_settings` / `desktop_set_settings`) leem e gravam
apenas o arquivo de preferências acima.

## Próximo passo: backend embarcado

Hoje o app **não** embarca o servidor — ele precisa do stack no ar. O caminho para um
instalador autocontido (sem Docker) já foi validado num spike: Postgres 16 binário +
`pgvector` 0.8.3 rodam embarcados no Windows e as 55 migrations passam. Falta empacotar
o backend Python e supervisioná-lo aqui no shell.
