#!/usr/bin/env sh
# Atalho para rodar o motor a mao (fora do app desktop): sobe banco + API + interface
# e abre o navegador. Argumentos extras vao para o launcher (ex.: --data-dir, --port).
HERE="$(cd "$(dirname "$0")" && pwd)"
exec "$HERE/python/bin/python3" "$HERE/launcher.py" "$@"
