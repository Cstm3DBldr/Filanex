@echo off
REM Polymaker installer launcher for Windows.
REM
REM Order of preference:
REM   1. install.exe -- prebuilt standalone, no Python required.
REM   2. py launcher (py.exe).
REM   3. python on PATH.
REM   4. Error message pointing at python.org.
setlocal
set SCRIPT_DIR=%~dp0
if exist "%SCRIPT_DIR%install.exe" (
    "%SCRIPT_DIR%install.exe" %*
    goto :end
)
where py >nul 2>nul
if %errorlevel% == 0 (
    py "%SCRIPT_DIR%install.py" %*
    goto :end
)
where python >nul 2>nul
if %errorlevel% == 0 (
    python "%SCRIPT_DIR%install.py" %*
    goto :end
)
echo.
echo ERROR: install.exe is missing AND Python is not installed.
echo.
echo Either re-download a complete BBL-injection.zip (it should contain
echo install.exe), or install Python 3.9+ from https://www.python.org/downloads/
echo and run install.bat again.
echo.
pause
:end
endlocal
