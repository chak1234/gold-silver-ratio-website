@echo off
setlocal
cd /d "%~dp0.."

where py >nul 2>&1
if errorlevel 1 (
    echo Python was not found. Install it from https://www.python.org/downloads/
    pause
    exit /b 1
)

if not exist .venv\Scripts\python.exe (
    echo Creating virtual environment...
    py -3 -m venv .venv || goto :failed
)

echo Installing dependencies...
.venv\Scripts\python -m pip install --quiet -r requirements.txt || goto :failed

if not exist .env (
    copy .env.example .env >nul
    echo Created .env. Add your METALPRICE_API_KEY to it, then run this script again.
    pause
    exit /b 1
)

start "" http://127.0.0.1:5000
.venv\Scripts\python -m goldsilver
exit /b %errorlevel%

:failed
echo Setup failed. See the messages above.
pause
exit /b 1
