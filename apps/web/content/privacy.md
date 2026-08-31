# Política de Privacidade — Singularity AI

**Última atualização:** 4 de agosto de 2026

## Em uma frase

O Singularity AI é um aplicativo **auto-hospedado**. Ele roda no computador ou no
servidor de quem o instalou, e seus dados ficam nessa máquina. Nós não operamos um
serviço central, não recebemos cópia dos seus dados e não temos acesso a eles.

## Quem é responsável pelos seus dados

Cada instalação é independente. **Quem responde pelos seus dados é quem opera a
instalação que você usa** — você mesmo, se instalou no seu computador; ou a pessoa,
equipe ou empresa que mantém o servidor onde você entrou.

Os autores do software não são operadores de nenhuma instalação de terceiros.

## Que dados o aplicativo guarda

Tudo abaixo fica no banco de dados da própria instalação:

| Dado | Para quê |
|---|---|
| E-mail e senha (com hash) | entrar na sua conta |
| Conversas, mensagens e arquivos enviados | o histórico que você vê no aplicativo |
| Memórias e notas | personalização que você mesmo pediu |
| Credenciais das integrações (tokens OAuth, chaves de API) | acessar os serviços que você conectou |
| Registros de uso e de auditoria | mostrar consumo e permitir investigar problemas de segurança |

As credenciais das integrações são **cifradas em repouso**, com uma chave derivada
do segredo da instalação. Quem tiver acesso ao banco sem esse segredo não consegue
lê-las.

## Para onde os dados saem

O aplicativo só envia dados para fora quando **você configura** um serviço externo:

- **Provedor de modelo de IA** (OpenRouter, OpenAI, ou um modelo local): recebe o
  conteúdo da conversa que você envia, para gerar a resposta. Se você usar um modelo
  local (Ollama), nada sai da máquina.
- **Provedor de busca na web**, quando você usa a pesquisa.
- **Serviços que você conectar** (Google, GitHub, Notion, Slack, Telegram, Discord,
  WhatsApp, e outros): recebem apenas as requisições necessárias para a ação que
  você pediu.

Não há telemetria, analytics de terceiros, rastreadores ou publicidade. Não vendemos
dados — não temos dados para vender.

## Dados de usuários do Google

Quando você conecta uma conta Google, o aplicativo pede acesso a Gmail e Google
Agenda para executar exatamente o que você pedir no chat (ler, redigir e enviar
e-mails; consultar e criar eventos).

O uso e a transferência de informações recebidas das APIs do Google seguem a
[Política de Dados de Usuário dos Serviços de API do Google](https://developers.google.com/terms/api-services-user-data-policy),
incluindo os requisitos de **Uso Limitado** (Limited Use). Especificamente:

- Os dados do Google são usados **somente** para prover as funções que você
  solicitou dentro do aplicativo.
- **Não** são usados para publicidade.
- **Não** são vendidos nem cedidos a terceiros.
- **Não** são usados para treinar modelos de inteligência artificial generalizados.
  O conteúdo é enviado ao provedor de modelo apenas para produzir a resposta daquela
  interação, quando você mesmo pede uma ação que envolve esses dados.
- Nenhum ser humano lê esses dados, exceto se você autorizar expressamente para
  suporte, se for necessário por segurança ou para cumprir a lei.

Você revoga esse acesso quando quiser, em **Configurações → Integrações → Google
Workspace → remover conta**, ou pela página de segurança da sua Conta Google. Ao
remover a conta no aplicativo, o token guardado é apagado do banco.

## Retenção e exclusão

Você controla o que fica guardado:

- Apagar uma conversa remove suas mensagens.
- Desconectar uma integração apaga a credencial correspondente.
- Excluir sua conta remove seus dados da instalação.

Backups feitos pelo operador da instalação podem conter cópias até serem
sobrescritos — isso é responsabilidade de quem administra o servidor.

## Segurança

Senhas com hash, credenciais cifradas em repouso, autenticação em duas etapas
opcional (TOTP), registro de auditoria e sessões revogáveis. Nenhuma medida é
perfeita: mantenha sua instalação atualizada e não exponha o servidor à internet sem
HTTPS.

## Menores de idade

O aplicativo não é destinado a menores de 13 anos.

## Mudanças nesta política

Alterações são publicadas neste mesmo endereço, com a data acima atualizada.

## Contato

`[PREENCHER: e-mail de contato]`
