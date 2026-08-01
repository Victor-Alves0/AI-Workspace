"""Camada de ferramentas (SIFT) — instâncias por-usuário.

Cada usuário tem uma instância SIFT própria contendo:
  - ferramentas embutidas (tempo, cálculo, web.search com a config dele)
  - as ferramentas que ELE criou (código Python registrado dinamicamente)

As instâncias são cacheadas e reconstruídas automaticamente quando as tools do
usuário mudam (assinatura) ou quando invalidadas explicitamente (ex.: troca de
segredos de busca). Tudo degrada com segurança: se a SIFT falhar, o chat segue
sem ferramentas em vez de derrubar o servidor.
"""

from __future__ import annotations

import ast
import asyncio
import datetime as _dt
import json
import logging
import math
import operator
import os
import re
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Iterable

import httpx

from sift import Sift
from sift.sandbox import SubprocessSandbox

from .. import deep_search, finance
from ..config import get_settings
from ..search import SearchConfig, web_search
from . import toolctx
from .sandbox import extract_valves, run_in_subprocess

logger = logging.getLogger(__name__)

# avaliador aritmético seguro (sem eval): só números e operadores
_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}

# funções e constantes matemáticas liberadas na Calculadora (avaliador AST seguro,
# sem eval/import/atributos). Poderosa mas de baixíssimo custo de tokens.
_MATH_FUNCS = {
    "sqrt": math.sqrt, "cbrt": lambda x: math.copysign(abs(x) ** (1 / 3), x),
    "abs": abs, "round": round, "floor": math.floor, "ceil": math.ceil,
    "trunc": math.trunc, "sign": lambda x: (x > 0) - (x < 0),
    "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "asin": math.asin, "acos": math.acos, "atan": math.atan, "atan2": math.atan2,
    "sinh": math.sinh, "cosh": math.cosh, "tanh": math.tanh,
    "log": math.log, "log2": math.log2, "log10": math.log10, "exp": math.exp,
    "pow": math.pow, "hypot": math.hypot, "gcd": math.gcd,
    "factorial": math.factorial, "degrees": math.degrees, "radians": math.radians,
    "min": min, "max": max,
}
_MATH_CONSTS = {"pi": math.pi, "e": math.e, "tau": math.tau}


