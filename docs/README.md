# Documentação — AI Workspace

Guia de referência do AI Workspace. Se você só quer subir e usar, comece pelo
[README principal](../README.md); as páginas abaixo aprofundam cada área.

## Índice

| Documento | Para quê |
|-----------|----------|
| [features.md](features.md)           | Catálogo completo de recursos, agrupado por área |
| [architecture.md](architecture.md)   | Visão de sistema, componentes e o ciclo de vida de um turno de chat |
| [configuration.md](configuration.md) | Referência de todas as variáveis de ambiente (`.env`) |
| [deployment.md](deployment.md)       | LAN/VPS, domínio + HTTPS, backup/restore, atualização, rotação de `APP_SECRET` |
| [public-api.md](public-api.md)       | API compatível com OpenAI (`/v1`) e gestão de chaves |
| [security.md](security.md)           | Modelo de segurança, superfície de ataque e recomendações |
| [desktop.md](desktop.md)             | App desktop (Tauri): bandeja, autostart, build |
| [development.md](development.md)     | Rodar sem Docker, testes, layout do monorepo |

## Convenções

- **Local-first:** salvo indicação em contrário, tudo roda na sua máquina/servidor via Docker
  Compose. A única saída externa obrigatória é o provedor de modelos (OpenRouter).
- **Segredos por usuário** (chaves de OpenRouter, Tavily/Brave, voz, tokens de bot/OAuth) são
  salvos **na interface**, cifrados em repouso — **não** vão no `.env`.
- **Segredos de infraestrutura** (`APP_SECRET`, senha do Postgres, chave do Evolution) vão no
  `.env`, que **nunca** deve ser versionado.
