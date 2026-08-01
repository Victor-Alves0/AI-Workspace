"""Configuração central (lida de variáveis de ambiente / .env)."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Segurança / ambiente
    app_env: str = "development"  # development | production
    app_secret: str = "dev-insecure-secret-change-me"
    enable_signup: bool = True
    access_token_ttl_min: int = 30
    refresh_token_ttl_days: int = 30

    # Rate limiting de autenticação (tentativas por janela)
    login_max_attempts: int = 10
    login_window_seconds: int = 300
    # Só ligue atrás de um proxy reverso controlado (nginx/traefik): faz o rate
    # limit usar X-Forwarded-For. Sem proxy, o header é forjável pelo cliente.
    trust_proxy: bool = False

    # Sandbox de execução de tools
    tool_timeout_seconds: int = 10
    tool_cpu_seconds: int = 5
    tool_mem_mb: int = 256
    # Off-switch global do "code mode" (run_code): executa código GERADO PELO
    # MODELO no sandbox de subprocesso. É o vetor de maior risco (RCE por design).
    # O sandbox limita CPU/memória/tempo, mas NÃO isola rede nem /proc — um processo
    # filho (mesmo usuário) pode ler /proc/<ppid>/environ (APP_SECRET, DATABASE_URL).
    # Deixe False em deploys multiusuário não confiáveis, ou isole o sandbox
    # (rede desligada, hidepid, nsjail/gVisor) e entregue segredos por arquivo.
    allow_code_mode: bool = True

    # Sandbox do code mode da SIFT (run_code). A partir da SIFT 0.4.1 o processo
    # filho não importa mais o pacote sift (numpy/gateway ficam fora), então
    # 256MB bastam e o boot é ~0.2s. Margem folgada aqui p/ snippets do modelo.
    # Tools chamadas via call() no sandbox contam contra este teto de parede; as
    # MUITO longas (pesquisa profunda) são promovidas a 1ª classe fora do sandbox
    # (loader._CODE_MODE_PROMOTE) — 30s cobre as médias (page.read ~12s, Google).
    sift_code_timeout_seconds: int = 30
    sift_code_mem_mb: int = 512

    # Limites de input
    max_message_chars: int = 100_000
    max_tool_code_chars: int = 50_000

    # Banco
    database_url: str = (
        "postgresql+asyncpg://aiworkspace:aiworkspace@localhost:5432/aiworkspace"
    )

    # OpenRouter
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    # Identificação do app enviada ao OpenRouter (aparece no dashboard dele).
    # Nome = X-Title; URL = HTTP-Referer (evite localhost para não aparecer como tal).
    openrouter_app_name: str = "AI Workspace"
    openrouter_app_url: str = "https://ai-workspace.app"

    # Web search
    # provider padrão: searxng (self-hosted, sem chave). Alternativas: tavily, brave, duckduckgo
    web_search_provider: str = "duckduckgo"
    searxng_url: str = "http://localhost:8080"
    web_search_max_results: int = 5

    # Voz (TTS/STT) — endpoint compatível com OpenAI (/audio/speech, /audio/transcriptions).
    # Padrão OpenAI; pode apontar p/ servidores locais (openedai-speech, faster-whisper-server).
    voice_base_url: str = "https://api.openai.com/v1"
    tts_model: str = "tts-1"
    tts_voice: str = "alloy"
    stt_model: str = "whisper-1"

    # CORS
    web_origin: str = "http://localhost:3000"

    # Integração Google Workspace (OAuth). O Client ID/Secret são configurados na
    # UI (Integrações → Google Workspace, admin) e ficam em app_settings — NÃO no
    # env. Só o redirect_uri fica aqui (não é segredo); precisa estar registrado no
    # console do Google e ser alcançado via localhost (política do Google).
    google_redirect_uri: str = "http://localhost:8000/integrations/google/callback"

    # Integração GitHub (OAuth App, opcional — o caminho principal é colar um PAT).
    # Client ID/Secret ficam em app_settings (UI); só o redirect_uri fica aqui e
    # precisa bater com o cadastrado no OAuth App do GitHub.
    github_redirect_uri: str = "http://localhost:8000/integrations/github/callback"

    # Integração Notion (OAuth público, opcional — o caminho principal é colar um
    # token de integração interna). Client ID/Secret ficam em app_settings (UI); só
    # o redirect_uri fica aqui e precisa bater com o cadastrado na integração Notion.
    notion_redirect_uri: str = "http://localhost:8000/integrations/notion/callback"

    # Integração Slack (tool: OAuth v2, opcional — o caminho principal é colar um Bot
    # User OAuth Token). Client ID/Secret ficam em app_settings (UI); só o redirect_uri
    # fica aqui e precisa bater com o cadastrado no app do Slack.
    slack_redirect_uri: str = "http://localhost:8000/integrations/slack/callback"

    # Integração WhatsApp
    # Evolution API (caminho NÃO oficial, QR Code): serviço opt-in do compose
    # (`--profile whatsapp`). Vazio = caminho não oficial indisponível (o oficial
    # via Meta Cloud API funciona sem nada disso).
    evolution_api_url: str = ""
    evolution_api_key: str = ""
    # Base das URLs de webhook geradas. Para o Evolution (rede interna do compose)
    # o padrão resolve; para o Cloud API da Meta é preciso uma URL PÚBLICA https —
    # defina aqui o endereço externo do server (túnel/proxy reverso).
    whatsapp_webhook_base: str = "http://server:8000"

    # Navegador headless (tool "web.browser.use"): Chromium num serviço opt-in do
    # compose (`--profile browser`, browserless). O server dirige por Playwright via
    # CDP; vazio = a tool avisa que o serviço não está ativo. Token = BROWSER_TOKEN.
    browser_ws_url: str = ""
    browser_token: str = ""

    # Codespace: onde os repositórios/worktrees dos projetos vivem no disco. No
    # compose é o volume `codespace_data` montado em /data/codespace; no desktop
    # (Windows, sem Docker) o Start-AIWorkspace.ps1 aponta para <DataDir>\codespace.
    # Hardcodar "/data/codespace" quebrava o Codespace fora do Linux/Docker.
    codespace_data_dir: str = "/data/codespace"

    # Sandbox de execução do Codespace (tool "code.exec.run"): roda testes/build do
    # projeto. Baseline UNIVERSAL = subprocesso no host (funciona em Win/Linux/desktop,
    # sem Docker). `code_runner_url` (opt-in) encaminha para um container `runner`
    # (--profile runner) com isolamento mais forte; vazio = subprocesso no host.
    code_runner_url: str = ""
    code_runner_token: str = ""
    # caps do exec (host): timeout por comando, CPU (rlimit Linux) e teto de saída.
    code_exec_timeout_seconds: int = 900
    code_exec_cpu_seconds: int = 600
    code_exec_output_bytes: int = 200_000
    # execução em BACKGROUND (comandos longos: download/instalação/build): teto que o
    # `code.exec.jobs wait` pode aguardar inline por um job antes de devolver "ainda
    # rodando" (o job segue vivo; o agente pode soltar o turno e ser acordado no fim).
    code_exec_bg_wait_ceiling_seconds: int = 1200
    # preview vivo (dev server/backend do projeto no ar): idade máxima antes do reaper
    # derrubar (6h) e teto de servidores no ar simultâneos por usuário.
    code_preview_max_age_seconds: int = 21_600
    code_preview_max_per_user: int = 4
    # ciclo de vida dos worktrees: TTL de ociosidade (reaper) e teto por usuário.
    codespace_worktree_ttl_seconds: int = 86_400
    codespace_max_worktrees_per_user: int = 20

    # Transcrição de vídeo (tool "media.video.transcribe" / yt-dlp): anti-bloqueio.
    # YouTube & afins barram scraping repetido do mesmo cliente ("Sign in to confirm
    # you're not a bot", HTTP 429). `transcribe_cookies_dir` aponta p/ uma pasta de
    # arquivos cookies.txt (formato Netscape) — CADA arquivo é uma identidade e o pool
    # rotaciona a menos-recente-usada, colocando em cooldown a que tomar bloqueio (não
    # bate sempre na mesma). Vazio = só headers realistas (User-Agent de navegador),
    # sem cookies. `transcribe_cookie_cooldown_seconds` é o tempo de banco após bloqueio
    # (com backoff por falhas consecutivas).
    transcribe_cookies_dir: str = ""
    transcribe_cookie_cooldown_seconds: int = 1800

    # Prompt caching (OpenRouter → Anthropic/Gemini/DeepSeek): marca breakpoints de
    # cache_control no system, no bloco de tools e no fim do histórico, para o prefixo
    # ESTÁVEL (que se repete a cada iteração do loop agêntico) ser cobrado a fração do
    # preço. Modelos que não suportam ignoram os marcadores. Desligue se algum provedor
    # reclamar do formato de conteúdo em blocos.
    prompt_cache_enabled: bool = True

    # Limite de iterações de tool-calling por turno. O padrão (8) cobre um chat
    # normal; um chat de Codespace roda o loop agêntico de código (escreve → testa
    # → corrige), que precisa de dezenas de passos como Codex/Claude Code/opencode —
    # por isso tem um teto próprio, bem mais alto, aplicado só nesses chats.
    max_tool_iterations: int = 8
    codespace_max_tool_iterations: int = 40

    # Cache do índice SIFT (.npz por usuário) — string vazia desabilita.
    # A SIFT valida por hash de conteúdo+modelo, então cache velho é ignorado.
    sift_index_cache_dir: str = "/home/app/.cache/sift-index"

    # Observabilidade (traces/spans fim-a-fim persistidos no Postgres).
    # `obs_enabled` desliga tudo (nenhum trace é gravado). `obs_sample_rate` é a
    # fração amostrada [0..1] — 1.0 = toda chamada (erros e lentas são sempre
    # mantidos, independente da amostra). Retenção poda por dias e por teto de
    # linhas. `obs_capture_content` (padrão OFF) libera texto de mensagens/prompts
    # nos spans — só ligue para depurar, é o dado sensível.
    obs_enabled: bool = True
    obs_sample_rate: float = 1.0
    obs_retention_days: int = 14
    obs_max_traces: int = 500_000
    obs_capture_content: bool = False
    # limiar (ms) acima do qual um trace é considerado "lento" e sempre mantido,
    # mesmo quando a amostragem descartaria
    obs_slow_ms: int = 1500

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() in ("production", "prod")

    @property
    def secret_is_insecure(self) -> bool:
        return self.app_secret in ("", "dev-insecure-secret-change-me") or len(
            self.app_secret
        ) < 16

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.web_origin.split(",") if o.strip()]

    @property
    def cors_origin_regex(self) -> str | None:
        """Em desenvolvimento (local-first), reflete origens da LAN — localhost e IPs
        privados (10/8, 192.168/16, 172.16/12) em qualquer porta — para abrir o app
        pelo IP da máquina (ex.: pelo celular). Em produção retorna None: só as
        origens EXATAS de WEB_ORIGIN são aceitas (sem afrouxar CORS na internet)."""
        if self.is_production:
            return None
        return (
            r"^https?://("
            r"localhost|127\.0\.0\.1|\[::1\]|"
            r"10\.\d{1,3}\.\d{1,3}\.\d{1,3}|"
            r"192\.168\.\d{1,3}\.\d{1,3}|"
            r"172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}"
            r")(:\d+)?$"
        )

    @property
    def sync_database_url(self) -> str:
        """URL síncrona (psycopg) usada pelo Alembic."""
        return self.database_url.replace("+asyncpg", "")


@lru_cache
def get_settings() -> Settings:
    return Settings()
