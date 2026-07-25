# Deployment e operação

Como colocar o AI Workspace no ar além do `localhost`: rede local, VPS, domínio com HTTPS,
além de atualização, backup/restore e rotação de segredo.

## Sumário

- [Rede local / VPS](#rede-local--vps)
- [Domínio + HTTPS](#domínio--https)
- [Atualizar](#atualizar)
- [Backup e restore](#backup-e-restore)
- [Rotação do APP_SECRET](#rotação-do-app_secret)

## Rede local / VPS

O app já é feito para isso: o frontend **descobre o backend a partir do host da página**, então
o mesmo build funciona por `localhost`, pelo IP da LAN e pela VPS — **sem rebuild**. O que muda
é **CORS** e **firewall**.

### 1. Prepare o `.env`

```bash
cp .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(48))"   # cole em APP_SECRET
```

Ajuste:

```dotenv
APP_ENV=production
APP_SECRET=<o valor gerado>
POSTGRES_PASSWORD=<uma senha forte>

# Origem que você vai abrir no navegador. Em produção o CORS aceita SÓ o que estiver
# aqui. Use o IP da VPS ou seu domínio, com a porta 3000:
WEB_ORIGIN=http://SEU_IP_OU_DOMINIO:3000

# Deixe VAZIO: o navegador chama o backend no mesmo host, porta 8000.
NEXT_PUBLIC_API_URL=
```

> Vai usar por vários endereços (ex.: localhost **e** o IP)? Liste separando por vírgula:
> `WEB_ORIGIN=http://localhost:3000,http://SEU_IP:3000`

### 2. Suba

```bash
docker compose up -d --build
```

### 3. Abra as portas no firewall

O app publica **3000** (web) e **8000** (server):

```bash
# ufw (Ubuntu/Debian)
sudo ufw allow 3000/tcp
sudo ufw allow 8000/tcp
```

> Em AWS/GCP/Oracle, libere 3000 e 8000 também no **Security Group** do painel.

Acesse **http://SEU_IP:3000**, cadastre-se (vira admin) e cole a chave do OpenRouter.

## Domínio + HTTPS

Para produção real, coloque um **proxy reverso** (Caddy/nginx/Traefik) na frente, terminando
TLS, apontando `/` para `web:3000` e (se preferir separar) uma URL própria para `server:8000`.
Nesse caso:

- `WEB_ORIGIN=https://seu-dominio.com`
- Se o backend tiver domínio próprio, defina `NEXT_PUBLIC_API_URL=https://api.seu-dominio.com`
  **e rebuild o web** (`docker compose up -d --build web`) — essa URL é embutida no build.
- Ligue `TRUST_PROXY=true` para o rate-limit enxergar o IP real via `X-Forwarded-For`.
- Você pode nem publicar a porta do server: deixe só o proxy alcançá-lo com
  `SERVER_BIND=127.0.0.1`.

Exemplo mínimo com **Caddy** (TLS automático):

```caddy
seu-dominio.com {
    reverse_proxy localhost:3000
}
api.seu-dominio.com {
    reverse_proxy localhost:8000
}
```

## Atualizar

O jeito recomendado é o script na raiz, que valida, reconstrói e migra:

```bash
./update.sh
```

Ele: confere que não há alterações locais, faz `git pull --ff-only`, reconstrói as imagens,
sobe os containers e roda `alembic upgrade head`. Manualmente seria:

```bash
git pull
docker compose up -d --build
```

> As **migrações do banco rodam sozinhas** no startup do server; o passo explícito no
> `update.sh` é só uma garantia. O painel do admin avisa quando há uma versão nova.

## Backup e restore

Todo o estado mora no Postgres (dados + vetores + embeddings). Um `pg_dump` cobre tudo.

**Backup:**

```bash
docker compose exec -T db pg_dump -U aiworkspace -Fc aiworkspace > backup.dump
```

**Restore** (num ambiente novo/limpo):

```bash
docker compose up -d db
docker compose exec -T db pg_restore -U aiworkspace -d aiworkspace --clean --if-exists < backup.dump
docker compose up -d
```

> **Para migrar de máquina, leve o mesmo `APP_SECRET`.** Os segredos por usuário estão cifrados
> com uma chave derivada dele; com um `APP_SECRET` diferente, o banco restaura mas os segredos
> ficam ilegíveis (a app os trata como "não configurados"). O painel do admin também oferece
> exportar/restaurar por lá.

## Rotação do `APP_SECRET`

Precisa trocar o `APP_SECRET` (vazou, política, etc.) **sem** perder os segredos guardados? Há
uma ferramenta que re-cifra tudo da chave antiga para a nova. Rode primeiro em modo simulação:

```bash
docker compose exec -e NEW_APP_SECRET=<nova> server \
  python -m aiworkspace.secret_rotation --dry-run

docker compose exec -e NEW_APP_SECRET=<nova> server \
  python -m aiworkspace.secret_rotation
```

Depois troque `APP_SECRET=<nova>` no `.env` e reinicie:

```bash
docker compose up -d server
```

## Checklist de produção

- [ ] `APP_ENV=production` e `APP_SECRET` forte (gerado, não o padrão).
- [ ] `POSTGRES_PASSWORD` trocada.
- [ ] `WEB_ORIGIN` com a(s) origem(ns) reais; `NEXT_PUBLIC_API_URL` vazio (ou o domínio da API).
- [ ] `ENABLE_SIGNUP=false` depois de criar sua conta.
- [ ] Proxy reverso com HTTPS e `TRUST_PROXY=true`.
- [ ] Firewall/Security Group liberando só o necessário.
- [ ] Rotina de backup (`pg_dump`) agendada.
- [ ] Avaliar `ALLOW_CODE_MODE=false` se houver usuários não confiáveis.
