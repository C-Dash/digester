@echo off
if "%~1"=="" (
    echo Drag a media file onto this batch file to prescreen it.
    pause
    exit /b 1
)
cd /d "%~dp0"
call .venv\Scripts\activate.bat
set PYTHONPATH=src
python -m cdash_digester.prescreener "%~1"
pause
