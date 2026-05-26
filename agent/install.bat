@echo off
:: =============================================================
:: install.bat
:: Installs the Softracker agent on a Windows client machine.
:: Run this script as Administrator once per machine.
:: The agent will launch automatically at user login via the
:: Windows Task Scheduler task imported from ..\tasks\tarea_programada.xml.
::
:: Author: Daniel Santiago Perez Madera <dsperezm@udistrital.edu.co>
:: =============================================================

echo ============================================
echo  Softracker Agent - Installation
echo ============================================
echo.

:: --- Verify Python ---------------------------------------------------
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python is not installed. Download it from https://python.org
    pause
    exit /b 1
)

:: --- Install Python dependencies -------------------------------------
echo [1/4] Installing Python dependencies...
pip install -r "%~dp0requirements.txt" --quiet
if errorlevel 1 (
    echo [ERROR] pip install failed. Check your internet connection.
    pause
    exit /b 1
)

:: --- Copy agent files to C:\monitoreo\ ------------------------------
echo [2/4] Copying agent files to C:\monitoreo\ ...
if not exist "C:\monitoreo" mkdir "C:\monitoreo"
copy /Y "%~dp0monitor.py"   "C:\monitoreo\monitor.py"   >nul
copy /Y "%~dp0config.json"  "C:\monitoreo\config.json"  >nul

echo.
echo  IMPORTANT: Edit C:\monitoreo\config.json before proceeding.
echo   - Set sala_id to the room number (e.g. 1, 2, 3 ...)
echo   - Set machine_name to this PC name (e.g. SALA01-PC05)
echo   - Set sala_token to the token for this room
echo   - Set api_url to the server address
echo.
pause

:: --- Register Scheduled Task -----------------------------------------
echo [3/4] Registering Scheduled Task...
set "XML_PATH=%~dp0..\tasks\tarea_programada.xml"
if not exist "%XML_PATH%" (
    echo [WARN] Task XML not found at %XML_PATH%. Skipping task registration.
    goto :DONE
)
schtasks /create /tn "\Universidad\SoftrackerMonitor" /xml "%XML_PATH%" /f
if errorlevel 1 (
    echo [WARN] Could not register the task. You may need to run as Administrator.
) else (
    echo Task registered successfully.
)

:DONE
echo [4/4] Done.
echo.
echo To test the agent manually, run:
echo     python C:\monitoreo\monitor.py
echo.
echo To run without a console window:
echo     pythonw C:\monitoreo\monitor.py
echo.
pause
