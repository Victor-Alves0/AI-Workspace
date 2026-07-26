# Monta o PACOTE DE CODIGO do app (camada leve da auto-atualizacao em 2 camadas).
#
# Diferente do build-engine.ps1 (que monta o motor pesado inteiro: Python, deps,
# Postgres, Node, modelo), aqui so' embrulhamos o que muda a cada commit:
#   - site-packages/aiworkspace/  (backend, codigo puro)
#   - web/                        (frontend Next.js "standalone", mesmo layout do motor)
#   - app/                        (alembic.ini + migracoes)
# Produz `app-<versao>.zip` + `app-update.json` (manifesto com versao/url/sha256/
# engine_required). O CI publica os dois no release fixo `app-latest`; o launcher
# na maquina do usuario le' o manifesto no boot e troca so' essas pastas.
#
# Uso (na raiz do repo):  pwsh desktop/engine/build-app-package.ps1 -OutDir dist
param(
    [string]$NodeVersion = "20.18.0",   # so' p/ paridade; nao empacota node aqui
    # versao do codigo (camada leve). Vazio => SHA curto do git (fallback: timestamp).
    [string]$AppVersion = "",
    # base de download onde o .zip vai ficar acessivel (o release `app-latest`).
    [string]$BaseUrl = "https://github.com/Victor-Alves0/AI-Workspace/releases/download/app-latest",
    # onde escrever app-<ver>.zip e app-update.json
    [string]$OutDir = "",
    # motor minimo exigido por este codigo. Vazio => o ENGINE_VERSION atual. So'
    # aumente (junto de um instalador completo novo) quando o codigo passar a
    # depender de algo que so' existe no motor novo (ex.: uma dep Python nova).
    [string]$EngineRequired = ""
)

$ErrorActionPreference = "Stop"
$RepoRoot  = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$ServerDir = Join-Path $RepoRoot "apps\server"
$WebDir    = Join-Path $RepoRoot "apps\web"
$Work      = Join-Path $PSScriptRoot "out-app"
$Out       = if ($OutDir) { (New-Item -ItemType Directory -Force $OutDir).FullName } else { (New-Item -ItemType Directory -Force (Join-Path $Work "dist")).FullName }
$Stage     = Join-Path $Work "stage"

Remove-Item -Recurse -Force $Work -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $Stage, $Out | Out-Null

if (-not $AppVersion) {
    $AppVersion = (& git -C $RepoRoot rev-parse --short HEAD 2>$null)
    if ($LASTEXITCODE -ne 0 -or -not $AppVersion) { $AppVersion = (Get-Date -Format "yyyyMMddHHmmss") }
}
$AppVersion = "$AppVersion".Trim()
if (-not $EngineRequired) { $EngineRequired = (Get-Content (Join-Path $PSScriptRoot "ENGINE_VERSION") -Raw).Trim() }

# ----------------------------------------------------------------------------- #
# 1) backend: copia o pacote aiworkspace (codigo puro; imports = aiworkspace.*)
# ----------------------------------------------------------------------------- #
$SpOut = Join-Path $Stage "site-packages"
New-Item -ItemType Directory -Force $SpOut | Out-Null
Copy-Item (Join-Path $ServerDir "aiworkspace") (Join-Path $SpOut "aiworkspace") -Recurse -Force
# nao leva caches de bytecode do dev
Get-ChildItem (Join-Path $SpOut "aiworkspace") -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

# ----------------------------------------------------------------------------- #
# 2) frontend: build "standalone" (mesmo layout do build-engine)
# ----------------------------------------------------------------------------- #
Write-Host "==> build do frontend (npm ci + next build)"
Push-Location $WebDir
$env:NEXT_PUBLIC_API_URL = ""
npm ci
if ($LASTEXITCODE -ne 0) { throw "npm ci falhou" }
npm run build
if ($LASTEXITCODE -ne 0) { throw "next build falhou" }
Pop-Location

$WebOut = Join-Path $Stage "web"
New-Item -ItemType Directory -Force (Join-Path $WebOut ".next") | Out-Null
Copy-Item (Join-Path $WebDir ".next\standalone\*") $WebOut -Recurse -Force
Copy-Item (Join-Path $WebDir ".next\static") (Join-Path $WebOut ".next\static") -Recurse -Force
if (Test-Path (Join-Path $WebDir "public")) {
    Copy-Item (Join-Path $WebDir "public") (Join-Path $WebOut "public") -Recurse -Force
}

# ----------------------------------------------------------------------------- #
# 3) app dir (alembic)
# ----------------------------------------------------------------------------- #
$AppOut = Join-Path $Stage "app"
New-Item -ItemType Directory -Force $AppOut | Out-Null
Copy-Item (Join-Path $ServerDir "alembic.ini") $AppOut
Copy-Item (Join-Path $ServerDir "alembic") (Join-Path $AppOut "alembic") -Recurse

# ----------------------------------------------------------------------------- #
# 4) zip + manifesto (app-update.json)
# ----------------------------------------------------------------------------- #
$ZipName = "app-$AppVersion.zip"
$ZipPath = Join-Path $Out $ZipName
Push-Location $Stage
& 7z a -tzip -mx=5 $ZipPath "*" | Out-Null
if ($LASTEXITCODE -ne 0) { Pop-Location; throw "7z falhou ao empacotar" }
Pop-Location

$Sha = (Get-FileHash -Algorithm SHA256 $ZipPath).Hash.ToLower()
$manifest = [ordered]@{
    app_version     = $AppVersion
    engine_required = [int]$EngineRequired
    url             = "$BaseUrl/$ZipName"
    sha256          = $Sha
    built_at        = (Get-Date).ToUniversalTime().ToString("o")
}
$ManifestPath = Join-Path $Out "app-update.json"
($manifest | ConvertTo-Json) | Out-File -Encoding ascii $ManifestPath

Write-Host "==> pacote:   $ZipPath"
Write-Host "==> manifesto:$ManifestPath  (app=$AppVersion engine_required=$EngineRequired)"
Write-Host "==> sha256:   $Sha"
