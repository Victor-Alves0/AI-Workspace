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
| **Google Workspace** | OAuth completo implementado, mas o admin precisa criar o projeto no console e colar Client ID/Secret. O device flow do Google **não serve**: ele cobre um conjunto restrito de escopos que não inclui Gmail. |
| **Notion, Slack** | Mesmo caso: app registrado pelo admin, secret, callback fixo. |

Para estes, o caminho de virar botão é o **app embutido**: registrar os apps uma vez,
distribuir as credenciais com o build e deixar a tela do admin apenas como override.
Esbarra em duas coisas reais: o secret não pode ir para um repositório público, e o
`redirect_uri` teria que estar cadastrado para cada endereço de instalação possível.
A saída usual é um endpoint de retorno hospedado por nós, que reencaminha para a
instalação — infraestrutura que hoje não existe.

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

**WhatsApp** é o caso à parte que já é botão sem OAuth: a conexão pelo Evolution é
por QR Code.

## Ao adicionar uma integração nova

Procure PKCE ou device flow antes de aceitar um campo de chave. Se o serviço tiver
os dois, prefira PKCE quando houver navegador (redirect é menos passos) e device
flow quando o retorno for problema. Se só houver chave, deixe isso explícito na tela
— o usuário merece saber que a fricção é do provedor, não do AI Workspace.
