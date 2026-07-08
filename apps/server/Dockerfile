FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# dependências de sistema (psycopg2, build de wheels nativas) + OCR:
# tesseract (motor OCR local, idiomas pt+en) e poppler (rasteriza PDF p/ OCR)
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential libpq-dev curl \
        tesseract-ocr tesseract-ocr-por tesseract-ocr-eng poppler-utils \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
# instala o projeto + dependências
COPY aiworkspace ./aiworkspace
RUN pip install --upgrade pip && pip install .

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
