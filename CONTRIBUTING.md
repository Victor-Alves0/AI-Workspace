# Contribuindo com o AI Workspace

Obrigado pelo interesse! Este guia cobre o básico para propor mudanças.

> **Nota de licença:** este repositório ainda **não** define uma licença (ver
> [README](README.md#licença)). Enquanto isso não for resolvido, o uso e a redistribuição por
> terceiros não estão formalmente autorizados. Se você pretende contribuir de fora, abra uma
> issue antes para alinhar.

## Antes de começar

1. Leia o [docs/development.md](docs/development.md) para subir o projeto localmente.
2. Para mudanças não triviais, **abra uma issue** descrevendo o problema/proposta antes de
   escrever muito código.

## Fluxo de trabalho

1. Crie uma branch a partir da `main`.
2. Faça a mudança com testes quando fizer sentido.
3. Rode a suíte e o type-check:
   ```bash
   cd apps/server && pytest -q
   cd ../web && npm run build     # type-check do frontend
   ```
4. Abra um Pull Request descrevendo **o quê** e **por quê**.

## Padrões de código

- **Backend:** Python ≥ 3.11, FastAPI assíncrono, SQLAlchemy 2. Mudou um modelo? Gere a
  migração Alembic (`alembic revision --autogenerate -m "..."`).
- **Frontend:** Next.js (App Router) + TypeScript + Tailwind. O `next build` valida os tipos.
- **Descrições de ferramentas** (texto que o modelo lê): em **inglês**, direto — o que faz +
  quando usar, sem detalhe de implementação.
- **UI limpa:** evite parágrafos explicativos sob headings; use um ícone "i" com tooltip.
- Escreva código que **combina com o redor** — mesma densidade de comentários, nomes e idioma.

## Não faça

- **Não** commite o `.env` nem qualquer segredo (já está no `.gitignore`).
- **Não** edite fontes com ferramentas que corrompem UTF-8 (ex.: `Set-Content` do PowerShell).
- **Não** rode migrações destrutivas sem backup.

## Commits

- Mensagens claras, no imperativo, explicando a intenção.
- Commit só depois de testado.

## Reportar bugs e vulnerabilidades

- **Bugs:** abra uma issue com passos de reprodução, comportamento esperado vs. obtido e
  ambiente (SO, versão).
- **Vulnerabilidades de segurança:** **não** abra issue pública — siga o [SECURITY.md](SECURITY.md).
