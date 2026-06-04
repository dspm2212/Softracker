<#
.SYNOPSIS
    Compiles the three monitoring agent entry-points into self-contained
    Windows executables using PyInstaller.

.DESCRIPTION
    1. Creates (or reuses) a build venv at .venv-build\.
    2. Installs runtime + dev dependencies (including PyInstaller).
    3. Compiles agente_captura.exe, agente_envio.exe, agente_retry.exe.
    4. Places finished executables in deploy\bin\.

    Run from any directory - paths are resolved from the script location.
    Python 3.13+ must be available in PATH.

.NOTES
    Author: Daniel Perez
    Output: deploy\bin\agente_captura.exe
            deploy\bin\agente_envio.exe
            deploy\bin\agente_retry.exe
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-Step { param([string]$Msg) Write-Host "`n[BUILD] $Msg" -ForegroundColor Cyan }
function Write-Ok   { param([string]$Msg) Write-Host "    OK  $Msg"   -ForegroundColor Green }
function Write-Fail { param([string]$Msg) Write-Host "  FAIL  $Msg"   -ForegroundColor Red; exit 1 }

# ── Paths ──────────────────────────────────────────────────────────────────────
$Root      = Split-Path $PSScriptRoot -Parent           # monitoreo-agente\
$BinOut    = Join-Path $PSScriptRoot "bin"              # deploy\bin\
$WorkDir   = Join-Path $Root "build\_work"              # temp build artifacts
$SpecDir   = Join-Path $Root "build\_specs"             # generated .spec files
$Venv      = Join-Path $Root ".venv-build"              # isolated build venv

# ── Python ─────────────────────────────────────────────────────────────────────
Write-Step "Locating Python 3"
try {
    $PyExe = (Get-Command python.exe -ErrorAction Stop).Source
    $PyVer = & $PyExe --version 2>&1
    Write-Ok "$PyExe  ($PyVer)"
} catch {
    Write-Fail "python.exe not found in PATH. Install Python 3.13+ and add it to PATH."
}

# ── Build venv ─────────────────────────────────────────────────────────────────
Write-Step "Preparing build venv at $Venv"
if (-not (Test-Path "$Venv\Scripts\python.exe")) {
    # --upgrade-deps installs and upgrades pip inside the new venv automatically
    & $PyExe -m venv --upgrade-deps $Venv
    if ($LASTEXITCODE -ne 0) { Write-Fail "venv creation failed." }
    Write-Ok "venv created"
} else {
    Write-Ok "reusing existing venv"
}

$VenvPython = Join-Path $Venv "Scripts\python.exe"

Write-Step "Installing dependencies into build venv"
# Guarantee pip is present even in venvs created without --upgrade-deps.
# ensurepip exit code is non-zero when pip was already there - that is fine.
& $VenvPython -m ensurepip --upgrade 2>$null
& $VenvPython -m pip install --quiet --upgrade pip
if ($LASTEXITCODE -ne 0) { Write-Fail "pip upgrade failed." }

& $VenvPython -m pip install --quiet -r "$Root\requirements.txt"
if ($LASTEXITCODE -ne 0) { Write-Fail "runtime dependencies install failed." }

& $VenvPython -m pip install --quiet -r "$Root\requirements-dev.txt"
if ($LASTEXITCODE -ne 0) { Write-Fail "dev dependencies install failed." }

Write-Ok "all dependencies installed"

# ── Output directories ─────────────────────────────────────────────────────────
New-Item -ItemType Directory -Force -Path $BinOut  | Out-Null
New-Item -ItemType Directory -Force -Path $WorkDir | Out-Null
New-Item -ItemType Directory -Force -Path $SpecDir | Out-Null

# ── Common PyInstaller flags ───────────────────────────────────────────────────
# --onefile     : single .exe - no external DLLs to distribute
# --noconsole   : suppress console window (equivalent to pythonw.exe)
# --clean       : discard stale build cache
# --paths       : let PyInstaller discover the agente\ package
# --hidden-import win32*: pyinstaller-hooks-contrib handles most of pywin32
#                         but explicit imports guard against hook gaps
# --collect-all pyarrow : pyarrow loads C extensions dynamically; collect all
$CommonFlags = @(
    "--onefile",
    "--noconsole",
    "--clean",
    "--distpath", $BinOut,
    "--workpath", $WorkDir,
    "--specpath", $SpecDir,
    "--paths", $Root,
    "--hidden-import", "win32api",
    "--hidden-import", "win32con",
    "--hidden-import", "win32gui",
    "--hidden-import", "win32process",
    "--hidden-import", "pywintypes",
    "--collect-all", "pyarrow"
)

# ── Entry-points to compile ────────────────────────────────────────────────────
$Scripts = @(
    @{ Script = "scripts\agente_captura.py"; Name = "agente_captura" },
    @{ Script = "scripts\agente_envio.py";   Name = "agente_envio"   },
    @{ Script = "scripts\agente_retry.py";   Name = "agente_retry"   }
)

foreach ($entry in $Scripts) {
    Write-Step "Compiling $($entry.Name).exe"
    $PyiArgs = $CommonFlags + @("--name", $entry.Name, (Join-Path $Root $entry.Script))
    & $VenvPython -m PyInstaller @PyiArgs
    if ($LASTEXITCODE -ne 0) { Write-Fail "PyInstaller failed for $($entry.Name)." }
    Write-Ok "$BinOut\$($entry.Name).exe"
}

# ── Summary ────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host "  Build complete." -ForegroundColor Green
Write-Host ""
Write-Host "  Executables written to:" -ForegroundColor Green
foreach ($entry in $Scripts) {
    Write-Host "    $BinOut\$($entry.Name).exe" -ForegroundColor White
}
Write-Host ""
Write-Host "  Next: run deploy\empaquetar.ps1 -Version 1.0.0" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
