# API pública

O AI Workspace expõe uma API **compatível com OpenAI** em `/v1`. Um cliente ou SDK da OpenAI
funciona trocando apenas o `base_url` e a chave — sem adaptador.

## Autenticação

Toda chamada usa um **Bearer token** no formato `aw-<prefixo>-<segredo>`:

```
Authorization: Bearer aw-xxxxxxxxxxxx-xxxxxxxx...
```

Clientes que não deixam customizar o header aceitam também `X-API-Key`. O segredo tem 256 bits
de entropia e é mostrado **uma única vez** na criação; o banco guarda apenas o SHA-256.

Crie e gerencie chaves em **Espaço de Trabalho → API** (ou nas Configurações). Cada chave tem:

- **Nome** e estado (ativa / desativada / revogada / expirada), com **revogação instantânea** e
  **regeneração**.
- **Permissões (scopes):** `chat`, `models:read`, `memory:read`, `memory:write`, `files:read`,
  `files:write`, `usage:read`.
- **Limites:** requisições por minuto (RPM), por dia (RPD), mensais, tokens de entrada/saída,
  **concorrência** e **orçamento (US$)** com bloqueio automático.
- **Política de modelos:** liberar todos ou só uma lista (impede acesso a versões futuras).
- **Modo de memória** por chave (ver abaixo), **allowlist de IP** (CIDR) e **webhooks** de
  eventos.

## Endpoints

### Chat Completions

`POST /v1/chat/completions` — compatível com OpenAI. Suporta:

- **Streaming** (`"stream": true`), **síncrono** e **assíncrono** (`"background": true`).
- **Modo plataforma** (padrão): reutiliza o orquestrador do app — ferramentas do servidor,
  memória, conhecimento e o preset do modelo. Metadados extras vão num campo `aiworkspace`.
- **Modo passthrough**: se o cliente enviar `tools` (function calling clássico), a chamada é
  encaminhada ao provedor sem interferência.

```bash
curl http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer aw-..." \
  -H "Content-Type: application/json" \
  -d '{
    "model": "openai/gpt-4o-mini",
    "messages": [{"role": "user", "content": "Olá!"}],
    "stream": true
  }'
```

Com o SDK da OpenAI:

```python
from openai import OpenAI

client = OpenAI(base_url="http://localhost:8000/v1", api_key="aw-...")
resp = client.chat.completions.create(
    model="openai/gpt-4o-mini",
    messages=[{"role": "user", "content": "Olá!"}],
)
print(resp.choices[0].message.content)
```

### Modelos

- `GET /v1/models` — lista os modelos disponíveis para a chave.
- `GET /v1/models/{id}` — detalhes de um modelo.

### Memória, arquivos, uso e conta

- `GET/POST/DELETE /v1/memories` — CRUD + limpar/exportar/importar (respeitando o isolamento do
  modo de memória da chave). Requer scope `memory:*`.
- `GET/POST /v1/files` — documentos da Base de Conhecimento. Requer scope `files:*`.
- `GET /v1/usage` — consumo da chave (requisições, tokens, custo). Requer `usage:read`.
- `GET /v1/account` — informações da conta/limites.

## Modos de memória (por chave)

O comportamento de memória é definido **na chave**, o que é essencial para quem revende a API:

| Modo        | Comportamento |
|-------------|---------------|
| `none`      | Sem memória. |
| `request`   | Só o contexto da própria requisição. |
| `persistent`| Compartilha a memória do app (do usuário dono da chave). |
| `shared`    | Igual a `persistent`, explicitando que várias chaves veem o mesmo. |
| `key`       | Isolada por chave (`apikey:<id>`). |
| `end_user`  | Isolada por **usuário final** (`enduser:<key>:<id>`) — o modo certo para revenda. |

## Limites e erros

Erros seguem o formato da OpenAI (`{"error": {message, type, code}}`). Quando um limite é
atingido, a resposta traz o código apropriado e, quando aplicável, o header `Retry-After`:

- `rate_limit_exceeded` / `concurrency_limit` → HTTP 429
- `daily_quota_exceeded` / `monthly_quota_exceeded` / `*_token_quota_exceeded` → HTTP 429
- `budget_exceeded` → HTTP 402
- `invalid_api_key` / `key_revoked` / `key_expired` → HTTP 401
- `insufficient_scope` / `ip_not_allowed` → HTTP 403

## Webhooks

Uma chave pode declarar uma URL de webhook (assinada com HMAC-SHA256) para receber eventos como
`limit.reached` e `request.error`, úteis para observar consumo do lado do integrador.

## Dashboard

O painel em **Espaço de Trabalho → API** lista as chaves, permite criar/revogar, mostra
monitoramento em tempo real, uso e custo por modelo, erros recentes, e traz a documentação e um
playground integrados.
