# AI Workspace

Workspace de IA **self-hosted, local-first** no estilo OpenWebUI, montado a partir dos
seus "blocos":

- **OpenRouter** — provedor padrão de modelos (API compatível com OpenAI).
- **[SIFT](https://github.com/Victor-Alves0/SIFT)** — tool calling via 3 meta-tools
  (search/schema/execute), com compressão de schema e filtragem de resposta.
- **mem0** — memória de longo prazo da IA.

Tudo roda na sua máquina. A única saída externa é o OpenRouter (e o que as suas
ferramentas fizerem). Segredos (chave do OpenRouter) ficam **criptografados em repouso**.

## Arquitetura

```
apps/server   FastAPI (async) — auth, chat orchestrator, SIFT, mem0, OpenRouter
apps/web      Next.js (App Router) — UI de chat
db            Postgres 16 + pgvector (dados + vetores do mem0)
```

O loop de cada turno: `mem0.search` → monta contexto + meta-tools da SIFT →
streaming do OpenRouter → executa tools (SIFT) e repete se necessário →
resposta final → `mem0.add`. Tudo via SSE.

## Pré-requisitos

- Docker + Docker Compose.

## Subir (servidor / produção local)

```bash
cd ai-workspace
cp .env.example .env

# gere um segredo forte e cole em APP_SECRET no .env:
python -c "import secrets; print(secrets.token_urlsafe(48))"

docker compose up -d --build
```

Acesse **http://localhost:3000**. O **primeiro usuário cadastrado vira admin**.
Depois, em **⚙ Configurações**, cole sua **chave do OpenRouter** (criptografada em repouso),
crie um chat, escolha um modelo e converse.

## Desenvolvimento (sem Docker)

Backend:

```bash
cd apps/server
python -m venv .venv && . .venv/Scripts/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
# suba um Postgres com pgvector (ex.: docker run -p 5432:5432 ... pgvector/pgvector:pg16)
alembic upgrade head
uvicorn aiworkspace.main:app --reload
```

Frontend:

```bash
cd apps/web
npm install
npm run dev
```

## Plugar suas próprias ferramentas (SIFT)

Edite `apps/server/aiworkspace/tools/sift_service.py` e registre funções com
`@sift.tool(...)` dentro de `register_tools()`. O índice é reconstruído no startup.

## Segurança

- Senhas com Argon2; sessão via JWT em cookie httpOnly (access + refresh rotativo).
- Segredos por usuário cifrados com Fernet (chave derivada do `APP_SECRET`).
- RBAC admin/user; cadastro desabilitável via `ENABLE_SIGNUP=false`.
- CORS restrito à origem do frontend; cabeçalhos de segurança (`X-Frame-Options`,
  `X-Content-Type-Options`, `Referrer-Policy`) em todas as respostas.
- **Rate limiting** em login/registro (config: `LOGIN_MAX_ATTEMPTS`, `LOGIN_WINDOW_SECONDS`).
- **APP_SECRET**: em `APP_ENV=production` o servidor recusa iniciar com segredo fraco/curto.
- **Sandbox de ferramentas**: o código das tools NÃO roda no processo do servidor — cada
  execução acontece em subprocesso isolado (`python -I`) com timeout e limites de CPU/memória
  (`TOOL_TIMEOUT_SECONDS`, `TOOL_CPU_SECONDS`, `TOOL_MEM_MB`). Criação de tools é restrita a admin.
- Avaliação aritmética (tool de cálculo) usa parser AST seguro, sem `eval`.
- Erros não tratados viram `500` limpo (sem vazar stack); exceções são logadas.

## Painel de Debug (admin)

Acesse **🐞 Debug** na barra lateral (visível só para admin) ou `/debug`. Mostra:
info do sistema, saúde profunda (DB + pgvector), conectividade dos provedores (OpenRouter,
voz, SearXNG), estado do cache SIFT por usuário, métricas de request (latência/erros por rota)
e logs recentes filtráveis — com auto-refresh. Backend em `GET /debug/*` (somente admin).

## Roadmap

- [ ] Desktop via Tauri (sidecar Python).
- [ ] Anexos / base de conhecimento (RAG), web search, modo de voz (já desenhados no excalidraw).
- [ ] Pastas, presets e tags na UI.
