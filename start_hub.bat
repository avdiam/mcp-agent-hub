@echo off
REM ============================================================
REM  Start the MCP Agent Hub server (portable, self-healing).
REM  Double-click, or run from any directory, on EITHER PC.
REM
REM  Works on both PCs from a single file because:
REM   * the project folder is derived from this .bat's own
REM     location (%~dp0), not a hard-coded path; and
REM   * the venv is machine-local (gitignored). It is created
REM     here on first run and rebuilt automatically if it is
REM     missing or broken (e.g. a venv copied from the other PC
REM     whose base interpreter path doesn't exist here).
REM ============================================================

setlocal EnableDelayedExpansion

REM Project root = the folder this .bat lives in (with trailing backslash).
set "HUB_DIR=%~dp0"
set "VENV_PY=%HUB_DIR%venv\Scripts\python.exe"

title MCP Agent Hub

REM --- Is there a venv that actually RUNS on this machine? ---
REM (A venv copied from the other PC can exist yet fail to start,
REM  so we test execution, not just file presence.)
REM NOTE: keep comments inside the if-blocks below free of ( ) characters --
REM cmd counts parens even inside REM lines and would close the block early.
set "VENV_OK="
if exist "%VENV_PY%" (
    "%VENV_PY%" -c "import sys" >nul 2>&1 && set "VENV_OK=1"
)

if not defined VENV_OK (
    echo [start_hub] No working venv found for this PC. Bootstrapping...

    REM Remove a broken or foreign venv if one is present.
    if exist "%HUB_DIR%venv" (
        echo [start_hub] Removing broken venv...
        rmdir /s /q "%HUB_DIR%venv"
    )

    REM Pick a Python to build the venv: prefer the py launcher, else python.
    REM Delayed expansion !BOOT_PY! is required here -- a %VAR% set and used
    REM in the same block would expand too early.
    set "BOOT_PY=python"
    where py >nul 2>&1 && set "BOOT_PY=py"

    echo [start_hub] Creating venv with: !BOOT_PY!
    !BOOT_PY! -m venv "%HUB_DIR%venv"
    if not exist "%VENV_PY%" (
        echo [start_hub] ERROR: venv creation failed. Is Python installed and on PATH?
        pause
        exit /b 1
    )

    echo [start_hub] Installing dependencies, one-time, may take a minute...
    "%VENV_PY%" -m pip install --upgrade pip
    "%VENV_PY%" -m pip install -r "%HUB_DIR%requirements.txt"
    if errorlevel 1 (
        echo [start_hub] ERROR: dependency install failed. See output above.
        pause
        exit /b 1
    )
    echo [start_hub] Bootstrap complete.
    echo.
)

REM cd into the project so "mcp_hub.hub:app" imports and logs\ resolve.
cd /d "%HUB_DIR%"

echo [start_hub] Project : %HUB_DIR%
echo [start_hub] Python   : %VENV_PY%
echo [start_hub] Dashboard: http://localhost:8000
echo [start_hub] MCP      : http://localhost:8000/mcp
echo [start_hub] Logs     : %HUB_DIR%logs\hub.log
echo [start_hub] Press Ctrl+C to stop the supervisor.
echo.

"%VENV_PY%" run_hub.py

set "RC=%ERRORLEVEL%"
echo.
echo [start_hub] Supervisor exited with code %RC%.

REM Keep the window open when launched by double-click so errors stay visible.
pause

endlocal
