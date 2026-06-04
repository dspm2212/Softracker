<#
.SYNOPSIS
    Packages the agent into a distributable ZIP - no git clone required.

.DESCRIPTION
    Assembles the release layout and compresses it to a single ZIP:

        bin\agente_captura.exe
        bin\agente_envio.exe
        bin\agente_retry.exe
        instalar.ps1
        tareas\captura.xml
        tareas\envio.xml
        tareas\arranque.xml

    The resulting ZIP is placed in the repository root as
    monitoreo-agente-v<Version>.zip.

    By default this script calls construir.ps1 first to build fresh
    executables. Pass -SkipBuild if the executables in deploy\bin\ are
    already up to date.

.PARAMETER Version
    Version string used in the ZIP filename. Example: "1.0.0"
    Defaults to "dev".

.PARAMETER SkipBuild
    Skip the PyInstaller build step and use the existing deploy\bin\
    executables.

.EXAMPLE
    .\empaquetar.ps1 -Version 1.0.0

.NOTES
    Author: Daniel Perez
#>

param(
    [string]$Version   = "dev",
    [switch]$SkipBuild = $false
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-Step { param([string]$Msg) Write-Host "`n[PACK] $Msg" -ForegroundColor Cyan }
function Write-Ok   { param([string]$Msg) Write-Host "    OK  $Msg"  -ForegroundColor Green }
function Write-Fail { param([string]$Msg) Write-Host "  FAIL  $Msg"  -ForegroundColor Red; exit 1 }

# ── Paths ──────────────────────────────────────────────────────────────────────
$DeployDir = $PSScriptRoot                             # monitoreo-agente\deploy\
$Root      = Split-Path $DeployDir -Parent             # monitoreo-agente\
$RepoRoot  = Split-Path $Root -Parent                  # Softracker\
$BinDir    = Join-Path $DeployDir "bin"
$ZipName   = "monitoreo-agente-v$Version.zip"
$ZipPath   = Join-Path $RepoRoot $ZipName

# ── Build step ─────────────────────────────────────────────────────────────────
if (-not $SkipBuild) {
    Write-Step "Building executables (construir.ps1)"
    & "$DeployDir\construir.ps1"
    if ($LASTEXITCODE -ne 0) { Write-Fail "Build failed - see output above." }
}

# ── Verify executables ─────────────────────────────────────────────────────────
Write-Step "Verifying executables in $BinDir"
$RequiredExes = @("agente_captura.exe", "agente_envio.exe", "agente_retry.exe")
foreach ($exe in $RequiredExes) {
    if (-not (Test-Path (Join-Path $BinDir $exe))) {
        Write-Fail "Missing $exe - run construir.ps1 first or omit -SkipBuild."
    }
    Write-Ok $exe
}

# ── Assemble staging directory ─────────────────────────────────────────────────
Write-Step "Assembling release layout"
$TmpDir    = Join-Path $env:TEMP ("monitoreo_pack_" + [guid]::NewGuid().ToString("N"))
$TmpBin    = Join-Path $TmpDir "bin"
$TmpTareas = Join-Path $TmpDir "tareas"

New-Item -ItemType Directory -Force -Path $TmpBin    | Out-Null
New-Item -ItemType Directory -Force -Path $TmpTareas | Out-Null

foreach ($exe in $RequiredExes) {
    Copy-Item (Join-Path $BinDir $exe) $TmpBin
}
Write-Ok "bin\ - $($RequiredExes.Count) executables"

Copy-Item (Join-Path $DeployDir "instalar.ps1") $TmpDir
Write-Ok "instalar.ps1"

foreach ($xml in @("captura.xml", "envio.xml", "arranque.xml")) {
    Copy-Item (Join-Path $DeployDir "tareas\$xml") $TmpTareas
}
Write-Ok "tareas\ - 3 XML task definitions"

# ── Compress ───────────────────────────────────────────────────────────────────
Write-Step "Creating $ZipPath"
if (Test-Path $ZipPath) {
    Remove-Item $ZipPath -Force
    Write-Ok "removed existing ZIP"
}
Compress-Archive -Path "$TmpDir\*" -DestinationPath $ZipPath
Remove-Item $TmpDir -Recurse -Force
Write-Ok $ZipPath

# ── Instructions ───────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host "  Release ready: $ZipName" -ForegroundColor Green
Write-Host ""
Write-Host "  Deploy on each target PC (run as Administrator):" -ForegroundColor Green
Write-Host "    1. Copy $ZipName to the PC" -ForegroundColor White
Write-Host "    2. Open an elevated PowerShell and run:" -ForegroundColor White
Write-Host "         Expand-Archive $ZipName ." -ForegroundColor White
Write-Host "         .\instalar.ps1 -SalaCode SALA-01 ``" -ForegroundColor White
Write-Host "                        -ApiUrl http://servidor:8080/v1/upload ``" -ForegroundColor White
Write-Host "                        -Token TU_TOKEN_AQUI" -ForegroundColor White
Write-Host "============================================================" -ForegroundColor Green
