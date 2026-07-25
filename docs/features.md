# Recursos

Catálogo completo do AI Workspace, agrupado por área. A maioria dos recursos é configurável
**por modelo** e/ou **por usuário** na interface.

## Chat e modelos

- **Chat multi-modelo** via OpenRouter (qualquer modelo compatível com OpenAI) e **modelos
  locais via Ollama** (configuráveis em Conexões).
- **Streaming retomável**: a geração roda desacoplada da request — F5, trocar de chat ou fechar
  o navegador não cancela; o stream é retomável. Botão de **parar** salva o parcial.
- **Modelos personalizados**: crie "GPTs" próprios com system prompt, parâmetros, ferramentas,
  capacidades, filtros, voz, memória e sub-agentes — tudo por modelo. Slug editável.
- **Mesa-redonda**: vários modelos conversando entre si, com você guiando/pausando. Políticas
  round-robin, manual ou moderador (LLM); persona por participante.
- **Sub-agentes**: um orquestrador delega a modelos-operário (sequencial ou paralelo), com
  limites de profundidade/loop/chamadas e permissão por modelo. Menção `@` roteia o turno.
- **Chats de referência**: anexe outras conversas como contexto de um turno.
- **Compactação não-destrutiva**: mensagens saem do contexto mas continuam visíveis (com
  resumo); apagar o nó ativo "descompacta".
- **Chat temporário** (não salvo) e **compartilhamento** por link público (com senha/validade
  opcionais).

## Ferramentas (SIFT)

A IA descobre e executa ferramentas sob demanda. Categorias: **nativas**, do **Codespace** e de
**integrações**.

- **Pesquisa na web** (DuckDuckGo, SearXNG, Tavily ou Brave) e **pesquisa profunda**
  (multi-passo, com síntese).
- **Leitura de página** e **navegador headless**: a IA controla um Chromium real (navegar,
  clicar, digitar, screenshot) numa aba que persiste por conversa. Com guarda anti-SSRF.
- **Gráficos** (renderizados como imagem para os canais), **diagramas** (Mermaid/Excalidraw),
  **cotações financeiras**, **data/hora em tempo real**.
- **Transcrição de vídeo/áudio**: pega o conteúdo falado de um link (YouTube + ~1800 sites) via
  legendas ou STT, com cookies rotativos anti-bloqueio.
- **Geração de imagens**: nativa (modelo com modalidade de imagem) ou via **roteador** que
  delega a um modelo de imagem; também **vídeo** (Higgsfield).
- **Código Python**: a IA escreve e executa código num **sandbox isolado** (subprocesso com
  limites de CPU/memória/tempo).
- **Agência de mensagens**: a IA age nas suas conexões de WhatsApp/Telegram/Discord
  (listar/ler/enviar).

Ferramentas ficam em **Espaço de Trabalho → Ferramentas** e são acopláveis por modelo.

## Memória e conhecimento

- **Memória (mem0)** com escopos **global / por modelo / por chat** e **bancos de memória
  compartilháveis** entre modelos. Leitura por união dos escopos; escrita por escopo; revisão
  opcional antes de salvar. Controlável em Espaço → Memória, por chat e por modelo.
- **Base de Conhecimento (RAG)**: suba documentos → indexados em pgvector (FastEmbed, 384 dim) →
  acoplados por modelo/chat. Modo **automático** (injeta trechos + cita) ou **ferramenta**
  (`search_knowledge`). Imagens indexadas por nome/tags e exibidas no chat.
- **Second brain**: cérebros de notas **interligadas** (`[[wikilinks]]`) com grafo estilo
  Obsidian; a IA pode ler/escrever e **propor skills** (com aprovação).
- **Aprendizado proativo (Curator)**: em background, a IA revisa conversas e sugere skills e
  memórias — sempre com sua aprovação. Opt-in.
