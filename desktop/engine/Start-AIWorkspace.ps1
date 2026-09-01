# Inicia o AI Workspace COMPLETO sem Docker: Postgres embarcado + backend (API) +
# frontend (interface web). Abre o navegador em http://localhost:3000.
#
# Nada e instalado no sistema: o banco, os caches de modelo e o segredo do app
# ficam em -DataDir (padrao: .\data\ ao lado do script). Apagar essa pasta = zerar.
#
# Este e o "motor" do app embarcado. O shell desktop (Tauri) chama este mesmo
# script com -DataDir (pasta gravavel do usuario) e -NoBrowser (o Tauri abre a
# janela). Rodado a mao (duplo-clique), abre o navegador sozinho.

param(
    # onde guardar banco/caches/segredo. O Tauri passa a pasta de dados do app;
    # a mao, o padrao e' .\data\ ao lado do script.
    [string]$DataDir = "",
    # nao abrir o navegador (o Tauri exibe a propria janela)
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path

$Py     = Join-Path $Root "python\python.exe"
$Node   = Join-Path $Root "node\node.exe"
$PgBin  = Join-Path $Root "pgsql\bin"
$AppDir = Join-Path $Root "app"
$WebDir = Join-Path $Root "web"
$Data   = if ($DataDir) { $DataDir } else { Join-Path $Root "data" }
$PgData = Join-Path $Data "pgdata"

$PgPort  = 55432
$ApiPort = 8000
$WebPort = 3000
$Loop    = "127.0.0.1"

New-Item -ItemType Directory -Force $Data, (Join-Path $Data "cache") | Out-Null

# ----------------------------------------------------------------------------- #
# Auto-atualizacao do CODIGO do app (camada leve), em 2 camadas.
#
# O MOTOR pesado (Python/Postgres/Node/deps/modelo) e' instalado 1x pelo .exe e
# raramente muda. NOSSO codigo (aiworkspace + web + migracoes) muda a cada commit
# e e' leve. Aqui, no boot e ANTES de subir os servidores, o launcher checa um
# manifesto publicado e troca em disco so' esses arquivos leves — sem reinstalar
# ~1 GB. Nunca bloqueia o boot: qualquer falha (sem internet, manifesto/
# checksum invalido) e' ignorada e o app sobe com o codigo que ja' esta' instalado.
#
# Guarda de compatibilidade: o manifesto declara `engine_required`; se o motor
# instalado for mais antigo que o exigido (ex.: o novo codigo precisa de uma dep
# nova), o update de codigo e' PULADO e o usuario segue ate' instalar o .exe novo.
$UpdateManifestUrl = "https://github.com/Victor-Alves0/AI-Workspace/releases/download/app-latest/app-update.json"

function Update-AppCode {
    if ($env:AIW_NO_UPDATE -eq "1") { return }
    if (Test-Path (Join-Path $Data ".no-update")) { return }  # opt-out por marcador

    $localAppFile    = Join-Path $Root "app-version.txt"
    $localEngineFile = Join-Path $Root "engine-version.txt"
    if (-not (Test-Path $localAppFile) -or -not (Test-Path $localEngineFile)) { return }  # bundle sem carimbo
    $localApp    = (Get-Content $localAppFile -Raw).Trim()
    $localEngine = [int]((Get-Content $localEngineFile -Raw).Trim())

    # GitHub exige TLS 1.2; a barra de progresso do IWR deixa o download 10x mais lento
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $ProgressPreference = "SilentlyContinue"

    # busca como TEXTO e converte a mao: o GitHub serve o asset como
    # application/octet-stream, e o Invoke-RestMethod as vezes devolve string
    # crua nesse content-type — o ConvertFrom-Json explicito evita isso.
    try {
        $resp = Invoke-WebRequest -Uri $UpdateManifestUrl -TimeoutSec 8 -UseBasicParsing
        $m = $resp.Content | ConvertFrom-Json
    } catch { Write-Host "==> Sem atualizacao (manifesto inacessivel)"; return }
    if (-not $m -or -not $m.app_version -or -not $m.url -or -not $m.sha256) { return }
    if ("$($m.app_version)".Trim() -eq $localApp) { return }  # ja' na versao publicada

    $engineReq = 0; if ($m.engine_required) { $engineReq = [int]$m.engine_required }
    if ($localEngine -lt $engineReq) {
        Write-Host "==> Ha' uma atualizacao que exige um motor mais novo; reinstale o app completo para receber."
        return
    }

    Write-Host "==> Atualizando o app ($localApp -> $($m.app_version))..."
    $work = Join-Path $Data "_update"
    Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force $work | Out-Null
    try {
        $zip = Join-Path $work "app.zip"
        Invoke-WebRequest -Uri $m.url -OutFile $zip -TimeoutSec 300 -UseBasicParsing
        $got = (Get-FileHash -Algorithm SHA256 $zip).Hash.ToLower()
        if ($got -ne "$($m.sha256)".Trim().ToLower()) { throw "checksum nao confere" }

        $ex = Join-Path $work "x"
        Expand-Archive -Path $zip -DestinationPath $ex -Force

        # troca cada pasta por espelho (robocopy /MIR): o destino fica IDENTICO a'
        # nova versao (inclui remover arquivos apagados). Extraimos e validamos o
        # checksum ANTES de tocar em qualquer arquivo instalado — nada de meia-troca
        # por download cortado. Os servidores ainda nao subiram, entao nada esta'
        # com esses .py/.js abertos.
        $pairs = @(
            @{ src = (Join-Path $ex "site-packages\aiworkspace"); dst = (Join-Path $Root "python\Lib\site-packages\aiworkspace") },
            @{ src = (Join-Path $ex "web"); dst = $WebDir },
            @{ src = (Join-Path $ex "app"); dst = $AppDir }
        )
        foreach ($p in $pairs) {
            if (-not (Test-Path $p.src)) { throw "pacote incompleto: falta $($p.src)" }
        }
        foreach ($p in $pairs) {
            robocopy $p.src $p.dst /MIR /NFL /NDL /NJH /NJS /NP /R:1 /W:1 | Out-Null
            if ($LASTEXITCODE -ge 8) { throw "robocopy falhou em $($p.dst) (code $LASTEXITCODE)" }
        }
        "$($m.app_version)".Trim() | Out-File -Encoding ascii -NoNewline $localAppFile
        Write-Host "==> App atualizado para $($m.app_version)."
    } catch {
        Write-Host "==> Falha ao atualizar (segue com o codigo atual): $_"
    } finally {
        Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
    }
}

try { Update-AppCode } catch { Write-Host "==> Auto-update ignorado: $_" }

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
# Codespace: projetos/worktrees dentro de .\data\ (sem Docker, o "/data/codespace"
# padrao nao existe no Windows). O exec do Codespace cai p/ subprocesso no host aqui.
$env:CODESPACE_DATA_DIR  = Join-Path $Data "codespace"
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
# ATENCAO: NAO canalizar a saida do pg_ctl start (`| Out-Null` / `> arquivo`).
# No Windows o Postgres herda o handle de saida do pg_ctl, e o pipe do PowerShell
# fica ESPERANDO esse handle fechar — ou seja, so' quando o Postgres morrer. Isso
# congela o launcher aqui, com o Postgres no ar mas sem nunca seguir. O `-l` ja'
# manda o log do servidor para o arquivo; a saida propria do pg_ctl e' inofensiva.
Write-Host "==> Iniciando Postgres em $($Loop):$PgPort"
& (Pg "pg_ctl.exe") -D $PgData -w -s -l (Join-Path $Data "pg.log") `
    -o "-p $PgPort -c listen_addresses=127.0.0.1" start

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

    # espera a interface responder e (se rodado a mao) abre o navegador
    for ($i = 0; $i -lt 40; $i++) {
        Start-Sleep -Milliseconds 500
        try { if ((Invoke-WebRequest "http://localhost:$WebPort" -UseBasicParsing -TimeoutSec 2).StatusCode -ge 200) { break } } catch {}
    }
    if (-not $NoBrowser) { Start-Process "http://localhost:$WebPort" }

    Write-Host ""
    Write-Host "==> AI Workspace no ar: http://localhost:$WebPort   (feche esta janela para encerrar)"
    # bloqueia enquanto os servidores estiverem vivos
    Wait-Process -Id ($procs | ForEach-Object { $_.Id })
}
finally {
    Write-Host "==> Encerrando..."
    foreach ($p in $procs) { if ($p -and -not $p.HasExited) { Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue } }
    & (Pg "pg_ctl.exe") -D $PgData -w -s stop
}
