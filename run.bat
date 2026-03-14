@echo off
REM ShipLog for Windows

echo =========================================
echo   ShipLog - Marine Project Manager
echo =========================================
echo.

REM Remember script directory
set "SCRIPT_DIR=%~dp0"

call "%SCRIPT_DIR%.venv\Scripts\activate.bat"

REM --- Launch the application ---
echo    Starting ShipLog now...
echo.
echo  ==========================================
echo   App is running. Close this window or
echo   press Ctrl+C to stop.
echo  ==========================================
echo.

echo.
pythonw "%SCRIPT_DIR%main.py"
pause
