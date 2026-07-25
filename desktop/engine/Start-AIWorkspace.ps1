# Inicia o AI Workspace COMPLETO sem Docker: Postgres embarcado + backend (API) +
# frontend (interface web). Abre o navegador em http://localhost:3000.
#
# Tudo e relativo a esta pasta e nada e instalado no sistema: o banco, os caches de
# modelo e o segredo do app ficam em .\data\. Apagar a pasta = zerar tudo.
#
# Este e o "motor" do app embarcado. O shell desktop (Tauri) vai supervisionar
# exatamente esta mesma sequencia numa etapa seguinte; por ora da pra rodar a mao.

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path

$Py     = Join-Path $Root "python\python.exe"
$Node   = Join-Path $Root "node\node.exe"
$PgBin  = Join-Path $Root "pgsql\bin"
$AppDir = Join-Path $Root "app"
$WebDir = Join-Path $Root "web"
$Data   = Join-Path $Root "data"
$PgData = Join-Path $Data "pgdata"

$PgPort  = 55432
$ApiPort = 8000
$WebPort = 3000
$Loop    = "127.0.0.1"

New-Item -ItemType Directory -Force $Data, (Join-Path $Data "cache") | Out-Null

# --- segredo do app: gerado uma vez e reusado (deriva a chave que cifra os
#     segredos por-usuario no banco; trocar tornaria o banco ilegivel) ---
$SecretFile = Join-Path $Data "secret.txt"
if (-not (Test-Path $SecretFile)) {
    (& $Py -c "import secrets;print(secrets.token_urlsafe(48))").Trim() |
        Out-File -Encoding ascii -NoNewline $SecretFile
}

# --- ambiente do backend: tudo apontando pra dentro de .\data\ ---
$env:APP_SECRET          = (Get-Content $SecretFile -Raw).Trim()
$env:DATABASE_URL        = "postgresql+asyncpg://aiworkspace:aiworkspace@$($Loop):$PgPort/aiworkspace"
$env:FASTEMBED_CACHE_PATH = Join-Path $Data "cache\fastembed"
$env:HF_HOME             = Join-Path $Data "cache\huggingface"
# a interface roda em localhost:3000 e chama a API em localhost:8000; libera as
# duas grafias do loopback no CORS
$env:WEB_ORIGIN          = "http://localhost:$WebPort,http://127.0.0.1:$WebPort"
$env:PATH                = "$PgBin;$env:PATH"

function Pg($exe) { Join-Path $PgBin $exe }
$procs = @()

# --- 1) initdb na primeira execucao (superusuario = aiworkspace, sem senha local) ---
if (-not (Test-Path (Join-Path $PgData "PG_VERSION"))) {
    Write-Host "==> Inicializando o banco (primeira execucao)"
    & (Pg "initdb.exe") -D $PgData -U aiworkspace -A trust -E UTF8 --locale=C | Out-Null
}

# --- 2) sobe o Postgres so no loopback, numa porta propria ---
Write-Host "==> Iniciando Postgres em $($Loop):$PgPort"
& (Pg "pg_ctl.exe") -D $PgData -w -l (Join-Path $Data "pg.log") `
    -o "-p $PgPort -c listen_addresses=127.0.0.1" start | Out-Null

try {
    # --- 3) banco + extensao pgvector (idempotente) ---
    & (Pg "createdb.exe") -h $Loop -p $PgPort -U aiworkspace aiworkspace 2>$null
    & (Pg "psql.exe") -h $Loop -p $PgPort -U aiworkspace -d aiworkspace `
        -v ON_ERROR_STOP=1 -c "CREATE EXTENSION IF NOT EXISTS vector" | Out-Null

    # --- 4) migracoes ---
    Write-Host "==> Aplicando migracoes (alembic upgrade head)"
    Push-Location $AppDir
    & $Py -m alembic upgrade head
    Pop-Location

    # --- 5) backend (API) em segundo plano ---
    Write-Host "==> Iniciando API em http://$($Loop):$ApiPort"
    $procs += Start-Process -FilePath $Py -PassThru -WindowStyle Hidden `
        -ArgumentList @("-m","uvicorn","aiworkspace.main:app","--host",$Loop,"--port","$ApiPort") `
        -RedirectStandardOutput (Join-Path $Data "api.out.log") `
        -RedirectStandardError  (Join-Path $Data "api.err.log")

    # --- 6) frontend (interface) em segundo plano ---
    Write-Host "==> Iniciando interface em http://localhost:$WebPort"
    $env:PORT = "$WebPort"; $env:HOSTNAME = $Loop; $env:NODE_ENV = "production"
    $procs += Start-Process -FilePath $Node -PassThru -WindowStyle Hidden `
        -WorkingDirectory $WebDir -ArgumentList @("server.js") `
        -RedirectStandardOutput (Join-Path $Data "web.out.log") `
        -RedirectStandardError  (Join-Path $Data "web.err.log")

    # espera a interface responder e abre o navegador
    for ($i = 0; $i -lt 40; $i++) {
        Start-Sleep -Milliseconds 500
        try { if ((Invoke-WebRequest "http://localhost:$WebPort" -UseBasicParsing -TimeoutSec 2).StatusCode -ge 200) { break } } catch {}
    }
    Start-Process "http://localhost:$WebPort"

    Write-Host ""
    Write-Host "==> AI Workspace no ar: http://localhost:$WebPort   (feche esta janela para encerrar)"
    # bloqueia enquanto os servidores estiverem vivos
    Wait-Process -Id ($procs | ForEach-Object { $_.Id })
}
finally {
    Write-Host "==> Encerrando..."
    foreach ($p in $procs) { if ($p -and -not $p.HasExited) { Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue } }
    & (Pg "pg_ctl.exe") -D $PgData -w stop | Out-Null
}
