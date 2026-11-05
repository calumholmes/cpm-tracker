@echo off
rem cpm-tracker launcher for Windows: sets up a virtual environment on first run, then starts the bot.
cd /d "%~dp0"

if not exist .venv (
  echo First run: setting up cpm-tracker...
  py -3 -m venv .venv 2>nul || python -m venv .venv || (
    echo.
    echo Python 3.10 or newer is required. Get it from https://www.python.org/downloads/
    echo and tick "Add python.exe to PATH" during install.
    pause & exit /b 1
  )
  .venv\Scripts\python -m pip install --upgrade pip
)

.venv\Scripts\python -m pip install -q -r requirements.txt || (
  echo.
  echo Installing dependencies failed. Is Git installed? Get it from https://git-scm.com/downloads
  pause & exit /b 1
)
.venv\Scripts\python bot.py
pause
