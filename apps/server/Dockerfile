FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# dependências de sistema (psycopg2, build de wheels nativas) + OCR:
# tesseract (motor OCR local, idiomas pt+en) e poppler (rasteriza PDF p/ OCR).
# git: clona os projetos do Codespace (tool code.graph/code.files) via HTTPS/SSH.
# openssh-client: `ssh`/`ssh-keygen` — projetos Codespace por SSH (deploy key
# gerada por-projeto, ver graph_service.generate_ssh_keypair).
# ripgrep: o "grep" da tool code.files.browse (action=search) — regex de verdade,
# detecção de binário e velocidade que a varredura em Python não dá. Sem ele o
# graph_service cai num fallback Python mais fraco (não quebra, só piora).
# ffmpeg: caminho de FALA da tool media.video.transcribe — recomprime/fatia o áudio
# baixado (16 kHz mono) p/ caber no teto do STT. Sem ele só o caminho de legendas roda.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential libpq-dev curl ca-certificates git openssh-client ripgrep ffmpeg \
        tesseract-ocr tesseract-ocr-por tesseract-ocr-eng poppler-utils \
    && rm -rf /var/lib/apt/lists/*

# postgresql-client-16 (pg_dump/pg_restore do backup completo no painel admin).
# Vem do repo oficial PGDG: o Debian do python:3.12-slim traz o client 15, que
# se RECUSA a dumpar um servidor Postgres 16.
RUN install -d /usr/share/postgresql-common/pgdg \
    && curl -fsSo /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
        https://www.postgresql.org/media/keys/ACCC4CF8.asc \
    && . /etc/os-release \
    && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] http://apt.postgresql.org/pub/repos/apt ${VERSION_CODENAME}-pgdg main" \
        > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client-16 \
    && rm -rf /var/lib/apt/lists/*

# mise: gerenciador de versões ROOTLESS (JDK/Maven/Node/Python/Go/…) para o sandbox
# de execução do Codespace. A IA instala toolchains sob demanda (ex.: `mise use -g
# java@17 maven`) sem root; ficam num cache PERSISTENTE dentro do volume codespace_data
# (ver MISE_DATA_DIR abaixo), sobrevivendo a rebuild/restart. É o que permite "baixar,
# instalar e usar" um JDK como Codex/Claude fazem, mas isolado do resto do server.
RUN curl -fsSL https://mise.run | MISE_INSTALL_PATH=/usr/local/bin/mise sh \
    && chmod 0755 /usr/local/bin/mise

COPY pyproject.toml ./
# instala só as DEPENDÊNCIAS numa camada própria, chaveada apenas pelo pyproject.
# Um stub do pacote (só __init__.py) faz o setuptools resolver; assim as deps
# ficam em cache e NÃO são reinstaladas/rebaixadas quando só o código muda.
RUN mkdir -p aiworkspace && touch aiworkspace/__init__.py \
    && pip install --upgrade pip && pip install .

# agora o código real; reinstala só o pacote (sem tocar nas deps, sem rede)
COPY aiworkspace ./aiworkspace
RUN pip install --no-deps --force-reinstall .

COPY alembic.ini ./
COPY alembic ./alembic
COPY entrypoint.sh ./
RUN chmod +x entrypoint.sh

# usuário não-root: o servidor (e o sandbox de tools, que herda o usuário)
# não roda como root dentro do container. HOME gravável p/ caches de modelos
# (fastembed/huggingface) usados pela SIFT e pelo mem0. /data/codespace guarda
# as working copies + índices dos projetos do Codespace (volume codespace_data).
RUN useradd --create-home --uid 10001 app \
    && mkdir -p /home/app/.cache /data/codespace \
    && chown -R app:app /home/app /data/codespace
USER app
# HOME/caches de modelos (fastembed→/tmp por padrão; redirecionados p/ o volume mlcache).
# mise: dirs no volume codespace_data (persistem os toolchains instalados); MISE_YES
# instala sem prompt; os shims entram no PATH p/ os binários resolverem a versão
# escolhida (ex.: `java` → o JDK do `mise use`). O `mise` fica em /usr/local/bin. O
# sandbox de exec herda este env (o env limpo só remove segredos, preserva PATH/MISE_*).
# NOTA: nada de comentário DENTRO deste ENV — comentário no meio de continuação `\`
# quebra a instrução no BuildKit.
ENV HOME=/home/app \
    FASTEMBED_CACHE_PATH=/home/app/.cache/fastembed \
    HF_HOME=/home/app/.cache/huggingface \
    MISE_DATA_DIR=/data/codespace/.mise/data \
    MISE_CONFIG_DIR=/data/codespace/.mise/config \
    MISE_CACHE_DIR=/data/codespace/.mise/cache \
    MISE_STATE_DIR=/data/codespace/.mise/state \
    MISE_YES=1 \
    PATH=/data/codespace/.mise/data/shims:/home/app/.local/bin:/usr/local/bin:${PATH}

# Commit de onde esta imagem foi construída. Fica no FIM de propósito: muda a cada
# build e invalidaria o cache das camadas pesadas se viesse antes.
# Serve p/ a checagem de atualização comparar COMMIT a commit — o `update.sh` puxa o
# BRANCH, então comparar só a tag de release dizia "atualizado" mesmo havendo código
# novo. Vazio (build manual sem o arg) = cai na comparação por release.
ARG GIT_COMMIT=""
ENV GIT_COMMIT=${GIT_COMMIT}

EXPOSE 8000
CMD ["./entrypoint.sh"]
