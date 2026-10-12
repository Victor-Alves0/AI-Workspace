#!/usr/bin/env bash
# Monta o "engine bundle" do AI Workspace para Linux (equivalente ao build-engine.ps1).
#
# Produz uma pasta autocontida com Python (python-build-standalone) + backend instalado
# + Postgres 16 + pgvector + interface estatica, que o launcher.py sobe sem depender de
# nada instalado no SO alem da glibc. Feito para rodar no runner `ubuntu-22.04`: a glibc
# mais antiga do build e' a minima exigida no usuario (binario feito em glibc nova nao
# roda em distro velha; o contrario roda).
#
# Uso (na raiz do repo):  bash desktop/engine/build-engine-linux.sh [--out DIR] [--work DIR] [--zip]
# Saida padrao:  desktop/engine/out-linux/aiworkspace-engine/
#
# Precisa no host: curl, tar, gcc, make, bison, flex, patchelf, node 20+ (npm), zlib (headers).
#
# Diferencas para o Windows, de proposito:
#   - Postgres compilado do fonte: nao ha binario portatil oficial para Linux, e o
#     pgvector precisa do pg_config + headers do MESMO build para compilar.
#   - Sem site-packages.zip (pack_site.py): no Windows ele existe pelo antivirus/instalador
#     tratarem arquivo a arquivo; no Linux o AppImage ja' e' um squashfs comprimido.
#   - Template do banco em .tar (preserva as permissoes 0700 que o Postgres exige).
set -euo pipefail

PYTHON_VERSION="3.12.15"
PBS_TAG="20261009"           # release do astral-sh/python-build-standalone
PG_VERSION="16.15"
# mesma versao do pgvector do motor Windows: um backup feito num SO restaura no outro
PGVECTOR_VERSION="0.8.3"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/../.." && pwd)"
SERVER_DIR="$REPO_ROOT/apps/server"
WEB_DIR="$REPO_ROOT/apps/web"

WORK="$HERE/out-linux"
OUT=""
APP_VERSION=""
ZIP=0
SKIP_WEB=0
while [ $# -gt 0 ]; do
    case "$1" in
        --out) OUT="$2"; shift 2 ;;
        # pasta de trabalho (downloads/compilacao). Precisa ser um FS Linux de verdade:
        # o initdb do template recusa NTFS/exFAT (sem permissao 0700).
        --work) WORK="$2"; shift 2 ;;
        --app-version) APP_VERSION="$2"; shift 2 ;;
        --zip) ZIP=1; shift ;;
        # reaproveita um export estatico ja' feito (apps/web/out) — util em iteracao local
        --skip-web) SKIP_WEB=1; shift ;;
        *) echo "argumento desconhecido: $1" >&2; exit 2 ;;
    esac
done
OUT="${OUT:-$WORK/aiworkspace-engine}"
DL="$WORK/_dl"
JOBS="$(nproc 2>/dev/null || echo 2)"

rm -rf "$OUT"
mkdir -p "$OUT" "$DL"

get() {  # get URL DESTINO — com cache: rodar de novo nao baixa outra vez
    if [ -s "$2" ]; then return; fi
    echo "==> baixando $1"
    curl -fSL --retry 3 -o "$2.part" "$1"
    mv "$2.part" "$2"
}

# ----------------------------------------------------------------------------- #
# 1) Python (python-build-standalone) + backend instalado nele
#
# Diferente do embeddable do Windows, e' um CPython completo e relocavel (o que o uv
# usa): o pip roda nele mesmo, entao os wheels batem com o interpretador que executa.
# ----------------------------------------------------------------------------- #
PY_TGZ="$DL/python-$PYTHON_VERSION-$PBS_TAG.tar.gz"
get "https://github.com/astral-sh/python-build-standalone/releases/download/$PBS_TAG/cpython-$PYTHON_VERSION+$PBS_TAG-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz" "$PY_TGZ"
tar -xzf "$PY_TGZ" -C "$OUT"   # extrai em $OUT/python
PY="$OUT/python/bin/python3"

echo "==> instalando o backend e dependencias (pode demorar)"
"$PY" -m pip install --quiet --upgrade pip
# [whatsapp-local]: WhatsApp por QR embutido (neonize). --no-compile: so' vira .pyc o
# que o app importa (pre-compilacao abaixo), nao os ~11 mil modulos instalados.
"$PY" -m pip install --quiet --no-compile "$SERVER_DIR[whatsapp-local]"
rm -rf "$SERVER_DIR/build"  # sobra do build em arvore do setuptools, nao versionar

