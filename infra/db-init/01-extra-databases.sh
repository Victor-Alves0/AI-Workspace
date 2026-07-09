#!/bin/bash
# Roda apenas no PRIMEIRO init do volume do Postgres: cria os bancos extras
# usados por serviços opcionais do compose (hoje: Evolution API / WhatsApp).
set -e
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<'EOSQL'
CREATE DATABASE evolution;
EOSQL
