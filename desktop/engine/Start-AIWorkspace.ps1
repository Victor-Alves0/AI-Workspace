# Inicia o AI Workspace SEM Docker: sobe o Postgres embarcado e o backend Python,
# aplica as migracoes e serve a API em http://127.0.0.1:8000.
#
# Tudo e relativo a esta pasta e nada e instalado no sistema: o banco, os caches de
# modelo e o segredo do app ficam em .\data\. Apagar a pasta = zerar tudo.
#
# Este e o "motor" da Etapa 1 do app embarcado. O shell desktop (Tauri) vai chamar
# exatamente esta mesma sequencia numa etapa seguinte; por ora da pra rodar a mao
# para provar que sobe sem Docker.

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path

$Py     = Join-Path $Root "python\python.exe"
$PgBin  = Join-Path $Root "pgsql\bin"
$AppDir = Join-Path $Root "app"
$Data   = Join-Path $Root "data"
$PgData = Join-Path $Data "pgdata"
$PgPort = 55432
$ApiHost = "127.0.0.1"
$ApiPort = 8000

New-Item -ItemType Directory -Force $Data | Out-Null
New-Item -ItemType Directory -Force (Join-Path $Data "cache") | Out-Null

# --- segredo do app: gerado uma vez e reusado (deriva a chave que cifra os
#     segredos por-usuario no banco; trocar tornaria o banco ilegivel) ---
$SecretFile = Join-Path $Data "secret.txt"
if (-not (Test-Path $SecretFile)) {
    (& $Py -c "import secrets;print(secrets.token_urlsafe(48))").Trim() |
        Out-File -Encoding ascii -NoNewline $SecretFile
}
$env:APP_SECRET = (Get-Content $SecretFile -Raw).Trim()

# --- ambiente do backend: tudo apontando pra dentro de .\data\ ---
$env:DATABASE_URL        = "postgresql+asyncpg://aiworkspace:aiworkspace@$($ApiHost):$PgPort/aiworkspace"
$env:FASTEMBED_CACHE_PATH = Join-Path $Data "cache\fastembed"
$env:HF_HOME             = Join-Path $Data "cache\huggingface"
# pg precisa das DLLs de pgsql\bin no PATH (libpq, ICU, etc.)
$env:PATH = "$PgBin;$env:PATH"

function Pg($exe) { Join-Path $PgBin $exe }

# --- 1) initdb na primeira execucao (superusuario = aiworkspace, sem senha local) ---
if (-not (Test-Path (Join-Path $PgData "PG_VERSION"))) {
    Write-Host "==> Inicializando o banco (primeira execucao)"
    & (Pg "initdb.exe") -D $PgData -U aiworkspace -A trust -E UTF8 --locale=C | Out-Null
}

# --- 2) sobe o Postgres so no loopback, numa porta propria ---
Write-Host "==> Iniciando Postgres em 127.0.0.1:$PgPort"
& (Pg "pg_ctl.exe") -D $PgData -w -l (Join-Path $Data "pg.log") `
    -o "-p $PgPort -c listen_addresses=127.0.0.1" start | Out-Null

try {
    # --- 3) banco + extensao pgvector (idempotente) ---
    & (Pg "createdb.exe") -h $ApiHost -p $PgPort -U aiworkspace aiworkspace 2>$null
    & (Pg "psql.exe") -h $ApiHost -p $PgPort -U aiworkspace -d aiworkspace `
        -v ON_ERROR_STOP=1 -c "CREATE EXTENSION IF NOT EXISTS vector" | Out-Null

    # --- 4) migracoes ---
    Write-Host "==> Aplicando migracoes (alembic upgrade head)"
    Push-Location $AppDir
    & $Py -m alembic upgrade head
    Pop-Location

    # --- 5) servidor (bloqueia ate Ctrl+C) ---
    Write-Host "==> API em http://$($ApiHost):$ApiPort  (docs em /docs).  Ctrl+C para sair."
    & $Py -m uvicorn aiworkspace.main:app --host $ApiHost --port $ApiPort
}
finally {
    Write-Host "==> Encerrando Postgres"
    & (Pg "pg_ctl.exe") -D $PgData -w stop | Out-Null
}
