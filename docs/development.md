# Desenvolvimento

Como rodar o AI Workspace localmente sem Docker, a organização do código e como rodar os testes.

## Layout do monorepo

```
apps/
  server/                 Backend FastAPI
    aiworkspace/
      chat/               Orquestrador de turno, rotas de chat, guardas
      api/                API pública (/v1): rotas, chaves, limites, webhooks
      integrations/       Google, Tuya, GitHub, canais, transcrição…
      memory/             mem0
      knowledge/          Base de Conhecimento (RAG)
      codespace/          Grafo de código, projetos
      tracing/            Observabilidade (traces/spans)
      tools/              SIFT, loader, sandbox
      models/             ORM (SQLAlchemy)
      main.py             App, middleware, registro de rotas
    alembic/versions/     55 migrações
    tests/                Suíte pytest (hermética)
    pyproject.toml
  web/                    Frontend Next.js (App Router)
    app/                  Páginas
    components/           Componentes
    lib/                  Cliente HTTP, SSE, tipos, ponte desktop
desktop/                  Shell Tauri (Rust)
infra/                    Configs de serviços opcionais
docs/                     Esta documentação
```

## Backend sem Docker

Requer **Python ≥ 3.11** e um **Postgres 16 com pgvector** acessível.

```bash
cd apps/server
python -m venv .venv
. .venv/Scripts/activate          # Linux/macOS: source .venv/bin/activate
pip install -e ".[dev]"

# suba um Postgres com pgvector (exemplo):
docker run -d -p 5432:5432 \
  -e POSTGRES_PASSWORD=aiworkspace -e POSTGRES_USER=aiworkspace -e POSTGRES_DB=aiworkspace \
  pgvector/pgvector:pg16

export APP_SECRET="dev-insecure-only"       # Windows PowerShell: $env:APP_SECRET="..."
alembic upgrade head
uvicorn aiworkspace.main:app --reload
```

## Frontend sem Docker

```bash
cd apps/web
npm install
npm run dev
```

O `next build` também serve de **type-check** do frontend.

> As imagens Docker **embutem** o código no build — ao mexer no código, rode
> `docker compose up -d --build` para aplicar. O `web` é um build de produção
> (`next start`), então mudanças na UI **exigem** `docker compose build web`.

## Testes

A suíte do backend é **hermética** (sem rede/binários — as costuras de I/O são substituídas por
fakes):

```bash
cd apps/server
pytest -q
```

Convenções:

- Testes ficam em `apps/server/tests`. `pytest` é dependência `[dev]` — **não** vai na imagem de
  produção.
- Ao rodar dentro do container reconstruído, instale as deps de teste primeiro
  (`pip install --user pytest pytest-asyncio`) e copie a pasta `tests/`, pois a imagem prod não
  as inclui.
- Prefira testes que exercitam **decisão e shape** (parsing, roteamento, guardas) sobre I/O real.

## Migrações

Ao mudar um modelo, gere uma migração:

```bash
cd apps/server
alembic revision --autogenerate -m "descrição curta"
alembic upgrade head
```

As migrações são aplicadas **automaticamente** no startup do servidor.

## Estilo e princípios

- **Descrições de ferramentas** (o texto que o modelo lê) em **inglês**, diretas: o que faz +
  quando usar, sem detalhe de implementação.
- **UI limpa**: evite parágrafos explicativos sob headings; prefira um ícone "i" com tooltip.
- **Nunca** edite fontes do repo com ferramentas que reescrevem encoding (ex.: `Set-Content` do
  PowerShell corrompe UTF-8) — use um editor que preserve UTF-8.
- **Commit** só depois de tudo testado; o `.env` **nunca** é commitado.

Veja também o [CONTRIBUTING.md](../CONTRIBUTING.md).
