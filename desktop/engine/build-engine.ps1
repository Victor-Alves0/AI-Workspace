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
    # binarios do Postgres (EDB). pgvector abaixo e' compilado p/ 16.14; como a ABI
    # de extensao e' estavel dentro do major 16, um minor proximo tambem carrega.
    [string]$PostgresUrl = "https://get.enterprisedb.com/postgresql/postgresql-16.14-1-windows-x64-binaries.zip",
    # build comunitario do pgvector p/ Windows (vector.dll + share/extension)
    [string]$PgvectorRepo = "andreiramani/pgvector_pgsql_windows",
    [string]$PgvectorTag  = "0.8.3_16.14",
    [string]$PgvectorAsset = "vector.v0.8.3-pg16.zip",
    # pasta final do bundle. Padrao: desktop/engine/out/aiworkspace-engine.
    # O build do app desktop passa desktop/src-tauri/engine (vira recurso do Tauri).
    [string]$OutDir = "",
    # versao do CODIGO do app (camada leve, auto-atualizavel). Vazio => SHA do git.
    # A camada pesada (motor) usa o arquivo ENGINE_VERSION ao lado deste script.
    [string]$AppVersion = "",
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
# [whatsapp-local]: o WhatsApp por QR roda embutido (neonize) — sem Docker, nao ha Evolution
# --no-compile: o pip pré-compila TODO .py (~11 mil __pycache__, 130 MB), inclusive
# módulos que nunca são importados. Sem isso, o Python compila no 1º uso só o que
# carrega, dentro da pasta do app (instalação por usuário = gravável).
python -m pip install --no-compile --target $Site "$($ServerDir)[whatsapp-local]"
if ($LASTEXITCODE -ne 0) { throw "pip install do backend falhou" }

# O tempo de instalação é dominado pela QUANTIDADE de arquivos (o instalador e o
# antivírus tratam um por um), não pelo tamanho. Suítes de teste dos pacotes não
# rodam em produção. Só pastas "tests"/"test": "testing" (numpy.testing,
# sqlalchemy.testing) pode ser importada em tempo de execução. O jedi fica inteiro
# (os .pyi dele são os stubs que ele usa).
Get-ChildItem $Site -Directory -Recurse -Include "tests", "test" |
    Where-Object { $_.FullName -notlike "*\jedi\*" } |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
Get-ChildItem $Site -Directory -Recurse -Filter "__pycache__" |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
# stubs de bibliotecas de TERCEIROS do jedi (~4,3 mil arquivos): sem eles o jedi
# analisa o código-fonte da biblioteca; os stubs da stdlib ficam
Remove-Item -Recurse -Force (Join-Path $Site "jedi\third_party\typeshed\stubs") -ErrorAction SilentlyContinue

# Os pacotes só-Python (~10 mil arquivos) viram UM arquivo: site-packages.zip, de onde
# o Python importa direto (ver pack_site.py). Roda com o Python embarcado para o .pyc
# gravado no zip ser da mesma versão que vai executá-lo.
& (Join-Path $PyDir "python.exe") (Join-Path $PSScriptRoot "pack_site.py") $Site (Join-Path $PyDir "Lib\site-packages.zip")
if ($LASTEXITCODE -ne 0) { throw "empacotamento do site-packages falhou" }
Add-Content $pth.FullName "Lib\site-packages.zip" -Encoding ascii

# ...mas sem NENHUM .pyc a 1ª abertura leva ~1 min (compila tudo que importa, com o
# antivírus olhando cada arquivo novo). Importar o app uma vez com o próprio Python
# embarcado gera o .pyc só do que ele carrega de fato (~2,4 mil, não 11 mil).
Write-Host "==> pré-compilando os módulos que o app carrega"
$env:APP_SECRET = "build-only-" + ("x" * 32)
& (Join-Path $PyDir "python.exe") -c @"
import importlib
for m in ('aiworkspace.main', 'aiworkspace.desktop', 'fastembed', 'onnxruntime', 'neonize',
          'mem0', 'langchain_community', 'yt_dlp', 'phonenumbers', 'PIL', 'jedi'):
    importlib.import_module(m)
"@
if ($LASTEXITCODE -ne 0) { throw "o backend não importa com o Python embarcado" }
Remove-Item Env:APP_SECRET

