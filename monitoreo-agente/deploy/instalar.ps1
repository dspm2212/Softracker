#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Installs the monitoring agent to C:\monitoreo on a target PC.

.DESCRIPTION
    1. Creates the folder structure under InstallDir.
    2. Copies the three self-contained executables from bin\.
    3. Writes config.json (UTF-8 without BOM).
    4. Imports the three Task Scheduler tasks under the student account,
       replacing __INSTALL_USER__ with the resolved DOMAIN\account.

    No Python, no virtualenv, no internet connection required on the target PC.
    The executables (agente_captura.exe, agente_envio.exe, agente_retry.exe)
    must be present in bin\ next to this script before running.

.PARAMETER InstallDir
    Destination directory. Default: C:\monitoreo

.PARAMETER StudentUser
    SAM account name of the monitored user. Defaults to the current user.

.PARAMETER SalaCode
    Room identifier stored in config.json (e.g. SALA-01). Prompted if omitted.

.PARAMETER ApiUrl
    Full upload endpoint URL. Prompted if omitted.

.PARAMETER Token
    Shared auth token for the university. Change from the default before deploying.

.NOTES
    Author: Daniel Perez
    Run from an elevated PowerShell prompt on each target PC.
    Source files must be in the same folder as this script (bin\, tareas\).
#>

param(
    [string]$InstallDir  = "C:\monitoreo",
    [string]$StudentUser = "",
    [string]$SalaCode    = "",
    [string]$ApiUrl      = "",
    [string]$Token       = "MONITOREO_SHARED_TOKEN_CHANGE_ME"
)

if (-not $StudentUser) { $StudentUser = $env:USERNAME }

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-Step { param([string]$Msg) Write-Host "`n[INSTALAR] $Msg" -ForegroundColor Cyan }
function Write-Ok   { param([string]$Msg) Write-Host "    OK  $Msg"     -ForegroundColor Green }
function Write-Fail { param([string]$Msg) Write-Host "  FAIL  $Msg"     -ForegroundColor Red; exit 1 }


# ── Resolve student account ────────────────────────────────────────────────────
Write-Step "Resolving student account '$StudentUser'"

$domain = $env:USERDOMAIN
if (-not $domain) { $domain = $env:COMPUTERNAME }
$installUser = "$domain\$StudentUser"

try {
    $sid = (New-Object System.Security.Principal.NTAccount($installUser)).Translate(
        [System.Security.Principal.SecurityIdentifier])
    Write-Ok "Account found: $installUser (SID $sid)"
} catch {
    Write-Host "  WARN  Account '$installUser' not found locally - continuing." -ForegroundColor Yellow
    Write-Host "        Tasks will be registered but may not run until the account exists." -ForegroundColor Yellow
}


# ── Prompt for required parameters ────────────────────────────────────────────
if (-not $SalaCode) {
    $SalaCode = Read-Host "Enter sala_codigo (e.g. SALA-01)"
    if (-not $SalaCode) { Write-Fail "sala_codigo is required." }
}
if (-not $ApiUrl) {
    $ApiUrl = Read-Host "Enter API URL (e.g. http://192.168.1.100:8080/v1/upload)"
    if (-not $ApiUrl) { Write-Fail "api_url is required." }
}


# ── Verify source executables ──────────────────────────────────────────────────
Write-Step "Verifying agent executables in bin\"

$BinDir  = Join-Path $PSScriptRoot "bin"
$RequiredExes = @("agente_captura.exe", "agente_envio.exe", "agente_retry.exe")
foreach ($exe in $RequiredExes) {
    if (-not (Test-Path (Join-Path $BinDir $exe))) {
        Write-Fail "Missing $exe in $BinDir. Build with deploy\construir.ps1 first."
    }
    Write-Ok $exe
}


# ── Create folder structure ────────────────────────────────────────────────────
Write-Step "Creating folder structure under $InstallDir"

$folders = @(
    "$InstallDir\data\raw",
    "$InstallDir\data\pendientes",
    "$InstallDir\data\enviados",
    "$InstallDir\logs"
)
foreach ($folder in $folders) {
    New-Item -ItemType Directory -Force -Path $folder | Out-Null
    Write-Ok $folder
}


# ── Copy executables ───────────────────────────────────────────────────────────
Write-Step "Copying agent executables to $InstallDir"

