FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# dependências de sistema (psycopg2, build de wheels nativas) + OCR:
# tesseract (motor OCR local, idiomas pt+en) e poppler (rasteriza PDF p/ OCR)
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential libpq-dev curl ca-certificates \
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
# (fastembed/huggingface) usados pela SIFT e pelo mem0.
RUN useradd --create-home --uid 10001 app \
    && mkdir -p /home/app/.cache \
    && chown -R app:app /home/app
USER app
ENV HOME=/home/app \
    # caches de modelos dentro do volume mlcache (fastembed baixa p/ /tmp por padrão)
    FASTEMBED_CACHE_PATH=/home/app/.cache/fastembed \
    HF_HOME=/home/app/.cache/huggingface

EXPOSE 8000
CMD ["./entrypoint.sh"]
