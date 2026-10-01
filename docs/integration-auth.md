# Como cada integração autentica

O objetivo é que conectar um serviço seja **clicar um botão**, não caçar uma chave
num painel de desenvolvedor e colar. Este documento diz onde já é assim, o que falta
para o resto, e — o mais importante — **quais integrações nunca vão virar botão**,
porque o serviço do outro lado não oferece OAuth.

## O que decide se dá para ser botão

Três perguntas, nesta ordem:

1. **O serviço tem OAuth?** Se não tem (só emite chave de API num painel), acabou:
   é colar chave, e nenhuma engenharia nossa muda isso.
2. **O OAuth exige *client secret*?** Se exige, alguém precisa registrar um app e
   guardar o segredo. Num produto self-hosted isso significa: ou o admin registra o
   app dele, ou nós embutimos um segredo no repositório — que, sendo público, deixa
   de ser segredo.
3. **O OAuth exige *redirect URI* cadastrado?** Se exige, cada instalação (desktop,
   `localhost:8000`, VPS com domínio próprio) precisa do endereço dela na whitelist
   do provedor. É o que mais atrapalha o self-hosted.

Os dois fluxos que passam por essas três perguntas sem tropeçar são **PKCE** (sem
segredo, callback livre) e **device flow** (sem segredo, sem callback).

## Situação por integração

### Botão hoje, sem configurar nada

| Integração | Fluxo | Observação |
|---|---|---|
| **OpenRouter** | OAuth PKCE | Nem app registrado, nem secret, nem callback cadastrado. Vale para qualquer instalação. Ver `integrations/openrouter_oauth.py`. |
| **Assinatura ChatGPT/Codex** | Device code (principal) + OAuth PKCE (fallback) | Login pela conta, sem chave de API. O device flow funciona em qualquer origem/IP e o painel consulta o resultado automaticamente. |

O painel usa o mesmo device flow do Codex CLI: abre
`https://auth.openai.com/codex/device`, mostra um código de uso único e consulta a
autorização no servidor. Não há callback para a origem da instalação, portanto
`localhost`, IP de LAN, domínio e servidor remoto funcionam do mesmo modo. Se device
auth estiver desabilitado na conta/workspace, o botão de login alternativo mantém o
fluxo PKCE: o callback automático funciona na mesma máquina e a colagem da URL fica
disponível para o caso remoto.

### Botão, dependendo de um `client_id` público no build

| Integração | Fluxo | O que falta |
|---|---|---|
| **GitHub** | Device flow | Registrar **um** OAuth App com "Enable Device Flow" e pôr o `client_id` em `GITHUB_DEVICE_CLIENT_ID`. Não há secret envolvido — o `client_id` é público por definição e pode ir na imagem. Sem ele, a tela cai no Personal Access Token. |

### OAuth existe, mas exige app registrado + secret + callback

| Integração | Situação |
|---|---|
| **Notion, Slack** | App registrado pelo admin, secret, callback fixo. Próximos a migrar para o modelo do Google (abaixo). |

### Google Workspace: app próprio da instalação (decisão de 01/10/2026)

Como no OpenClaw e no Hermes Agent, cada instalação usa o **próprio** cliente OAuth
(o admin cria no Google Cloud e cola Client ID/Secret em Integrações → Google
Workspace). O retorno é direto para a instalação (`GOOGLE_REDIRECT_URI`, que o
Google só aceita em `localhost` ou domínio HTTPS). Um app do projeto exigiria
verificação do Google para os escopos do Gmail (avaliação de segurança paga) — por
isso ficou de fora.

"Conectar conta Google" abre o seletor de contas **fora do app** (no desktop, no
navegador do sistema) e a tela acompanha o resultado sozinha.

O que segue abaixo (app embutido + página de retorno) está implementado mas
**desligado**: só entra em uso se `_BUILTIN_CLIENT_ID/SECRET` (ou
`GOOGLE_APP_CLIENT_ID/SECRET`) forem preenchidos e o GitHub Pages for ligado.

1. **App embutido** — um cliente OAuth do tipo Web, registrado uma vez pelo projeto
   (`_BUILTIN_CLIENT_ID/SECRET` em `google_service.py`, ou `GOOGLE_APP_CLIENT_ID/SECRET`).
   O app próprio do admin, se salvo na UI, tem prioridade. Cada conta guarda o
   `client_id` que a emitiu, porque só aquele app renova o token.
2. **Página de retorno** (`site/oauth/index.html`, publicada no GitHub Pages por
   `.github/workflows/pages.yml`) — o único redirect cadastrado no Google. Ela lê
   `ret` + `cb` do `state` e manda o navegador para a instalação: IP de LAN,
   `localhost:41414` do desktop ou domínio. Endereço local segue direto; domínio
   público pede um clique ("Continuar").
3. **PKCE** — o `code` passa pela página, então sozinho não vale nada: o
   verificador é derivado do `APP_SECRET` + nonce da tentativa e nunca sai da
   instalação.

O que isso **não** resolve: um app que pede Gmail precisa estar publicado ("Em
produção") no Google — em teste o refresh token expira em 7 dias — e, até passar
pela verificação do Google (escopos restritos exigem avaliação de segurança), a tela
de consentimento mostra "o Google não verificou este app" e o limite é de 100
usuários. O device flow do Google **não serve**: não cobre os escopos do Gmail.

Como OpenClaw e Hermes Agent fazem (set/2026): cada usuário cria o próprio projeto no
Google Cloud e aponta o `client_secret.json` (o Hermes cola a URL de retorno de
volta no chat). O `gws auth setup` automatiza isso via `gcloud`. O OpenCode não tem
integração Google.

> **Armadilha já corrigida:** o `.env.example` documentava `GOOGLE_REDIRECT_URI` e
> companhia, mas o `docker-compose.yml` não repassava essas variáveis ao container.
> Definir a variável não tinha efeito nenhum: o server ficava preso no default
> `localhost:8000`, e numa VPS o OAuth simplesmente não fechava. Ver o bloco
> `environment:` do serviço `server`.

### Sem OAuth — a chave é inevitável

Não é limitação nossa; estes serviços não oferecem outro caminho.

| Integração | Credencial |
|---|---|
| **ElevenLabs** | chave de API |
| **Higgsfield** | chave de API |
| **Tuya / Smart Life** | Access ID + Secret (assinatura HMAC) |
| **Vercel** | token de acesso |
| **Spotify** | Client ID/Secret (client credentials, só busca no catálogo) |
| **Telegram** | token de bot (BotFather) |
| **Discord** | token de bot (Developer Portal) |
| **LiteLLM / provedores personalizados** | chave do próprio endpoint |

**WhatsApp** é o caso à parte que já é botão sem OAuth: a conexão embutida (whatsmeow) é
por QR Code.

## Ao adicionar uma integração nova

Procure PKCE ou device flow antes de aceitar um campo de chave. Se o serviço tiver
os dois, prefira PKCE quando houver navegador (redirect é menos passos) e device
flow quando o retorno for problema. Se só houver chave, deixe isso explícito na tela
— o usuário merece saber que a fricção é do provedor, não do AI Workspace.