# ----------------------------------------------------------------------------- #
# 2) Postgres 16 (binarios EDB)
# ----------------------------------------------------------------------------- #
$PgZip = Join-Path $Dl "postgres.zip"
Get-File $PostgresUrl $PgZip
$PgTmp = Join-Path $Work "_pg"
Unzip $PgZip $PgTmp
# o zip da EDB extrai numa pasta "pgsql/"
Move-Item (Join-Path $PgTmp "pgsql") (Join-Path $Out "pgsql")
# O zip da EDB traz o pgAdmin 4 (~16 mil arquivos, 670 MB), o StackBuilder, a
# documentação, os headers de compilação e os símbolos de depuração: nada disso roda
# no app, que só usa bin/ (postgres, initdb, pg_ctl, pg_dump, psql), lib/ e share/.
# Era ~80% dos arquivos do instalador.
foreach ($extra in @("pgAdmin 4", "StackBuilder", "doc", "include", "symbols")) {
    Remove-Item -Recurse -Force (Join-Path $Out "pgsql\$extra") -ErrorAction SilentlyContinue
}

# ----------------------------------------------------------------------------- #
# 3) pgvector (merge de lib/ e share/ dentro do pgsql)
# ----------------------------------------------------------------------------- #
# link direto do arquivo (versão fixada): consultar a API do GitHub para descobrir o
# nome falhava no CI — o gh às vezes falha e a API sem token responde 403 por limite
$asset = "https://github.com/$PgvectorRepo/releases/download/$PgvectorTag/$PgvectorAsset"
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
# 4) Frontend: export ESTATICO (sem Node no motor)
#
# O Docker usa o build "standalone" (servidor Node); aqui a mesma interface sai
# como arquivos estaticos, servidos pelo proprio backend (aiworkspace.desktop).
# Sai o node.exe (~80 MB), um processo e uma porta.
# ----------------------------------------------------------------------------- #
Write-Host "==> build do frontend (npm ci + export estatico)"
Push-Location $WebDir
# interface ESTATICA (sem Node): o aiworkspace.desktop serve estes arquivos e a API em
# /api na mesma porta. API relativa => o front chama a propria origem + /api.
$env:NEXT_OUTPUT = "export"
$env:NEXT_PUBLIC_API_URL = "/api"
npm ci
if ($LASTEXITCODE -ne 0) { throw "npm ci falhou" }
npm run build
if ($LASTEXITCODE -ne 0) { throw "next build (export) falhou" }
Pop-Location
Remove-Item Env:NEXT_OUTPUT, Env:NEXT_PUBLIC_API_URL -ErrorAction SilentlyContinue

$WebOut = Join-Path $Out "web"
Copy-Item (Join-Path $WebDir "out") $WebOut -Recurse -Force

# ----------------------------------------------------------------------------- #
# 5) app dir (alembic) + launcher
# ----------------------------------------------------------------------------- #
$AppOut = Join-Path $Out "app"
New-Item -ItemType Directory -Force $AppOut | Out-Null
Copy-Item (Join-Path $ServerDir "alembic.ini") $AppOut
Copy-Item (Join-Path $ServerDir "alembic") (Join-Path $AppOut "alembic") -Recurse
Copy-Item (Join-Path $PSScriptRoot "Start-AIWorkspace.ps1") $Out
Copy-Item (Join-Path $PSScriptRoot "README.txt") $Out -ErrorAction SilentlyContinue

# ----------------------------------------------------------------------------- #
# 5b) carimbo de versoes (auto-atualizacao em 2 camadas)
#
# engine-version.txt = camada PESADA (Python/deps/Postgres). So muda quando
#   deps/binarios mudam -> exige instalador completo novo.
# app-version.txt     = camada LEVE (nosso codigo: aiworkspace + web + migracoes).
#   Muda a cada commit -> o launcher baixa so o diff e troca em disco, sem reinstalar.
# O launcher compara estes com o manifesto publicado (app-update.json) no boot.
# ----------------------------------------------------------------------------- #
$EngineVersion = (Get-Content (Join-Path $PSScriptRoot "ENGINE_VERSION") -Raw).Trim()
if (-not $AppVersion) {
    $AppVersion = (& git -C $RepoRoot rev-parse --short HEAD 2>$null)
    if ($LASTEXITCODE -ne 0 -or -not $AppVersion) { $AppVersion = (Get-Date -Format "yyyyMMddHHmmss") }
    $AppVersion = "$AppVersion".Trim()
}
$EngineVersion | Out-File -Encoding ascii -NoNewline (Join-Path $Out "engine-version.txt")
$AppVersion    | Out-File -Encoding ascii -NoNewline (Join-Path $Out "app-version.txt")
Write-Host "==> engine-version=$EngineVersion  app-version=$AppVersion"

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
