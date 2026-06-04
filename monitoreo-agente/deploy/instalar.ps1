#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Installs the monitoring agent to C:\monitoreo on a target PC.

.DESCRIPTION
    1. Verifies the estudiante account exists.
    2. Creates the folder structure under InstallDir.
    3. Copies entry-point scripts, and requirements.txt.
    4. Creates a Python virtualenv and installs all dependencies.
    5. Writes config.json (UTF-8 without BOM so Python can parse it).
    6. Imports the three Task Scheduler tasks under the estudiante account,
       replacing the __INSTALL_USER__ placeholder with the resolved account.

.PARAMETER InstallDir
    Destination directory. Default: C:\monitoreo

.PARAMETER StudentUser
    SAM account name of the monitored user. Default: estudiante

.PARAMETER SalaCode***
    Room code stored in config.json (e.g. SALA-01). Prompted if omitted.

.PARAMETER ApiUrl
    Full upload endpoint URL. Prompted if omitted.

.PARAMETER Token
    Shared auth token. Prompted if omitted.

.NOTES
    Author: Daniel Perez
    Run from an elevated PowerShell prompt on each target PC.
    Source files are resolved relative to this script's location.
#>

param(
    [string]$InstallDir  = "C:\monitoreo",
    [string]$StudentUser = "estudiante",
    [string]$SalaCode    = "",
    [string]$ApiUrl      = "",
    [string]$Token       = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"


function Write-Step { param([string]$Msg) Write-Host "`n[INSTALAR] $Msg" -ForegroundColor Cyan }
function Write-Ok   { param([string]$Msg) Write-Host "    OK  $Msg"     -ForegroundColor Green }
function Write-Fail { param([string]$Msg) Write-Host "  FAIL  $Msg"     -ForegroundColor Red; exit 1 }


Write-Step "Resolving student account '$StudentUser'"

$domain = $env:USERDOMAIN
if (-not $domain) { $domain = $env:COMPUTERNAME }
$installUser = "$domain\$StudentUser"

try {
    $sid = (New-Object System.Security.Principal.NTAccount($installUser)).Translate(
        [System.Security.Principal.SecurityIdentifier])
    Write-Ok "Account found: $installUser (SID $sid)"
} catch {
    Write-Fail "Account '$installUser' not found. Provide -StudentUser with the correct SAM name and retry."
}


if (-not $SalaCode) {
    $SalaCode = Read-Host "Enter sala_codigo (e.g. SALA-01)"
    if (-not $SalaCode) { Write-Fail "sala_codigo is required." }
}
if (-not $ApiUrl) {
    $ApiUrl = Read-Host "Enter API URL (e.g. http://192.168.1.100:8080/v1/upload)"
    if (-not $ApiUrl) { Write-Fail "api_url is required." }
}
if (-not $Token) {
    $Token = Read-Host "Enter auth token"
    if (-not $Token) { Write-Fail "token is required." }
}


Write-Step "Creating folder structure under $InstallDir"

$folders = @(
    "$InstallDir\agente",
    "$InstallDir\scripts",
    "$InstallDir\data\raw",
    "$InstallDir\data\pendientes",
    "$InstallDir\data\enviados",
    "$InstallDir\logs"
)
foreach ($folder in $folders) {
    New-Item -ItemType Directory -Force -Path $folder | Out-Null
    Write-Ok $folder
}


Write-Step "Copying source files"

# $PSScriptRoot is deploy\, parent is monitoreo-agente\
$sourceRoot = Split-Path $PSScriptRoot -Parent

if (-not (Test-Path "$sourceRoot\agente")) {
    Write-Fail "Source not found at '$sourceRoot'. Run instalar.ps1 from the deploy\ folder."
}

Copy-Item -Path "$sourceRoot\agente\*"    -Destination "$InstallDir\agente\"   -Recurse -Force
Write-Ok "agente package"

Copy-Item -Path "$sourceRoot\scripts\*.py" -Destination "$InstallDir\scripts\" -Force
Write-Ok "entry-point scripts"

Copy-Item -Path "$sourceRoot\requirements.txt" -Destination "$InstallDir\" -Force
Write-Ok "requirements.txt"


Write-Step "Locating Python 3 interpreter"
try {
    $pythonExe = (Get-Command python.exe -ErrorAction Stop).Source
    $pyVersion = & $pythonExe --version 2>&1
    Write-Ok "$pythonExe  ($pyVersion)"
} catch {
    Write-Fail "python.exe not found in PATH. Install Python 3.13+ and add it to the system PATH."
}

Write-Step "Creating virtualenv at $InstallDir\.venv"
& $pythonExe -m venv "$InstallDir\.venv"
if ($LASTEXITCODE -ne 0) { Write-Fail "venv creation failed." }
Write-Ok ".venv created"

Write-Step "Installing Python dependencies"
& "$InstallDir\.venv\Scripts\pip.exe" install --quiet -r "$InstallDir\requirements.txt"
if ($LASTEXITCODE -ne 0) { Write-Fail "pip install failed." }
Write-Ok "dependencies installed"


Write-Step "Writing $InstallDir\config.json"

$configObj = @{
    api_url               = $ApiUrl
    token                 = $Token
    sala_codigo           = $SalaCode
    datos_dir             = ($InstallDir + "\data") -replace "\\", "/"
    log_dir               = ($InstallDir + "\logs") -replace "\\", "/"
    intervalo_captura_min = 10
    hora_envio            = "14:00"
    timeout_envio_seg     = 60
    reintentos_envio      = 3
    dias_retencion_local  = 7
}
$jsonContent = $configObj | ConvertTo-Json -Depth 3

# PowerShell 5.1's Out-File -Encoding utf8 adds a BOM that breaks Python's
# json.load. Use the .NET API directly to write UTF-8 without BOM.
$utf8NoBom = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText("$InstallDir\config.json", $jsonContent, $utf8NoBom)

Write-Ok "$InstallDir\config.json"



Write-Step "Importing Task Scheduler tasks for $installUser"

$tasksDir = "$PSScriptRoot\tareas"
$tasks = @(
    @{ Xml = "captura.xml";  Name = "\Monitoreo\Captura"  },
    @{ Xml = "envio.xml";    Name = "\Monitoreo\Envio"    },
    @{ Xml = "arranque.xml"; Name = "\Monitoreo\Arranque" }
)

foreach ($task in $tasks) {
    $xmlPath = "$tasksDir\$($task.Xml)"
    if (-not (Test-Path $xmlPath)) {
        Write-Fail "Missing task XML: $xmlPath"
    }

    # Replace placeholder with the resolved account
    $xmlContent = [System.IO.File]::ReadAllText($xmlPath, [System.Text.Encoding]::UTF8)
    $xmlContent  = $xmlContent -replace "__INSTALL_USER__", $installUser
    $tmpXml      = "$env:TEMP\monitoreo_$($task.Xml)"
    $utf8NoBom   = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($tmpXml, $xmlContent, $utf8NoBom)

    # Delete existing task silently (ignore failure if it doesn't exist yet)
    $null = schtasks /Delete /TN $task.Name /F 2>&1

    # Import fresh
    schtasks /Create /XML $tmpXml /TN $task.Name /F | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Remove-Item $tmpXml -Force -ErrorAction SilentlyContinue
        Write-Fail "schtasks /Create failed for $($task.Name). Check permissions."
    }
    Remove-Item $tmpXml -Force
    Write-Ok "$($task.Name)"
}



Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host "  Installation complete." -ForegroundColor Green
Write-Host ""
Write-Host "  Install dir  : $InstallDir" -ForegroundColor Green
Write-Host "  Student user : $installUser" -ForegroundColor Green
Write-Host "  Sala         : $SalaCode" -ForegroundColor Green
Write-Host "  API URL      : $ApiUrl" -ForegroundColor Green
Write-Host ""
Write-Host "  Scheduled tasks registered:" -ForegroundColor Green
Write-Host "    \Monitoreo\Captura   — every 10 min while estudiante is logged in" -ForegroundColor Green
Write-Host "    \Monitoreo\Envio     — daily at 14:00" -ForegroundColor Green
Write-Host "    \Monitoreo\Arranque  — once at login (orphan retry)" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
