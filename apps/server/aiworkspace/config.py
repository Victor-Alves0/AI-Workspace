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

    # Limite de iterações de tool-calling por turno
    max_tool_iterations: int = 8

    # Cache do índice SIFT (.npz por usuário) — string vazia desabilita.
    # A SIFT valida por hash de conteúdo+modelo, então cache velho é ignorado.
    sift_index_cache_dir: str = "/home/app/.cache/sift-index"

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
