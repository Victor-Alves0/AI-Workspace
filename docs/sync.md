# Sincronização entre instâncias

Mantém os mesmos dados em mais de uma instalação do AI Workspace — o caso típico é o
servidor de casa (ou uma VPS) e o app desktop. Funciona nas duas direções: o que você
faz em qualquer lado aparece no outro, inclusive o que foi feito com o desktop offline
(vai na próxima troca).

## Como parear

1. Na instância que vai **receber** a conexão (em geral o servidor): **Admin →
   Sincronização → Gerar código**. O código vale 10 minutos e uma única vez.
2. Na outra (em geral o desktop): **Admin → Sincronização → Adicionar instância**, com o
   endereço da primeira (o que aparece embaixo do nome dela, na mesma tela) e o código.

Quem adiciona é quem inicia as trocas — a cada minuto, e na hora pelo botão
**Sincronizar agora**. Por isso quem adiciona deve ser a instância que *alcança* a outra:
o desktop alcança o servidor; o servidor normalmente não alcança o desktop (NAT).

As duas precisam de uma conta com o **mesmo e-mail**. Só essas contas sincronizam — as
demais contas do servidor não descem para o desktop.

## O que sincroniza

Conversas e mensagens, anexos (com os arquivos), artefatos, imagens geradas, modelos,
prompts, skills, ferramentas, bases de conhecimento, cérebros, memórias, integrações
(Google, GitHub, Notion, Slack), chaves de API, máquinas do Remote Terminal, campanhas do
Imaginai, grafos de investigação, benchmarks e o histórico de uso.

**Ficam só em cada instância:**

| O quê | Por quê |
|---|---|
| Automações | Rodariam nas duas (um e-mail seria enviado duas vezes) |
| Bots de canal (WhatsApp, Telegram, Discord, Slack) | Responderiam duas vezes |
| Projetos do Codespace | Os arquivos vivem em disco, fora do banco |
| Configurações da instância, logs, observabilidade, chaves da API pública, notificações push | São da máquina |

## Conflitos

Por registro: se a mesma coisa foi editada nos dois lados, vale a edição mais recente.
Apagar também propaga. No primeiro pareamento, o que as duas já tinham com o mesmo nome
(a chave "openrouter", o modelo de slug "gpt", uma skill) vira um registro só — o mais
recente — em vez de duplicar.

## Segurança

- Cada instância continua com a própria chave de cifra (derivada do `APP_SECRET` dela).
  Os segredos saem decifrados só dentro do envelope da troca e chegam recifrados com a
  chave de quem recebe.
- Cada troca vai cifrada e autenticada com um segredo do par (criado no pareamento), com
  validade curta contra repetição. Mesmo em HTTP numa rede local o conteúdo não trafega
  em claro — mas use HTTPS fora de casa (ver [https.md](https.md)).
- O pareamento só acontece com o código de uso único, com limite de tentativas.
- **Desconectar** (lixeira no card) para a sincronização; o que já veio fica.

## Detalhes

- Versões diferentes das duas instâncias sincronizam o que ambas conhecem; o painel avisa
  para atualizar.
- A captura de mudanças (gatilhos no Postgres) só é instalada quando a primeira instância
  é pareada — quem não usa não paga nada.
- Código: `apps/server/aiworkspace/sync/` (`tables.py` decide o que viaja, derivado dos
  modelos; `engine.py` coleta/aplica; `service.py` pareamento e trocas).
