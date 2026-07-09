# AI Workspace

Workspace de IA **self-hosted, local-first** no estilo OpenWebUI — chat multi-modelo com
ferramentas, memória de longo prazo, automações, artefatos e integrações, tudo rodando na
**sua** máquina/servidor. A única saída externa é o provedor de modelos (OpenRouter) e o que
as suas ferramentas fizerem. Segredos ficam **criptografados em repouso** no banco.

> Stack: **FastAPI** (async) + **SQLAlchemy 2** + **Postgres 16 / pgvector** ·
> **Next.js 14** (App Router) + Tailwind · **[SIFT](https://github.com/Victor-Alves0/SIFT)**
> (tool calling) · **mem0** (memória) · **OpenRouter** · tudo em **Docker Compose**.

---

## Recursos

- **Chat multi-modelo** via OpenRouter (qualquer modelo compatível com OpenAI) e **modelos
  locais** via Ollama. Respostas em streaming, retomáveis após F5, com botão de **parar**.
- **Modelos personalizados** (estilo "Models"): prompt, parâmetros, ferramentas, capacidades,
  filtros, voz, memória e sub-agentes por modelo.
- **Ferramentas (SIFT)**: pesquisa na web, ler página, calculadora, data/hora, gráficos,
  diagramas (Excalidraw/Mermaid), cotações, **pesquisa profunda**, lembretes e monitores,
  perfil do usuário, e ferramentas que você mesmo escreve em Python (sandbox isolado).
- **Memória (mem0)** com escopos **global / por modelo / por chat** e **bancos de memória
  compartilháveis entre modelos**; revisão opcional antes de salvar.
- **Automações**: tarefas agendadas e monitores (preço, página, busca, RSS) que avisam você.
- **Artefatos** (estilo Claude): código/documentos/HTML/SVG/Mermaid/CSV numa janela dedicada,
  com edição, histórico de versões e compartilhamento.
- **Mesa-redonda**: vários modelos conversando entre si, com você guiando.
- **Geração de imagens** (nativa ou via roteador para um modelo de imagem).
- **Voz** (TTS/STT) por endpoint compatível com OpenAI, incluindo **Kokoro** local (opt-in).
- **Integrações**: **Google** (Gmail + Agenda), **Tuya/Smart Life** (casa inteligente) e
  **WhatsApp** (QR não oficial via Evolution API, ou Cloud API oficial da Meta).
- **Analítica** de uso (tokens/custo/requisições, por modelo e fonte), memória, atalhos de
  teclado, painel de **Debug** para admin.

---

## Arquitetura

```
apps/server   FastAPI — auth, orquestrador de chat, SIFT, mem0, integrações
apps/web      Next.js — UI de chat
db            Postgres 16 + pgvector (dados + vetores do mem0)
infra/        configs dos serviços opcionais (searxng, db-init)
```

Serviços do Docker Compose:

| Serviço    | Sempre sobe? | Como ligar                                   |
|------------|:------------:|----------------------------------------------|
| `db`       | ✅           | (padrão)                                     |
| `server`   | ✅           | (padrão)                                     |
| `web`      | ✅           | (padrão)                                     |
| `searxng`  | opt-in       | `docker compose --profile search up -d`      |
| `kokoro`   | opt-in       | `docker compose --profile voice up -d`       |
| `evolution`| opt-in       | `docker compose --profile whatsapp up -d`    |

---

## Início rápido (na sua máquina)

Pré-requisitos: **Docker** + **Docker Compose**.

```bash
git clone https://github.com/Victor-Alves0/AI-Workspace.git
cd AI-Workspace
cp .env.example .env

# gere um segredo forte e cole em APP_SECRET no .env:
python -c "import secrets; print(secrets.token_urlsafe(48))"

docker compose up -d --build
```

Abra **http://localhost:3000**. O **primeiro usuário cadastrado vira admin**. Depois, em
**⚙ Configurações → Conexões → APIs**, cole sua **chave do OpenRouter** (fica cifrada),
escolha um modelo e converse.

---

## Subir numa VPS / acessar pela rede (LAN)

O app já é feito para isso: o frontend **descobre o backend a partir do host da página**, então
o mesmo build funciona por `localhost`, pelo IP da LAN e pela VPS — sem rebuild. O que muda é
**CORS** e **firewall**.

### 1. Prepare o `.env`

```bash
cp .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(48))"   # cole em APP_SECRET
```

No `.env`, ajuste:

```dotenv
APP_ENV=production
APP_SECRET=<o valor gerado acima>
POSTGRES_PASSWORD=<uma senha forte>

# Origem que você vai abrir no navegador (IMPORTANTE: em produção o CORS aceita
# SÓ o que estiver aqui). Use o IP da VPS ou seu domínio, com a porta 3000:
WEB_ORIGIN=http://SEU_IP_OU_DOMINIO:3000

# Deixe VAZIO: o navegador chama o backend no mesmo host, porta 8000.
NEXT_PUBLIC_API_URL=
```

> Vai usar por vários endereços (ex.: localhost E o IP)? Liste separando por vírgula:
> `WEB_ORIGIN=http://localhost:3000,http://SEU_IP:3000`

### 2. Suba

```bash
docker compose up -d --build
```

### 3. Abra as portas no firewall da VPS

O app publica **3000** (web) e **8000** (server). Abra as duas:

```bash
# ufw (Ubuntu/Debian)
sudo ufw allow 3000/tcp
sudo ufw allow 8000/tcp
```

> Em provedores como AWS/GCP/Oracle, libere 3000 e 8000 também no **Security Group** do painel.

Acesse **http://SEU_IP:3000**. Pronto — cadastre-se (vira admin) e cole a chave do OpenRouter.

### Domínio + HTTPS (recomendado para produção real)

Coloque um proxy reverso (Caddy/nginx/Traefik) na frente, terminando TLS, apontando `/` para
`web:3000` e (se preferir separar) uma URL própria para `server:8000`. Nesse caso:

- `WEB_ORIGIN=https://seu-dominio.com`
- Se o backend tiver domínio próprio, defina `NEXT_PUBLIC_API_URL=https://api.seu-dominio.com`
  **e rebuild o web** (`docker compose up -d --build web`), pois essa URL é embutida no build.
- Ligue `TRUST_PROXY=true` para o rate-limit enxergar o IP real via `X-Forwarded-For`.
- Você pode nem publicar a porta do server (deixe só o proxy alcançá-lo): use `SERVER_BIND=127.0.0.1`.

---

## Serviços opcionais (profiles)

```bash
# Pesquisa na web self-hosted (SearXNG, sem chave de API)
docker compose --profile search up -d searxng
#   depois no .env: WEB_SEARCH_PROVIDER=searxng   (o server já aponta p/ searxng:8080)

# Voz local (Kokoro-FastAPI, TTS compatível com OpenAI em :8880)
docker compose --profile voice up -d kokoro
#   configure a conexão de voz na UI apontando para http://kokoro:8880/v1

# WhatsApp não oficial (Evolution API, QR Code)
docker compose --profile whatsapp up -d
#   requer EVOLUTION_API_KEY no .env; conecte números em Integrações → WhatsApp
```

Para subir tudo: `docker compose --profile search --profile voice --profile whatsapp up -d --build`.

---

## Configuração (`.env`)

Todas as chaves estão documentadas em **[.env.example](.env.example)**. As principais:

| Variável              | Para quê                                                                  |
|-----------------------|---------------------------------------------------------------------------|
| `APP_SECRET`          | **Obrigatório.** Assina JWT + deriva a chave de criptografia dos segredos.|
| `APP_ENV`             | `production` endurece o CORS e recusa segredo fraco.                      |
| `ENABLE_SIGNUP`       | Feche o cadastro (`false`) depois de criar sua conta.                     |
| `WEB_ORIGIN`          | Origem(ns) do frontend aceitas pelo CORS (crucial em produção).          |
| `NEXT_PUBLIC_API_URL` | URL do backend p/ o navegador; **vazio** = deriva do host (LAN/VPS).     |
| `POSTGRES_PASSWORD`   | Troque numa instalação real.                                             |
| `WEB_SEARCH_PROVIDER` | `duckduckgo` (padrão) · `searxng` · `tavily` · `brave`.                  |

> As chaves de provedores (OpenRouter, Tavily/Brave, voz) **não** vão no `.env`: cada usuário
> salva as suas na UI, cifradas em repouso.

---

## Atualizar

```bash
git pull
docker compose up -d --build
```

As **migrações do banco (Alembic) rodam sozinhas** no startup do server.

---

## Desenvolvimento (sem Docker)

Backend:

```bash
cd apps/server
python -m venv .venv && . .venv/Scripts/activate   # Linux/Mac: source .venv/bin/activate
pip install -e ".[dev]"
# suba um Postgres com pgvector, ex.:
#   docker run -d -p 5432:5432 -e POSTGRES_PASSWORD=aiworkspace -e POSTGRES_USER=aiworkspace -e POSTGRES_DB=aiworkspace pgvector/pgvector:pg16
alembic upgrade head
uvicorn aiworkspace.main:app --reload
```

Frontend:

```bash
cd apps/web
npm install
npm run dev
```

> As imagens Docker **embutem** o código no build — ao mexer no código, rode
> `docker compose up -d --build` para aplicar. `next build` também serve de type-check do web.

---

## Segurança

- Senhas com **Argon2**; sessão via **JWT em cookie httpOnly** (access + refresh rotativo);
  `token_version` para revogar todas as sessões.
- Segredos por usuário cifrados com **Fernet** (chave derivada do `APP_SECRET`).
- RBAC **admin/user**; cadastro desabilitável (`ENABLE_SIGNUP=false`).
- **CORS** restrito às origens de `WEB_ORIGIN`; cabeçalhos de segurança em todas as respostas;
  **HSTS** sob HTTPS.
- **Rate limiting** em login/registro (`LOGIN_MAX_ATTEMPTS`, `LOGIN_WINDOW_SECONDS`).
- Em `APP_ENV=production`, o servidor **recusa iniciar** com `APP_SECRET` fraco/curto.
- **Sandbox de ferramentas**: o código das tools roda em **subprocesso isolado** (`python -I`)
  com timeout e limites de CPU/memória — não no processo do servidor.
- Postgres publicado só em `127.0.0.1`; opção de allowlist de IP e `TRUST_PROXY` atrás de proxy.

> `allow_code_mode` executa código gerado pelo modelo (RCE por design, mitigado pelo sandbox).
> Em deploy multiusuário **não confiável**, avalie desligá-lo ou isolar ainda mais o sandbox.

---

## Licença

Uso pessoal / self-hosted. Sinta-se livre para adaptar à sua infraestrutura.