- **Biblioteca de skills e prompts**: importe uma skill de um link (`SKILL.md` do GitHub) ou
  crie prompts reutilizáveis, sempre via card de aprovação.

## Automação e canais

- **Automações agendadas** e **monitores** (preço, página, busca, RSS) que disparam a IA e te
  avisam. Histórico de execuções. Ferramenta `Monitor` disponível no chat.
- **Notificações**: in-app, **Web Push** no navegador (VAPID) e entrega nos canais.
- **Canais de mensagem** — converse com seus modelos por fora do app; cada conversa vira um chat
  na barra lateral:
  - **WhatsApp**: não oficial via **Evolution API** (QR Code) ou **Cloud API oficial** da Meta.
  - **Telegram**: bot por long-polling.
  - **Discord**: canal via Gateway (WebSocket).
  - Filtros, memória e **janela de contexto** por conexão; agregação de mensagens fragmentadas
    (debounce); saída visual (gráfico vira PNG, markdown sanitizado).

## Plataforma

- **API pública** compatível com OpenAI (`/v1/chat/completions`, `/v1/models`) com streaming,
  modo síncrono e assíncrono. Ver [public-api.md](public-api.md).
- **Gestão de chaves de API**: crie várias, nomeie, revogue/regenere, expiração, permissões,
  **limites** (RPM/RPD/mensal/tokens/concorrência), **orçamento** com bloqueio, **política de
  modelos**, **modos de memória** por chave, allowlist de IP e webhooks.
- **Observabilidade**: cada requisição vira um _trace_ com spans (tempo de banco,
  leituras/escritas, chamadas de LLM, ferramentas). Painel admin com waterfall, percentis
  (p50/p95/p99) e séries por rota.
- **Analítica de uso**: tokens, custo e requisições por modelo e por origem; ledger que
  sobrevive à exclusão de chats.
- **Codespace**: clone um repositório (git/SSH/local), navegue no **grafo de código**
  (estilo Obsidian), edite arquivos, mantenha chats e um banco de memória **por projeto**, e
  arraste arquivos/trechos para o chat.
- **Playground**: **benchmarks** (com juiz + regra), **comparações** lado a lado e **debug de
  ferramentas**.

## Voz e mídia

- **Voz (TTS/STT)** por endpoint compatível com OpenAI, incluindo **Kokoro** local (opt-in) e
  **clonagem de voz**. Voz por modelo (com mistura). Entrada por voz nos canais (áudio → texto).
- **Artefatos** (estilo Claude): código, documentos, HTML, SVG, Mermaid, CSV numa **janela
  dedicada**, com preview/código, **histórico de versões** e edição.

## Integrações

- **Google Workspace**: Gmail + Agenda via OAuth (2 ferramentas, multi-conta, ativação por
  operação).
- **Tuya / Smart Life**: casa inteligente via HMAC (descoberta automática de dispositivos,
  gating por modelo).
- **GitHub**: leitura e escrita (com confirmação), via PAT ou OAuth.
- **Assinaturas**: use ChatGPT/Codex por login (OAuth), quando aplicável.

## Interface e experiência

- **PWA / mobile**: layout responsivo, instalável, com safe-areas e navegação drill-down.
- **App desktop (Windows)**: janela nativa, bandeja, "rodar em segundo plano" e "iniciar com o
  Windows". Ver [desktop.md](desktop.md).
- **Paleta de comandos** (Ctrl/⌘+K): lançador único de ações, configurações, modelos e chats,
  com busca difusa.
- **Atalhos de teclado** personalizáveis; **onboarding** de primeiro uso; painel de **Status**;
  **orçamento pessoal** (aviso/pausa) opt-in.
- **Composer**: colar/arrastar imagens, anexar documentos (`#`), skills (`$`), agentes (`@`).
- **Segurança do usuário**: 2FA (TOTP), logs de auditoria, pedir confirmação antes de ações
  sensíveis (opt-in).
