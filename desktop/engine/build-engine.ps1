# Monta o "engine bundle" do AI Workspace para Windows (Etapa 1 do app sem Docker).
#
# Produz uma pasta autocontida com Python embarcado + backend instalado + Postgres 16
# + pgvector, que o Start-AIWorkspace.ps1 sobe sem depender de nada instalado no SO.
# Feito para rodar no runner `windows-latest` (tem 7-Zip e MSVC para eventuais wheels).
#
# Uso (na raiz do repo):  pwsh desktop/engine/build-engine.ps1
# Saida:  desktop/engine/out/aiworkspace-engine/  (+ o zip, se -Zip)

param(
    [string]$PythonVersion = "3.12.7",
    [string]$NodeVersion   = "20.18.0",
    # binarios do Postgres (EDB). pgvector abaixo e' compilado p/ 16.14; como a ABI
    # de extensao e' estavel dentro do major 16, um minor proximo tambem carrega.
    [string]$PostgresUrl = "https://get.enterprisedb.com/postgresql/postgresql-16.14-1-windows-x64-binaries.zip",
    # build comunitario do pgvector p/ Windows (vector.dll + share/extension)
    [string]$PgvectorRepo = "andreiramani/pgvector_pgsql_windows",
    [string]$PgvectorTag  = "0.8.3_16.14",
    # pasta final do bundle. Padrao: desktop/engine/out/aiworkspace-engine.
    # O build do app desktop passa desktop/src-tauri/engine (vira recurso do Tauri).
    [string]$OutDir = "",
    [switch]$Zip
)

$ErrorActionPreference = "Stop"
$RepoRoot  = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$ServerDir = Join-Path $RepoRoot "apps\server"
$WebDir    = Join-Path $RepoRoot "apps\web"
$Work      = Join-Path $PSScriptRoot "out"
$Out       = if ($OutDir) { $OutDir } else { Join-Path $Work "aiworkspace-engine" }
$Dl        = Join-Path $Work "_dl"

Remove-Item -Recurse -Force $Work -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force $Out  -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $Out, $Dl | Out-Null

function Get-File($url, $dest) {
    Write-Host "==> baixando $url"
    curl.exe -fSL --retry 3 -o $dest $url
    if ($LASTEXITCODE -ne 0) { throw "download falhou: $url" }
}
function Unzip($zip, $dest) {
    # 7z e' MUITO mais rapido que Expand-Archive nos zips gigantes do Postgres
    & 7z x -y -o"$dest" $zip | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "7z falhou: $zip" }
}

# ----------------------------------------------------------------------------- #
# 1) Python embarcado (interpretador de runtime) + backend instalado nele
#
# O interpretador que VAI RODAR na maquina do usuario e' o embeddable. Mas a
# INSTALACAO das deps e' feita com o Python do runner (via `python` no PATH),
# num --target dentro do site-packages do embeddable. Isso evita compilar/pip
# sob o embeddable (que nao tem headers) e ainda pega os wheels certos: runner e
# embeddable sao ambos 3.12 (ABI cp312), entao os .pyd casam.
# ----------------------------------------------------------------------------- #
$PyDir = Join-Path $Out "python"
$PyZip = Join-Path $Dl "python-embed.zip"
Get-File "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip" $PyZip
Unzip $PyZip $PyDir

# habilita site-packages no python embarcado (por padrao vem desligado)
$pth = Get-ChildItem $PyDir -Filter "python*._pth" | Select-Object -First 1
(Get-Content $pth.FullName) `
    -replace "^#import site", "import site" `
    | Set-Content $pth.FullName -Encoding ascii
Add-Content $pth.FullName "Lib\site-packages" -Encoding ascii

$Site = Join-Path $PyDir "Lib\site-packages"
New-Item -ItemType Directory -Force $Site | Out-Null

Write-Host "==> instalando o backend e dependencias (pode demorar)"
python -m pip install --upgrade pip
python -m pip install --target $Site $ServerDir
if ($LASTEXITCODE -ne 0) { throw "pip install do backend falhou" }

# ----------------------------------------------------------------------------- #
# 2) Postgres 16 (binarios EDB)
# ----------------------------------------------------------------------------- #
$PgZip = Join-Path $Dl "postgres.zip"
Get-File $PostgresUrl $PgZip
$PgTmp = Join-Path $Work "_pg"
Unzip $PgZip $PgTmp
# o zip da EDB extrai numa pasta "pgsql/"
Move-Item (Join-Path $PgTmp "pgsql") (Join-Path $Out "pgsql")

