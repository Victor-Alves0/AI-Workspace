# Atualização do aplicativo desktop

Como o usuário do `.exe` sai de uma versão para a próxima. Três caminhos, do mais
automático ao manual — todos preservam os dados.

## Por que os dados sobrevivem

O que a atualização substitui e o que ela não toca:

| | Onde fica | No update |
|---|---|---|
| Binários (app, motor: Postgres + backend) | pasta de instalação (`resource_dir`) | **substituídos** |
| Dados (banco, uploads, configurações) | `app_data_dir()/data` | **preservados** |

O instalador é NSIS com `installMode: currentUser` e o `identifier`
(`com.aiworkspace.app`) é estável, então rodar um instalador novo sobre uma instalação
existente **atualiza no lugar** — não cria uma segunda cópia nem pede desinstalação.

## 1. Atualização in-app (automática)

`Configurações → Sobre` mostra "vX.Y.Z disponível" e o botão **Atualizar para vX.Y.Z**:
o `tauri-plugin-updater` lê o `latest.json` **assinado** da release, baixa o instalador,
aplica por cima e reinicia o app.

Exige que a release traga o `latest.json` — o que só acontece com a chave de assinatura
configurada no CI (abaixo). Sem ela, o botão não aparece e o app cai no caminho 2.

## 2. Baixar o instalador (manual, sempre disponível)

O mesmo aviso oferece **Baixar instalador**, que abre a página da release. Baixe o `.exe`
e rode por cima — mesmo efeito da atualização in-app, só que sem o download automático.

> No app desktop, links externos passam por `openExternal` (`lib/desktop.ts`). Um
> `<a target="_blank">` **não faz nada** no webview do Tauri: não há handler de nova
> janela nem plugin de shell.

## 3. Deploy em servidor (Docker)

Não usa instalador: `./update.sh` no host puxa o branch, reconstrói e migra. O painel
`Admin → Atualização` compara **release e commit** (a imagem grava o commit de build em
`GIT_COMMIT`; ver `update.sh`).

## Ligando a atualização in-app (uma vez)

A chave PÚBLICA já está em `tauri.conf.json` → `plugins.updater.pubkey`. Falta a privada:

```bash
# 1) gere o par (guarde o arquivo .key fora do repositório — é segredo)
npx @tauri-apps/cli signer generate -w aiworkspace.key

# 2) no GitHub: Settings > Secrets and variables > Actions
#    TAURI_SIGNING_PRIVATE_KEY          = conteúdo do arquivo aiworkspace.key
#    TAURI_SIGNING_PRIVATE_KEY_PASSWORD = a senha (vazio se gerou sem senha)

# 3) atualize o pubkey em tauri.conf.json com o conteúdo de aiworkspace.key.pub
```

O workflow (`.github/workflows/desktop.yml`) detecta o secret e só então liga
`createUpdaterArtifacts`. Isso é proposital: com a flag ligada e **sem** a chave, o build
falha — e uma funcionalidade opcional derrubaria a release inteira.

**Se perder a chave privada, os apps já instalados param de aceitar atualizações** (a
assinatura deixa de bater). Guarde-a junto com o certificado de code signing.

## Repositório privado

O endpoint do updater e o download do instalador apontam para as Releases do GitHub. Num
repositório **privado** eles exigem autenticação, que o app do usuário final não tem —
na prática só funciona para quem tem acesso ao repo. Para distribuir de verdade, as
releases precisam estar num canal público (repositório público, um repo separado só de
releases, ou um endpoint próprio).
