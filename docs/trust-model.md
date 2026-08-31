# Modelo de confiança do Singularity AI

Este documento fixa **uma decisão de produto**, não uma aspiração. Ela determina o que
precisa (e o que **não** precisa) ser isolado, e existe para impedir a deriva silenciosa
para um produto diferente.

## O invariante

> **Uma instância = um domínio de confiança.**
> Os usuários de uma instância são o dono e as pessoas que ele convidou (equipe, casa,
> amigos). Eles **não** são adversários entre si.

Corolário: o Singularity AI é **multiusuário**, e **não** é **multi-inquilino**.

| | Multiusuário (o que somos) | Multi-inquilino (o que NÃO somos) |
|---|---|---|
| Usuário A é ameaça ao B? | não — separar é privacidade/organização | sim — é fronteira de segurança |
| Sandbox de execução por usuário | não exigido | exigido |
| Cotas/abuso/isolamento por conta | fora de escopo | central |

## Por que isso importa

Sem o invariante escrito, toda decisão de segurança vira discussão do zero e a tendência é
sempre "adicionar mais isolamento". Com ele, o critério é objetivo: uma proteção só se
justifica se defender contra **as ameaças reais abaixo** — não contra "e se um usuário for
malicioso".

## As duas formas de uso

1. **Desktop** (`.exe`/releases, engine embarcada): o app é o programa do usuário, na
   máquina dele. Acesso à máquina é *a funcionalidade* — é o que o torna comparável a
   Claude Code/Codex. Raio de dano = a própria máquina do dono.
2. **Servidor** (VPS/homelab, Docker): a mesma app publicada para acesso remoto e para os
   convidados. Roda 24/7 e **guarda credencial de todo mundo** (OAuth do Google, PAT do
   GitHub, tokens de Slack/Notion/Tuya, sessões de WhatsApp).

## As ameaças que realmente contam

O ator não-confiável **não é o usuário — é a própria IA**, porque ela consome conteúdo de
terceiros (página web, e-mail, issue, mensagem de canal, PDF, transcrição).

1. **Injeção de prompt → execução.** Conteúdo hostil convence o modelo a rodar um comando.
   Vale mesmo com **um único usuário**: o alvo é o segredo da instância, não o vizinho.
   - Mitigação: segredos **fora do ambiente do processo** (ver `APP_SECRET_FILE` em
     `config.py` e `docs/`), confirmação em ações de risco, e — no perfil servidor — o
     backend `runner` para execução.
2. **Exposição de rede não intencional.** Preview/porta alcançável por quem não deveria.
   - Mitigação: `PREVIEW_BIND` (padrão `127.0.0.1`) + proxy autenticado
     (`/codespace/preview/<porta>/`, com `port_owned_by`).
3. **Vazamento acidental entre usuários.** Um recurso resolvido por id sem checar dono.
   - Isto **não** é "defesa contra usuário malicioso": é higiene. É a classe de bug mais
     fácil de introduzir sem perceber, então é coberta por teste automatizado —
     ver `tests/test_authorization_scoping.py`.

## Consequências práticas (o que fica decidido)

- **Cadastro é fechado por padrão.** `signups_allowed()` só libera quando não há nenhum
  usuário (bootstrap do primeiro admin); depois disso exige o admin habilitar
  explicitamente. Não existe (nem deve existir) variável de ambiente que abra isso.
- **Não construir** isolamento de execução por usuário, cota por conta, nem defesa contra
  abuso de inquilino. Se um dia isso for necessário, é porque o invariante mudou — e a
  mudança tem que ser deliberada, começando por este arquivo.
- **Construir e manter**: contenção do que a IA executa, exposição de rede fechada por
  padrão, e o teste de escopo de autorização.
