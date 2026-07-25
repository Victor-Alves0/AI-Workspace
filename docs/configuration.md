# Configuração

Toda a configuração de **infraestrutura** vem de variáveis de ambiente, lidas de um arquivo
`.env` na raiz. Comece copiando o modelo:

```bash
cp .env.example .env
```

O [`.env.example`](../.env.example) é a fonte da verdade — cada variável está comentada lá.
Esta página organiza e explica as principais.

> **Segredos por usuário** (chaves de OpenRouter, Tavily/Brave, provedor de voz, tokens de
> bot/OAuth) **não** ficam no `.env`: cada usuário salva os seus na interface, cifrados em
> repouso. No `.env` ficam apenas os segredos de **infraestrutura**.

## Segurança

| Variável        | Padrão        | Descrição |
|-----------------|---------------|-----------|
| `APP_ENV`       | `development` | `production` endurece o CORS (aceita só as origens exatas de `WEB_ORIGIN`) e **recusa iniciar** com um `APP_SECRET` fraco. |
| `APP_SECRET`    | —             | **Obrigatório trocar.** Assina os JWT e **deriva a chave de criptografia** dos segredos guardados no banco. Gere com `python -c "import secrets; print(secrets.token_urlsafe(48))"`. |
| `ENABLE_SIGNUP` | `true`        | Permite cadastro. O **primeiro** usuário vira admin; depois considere `false` para fechar. |

