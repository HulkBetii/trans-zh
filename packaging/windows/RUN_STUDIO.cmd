@echo off
setlocal

set "PROJECT_DIR=%~dp0"
set "STUDIO_URL=http://127.0.0.1:8756"

cd /d "%PROJECT_DIR%"

where uv >nul 2>nul
if errorlevel 1 (
    echo uv is not installed. Run INSTALL_WINDOWS.cmd first.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo zhsub is not installed. Run INSTALL_WINDOWS.cmd first.
    pause
    exit /b 1
)

powershell.exe -NoLogo -NoProfile -Command "try { Invoke-WebRequest -UseBasicParsing -Uri '%STUDIO_URL%' -TimeoutSec 2 ^| Out-Null; exit 0 } catch { exit 1 }"
if not errorlevel 1 (
    start "" "%STUDIO_URL%"
    exit /b 0
)

start "" /b powershell.exe -NoLogo -NoProfile -WindowStyle Hidden -Command "$url = '%STUDIO_URL%'; for ($attempt = 0; $attempt -lt 120; $attempt++) { try { Invoke-WebRequest -UseBasicParsing -Uri $url -TimeoutSec 1 | Out-Null; Start-Process $url; exit } catch { Start-Sleep -Milliseconds 500 } }"
uv run --frozen zhsub web

if errorlevel 1 (
    echo.
    echo Studio failed to start. Review the error above.
    pause
)