# ----------------------------------------------------------------------------- #
# 3) pgvector (merge de lib/ e share/ dentro do pgsql)
# ----------------------------------------------------------------------------- #
$asset = (& gh release view $PgvectorTag --repo $PgvectorRepo `
    --json assets --jq ".assets[] | select(.name|endswith(\"".zip\"")) | .url" 2>$null) | Select-Object -First 1
if (-not $asset) {
    # fallback sem gh: monta a URL de download direto do primeiro asset via API
    $asset = (curl.exe -fsSL "https://api.github.com/repos/$PgvectorRepo/releases/tags/$PgvectorTag" |
        ConvertFrom-Json).assets |
        Where-Object { $_.name -like "*.zip" } |
        Select-Object -First 1 -ExpandProperty browser_download_url
}
if (-not $asset) { throw "nao achei o asset .zip do pgvector em $PgvectorRepo@$PgvectorTag" }
$PvZip = Join-Path $Dl "pgvector.zip"
Get-File $asset $PvZip
$PvTmp = Join-Path $Work "_pv"
Unzip $PvZip $PvTmp
# o build do pgvector traz lib\vector.dll e share\extension\vector*; procura essas
# pastas em qualquer nivel (alguns zips tem pasta raiz) e copia por cima do pgsql
foreach ($sub in @("lib", "share")) {
    $src = Get-ChildItem $PvTmp -Recurse -Directory -Filter $sub | Select-Object -First 1
    if ($src) {
        Copy-Item "$($src.FullName)\*" (Join-Path $Out "pgsql\$sub") -Recurse -Force
    }
}

# ----------------------------------------------------------------------------- #
# 4) Frontend (Next.js "standalone") + node.exe de runtime
#
# Mesmo layout do Dockerfile de producao: standalone/ na raiz, .next/static e
# public/ ao lado. Roda com `node server.js`. NEXT_PUBLIC_API_URL vazio => o front
# deriva a API de host:8000 em runtime (lib/api.ts), igual ao deploy normal.
# ----------------------------------------------------------------------------- #
Write-Host "==> build do frontend (npm ci + next build)"
Push-Location $WebDir
$env:NEXT_PUBLIC_API_URL = ""
npm ci
if ($LASTEXITCODE -ne 0) { throw "npm ci falhou" }
npm run build
if ($LASTEXITCODE -ne 0) { throw "next build falhou" }
Pop-Location

$WebOut = Join-Path $Out "web"
New-Item -ItemType Directory -Force (Join-Path $WebOut ".next") | Out-Null
Copy-Item (Join-Path $WebDir ".next\standalone\*") $WebOut -Recurse -Force
Copy-Item (Join-Path $WebDir ".next\static") (Join-Path $WebOut ".next\static") -Recurse -Force
if (Test-Path (Join-Path $WebDir "public")) {
    Copy-Item (Join-Path $WebDir "public") (Join-Path $WebOut "public") -Recurse -Force
}

$NodeZip = Join-Path $Dl "node.zip"
Get-File "https://nodejs.org/dist/v$NodeVersion/node-v$NodeVersion-win-x64.zip" $NodeZip
$NodeTmp = Join-Path $Work "_node"
Unzip $NodeZip $NodeTmp
$NodeExe = Get-ChildItem $NodeTmp -Recurse -Filter node.exe | Select-Object -First 1
New-Item -ItemType Directory -Force (Join-Path $Out "node") | Out-Null
Copy-Item $NodeExe.FullName (Join-Path $Out "node\node.exe")

# ----------------------------------------------------------------------------- #
# 5) app dir (alembic) + launcher
# ----------------------------------------------------------------------------- #
$AppOut = Join-Path $Out "app"
New-Item -ItemType Directory -Force $AppOut | Out-Null
Copy-Item (Join-Path $ServerDir "alembic.ini") $AppOut
Copy-Item (Join-Path $ServerDir "alembic") (Join-Path $AppOut "alembic") -Recurse
Copy-Item (Join-Path $PSScriptRoot "Start-AIWorkspace.ps1") $Out
Copy-Item (Join-Path $PSScriptRoot "README.txt") $Out -ErrorAction SilentlyContinue

Write-Host "==> bundle montado em $Out"

# ----------------------------------------------------------------------------- #
# 6) zip final (opcional)
# ----------------------------------------------------------------------------- #
if ($Zip) {
    $ZipPath = Join-Path $Work "aiworkspace-engine-windows.zip"
    Push-Location $Out
    & 7z a -tzip -mx=5 $ZipPath "*" | Out-Null
    Pop-Location
    Write-Host "==> zip em $ZipPath"
}
