@echo off
setlocal ENABLEDELAYEDEXPANSION

rem Resolve repository root relative to this script
set "SCRIPT_DIR=%~dp0"
set "ROOT_DIR=%SCRIPT_DIR%.."

if "%VIRTUAL_ENV%"=="" (
    echo [INFO] No active virtual environment detected. Using system Python.
) else (
    echo [INFO] Using Python interpreter from %VIRTUAL_ENV%
)

echo [INFO] Launching CiphERA verifier node 1 on port 8001...
start "CiphERA Node 1" cmd /k "cd /d %ROOT_DIR%\node1 && python -m uvicorn app:app --host 0.0.0.0 --port 8001"

echo [INFO] Launching CiphERA verifier node 2 on port 8002...
start "CiphERA Node 2" cmd /k "cd /d %ROOT_DIR%\node2 && python -m uvicorn app:app --host 0.0.0.0 --port 8002"

echo [INFO] Launching CiphERA gateway on port 8000...
start "CiphERA Gateway" cmd /k "cd /d %ROOT_DIR%\gateway && set SECRET=CIPHERA_KEY && set NODES=[\"http://127.0.0.1:8001\",\"http://127.0.0.1:8002\"] && python -m uvicorn main_api:app --host 0.0.0.0 --port 8000"

echo [INFO] Serving web assets from /web on http://127.0.0.1:8080 ...
start "CiphERA Web" cmd /k "cd /d %ROOT_DIR%\web && python -m http.server 8080"

echo [INFO] Allowing web server to initialise...
timeout /t 2 /nobreak >nul
echo [INFO] Opening demo portal in your default browser.
start "" "http://127.0.0.1:8080/"

echo [INFO] All components launched. Close their windows to stop services.
endlocal
