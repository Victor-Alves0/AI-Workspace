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

> O `identifier` guarda o nome **antigo** de propósito. É ele que define
> `app_data_dir()` — a pasta com o banco e os uploads. Trocá-lo para acompanhar o
> rebrand faria cada instalação existente perder de vista os próprios dados (eles
> continuariam no disco, órfãos, em `%APPDATA%\com.aiworkspace.app`).

## Renomear o produto quebra a atualização no lugar (uma vez)

`productName` mudou de "AI Workspace" para "Singularity AI". Isso **não** é inócuo:
no template NSIS do Tauri a chave de desinstalação é derivada do PRODUCTNAME, não do
identifier —

```nsi
!define UNINSTKEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\${PRODUCTNAME}"
StrCpy $INSTDIR "$LOCALAPPDATA\${PRODUCTNAME}"      ; installMode currentUser
```

— então o instalador novo procura por `...\Uninstall\Singularity AI`, não acha, e se
comporta como **primeira instalação**. Consequência para quem já tinha o app:

| | O que acontece |
|---|---|
| Dados (banco, uploads, config) | **preservados** — vivem sob o `identifier`, que não mudou |
| Instalação antiga | **fica no disco**, em `%LOCALAPPDATA%\AI Workspace`, com entrada própria em "Aplicativos e recursos" |
| Atalhos | passam a existir dois |

Ou seja: ninguém perde nada, mas fica com **duas cópias instaladas**. Na primeira
release com o nome novo, oriente a desinstalar "AI Workspace" pelo Windows — é
seguro, porque o desinstalador só apaga a pasta de instalação, não os dados.

Dá para automatizar com um `NSIS_HOOK_PREINSTALL` que roda o desinstalador antigo em
silêncio, mas **não faça isso sem testar o instalador de verdade**: se os parâmetros
de modo silencioso estiverem errados, o desinstalador antigo pode entender que deve
apagar os dados do aplicativo — e aí a perda é permanente.

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
