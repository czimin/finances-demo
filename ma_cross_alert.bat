@echo off
rem ma_cross_alert.bat - run scripts\ma_cross_alert.py with the project venv's
rem Python (system Python lacks pandas/yfinance/requests). Point Task Scheduler
rem at this file, or double-click it. No pause, so a scheduled run can finish.
setlocal
set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" (
    echo No .venv next to this script. Create it with:
    echo   python -m venv .venv
    echo   .venv\Scripts\python.exe -m pip install -r requirements.txt
    exit /b 1
)
"%PY%" "%~dp0scripts\ma_cross_alert.py" %*
exit /b %errorlevel%