foreach ($exe in $RequiredExes) {
    Copy-Item (Join-Path $BinDir $exe) "$InstallDir\$exe" -Force
    Write-Ok "$InstallDir\$exe"
}


# ── Write config.json ──────────────────────────────────────────────────────────
Write-Step "Writing $InstallDir\config.json"

$configObj = @{
    api_url               = $ApiUrl
    token                 = $Token
    sala_codigo           = $SalaCode
    datos_dir             = ($InstallDir + "\data") -replace "\\", "/"
    log_dir               = ($InstallDir + "\logs") -replace "\\", "/"
    intervalo_captura_min = 10
    hora_envio            = "21:45"
    timeout_envio_seg     = 60
    reintentos_envio      = 3
    dias_retencion_local  = 7
}
$jsonContent = $configObj | ConvertTo-Json -Depth 3

# PowerShell 5.1 Out-File adds a BOM that breaks Python's json.load.
# Use .NET directly to write UTF-8 without BOM.
$utf8NoBom = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText("$InstallDir\config.json", $jsonContent, $utf8NoBom)

Write-Ok "$InstallDir\config.json"


# ── Register Task Scheduler tasks ─────────────────────────────────────────────
Write-Step "Importing Task Scheduler tasks for $installUser"

$tasksDir = Join-Path $PSScriptRoot "tareas"
$tasks = @(
    @{ Xml = "captura.xml";  Name = "\Monitoreo\Captura"  },
    @{ Xml = "envio.xml";    Name = "\Monitoreo\Envio"    },
    @{ Xml = "arranque.xml"; Name = "\Monitoreo\Arranque" }
)

foreach ($task in $tasks) {
    $xmlPath = Join-Path $tasksDir $task.Xml
    if (-not (Test-Path $xmlPath)) {
        Write-Fail "Missing task XML: $xmlPath"
    }

    # Replace placeholder and switch encoding to UTF-16 (schtasks requirement)
    $xmlContent = [System.IO.File]::ReadAllText($xmlPath, [System.Text.Encoding]::UTF8)
    $xmlContent = $xmlContent -replace "__INSTALL_USER__", $installUser
    $xmlContent = $xmlContent -replace "__INSTALL_DIR__",  $InstallDir
    $xmlContent = $xmlContent -replace 'encoding="UTF-8"', 'encoding="UTF-16"'

    $tmpXml = "$env:TEMP\monitoreo_$($task.Xml)"
    [System.IO.File]::WriteAllText($tmpXml, $xmlContent, [System.Text.Encoding]::Unicode)

    # Delete existing task silently (ignore failure if it doesn't exist yet)
    $deleteBase = Join-Path $env:TEMP ("monitoreo_del_" + [guid]::NewGuid().ToString("N"))
    Start-Process -FilePath "$env:SystemRoot\System32\schtasks.exe" `
        -ArgumentList @("/Delete", "/TN", $task.Name, "/F") `
        -Wait -PassThru -WindowStyle Hidden `
        -RedirectStandardOutput "$deleteBase.out" `
        -RedirectStandardError  "$deleteBase.err" | Out-Null
    Remove-Item "$deleteBase.out", "$deleteBase.err" -Force -ErrorAction SilentlyContinue

    # Import fresh task
    schtasks /Create /XML $tmpXml /TN $task.Name /F | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Remove-Item $tmpXml -Force -ErrorAction SilentlyContinue
        Write-Fail "schtasks /Create failed for $($task.Name). Check permissions."
    }
    Remove-Item $tmpXml -Force
    Write-Ok $task.Name
}


# ── Done ───────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host "  Installation complete." -ForegroundColor Green
Write-Host ""
Write-Host "  Install dir  : $InstallDir"  -ForegroundColor Green
Write-Host "  Student user : $installUser" -ForegroundColor Green
Write-Host "  Sala         : $SalaCode"    -ForegroundColor Green
Write-Host "  API URL      : $ApiUrl"      -ForegroundColor Green
Write-Host ""
Write-Host "  Scheduled tasks registered:" -ForegroundColor Green
Write-Host "    \Monitoreo\Captura   - every 10 min while student is logged in" -ForegroundColor Green
Write-Host "    \Monitoreo\Envio     - daily at 21:45" -ForegroundColor Green
Write-Host "    \Monitoreo\Arranque  - once at login (retry orphan data)" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
