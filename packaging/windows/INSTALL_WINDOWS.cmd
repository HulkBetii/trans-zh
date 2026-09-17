@echo off
setlocal

cd /d "%~dp0"

where uv >nul 2>nul
if errorlevel 1 (
    echo uv is required. Install it with: winget install astral-sh.uv
    pause
    exit /b 1
)

where ffmpeg >nul 2>nul
if errorlevel 1 (
    echo FFmpeg is required. Install it with: winget install Gyan.FFmpeg
    pause
    exit /b 1
)

if not exist "zhsub.toml" copy /y "zhsub.toml.example" "zhsub.toml" >nul

echo Installing zhsub and runtime dependencies...
uv sync --frozen --no-dev --extra web --extra chatgpt-web
if errorlevel 1 goto :failed

echo Installing Playwright Chromium...
uv run playwright install chromium
if errorlevel 1 goto :failed

echo.
echo Installation completed. Run RUN_STUDIO.cmd to open the tool.
pause
exit /b 0

:failed
echo.
echo Installation failed. Review the error above.
pause
exit /b 1
