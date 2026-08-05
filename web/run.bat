@echo off
setlocal
chcp 65001 >nul
title Web Novel EPUB Translator - Web GUI
cd /d "%~dp0"

set "PY="
py -3.13 -c "import sys" >nul 2>nul && set "PY=py -3.13"
if not defined PY py -3.12 -c "import sys" >nul 2>nul && set "PY=py -3.12"
if not defined PY py -3.11 -c "import sys" >nul 2>nul && set "PY=py -3.11"
if not defined PY python -c "import sys; sys.exit(0 if (3, 11) <= sys.version_info < (3, 14) else 1)" >nul 2>nul && set "PY=python"

if not defined PY (
    echo This app needs Python 3.11, 3.12, or 3.13.
    pause
    exit /b 1
)

if not exist .venv (
    echo First run: setting up the environment...
    %PY% -m venv .venv
)

call .venv\Scripts\activate.bat

echo Checking dependencies...
python -m pip install --disable-pip-version-check -q -r requirements.txt
if errorlevel 1 (
    echo Failed to install dependencies.
    pause
    exit /b 1
)

echo Starting web GUI at http://127.0.0.1:8177
python app.py
pause
