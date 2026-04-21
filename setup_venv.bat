@echo off
:: Bootstrap script — creates .venv and installs all dependencies
:: Run once on a new machine: setup_venv.bat

echo [cdash-digester] Creating virtual environment...
python -m venv .venv
if errorlevel 1 (
    echo ERROR: python not found or venv creation failed.
    exit /b 1
)

echo [cdash-digester] Upgrading pip...
.venv\Scripts\python.exe -m pip install --upgrade pip

echo [cdash-digester] Installing dependencies...
.venv\Scripts\pip.exe install -r requirements.txt

echo [cdash-digester] Installing package in editable mode...
.venv\Scripts\pip.exe install -e .

echo.
echo Done. Activate the environment with:
echo   .venv\Scripts\activate