def _safe_eval(expr: str) -> float:
    def _eval(node: ast.AST) -> float:
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.Name) and node.id in _MATH_CONSTS:
            return _MATH_CONSTS[node.id]
        if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
            if isinstance(node.op, ast.Pow):  # limita expoentes p/ evitar DoS
                exp = _eval(node.right)
                if abs(exp) > 1000:
                    raise ValueError("expoente muito grande")
            return _BIN_OPS[type(node.op)](_eval(node.left), _eval(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
            return _UNARY_OPS[type(node.op)](_eval(node.operand))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            fn = _MATH_FUNCS.get(node.func.id)
            if fn is None or node.keywords:
                raise ValueError(f"função não permitida: {getattr(node.func, 'id', '?')}")
            if node.func.id == "factorial":  # evita explosão de custo
                arg = _eval(node.args[0])
                if arg > 1000:
                    raise ValueError("fatorial muito grande")
            return fn(*[_eval(a) for a in node.args])
        raise ValueError("expressão não permitida")

    return _eval(ast.parse(expr, mode="eval").body)

# cache: user_id (str) -> (assinatura, Sift|None)
_cache: dict[str, tuple[tuple, Sift | None]] = {}


@dataclass
class GoogleConfig:
    """Config por-modelo das tools Google (Gmail + Agenda).

    O TOKEN não mora aqui (é buscado ao vivo por conta em cada chamada); só as
    contas liberadas, as operações ativas e as preferências — que definem a
    assinatura do cache SIFT. `accounts` = [{"id","email"}] (vazio = sem conta →
    responde 'não conectado'). `ops` = capacidade→ligado (ausente = ligado):
    gmail_search / gmail_send / gmail_organize / cal_view / cal_create / cal_delete."""
    user_id: str = ""
    require_confirm: bool = True     # pedir confirmação (opções) antes de escrever
    max_results: int = 10
    default_calendar: str = "primary"
    accounts: list = field(default_factory=list)
    ops: dict = field(default_factory=dict)


@dataclass
class TuyaConfig:
    """Config das tools Tuya/Smart Life. Reúne a conexão GLOBAL (creds + catálogo de
    dispositivos, de app_settings) com o gating POR-MODELO. `conn` = dict passado ao
    tuya_service (base_url, access_id, access_secret, devices, aliases, profiles,
    scenes). `allowed_devices` = nomes liberados neste modelo (vazio = todos). `ops` =
    capacidade→ligado (ausente = ligado): tuya_query / tuya_switch / tuya_ac / tuya_scene."""
    conn: dict = field(default_factory=dict)
    allowed_devices: list = field(default_factory=list)
    require_confirm: bool = True
    ops: dict = field(default_factory=dict)


@dataclass
class GithubConfig:
    """Config por-modelo da tool GitHub. Como a do Google, o TOKEN não mora aqui (é
    buscado ao vivo por conta em cada chamada); só as contas liberadas, as operações
    ativas e a confirmação. `accounts` = [{"id","login"}] (vazio = sem conta →
    responde 'não conectado'). `ops` = capacidade→ligado (ausente = ligado):
    gh_read / gh_issue / gh_comment / gh_pr / gh_commit."""
    user_id: str = ""
    require_confirm: bool = True
    accounts: list = field(default_factory=list)
    ops: dict = field(default_factory=dict)


@dataclass
class HiggsfieldConfig:
    """Config da tool Higgsfield (geração de imagem/vídeo). `conn` = {api_key,
    api_secret} decifrado do app_settings — diferente do Google/GitHub, a chave
    VIAJA na config: a tool fala com a API direto (sem refresh de token), e a
    conexão é por-usuário como a Tuya."""
    conn: dict = field(default_factory=dict)


@dataclass
class NotionConfig:
    """Config por-modelo da tool Notion. Como a do GitHub, o TOKEN não mora aqui (é
    buscado ao vivo por conta em cada chamada); só as contas liberadas, as operações
    ativas e a confirmação. `accounts` = [{"id","workspace"}] (vazio = sem conta →
    responde 'não conectado'). `ops` = capacidade→ligado (ausente = ligado):
    nt_read / nt_create / nt_append."""
    user_id: str = ""
    require_confirm: bool = True
    accounts: list = field(default_factory=list)
    ops: dict = field(default_factory=dict)


@dataclass
class SlackConfig:
    """Config por-modelo da tool Slack. Como a do GitHub, o TOKEN não mora aqui (é
    buscado ao vivo por conta em cada chamada); só os workspaces liberados, as operações
    ativas e a confirmação. `accounts` = [{"id","team"}] (vazio = sem conta →
    responde 'não conectado'). `ops` = capacidade→ligado (ausente = ligado):
    sl_read / sl_send."""
    user_id: str = ""
    require_confirm: bool = True
    accounts: list = field(default_factory=list)
    ops: dict = field(default_factory=dict)


@dataclass
class ElevenLabsConfig:
    """Config da tool ElevenLabs (áudio: TTS + efeitos sonoros). `conn` = {api_key,
    model, default_voice} decifrado do app_settings — como a Higgsfield, a chave VIAJA
    na config: a tool fala com a API direto. Conexão por-usuário."""
    conn: dict = field(default_factory=dict)


@dataclass
class VercelConfig:
    """Config da tool Vercel (consulta de projetos/deployments). `conn` = {token}
    decifrado do UserSecret — como a Higgsfield/ElevenLabs, o token VIAJA na config: a
    tool fala com a API da Vercel direto. Conexão por-usuário, somente leitura."""
    conn: dict = field(default_factory=dict)


@dataclass
class SpotifyConfig:
    """Config da tool Spotify (busca no catálogo). `conn` = {id, secret} (Client
    Credentials) decifrado do UserSecret — as credenciais VIAJAM na config: a tool
    pega o token de app e fala com a API do Spotify direto. Conexão por-usuário,
    somente catálogo (playback exige OAuth de usuário, fora de escopo)."""
    conn: dict = field(default_factory=dict)


@dataclass
class MessagingConfig:
    """Config por-modelo da tool de mensagens (agir nas conexões de chat do usuário:
    WhatsApp/Telegram/Discord). Nada de token/instância aqui — resolvidos ao vivo por
    conexão em cada chamada. `accounts` = [{"id","platform","label"}] das conexões
    liberadas p/ este modelo (vazio = nenhuma → 'nenhuma conexão'). `ops` =
    capacidade→ligado (ausente = ligado): msg_list / msg_read / msg_send."""
    user_id: str = ""
    require_confirm: bool = True
    accounts: list = field(default_factory=list)
    ops: dict = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Ferramentas embutidas (do sistema)
# --------------------------------------------------------------------------- #
# Registro exposto p/ a UI (caixas de "Ferramentas de Sistema" no editor de modelo).
# SIFT exige paths no formato 'categoria.servico.funcao' (3 segmentos).
#   name/description => rótulo e texto da UI (idioma do app, hoje PT-BR).
#   model_desc       => linha em INGLÊS que o MODELO recebe no catálogo (modo
#                       "list"); no modo padrão "prompt" ele descobre via SIFT,
#                       que já expõe as descrições em inglês do @sift.tool.
#   O que a IA vê "por baixo dos panos" é sempre inglês (melhor desempenho); a UI
#   fica no idioma do app. Um toggle de idioma real virá depois.
BUILTIN_TOOLS: list[dict[str, str]] = [
    {"path": "utils.time.now", "name": "Data e Hora", "description": "Data/hora atuais em qualquer fuso horário.",
     "model_desc": "Current date/time in a given time zone."},
    {"path": "utils.math.eval", "name": "Calculadora", "description": "Avalia expressões com funções (sqrt, sin, log…) e constantes (pi, e).",
     "model_desc": "Evaluate a math expression (functions, constants)."},
    {"path": "user.profile.get", "name": "Perfil do Usuário", "description": "Consulta os dados da conta do usuário: nome, sobre, gênero, data de nascimento (e idade), e-mail e idioma.",
     "model_desc": "Get the user's account info: name, about, gender, birth date/age, email, language."},
    {"path": "web.search.query", "name": "Pesquisa na Web", "description": "Busca informações atuais na web (título, url, trecho).",
     "model_desc": "Search the web for current or factual info."},
    {"path": "web.page.read", "name": "Ler Página", "description": "Abre uma URL e devolve o texto legível da página (lê o conteúdo do site, não só o trecho da busca).",
     "model_desc": "Fetch a URL and return the page's readable text."},
    # Navegador headless (Chromium) — requer o serviço opt-in `browser` (browserless).
    # A IA controla uma aba VIVA por conversa: navega com JS, lê, clica, digita, rola e
    # tira screenshot. Para sites com JS/SPA, login e formulários que o "Ler Página" não dá.
    {"path": "web.browser.use", "name": "Navegador (Browser)", "description": "Controla um Chromium headless: navega (com JS), lê, clica, digita, rola e tira screenshot — mantém a página aberta entre as ações.",
     "model_desc": "Drive a headless Chromium (renders JS; live tab across calls): goto, read, click, type, scroll, back, screenshot."},
    # Transcrição de vídeo/áudio (YouTube e ~1800 sites via yt-dlp). Usa as legendas
    # quando existem (rápido); senão baixa o áudio e transcreve a fala pelo STT do
    # usuário (conexão de Voz Local → provedor global). Dá à IA o CONTEÚDO falado de
    # um link de vídeo p/ resumir/responder. ffmpeg no servidor cobre o caminho de fala.
    {"path": "media.video.transcribe", "name": "Transcrever Vídeo/Áudio", "description": "Extrai o conteúdo falado de um link de vídeo/áudio (YouTube e ~1800 outros sites) como texto, para resumir ou responder perguntas — usa as legendas quando há, senão transcreve a fala.",
     "model_desc": "Transcribe the spoken content of a video/audio URL (YouTube + ~1800 sites) to text — captions when available, else speech-to-text. Use when the user shares a video/audio link and wants its content, a summary, or Q&A."},
    # Biblioteca do usuário (autoensinamento): a IA lê o que já existe e PROPÕE novas
    # skills/prompts — inclusive importando de um link (SKILL.md do GitHub etc.).
    # Toda escrita passa pelo CARD de aprovação; a tool nunca grava no banco.
    {"path": "skills.library.manage", "name": "Gerenciar Skills", "description": "Lista e lê as skills do usuário e propõe novas — inclusive importando de um link (ex.: um SKILL.md no GitHub). A skill só é salva depois que você aprova no card.",
     "model_desc": "The user's skill library: list/read existing skills and propose new ones, including importing from a URL (e.g. a SKILL.md on GitHub). Proposals are approved by the user in a card — nothing is saved automatically."},
    {"path": "prompts.library.manage", "name": "Gerenciar Prompts", "description": "Lista e lê os prompts reutilizáveis (/comandos) do usuário e propõe novos — inclusive a partir de um link. O prompt só é salvo depois que você aprova no card.",
     "model_desc": "The user's reusable prompt library (/commands): list/read existing prompts and propose new ones, including from a URL. Proposals are approved by the user in a card — nothing is saved automatically."},
    # Ledger de tarefa: memória de trabalho estruturada e persistente do objetivo atual
    # (um por chat), injetada no contexto a cada turno. Faz o agente CONVERGIR em tarefas
    # longas (dev/refactor/security) sem re-derivar nem re-reportar achado refutado.
    {"path": "task.tracker", "name": "Ledger de Tarefa", "description": "Sua memória de trabalho do objetivo atual (plano, achados, notas, próximo passo) — mantém o foco em tarefas de vários passos e persiste entre turnos.",
     "model_desc": "Your persistent WORKING MEMORY for the current multi-step objective (one ledger per chat, injected into every turn). Record the goal, a plan with step statuses, findings with a lifecycle (open/confirmed/refuted), evidence notes, and the next step — update it AS YOU WORK so you converge instead of re-deriving. A 'refuted' finding must not be reported again as valid. Use on any long task: a refactor, a feature build, an investigation."},
    {"path": "diagram.excalidraw.render", "name": "Excalidraw (Diagrama)", "description": "Desenha um diagrama/fluxograma editável (canvas) a partir de Mermaid.",
     "model_desc": "Draw an editable diagram from a Mermaid flowchart."},
    {"path": "chart.render.plot", "name": "Gráfico", "description": "Desenha um gráfico (linha, barra, área ou pizza) a partir de dados.",
     "model_desc": "Render a chart (line, bar, area, pie) from data."},
    {"path": "finance.quote.get", "name": "Cotação (Ações)", "description": "Busca cotação real de ações/índices e mostra um card com mini-gráfico.",
     "model_desc": "Get a real stock/index/crypto quote as a card."},
    {"path": "research.deep.run", "name": "Deep Search", "description": "Pesquisa profunda e iterativa (planeja, busca, lê, resume) com fontes. Só quando pedida.",
     "model_desc": "Deep, multi-step web research with sources. Only when explicitly asked."},
    {"path": "automation.monitor.create", "name": "Monitor (Automação)", "description": "Cria um monitor que avisa o usuário quando algo mudar (preço, página, busca, RSS).",
     "model_desc": "Create a background monitor that alerts the user when something changes."},
    {"path": "automation.reminder.create", "name": "Lembrete (Automação)", "description": "Agenda um lembrete pontual: no horário, entrega a mensagem no chat + notificação.",
     "model_desc": "Schedule a one-shot reminder delivered in-app at a future time."},
    # Google Workspace (requer conta conectada em Integrações). Cada ferramenta
    # reúne suas operações num parâmetro `action`; a ativação de cada operação é
    # configurável na engrenagem da ferramenta.
    {"path": "google.gmail.mailbox", "name": "Google Gmail", "description": "Buscar, ler, enviar e organizar e-mails da conta Google conectada.",
     "model_desc": "Read, search, send and organize the user's Gmail."},
    {"path": "google.calendar.events", "name": "Google Calendar", "description": "Ver, buscar, criar, editar e excluir eventos da Google Agenda.",
     "model_desc": "View, search, create, edit and delete Google Calendar events."},
    # Tuya / Smart Life (requer conexão em Integrações). Controla luzes, tomadas,
    # ar-condicionado e cenas; os dispositivos e ações liberados são escolhidos na
    # engrenagem da ferramenta (por modelo).
    {"path": "smartlife.tuya.devices", "name": "Tuya Smart Home", "description": "Controlar dispositivos Smart Life/Tuya: luzes, tomadas, ar-condicionado e cenas.",
     "model_desc": "Control the user's smart home (Tuya/Smart Life): lights, plugs, AC and scenes."},
    # GitHub (requer conta conectada em Integrações via PAT ou OAuth). Uma ferramenta
    # com `action`: lê repos/arquivos/issues/PRs e (com confirmação) cria issues,
    # comentários, PRs e commits. As operações liberadas são escolhidas na engrenagem.
    {"path": "github.repo.manage", "name": "GitHub", "description": "Repositórios GitHub do usuário: listar repos, ler arquivos, buscar código, ver/criar issues, PRs e comentários.",
     "model_desc": "The user's GitHub: list repos, read files, search code, view/create issues, PRs and comments."},
    # Notion (requer conta conectada em Configurações → Integrações via token de
    # integração interna ou OAuth). Uma ferramenta com `action`: busca páginas/bases,
    # lê o conteúdo e (com confirmação) cria páginas e adiciona conteúdo.
    {"path": "notion.workspace.manage", "name": "Notion", "description": "Workspace Notion do usuário: buscar páginas e bases de dados, ler conteúdo, criar páginas e adicionar conteúdo.",
     "model_desc": "The user's Notion workspace: search pages and databases, read page content, query databases, create pages and append content."},
    # Slack (requer workspace conectado em Configurações → Integrações via token ou
    # OAuth). Uma ferramenta com `action`: lista canais, lê/busca mensagens e (com
    # confirmação) envia mensagens. As operações liberadas são escolhidas na engrenagem.
    {"path": "slack.workspace.manage", "name": "Slack", "description": "Workspace Slack do usuário: listar canais, ler e buscar mensagens e enviar mensagens.",
     "model_desc": "The user's Slack workspace: list channels, read and search messages, and send messages."},
    # Mensagens (requer uma conexão de chat em Integrações: WhatsApp/Telegram/Discord).
    # Uma ferramenta com `action`: lista conversas, lê o histórico e (com confirmação)
    # envia mensagens pelas conexões do usuário. As conexões e ações liberadas são
    # escolhidas na engrenagem (por modelo).
    {"path": "messaging.chat.manage", "name": "Mensagens (WhatsApp/Telegram/Discord)", "description": "Agir nas conexões de chat do usuário: listar conversas, ler mensagens e enviar mensagens por WhatsApp, Telegram ou Discord.",
     "model_desc": "Act on the user's chat connections: list conversations, read messages, and send messages via WhatsApp, Telegram or Discord."},
    # Higgsfield (requer conexão em Configurações → Conexões). Geração de imagem
    # (Soul, Seedream, FLUX) e vídeo (DoP, Kling, Seedance) pela API oficial;
    # resultado baixado e guardado no banco (as URLs deles expiram) e mostrado
    # no chat pelos cards de imagem/vídeo.
    {"path": "higgsfield.media.generate", "name": "Higgsfield (Imagem/Vídeo)", "description": "Gera imagens (Soul, Seedream, FLUX) e vídeos a partir de imagem (DoP, Kling, Seedance) com os modelos da Higgsfield.",
     "model_desc": "Generate images (Soul, Seedream, FLUX) and image-to-video (DoP, Kling, Seedance) via Higgsfield."},
    # ElevenLabs (requer conexão em Configurações → Conexões). Gera fala premium de
    # qualquer texto e efeitos sonoros; o áudio é guardado e tocado no chat.
    {"path": "elevenlabs.audio.generate", "name": "ElevenLabs (Áudio)", "description": "Gera fala premium (TTS) de qualquer texto e efeitos sonoros com a ElevenLabs; o áudio aparece no chat.",
     "model_desc": "Generate premium speech (TTS) from any text and sound effects via ElevenLabs; the audio is shown in the chat."},
    # Vercel (requer conexão em Configurações → Integrações via Personal Access Token).
    # Somente leitura: lista os projetos e os deployments recentes (estado + url) para
    # o usuário acompanhar o que está no ar e o status dos deploys.
    {"path": "vercel.projects.manage", "name": "Vercel", "description": "Consulta a conta Vercel do usuário: listar projetos e ver os deployments recentes (estado, url) de um projeto.",
     "model_desc": "Query the user's Vercel account: list projects and inspect recent deployments (state, url) — read-only, useful to check what is live and deploy status."},
    # Spotify (requer conexão em Configurações → Integrações via Client ID + Secret,
    # fluxo Client Credentials). Busca no catálogo público: faixas, artistas, álbuns e
    # playlists (nome, artista, link). Sem controle de playback (exige login do usuário).
    {"path": "spotify.music.search", "name": "Spotify", "description": "Busca no catálogo do Spotify: encontrar faixas, artistas, álbuns ou playlists (nome, artista e link).",
     "model_desc": "Search the Spotify catalog: find tracks, artists, albums or playlists (name, artist, link). Read-only — no playback control."},
    # Codespace (grafo de código — GraphCodeMap): só funciona em chats vinculados a
    # um projeto (Espaço de Trabalho → Codespace). Query estrutural (símbolos, quem
    # chama quem, blast radius) em vez de grep — menos rodadas de leitura, mais
    # precisão. Confiança (certain/inferred/possible) e avisos de completude NUNCA
    # são escondidos do modelo.
    {"path": "code.graph.query", "name": "Grafo de Código", "description": "Consulta o grafo de código do projeto vinculado: encontra símbolos, quem chama quem, o impacto de mudar algo, a vizinhança de uma função/classe, quais arquivos abrir para uma tarefa, quais testes cobrem um símbolo e o raio de impacto de um diff.",
     "model_desc": "Query the project's code graph: find symbols, who calls what, blast radius of a change or of a whole diff, a symbol's neighborhood, which files to read for a task, and which tests cover a symbol. Use INSTEAD of grepping when a project is attached."},
    {"path": "code.files.browse", "name": "Arquivos do Projeto", "description": "Lista, lê e busca texto nos arquivos do projeto vinculado, além do histórico de commits (respeitando o escopo liberado/bloqueado).",
     "model_desc": "List, read and text-search the attached project's files, plus its git history/diffs (respects the project's allow/deny scope)."},
    # Escrita: SEPARADA da leitura (code.files.browse) por princípio de menor
    # privilégio — um modelo pode ganhar só leitura sem nunca poder escrever. Toda
    # escrita vira um commit LOCAL automático (reversível); push/delete passam
    # pelo mesmo toggle global "confirmar ações" que já protege GitHub/Google/Tuya.
    {"path": "code.files.write", "name": "Editar Projeto", "description": "Cria, edita e apaga arquivos do projeto vinculado — cada mudança vira um commit local automático — e envia (push) para o repositório remoto.",
     "model_desc": "Write/edit/delete files in the attached project (every change auto-commits locally) and push commits to the remote. Requires code.files.browse to read first."},
    # Fluxo de dados/taint: análise de SEGURANÇA, separada das consultas normais
    # do grafo — é uma capacidade com identidade própria (e um modelo pode ter o
    # grafo sem ter isto). Estática e "may-taint": over-aproxima de propósito.
    {"path": "code.flow.analyze", "name": "Fluxo e Segurança do Código", "description": "Analisa para onde os dados fluem no projeto vinculado: o que um parâmetro alcança, se algo chega a um destino sensível (rede, banco, disco) e entradas não confiáveis que chegam a pontos perigosos.",
     "model_desc": "Data-flow and taint analysis on the attached project: where a function's parameters flow, whether a symbol reaches a sink (http/db/fs), and untrusted input reaching dangerous sinks. Static may-analysis — findings are candidates to verify, not proof."},
    # Execução: roda comandos do projeto (testes/build/lint) num sandbox. Gate DUPLO
    # (tool por-modelo + exec_enabled por-projeto) + confirmação — é a capacidade mais
    # sensível do Codespace (roda código de verdade). É o que fecha o loop "escreve →
    # testa → corrige".
    {"path": "code.exec.run", "name": "Executar no Projeto", "description": "Roda comandos do projeto vinculado (testes, build, lint, scripts) num sandbox e devolve a saída — para verificar as mudanças. Comandos longos (baixar deps/instalar/buildar) rodam em background. Precisa ser liberado por-projeto em Configurações do projeto.",
     "model_desc": "Run shell commands in the attached project's sandbox (tests, build, lint, scripts) and get stdout+exit code — use to VERIFY your changes after editing. Long commands (downloads/installs/builds) run in the background and return a job_id. Prefer the project's test_command. Requires the project to have execution enabled."},
    # Jobs de background: espera/consulta/lista dos comandos longos disparados pelo exec.
    {"path": "code.exec.jobs", "name": "Comandos em Background", "description": "Espera, consulta o status ou lista os comandos longos (downloads/instalações/builds) que estão rodando em segundo plano no projeto.",
     "model_desc": "Wait for, check the status of, or list background commands (long downloads/installs/builds) started by code.exec.run. Use action=wait to get a job's result now, or end your reply and get woken up when it finishes."},
    # Preview vivo: sobe um dev server/backend e o mantém no ar entre turnos p/ o usuário
    # abrir e testar o app rodando (com backend), não só ver o arquivo.
    {"path": "code.preview.serve", "name": "Pôr no Ar (Preview)", "description": "Sobe o app do projeto vinculado (dev server/backend) e o mantém no ar para o usuário abrir e testar rodando de verdade — não só como arquivo. Ligar/parar/ver logs; padrão localhost, ou expor na LAN a pedido.",
     "model_desc": "Put the attached project's app ON THE AIR: start a long-running dev server/backend (npm run dev, python app.py, gradlew bootRun…) and keep it up across turns so the user can open and test the running app with its backend. Actions start/status/logs/stop/list; expose localhost (default) or lan. Requires execution enabled. Use when asked to 'run it'/'serve it'/'put it up to test'."},
    # Tarefas/worktrees: cria uma branch isolada (worktree) por tarefa p/ o agente
    # trabalhar sem colidir com o `src` nem com outros agentes; lista/inspeciona o diff
    # e mescla/descarta. O merge normalmente é aprovado pelo humano na UI.
    {"path": "code.task.manage", "name": "Tarefas do Projeto", "description": "Abre uma tarefa isolada (worktree/branch) no projeto vinculado para trabalhar sem afetar o código principal, lista as tarefas, mostra o diff e mescla ou descarta.",
     "model_desc": "Open an isolated worktree/branch to work in without touching the project's main tree, list tasks, show a task's diff, and merge or discard it. Use 'open' before a big or parallel change; merging is usually approved by the human in the UI."},
]
# NOTA: "perguntar opções" (kind:"ask") é uma PRIMITIVA de sistema (tools/interaction.py),
# não uma tool equipável — qualquer ferramenta a usa via `ask_options(...)` (ex.: o Lembrete
# quando falta o destino). Por isso não entra em BUILTIN_TOOLS.


def normalize_sift_path(path: str) -> str:
    """Garante o formato 'categoria.servico.funcao' exigido pela SIFT.

    Paths curtos criados na UI (ex.: 'previsao', 'clima.previsao') são
    completados com o prefixo 'custom' para atingir 3 segmentos, de modo que a
    ferramenta seja realmente registrada em vez de silenciosamente descartada.
    """
    parts = [p for p in (path or "").split(".") if p]
    if not parts:
        return "custom.tools.tool"
    while len(parts) < 3:
        parts.insert(0, "custom")
    return ".".join(parts)


# code.exec.run: comandos que MUDAM o ambiente — instalam, baixam da rede ou apagam —
# pedem confirmação do usuário por padrão (independente do toggle global), o "quer que
# eu instale e execute?" dos agentes de código (Codex/Claude). Testes/build/lint rodam
# direto (fluidez). Casa o verbo no início do comando ou após ; & | (comando encadeado).
_RISKY_EXEC_RE = re.compile(
    r"(?:^|[;&|]|\s)(?:"
    r"sudo|apt|apt-get|dpkg|yum|dnf|apk|brew|"
    r"mise\s+(?:install|use|plugin)|asdf\s+install|sdk\s+install|"
    r"pip3?\s+install|pipx\s+install|poetry\s+add|uv\s+(?:pip\s+install|add)|"
    r"npm\s+(?:i|install|ci|add)|pnpm\s+(?:i|install|add)|yarn\s+(?:add|install)|bun\s+(?:i|install|add)|"
    r"gem\s+install|cargo\s+install|go\s+install|"
    r"curl|wget|git\s+clone|"
    r"rm\s+-\w*[rf]|rmdir|mkfs|dd\s|chmod\s+-R|chown\s+-R"
    r")\b",
    re.IGNORECASE,
)


def _is_risky_exec(command: str) -> bool:
    """True se o comando instala/baixa/apaga (→ pede confirmação por padrão)."""
    return bool(_RISKY_EXEC_RE.search(command or ""))


# Comandos que tendem a DEMORAR (baixam deps / instalam toolchain / buildam) — candidatos
# a rodar em BACKGROUND p/ não estourar o timeout do exec. Heurística: o agente pode forçar
# com background=true/false; isto só decide o modo "auto".
_LONG_RUNNER_RE = re.compile(
    r"(?:^|[;&|]|\s)(?:"
    r"mise\s+(?:install|use)|asdf\s+install|sdk\s+install|"
    r"mvn\b|gradle\b|\./gradlew\b|\./mvnw\b|lein\s+(?:deps|install|uberjar|test)|"
    r"clojure\s+-[PXMA]|"
    r"npm\s+(?:i|install|ci)|pnpm\s+(?:i|install)|yarn\s+(?:install|add)|bun\s+(?:i|install)|"
    r"pip3?\s+install|pipx\s+install|poetry\s+(?:install|add)|uv\s+(?:pip\s+install|sync|add)|"
    r"cargo\s+(?:build|install|test)|go\s+(?:build|install|test|mod\s+download)|"
    r"make\b|cmake\b|docker\s+build|"
    r"apt(?:-get)?\s+install|dpkg\s+-i|yum\s+install|dnf\s+install|apk\s+add|brew\s+install|"
    r"gem\s+install|"
    r"git\s+clone|wget\b"
    r")\b",
    re.IGNORECASE,
)


def _is_long_runner(command: str) -> bool:
    """True se o comando tende a demorar (download/instalação/build) → auto-background."""
    return bool(_LONG_RUNNER_RE.search(command or ""))


# Comandos que NÃO funcionam no sandbox — ele roda sem privilégio (usuário 'app', uid
# 10001), sem sudo, sem apt em runtime e sem daemon do Docker. Duas famílias, com
# conselhos DIFERENTES: (1) Docker/serviço de sistema — normalmente é tentativa de
# RODAR/servir um app → aponte o caminho nativo + code.preview.serve, NÃO "instale um
# toolchain"; (2) sudo/pacote de sistema → aponte o mise. Ancorado à POSIÇÃO de comando
# (início ou após separador) p/ não pegar a palavra num argumento (`npm run docker:build`,
# `python service.py`); um `sudo ` opcional na frente ainda cai na família certa.
_DOCKER_EXEC_RE = re.compile(
    r"(?:^|[\n;|]|&&|\|\|)\s*(?:sudo\s+)?(?:docker-compose|docker|systemctl|service)\b",
    re.IGNORECASE,
)
_ROOT_PKG_RE = re.compile(
    r"(?:^|[\n;|]|&&|\|\|)\s*(?:sudo|apt-get|apt|dpkg|yum|dnf|apk|mount|umount|mkfs)\b",
    re.IGNORECASE,
)


def _needs_root_exec(command: str) -> str | None:
    """Erro ACIONÁVEL quando o comando não roda no sandbox (root/Docker/serviço) — em vez
    de deixar o shell morrer com 'sudo: command not found'. A mensagem já traz o caminho
    certo p/ o modelo se auto-corrigir NO MESMO turno, sem ficar tentando variações."""
    c = command or ""
    if _DOCKER_EXEC_RE.search(c):
        return ("Docker e serviços de sistema (systemctl/service) NÃO estão disponíveis no "
                "sandbox — ele roda sem privilégio e sem daemon do Docker, e instalar o "
                "Docker aqui é impossível (não insista com docker/sudo/apt). Para RODAR ou "
                "SERVIR um app, rode-o NATIVAMENTE: este ambiente tem Java, Node, Python e "
                "Go via `mise` (ex.: `mise use -g java@21` e depois `java -jar app.jar`, ou "
                "o dev server do projeto), e exponha a porta com a tool code.preview.serve. "
                "Se o app precisa de um banco, use um embarcado (ex.: H2/SQLite) ou o "
                "Postgres já disponível — não o Docker.")
    if _ROOT_PKG_RE.search(c):
        return ("O sandbox roda SEM privilégio (usuário 'app', sem sudo e sem apt em "
                "runtime) — não dá pra instalar pacote de sistema. Toolchains de linguagem "
                "vêm do `mise` (ex.: `mise use -g java@21`, `node@22`, `python`, `go`, "
                "`maven`), que instala sem root no volume do projeto; bibliotecas, do "
                "gerenciador do próprio ecossistema (pip/npm/cargo/…).")
    return None


# Nome da integração por PREFIXO de path — uma regra, não uma flag por entrada.
# Tools de integração dependem de uma conta/conexão externa; as "code.*" só
# funcionam num chat de projeto (Codespace); o resto é nativo do app.
_INTEGRATION_PREFIXES: dict[str, str] = {
    "google.": "Google",
    "smartlife.": "Tuya Smart Life",
    "github.": "GitHub",
    "notion.": "Notion",
    "slack.": "Slack",
    "messaging.": "Mensagens",
    "higgsfield.": "Higgsfield",
    "elevenlabs.": "ElevenLabs",
    "vercel.": "Vercel",
    "spotify.": "Spotify",
}


def tool_category(path: str) -> dict[str, str]:
    """{"category": "native"|"codespace"|"integration"[, "integration": nome]}
    — a UI usa p/ ícone + hover; derivada do path, nunca configurada à mão."""
    if path.startswith("code."):
        return {"category": "codespace"}
    for prefix, name in _INTEGRATION_PREFIXES.items():
        if path.startswith(prefix):
            return {"category": "integration", "integration": name}
    return {"category": "native"}


def system_tools() -> list[dict[str, str]]:
    return [{**t, **tool_category(t["path"])} for t in BUILTIN_TOOLS]


_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


async def _fetch_page(url: str, max_chars: int, find: str = "") -> dict[str, Any]:
    """Baixa uma URL pública e devolve o texto legível (HTML removido).

    Reusa os guards de deep_search (`_is_public_url` anti-SSRF, `_html_to_text`).
    Nunca levanta exceção p/ fora — devolve {"error": ...}."""
    if not deep_search._is_public_url(url):
        return {"error": "URL not allowed (must be a public http/https address)"}
    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            r = await client.get(
                url, headers={"User-Agent": deep_search._UA}, timeout=12
            )
    except Exception as exc:  # noqa: BLE001
        return {"error": f"fetch failed: {exc}"}
    if r.status_code != 200:
        return {"error": f"HTTP {r.status_code}"}
    ct = r.headers.get("content-type", "")
    if "html" not in ct and "text" not in ct:
        return {"error": f"unsupported content-type: {ct or 'unknown'}"}
    title = ""
    m = _TITLE_RE.search(r.text)
    if m:
        title = deep_search._html_to_text(m.group(1))[:200]
    text = deep_search._html_to_text(r.text)
    if find:
        idx = text.lower().find(find.lower())
        if idx > 0:
            text = text[max(0, idx - max_chars // 4):]
    text = text[:max_chars]
    return {"ok": True, "url": str(r.url), "title": title, "text": text, "chars": len(text)}


def _public_web_url(url: str) -> bool:
    """Guarda anti-SSRF do Navegador (mais restrita que a do deep_search): além de
    barrar localhost/.local/IP privado LITERAL, exige host com ponto (bloqueia nomes
    de serviço do compose — db, server, browser…) e RESOLVE o host, barrando se cair
    em IP privado (anti DNS-rebinding). Falha de resolução não bloqueia (o dot-check
    já cobre os nomes internos)."""
    import ipaddress
    import socket
    from urllib.parse import urlparse

    if not deep_search._is_public_url(url):
        return False
    host = (urlparse(url).hostname or "").lower()
    if "." not in host or host.endswith("."):
        return False
    try:
        ipaddress.ip_address(host)  # IP literal já passou pelo _is_public_url
        return True
    except ValueError:
        pass
    try:
        for res in socket.getaddrinfo(host, None):
            ip = ipaddress.ip_address(res[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                return False
    except OSError:
        pass  # não resolveu — deixa o browser tentar (o dot-check já barrou os internos)
    return True


async def _store_media(
    user_id: str | None, chat_id: str | None, data: bytes,
    mime: str = "image/png", prompt: str = "", model: str = "",
) -> str | None:
    """Guarda mídia gerada (screenshot, imagem/vídeo da Higgsfield) como
    GeneratedImage e devolve o id (p/ servir por URL assinada — que já honra
    Range, então <video> também funciona).

    Usa um engine EFÊMERO com NullPool: a tool chama isto via `asyncio.run` (loop
    próprio, em threadpool), e o pool compartilhado do engine global prende conexões
    a OUTRO loop (erro 'attached to a different loop'). Uma conexão nova, criada e
    descartada neste mesmo loop, evita o problema (gerações são raras)."""
    if not user_id or not data:
        return None
    import uuid as _uuid

    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.pool import NullPool

    from ..config import get_settings
    from ..models import GeneratedImage
    eng = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with AsyncSession(eng, expire_on_commit=False) as db:
            row = GeneratedImage(
                user_id=_uuid.UUID(user_id),
                chat_id=_uuid.UUID(chat_id) if chat_id else None,
                mime=mime, data=data, prompt=prompt, model=model,
            )
            db.add(row)
            await db.commit()
            await db.refresh(row)
            return str(row.id)
    finally:
        await eng.dispose()


async def _load_media(image_id: str) -> tuple[bytes, str] | None:
    """Bytes + mime de uma mídia gerada (p/ reusar como referência num vídeo:
    a Higgsfield não alcança nossas URLs de LAN, então subimos os bytes)."""
    import uuid as _uuid

    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.pool import NullPool

    from ..config import get_settings
    from ..models import GeneratedImage
    try:
        iid = _uuid.UUID(image_id)
    except ValueError:
        return None
    eng = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with AsyncSession(eng, expire_on_commit=False) as db:
            row = await db.get(GeneratedImage, iid)
            if row is None or not row.data:
                return None
            return bytes(row.data), row.mime or "image/png"
    finally:
        await eng.dispose()


async def _stt_candidates(user_id: str | None) -> list[dict[str, str]]:
    """Candidatos de STT (base_url/api_key/model) do usuário, EM ORDEM: conexão de
    Voz Local primeiro (speaches/faster-whisper), provedor global depois. Resolvido
    ao vivo por sessão efêmera (mesmo motivo do `_store_media`: loop próprio da tool)
    em vez de threadado como config. Vazio = sem STT (o caminho de fala avisa)."""
    if not user_id:
        return []
    import uuid as _uuid

    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.pool import NullPool

    from ..config import get_settings
    from ..integrations import voice_service
    from ..secrets_service import VOICE_KEY, get_secret
    try:
        uid = _uuid.UUID(user_id)
    except ValueError:
        return []
    s = get_settings()
    cands: list[dict[str, str]] = []
    eng = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with AsyncSession(eng, expire_on_commit=False) as db:
            prov = await voice_service.get_provider(db, uid)
            if prov:
                cands.append({"base_url": prov["base_url"], "api_key": prov["api_key"],
                              "model": s.stt_model})
            key = await get_secret(db, uid, VOICE_KEY)
            if key:
                cands.append({"base_url": s.voice_base_url, "api_key": key,
                              "model": s.stt_model})
    finally:
        await eng.dispose()
    return cands


def _library_session(user_id: str | None):
    """(engine efêmero, uuid) p/ ler a biblioteca do usuário de dentro de uma tool.
    Mesmo motivo do `_store_media`: a tool tem loop próprio e o pool global prende
    conexões a OUTRO loop."""
    import uuid as _uuid

    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    from ..config import get_settings
    if not user_id:
        return None, None
    try:
        uid = _uuid.UUID(user_id)
    except ValueError:
        return None, None
    return create_async_engine(get_settings().database_url, poolclass=NullPool), uid


async def _library_list(user_id: str | None, kind: str) -> dict[str, Any]:
    """Índice barato da biblioteca: skills (slug/nome/descrição) ou prompts
    (comando/título). Só o suficiente p/ a IA saber o que já existe e não duplicar."""
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession

    from ..models import Prompt, Skill
    eng, uid = _library_session(user_id)
    if eng is None:
        return {"error": "no user in context"}
    try:
        async with AsyncSession(eng, expire_on_commit=False) as db:
            if kind == "skill":
                rows = list(await db.scalars(select(Skill).where(Skill.user_id == uid)))
                items = [
                    {"slug": r.slug, "name": r.name,
                     "description": (r.description or "")[:300], "enabled": r.enabled}
                    for r in rows
                ]
                return {"ok": True, "skills": items, "count": len(items)}
            rows = list(await db.scalars(select(Prompt).where(Prompt.user_id == uid)))
            items = [
                {"command": r.command, "title": r.title, "enabled": r.enabled} for r in rows
            ]
            return {"ok": True, "prompts": items, "count": len(items)}
    finally:
        await eng.dispose()


async def _library_read(user_id: str | None, kind: str, key: str) -> dict[str, Any]:
    """Conteúdo completo de UMA skill (por slug) ou prompt (por comando)."""
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession

    from ..models import Prompt, Skill
    eng, uid = _library_session(user_id)
    if eng is None:
        return {"error": "no user in context"}
    try:
        async with AsyncSession(eng, expire_on_commit=False) as db:
            if kind == "skill":
                row = (await db.scalars(select(Skill).where(
                    Skill.user_id == uid, Skill.slug == key))).first()
                if row is None:
                    return {"error": f"no skill with slug '{key}' (use action=list to see them)"}
                return {"ok": True, "slug": row.slug, "name": row.name,
                        "description": row.description or "", "content": row.content or "",
                        "tags": list(row.tags or [])}
            row = (await db.scalars(select(Prompt).where(
                Prompt.user_id == uid, Prompt.command == key))).first()
            if row is None:
                return {"error": f"no prompt with command '{key}' (use action=list to see them)"}
            return {"ok": True, "command": row.command, "title": row.title,
                    "content": row.content or ""}
    finally:
        await eng.dispose()


def _browser_endpoint(browser_cfg: dict | None) -> str:
    """Endpoint CDP do navegador (ws_url[?token=]) resolvido por CAMADAS:
    config por-usuário (Conexões → Web) primeiro; senão o env (BROWSER_WS_URL/
    BROWSER_TOKEN). Devolve "" quando desativado/sem URL — a tool avisa."""
    cfg = browser_cfg or {}
    if cfg.get("enabled") is False:
        return ""
    ws = (cfg.get("ws_url") or "").strip()
    token = (cfg.get("token") or "").strip()
    if not ws:
        s = get_settings()
        ws = (s.browser_ws_url or "").strip()
        token = (s.browser_token or "").strip()
    if not ws:
        return ""
    return f"{ws}?token={token}" if token else ws


# Chaves que a SIFT deixa passar no retorno de `code.graph.query`. É um FILTRO:
# o que não está aqui é descartado antes de chegar ao modelo — foi assim que
# 'status'/'doctor' acabaram respondendo só "files/symbols", justo as duas ações
# cujo valor é explicar um índice vazio ou uma busca que não acha nada. O bloco
# de diagnóstico está coberto por teste contra o retorno REAL do graph_service.
CODE_GRAPH_RETURNS = [
    # consultas estruturais
    "symbols", "target", "callers", "callees", "references", "affects", "symbol",
    "children", "calls", "called_by", "counts", "domain", "communities", "meta",
    "total_found", "total_files", "warnings", "files", "edges", "error",
    # consultas de alto nível (v0.1.0): change_impact / affected_modules /
    # related_tests / suggest_files (explain reusa symbol/children/counts/domain/
    # callers/callees, já acima).
    "changed_files", "changed_symbols", "impacted", "n_changed", "n_impacted",
    "modules", "n_modules", "tests", "n", "task", "tokens",
    # diagnóstico (status/doctor)
    "by_language", "edges_resolved", "edges_dangling", "parse_partial",
    "parse", "parse_failed_total", "parse_failed_sample", "call_edges",
    "confidence", "certain_pct", "dangling", "l1_resolvers",
    "l1_missing",  # doctor() >= v0.1.0: quais resolvers L1 NÃO estão instalados
    "last_full_scan_age_s", "indexer_version",
    "root_name",  # doctor() >= 45a35c4: só o NOME do diretório (não vaza path)
]


def _register_builtins(
    sift: Sift,
    search_cfg: SearchConfig,
    allowed: set[str] | None,
    finance_cfg: "finance.FinanceConfig | None" = None,
    deep_cfg: "deep_search.DeepSearchConfig | None" = None,
    user_id: str | None = None,
    google_cfg: "GoogleConfig | None" = None,
    tuya_cfg: "TuyaConfig | None" = None,
    github_cfg: "GithubConfig | None" = None,
    messaging_cfg: "MessagingConfig | None" = None,
    browser_cfg: dict | None = None,
    higgsfield_cfg: "HiggsfieldConfig | None" = None,
    notion_cfg: "NotionConfig | None" = None,
    slack_cfg: "SlackConfig | None" = None,
    elevenlabs_cfg: "ElevenLabsConfig | None" = None,
    vercel_cfg: "VercelConfig | None" = None,
    spotify_cfg: "SpotifyConfig | None" = None,
) -> None:
    """Registra as ferramentas de sistema. `allowed=None` = todas; caso contrário
    apenas os paths presentes no conjunto."""

    def want(path: str) -> bool:
        return allowed is None or path in allowed

    if want("task.tracker"):
        @sift.tool(
            "task.tracker",
            description=(
                "Your persistent WORKING MEMORY for the CURRENT multi-step objective (one "
                "ledger per chat), shown to you at the top of every turn. Keep it updated AS "
                "YOU WORK so you converge on long tasks (a refactor, a feature, a security "
                "review) instead of re-deriving each turn. Record: the objective, a plan whose "
                "steps you move todo→doing→done, findings you move open→confirmed→refuted, "
                "evidence notes, and the next step. A finding you set to 'refuted' was "
                "disproven by evidence — never present it again as a valid conclusion. "
                "Actions: 'get' (read it); 'set' (objective/next_step/status=active|done|"
                "paused); 'plan_add' (text→new step); 'plan_status' (id + status=todo|doing|"
                "done|blocked [+note]); 'note' (text→append an observation/evidence); "
                "'finding_add' (text [+status]); 'finding_status' (id + status=open|confirmed|"
                "refuted [+evidence])."
            ),
            params={
                "action": "string:r::get | set | plan_add | plan_status | note | finding_add | finding_status",
                "objective": "string:o::set: the one-line goal you're working toward",
                "next_step": "string:o::set: the immediate next concrete action",
                "status": "string:o::set→active|done|paused; plan_status→todo|doing|done|blocked; finding_*→open|confirmed|refuted",
                "text": "string:o::plan_add / note / finding_add: the step / observation / finding text",
                "id": "string:o::plan_status / finding_status: the step or finding id (e.g. s1, f2)",
                "evidence": "string:o::finding_add / finding_status: the evidence backing this finding",
                "note": "string:o::plan_status: a short note on the step",
            },
            returns=["ok", "ledger", "error"],
            examples=["track this refactor as a plan", "mark step s2 done",
                      "record that the SSRF hypothesis was refuted", "what's my current plan?",
                      "note the test output", "set the objective and next step"],
        )
        def _task_tracker(action: str = "", objective: Any = None, next_step: Any = None,
                          status: Any = None, text: Any = None, id: Any = None,
                          evidence: Any = None, note: Any = None) -> dict[str, Any]:
            from ..chat import ledger_service
            chat_id = toolctx.current_chat_id.get()
            if not chat_id:
                return {"error": "sem chat vinculado — o ledger de tarefa é por chat"}
            project_id = toolctx.current_codespace_project_id.get()
            kw = {"objective": objective, "next_step": next_step, "status": status,
                  "text": text, "id": id, "evidence": evidence, "note": note}
            kw = {k: v for k, v in kw.items() if v is not None}
            return ledger_service.apply(str(user_id or ""), str(chat_id), project_id,
                                        (action or "").strip().lower(), **kw)

    if want("utils.time.now"):
        @sift.tool(
            "utils.time.now",
            # descrição vista pelo MODELO em inglês (tool-calling mais confiável) e
            # direta: o que faz + quando usar. Nada de "retorno compacto" e afins —
            # isso é implementação, o modelo não decide nada com essa info.
            description="Current date and time in a given time zone (IANA, e.g. 'America/Sao_Paulo'; empty = UTC).",
            params={"timezone": "string:o::IANA time zone, e.g. America/Sao_Paulo (default UTC)"},
            returns=["datetime", "weekday", "unix", "error"],
        )
        def _time_now(timezone: str = "") -> dict[str, Any]:
            tz: _dt.tzinfo = _dt.timezone.utc
            if timezone:
                try:
                    from zoneinfo import ZoneInfo

                    tz = ZoneInfo(timezone)
                except Exception:  # noqa: BLE001
                    return {"error": f"invalid time zone: {timezone}"}
            now = _dt.datetime.now(tz)
            return {
                "datetime": now.strftime("%Y-%m-%d %H:%M:%S %Z").strip(),
                "weekday": now.strftime("%A"),
                "unix": int(now.timestamp()),
            }

    if want("utils.math.eval"):
        @sift.tool(
            "utils.math.eval",
            description=(
                "Evaluate a math expression: arithmetic, common functions (sqrt, sin, "
                "log, factorial, …) and constants (pi, e, tau). E.g. 'sqrt(2)*sin(pi/4)'."
            ),
            params={"expression": "string:o::math expression"},
            returns=["result", "error"],
        )
        def _math_eval(expression: str = "") -> dict[str, Any]:
            try:
                return {"result": _safe_eval(expression)}
            except Exception as exc:  # noqa: BLE001
                return {"error": str(exc)}

    if want("user.profile.get"):
        @sift.tool(
            "user.profile.get",
            description=(
                "Get the current user's account/profile info to personalize answers or when "
                "they ask about their own data (name, age, birthday, etc.). Returns only the "
                "fields the user has filled in: name, about (free-text bio), gender, birthdate "
                "(YYYY-MM-DD) with computed age, email, language. Never invent these values — "
                "call this tool instead of guessing."
            ),
            params={},
            returns=["name", "about", "gender", "birthdate", "age", "email", "language", "note"],
        )
        def _user_profile() -> dict[str, Any]:
            prof = toolctx.user_profile.get() or {}
            out: dict[str, Any] = {k: v for k, v in prof.items() if v}
            bd = (prof.get("birthdate") or "").strip()
            if bd:
                try:
                    d = _dt.date.fromisoformat(bd)
                    today = _dt.date.today()
                    out["age"] = today.year - d.year - ((today.month, today.day) < (d.month, d.day))
                except ValueError:
                    pass
            if not out:
                out["note"] = "The user has not filled in any profile info yet."
            return out

    if want("web.search.query"):
        @sift.tool(
            "web.search.query",
            description=(
                "Search the web for current or factual info you don't know: news, "
                "schedules, sports results, prices, events."
            ),
            params={
                "query": "string:o::search terms",
                "limit": "number:o:5:max results (1-10)",
            },
            returns=["results", "error"],
        )
        def _web_search(query: str = "", limit: int = 5) -> dict[str, Any]:
            try:
                n = max(1, min(int(limit or 5), 10))
                results = asyncio.run(web_search(query, search_cfg))
                # enxuga: trecho curto, sem campos redundantes → poucos tokens
                trimmed = [
                    {
                        "title": (r.get("title") or "")[:120],
                        "url": r.get("url") or "",
                        "snippet": (r.get("content") or "").strip()[:200],
                    }
                    for r in results[:n]
                ]
                return {"results": trimmed}
            except Exception as exc:  # noqa: BLE001
                return {"error": str(exc)}

    if want("web.page.read"):
        @sift.tool(
            "web.page.read",
            description=(
                "Fetch a web page by URL and return its readable text (HTML stripped). "
                "Use this to actually READ a page's content — e.g. after web.search.query "
                "gives you a link, or when the user hands you a URL. Public http/https "
                "pages only. Optionally pass `find` to center the excerpt on a keyword."
            ),
            params={
                "url": "string:o::the page URL (http/https)",
                "max_chars": "number:o:6000:max characters of text to return (500-20000)",
                "find": "string:o::optional keyword to focus the excerpt around",
            },
            returns=["ok", "url", "title", "text", "chars", "error"],
        )
        def _web_page_read(url: str = "", max_chars: Any = 6000, find: str = "") -> dict[str, Any]:
            u = (url or "").strip()
            if not u:
                return {"error": "provide a URL (http/https)"}
            if not u.lower().startswith(("http://", "https://")):
                u = "https://" + u
            try:
                cap = max(500, min(int(max_chars or 6000), 20000))
            except (TypeError, ValueError):
                cap = 6000
            try:
                return asyncio.run(_fetch_page(u, cap, (find or "").strip()))
            except Exception as exc:  # noqa: BLE001
                return {"error": str(exc)}

    if want("web.browser.use"):
        @sift.tool(
            "web.browser.use",
            description=(
                "Drive a REAL headless Chromium browser. It renders JavaScript and keeps a "
                "LIVE tab across calls in this conversation — use it for JS-heavy sites, "
                "SPAs, logins, and multi-step forms that web.page.read can't handle. "
                "Actions: 'goto' (open a URL), 'read' (current page's text + a numbered list "
                "of clickable 'elements'), 'click' (target = the element's visible text OR a "
                "CSS selector), 'type' (target = a field's label/placeholder/CSS, `text` to "
                "enter, set submit=true to press Enter), 'scroll' (up/down/top/bottom), "
                "'back', 'screenshot' (shows the page image to the user), 'close'. After "
                "goto/click, look at the returned 'elements' to pick your next target."
            ),
            params={
                "action": "string:r::goto | read | click | type | scroll | back | screenshot | close",
                "url": "string:o::URL for 'goto' (http/https)",
                "target": "string:o::for click/type: the element's visible text OR a CSS selector",
                "text": "string:o::for 'type': the text to enter in the field",
                "submit": "boolean:o:false:for 'type': press Enter after filling",
                "direction": "string:o:down:for 'scroll': up | down | top | bottom",
            },
            returns=["ok", "url", "title", "text", "elements", "kind", "note", "error"],
        )
        def _browser_use(
            action: str = "", url: str = "", target: str = "",
            text: str = "", submit: Any = False, direction: str = "down",
        ) -> dict[str, Any]:
            from .browser_driver import driver
            from ..providers import image_gen
            endpoint = _browser_endpoint(browser_cfg)
            if not endpoint:
                return {"error": "O navegador não está ativo. Configure-o em "
                        "Configurações → Conexões → Web (ou suba o serviço: "
                        "docker compose --profile browser up -d browser)."}
            act = (action or "").strip().lower()
            chat_id = toolctx.current_chat_id.get()
            key = chat_id or (user_id or "anon")
            try:
                if act == "goto":
                    u = (url or "").strip()
                    if not u:
                        return {"error": "provide a URL for 'goto'"}
                    if not u.lower().startswith(("http://", "https://")):
                        u = "https://" + u
                    if not _public_web_url(u):
                        return {"error": "URL not allowed (only public http/https sites)"}
                    return driver.goto(endpoint, key, u)
                if act == "read":
                    return driver.read(endpoint, key)
                if act == "click":
                    if not (target or "").strip():
                        return {"error": "provide 'target' (visible text or CSS selector)"}
                    return driver.click(endpoint, key, target.strip())
                if act == "type":
                    return driver.type(endpoint, key, (target or "").strip(), text or "", bool(submit))
                if act == "scroll":
                    return driver.scroll(endpoint, key, (direction or "down").strip().lower())
                if act == "back":
                    return driver.back(endpoint, key)
                if act == "screenshot":
                    png = driver.screenshot(endpoint, key)
                    iid = asyncio.run(_store_media(
                        user_id, chat_id, png,
                        mime="image/png", prompt="browser screenshot", model="browser",
                    ))
                    if not iid:
                        return {"error": "screenshot capturado, mas falhou ao salvar"}
                    return {"ok": True, "kind": "image", "url": image_gen.sign_image_url(iid),
                            "note": "screenshot da página atual"}
                if act == "close":
                    return driver.close(endpoint, key)
                return {"error": f"unknown action '{act}' (use goto/read/click/type/scroll/back/screenshot/close)"}
            except Exception as exc:  # noqa: BLE001 - erros do browser não quebram o turno
                return {"error": str(exc)[:300]}

    if want("media.video.transcribe"):
        @sift.tool(
            "media.video.transcribe",
            description=(
                "Get the spoken CONTENT of a video or audio at a URL — YouTube and ~1800 "
                "other sites (Vimeo, TikTok, news sites, podcasts, direct media links…). "
                "Returns a text transcript so you can summarize it, answer questions about "
                "it, or quote it. Uses the video's captions when available (fast); otherwise "
                "downloads the audio and transcribes the speech. Use whenever the user shares "
                "a video/audio link and wants its content, a summary, or Q&A. Public http/https "
                "only; long media can take a while.\n"
                "It also returns the real platform metadata: 'title', 'channel' (who published "
                "it), 'upload_date' and 'duration'. NEVER guess or infer the channel/author from "
                "the spoken text — the transcript does not contain it. If a field is absent from "
                "the result, say you don't know instead of inventing it.\n"
                "One call is enough: the result is cached, and a truncated transcript is still "
                "enough to summarize. Do not call repeatedly with slightly larger max_chars."
            ),
            params={
                "url": "string:r::the video/audio URL (http/https)",
                "lang": "string:o::preferred caption language code, e.g. 'en' or 'pt' (optional; leave empty to use the video's own language)",
                "max_chars": "number:o:12000:max transcript characters (1000-50000); the default suits most videos — do not lower it to 'sample' first",
            },
            returns=["ok", "source", "title", "channel", "upload_date", "webpage_url",
                     "lang", "duration", "text", "chars", "full_chars", "truncated",
                     "note", "error"],
        )
        def _media_transcribe(url: str = "", lang: str = "", max_chars: Any = 12000) -> dict[str, Any]:
            from ..integrations import transcribe_service
            u = (url or "").strip()
            if not u:
                return {"error": "provide a video/audio URL (http/https)"}
            if not u.lower().startswith(("http://", "https://")):
                u = "https://" + u
            if not _public_web_url(u):
                return {"error": "URL not allowed (only public http/https sites)"}
            try:
                cap = max(1000, min(int(max_chars or 12000), 50000))
            except (TypeError, ValueError):
                cap = 12000

            async def _go() -> dict[str, Any]:
                cands = await _stt_candidates(user_id)
                return await transcribe_service.transcribe_url(u, (lang or "").strip(), cap, cands)

            try:
                return asyncio.run(_go())
            except Exception as exc:  # noqa: BLE001 - falhas de rede/yt-dlp não quebram o turno
                return {"error": str(exc)[:300]}

    if want("skills.library.manage"):
        @sift.tool(
            "skills.library.manage",
            description=(
                "The user's SKILL library (reusable procedure documents invoked with $slug). "
                "Actions: 'list' (their skills: slug, name, description), 'read' (full content "
                "of one skill by `slug`), 'import' (download a skill from `url` — a SKILL.md "
                "file, or a GitHub folder/repo which also pulls its reference files like "
                "references/*, or any page — and propose it), 'create' (propose one you wrote "
                "yourself from `name`/`description`/`content`). "
                "IMPORTANT: 'import' and 'create' only PROPOSE — the user reviews an editable "
                "card and approves; nothing is written automatically, so tell the user to "
                "approve the card rather than claiming the skill was saved."
            ),
            params={
                "action": "string:r::list | read | import | create",
                "url": "string:o::for 'import': the skill's URL (http/https)",
                "slug": "string:o::for 'read': which skill; for 'create': its identifier",
                "name": "string:o::for 'create': short human-readable name",
                "description": "string:o::for 'create': WHEN to use it (1-3 sentences)",
                "content": "string:o::for 'create': HOW to do it, full markdown instructions",
            },
            returns=["ok", "kind", "proposal_id", "skills", "count", "slug", "name",
                     "description", "content", "files", "tags", "source_url", "aux_count",
                     "note", "error"],
        )
        def _skills_manage(
            action: str = "", url: str = "", slug: str = "",
            name: str = "", description: str = "", content: str = "",
        ) -> dict[str, Any]:
            import uuid as _uuid

            from ..integrations import library_import
            act = (action or "").strip().lower()
            if act == "list":
                return asyncio.run(_library_list(user_id, "skill"))
            if act == "read":
                if not (slug or "").strip():
                    return {"error": "provide 'slug' of the skill to read"}
                return asyncio.run(_library_read(user_id, "skill", slug.strip()))
            if act == "import":
                u = (url or "").strip()
                if not u:
                    return {"error": "provide the skill 'url' to import"}
                if not u.lower().startswith(("http://", "https://")):
                    u = "https://" + u
                if not _public_web_url(u):
                    return {"error": "URL not allowed (only public http/https sites)"}
                got = asyncio.run(library_import.fetch_skill(u))
                if "error" in got:
                    return got
                return {
                    "kind": "skill_proposal", "proposal_id": str(_uuid.uuid4()),
                    "slug": got["slug"], "name": got["name"],
                    "description": got["description"], "content": got["content"],
                    "files": got.get("files", []),
                    "tags": [], "source_url": got["source_url"],
                    "aux_count": got.get("aux_count", 0),
                    "note": "Proposal shown to the user for approval — NOT saved yet.",
                }
            if act == "create":
                n, d, c = (name or "").strip(), (description or "").strip(), (content or "").strip()
                if not n or not c:
                    return {"error": "'name' and 'content' are required to create a skill"}
                return {
                    "kind": "skill_proposal", "proposal_id": str(_uuid.uuid4()),
                    "slug": library_import.slugify(slug or n),
                    "name": n[:255], "description": d[:2000] or f"Skill: {n}",
                    "content": c[:library_import.MAX_CONTENT], "tags": [],
                    "note": "Proposal shown to the user for approval — NOT saved yet.",
                }
            return {"error": f"unknown action '{act}' (use list/read/import/create)"}

    if want("prompts.library.manage"):
        @sift.tool(
            "prompts.library.manage",
            description=(
                "The user's reusable PROMPT library (snippets they invoke by typing /command "
                "in the message box). Actions: 'list' (their prompts: command, title), 'read' "
                "(full content by `command`), 'import' (fetch `url` and propose its text as a "
                "prompt), 'create' (propose one from `title`/`content`). "
                "IMPORTANT: 'import' and 'create' only PROPOSE — the user approves an editable "
                "card; nothing is written automatically. A prompt is text the USER will send, "
                "not instructions you follow now."
            ),
            params={
                "action": "string:r::list | read | import | create",
                "url": "string:o::for 'import': the URL to turn into a prompt",
                "command": "string:o::for 'read': which prompt; for 'create': its /command",
                "title": "string:o::for 'create': short human-readable title",
                "content": "string:o::for 'create': the prompt text itself",
            },
            returns=["ok", "kind", "proposal_id", "prompts", "count", "command", "title",
                     "content", "source_url", "note", "error"],
        )
        def _prompts_manage(
            action: str = "", url: str = "", command: str = "",
            title: str = "", content: str = "",
        ) -> dict[str, Any]:
            import uuid as _uuid

            from ..integrations import library_import
            act = (action or "").strip().lower()
            if act == "list":
                return asyncio.run(_library_list(user_id, "prompt"))
            if act == "read":
                if not (command or "").strip():
                    return {"error": "provide 'command' of the prompt to read"}
                return asyncio.run(_library_read(user_id, "prompt", command.strip().lstrip("/")))
            if act in ("import", "create"):
                if act == "import":
                    u = (url or "").strip()
                    if not u:
                        return {"error": "provide the 'url' to import"}
                    if not u.lower().startswith(("http://", "https://")):
                        u = "https://" + u
                    if not _public_web_url(u):
                        return {"error": "URL not allowed (only public http/https sites)"}
                    got = asyncio.run(library_import.fetch_text(u))
                    if "error" in got:
                        return got
                    ttl = (title or got.get("title") or "").strip() or "Prompt importado"
                    body, src = got["content"], got.get("source_url", "")
                else:
                    ttl, body, src = (title or "").strip(), (content or "").strip(), ""
                    if not ttl or not body:
                        return {"error": "'title' and 'content' are required to create a prompt"}
                # comando usa HÍFEN (o slug de skill usa underscore); e o POST /prompts
                # limita o conteúdo a 100k — propor mais que isso seria insalvável.
                cmd = library_import.slugify(command or ttl, "prompt", sep="-")
                return {
                    "kind": "prompt_proposal", "proposal_id": str(_uuid.uuid4()),
                    "command": cmd, "title": ttl[:255],
                    "content": body[:100_000], "source_url": src,
                    "note": "Proposal shown to the user for approval — NOT saved yet.",
                }
            return {"error": f"unknown action '{act}' (use list/read/import/create)"}

    # Codespace (grafo de código) — só existe se este chat estiver vinculado a um
    # projeto (toolctx.current_codespace_project_id). O projeto é resolvido a CADA
    # chamada (ownership check embutido em graph_service.load_project) em vez de
    # threadado como config: cada chat pode ter um projeto diferente, mas a
    # instância SIFT é cacheada por-USUÁRIO (não por-chat) — mesma solução do
    # navegador (current_chat_id) e do mesmo motivo.
    if (want("code.graph.query") or want("code.files.browse") or want("code.files.write")
            or want("code.flow.analyze") or want("code.exec.run") or want("code.task.manage")
            or want("code.preview.serve")):
        from ..codespace import graph_service

        def _cs_project():
            """Resolve e valida o projeto do chat atual. Devolve (project, None) ou
            (None, error_dict) — o error_dict já é a resposta pronta da tool."""
            pid = toolctx.current_codespace_project_id.get()
            if not pid:
                return None, {"error": "Nenhum projeto do Codespace vinculado a este chat. "
                              "Vincule um projeto em Espaço de Trabalho → Codespace."}
            proj = asyncio.run(graph_service.load_project(user_id, pid))
            if proj is None:
                return None, {"error": "projeto não encontrado (ou não pertence a este usuário)"}
            return proj, None

        def _cs_project_ctx():
            """Como `_cs_project`, mas também devolve se 'confirmar ações' está
            ligado — usado pelas ações destrutivas/externas (delete/push)."""
            pid = toolctx.current_codespace_project_id.get()
            if not pid:
                return None, False, {"error": "Nenhum projeto do Codespace vinculado a este chat. "
                                      "Vincule um projeto em Espaço de Trabalho → Codespace."}
            proj, confirm = asyncio.run(graph_service.load_project_ctx(user_id, pid))
            if proj is None:
                return None, False, {"error": "projeto não encontrado (ou não pertence a este usuário)"}
            return proj, confirm, None

        def _cs_confirm_guard(confirm_on: bool, summary: str, confirm: Any) -> dict | None:
            # mesmo toggle global (Configurações → Segurança) que já protege
            # escritas do GitHub/Google/Tuya; em background (automação/canal)
            # não há usuário pra confirmar → executa direto, igual às demais.
            truthy = confirm is True or (isinstance(confirm, str) and confirm.strip().lower() in ("true", "1", "yes", "sim", "on"))
            if confirm_on and not truthy and not toolctx.background.get():
                from .interaction import ask_options
                return ask_options(
                    summary,
                    [
                        {"label": "Confirmar", "value": "Sim, confirmo — refaça a ação agora com confirm=true."},
                        {"label": "Cancelar", "value": "Cancele, não execute a ação."},
                    ],
                    allow_custom=False,
                )
            return None

        _CS_STATUS_MSG = {
            "pending": "o projeto ainda não começou a clonar — aguarde alguns segundos e tente de novo",
            "cloning": "o projeto está sendo clonado — aguarde e tente de novo em instantes",
            "indexing": "o projeto está sendo indexado — aguarde e tente de novo em instantes",
            "error": "a última indexação falhou: {err}",
        }

        def _cs_require_ready(proj):
            if proj.index_status == "ready":
                return None
            msg = _CS_STATUS_MSG.get(proj.index_status, proj.index_status)
            if proj.index_status == "error":
                msg = msg.format(err=(proj.error_message or "erro desconhecido")[:300])
            return {"error": msg}

    if want("code.graph.query"):
        @sift.tool(
            "code.graph.query",
            description=(
                "Query the code graph of the project attached to this chat — structural, not text "
                "search: exact call sites, blast radius, symbol neighborhood. Prefer this over "
                "grepping when a project is attached. `action`: 'overview' (START HERE on an "
                "unfamiliar project — the important files/symbols ranked by relevance, within a "
                "token budget), 'find' (locate a symbol by name; returns fqn/kind/path/line/"
                "signature — use its `fqn` in the other actions), 'callers' (who calls this symbol), "
                "'callees' (what this symbol calls), 'references' (EVERY reference, not just calls "
                "— includes imports), 'impact' (what breaks if you change this — transitive callers), "
                "'ego' (neighborhood: parent/children/calls/called_by), 'info' (definition + how many "
                "callers/callees/references it has), 'explain' (info + top callers/callees + domain in "
                "one shot — decide about a symbol WITHOUT re-reading its code), 'communities' (how the "
                "codebase clusters into modules), 'suggest_files' (given a natural-language TASK, the "
                "files most worth opening first — START HERE when you don't know a symbol name yet), "
                "'related_tests' (which existing tests exercise a symbol), 'change_impact' (blast "
                "radius of a SET of changes — pass `target` as paths or a diff, not an fqn — what to "
                "re-review/re-test), 'affected_modules' (change_impact grouped by file/module), "
                "'status' (index counts), 'doctor' (index health: files that failed to parse, %% of "
                "certain edges — use when a symbol you expect is missing). Edges carry a confidence "
                "level (certain/inferred/possible) — static analysis, so treat 'possible' as a hint, "
                "not a fact."
            ),
            params={
                "action": ("string:r::overview | find | callers | callees | references | impact | "
                           "ego | info | explain | communities | suggest_files | related_tests | "
                           "change_impact | affected_modules | status | doctor"),
                "query": "string:o::find: symbol name or substring to search for",
                "symbol": "string:o::callers/callees/references/impact/ego/info/explain/related_tests: the fully-qualified name (fqn) from a previous 'find'",
                "task": "string:o::suggest_files: a natural-language description of what you want to do",
                "target": "string:o::change_impact/affected_modules: changed paths (comma/space-separated) or a unified diff",
                "depth": "number:o::callers/callees/impact/related_tests/change_impact: hops to follow (1-4, impact/change 1-5)",
                "limit": "number:o:10:find/suggest_files: max matches to return",
                "scope": "string:o::overview: restrict to a subtree (e.g. 'src/api')",
                "token_budget": "number:o:2000:overview: how big the map may be (200-8000)",
                "kind": "string:o::references: filter by reference kind (e.g. 'calls', 'imports')",
            },
            returns=CODE_GRAPH_RETURNS,
            examples=["give me an overview of this project", "find the function validate_token",
                      "who calls run_turn?", "what does handle_request call?",
                      "find all references to Settings", "what breaks if I change _register_builtins?",
                      "which files should I read to add rate limiting?", "what tests cover run_turn?",
                      "what's the blast radius of my current diff?", "why can't you find my Rust files?"],
        )
        def _code_graph_query(action: str = "", query: str = "", symbol: str = "",
                               depth: Any = None, limit: Any = 10, scope: str = "",
                               token_budget: Any = 2000, kind: str = "",
                               task: str = "", target: str = "") -> dict[str, Any]:
            proj, err = _cs_project()
            if err:
                return err
            act = (action or "").strip().lower()
            # 'status'/'doctor' respondem SOBRE o índice — úteis justamente quando
            # ele ainda não está pronto; as demais exigem 'ready'.
            if act not in ("status", "doctor"):
                bad = _cs_require_ready(proj)
                if bad:
                    return bad
            uid, pid = str(proj.user_id), str(proj.id)

            def _int(v: Any, default: int) -> int:
                try:
                    return int(v) if v not in (None, "") else default
                except (TypeError, ValueError):
                    return default

            try:
                if act == "overview":
                    return graph_service.overview(
                        uid, pid, scope=scope or "", token_budget=_int(token_budget, 2000)
                    )
                if act == "find":
                    if not (query or "").strip():
                        return {"error": "provide 'query' (symbol name to search for)"}
                    return graph_service.find(uid, pid, query.strip(), limit=_int(limit, 10))
                if act == "suggest_files":
                    if not (task or "").strip():
                        return {"error": "provide 'task' (what you want to do, in natural language)"}
                    return graph_service.suggest_files(uid, pid, task.strip(), limit=_int(limit, 8))
                if act in ("change_impact", "affected_modules"):
                    if not (target or "").strip():
                        return {"error": f"provide 'target' (changed paths or a diff) for '{act}'"}
                    tgt, dep = target.strip(), _int(depth, 3)
                    if act == "change_impact":
                        return graph_service.change_impact(uid, pid, tgt, depth=dep)
                    return graph_service.affected_modules(uid, pid, tgt, depth=dep)
                if act in ("callers", "callees", "references", "impact", "ego", "info",
                           "explain", "related_tests"):
                    if not (symbol or "").strip():
                        return {"error": f"provide 'symbol' (fqn from a previous 'find') for '{act}'"}
                    sym = symbol.strip()
                    if act == "callers":
                        return graph_service.callers(uid, pid, sym, depth=_int(depth, 1))
                    if act == "callees":
                        return graph_service.callees(uid, pid, sym, depth=_int(depth, 1))
                    if act == "references":
                        return graph_service.references(uid, pid, sym, kind=kind or "")
                    if act == "impact":
                        return graph_service.impact(uid, pid, sym, depth=_int(depth, 3))
                    if act == "info":
                        return graph_service.symbol_info(uid, pid, sym)
                    if act == "explain":
                        return graph_service.explain(uid, pid, sym)
                    if act == "related_tests":
                        return graph_service.related_tests(uid, pid, sym, depth=_int(depth, 3))
                    return graph_service.ego(uid, pid, sym)
                if act == "communities":
                    return graph_service.communities(uid, pid, limit=_int(limit, 20))
                if act == "status":
                    return graph_service.status(uid, pid)
                if act == "doctor":
                    return graph_service.doctor(uid, pid, failed_limit=_int(limit, 20))
                return {"error": f"unknown action '{act}' (use overview/find/callers/callees/"
                                 "references/impact/ego/info/explain/communities/suggest_files/"
                                 "related_tests/change_impact/affected_modules/status/doctor)"}
            except Exception as exc:  # noqa: BLE001 - erro do grafo não quebra o turno
                return {"error": str(exc)[:300]}

    if want("code.files.browse"):
        @sift.tool(
            "code.files.browse",
            description=(
                "List/read/search the files of the project attached to this chat, plus its git "
                "history (respects the project's allow/deny scope — a path outside it is refused). "
                "`action`: 'list' (directory tree from `path`, default the project root), 'read' "
                "(file content, numbered lines; use `start_line`/`end_line` for a range on large "
                "files), 'search' (grep across the project — literal by default, set `regex=true` "
                "for a pattern; `files_only=true` answers \"which files mention X\" far cheaper; "
                "`context` shows surrounding lines), 'log' (recent commits — every write/edit/delete "
                "auto-commits), 'diff' (what changed; empty `ref` = uncommitted changes, or pass a "
                "sha/'HEAD~N')."
            ),
            params={
                "action": "string:r::list | read | search | log | diff",
                "path": "string:o::list/read/diff: path relative to the project root (default: root)",
                "query": "string:o::search: text (or regex, with regex=true) to look for",
                "glob": "string:o::search: restrict to files matching this glob (e.g. 'src/**/*.py')",
                "regex": "boolean:o::search: treat `query` as a regular expression instead of literal text",
                "case_sensitive": "boolean:o::search: match case exactly (default: case-insensitive)",
                "context": "number:o:0:search: lines of context to show around each match (0-5)",
                "files_only": "boolean:o::search: return only the list of matching files, not each line",
                "start_line": "number:o:1:read: first line to return",
                "end_line": "number:o::read: last line to return (default: start_line + 400)",
                "depth": "number:o:3:list: how many directory levels to descend",
                "limit": "number:o:20:log: how many commits to return",
                "ref": "string:o::diff: sha/'HEAD~N' to compare against (default: uncommitted changes)",
            },
            returns=["entries", "content", "results", "files", "commits", "diff", "path",
                     "total_lines", "start_line", "end_line", "truncated", "error"],
            examples=["list the src folder", "read config.py", "search TODO in the project",
                      "which files mention DATABASE_URL?", "find all functions starting with handle_",
                      "show the last 5 commits", "what did the last commit change?"],
        )
        def _code_files_browse(action: str = "", path: str = "", query: str = "", glob: str = "",
                                start_line: Any = 1, end_line: Any = None, depth: Any = 3,
                                limit: Any = 20, ref: str = "", regex: Any = None,
                                case_sensitive: Any = None, context: Any = 0,
                                files_only: Any = None) -> dict[str, Any]:
            proj, err = _cs_project()
            if err:
                return err
            if proj.index_status not in ("indexing", "ready"):
                bad = _cs_require_ready(proj)
                if bad:
                    return bad
            uid, pid, scope = str(proj.user_id), str(proj.id), proj.scope or {}
            act = (action or "").strip().lower()
            try:
                if act == "list":
                    d = int(depth) if depth not in (None, "") else 3
                    return graph_service.list_files(uid, pid, scope, path=path or "", max_depth=d)
                if act == "read":
                    lo = int(start_line) if start_line not in (None, "") else 1
                    hi = int(end_line) if end_line not in (None, "") else None
                    if not (path or "").strip():
                        return {"error": "provide 'path'"}
                    return graph_service.read_file(uid, pid, scope, path.strip(), start_line=lo, end_line=hi)
                if act == "search":
                    def _truthy(v: Any) -> bool:
                        if v is True:
                            return True
                        return isinstance(v, str) and v.strip().lower() in ("true", "1", "yes", "sim", "on")
                    try:
                        ctx = int(context) if context not in (None, "") else 0
                    except (TypeError, ValueError):
                        ctx = 0
                    return graph_service.search_files(
                        uid, pid, scope, query or "", glob=glob or "",
                        regex=_truthy(regex), case_sensitive=_truthy(case_sensitive),
                        context=ctx, files_only=_truthy(files_only),
                    )
                if act == "log":
                    lim = int(limit) if limit not in (None, "") else 20
                    return graph_service.git_log(uid, pid, limit=lim)
                if act == "diff":
                    return graph_service.git_diff(uid, pid, scope, path=path or "", ref=ref or "")
                return {"error": f"unknown action '{act}' (use list/read/search/log/diff)"}
            except ValueError as exc:
                return {"error": str(exc)}
            except Exception as exc:  # noqa: BLE001 - erro de arquivo não quebra o turno
                return {"error": str(exc)[:300]}

    if want("code.files.write"):
        @sift.tool(
            "code.files.write",
            description=(
                "Write/edit/delete files in the project attached to this chat, and push commits to "
                "the remote. Every write/edit/delete/patch AUTO-COMMITS locally (reversible, no extra "
                "step needed) — provide `message` describing WHY, it becomes the commit message. "
                "`action`: 'patch' (PREFERRED for changing existing files — apply a unified `git diff` "
                "in `diff`, covering several hunks/files at once; more reliable and token-cheap than "
                "repeated edits), 'write' (full file content — creates the file/folders if needed), "
                "'edit' (single SEARCH/REPLACE: `search` must match EXACTLY and be UNIQUE in the file — "
                "copy it verbatim from a prior 'read'; if unsure, use 'write' with the full new content), "
                "'delete' (removes a file — asks for confirmation unless confirm=true), 'push' (sends "
                "local commits to the remote — asks for confirmation unless confirm=true). Read the file "
                "with code.files.browse BEFORE editing/patching it, and build the diff from its CURRENT content."
            ),
            params={
                "action": "string:r::patch | write | edit | delete | push",
                "path": "string:o::write/edit/delete: path relative to the project root",
                "content": "string:o::write: the FULL new content of the file",
                "diff": "string:o::patch: a unified `git diff` (with '--- a/<path>' and '+++ b/<path>' headers) to apply — can span multiple files/hunks",
                "search": "string:o::edit: exact text to find (must be unique in the file)",
                "replace": "string:o::edit: text to replace it with",
                "message": "string:o::commit message describing WHY (optional, a generic one is used otherwise)",
                "confirm": "boolean:o::set true only after the user confirmed delete/push",
            },
            returns=["ok", "path", "created", "commit", "branch", "error",
                     # símbolos que a escrita mudou (added/removed/signature_changed,
                     # do reindex incremental) — o modelo checa `impact` na hora
                     "symbol_changes",
                     "kind", "question", "options", "allow_custom", "custom_label"],
            risk=True,
            examples=["create a new file utils/helpers.py with this content", "fix the typo in README.md",
                      "delete the old config.json", "push my changes"],
        )
        def _code_files_write(action: str = "", path: str = "", content: str = "", search: str = "",
                               replace: str = "", message: str = "", confirm: Any = None,
                               diff: str = "") -> dict[str, Any]:
            proj, confirm_on, err = _cs_project_ctx()
            if err:
                return err
            # Diferente de code.files.browse: aqui NÃO se abre exceção para
            # "indexing" — write/edit/delete disparam _reindex_after_write, que
            # chama cg.index() na MESMA instância CodeGraph que a indexação de
            # fundo já está usando (graph_service._get_graph é cacheada por
            # projeto); rodar os dois ao mesmo tempo, em threads diferentes,
            # arrisca "database is locked"/corromper o graph.db. Só libera
            # escrita com o índice inicial já concluído (ready).
            bad = _cs_require_ready(proj)
            if bad:
                return bad
            uid, pid, scope = str(proj.user_id), str(proj.id), proj.scope or {}
            act = (action or "").strip().lower()
            if not (path or "").strip() and act in ("write", "edit", "delete"):
                return {"error": "provide 'path'"}
            if act == "patch" and not (diff or "").strip():
                return {"error": "provide 'diff' (a unified git diff)"}
            # Tarefa isolada ativa? Então escreve no WORKTREE (branch própria, sem
            # reindex do grafo do projeto, sem push — o push acontece no merge).
            wt = toolctx.current_codespace_worktree.get()
            wt_root = graph_service.wt_dir(uid, pid, wt) if wt else None
            reindex = wt_root is None
            try:
                if act == "patch":
                    return graph_service.apply_patch(uid, pid, scope, diff, message=message,
                                                     root=wt_root, reindex=reindex)
                if act == "write":
                    return graph_service.write_file(uid, pid, scope, path.strip(), content, message=message,
                                                    root=wt_root, reindex=reindex)
                if act == "edit":
                    return graph_service.edit_file(uid, pid, scope, path.strip(), search, replace, message=message,
                                                   root=wt_root, reindex=reindex)
                if act == "delete":
                    block = _cs_confirm_guard(confirm_on, f"Apagar '{path.strip()}' do projeto '{proj.name}'?", confirm)
                    if block:
                        return block
                    return graph_service.delete_file(uid, pid, scope, path.strip(), message=message,
                                                     root=wt_root, reindex=reindex)
                if act == "push":
                    if wt_root is not None:
                        return {"error": "você está numa tarefa isolada (worktree) — os commits vão para o "
                                         "remoto quando a tarefa for mesclada/aprovada, não com push direto"}
                    block = _cs_confirm_guard(confirm_on, f"Enviar os commits locais de '{proj.name}' para o repositório remoto?", confirm)
                    if block:
                        return block
                    return asyncio.run(graph_service.push(uid, pid))
                return {"error": f"unknown action '{act}' (use patch/write/edit/delete/push)"}
            except ValueError as exc:
                return {"error": str(exc)}
            except Exception as exc:  # noqa: BLE001 - erro de escrita não quebra o turno
                return {"error": str(exc)[:300]}

    if want("code.exec.run"):
        from ..codespace import exec_service

        @sift.tool(
            "code.exec.run",
            description=(
                "Run shell commands in the attached project's sandbox — the way to VERIFY your "
                "changes (run tests/build/lint) instead of guessing. Runs with the project root (or "
                "the active task's worktree) as working directory; returns combined stdout+stderr, "
                "the exit code and duration. Leave `command` empty to run the project's configured "
                "test_command. Set `setup=true` to run the project's setup_command first (e.g. install "
                "deps) — do this once before the first test run. "
                "TOOLCHAINS: the sandbox has git/curl/build tools and `mise` (rootless version manager) "
                "— install what a project needs on demand and it PERSISTS across runs, e.g. "
                "`mise use -g java@17 maven` then `mvn -q test`, or `mise use -g node@20` then `npm test`. "
                "Prefer asking the user before installing/downloading (those commands prompt for "
                "confirmation automatically; tests/build run directly). "
                "LONG COMMANDS (dependency downloads, installs, big builds) run in the BACKGROUND so "
                "they never hit the timeout: you get a `job_id` back immediately. Then either wait for it "
                "with code.exec.jobs (action=wait) if you want the result now, OR just end your reply — "
                "you'll be woken up in a new turn when it finishes. Long commands go to background "
                "automatically; force it with background=true or disable with background=false. "
                "Requires the project owner to have enabled execution; a bad exit code means the command "
                "failed — read the output and fix."
            ),
            params={
                "command": "string:o::the shell command to run (empty = the project's test_command)",
                "setup": "boolean:o::run the project's setup_command (install deps) before the command",
                "background": "string:o::auto | true | false — run detached (long downloads/builds). auto = decide by the command",
                "confirm": "boolean:o::set true only after the user confirmed running commands",
            },
            returns=["ok", "exit_code", "output", "seconds", "truncated", "timed_out",
                     "command", "steps", "error", "job_id", "note", "status", "running",
                     "kind", "question", "options", "allow_custom", "custom_label"],
            risk=True,
            examples=["run the tests", "run npm run build and show me errors",
                      "install the dependencies then run the tests", "run pytest -k auth"],
        )
        def _code_exec_run(command: str = "", setup: Any = None, background: Any = None,
                           confirm: Any = None) -> dict[str, Any]:
            proj, confirm_on, err = _cs_project_ctx()
            if err:
                return err
            bad = _cs_require_ready(proj)
            if bad:
                return bad
            if not getattr(proj, "exec_enabled", False):
                return {"error": "execução desativada neste projeto. O dono precisa ligar "
                                 "'Permitir execução' nas Configurações do projeto (Codespace)."}
            uid, pid = str(proj.user_id), str(proj.id)
            wt = toolctx.current_codespace_worktree.get()
            root = graph_service.wt_dir(uid, pid, wt) if wt else graph_service.working_copy_path(uid, pid)
            # dir de dados PERSISTENTE do projeto (sobrevive a restart, fora do git) exposto
            # como $WORKSPACE_DATA — dá continuidade de ambiente entre turnos (DB/caches).
            data_env = {"WORKSPACE_DATA": str(graph_service.project_data_dir(uid, pid))}
            cmd = (command or "").strip() or (proj.test_command or "").strip()
            if not cmd:
                return {"error": "informe 'command' — nenhum test_command configurado no projeto"}
            # root/serviço de sistema (sudo/apt/docker/…) não roda no sandbox → erro claro
            # + caminho certo (mise), em vez de deixar o shell morrer com 'sudo: not found'.
            priv = _needs_root_exec(cmd)
            if priv:
                return {"error": priv}
            # O SANDBOX é o isolamento: roda sem privilégio, com escopo no volume do projeto
            # e morto no timeout. Por isso NÃO nos intrometemos com um card de texto por
            # padrão — só confirmamos (qualquer comando, com aviso extra p/ install/baixa/
            # apaga) quando o toggle global (Configurações → Segurança) está LIGADO. Off =
            # executa direto ("se a IA faz isso, é porque o usuário pediu e sabe dos riscos").
            if confirm_on:
                risky = _is_risky_exec(cmd)
                summary = (f"Instalar/baixar e rodar no projeto '{proj.name}'?" if risky
                           else f"Rodar no projeto '{proj.name}':") + f"\n`{cmd[:200]}`"
                block = _cs_confirm_guard(True, summary, confirm)
                if block:
                    return block

            def _truthy(v: Any) -> bool:
                return v is True or (isinstance(v, str) and v.strip().lower() in ("true", "1", "yes", "sim", "on"))

            def _falsy(v: Any) -> bool:
                return v is False or (isinstance(v, str) and v.strip().lower() in ("false", "0", "no", "nao", "não", "off"))

            # BACKGROUND: comandos longos (download/instala/build) rodam desacoplados p/ não
            # estourar o timeout — o agente espera (code.exec.jobs wait) ou é acordado no fim.
            # Nunca em turno autônomo (canal/automação: não há usuário/UI pra acordar).
            go_bg = (not toolctx.background.get()) and not _falsy(background) and (
                _truthy(background) or _is_long_runner(cmd)
            )
            if go_bg:
                from ..codespace import exec_jobs
                full = cmd
                if _truthy(setup) and (proj.setup_command or "").strip():
                    full = f"{proj.setup_command.strip()} && {cmd}"
                return exec_jobs.start_job(
                    root, full, chat_id=toolctx.current_chat_id.get(),
                    user_id=uid, project_id=pid, worktree=wt, env_extra=data_env,
                )

            try:
                steps: list[dict] = []
                if _truthy(setup) and (proj.setup_command or "").strip():
                    r0 = exec_service.run_command(root, proj.setup_command.strip(),
                                                  data_root=graph_service.data_root(),
                                                  env_extra=data_env)
                    r0["command"] = proj.setup_command.strip()
                    steps.append(r0)
                    if r0.get("error") or r0.get("exit_code") not in (0, None):
                        return {"ok": False, "steps": steps}
                r = exec_service.run_command(root, cmd, data_root=graph_service.data_root(),
                                             env_extra=data_env)
                if r.get("error") and not steps:
                    return r
                r["command"] = cmd
                r["ok"] = r.get("exit_code") == 0
                # loop de verificação: rodando o test_command DENTRO de um worktree,
                # grava pass/fail na tarefa (aparece na aba Tarefas p/ o humano).
                is_test = not (command or "").strip() or cmd == (proj.test_command or "").strip()
                if wt and is_test:
                    from ..codespace import worktree_service
                    try:
                        asyncio.run(worktree_service.set_test_status(wt, "pass" if r["ok"] else "fail"))
                    except Exception:  # noqa: BLE001
                        pass
                return {"ok": r["ok"], "steps": steps + [r]} if steps else r
            except Exception as exc:  # noqa: BLE001
                return {"error": str(exc)[:300]}

    if want("code.exec.jobs"):
        from ..codespace import exec_jobs

        @sift.tool(
            "code.exec.jobs",
            description=(
                "Manage BACKGROUND commands started by code.exec.run (long downloads/installs/builds). "
                "`action`: 'wait' (block until a job finishes and return its output — pass job_id; use "
                "this when you need the result right now, it can take minutes), 'status' (current status "
                "+ output tail of one job_id), 'list' (background jobs in this project). If a job is still "
                "running you can simply end your reply — you'll be woken up in a new turn when it finishes."
            ),
            params={
                "action": "string:r::wait | status | list",
                "job_id": "string:o::the job id returned by code.exec.run (required for wait/status)",
                "timeout": "number:o::wait: max seconds to block (capped by the server)",
            },
            returns=["job_id", "command", "status", "exit_code", "seconds", "output",
                     "truncated", "running", "jobs", "note", "error"],
            examples=["wait for the dependency install to finish", "check the build job status",
                      "list the background jobs"],
        )
        def _code_exec_jobs(action: str = "", job_id: str = "", timeout: Any = None) -> dict[str, Any]:
            proj, _confirm_on, err = _cs_project_ctx()
            if err:
                return err
            act = (action or "").strip().lower()
            if act == "list":
                return {"jobs": exec_jobs.list_jobs(project_id=str(proj.id))}
            jid = (job_id or "").strip()
            if not jid:
                return {"error": "informe job_id"}
            if act == "status":
                return exec_jobs.job_status(jid)
            if act == "wait":
                try:
                    to = float(timeout) if timeout is not None else None
                except (TypeError, ValueError):
                    to = None
                return asyncio.run(exec_jobs.wait_job(jid, to))
            return {"error": f"ação desconhecida '{action}' (use wait/status/list)"}

    if want("code.preview.serve"):
        from ..codespace import preview_service

        @sift.tool(
            "code.preview.serve",
            description=(
                "Put the project's app ON THE AIR: start a long-running dev server / backend and keep it "
                "up across turns so the USER can open it and actually test the running app (with backend), "
                "not just see files. Use this when asked to 'run it', 'serve it', 'put it up so I can test'. "
                "`action`: 'start' (launch the server — give the `command` that starts it, e.g. 'npm run "
                "dev' / 'python app.py' / 'gradlew bootRun', and the `port` it listens on), 'status' "
                "(is it up yet? + recent logs), 'wait' (BLOCK until the server is up or crashes — ONE call "
                "instead of polling status in a loop; heavy backends take minutes, just call wait again if "
                "it returns still-starting), 'logs' (recent output, to "
                "debug a crash), 'request' (send an HTTP request to your OWN running preview and get the "
                "raw status/headers/body back — use it to TEST/probe the running app end-to-end, e.g. hit "
                "an API route; it runs from the server so it reaches the app in any environment, and it's "
                "ANONYMOUS by default — no auth is added, so it's also how you verify what an unauthenticated "
                "attacker can reach; it does NOT follow redirects), 'stop', 'list'. `expose`: 'localhost' (default, safe — only this machine / "
                "the embedded preview panel) or 'lan' (reachable on the local network, e.g. to test on a "
                "phone) — set 'lan' ONLY when the user asks to expose it on the network, and for frameworks "
                "that need it also pass the host flag in the command (e.g. `vite --host 0.0.0.0`). The dev "
                "server runs without the exec timeout (it's meant to stay up). Needs the same 'Permitir "
                "execução' that code.exec.run needs. Prefer background installs (code.exec.run) BEFORE "
                "starting the server."
            ),
            params={
                "action": "string:r::start | status | wait | logs | request | stop | list",
                "command": "string:o::start: the command that starts the server (e.g. 'npm run dev')",
                "port": "number:o::start: the port the app listens on — MUST be in the published preview range (4001-4010); out-of-range is auto-remapped into it. The app must listen on 0.0.0.0:<port>.",
                "expose": "string:o:localhost:start: informational only now — the preview always runs on its own published port, reachable on the host",
                "preview_id": "string:o::status/logs/request/stop: the id (or port) returned by 'start'/'list'",
                "method": "string:o:GET:request: HTTP method (GET/POST/PUT/DELETE…)",
                "path": "string:o:/:request: path to hit on the app (e.g. '/api/session/properties')",
                "headers": "string:o::request: request headers as a JSON object (e.g. {\"X-Forwarded-For\":\"127.0.0.1\"})",
                "body": "string:o::request: request body (raw string / JSON)",
                "timeout": "number:o::wait: max seconds to block waiting for the server to come up (capped at 240)",
                "confirm": "boolean:o::set true only after the user confirmed starting/exposing the server",
            },
            returns=["id", "command", "port", "expose", "status", "preview_url",
                     "age_seconds", "exit_code", "logs", "previews", "note", "ok", "stopped", "error",
                     "http_status", "resp_headers", "body", "truncated", "url",
                     "kind", "question", "options", "allow_custom", "custom_label"],
            risk=True,
            examples=["run this app so I can test it", "put the site up on localhost",
                      "expose it on the LAN so I can open it on my phone", "is the server up yet?",
                      "show the server logs", "stop the preview"],
        )
        def _code_preview_serve(action: str = "", command: str = "", port: Any = None,
                                expose: str = "localhost", preview_id: str = "",
                                method: str = "GET", path: str = "/", headers: str = "",
                                body: str = "", timeout: Any = None,
                                confirm: Any = None) -> dict[str, Any]:
            proj, confirm_on, err = _cs_project_ctx()
            if err:
                return err
            uid, pid = str(proj.user_id), str(proj.id)
            act = (action or "").strip().lower()
            if act == "list":
                return preview_service.list_previews(uid, project_id=pid)
            pvid = (preview_id or "").strip()
            if act == "status":
                if not pvid:
                    return {"error": "informe preview_id"}
                return preview_service.preview_status(uid, pvid)
            if act == "logs":
                if not pvid:
                    return {"error": "informe preview_id"}
                return preview_service.preview_status(uid, pvid, with_logs=True, tail=120)
            if act == "wait":
                if not pvid:
                    return {"error": "informe preview_id (ou a porta) do preview a aguardar"}
                # bloqueia até ficar 'up'/cair/timeout — UM call em vez de dezenas de status
                try:
                    to = float(timeout) if timeout not in (None, "") else 120
                except (TypeError, ValueError):
                    to = 120
                return preview_service.wait_ready(uid, pid, pvid, timeout=to)
            if act == "request":
                if not pvid:
                    return {"error": "informe preview_id (ou a porta) do preview a testar"}
                hdrs = None
                if (headers or "").strip():
                    try:
                        hdrs = json.loads(headers)
                        if not isinstance(hdrs, dict):
                            return {"error": "`headers` deve ser um objeto JSON"}
                    except json.JSONDecodeError:
                        return {"error": "`headers` não é um JSON válido"}
                return preview_service.request_preview(
                    uid, pid, pvid, method=method or "GET", path=path or "/",
                    headers=hdrs, body=body or None,
                )
            if act == "stop":
                if not pvid:
                    return {"error": "informe preview_id"}
                return preview_service.stop_preview(uid, pvid)
            if act == "start":
                bad = _cs_require_ready(proj)
                if bad:
                    return bad
                if not getattr(proj, "exec_enabled", False):
                    return {"error": "execução desativada neste projeto. O dono precisa ligar "
                                     "'Permitir execução' nas Configurações do projeto (Codespace)."}
                if toolctx.background.get():
                    return {"error": "preview só em conversa interativa (não em canal/automação)"}
                cmd = (command or "").strip()
                if not cmd:
                    return {"error": "informe 'command' (o que sobe o servidor, ex.: 'npm run dev')"}
                # subir um servidor exposto na LAN é a ação sensível: confirma quando LAN
                # (fica alcançável na rede) OU sob o toggle global de confirmação.
                exp = "lan" if str(expose).lower() in ("lan", "0.0.0.0", "network") else "localhost"
                if exp == "lan" or confirm_on:
                    where = "exposto na REDE LOCAL" if exp == "lan" else "em localhost"
                    block = _cs_confirm_guard(True, f"Subir o servidor do projeto '{proj.name}' {where}?\n`{cmd[:200]}`", confirm)
                    if block:
                        return block
                wt = toolctx.current_codespace_worktree.get()
                root = graph_service.wt_dir(uid, pid, wt) if wt else graph_service.working_copy_path(uid, pid)
                return preview_service.start_preview(uid, pid, root, cmd, port, expose=exp)
            return {"error": f"ação desconhecida '{action}' (use start/status/wait/logs/request/stop/list)"}

    if want("code.task.manage"):
        from ..codespace import worktree_service

        @sift.tool(
            "code.task.manage",
            description=(
                "Work in an ISOLATED worktree (its own branch) so a big or parallel change never "
                "clobbers the project's main tree. `action`: 'open' (start a task — creates a worktree/"
                "branch; AFTER this, your file writes in this chat go into the worktree, not the main "
                "tree, until it's merged/discarded), 'list' (tasks and their status/diff size), 'diff' "
                "(the full diff of a task vs its base — pass `task_id`), 'merge' (merge the task into the "
                "project branch — usually the HUMAN approves this in the UI; set push=true to also push), "
                "'discard' (throw the worktree away), 'synthesize' (integration plan for ALL open tasks: "
                "which files each touches and which are touched by more than one task — the conflict risks "
                "— so you can propose a merge ORDER and resolve overlaps before merging). Use 'open' before "
                "delegating parallel coding work, and 'synthesize' after the parallel workers finish."
            ),
            params={
                "action": "string:r::open | list | diff | merge | discard | synthesize",
                "title": "string:o::open: a short description of the task",
                "task_id": "string:o::diff/merge/discard: the task id from 'open'/'list'",
                "agent": "string:o::open: label of the agent doing the work (optional)",
                "base": "string:o::open: branch to start from (default: the project branch)",
                "push": "boolean:o::merge: also push the project branch to the remote after merging",
                "confirm": "boolean:o::set true only after the user confirmed merge/discard",
            },
            returns=["id", "title", "agent", "branch", "base_branch", "status", "diff_stat",
                     "test_status", "created_at", "tasks", "diff", "ok", "merged", "discarded",
                     "already", "conflict", "push", "error",
                     "overlaps", "n_awaiting", "note",  # synthesize
                     "kind", "question", "options", "allow_custom", "custom_label"],
            risk=True,
            examples=["open an isolated task to refactor the auth module", "list the open tasks",
                      "show me the diff of that task", "merge the task", "discard this task",
                      "how should I integrate the parallel tasks?"],
        )
        def _code_task_manage(action: str = "", title: str = "", task_id: str = "",
                               agent: str = "", base: str = "", push: Any = None,
                               confirm: Any = None) -> dict[str, Any]:
            proj, confirm_on, err = _cs_project_ctx()
            if err:
                return err
            uid, pid = str(proj.user_id), str(proj.id)
            act = (action or "").strip().lower()
            chat_id = toolctx.current_chat_id.get()

            def _truthy(v: Any) -> bool:
                return v is True or (isinstance(v, str) and v.strip().lower() in ("true", "1", "yes", "sim", "on"))

            tid = (task_id or "").strip()
            if act == "open":
                bad = _cs_require_ready(proj)
                if bad:
                    return bad
                return asyncio.run(worktree_service.open_task(uid, pid, title=title, agent=agent,
                                                              chat_id=chat_id, base=base))
            if act == "list":
                return asyncio.run(worktree_service.list_tasks(uid, pid))
            if act in ("synthesize", "synthesise", "integrate"):
                return asyncio.run(worktree_service.synthesize_merge(uid, pid))
            if act == "diff":
                if not tid:
                    return {"error": "informe task_id"}
                return asyncio.run(worktree_service.task_diff(uid, tid))
            if act == "merge":
                if not tid:
                    return {"error": "informe task_id"}
                block = _cs_confirm_guard(confirm_on, f"Mesclar a tarefa no projeto '{proj.name}'?", confirm)
                if block:
                    return block
                return asyncio.run(worktree_service.merge_task(uid, tid, push=_truthy(push)))
            if act == "discard":
                if not tid:
                    return {"error": "informe task_id"}
                block = _cs_confirm_guard(confirm_on, f"Descartar a tarefa (perde as mudanças não mescladas)?", confirm)
                if block:
                    return block
                return asyncio.run(worktree_service.discard_task(uid, tid))
            return {"error": f"unknown action '{act}' (use open/list/diff/merge/discard/synthesize)"}

    if want("code.flow.analyze"):
        @sift.tool(
            "code.flow.analyze",
            description=(
                "Data-flow and taint analysis on the project attached to this chat — answers where "
                "values GO, which the call graph alone cannot. `action`: 'data_flow' (where do this "
                "function's parameters end up — returned? passed to which callees?), 'reaches' (does "
                "`symbol` reach a `sink` — 'http', 'db', 'fs', … — returning the call chains that get "
                "there), 'taint' (scan for untrusted input reaching dangerous sinks; without `entry` "
                "it scans the project, with `entry` it starts from one function). This is STATIC "
                "may-analysis: it over-approximates on purpose, so results are candidates to verify "
                "by reading the code, never proof of a vulnerability. Always report the `warnings` "
                "and the per-result `confidence` to the user instead of stating findings as fact."
            ),
            params={
                "action": "string:r::data_flow | reaches | taint",
                "symbol": "string:o::data_flow/reaches: fully-qualified name (fqn) from code.graph.query 'find'",
                "sink": "string:o:http:reaches: the sink to test against (e.g. 'http', 'db', 'fs')",
                "via": "string:o::reaches: only count paths passing through this symbol",
                "entry": "string:o::taint: start from this function instead of scanning everything",
                "scope": "string:o::taint: restrict the scan to a subtree (e.g. 'src/api')",
                "depth": "number:o::how many hops to follow (data_flow 1-5, reaches 1-12, taint 1-8)",
            },
            returns=["function", "supported", "params", "target", "sink", "via", "paths",
                     "mode", "findings", "scanned", "total_found", "warnings", "error"],
            examples=["where does the `user` parameter of handle_request end up?",
                      "does login() reach the network?",
                      "is there untrusted input reaching a database call?"],
        )
        def _code_flow_analyze(action: str = "", symbol: str = "", sink: str = "http",
                                via: str = "", entry: str = "", scope: str = "",
                                depth: Any = None) -> dict[str, Any]:
            proj, err = _cs_project()
            if err:
                return err
            bad = _cs_require_ready(proj)
            if bad:
                return bad
            uid, pid = str(proj.user_id), str(proj.id)
            act = (action or "").strip().lower()

            def _int(v: Any, default: int) -> int:
                try:
                    return int(v) if v not in (None, "") else default
                except (TypeError, ValueError):
                    return default

            try:
                if act in ("data_flow", "dataflow", "flow"):
                    if not (symbol or "").strip():
                        return {"error": "provide 'symbol' (fqn of the function to trace)"}
                    return graph_service.data_flow(uid, pid, symbol.strip(), depth=_int(depth, 2))
                if act == "reaches":
                    if not (symbol or "").strip():
                        return {"error": "provide 'symbol' (fqn of the starting point)"}
                    return graph_service.reaches(
                        uid, pid, symbol.strip(), sink=sink or "http",
                        via=via or "", depth=_int(depth, 8),
                    )
                if act == "taint":
                    return graph_service.taint(
                        uid, pid, scope=scope or "", entry=entry or "", depth=_int(depth, 4)
                    )
                return {"error": f"unknown action '{act}' (use data_flow/reaches/taint)"}
            except ValueError as exc:
                return {"error": str(exc)}
            except Exception as exc:  # noqa: BLE001 - erro da análise não quebra o turno
                return {"error": str(exc)[:300]}

    if want("diagram.excalidraw.render"):
        @sift.tool(
            "diagram.excalidraw.render",
            description=(
                "Draw an editable diagram (Excalidraw canvas) from a Mermaid flowchart — "
                "flowcharts, architectures, mind maps. Shown to the user; don't redraw it "
                "as text. Optionally write [[diagram]] where it should appear."
            ),
            params={
                "mermaid": "string:o::Mermaid flowchart, e.g. 'flowchart TD\\n A[Start]-->B{Ok?}\\n B-->|Yes| C[End]'",
                "title": "string:o::diagram title (optional)",
            },
            # `kind`+`mermaid` precisam sobreviver ao filtro do SIFT: o front lê
            # esse resultado (tool_events) p/ renderizar o canvas.
            returns=["ok", "kind", "title", "mermaid", "error"],
        )
        def _excalidraw(mermaid: str = "", title: str = "") -> dict[str, Any]:
            src = (mermaid or "").strip()
            # remove cercas de código ```mermaid ... ``` se o modelo as enviar
            if src.startswith("```"):
                src = src.split("\n", 1)[1] if "\n" in src else ""
                if src.rstrip().endswith("```"):
                    src = src.rstrip()[:-3]
                src = src.strip()
            if not src:
                return {"error": "provide `mermaid` (e.g. 'flowchart TD\\n A-->B')"}
            # retorno compacto: só o necessário p/ o front renderizar o canvas
            # (NUNCA o HTML/SVG — economia de tokens, princípio do SIFT)
            return {
                "ok": True,
                "kind": "excalidraw",
                "title": (title or "Diagrama").strip()[:120],
                "mermaid": src[:6000],
            }

    if want("chart.render.plot"):
        @sift.tool(
            "chart.render.plot",
            description=(
                "Render a chart (line, bar, area, pie) to visualize numbers, comparisons "
                "or trends. Use `labels`+`values` for one series or `series` for many. "
                "Optionally write [[chart]] where it should appear."
            ),
            params={
                "type": "string:o:line:chart type: line, bar, area or pie",
                "title": "string:o::chart title",
                "labels": "array:o::x-axis labels, e.g. ['Jan','Feb','Mar']",
                "values": "array:o::numeric values aligned to labels, e.g. [10, 20, 15]",
                "series": "array:o::multiple series: [{name, data:[...]}, ...]",
            },
            returns=["kind", "type", "title", "labels", "series", "error"],
        )
        def _chart(type: str = "line", title: str = "", labels: Any = None,
                   values: Any = None, series: Any = None) -> dict[str, Any]:
            def _num(v: Any) -> float:
                try:
                    return round(float(v), 6)
                except (TypeError, ValueError):
                    return 0.0
            t = (type or "line").lower()
            if t not in ("line", "bar", "area", "pie"):
                t = "line"
            labs = [str(x) for x in (labels or [])]
            norm: list[dict[str, Any]] = []
            if isinstance(series, list) and series:
                for s in series:
                    if isinstance(s, dict) and isinstance(s.get("data"), list):
                        norm.append({"name": str(s.get("name") or ""), "data": [_num(v) for v in s["data"]]})
            elif isinstance(values, list) and values:
                norm.append({"name": str(title or "série"), "data": [_num(v) for v in values]})
            if not norm or not any(s["data"] for s in norm):
                return {"error": "provide `values` (numbers) or `series`, plus optional `labels`"}
            return {"kind": "chart", "type": t, "title": (title or "")[:120], "labels": labs, "series": norm}

    if want("finance.quote.get"):
        # config default + injeta a busca web p/ o fallback (usa a mesma search_cfg)
        fin_cfg = finance_cfg or finance.FinanceConfig()

        async def _fin_ws(q: str) -> list[dict]:
            return await web_search(q, search_cfg)

        fin_cfg.web_search = _fin_ws

        @sift.tool(
            "finance.quote.get",
            description=(
                "Get a real stock/index/ETF/crypto quote (e.g. AAPL, NESN.SW, PETR4.SA, "
                "BTC-USD). By DEFAULT return only the numbers and present them as a "
                "text/table. ONLY when the user asks for a chart/card/visual (e.g. 'com "
                "gráfico', 'mostra o card', 'with a chart'), pass chart=true to render a "
                "visual card with a mini price chart, and write [[stock]] where it should "
                "appear. Pass chart=false to force text."
            ),
            params={
                "symbol": "string:o::ticker symbol, e.g. AAPL, NESN.SW, PETR4.SA",
                "range": "string:o:1d:period: 1d, 5d, 1mo, 6mo, ytd, 1y, 5y, max",
                "chart": "boolean:o::true = show the visual card + mini chart (only if the user asks for a chart/card); omit or false = numbers only",
            },
            returns=[
                "kind", "ok", "symbol", "name", "exchange", "currency", "price",
                "prev_close", "change", "change_pct", "series", "stats", "source",
                "range", "web_results", "error",
            ],
        )
        def _finance_quote(symbol: str = "", range: str = "1d", chart: Any = None) -> dict[str, Any]:
            try:
                q = asyncio.run(finance.fetch_quote(symbol, fin_cfg, range or "1d"))
            except Exception as exc:  # noqa: BLE001
                return {"error": str(exc)}
            if q.get("error"):
                return q
            q["ok"] = True
            # `chart` não especificado => usa o modo configurado no modelo
            # (fin_cfg.default_card, padrão False = texto). Explícito vence.
            if chart is None or (isinstance(chart, str) and not chart.strip()):
                want_chart = bool(fin_cfg.default_card)
            else:
                want_chart = not (chart is False or str(chart).strip().lower() in ("false", "0", "no", "off", "nao", "não"))
            if want_chart:
                q["kind"] = "stock_card"  # o front desenha o card com mini-gráfico
            else:
                q.pop("series", None)      # só os números (o modelo formata em texto)
            return q

    if want("research.deep.run"):
        d_cfg = deep_cfg or deep_search.DeepSearchConfig()

        async def _deep_ws(qq: str) -> list[dict]:
            return await web_search(qq, search_cfg)

        d_cfg.web_search = _deep_ws

        @sift.tool(
            "research.deep.run",
            description=(
                "Deep, multi-step web research: plans sub-questions, searches, reads pages, "
                "returns a synthesized brief WITH sources. Slow and costly — use ONLY when the "
                "user EXPLICITLY asks for it ('pesquisa profunda', 'deep research', 'investigue "
                "a fundo'). Needing a web lookup is NOT enough: questions about a link/product/"
                "fact use page.read and web.search instead. Optionally write [[research]] where "
                "it should appear. Always follow it with your own textual answer to the user."
            ),
            params={"query": "string:o::the research topic or question"},
            returns=["kind", "query", "brief", "sources", "rounds", "error"],
        )
        def _deep(query: str = "") -> dict[str, Any]:
            try:
                return asyncio.run(deep_search.run(query, d_cfg))
            except Exception as exc:  # noqa: BLE001
                return {"error": str(exc)}

    if want("automation.monitor.create"):
        @sift.tool(
            "automation.monitor.create",
            description=(
                "Create a background monitor that alerts the user when something changes. "
                "Use ONLY when the user asks to be notified/alerted about a future event "
                "(e.g. 'tell me when…', 'alert me if the price hits…'). Pick watcher_type: "
                "'price' (needs symbol, op=above|below|pct, value), 'web_search' (needs query; "
                "optional condition), 'page' (needs url; optional contains), 'rss' (needs url; "
                "optional keywords). The monitor then runs on its own and notifies the user."
            ),
            params={
                "watcher_type": "string:o:price:one of: price, web_search, page, rss",
                "title": "string:o::short title for the monitor",
                "symbol": "string:o::ticker for price, e.g. PETR4.SA, AAPL, BTC-USD",
                "op": "string:o:above:price condition: above, below or pct",
                "value": "number:o::threshold (price) or percent (pct)",
                "query": "string:o::search query for web_search",
                "condition": "string:o::natural-language condition for web_search",
                "url": "string:o::page or feed URL for page/rss",
                "contains": "string:o::text to watch on the page (page)",
                "keywords": "string:o::comma-separated keywords (rss)",
                "interval_minutes": "number:o:5:how often to check, in minutes",
            },
            returns=["ok", "monitor_id", "title", "watcher_type", "error"],
        )
        def _monitor_create(
            watcher_type: str = "price", title: str = "", symbol: str = "",
            op: str = "above", value: Any = None, query: str = "", condition: str = "",
            url: str = "", contains: str = "", keywords: str = "", interval_minutes: Any = 5,
        ) -> dict[str, Any]:
            if not user_id:
                return {"error": "monitor creation unavailable (no user context)"}
            # import tardio: evita ciclo com o pacote automation em tempo de import
            from ..automation import creator
            try:
                return asyncio.run(creator.create_monitor(
                    user_id, title=title, watcher_type=watcher_type,
                    interval_minutes=interval_minutes, symbol=symbol, op=op, value=value,
                    query=query, condition=condition, url=url, contains=contains, keywords=keywords,
                ))
            except Exception as exc:  # noqa: BLE001
                return {"error": str(exc)}

    if want("automation.reminder.create"):
        @sift.tool(
            "automation.reminder.create",
            description=(
                "Schedule a ONE-SHOT in-app reminder for the user at a future time. Use when "
                "the user asks to be reminded/pinged later (e.g. 'remind me to sleep in 2 "
                "minutes', 'me lembra às 14:00'). Set `in_minutes` for a relative delay OR "
                "`at` for a clock time ('HH:MM' in the USER'S LOCAL timezone) — get the current "
                "local time from the temporal context. `message` is the exact text to send. "
                "This delivers IN-APP (chat + notification). If the user wants it on their "
                "AGENDA/calendar instead, use the Google Calendar tool. If the user did NOT say "
                "where to receive it, just OMIT `target` — it defaults to THIS chat; do NOT ask. "
                "Set target='new' only if they clearly want it in a fresh chat. Just create it and "
                "confirm briefly."
            ),
            params={
                "message": "string:o::the reminder text to deliver, e.g. 'lembre-se de dormir'",
                "in_minutes": "number:o::delay from now in minutes (use this for 'in X min/hours')",
                "at": "string:o::local clock time 'HH:MM' or 'YYYY-MM-DD HH:MM' (user's timezone); alternative to in_minutes",
                "target": "string:o::where to deliver: 'current' (this chat) or 'new' (fresh chat); OMIT to deliver in THIS chat (default)",
                "title": "string:o::short title for the reminder (optional)",
            },
            returns=["ok", "reminder_id", "title", "when", "error"],
        )
        def _reminder_create(
            message: str = "", in_minutes: Any = None, at: str = "",
            target: str = "", title: str = "",
        ) -> dict[str, Any]:
            if not user_id:
                return {"error": "reminder creation unavailable (no user context)"}
            if not (message or "").strip():
                return {"error": "reminder requires a `message`"}
            from ..automation import creator
            chat_id = toolctx.current_chat_id.get()
            # sem destino explícito → entrega AQUI (chat atual) por padrão; sem chat
            # atual (contexto sem conversa) cai num chat novo. Nada de perguntar: o
            # lembrete é in-app e barato, então a pergunta só gastava tokens e um
            # round-trip. Quem quiser a agenda usa a ferramenta do Google Calendar.
            tgt = (target or "").strip() or ("current" if chat_id else "new")
            try:
                return asyncio.run(creator.create_reminder(
                    user_id, message=message, in_minutes=in_minutes, at=at,
                    target=tgt, chat_id=chat_id, title=title,
                    tz=toolctx.user_tz.get(),
                ))
            except Exception as exc:  # noqa: BLE001
                return {"error": str(exc)}

    # ---------------------- Google Workspace (Gmail + Agenda) ------------------ #
    # Paths de 3 segmentos (exigência da SIFT); os nomes na UI são "Google Gmail" /
    # "Google Calendar". Cada tool reúne suas operações no parâmetro `action`.
    if want("google.gmail.mailbox") or want("google.calendar.events"):
        g_confirm = True if google_cfg is None else bool(google_cfg.require_confirm)
        g_max = (google_cfg.max_results if google_cfg else 10) or 10
        g_cal = (google_cfg.default_calendar if google_cfg else "primary") or "primary"
        g_accounts = list(google_cfg.accounts) if google_cfg else []  # [{"id","email"}]
        g_ops = google_cfg.ops if google_cfg else {}

        def _op_on(cap: str) -> bool:
            """Operação ligada na config da ferramenta (ausente = ligada)."""
            return g_ops.get(cap, True) is not False

        _ASK_KEYS = ["kind", "question", "options", "allow_custom", "custom_label"]
        _ACCOUNT_PARAM = "string:o::which connected Google account to use (email); omit if only one"

        def _g_truthy(v: Any) -> bool:
            if v is True:
                return True
            return isinstance(v, str) and v.strip().lower() in ("true", "1", "yes", "sim", "on")

        def _g_pick_account(account: str = "") -> tuple[dict | None, dict | None]:
            """Escolhe a conta (dict {id,email}) sem buscar token. Retorna (conta,
            bloqueio): bloqueio != None = não conectado / escolha de conta / erro."""
            if not g_accounts:
                return None, {"error": "Google não conectado. Conecte uma conta em Configurações → Integrações."}
            if (account or "").strip():
                a = account.strip().lower()
                chosen = next(
                    (x for x in g_accounts
                     if a == str(x.get("id", "")).lower() or a == (x.get("email", "") or "").lower()),
                    None,
                )
                if chosen is None:
                    emails = ", ".join(x.get("email", "") for x in g_accounts)
                    return None, {"error": f"conta '{account}' não liberada para este modelo. Disponíveis: {emails}"}
                return chosen, None
            if len(g_accounts) == 1:
                return g_accounts[0], None
            from .interaction import ask_options
            return None, ask_options(
                "Qual conta Google devo usar?",
                [{"label": x.get("email", ""), "value": f"Use a conta {x.get('email', '')}"} for x in g_accounts],
                allow_custom=False,
            )

        def _g_ctx(account: str = "") -> tuple[str | None, dict | None]:
            """Resolve a conta e devolve (access_token, bloqueio)."""
            chosen, block = _g_pick_account(account)
            if block is not None:
                return None, block
            from ..integrations import google_service
            tok = asyncio.run(google_service.get_access_token(str(chosen.get("id"))))
            if not tok:
                return None, {"error": f"não foi possível acessar a conta {chosen.get('email', '')} (reconecte em Integrações)."}
            return tok, None

        def _g_guard(summary: str, confirm: Any) -> dict | None:
            """Confirmação antes de escrever. Retorna ask, ou None p/ prosseguir.
            (O bloqueio por operação é feito no `_op_on` de cada ação.)

            NÃO migrar p/ o `on_risky` da SIFT 0.7: aquele hook é um GATE booleano
            por-TOOL (nega = PermissionError seco). Este guard é por-AÇÃO (só as
            escritas de uma tool consolidada) e INTERATIVO (devolve um ask_options
            que vira card no chat; o usuário confirma e o modelo refaz com
            confirm=true) — migrar perderia as duas coisas. Se um dia o on_risky
            aceitar devolver um payload (ask) em vez de bool, aí sim."""
            # em automações/canais (background) não há usuário p/ confirmar → executa direto
            if g_confirm and not _g_truthy(confirm) and not toolctx.background.get():
                from .interaction import ask_options
                return ask_options(
                    summary,
                    [
                        {"label": "Confirmar", "value": "Sim, confirmo — refaça a ação agora com confirm=true."},
                        {"label": "Cancelar", "value": "Cancele, não execute a ação."},
                    ],
                    allow_custom=False,
                )
            return None

        def _g_n(max_results: Any) -> int:
            return min(int(max_results or g_max), 50) if str(max_results or "").strip() else g_max

        def _g_search_n(max_results: Any) -> int:
            """Nº de e-mails da BUSCA do Gmail. Omitido → default do usuário; `0`
            (ou negativo) → TODOS que casam a query (o google_service aplica o teto
            de segurança). Diferente do `_g_n` do calendário, aqui 0 NÃO vira default."""
            s = str(max_results if max_results is not None else "").strip()
            if s == "":
                return g_max
            return max(0, int(float(s)))

        if want("google.gmail.mailbox"):
            @sift.tool(
                "google.gmail.mailbox",
                description=(
                    "The user's Gmail. `action`: 'search' (list messages; `query` uses Gmail "
                    "operators like from:/is:unread/newer_than:7d), 'read' (full body by `id`), "
                    "'send' (needs `to`,`subject`,`body`), 'archive'/'trash'/'mark_read'/"
                    "'mark_unread' (by `id`). For 'send', just provide the fields: the user is "
                    "shown an EDITABLE draft to review and send themselves — do NOT say you sent "
                    "it, and don't ask for confirmation in text (the draft IS the confirmation)."
                ),
                params={
                    "action": "string:n::search | read | send | archive | trash | mark_read | mark_unread",
                    "query": "string:o::search: Gmail query (operators allowed)",
                    "id": "string:o::read/archive/trash/mark: the message id",
                    "to": "string:o::send: recipient(s), comma-separated",
                    "subject": "string:o::send: subject",
                    "body": "string:o::send: plain-text body",
                    "cc": "string:o::send: optional CC",
                    "max_results": "number:o::search: max messages (metadata; capped). To just COUNT matches (e.g. 'how many unread?'), pass 0 — fast, returns `count` + a small sample. Omit for the default",
                    "confirm": "boolean:o::set true only after the user confirmed a write (archive/trash/mark)",
                    "account": _ACCOUNT_PARAM,
                },
                returns=["messages", "count", "note", "truncated", "from", "subject", "date", "snippet", "body", "id", "ok", "action", "error",
                         "kind", "draft_id", "to", "cc", "account", "account_email", *_ASK_KEYS],
                risk=True,
                examples=["read my last email", "any unread emails?", "send an email to bob", "archive this message"],
            )
            def _g_gmail(action: str = "", query: str = "", id: str = "", to: str = "",
                         subject: str = "", body: str = "", cc: str = "", max_results: Any = None,
                         confirm: Any = None, account: str = "") -> dict[str, Any]:
                act = (action or "").strip().lower()
                reads = {"search", "read"}
                writes = {"send", "archive", "trash", "mark_read", "mark_unread"}
                if act not in reads | writes:
                    return {"error": f"unknown action '{action}' (use search/read/send/archive/trash/mark_read/mark_unread)"}
                cap = ("gmail_send" if act == "send"
                       else "gmail_organize" if act in {"archive", "trash", "mark_read", "mark_unread"}
                       else "gmail_search")
                if not _op_on(cap):
                    return {"error": f"a operação '{act}' está desativada nas configurações desta ferramenta."}
                # ENVIO: com confirmação ligada e usuário presente (não é automação),
                # devolve um RASCUNHO editável — o composer no chat é a confirmação.
                # A IA não envia; o usuário revisa, edita e clica Enviar (rota direta).
                if act == "send":
                    if not (to or "").strip():
                        return {"error": "`to` is required for send"}
                    if g_confirm and not toolctx.background.get():
                        chosen, block = _g_pick_account(account)
                        if block is not None:
                            return block
                        import uuid as _uuid
                        return {
                            "kind": "email_draft",
                            "draft_id": str(_uuid.uuid4()),
                            "to": to, "cc": cc, "subject": subject, "body": body,
                            "account": str(chosen.get("id")),
                            "account_email": chosen.get("email", ""),
                        }
                    # confirmação desligada OU automação → envia direto (abaixo)
                elif act in writes:
                    blocked = _g_guard(f"{act} no e-mail selecionado?", confirm)
                    if blocked is not None:
                        return blocked
                tok, block = _g_ctx(account)
                if block is not None:
                    return block
                from ..integrations import google_service
                try:
                    if act == "search":
                        return google_service.gmail_search(tok, query, _g_search_n(max_results))
                    if act == "read":
                        if not (id or "").strip():
                            return {"error": "`id` is required for read"}
                        return google_service.gmail_get(tok, id)
                    if act == "send":
                        if not (to or "").strip():
                            return {"error": "`to` is required for send"}
                        return google_service.gmail_send(tok, to, subject, body, cc)
                    if not (id or "").strip():
                        return {"error": "`id` is required"}
                    return google_service.gmail_modify(
                        tok, id,
                        {"archive": "archive", "trash": "trash", "mark_read": "read", "mark_unread": "unread"}[act],
                    )
                except Exception as exc:  # noqa: BLE001
                    return {"error": str(exc)}

        if want("google.calendar.events"):
            @sift.tool(
                "google.calendar.events",
                description=(
                    "The user's Google Calendar. Use it to put reminders/events on the user's "
                    "agenda. `action`: 'list' (upcoming events; `time_min`/`time_max` RFC3339), "
                    "'search' (`query` text), 'create' (needs `summary`,`start`,`end`; RFC3339 "
                    "datetime or YYYY-MM-DD for all-day), 'update' (`event_id` + fields to change), "
                    "'delete' (`event_id`). Give `start`/`end` as the user's LOCAL wall-clock time "
                    "(from the temporal context) WITHOUT a UTC offset, e.g. '2026-07-07T14:30:00' — "
                    "the server tags it with the user's timezone. Writes ask for confirmation "
                    "unless confirm=true."
                ),
                params={
                    "action": "string:n::list | search | create | update | delete",
                    "query": "string:o::search: text",
                    "time_min": "string:o::list: RFC3339 lower bound (default now)",
                    "time_max": "string:o::list: RFC3339 upper bound",
                    "summary": "string:o::create/update: event title",
                    "start": "string:o::create/update: local RFC3339 datetime (no offset) or YYYY-MM-DD",
                    "end": "string:o::create/update: local RFC3339 datetime (no offset) or YYYY-MM-DD",
                    "description": "string:o::create/update: details",
                    "location": "string:o::create/update: location",
                    "attendees": "string:o::create: attendee emails, comma-separated",
                    "event_id": "string:o::update/delete: the event id",
                    "max_results": "number:o::list/search: max events (1-50)",
                    "calendar_id": "string:o::calendar id (default primary)",
                    "confirm": "boolean:o::set true only after the user confirmed a write",
                    "account": _ACCOUNT_PARAM,
                },
                returns=["events", "id", "title", "start", "end", "location", "ok", "deleted", "action", "error", *_ASK_KEYS],
                risk=True,
                examples=["what's on my calendar tomorrow", "schedule a meeting friday 3pm", "delete that event", "reschedule my dentist appointment"],
            )
            def _g_calendar(action: str = "", query: str = "", time_min: str = "", time_max: str = "",
                            summary: str = "", start: str = "", end: str = "", description: str = "",
                            location: str = "", attendees: str = "", event_id: str = "",
                            max_results: Any = None, calendar_id: str = "", confirm: Any = None,
                            account: str = "") -> dict[str, Any]:
                act = (action or "").strip().lower()
                reads = {"list", "search"}
                writes = {"create", "update", "delete"}
                if act not in reads | writes:
                    return {"error": f"unknown action '{action}' (use list/search/create/update/delete)"}
                cap = "cal_view" if act in reads else "cal_delete" if act == "delete" else "cal_create"
                if not _op_on(cap):
                    return {"error": f"a operação '{act}' está desativada nas configurações desta ferramenta."}
                if act in writes:
                    summary_txt = ("Excluir este evento da agenda?" if act == "delete"
                                   else f"Criar evento “{summary}” ({start} → {end})?" if act == "create"
                                   else "Salvar as alterações neste evento?")
                    blocked = _g_guard(summary_txt, confirm)
                    if blocked is not None:
                        return blocked
                tok, block = _g_ctx(account)
                if block is not None:
                    return block
                from ..integrations import google_service
                cal = calendar_id or g_cal
                tz = toolctx.user_tz.get()  # fuso do usuário → horas locais na agenda
                try:
                    if act == "list":
                        tmin = time_min or _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                        return google_service.cal_list(tok, tmin, time_max, _g_n(max_results), cal)
                    if act == "search":
                        return google_service.cal_search(tok, query, _g_n(max_results), cal)
                    if act == "create":
                        if not ((summary or "").strip() and (start or "").strip() and (end or "").strip()):
                            return {"error": "`summary`, `start` and `end` are required"}
                        return google_service.cal_create(tok, summary, start, end, description, location, attendees, cal, tz)
                    if act == "update":
                        if not (event_id or "").strip():
                            return {"error": "`event_id` is required"}
                        return google_service.cal_update(tok, event_id, summary, start, end, description, location, cal, tz)
                    if not (event_id or "").strip():
                        return {"error": "`event_id` is required"}
                    return google_service.cal_delete(tok, event_id, cal)
                except Exception as exc:  # noqa: BLE001
                    return {"error": str(exc)}

    # ---------------------- Tuya / Smart Life (casa) --------------------------- #
    if want("smartlife.tuya.devices"):
        t_conn = tuya_cfg.conn if tuya_cfg else {}
        t_confirm = True if tuya_cfg is None else bool(tuya_cfg.require_confirm)
        t_allowed = {str(d) for d in (tuya_cfg.allowed_devices if tuya_cfg else [])}
        t_ops = tuya_cfg.ops if tuya_cfg else {}

        def _t_on(cap: str) -> bool:
            return t_ops.get(cap, True) is not False

        def _t_truthy(v: Any) -> bool:
            if v is True:
                return True
            return isinstance(v, str) and v.strip().lower() in ("true", "1", "yes", "sim", "on")

        def _t_allowed(device: str) -> dict | None:
            """Bloqueio se o device (resolvido) não está liberado neste modelo."""
            if not t_allowed:
                return None
            from ..integrations import tuya_service
            resolved = tuya_service.resolve_device(t_conn, device)
            name = resolved["name"] if resolved else device
            if name in t_allowed:
                return None
            return {"error": f"o dispositivo '{name}' não está liberado para este modelo."}

        def _t_guard(summary: str, confirm: Any) -> dict | None:
            # background (automações/canais): sem usuário p/ confirmar → aciona direto
            if t_confirm and not _t_truthy(confirm) and not toolctx.background.get():
                from .interaction import ask_options
                return ask_options(
                    summary,
                    [
                        {"label": "Confirmar", "value": "Sim, confirmo — refaça a ação agora com confirm=true."},
                        {"label": "Cancelar", "value": "Cancele, não execute a ação."},
                    ],
                    allow_custom=False,
                )
            return None

        @sift.tool(
            "smartlife.tuya.devices",
            description=(
                "Control the user's smart home (Tuya / Smart Life) — lights (luz, lâmpada), "
                "plugs/sockets (tomada), air conditioner (ar-condicionado), projector (projetor), "
                "scenes (cena). Use this for ANY request to turn devices on/off or control the "
                "home, including Portuguese phrasings like 'apague a luz do quarto', 'liga o ar', "
                "'acende a lâmpada'. `action`: 'list' (show the user's devices/scenes — call this "
                "first if unsure what exists), 'status' (a device's live state: on/off, and for an "
                "AC its temperature/mode/fan), 'on'/'off' (switch a light/plug/device by `device`; "
                "optional `channel`), 'ac' (turn on and set the AC: `temperature`, `mode` "
                "cold/hot/auto/dry/fan, `fan` auto/low/mid/high; use action 'off' to turn it off), "
                "'scene' (trigger a tap-to-run `scene`). `device` accepts a name or natural alias. "
                "Writes ask for confirmation unless confirm=true."
            ),
            params={
                "action": "string:n::list | status | on | off | ac | scene",
                "device": "string:o::the device name or alias (on/off/status/ac)",
                "channel": "number:o:1:on/off: switch channel (1, or 2 for a second button)",
                "temperature": "number:o:23:ac: target temperature in Celsius",
                "mode": "string:o:frio:ac: cold/hot/auto/dry/fan (aliases accepted)",
                "fan": "string:o:auto:ac: fan speed auto/low/mid/high",
                "scene": "string:o::scene: the scene/tap-to-run name",
                "confirm": "boolean:o::set true only after the user confirmed the action",
            },
            returns=["ok", "devices", "aliases", "scenes", "device", "status", "functions",
                     "action", "temperature", "mode", "fan", "scene", "error",
                     "kind", "question", "options", "allow_custom", "custom_label"],
            risk=True,
            examples=["apague a luz do quarto", "acenda a lâmpada da sala", "liga a luz",
                      "desliga o ar", "liga o ar condicionado a 22 graus", "liga a tomada",
                      "ligar o projetor", "quais dispositivos eu tenho", "ativar uma cena",
                      "turn on the bedroom light", "turn off the AC", "what devices do I have"],
        )
        def _tuya(action: str = "", device: str = "", channel: Any = 1,
                  temperature: Any = 23, mode: str = "frio", fan: str = "auto",
                  scene: str = "", confirm: Any = None) -> dict[str, Any]:
            act = (action or "").strip().lower()
            if not t_conn:
                return {"error": "Tuya não conectado. Configure em Configurações → Integrações."}
            reads = {"list", "status"}
            writes = {"on", "off", "ac", "scene"}
            if act not in reads | writes:
                return {"error": f"unknown action '{action}' (use list/status/on/off/ac/scene)"}
            cap = ("tuya_query" if act in reads else "tuya_ac" if act == "ac"
                   else "tuya_scene" if act == "scene" else "tuya_switch")
            if not _t_on(cap):
                return {"error": f"a operação '{act}' está desativada nas configurações desta ferramenta."}
            from ..integrations import tuya_service
            try:
                if act == "list":
                    return tuya_service.list_devices(t_conn)
                if act == "status":
                    if not (device or "").strip():
                        return {"error": "`device` is required for status"}
                    blocked = _t_allowed(device)
                    return blocked if blocked else tuya_service.device_state(t_conn, device)
                # writes
                if act in {"on", "off"} and not (device or "").strip():
                    return {"error": "`device` is required"}
                if act == "scene" and not (scene or "").strip():
                    return {"error": "`scene` is required"}
                if act != "scene":  # on/off/ac miram um device (ac default = "ar")
                    blocked = _t_allowed(device or "ar")
                    if blocked:
                        return blocked
                summary = {
                    "on": f"Ligar '{device}'?",
                    "off": f"Desligar '{device}'?",
                    "ac": f"Ligar o ar '{device or 'ar'}' em {temperature}°C ({mode})?",
                    "scene": f"Disparar a cena '{scene}'?",
                }[act]
                blocked = _t_guard(summary, confirm)
                if blocked is not None:
                    return blocked
                try:
                    ch = int(channel or 1)
                except (TypeError, ValueError):
                    ch = 1
                if act == "on":
                    return tuya_service.set_switch(t_conn, device, True, ch)
                if act == "off":
                    return tuya_service.set_switch(t_conn, device, False, ch)
                if act == "ac":
                    try:
                        temp = int(float(temperature or 23))
                    except (TypeError, ValueError):
                        temp = 23
                    return tuya_service.configure_ac(t_conn, temp, mode or "frio", fan or "auto", device or "ar")
                return tuya_service.trigger_scene(t_conn, scene)
            except Exception as exc:  # noqa: BLE001
                return {"error": str(exc)}

    if want("elevenlabs.audio.generate"):
        el_conn = elevenlabs_cfg.conn if elevenlabs_cfg else {}

        @sift.tool(
            "elevenlabs.audio.generate",
            description=(
                "Generate audio with ElevenLabs and show it to the user in the chat. "
                "`action`: 'tts' (speak `text` in a voice — optional `voice` id or name; "
                "great for narration/voiceover of any text), 'sound_effect' (generate a sound "
                "from a description in `text`, optional `duration` in seconds), 'voices' (list "
                "the available voices). The audio player appears automatically — do not paste "
                "the url back."
            ),
            params={
                "action": "string:n::tts | sound_effect | voices",
                "text": "string:o::tts: the text to speak; sound_effect: the sound to describe",
                "voice": "string:o::tts: a voice id or name (from action 'voices'); omit for the default",
                "duration": "number:o::sound_effect: length in seconds (0.5-22)",
            },
            returns=["kind", "url", "prompt", "voices", "id", "name", "action", "error"],
            examples=["narrate this paragraph with elevenlabs",
                      "gere a narração desse texto em áudio",
                      "make a sound effect of ocean waves",
                      "which elevenlabs voices do I have?"],
        )
        def _elevenlabs_audio(action: str = "", text: str = "", voice: str = "",
                              duration: Any = None) -> dict[str, Any]:
            act = (action or "").strip().lower()
            if act not in ("tts", "sound_effect", "voices"):
                return {"error": f"unknown action '{action}' (use tts/sound_effect/voices)"}
            api_key = el_conn.get("api_key") or ""
            if not api_key:
                return {"error": "ElevenLabs não conectada. Conecte em Configurações → Conexões."}
            from ..integrations import elevenlabs_service as els
            from ..providers import image_gen
            try:
                if act == "voices":
                    return {"voices": els.list_voices(api_key)}
                if not (text or "").strip():
                    return {"error": "`text` is required"}
                if act == "tts":
                    data, mime = els.tts(api_key, text, voice or el_conn.get("default_voice", ""),
                                         el_conn.get("model", ""))
                else:  # sound_effect
                    dur = None
                    try:
                        dur = float(duration) if duration not in (None, "") else None
                    except (TypeError, ValueError):
                        dur = None
                    data, mime = els.sound_effect(api_key, text, dur)
            except Exception as exc:  # noqa: BLE001
                return {"error": str(exc)}
            chat_id = toolctx.current_chat_id.get()
            iid = asyncio.run(_store_media(user_id, chat_id, data, mime=mime,
                                           prompt=text[:200], model="elevenlabs"))
            if not iid:
                return {"error": "audio generated but failed to store it"}
            return {"kind": "audio", "url": image_gen.sign_image_url(iid), "prompt": text[:200]}

    # ------------------------------ Vercel ------------------------------------ #
    if want("vercel.projects.manage"):
        vc_token = (vercel_cfg.conn.get("token") if vercel_cfg else "") or ""

        @sift.tool(
            "vercel.projects.manage",
            description=(
                "Query the user's Vercel account (read-only). "
                "`action`: 'projects' (list the user's projects — id, name, framework), "
                "'deployments' (list recent deployments with state and url; pass `project` "
                "to filter to one project by name or id). Use to check what is deployed and "
                "the status of recent deploys."
            ),
            params={
                "action": "string:n::projects | deployments",
                "project": "string:o::deployments: a project name or id (prj_...) to filter; omit for all",
                "limit": "number:o::how many rows to return (1-100, default 20)",
            },
            returns=["projects", "deployments", "action", "error"],
            examples=["list my vercel projects",
                      "quais projetos eu tenho na vercel?",
                      "what's the status of my last vercel deployment?",
                      "show recent deployments for my-site on vercel"],
        )
        def _vercel(action: str = "", project: str = "", limit: Any = None) -> dict[str, Any]:
            act = (action or "").strip().lower()
            if act not in ("projects", "deployments"):
                return {"error": f"unknown action '{action}' (use projects/deployments)"}
            if not vc_token:
                return {"error": "Vercel não conectada. Conecte em Configurações → Integrações."}
            try:
                lim = int(limit) if limit not in (None, "") else 20
            except (TypeError, ValueError):
                lim = 20
            from ..integrations import vercel_service as vs
            try:
                if act == "projects":
                    return {"action": "projects", "projects": asyncio.run(vs.list_projects(vc_token, lim))}
                return {"action": "deployments",
                        "deployments": asyncio.run(vs.list_deployments(vc_token, (project or "").strip(), lim))}
            except Exception as exc:  # noqa: BLE001
                return {"error": str(exc)}

    # ------------------------------ Spotify ----------------------------------- #
    if want("spotify.music.search"):
        sp_conn = spotify_cfg.conn if spotify_cfg else {}

        @sift.tool(
            "spotify.music.search",
            description=(
                "Search the Spotify catalog (read-only). Find music by name and get a "
                "shareable link. `query`: what to search for. `kind`: 'track' (default), "
                "'artist', 'album' or 'playlist'. Returns name, artist(s) and the Spotify "
                "url. No playback control."
            ),
            params={
                "query": "string:n::what to search for (song, artist, album or playlist name)",
                "kind": "string:o::track | artist | album | playlist (default track)",
                "limit": "number:o::how many results to return (1-50, default 10)",
            },
            returns=["results", "kind", "query", "error"],
            examples=["find the song bohemian rhapsody on spotify",
                      "procure o álbum Random Access Memories no spotify",
                      "search spotify for playlists about focus",
                      "what's the spotify link for Daft Punk?"],
        )
        def _spotify(query: str = "", kind: str = "", limit: Any = None) -> dict[str, Any]:
            q = (query or "").strip()
            if not q:
                return {"error": "`query` is required"}
            cid, sec = sp_conn.get("id") or "", sp_conn.get("secret") or ""
            if not (cid and sec):
                return {"error": "Spotify não conectado. Conecte em Configurações → Integrações."}
            try:
                lim = int(limit) if limit not in (None, "") else 10
            except (TypeError, ValueError):
                lim = 10
            k = (kind or "track").strip().lower()
            from ..integrations import spotify_service as sp
            try:
                results = asyncio.run(sp.search(cid, sec, q, k, lim))
            except Exception as exc:  # noqa: BLE001
                return {"error": str(exc)}
            return {"query": q, "kind": k if k in ("track", "artist", "album", "playlist") else "track",
                    "results": results}

    # ------------------------------ GitHub ------------------------------------ #
    if want("higgsfield.media.generate"):
        hf_conn = higgsfield_cfg.conn if higgsfield_cfg else {}

        def _hf_finish(user_id_: str | None, result: dict, model_id: str, prompt_: str) -> dict:
            """Resultado 'completed' → baixa a mídia, guarda no banco e devolve o
            card. As URLs do CDN deles expiram — os bytes ficam conosco."""
            from ..integrations import higgsfield_service as hf
            from ..providers import image_gen

            url = ""
            kind = "image"
            imgs = result.get("images")
            if isinstance(imgs, list) and imgs and isinstance(imgs[0], dict):
                url = imgs[0].get("url") or ""
            vid = result.get("video")
            if not url and isinstance(vid, dict):
                url, kind = vid.get("url") or "", "video"
            vids = result.get("videos")
            if not url and isinstance(vids, list) and vids and isinstance(vids[0], dict):
                url, kind = vids[0].get("url") or "", "video"
            if not url:
                return {"error": f"generation finished but no media url in response: {str(result)[:200]}"}
            got = hf.download(url)
            if isinstance(got, dict):
                return got
            data, mime = got
            if kind == "image" and mime.startswith("video/"):
                kind = "video"  # confia no mime real, não na chave do JSON
            chat_id = toolctx.current_chat_id.get()
            iid = asyncio.run(_store_media(
                user_id_, chat_id, data, mime=mime, prompt=prompt_, model=model_id,
            ))
            if not iid:
                return {"error": "media generated but failed to store it"}
            return {"kind": kind, "url": image_gen.sign_image_url(iid), "prompt": prompt_,
                    "model": model_id}

        def _hf_resolve_reference(image_url: str) -> str | dict:
            """`image_url` p/ um formato que a Higgsfield alcança: URL nossa
            (/images/<id>) → sobe os bytes; URL privada → erro; pública → passa."""
            from ..integrations import higgsfield_service as hf

            m = re.search(r"/images/([0-9a-f-]{36})", image_url)
            if m:
                got = asyncio.run(_load_media(m.group(1)))
                if got is None:
                    return {"error": "referenced image not found"}
                data, mime = got
                return hf.upload_bytes(hf_conn, data, mime)
            if not _public_web_url(image_url):
                return {"error": "image_url must be public (or a generated /images/<id> url)"}
            return image_url

        @sift.tool(
            "higgsfield.media.generate",
            description=(
                "Generate images and videos with Higgsfield's models (Soul, Seedream, FLUX, "
                "DoP, Kling, Seedance). `action`: 'image' (text-to-image from `prompt`; "
                "optional `model`, `aspect_ratio` like '16:9', `resolution` like '1080p'; "
                "edit models take `image_url`), 'video' (image-to-video: `image_url` REQUIRED "
                "— a previously generated /images/... url or any public image — plus `prompt` "
                "describing the motion; optional `model`, `duration` in seconds), 'models' "
                "(the catalog: ids + what each accepts), 'status' (resume a generation that "
                "was still running, by `request_id`). The tool waits for the result and the "
                "media is shown to the user automatically — do not paste the url back."
            ),
            params={
                "action": "string:n::image | video | models | status",
                "prompt": "string:o::image/video: what to generate / how to animate",
                "model": "string:o::model id from action 'models' (default: Soul for image, DoP for video)",
                "image_url": "string:o::video (required) and edit models: source image url",
                "aspect_ratio": "string:o::image: e.g. '1:1', '16:9', '9:16'",
                "resolution": "string:o::image: e.g. '720p', '1080p', '2K'",
                "duration": "number:o:5:video: length in seconds",
                "seed": "number:o::image: reproducible output (models that support it)",
                "request_id": "string:o::status: id returned when a generation kept running",
            },
            returns=["kind", "url", "prompt", "model", "models", "status", "request_id",
                     "note", "error"],
            examples=["generate a photorealistic portrait with higgsfield",
                      "crie uma imagem de um lago ao pôr do sol",
                      "anime essa imagem em um vídeo de 5 segundos",
                      "which higgsfield models are available?"],
        )
        def _higgsfield_generate(action: str = "", prompt: str = "", model: str = "",
                                 image_url: str = "", aspect_ratio: str = "",
                                 resolution: str = "", duration: Any = None,
                                 seed: Any = None, request_id: str = "") -> dict[str, Any]:
            from ..integrations import higgsfield_service as hf

            act = (action or "").strip().lower()
            if act == "models":
                return {"models": {
                    "image": [{"id": k, **v} for k, v in hf.IMAGE_MODELS.items()],
                    "video": [{"id": k, **v} for k, v in hf.VIDEO_MODELS.items()],
                }}
            if not hf_conn:
                return {"error": "Higgsfield is not connected. Ask the user to add their "
                                 "API key in Settings → Connections → Higgsfield."}

            def _int(v: Any, default: int) -> int:
                try:
                    return int(v) if v not in (None, "") else default
                except (TypeError, ValueError):
                    return default

            if act == "status":
                rid = (request_id or "").strip()
                if not rid:
                    return {"error": "provide 'request_id'"}
                st = hf.fetch_status(hf_conn, rid)
                if st.get("error"):
                    return st
                if st.get("status") == "completed":
                    return _hf_finish(user_id, st, model or "higgsfield", prompt)
                return {"status": st.get("status"), "request_id": rid,
                        "note": "still running — call action 'status' again in a moment"}

            if act in ("image", "video"):
                p = (prompt or "").strip()
                if not p and act == "image":
                    return {"error": "provide 'prompt'"}
                catalog = hf.IMAGE_MODELS if act == "image" else hf.VIDEO_MODELS
                model_id = (model or "").strip() or (
                    hf.DEFAULT_IMAGE_MODEL if act == "image" else hf.DEFAULT_VIDEO_MODEL
                )
                if model_id not in catalog:
                    return {"error": f"unknown {act} model '{model_id}' — call action 'models' "
                                     "to list the valid ids"}
                supported = catalog[model_id]["params"]
                args: dict[str, Any] = {"prompt": p} if p else {}
                if aspect_ratio and "aspect_ratio" in supported:
                    args["aspect_ratio"] = aspect_ratio.strip()
                if resolution and "resolution" in supported:
                    args["resolution"] = resolution.strip()
                if seed not in (None, "") and "seed" in supported:
                    args["seed"] = _int(seed, 0)
                if "duration" in supported and act == "video":
                    args["duration"] = _int(duration, 5)
                needs_image = act == "video" or "image_url" in supported
                if needs_image:
                    src = (image_url or "").strip()
                    if not src:
                        return {"error": f"'{model_id}' needs 'image_url' (a generated "
                                         "/images/... url or a public image url)"}
                    resolved = _hf_resolve_reference(src)
                    if isinstance(resolved, dict):
                        return resolved
                    args["image_url"] = resolved
                sub = hf.submit(hf_conn, model_id, args)
                if sub.get("error"):
                    return sub
                rid = str(sub["request_id"])
                # imagem sai em segundos; vídeo pode levar minutos — orçamentos
                # distintos, e no estouro devolvemos o request_id p/ retomar.
                budget = 240.0 if act == "image" else 540.0
                st = hf.wait(hf_conn, rid, budget_s=budget)
                if st.get("error"):
                    return {**st, "request_id": rid}
                status_ = st.get("status")
                if status_ == "completed":
                    return _hf_finish(user_id, st, model_id, p)
                if status_ == "nsfw":
                    return {"error": "generation flagged as NSFW by Higgsfield (credits refunded)"}
                if status_ in ("failed", "canceled"):
                    return {"error": f"generation {status_} (credits refunded on failure)"}
                return {"status": status_, "request_id": rid,
                        "note": "generation still running — call action 'status' with this "
                                "request_id to fetch the result"}

            return {"error": f"unknown action '{act}' (use image/video/models/status)"}

    if want("github.repo.manage"):
        gh_confirm = True if github_cfg is None else bool(github_cfg.require_confirm)
        gh_accounts = list(github_cfg.accounts) if github_cfg else []  # [{"id","login"}]
        gh_ops = github_cfg.ops if github_cfg else {}

        def _gh_on(cap: str) -> bool:
            return gh_ops.get(cap, True) is not False

        def _gh_truthy(v: Any) -> bool:
            if v is True:
                return True
            return isinstance(v, str) and v.strip().lower() in ("true", "1", "yes", "sim", "on")

        def _gh_pick(account: str = "") -> tuple[dict | None, dict | None]:
            if not gh_accounts:
                return None, {"error": "GitHub não conectado. Conecte uma conta em Configurações → Integrações."}
            if (account or "").strip():
                a = account.strip().lower()
                chosen = next(
                    (x for x in gh_accounts
                     if a == str(x.get("id", "")).lower() or a == (x.get("login", "") or "").lower()),
                    None,
                )
                if chosen is None:
                    logins = ", ".join(x.get("login", "") for x in gh_accounts)
                    return None, {"error": f"conta '{account}' não liberada para este modelo. Disponíveis: {logins}"}
                return chosen, None
            if len(gh_accounts) == 1:
                return gh_accounts[0], None
            from .interaction import ask_options
            return None, ask_options(
                "Qual conta GitHub devo usar?",
                [{"label": x.get("login", ""), "value": f"Use a conta {x.get('login', '')}"} for x in gh_accounts],
                allow_custom=False,
            )

        def _gh_ctx(account: str = "") -> tuple[str | None, dict | None]:
            chosen, block = _gh_pick(account)
            if block is not None:
                return None, block
            from ..integrations import github_service
            tok = asyncio.run(github_service.get_token(str(chosen.get("id"))))
            if not tok:
                return None, {"error": f"não foi possível acessar a conta {chosen.get('login', '')} (reconecte em Integrações)."}
            return tok, None

        def _gh_guard(summary: str, confirm: Any) -> dict | None:
            # em automações/canais (background) não há usuário p/ confirmar → executa direto
            if gh_confirm and not _gh_truthy(confirm) and not toolctx.background.get():
                from .interaction import ask_options
                return ask_options(
                    summary,
                    [
                        {"label": "Confirmar", "value": "Sim, confirmo — refaça a ação agora com confirm=true."},
                        {"label": "Cancelar", "value": "Cancele, não execute a ação."},
                    ],
                    allow_custom=False,
                )
            return None

        def _gh_n(limit: Any, default: int = 20) -> int:
            s = str(limit if limit is not None else "").strip()
            if not s:
                return default
            try:
                return max(1, min(int(float(s)), 100))
            except (TypeError, ValueError):
                return default

        @sift.tool(
            "github.repo.manage",
            description=(
                "The user's GitHub. `repo` is 'owner/name'. `action`: 'list_repos' (their repos), "
                "'read_file' (`repo`,`path`; optional `ref`; a directory lists its entries), "
                "'search_code' (`query`, optional `repo`), 'list_issues'/'list_prs' (`repo`, optional "
                "`state` open/closed/all), 'read_issue'/'read_pr' (`repo`,`number`), 'create_issue' "
                "(`repo`,`title`,`body`), 'comment' (`repo`,`number`,`body`), 'create_pr' "
                "(`repo`,`title`,`head`,`base`,`body`), 'put_file' (commit: `repo`,`path`,`body` as the "
                "file content, `message`; `branch` and `sha` to update an existing file). Writes ask "
                "the user for confirmation unless confirm=true."
            ),
            params={
                "action": "string:n::list_repos | read_file | search_code | list_issues | read_issue | list_prs | read_pr | create_issue | comment | create_pr | put_file",
                "repo": "string:o::'owner/name' (required for everything except list_repos)",
                "path": "string:o::read_file/put_file: file path in the repo",
                "ref": "string:o::read_file: branch/tag/sha (default the repo's default branch)",
                "query": "string:o::search_code: what to look for",
                "number": "number:o::read_issue/read_pr/comment: the issue or PR number",
                "state": "string:o::list_issues/list_prs: open | closed | all (default open)",
                "title": "string:o::create_issue/create_pr: title",
                "body": "string:o::create_issue/comment/create_pr: text. For put_file: the FULL file content",
                "head": "string:o::create_pr: the branch with your changes",
                "base": "string:o::create_pr: the branch to merge into (e.g. main)",
                "message": "string:o::put_file: commit message",
                "branch": "string:o::put_file: target branch (default the repo's default)",
                "sha": "string:o::put_file: blob sha of the file being replaced (required to update, omit to create)",
                "limit": "number:o::list_*/search: max items (1-100)",
                "confirm": "boolean:o::set true only after the user confirmed a write",
                "account": "string:o::which connected GitHub account to use (login); omit if only one",
            },
            returns=["repos", "full_name", "content", "path", "sha", "entries", "type", "items",
                     "number", "title", "state", "user", "url", "body", "comment_list", "head",
                     "base", "ok", "commit", "action", "error",
                     "kind", "question", "options", "allow_custom", "custom_label"],
            risk=True,
            examples=["list my repositories", "read src/app.py in me/repo", "search TODO in my repo",
                      "any open issues on me/repo?", "create an issue in me/repo", "comment on issue 5",
                      "open a PR from feature to main"],
        )
        def _github(action: str = "", repo: str = "", path: str = "", ref: str = "",
                    query: str = "", number: Any = None, state: str = "", title: str = "",
                    body: str = "", head: str = "", base: str = "", message: str = "",
                    branch: str = "", sha: str = "", limit: Any = None, confirm: Any = None,
                    account: str = "") -> dict[str, Any]:
            act = (action or "").strip().lower()
            reads = {"list_repos", "read_file", "search_code", "list_issues",
                     "read_issue", "list_prs", "read_pr"}
            writes = {"create_issue", "comment", "create_pr", "put_file"}
            if act not in reads | writes:
                return {"error": f"unknown action '{action}' (use list_repos/read_file/search_code/"
                                 "list_issues/read_issue/list_prs/read_pr/create_issue/comment/create_pr/put_file)"}
            cap = ("gh_read" if act in reads else "gh_issue" if act == "create_issue"
                   else "gh_comment" if act == "comment" else "gh_pr" if act == "create_pr"
                   else "gh_commit")
            if not _gh_on(cap):
                return {"error": f"a operação '{act}' está desativada nas configurações desta ferramenta."}
            if act != "list_repos" and not (repo or "").strip():
                return {"error": "`repo` ('owner/name') is required"}
            # confirmação antes de escrever
            if act in writes:
                summ = {
                    "create_issue": f"Criar a issue “{title}” em {repo}?",
                    "comment": f"Comentar na #{number} de {repo}?",
                    "create_pr": f"Abrir PR “{title}” em {repo} ({head} → {base})?",
                    "put_file": f"Commitar em {repo}: {path}?",
                }[act]
                blocked = _gh_guard(summ, confirm)
                if blocked is not None:
                    return blocked
            tok, block = _gh_ctx(account)
            if block is not None:
                return block
            from ..integrations import github_service as ghs
            try:
                if act == "list_repos":
                    return {"repos": ghs.list_repos(tok, limit=_gh_n(limit, 30))}
                if act == "read_file":
                    if not (path or "").strip():
                        return {"error": "`path` is required for read_file"}
                    return ghs.get_file(tok, repo, path, ref)
                if act == "search_code":
                    if not (query or "").strip():
                        return {"error": "`query` is required for search_code"}
                    return {"items": ghs.search_code(tok, query, repo, _gh_n(limit, 10))}
                if act == "list_issues":
                    return {"items": ghs.list_issues(tok, repo, state or "open", _gh_n(limit))}
                if act == "read_issue":
                    if number is None:
                        return {"error": "`number` is required"}
                    return ghs.get_issue(tok, repo, int(number))
                if act == "list_prs":
                    return {"items": ghs.list_prs(tok, repo, state or "open", _gh_n(limit))}
                if act == "read_pr":
                    if number is None:
                        return {"error": "`number` is required"}
                    return ghs.get_pr(tok, repo, int(number))
                if act == "create_issue":
                    if not (title or "").strip():
                        return {"error": "`title` is required"}
                    return ghs.create_issue(tok, repo, title, body)
                if act == "comment":
                    if number is None or not (body or "").strip():
                        return {"error": "`number` and `body` are required"}
                    return ghs.comment_issue(tok, repo, int(number), body)
                if act == "create_pr":
                    if not ((title or "").strip() and (head or "").strip() and (base or "").strip()):
                        return {"error": "`title`, `head` and `base` are required"}
                    return ghs.create_pr(tok, repo, title, head, base, body)
                # put_file
                if not ((path or "").strip() and (message or "").strip()):
                    return {"error": "`path` and `message` are required for put_file"}
                return ghs.put_file(tok, repo, path, body, message, branch, sha)
            except ghs.GithubError as exc:
                return {"error": str(exc)}
            except Exception as exc:  # noqa: BLE001
                return {"error": str(exc)}

    if want("notion.workspace.manage"):
        nt_confirm = True if notion_cfg is None else bool(notion_cfg.require_confirm)
        nt_accounts = list(notion_cfg.accounts) if notion_cfg else []  # [{"id","workspace"}]
        nt_ops = notion_cfg.ops if notion_cfg else {}

        def _nt_on(cap: str) -> bool:
            return nt_ops.get(cap, True) is not False

        def _nt_truthy(v: Any) -> bool:
            if v is True:
                return True
            return isinstance(v, str) and v.strip().lower() in ("true", "1", "yes", "sim", "on")

        def _nt_pick(account: str = "") -> tuple[dict | None, dict | None]:
            if not nt_accounts:
                return None, {"error": "Notion não conectado. Conecte um workspace em Configurações → Integrações."}
            if (account or "").strip():
                a = account.strip().lower()
                chosen = next(
                    (x for x in nt_accounts
                     if a == str(x.get("id", "")).lower() or a == (x.get("workspace", "") or "").lower()),
                    None,
                )
                if chosen is None:
                    names = ", ".join(x.get("workspace", "") for x in nt_accounts)
                    return None, {"error": f"workspace '{account}' não liberado para este modelo. Disponíveis: {names}"}
                return chosen, None
            if len(nt_accounts) == 1:
                return nt_accounts[0], None
            from .interaction import ask_options
            return None, ask_options(
                "Qual workspace Notion devo usar?",
                [{"label": x.get("workspace", ""), "value": f"Use o workspace {x.get('workspace', '')}"} for x in nt_accounts],
                allow_custom=False,
            )

        def _nt_ctx(account: str = "") -> tuple[str | None, dict | None]:
            chosen, block = _nt_pick(account)
            if block is not None:
                return None, block
            from ..integrations import notion_service
            tok = asyncio.run(notion_service.get_token(str(chosen.get("id"))))
            if not tok:
                return None, {"error": f"não foi possível acessar o workspace {chosen.get('workspace', '')} (reconecte em Integrações)."}
            return tok, None

        def _nt_guard(summary: str, confirm: Any) -> dict | None:
            if nt_confirm and not _nt_truthy(confirm) and not toolctx.background.get():
                from .interaction import ask_options
                return ask_options(
                    summary,
                    [
                        {"label": "Confirmar", "value": "Sim, confirmo — refaça a ação agora com confirm=true."},
                        {"label": "Cancelar", "value": "Cancele, não execute a ação."},
                    ],
                    allow_custom=False,
                )
            return None

        def _nt_n(limit: Any, default: int = 10) -> int:
            s = str(limit if limit is not None else "").strip()
            if not s:
                return default
            try:
                return max(1, min(int(float(s)), 100))
            except (TypeError, ValueError):
                return default

        @sift.tool(
            "notion.workspace.manage",
            description=(
                "The user's Notion workspace. `action`: 'search' (find pages/databases by "
                "`query`; optional `kind` page|database), 'read_page' (`page_id` — returns the "
                "page's title and readable text content), 'query_database' (`database_id` — list "
                "its rows), 'create_page' (`title`, optional `content`; a parent is required: "
                "`parent_page_id` for a subpage OR `database_id` to add a row), 'append' (`page_id`, "
                "`content` — add content to an existing page). `content` is light markdown ('# ' "
                "heading, '- ' bullet, blank line = paragraph). Writes ask the user for confirmation "
                "unless confirm=true. IDs come from a prior 'search'."
            ),
            params={
                "action": "string:n::search | read_page | query_database | create_page | append",
                "query": "string:o::search: what to look for (empty = list everything shared with the integration)",
                "kind": "string:o::search: restrict to 'page' or 'database' (default both)",
                "page_id": "string:o::read_page/append: the page id (from search)",
                "database_id": "string:o::query_database: the database id; create_page: add a row to this database",
                "parent_page_id": "string:o::create_page: create the page UNDER this existing page",
                "title": "string:o::create_page: the new page's title",
                "content": "string:o::create_page/append: body text (light markdown)",
                "limit": "number:o::search/query_database: max items (1-100)",
                "confirm": "boolean:o::set true only after the user confirmed a write",
                "account": "string:o::which connected Notion workspace to use; omit if only one",
            },
            returns=["results", "id", "type", "title", "url", "last_edited", "content",
                     "ok", "action", "error",
                     "kind", "question", "options", "allow_custom", "custom_label"],
            risk=True,
            examples=["search my notion for the roadmap", "read that notion page",
                      "list the rows in my tasks database", "create a notion page titled Notes",
                      "append a summary to that page"],
        )
        def _notion(action: str = "", query: str = "", kind: str = "", page_id: str = "",
                    database_id: str = "", parent_page_id: str = "", title: str = "",
                    content: str = "", limit: Any = None, confirm: Any = None,
                    account: str = "") -> dict[str, Any]:
            act = (action or "").strip().lower()
            reads = {"search", "read_page", "query_database"}
            writes = {"create_page", "append"}
            if act not in reads | writes:
                return {"error": f"unknown action '{action}' (use search/read_page/query_database/create_page/append)"}
            cap = ("nt_read" if act in reads else "nt_create" if act == "create_page" else "nt_append")
            if not _nt_on(cap):
                return {"error": f"a operação '{act}' está desativada nas configurações desta ferramenta."}
            if act in writes:
                summ = (f"Criar a página “{title}” no Notion?" if act == "create_page"
                        else "Adicionar conteúdo à página do Notion?")
                blocked = _nt_guard(summ, confirm)
                if blocked is not None:
                    return blocked
            tok, block = _nt_ctx(account)
            if block is not None:
                return block
            from ..integrations import notion_service as nts
            try:
                if act == "search":
                    return {"results": nts.search(tok, query, kind, _nt_n(limit))}
                if act == "read_page":
                    if not (page_id or "").strip():
                        return {"error": "`page_id` is required for read_page"}
                    return nts.get_page(tok, page_id.strip())
                if act == "query_database":
                    if not (database_id or "").strip():
                        return {"error": "`database_id` is required"}
                    return {"results": nts.query_database(tok, database_id, _nt_n(limit, 20))}
                if act == "create_page":
                    if not (title or "").strip():
                        return {"error": "`title` is required for create_page"}
                    if not (parent_page_id or "").strip() and not (database_id or "").strip():
                        return {"error": "a parent is required: `parent_page_id` or `database_id`"}
                    return nts.create_page(tok, title, content, parent_page_id, database_id)
                # append
                if not ((page_id or "").strip() and (content or "").strip()):
                    return {"error": "`page_id` and `content` are required for append"}
                return nts.append_blocks(tok, page_id, content)
            except nts.NotionError as exc:
                return {"error": str(exc)}
            except Exception as exc:  # noqa: BLE001
                return {"error": str(exc)}

    if want("slack.workspace.manage"):
        sl_confirm = True if slack_cfg is None else bool(slack_cfg.require_confirm)
        sl_accounts = list(slack_cfg.accounts) if slack_cfg else []  # [{"id","team"}]
        sl_ops = slack_cfg.ops if slack_cfg else {}

        def _sl_on(cap: str) -> bool:
            return sl_ops.get(cap, True) is not False

        def _sl_truthy(v: Any) -> bool:
            if v is True:
                return True
            return isinstance(v, str) and v.strip().lower() in ("true", "1", "yes", "sim", "on")

        def _sl_pick(account: str = "") -> tuple[dict | None, dict | None]:
            if not sl_accounts:
                return None, {"error": "Slack não conectado. Conecte um workspace em Configurações → Integrações."}
            if (account or "").strip():
                a = account.strip().lower()
                chosen = next(
                    (x for x in sl_accounts
                     if a == str(x.get("id", "")).lower() or a == (x.get("team", "") or "").lower()),
                    None,
                )
                if chosen is None:
                    names = ", ".join(x.get("team", "") for x in sl_accounts)
                    return None, {"error": f"workspace '{account}' não liberado para este modelo. Disponíveis: {names}"}
                return chosen, None
            if len(sl_accounts) == 1:
                return sl_accounts[0], None
            from .interaction import ask_options
            return None, ask_options(
                "Qual workspace Slack devo usar?",
                [{"label": x.get("team", ""), "value": f"Use o workspace {x.get('team', '')}"} for x in sl_accounts],
                allow_custom=False,
            )

        def _sl_ctx(account: str = "") -> tuple[str | None, dict | None]:
            chosen, block = _sl_pick(account)
            if block is not None:
                return None, block
            from ..integrations import slack_service
            tok = asyncio.run(slack_service.get_token(str(chosen.get("id"))))
            if not tok:
                return None, {"error": f"não foi possível acessar o workspace {chosen.get('team', '')} (reconecte em Integrações)."}
            return tok, None

        def _sl_guard(summary: str, confirm: Any) -> dict | None:
            if sl_confirm and not _sl_truthy(confirm) and not toolctx.background.get():
                from .interaction import ask_options
                return ask_options(
                    summary,
                    [
                        {"label": "Enviar", "value": "Sim, confirmo — reenvie agora com confirm=true."},
                        {"label": "Cancelar", "value": "Cancele, não envie a mensagem."},
                    ],
                    allow_custom=False,
                )
            return None

        def _sl_n(limit: Any, default: int = 20) -> int:
            s = str(limit if limit is not None else "").strip()
            if not s:
                return default
            try:
                return max(1, min(int(float(s)), 100))
            except (TypeError, ValueError):
                return default

        @sift.tool(
            "slack.workspace.manage",
            description=(
                "The user's Slack workspace. `action`: 'list_channels' (channels & DMs the bot is "
                "in), 'read' (`channel` id — recent messages, chronological), 'search' (`query` — "
                "needs a user token with search:read), 'send' (`channel` id, `text` — post a "
                "message). Channel IDs come from list_channels. Sending asks the user for "
                "confirmation unless confirm=true."
            ),
            params={
                "action": "string:n::list_channels | read | search | send",
                "channel": "string:o::read/send: the channel or DM id (from list_channels)",
                "text": "string:o::send: the message text",
                "query": "string:o::search: what to look for",
                "limit": "number:o::list_channels/read/search: max items (1-100/200)",
                "confirm": "boolean:o::set true only after the user confirmed a send",
                "account": "string:o::which connected Slack workspace to use; omit if only one",
            },
            returns=["channels", "messages", "results", "id", "name", "is_private", "is_im",
                     "is_member", "user", "text", "ts", "channel", "permalink", "ok", "action", "error",
                     "kind", "question", "options", "allow_custom", "custom_label"],
            risk=True,
            examples=["list my slack channels", "what are the latest messages in that channel",
                      "search slack for the deploy thread", "send a message to the team channel"],
        )
        def _slack(action: str = "", channel: str = "", text: str = "", query: str = "",
                   limit: Any = None, confirm: Any = None, account: str = "") -> dict[str, Any]:
            act = (action or "").strip().lower()
            reads = {"list_channels", "read", "search"}
            writes = {"send"}
            if act not in reads | writes:
                return {"error": f"unknown action '{action}' (use list_channels/read/search/send)"}
            cap = "sl_send" if act == "send" else "sl_read"
            if not _sl_on(cap):
                return {"error": f"a operação '{act}' está desativada nas configurações desta ferramenta."}
            if act == "send":
                if not ((channel or "").strip() and (text or "").strip()):
                    return {"error": "`channel` and `text` are required for send"}
                blocked = _sl_guard(f"Enviar mensagem no canal {channel} do Slack?", confirm)
                if blocked is not None:
                    return blocked
            tok, block = _sl_ctx(account)
            if block is not None:
                return block
            from ..integrations import slack_service as sls
            try:
                if act == "list_channels":
                    return {"channels": sls.list_channels(tok, _sl_n(limit, 50))}
                if act == "read":
                    if not (channel or "").strip():
                        return {"error": "`channel` is required for read"}
                    return {"messages": sls.channel_history(tok, channel, _sl_n(limit))}
                if act == "search":
                    if not (query or "").strip():
                        return {"error": "`query` is required for search"}
                    return {"results": sls.search_messages(tok, query, _sl_n(limit))}
                # send
                return sls.post_message(tok, channel, text)
            except sls.SlackError as exc:
                return {"error": str(exc)}
            except Exception as exc:  # noqa: BLE001
                return {"error": str(exc)}

    if want("messaging.chat.manage"):
        msg_confirm = True if messaging_cfg is None else bool(messaging_cfg.require_confirm)
        msg_accounts = list(messaging_cfg.accounts) if messaging_cfg else []  # [{id,platform,label}]
        msg_ops = messaging_cfg.ops if messaging_cfg else {}
        _MSG_PLAT_PT = {"whatsapp": "WhatsApp", "telegram": "Telegram", "discord": "Discord"}

        def _msg_on(cap: str) -> bool:
            return msg_ops.get(cap, True) is not False

        def _msg_truthy(v: Any) -> bool:
            if v is True:
                return True
            return isinstance(v, str) and v.strip().lower() in ("true", "1", "yes", "sim", "on")

        def _msg_label(a: dict) -> str:
            plat = _MSG_PLAT_PT.get(a.get("platform", ""), a.get("platform", ""))
            return f"{a.get('label') or plat} ({plat})"

        def _msg_pick(account: str = "", platform: str = "") -> tuple[dict | None, dict | None]:
            pool = msg_accounts
            plat = (platform or "").strip().lower()
            if plat:
                pool = [a for a in pool if a.get("platform") == plat]
            if not pool:
                if not msg_accounts:
                    return None, {"error": "Nenhuma conexão de chat liberada. Conecte um WhatsApp/Telegram/Discord em Configurações → Integrações e libere-o na engrenagem desta ferramenta."}
                return None, {"error": f"nenhuma conexão de '{platform}' liberada para este modelo."}
            if (account or "").strip():
                a = account.strip().lower()
                chosen = next(
                    (x for x in pool
                     if a == str(x.get("id", "")).lower() or a == (x.get("label", "") or "").lower()
                     or a == (x.get("platform", "") or "").lower()),
                    None,
                )
                if chosen is None:
                    labels = ", ".join(_msg_label(x) for x in pool)
                    return None, {"error": f"conexão '{account}' não encontrada. Disponíveis: {labels}"}
                return chosen, None
            if len(pool) == 1:
                return pool[0], None
            from .interaction import ask_options
            return None, ask_options(
                "Por qual conexão devo agir?",
                [{"label": _msg_label(x), "value": f"Use a conexão {x.get('label') or x.get('platform')} ({x.get('platform')})"} for x in pool],
                allow_custom=False,
            )

        def _msg_guard(summary: str, confirm: Any) -> dict | None:
            # canais/automações (background): não há usuário p/ confirmar → envia direto
            if msg_confirm and not _msg_truthy(confirm) and not toolctx.background.get():
                from .interaction import ask_options
                return ask_options(
                    summary,
                    [
                        {"label": "Enviar", "value": "Sim, confirmo — reenvie agora com confirm=true."},
                        {"label": "Cancelar", "value": "Cancele, não envie a mensagem."},
                    ],
                    allow_custom=False,
                )
            return None

        def _msg_n(limit: Any, default: int = 20) -> int:
            s = str(limit if limit is not None else "").strip()
            if not s:
                return default
            try:
                return max(1, min(int(float(s)), 100))
            except (TypeError, ValueError):
                return default

        @sift.tool(
            "messaging.chat.manage",
            description=(
                "Act on the user's OWN chat connections (WhatsApp/Telegram/Discord) — send, read "
                "and list on their behalf ('reply to X for me', 'tell the group about the meeting', "
                "'what did X say about Y?'). `action`: 'list_chats' (find conversations; optional "
                "`query` to filter by name/number), 'read_messages' (`chat` = a conversation id from "
                "list_chats, or a phone number for WhatsApp; recent history), 'send_message' (`chat` + "
                "`text`). Pick the connection with `platform` (whatsapp|telegram|discord) and/or "
                "`account` when the user has more than one. IMPORTANT per-platform limits: WhatsApp "
                "acts AS the user (full read/send to anyone). Telegram/Discord act as a BOT — they only "
                "reach conversations the bot is already in, and Telegram CANNOT read history. Prefer "
                "calling list_chats first to get the exact `chat` id. Sending asks the user to confirm "
                "unless confirm=true."
            ),
            params={
                "action": "string:n::list_chats | read_messages | send_message",
                "platform": "string:o::which network: whatsapp | telegram | discord (omit if the user has only one)",
                "account": "string:o::which connection (its label) when several on the same network",
                "chat": "string:o::the conversation: an id from list_chats, or a phone number (with country code) for WhatsApp",
                "text": "string:o::send_message: the message to send",
                "query": "string:o::list_chats: filter conversations by name or number",
                "limit": "number:o::list_chats/read_messages: max items (1-100)",
                "confirm": "boolean:o::set true only after the user confirmed sending",
            },
            returns=["chats", "messages", "id", "name", "is_group", "is_dm", "from", "text",
                     "from_me", "ts", "ok", "to", "platform", "action", "error",
                     "kind", "question", "options", "allow_custom", "custom_label"],
            risk=True,
            examples=["reply to Ana on WhatsApp for me", "what did the group say about the trip?",
                      "tell the family group there's a meeting at 8pm", "list my WhatsApp chats",
                      "read my last messages with João"],
        )
        def _messaging(action: str = "", platform: str = "", account: str = "", chat: str = "",
                       text: str = "", query: str = "", limit: Any = None,
                       confirm: Any = None) -> dict[str, Any]:
            act = (action or "").strip().lower()
            caps = {"list_chats": "msg_list", "read_messages": "msg_read", "send_message": "msg_send"}
            if act not in caps:
                return {"error": f"unknown action '{action}' (use list_chats/read_messages/send_message)"}
            if not _msg_on(caps[act]):
                return {"error": f"a operação '{act}' está desativada nas configurações desta ferramenta."}
            chosen, block = _msg_pick(account, platform)
            if block is not None:
                return block
            plat = chosen["platform"]
            cid = str(chosen["id"])
            plat_pt = _MSG_PLAT_PT.get(plat, plat)
            if act == "send_message":
                if not (text or "").strip():
                    return {"error": "`text` (a mensagem) é obrigatório para send_message"}
                if not (chat or "").strip():
                    return {"error": "`chat` (o destinatário) é obrigatório — use list_chats p/ achar o id"}
                who = chat.strip()
                blocked = _msg_guard(f"Enviar no {plat_pt} para {who}: “{text.strip()[:140]}”?", confirm)
                if blocked is not None:
                    return blocked
            from ..integrations import messaging_service as ms
            try:
                if act == "list_chats":
                    rows = asyncio.run(ms.list_chats(plat, cid, query, _msg_n(limit, 30)))
                    return {"chats": rows, "platform": plat}
                if act == "read_messages":
                    rows = asyncio.run(ms.read_messages(plat, cid, chat, _msg_n(limit, 20)))
                    return {"messages": rows, "platform": plat}
                return asyncio.run(ms.send_message(plat, cid, chat, text))
            except ms.MessagingError as exc:
                return {"error": str(exc)}
            except Exception as exc:  # noqa: BLE001
                return {"error": str(exc)}


# --------------------------------------------------------------------------- #
# Ferramentas do usuário (código dinâmico)
# --------------------------------------------------------------------------- #
def _register_user_tool(sift: Sift, *, path: str, description: str, params: dict,
                        returns: list, code: str, valves: dict) -> None:
    """Registra a tool do usuário. O código NÃO roda aqui: a cada chamada ele é
    executado em subprocesso isolado (sandbox), com timeout e limites de recurso.

    Contrato: o código deve definir `def run(**params): ...` retornando algo
    serializável em JSON. As `valves` configuradas ficam disponíveis como global.
    """

    # garante que "error" sobreviva ao filtro de resposta do SIFT: senão, quando a
    # tool falha, o modelo recebe {} (a mensagem de erro é descartada pelo returns).
    returns = list(returns or [])
    if "error" not in returns:
        returns = returns + ["error"]

    @sift.tool(normalize_sift_path(path), description=description, params=params, returns=returns)
    def _wrapper(**kwargs):  # noqa: ANN003
        try:
            return run_in_subprocess(code, kwargs, valves)
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)}


# --------------------------------------------------------------------------- #
# Construção / cache
# --------------------------------------------------------------------------- #
def _signature(
    tool_rows: Iterable[Any], search_cfg: SearchConfig,
    finance_cfg: "finance.FinanceConfig | None" = None,
    deep_cfg: "deep_search.DeepSearchConfig | None" = None,
    google_cfg: "GoogleConfig | None" = None,
    tuya_cfg: "TuyaConfig | None" = None,
    github_cfg: "GithubConfig | None" = None,
    messaging_cfg: "MessagingConfig | None" = None,
    browser_cfg: dict | None = None,
    higgsfield_cfg: "HiggsfieldConfig | None" = None,
    notion_cfg: "NotionConfig | None" = None,
    slack_cfg: "SlackConfig | None" = None,
    elevenlabs_cfg: "ElevenLabsConfig | None" = None,
    vercel_cfg: "VercelConfig | None" = None,
    spotify_cfg: "SpotifyConfig | None" = None,
) -> tuple:
    rows = tuple(
        sorted(
            (str(t.id), t.path, t.enabled, t.updated_at.isoformat())
            for t in tool_rows
        )
    )
    fin = (
        (tuple(finance_cfg.providers), finance_cfg.web_fallback,
         bool(finance_cfg.finnhub_key), bool(finance_cfg.alphavantage_key),
         finance_cfg.default_card)
        if finance_cfg else ()
    )
    dp = (
        (deep_cfg.model, deep_cfg.max_subqueries, deep_cfg.max_rounds,
         deep_cfg.read_pages, deep_cfg.max_pages, bool(deep_cfg.api_key))
        if deep_cfg else ()
    )
    # prefs + contas liberadas entram na assinatura (o token NÃO): muda
    # config/conta → rebuild; refresh de token → sem rebuild.
    gg = (
        (google_cfg.require_confirm, google_cfg.max_results, google_cfg.default_calendar,
         tuple(sorted(google_cfg.ops.items())),
         tuple(sorted(str(a.get("id")) for a in google_cfg.accounts)))
        if google_cfg else ()
    )
    # config Tuya: conexão global (id + versão do catálogo) + gating por-modelo.
    # o access_secret não entra (só um bool); o catálogo entra por um hash barato.
    ty = (
        (tuya_cfg.conn.get("access_id", ""), tuya_cfg.conn.get("base_url", ""),
         bool(tuya_cfg.conn.get("access_secret")),
         hash(json.dumps(
             {k: tuya_cfg.conn.get(k) for k in ("devices", "aliases", "scenes")},
             sort_keys=True, default=str,
         )),
         tuya_cfg.require_confirm, tuple(sorted(tuya_cfg.ops.items())),
         tuple(sorted(str(d) for d in tuya_cfg.allowed_devices)))
        if tuya_cfg else ()
    )
    # config GitHub: contas liberadas (id) + ops + confirmação (token NÃO entra).
    ghc = (
        (github_cfg.require_confirm, tuple(sorted(github_cfg.ops.items())),
         tuple(sorted(str(a.get("id")) for a in github_cfg.accounts)))
        if github_cfg else ()
    )
    # config Mensagens: conexões liberadas (id+platform) + ops + confirmação.
    msgc = (
        (messaging_cfg.require_confirm, tuple(sorted(messaging_cfg.ops.items())),
         tuple(sorted((str(a.get("id")), a.get("platform", "")) for a in messaging_cfg.accounts)))
        if messaging_cfg else ()
    )
    # config Notion: contas liberadas (id) + ops + confirmação (token NÃO entra).
    ntc = (
        (notion_cfg.require_confirm, tuple(sorted(notion_cfg.ops.items())),
         tuple(sorted(str(a.get("id")) for a in notion_cfg.accounts)))
        if notion_cfg else ()
    )
    # config Slack: workspaces liberados (id) + ops + confirmação (token NÃO entra).
    slc = (
        (slack_cfg.require_confirm, tuple(sorted(slack_cfg.ops.items())),
         tuple(sorted(str(a.get("id")) for a in slack_cfg.accounts)))
        if slack_cfg else ()
    )
    # config ElevenLabs: a key identifica a conexão; voz/modelo entram (mudam → rebuild).
    elc = (
        (elevenlabs_cfg.conn.get("api_key", ""), elevenlabs_cfg.conn.get("model", ""),
         elevenlabs_cfg.conn.get("default_voice", ""))
        if elevenlabs_cfg else ()
    )
    # config Vercel: o token identifica a conexão (conectou/desconectou/trocou → rebuild).
    vlc = ((vercel_cfg.conn.get("token", ""),) if vercel_cfg else ())
    # config Spotify: o client_id identifica a conexão; o secret entra só como bool.
    spc = ((spotify_cfg.conn.get("id", ""), bool(spotify_cfg.conn.get("secret")))
           if spotify_cfg else ())
    return (
        rows,
        search_cfg.provider,
        search_cfg.searxng_url,
        tuple(search_cfg.providers),
        search_cfg.multi,
        search_cfg.max_results,
        tuple(search_cfg.domain_filter),
        fin,
        dp,
        gg,
        ty,
        ghc,
        msgc,
        # config do browser por-usuário: muda ws_url/token/enabled → rebuild (o
        # endpoint é capturado no closure da tool). O token entra aqui (é infra
        # local do próprio usuário, não um segredo de terceiros).
        ((browser_cfg or {}).get("ws_url", ""), (browser_cfg or {}).get("token", ""),
         (browser_cfg or {}).get("enabled", True)),
        # Higgsfield: a key identifica a conexão; o secret entra só como bool
        # (trocou key/conectou/desconectou → rebuild).
        ((higgsfield_cfg.conn.get("api_key", ""), bool(higgsfield_cfg.conn.get("api_secret")))
         if higgsfield_cfg else ()),
        ntc,
        slc,
        elc,
        vlc,
        spc,
    )


# Ferramentas REALMENTE executadas no turno — inclusive as chamadas por `call()` de
# DENTRO do run_code. Sem isto, o Modo Código é uma caixa-preta na conta de tokens: o
# detalhamento mostra só "run_code: 15.382" e o Gmail (que trouxe os 17 KB) fica
# invisível. O `on_result` da SIFT é um pós-filtro global que roda p/ TODA tool — o
# sandbox do run_code proxia as chamadas de volta a ESTE processo, então elas passam
# por aqui. O contextvar isola por turno (cada request tem seu próprio contexto), o que
# importa porque a instância Sift é CACHEADA e compartilhada entre turnos do usuário.
tool_calls_log: ContextVar[list | None] = ContextVar("sift_tool_calls_log", default=None)


def _record_call(path: str, result: Any) -> Any:
    """Hook `on_result` da SIFT: anota (path, tamanho) e devolve o resultado intacto."""
    log = tool_calls_log.get()
    if log is not None:
        try:
            blob = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str)
            log.append({"path": path, "chars": len(blob)})
        except (TypeError, ValueError):  # resultado exótico: registra sem tamanho
            log.append({"path": path, "chars": 0})
    return result


def _index_cache_path(user_id: str | None) -> str | None:
    """Arquivo .npz de cache do índice p/ o usuário (None = desabilitado)."""
    cache_dir = get_settings().sift_index_cache_dir
    if not cache_dir or not user_id:
        return None
    try:
        os.makedirs(cache_dir, exist_ok=True)
        return os.path.join(cache_dir, f"{user_id}.npz")
    except OSError as exc:
        logger.warning("Cache de índice SIFT indisponível (%s); seguindo sem", exc)
        return None


def build_user_sift(
    tool_rows: Iterable[Any], search_cfg: SearchConfig, user_id: str | None = None,
    finance_cfg: "finance.FinanceConfig | None" = None,
    deep_cfg: "deep_search.DeepSearchConfig | None" = None,
    google_cfg: "GoogleConfig | None" = None,
    tuya_cfg: "TuyaConfig | None" = None,
    github_cfg: "GithubConfig | None" = None,
    messaging_cfg: "MessagingConfig | None" = None,
    browser_cfg: dict | None = None,
    higgsfield_cfg: "HiggsfieldConfig | None" = None,
    notion_cfg: "NotionConfig | None" = None,
    slack_cfg: "SlackConfig | None" = None,
    elevenlabs_cfg: "ElevenLabsConfig | None" = None,
    vercel_cfg: "VercelConfig | None" = None,
    spotify_cfg: "SpotifyConfig | None" = None,
) -> Sift | None:
    """Constrói a instância SIFT completa do usuário (builtins + tools dele).

    O gating por modelo NÃO acontece aqui: a instância contém tudo e o chamador
    aplica `sift.scope(allow=[...])` (SIFT v0.4) para restringir visibilidade e
    execução por chat. Assim o índice (embeddings) é construído uma única vez
    por usuário, não uma vez por combinação de ferramentas.
    """
    try:
        s = get_settings()
        # Code mode: run_code executa em subprocesso isolado da SIFT (tool calls
        # proxiadas por stdio, env limpo, rlimits de CPU/memória). O meta-tool só
        # chega ao modelo quando o chamador envia code_tools() — a exposição é
        # decidida por modelo (ModelConfig.code_mode), não aqui.
        # index_cache: warm start do índice entre restarts (validado por hash na SIFT).
        sift = Sift(
            sandbox=SubprocessSandbox(
                timeout=float(s.sift_code_timeout_seconds),
                cpu_seconds=s.tool_cpu_seconds,
                memory_mb=s.sift_code_mem_mb,
            ),
            on_result=_record_call,
            index_cache=_index_cache_path(user_id),
        )
        _register_builtins(sift, search_cfg, None, finance_cfg, deep_cfg, user_id, google_cfg, tuya_cfg, github_cfg, messaging_cfg, browser_cfg, higgsfield_cfg, notion_cfg, slack_cfg, elevenlabs_cfg, vercel_cfg, spotify_cfg)
        for t in tool_rows:
            if not t.enabled:
                continue
            # integrações MCP ainda não executam (só config salva); não registra
            # na SIFT p/ o modelo não enxergar uma tool que não responde.
            if getattr(t, "tool_type", "code") == "mcp":
                continue
            try:
                _register_user_tool(
                    sift,
                    path=t.path,
                    description=t.description or "",
                    params=t.params or {},
                    returns=t.returns or [],
                    code=t.code or "",
                    valves=t.valves or {},
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Tool '%s' ignorada (erro de registro): %s", t.path, exc)
        sift.build_index()
        return sift
    except Exception as exc:  # noqa: BLE001
        logger.warning("SIFT indisponível (chat seguirá sem ferramentas): %s", exc)
        return None


def get_user_sift(
    user_id: str, tool_rows: list[Any], search_cfg: SearchConfig,
    finance_cfg: "finance.FinanceConfig | None" = None,
    deep_cfg: "deep_search.DeepSearchConfig | None" = None,
    google_cfg: "GoogleConfig | None" = None,
    tuya_cfg: "TuyaConfig | None" = None,
    github_cfg: "GithubConfig | None" = None,
    messaging_cfg: "MessagingConfig | None" = None,
    browser_cfg: dict | None = None,
    higgsfield_cfg: "HiggsfieldConfig | None" = None,
    notion_cfg: "NotionConfig | None" = None,
    slack_cfg: "SlackConfig | None" = None,
    elevenlabs_cfg: "ElevenLabsConfig | None" = None,
    vercel_cfg: "VercelConfig | None" = None,
    spotify_cfg: "SpotifyConfig | None" = None,
) -> Sift | None:
    sig = _signature(tool_rows, search_cfg, finance_cfg, deep_cfg, google_cfg, tuya_cfg, github_cfg, messaging_cfg, browser_cfg, higgsfield_cfg, notion_cfg, slack_cfg, elevenlabs_cfg, vercel_cfg, spotify_cfg)
    cached = _cache.get(user_id)
    if cached is not None and cached[0] == sig:
        return cached[1]
    sift = build_user_sift(tool_rows, search_cfg, user_id, finance_cfg, deep_cfg, google_cfg, tuya_cfg, github_cfg, messaging_cfg, browser_cfg, higgsfield_cfg, notion_cfg, slack_cfg, elevenlabs_cfg, vercel_cfg, spotify_cfg)
    _cache[user_id] = (sig, sift)
    return sift


def invalidate(user_id: str) -> None:
    _cache.pop(user_id, None)


def cache_info() -> dict[str, Any]:
    """Estado do cache de instâncias SIFT (para o painel de debug)."""
    out = []
    for uid, (_sig, sift) in _cache.items():
        tools = 0
        if sift is not None:
            try:
                tools = len(sift.openai_tools())
            except Exception:  # noqa: BLE001
                tools = -1
        out.append({"user_id": uid, "ready": sift is not None, "meta_tools": tools})
    return {"cached_users": len(_cache), "instances": out}


def search_config_from_secrets(
    tavily: str | None, brave: str | None, web_prefs: dict | None = None
) -> SearchConfig:
    """Config de busca: prefs do usuário (profile.web_search) sobrepõem o env."""
    s = get_settings()
    prefs = web_prefs or {}
    provider = (prefs.get("primary") or s.web_search_provider or "duckduckgo")
    try:
        max_results = int(prefs.get("max_results") or s.web_search_max_results)
    except (TypeError, ValueError):
        max_results = s.web_search_max_results
    max_results = max(1, min(max_results, 15))
    searxng_url = (prefs.get("searxng_url") or s.searxng_url)
    providers = tuple(p for p in (prefs.get("providers") or ()) if isinstance(p, str) and p)
    domain_filter = tuple(
        d.strip() for d in str(prefs.get("domain_filter") or "").split(",") if d.strip()
    )
    return SearchConfig(
        provider=provider,
        max_results=max_results,
        searxng_url=searxng_url,
        tavily_api_key=tavily,
        brave_api_key=brave,
        providers=providers,
        multi=bool(prefs.get("multi")),
        domain_filter=domain_filter,
    )


def finance_config_from_secrets(
    finnhub: str | None, alphavantage: str | None, finance_prefs: dict | None = None
) -> "finance.FinanceConfig":
    """Config de finanças: ordem de provedores (profile.finance) + chaves.

    `providers` é a ordem de tentativa; provedores sem chave são pulados. Yahoo é o
    padrão (sem chave). `web_fallback` tenta a pesquisa na web se todos falharem."""
    prefs = finance_prefs or {}
    provs = tuple(
        p for p in (prefs.get("providers") or ["yahoo"])
        if isinstance(p, str) and p in finance.VALID_PROVIDERS
    ) or ("yahoo",)
    # modo do card: "always" = mostra o card por padrão; qualquer outro (default) =
    # texto por padrão, card só quando o usuário pedir.
    return finance.FinanceConfig(
        providers=provs,
        finnhub_key=finnhub,
        alphavantage_key=alphavantage,
        web_fallback=bool(prefs.get("web_fallback")),
        default_card=(prefs.get("card_mode") == "always"),
    )


def deep_config_from_secrets(
    openrouter_key: str | None, deep_prefs: dict | None = None
) -> "deep_search.DeepSearchConfig":
    """Config do Deep Search: chave OpenRouter (p/ os passos internos) + prefs."""
    p = deep_prefs or {}

    def _int(k: str, d: int, lo: int, hi: int) -> int:
        try:
            return max(lo, min(int(p.get(k, d)), hi))
        except (TypeError, ValueError):
            return d

    return deep_search.DeepSearchConfig(
        api_key=openrouter_key,
        model=str(p.get("model") or ""),
        max_subqueries=_int("max_subqueries", 3, 1, 6),
        max_rounds=_int("max_rounds", 2, 1, 4),
        max_results_per_query=_int("max_results_per_query", 4, 1, 8),
        read_pages=p.get("read_pages", True) is not False,
        max_pages=_int("max_pages", 4, 0, 10),
        max_page_chars=_int("max_page_chars", 4000, 500, 12000),
    )


def google_config_from_secrets(
    user_id: str, accounts: list[dict] | None = None, google_prefs: dict | None = None,
    *, confirm_actions: bool = False,
) -> "GoogleConfig":
    """Config das tools Google. `accounts` = contas liberadas p/ este modelo
    ([{"id","email"}]); vazio → as tools existem mas respondem 'não conectado'.
    Confirmação de escritas (enviar/arquivar e-mail, mexer na agenda) é OPT-IN pelo
    perfil global do usuário (`confirm_actions`, Configurações → Segurança); por padrão
    a IA executa direto, sem pedir permissão."""
    p = google_prefs or {}

    def _int(k: str, d: int, lo: int, hi: int) -> int:
        try:
            return max(lo, min(int(p.get(k, d)), hi))
        except (TypeError, ValueError):
            return d

    return GoogleConfig(
        user_id=user_id,
        require_confirm=bool(confirm_actions),
        max_results=_int("max_results", 10, 1, 50),
        default_calendar=str(p.get("default_calendar") or "primary"),
        accounts=list(accounts or []),
        ops=p.get("ops") if isinstance(p.get("ops"), dict) else {},
    )


def tuya_config_from_secrets(
    conn: dict | None, tuya_prefs: dict | None = None, *, confirm_actions: bool = False,
) -> "TuyaConfig":
    """Config da tool Tuya. `conn` = conexão global (creds + catálogo) do app_settings;
    None → a tool existe mas responde 'não conectado'. Confirmação antes de acionar
    dispositivos é OPT-IN pelo perfil global (`confirm_actions`, Configurações →
    Segurança); por padrão aciona direto."""
    p = tuya_prefs or {}
    return TuyaConfig(
        conn=conn or {},
        allowed_devices=[str(d) for d in (p.get("devices") or [])],
        require_confirm=bool(confirm_actions),
        ops=p.get("ops") if isinstance(p.get("ops"), dict) else {},
    )


def higgsfield_config_from_secrets(conn: dict | None) -> "HiggsfieldConfig | None":
    """Config da tool Higgsfield. `conn` = {api_key, api_secret} decifrado do
    app_settings; None → a tool existe mas responde 'não conectado'."""
    if not conn:
        return None
    return HiggsfieldConfig(conn=conn)


def elevenlabs_config_from_secrets(conn: dict | None) -> "ElevenLabsConfig | None":
    """Config da tool ElevenLabs. `conn` = {api_key, model, default_voice} do
    app_settings; None → a tool existe mas responde 'não conectada'."""
    if not conn or not conn.get("api_key"):
        return None
    return ElevenLabsConfig(conn=conn)


def vercel_config_from_secrets(token: str | None) -> "VercelConfig | None":
    """Config da tool Vercel. `token` = Personal Access Token decifrado do UserSecret;
    None/vazio → a tool existe mas responde 'não conectada'."""
    if not token:
        return None
    return VercelConfig(conn={"token": token})


def spotify_config_from_secrets(creds: tuple[str, str] | None) -> "SpotifyConfig | None":
    """Config da tool Spotify. `creds` = (client_id, client_secret) decifrados do
    UserSecret; None → a tool existe mas responde 'não conectado'."""
    if not creds or not creds[0] or not creds[1]:
        return None
    return SpotifyConfig(conn={"id": creds[0], "secret": creds[1]})


def github_config_from_secrets(
    user_id: str, accounts: list[dict] | None = None, github_prefs: dict | None = None,
    *, confirm_actions: bool = False,
) -> "GithubConfig":
    """Config da tool GitHub. `accounts` = contas liberadas p/ este modelo
    ([{"id","login"}]); vazio → a tool existe mas responde 'não conectado'.
    Confirmação de escritas (criar issue/PR/comentário/commit) é OPT-IN pelo perfil
    global (`confirm_actions`, Configurações → Segurança)."""
    p = github_prefs or {}
    return GithubConfig(
        user_id=user_id,
        require_confirm=bool(confirm_actions),
        accounts=list(accounts or []),
        ops=p.get("ops") if isinstance(p.get("ops"), dict) else {},
    )


def notion_config_from_secrets(
    user_id: str, accounts: list[dict] | None = None, notion_prefs: dict | None = None,
    *, confirm_actions: bool = False,
) -> "NotionConfig":
    """Config da tool Notion. `accounts` = contas liberadas p/ este modelo
    ([{"id","workspace"}]); vazio → a tool existe mas responde 'não conectado'.
    Confirmação de escritas (criar página/adicionar conteúdo) é OPT-IN pelo perfil
    global (`confirm_actions`, Configurações → Segurança)."""
    p = notion_prefs or {}
    return NotionConfig(
        user_id=user_id,
        require_confirm=bool(confirm_actions),
        accounts=list(accounts or []),
        ops=p.get("ops") if isinstance(p.get("ops"), dict) else {},
    )


def slack_config_from_secrets(
    user_id: str, accounts: list[dict] | None = None, slack_prefs: dict | None = None,
    *, confirm_actions: bool = False,
) -> "SlackConfig":
    """Config da tool Slack. `accounts` = workspaces liberados p/ este modelo
    ([{"id","team"}]); vazio → a tool existe mas responde 'não conectado'.
    Confirmação de ENVIO é OPT-IN pelo perfil global (`confirm_actions`)."""
    p = slack_prefs or {}
    return SlackConfig(
        user_id=user_id,
        require_confirm=bool(confirm_actions),
        accounts=list(accounts or []),
        ops=p.get("ops") if isinstance(p.get("ops"), dict) else {},
    )


def messaging_config_from_secrets(
    user_id: str, accounts: list[dict] | None = None, messaging_prefs: dict | None = None,
    *, confirm_actions: bool = False,
) -> "MessagingConfig":
    """Config da tool de Mensagens. `accounts` = conexões liberadas p/ este modelo
    ([{"id","platform","label"}]); vazio → a tool existe mas responde 'nenhuma conexão'.
    Confirmação de ENVIO é OPT-IN pelo perfil global (`confirm_actions`, Configurações →
    Segurança); leitura/listagem nunca pedem confirmação."""
    p = messaging_prefs or {}
    return MessagingConfig(
        user_id=user_id,
        require_confirm=bool(confirm_actions),
        accounts=list(accounts or []),
        ops=p.get("ops") if isinstance(p.get("ops"), dict) else {},
    )


def run_user_code(code: str, params: dict[str, Any], valves: dict[str, Any] | None = None) -> Any:
    """Executa o código de uma tool no sandbox (para o botão 'testar')."""
    return run_in_subprocess(code, params, valves)


def valves_defaults(code: str) -> dict[str, Any]:
    """Defaults das valves (dict VALVES) declaradas no código."""
    return extract_valves(code)
