@echo off
REM ShipLog installer for Windows
REM Creates a virtual environment, installs dependencies, and runs the app.

echo =========================================
echo   ShipLog — Marine Project Manager
echo   Installer for Windows
echo =========================================
echo.

REM Remember script directory
set "SCRIPT_DIR=%~dp0"

REM Check Python
where python >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found. Please install Python 3.11+.
    echo Download from https://www.python.org/downloads/
    pause
    exit /b 1
)

for /f "tokens=*" %%i in ('python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"') do set PYVER=%%i
echo Found Python %PYVER%
echo.

REM Create virtual environment if not exists
if not exist "%SCRIPT_DIR%.venv" (
    echo [1/2] Creating virtual environment...
    python -m venv "%SCRIPT_DIR%.venv"
    echo       Done.
) else (
    echo [1/2] Virtual environment already exists.
)
echo.

REM Activate and install
echo [2/2] Installing dependencies...
echo.
call "%SCRIPT_DIR%.venv\Scripts\activate.bat"
python -m pip install -r "%SCRIPT_DIR%requirements.txt" --trusted-host pypi.org --trusted-host files.pythonhosted.org
echo.

echo =========================================
echo   Installation complete!
echo =========================================
echo.
echo Starting ShipLog now...
echo.
python "%SCRIPT_DIR%main.py"
pause
