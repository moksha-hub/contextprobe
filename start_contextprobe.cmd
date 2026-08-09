@echo off
setlocal
cd /d "%~dp0"
title Contextprobe local demo
set "CONTEXTPROBE_SIMULATION_ONLY=true"
if /I "%~1"=="real" set "CONTEXTPROBE_SIMULATION_ONLY=false"

echo [Contextprobe] Preparing the local browser demo...
where py >nul 2>nul || (echo ERROR: Install Python 3.10 or newer. & pause & exit /b 1)
where npm >nul 2>nul || (echo ERROR: Install Node.js 20 or newer. & pause & exit /b 1)

if /I "%~1"=="reset" (
  if exist "backend\contextprobe.db" del /q "backend\contextprobe.db"
  echo [Contextprobe] Local fixture reset.
)

if not exist ".venv\Scripts\python.exe" (
  echo [1/4] Creating Python environment...
  py -3 -m venv .venv || goto :failed
)

if not exist ".venv\.contextprobe-ready" (
  echo [2/4] Installing Python dependencies...
  ".venv\Scripts\python.exe" -m pip install -r "backend\requirements.txt" || goto :failed
  type nul > ".venv\.contextprobe-ready"
) else (
  echo [2/4] Python dependencies ready.
)

if not exist "frontend\node_modules" (
  echo [3/4] Installing frontend dependencies...
  pushd frontend
  call npm ci || (popd & goto :failed)
  popd
) else (
  echo [3/4] Frontend dependencies ready.
)

if not exist "frontend\dist\index.html" (
  echo [4/4] Building browser interface...
  pushd frontend
  call npm run build || (popd & goto :failed)
  popd
) else (
  echo [4/4] Browser interface ready.
)

if /I "%~1"=="check" (
  echo [Contextprobe] Launcher setup check passed.
  exit /b 0
)

echo.
echo Opening http://127.0.0.1:8000
echo Press Ctrl+C in this window to stop Contextprobe.
start "" powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 2; Start-Process 'http://127.0.0.1:8000'"
".venv\Scripts\python.exe" -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
exit /b %ERRORLEVEL%

:failed
echo.
echo ERROR: Setup failed. Read the message above, then run this file again.
pause
exit /b 1