SITE="$("$PY" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"
# suites de teste dos pacotes nao rodam em producao. So' "tests"/"test": "testing"
# (numpy.testing, sqlalchemy.testing) pode ser importada em tempo de execucao.
find "$SITE" -type d \( -name tests -o -name test \) -prune -exec rm -rf {} +
find "$OUT/python" -type d -name __pycache__ -prune -exec rm -rf {} +
# o pip nao roda na maquina do usuario: sai o pip e a suite de testes da stdlib
"$PY" -m pip uninstall --quiet -y pip
rm -rf "$OUT/python/lib/python3.12/test" "$OUT/python/lib/python3.12/idlelib" \
       "$OUT/python/lib/python3.12/tkinter" "$OUT/python/lib/python3.12/turtledemo"

echo "==> pre-compilando os modulos que o app carrega"
APP_SECRET="build-only-$(printf 'x%.0s' $(seq 32))" "$PY" - <<'EOF'
import importlib
for m in ('aiworkspace.main', 'aiworkspace.desktop', 'fastembed', 'onnxruntime', 'neonize',
          'aiworkspace.memory.memory_service', 'yt_dlp', 'phonenumbers', 'PIL', 'codegraph',
          'aiworkspace.integrations.voice_builtin', 'kokoro_onnx'):
    importlib.import_module(m)
EOF

# ----------------------------------------------------------------------------- #
# 2) Postgres 16 (do fonte) + pgvector
# ----------------------------------------------------------------------------- #
PG_TBZ="$DL/postgresql-$PG_VERSION.tar.bz2"
get "https://ftp.postgresql.org/pub/source/v$PG_VERSION/postgresql-$PG_VERSION.tar.bz2" "$PG_TBZ"
PG_SRC="$WORK/_pgsrc"
PG_DIR="$OUT/pgsql"
rm -rf "$PG_SRC"; mkdir -p "$PG_SRC"
tar -xjf "$PG_TBZ" -C "$PG_SRC" --strip-components=1
echo "==> compilando o Postgres $PG_VERSION"
(
    cd "$PG_SRC"
    # sem readline/ICU/openssl: o app so' fala com o banco pelo loopback, e cada lib a
    # menos e' uma dependencia a menos na distro do usuario. --disable-rpath: o rpath
    # absoluto do prefixo e' trocado abaixo por um relativo ($ORIGIN) => pasta relocavel.
    ./configure --quiet --prefix="$PG_DIR" --disable-rpath \
        --without-readline --without-icu --without-openssl --without-llvm
    make -s -j"$JOBS"
    make -s install
) >"$WORK/pg-build.log" 2>&1 || { tail -40 "$WORK/pg-build.log"; exit 1; }

PV_TGZ="$DL/pgvector-$PGVECTOR_VERSION.tar.gz"
get "https://github.com/pgvector/pgvector/archive/refs/tags/v$PGVECTOR_VERSION.tar.gz" "$PV_TGZ"
PV_SRC="$WORK/_pvsrc"
rm -rf "$PV_SRC"; mkdir -p "$PV_SRC"
tar -xzf "$PV_TGZ" -C "$PV_SRC" --strip-components=1
echo "==> compilando o pgvector $PGVECTOR_VERSION"
# OPTFLAGS="": o padrao do pgvector e' -march=native — o .so sairia com instrucoes da
# CPU do runner e cairia com "illegal instruction" numa CPU mais antiga.
make -s -C "$PV_SRC" PG_CONFIG="$PG_DIR/bin/pg_config" OPTFLAGS="" -j"$JOBS" >>"$WORK/pg-build.log" 2>&1
make -s -C "$PV_SRC" PG_CONFIG="$PG_DIR/bin/pg_config" OPTFLAGS="" install >>"$WORK/pg-build.log" 2>&1