> **`APP_SECRET` não dá acesso ao banco** (isso é a senha do Postgres) — ele cifra os
> **valores** dos segredos. Trocá-lo à toa torna esses valores ilegíveis (a app os trata como
> "não configurados") e desloga todo mundo. Precisa trocar sem perder os segredos? Use a
> **rotação** (ver [deployment.md](deployment.md#rota%C3%A7%C3%A3o-do-app_secret)).

## Banco de dados

| Variável            | Padrão        | Descrição |
|---------------------|---------------|-----------|
| `POSTGRES_USER`     | `aiworkspace` | Usuário do Postgres. |
| `POSTGRES_PASSWORD` | `aiworkspace` | **Troque numa instalação real.** |
| `POSTGRES_DB`       | `aiworkspace` | Nome do banco. |
| `DATABASE_URL`      | (montada)     | Só mexa para rodar o server **fora** do Docker. |
| `DB_PORT` / `DB_BIND` | (comentado) | O Postgres **não** é publicado no host por padrão. Para conectar um cliente externo (psql/DBeaver), copie `docker-compose.override.yml.example` e ajuste. |

## Modelos (OpenRouter)

| Variável              | Padrão                          | Descrição |
|-----------------------|---------------------------------|-----------|
| `OPENROUTER_BASE_URL` | `https://openrouter.ai/api/v1`  | Endpoint do provedor de modelos. A **chave** é salva por usuário na UI, não aqui. |

## Rede e acesso

Esta seção é a que você ajusta para acessar de **outra máquina** (LAN ou VPS).

| Variável              | Padrão                   | Descrição |
|-----------------------|--------------------------|-----------|
| `WEB_ORIGIN`          | `http://localhost:3000`  | Origem(ns) do frontend aceitas pelo **CORS**, separadas por vírgula. Em produção, **só** estas são aceitas — inclua a que você abrirá no navegador. |
| `NEXT_PUBLIC_API_URL` | (vazio)                  | URL do backend que o **navegador** usa, embutida no build. **Deixe vazio** para o front derivar a API do host da página (funciona por localhost/LAN/VPS sem rebuild). Só preencha se o backend tiver domínio próprio — e então **rebuild o web**. |
| `TRUST_PROXY`         | `false`                  | Ligue **apenas** atrás de um proxy reverso confiável, para o rate-limit usar `X-Forwarded-For`. Sem proxy o header é forjável. |

### Bind e portas

Todos publicados em `0.0.0.0` por padrão (exceto os opt-in, em `127.0.0.1`). Se uma porta já
estiver ocupada, troque a correspondente — tudo funciona igual, só muda por onde você acessa.

| Variável        | Padrão    | Serviço            |
|-----------------|-----------|--------------------|
| `WEB_BIND` / `WEB_PORT`       | `0.0.0.0` / `3000` | Interface web |
| `SERVER_BIND` / `SERVER_PORT` | `0.0.0.0` / `8000` | API |
| `SEARXNG_PORT`   | `8080`   | Profile `search`   |
| `EVOLUTION_PORT` | `8081`   | Profile `whatsapp` |
| `KOKORO_PORT`    | `8880`   | Profile `voice`    |
| `BROWSER_PORT`   | `3009`   | Profile `browser`  |

## Pesquisa na web

| Variável                | Padrão       | Descrição |
|-------------------------|--------------|-----------|
| `WEB_SEARCH_PROVIDER`   | `duckduckgo` | `duckduckgo` (sem chave) · `searxng` · `tavily` · `brave`. Chaves de Tavily/Brave são por usuário. |
| `SEARXNG_URL`           | `http://searxng:8080` | Só mude para apontar a um SearXNG externo (precisa do formato JSON habilitado). |
| `WEB_SEARCH_MAX_RESULTS`| `5`          | Máximo de resultados por busca. |

## Voz (TTS/STT)

| Variável         | Padrão                     | Descrição |
|------------------|----------------------------|-----------|
| `VOICE_BASE_URL` | `https://api.openai.com/v1`| Endpoint compatível com OpenAI. Pode apontar para o Kokoro local (`http://kokoro:8880/v1`) ou outro servidor. |
| `TTS_MODEL`      | `tts-1`                    | Modelo de síntese. |
| `TTS_VOICE`      | `alloy`                    | Voz padrão. |
| `STT_MODEL`      | `whisper-1`                | Modelo de transcrição. |

## Integrações opt-in

| Variável                 | Descrição |
|--------------------------|-----------|
| `EVOLUTION_API_KEY`      | Chave do serviço Evolution (WhatsApp não oficial). Gere uma forte. |
| `WHATSAPP_WEBHOOK_BASE`  | URL pública https do server para webhooks do **Cloud API oficial** da Meta. |
| `BROWSER_WS_URL` / `BROWSER_TOKEN` | Endpoint CDP e token do serviço `browser` (navegador headless). |
| `TRANSCRIBE_COOKIES_DIR` | Pasta com arquivos `cookies.txt` (Netscape) rotacionados pela transcrição de vídeo, para reduzir bloqueio. Vazio = só headers realistas. |

## Observabilidade

| Variável              | Padrão | Descrição |
|-----------------------|--------|-----------|
| `OBS_ENABLED`         | `true` | Liga/desliga o registro de traces. |
| `OBS_SAMPLE_RATE`     | `1.0`  | Fração amostrada [0..1]. Erros e traces lentos são sempre mantidos. |
| `OBS_RETENTION_DAYS`  | `14`   | Retenção (poda por dias). |
| `OBS_CAPTURE_CONTENT` | `false`| **Sensível.** Libera texto de mensagens/prompts nos spans — só para depurar. |

## Autenticação / rate-limit

| Variável               | Padrão | Descrição |
|------------------------|--------|-----------|
| `ACCESS_TOKEN_TTL_MIN` | `30`   | Validade do access token (minutos). |
| `REFRESH_TOKEN_TTL_DAYS`| `30`  | Validade do refresh token (dias). |
| `LOGIN_MAX_ATTEMPTS`   | `10`   | Tentativas de login por janela. |
| `LOGIN_WINDOW_SECONDS` | `300`  | Janela do rate-limit de login (segundos). |

## Sandbox de ferramentas

| Variável                    | Padrão | Descrição |
|-----------------------------|--------|-----------|
| `ALLOW_CODE_MODE`           | `true` | Executa código **gerado pelo modelo** no sandbox (RCE por design, mitigado). Desligue em deploy multiusuário não confiável. |
| `TOOL_TIMEOUT_SECONDS`      | `10`   | Timeout de parede de uma ferramenta. |
| `TOOL_CPU_SECONDS`          | `5`    | Teto de CPU do subprocesso. |
| `TOOL_MEM_MB`               | `256`  | Teto de memória do subprocesso. |
| `SIFT_CODE_TIMEOUT_SECONDS` | `30`   | Timeout do `run_code` da SIFT. |

> Consulte o [`.env.example`](../.env.example) para a lista completa, incluindo variáveis
> avançadas não listadas aqui.
