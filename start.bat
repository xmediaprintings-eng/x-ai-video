@echo off
setlocal
cd /d "%~dp0"
title X Media AI Video

rem ---------- Python ----------
python --version >nul 2>&1
if not errorlevel 1 goto have_python
echo [X Media] Python not found. Installing Python 3.12 with winget...
winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
echo.
echo Python installed. Please CLOSE this window and double-click start.bat again.
pause
exit /b
:have_python

rem ---------- FFmpeg ----------
where ffmpeg >nul 2>&1
if not errorlevel 1 goto have_ffmpeg
echo [X Media] FFmpeg not found. Installing FFmpeg with winget...
winget install -e --id Gyan.FFmpeg --accept-source-agreements --accept-package-agreements
echo.
echo FFmpeg installed. Please CLOSE this window and double-click start.bat again.
pause
exit /b
:have_ffmpeg

rem ---------- Python packages ----------
if exist ".venv\Scripts\python.exe" goto have_venv
echo [X Media] First run: creating Python environment...
python -m venv .venv
:have_venv
set "PY=.venv\Scripts\python.exe"

fc /b requirements.txt .venv\req.txt >nul 2>&1
if not errorlevel 1 goto packages_ok
echo [X Media] Installing packages - first run takes a few minutes...
"%PY%" -m pip install --upgrade pip
"%PY%" -m pip install -r requirements.txt
if errorlevel 1 goto pip_failed
echo [X Media] Installing auto-captions package - optional...
"%PY%" -m pip install faster-whisper
if errorlevel 1 echo [X Media] Auto-captions package failed. The app still works without captions.
copy /y requirements.txt .venv\req.txt >nul
:packages_ok

rem ---------- Run ----------
"%PY%" -m app.launcher
pause
exit /b

:pip_failed
echo.
echo [X Media] Package install failed. Check your internet connection and try again.
pause
exit /b 1