# rpath relativo: bin/* acha a libpq em ../lib, e as libs se acham entre si. O Postgres
# ja' resolve share/ e lib/postgresql relativos ao proprio executavel (relocavel).
for f in "$PG_DIR"/bin/*; do
    if [ "$(head -c4 "$f" | tail -c3)" = "ELF" ]; then patchelf --set-rpath '$ORIGIN/../lib' "$f"; fi
done
for f in "$PG_DIR"/lib/*.so* "$PG_DIR"/lib/postgresql/*.so; do
    if [ -f "$f" ] && [ ! -L "$f" ]; then patchelf --set-rpath '$ORIGIN:$ORIGIN/..' "$f"; fi
done
# so' o que roda: headers, PGXS, estaticas e docs serviram ao pgvector e saem
rm -rf "$PG_DIR/include" "$PG_DIR/lib/pgxs" "$PG_DIR/share/doc" "$PG_DIR/share/man"
find "$PG_DIR/lib" -name '*.a' -delete
strip --strip-unneeded "$PG_DIR"/bin/* "$PG_DIR"/lib/*.so* "$PG_DIR"/lib/postgresql/*.so 2>/dev/null || true

# ----------------------------------------------------------------------------- #
# 3) Frontend: export ESTATICO (sem Node no motor) — igual ao Windows
# ----------------------------------------------------------------------------- #
if [ "$SKIP_WEB" = 0 ] || [ ! -d "$WEB_DIR/out" ]; then
    echo "==> build do frontend (npm ci + export estatico)"
    (
        cd "$WEB_DIR"
        export NEXT_OUTPUT=export NEXT_PUBLIC_API_URL=/api
        npm ci --no-audit --no-fund
        npm run build
    )
fi
cp -r "$WEB_DIR/out" "$OUT/web"

# ----------------------------------------------------------------------------- #
# 4) app dir (alembic) + launcher
# ----------------------------------------------------------------------------- #
mkdir -p "$OUT/app"
cp "$SERVER_DIR/alembic.ini" "$OUT/app/"
cp -r "$SERVER_DIR/alembic" "$OUT/app/alembic"
find "$OUT/app" -type d -name __pycache__ -prune -exec rm -rf {} +
cp "$HERE/launcher.py" "$HERE/start-aiworkspace.sh" "$OUT/"
chmod +x "$OUT/start-aiworkspace.sh"
cp "$HERE/README-linux.txt" "$OUT/README.txt"

# ----------------------------------------------------------------------------- #
# 5a) banco PRONTO: initdb + pgvector + todas as migracoes, feito uma vez aqui.
# Na 1a abertura o launcher so' extrai este tar (bem mais rapido que initdb+migracoes).
# ----------------------------------------------------------------------------- #
echo "==> banco pre-migrado (pgdata-template.tar)"
TPL="$WORK/_pgtemplate"
TPL_PORT=55499
rm -rf "$TPL"
"$PG_DIR/bin/initdb" -D "$TPL" -U aiworkspace -A trust -E UTF8 --locale=C >/dev/null
"$PG_DIR/bin/pg_ctl" -D "$TPL" -w -s -l "$WORK/pgtemplate.log" \
    -o "-p $TPL_PORT -c listen_addresses=127.0.0.1 -c unix_socket_directories=''" start
stop_tpl() { "$PG_DIR/bin/pg_ctl" -D "$TPL" -w -s -m fast stop || true; }
trap stop_tpl EXIT
"$PG_DIR/bin/createdb" -h 127.0.0.1 -p $TPL_PORT -U aiworkspace aiworkspace
"$PG_DIR/bin/psql" -h 127.0.0.1 -p $TPL_PORT -U aiworkspace -d aiworkspace -q \
    -v ON_ERROR_STOP=1 -c "CREATE EXTENSION IF NOT EXISTS vector"
(
    cd "$OUT/app"
    DATABASE_URL="postgresql+asyncpg://aiworkspace:aiworkspace@127.0.0.1:$TPL_PORT/aiworkspace" \
    APP_SECRET="build-only-$(printf 'x%.0s' $(seq 32))" \
        "$PY" -m alembic upgrade head
)
stop_tpl; trap - EXIT
rm -f "$TPL/postmaster.opts"
tar -cf "$OUT/pgdata-template.tar" -C "$TPL" .
rm -rf "$TPL"
# o alembic gravou __pycache__ nas migracoes: sai (a atualizacao leve espelha app/)
find "$OUT/app" -type d -name __pycache__ -prune -exec rm -rf {} +

# ----------------------------------------------------------------------------- #
# 5b) carimbo de versoes (auto-atualizacao em 2 camadas) — mesmo esquema do Windows
# ----------------------------------------------------------------------------- #
ENGINE_VERSION="$(tr -d '[:space:]' < "$HERE/ENGINE_VERSION")"
if [ -z "$APP_VERSION" ]; then
    APP_VERSION="$(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null || date +%Y%m%d%H%M%S)"
fi
printf '%s' "$ENGINE_VERSION" > "$OUT/engine-version.txt"
printf '%s' "$APP_VERSION"    > "$OUT/app-version.txt"
echo "==> engine-version=$ENGINE_VERSION  app-version=$APP_VERSION"
echo "==> bundle montado em $OUT ($(du -sh "$OUT" | cut -f1))"

if [ "$ZIP" = 1 ]; then
    TGZ="$WORK/aiworkspace-engine-linux-x86_64.tar.gz"
    tar -czf "$TGZ" -C "$(dirname "$OUT")" "$(basename "$OUT")"
    echo "==> pacote em $TGZ"
fi
