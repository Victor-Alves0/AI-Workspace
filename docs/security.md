# Segurança

O AI Workspace guarda segredos sensíveis (chaves de API, tokens de OAuth/bot) e pode executar
código gerado por modelos. Esta página descreve o modelo de segurança e as recomendações de
operação.

## Autenticação e sessão

- **Senhas** com **Argon2** (não SHA/bcrypt simples).
- **Sessão** via **JWT em cookie httpOnly** — access token curto (~30 min) + refresh token
  rotativo (dias). O front renova sozinho no `401`.
- **Revogação global** por `token_version`: um bump invalida todas as sessões do usuário.
- **2FA (TOTP)** opcional, com QR e desafio no login.
- **RBAC** admin/user. O primeiro usuário cadastrado vira admin; feche o cadastro com
  `ENABLE_SIGNUP=false`.

## Segredos em repouso

- Segredos **por usuário** são cifrados com **Fernet**, com a chave **derivada do `APP_SECRET`**
  — nunca são gravados em claro no banco.
- `APP_SECRET` **não** dá acesso ao banco (isso é a senha do Postgres); ele cifra os **valores**.
- Há uma ferramenta de **rotação** que re-cifra o banco inteiro ao trocar o `APP_SECRET`, sem
  perder os segredos (ver [deployment.md](deployment.md#rota%C3%A7%C3%A3o-do-app_secret)).
- O `.env` (que tem `APP_SECRET` e a senha do Postgres) **nunca** deve ser versionado — já está
  no `.gitignore`.

## Rede e transporte

- **CORS** restrito às origens de `WEB_ORIGIN`. Em `APP_ENV=production`, **só** as origens
  exatas são aceitas (sem liberar a LAN automaticamente).
- **Cabeçalhos de segurança** em todas as respostas (`X-Content-Type-Options`,
  `X-Frame-Options`, `Referrer-Policy`) e **HSTS** sob HTTPS.
- **Allowlist de IP** opcional no nível do servidor.
- Atrás de proxy reverso confiável, ligue `TRUST_PROXY=true` para o rate-limit usar o IP real.
- O Postgres **não** é publicado no host por padrão — a app o acessa pela rede interna do Compose.

## Rate limiting

- **Login/registro**: `LOGIN_MAX_ATTEMPTS` por `LOGIN_WINDOW_SECONDS`.
- **API pública**: por chave — RPM, RPD, mensal, tokens, concorrência e orçamento (ver
  [public-api.md](public-api.md)).

## Execução de ferramentas (sandbox)

A IA pode escrever e executar **código Python** (o vetor de maior risco — RCE por design).
Mitigações:

- O código roda num **subprocesso isolado** (`python -I`), **não** no processo do servidor.
- Limites de **CPU**, **memória** e **tempo de parede** (`TOOL_*`, `SIFT_CODE_TIMEOUT_SECONDS`).
- **Guarda anti-SSRF** nas ferramentas de navegação/leitura de página (bloqueia IPs internos,
  sem `file://`).

> ⚠️ O sandbox limita CPU/memória/tempo, mas **não** isola a rede nem o `/proc`. Em um deploy
> **multiusuário não confiável**, avalie desligar `ALLOW_CODE_MODE`, ou endurecer o sandbox
> (rede desligada, `hidepid`, nsjail/gVisor) e entregar segredos por arquivo. Numa instalação
> **single-user self-hosted** (o caso comum), o risco é o seu próprio código.

## Endurecimento em produção

- `APP_ENV=production` faz o servidor **recusar iniciar** com `APP_SECRET` fraco/curto.
- HTTPS com proxy reverso + `TRUST_PROXY=true`.
- `ENABLE_SIGNUP=false` após criar sua conta.
- Backups regulares (ver [deployment.md](deployment.md#backup-e-restore)).
- Observabilidade: `OBS_CAPTURE_CONTENT` fica **desligado** por padrão (não grava texto de
  mensagens/prompts) — só ligue para depurar.

## Reportar uma vulnerabilidade

Veja [SECURITY.md](../SECURITY.md).
