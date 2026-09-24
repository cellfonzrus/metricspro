@echo off
REM Double-click this file. First run installs everything (takes a few minutes).
cd /d "%~dp0"
if not exist .venv (
  echo Setting up for the first time...
  py -3 -m venv .venv || python -m venv .venv
  call .venv\Scripts\activate.bat
  python -m pip install --upgrade pip
  pip install -r requirements.txt
  python -m playwright install chromium
) else (
  call .venv\Scripts\activate.bat
)
if not exist credentials.csv copy credentials_TEMPLATE.csv credentials.csv >nul
python compare_prices.py %*
pause
