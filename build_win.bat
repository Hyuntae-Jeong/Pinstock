@echo off
REM ====================================================================
REM Pinstock Windows build script
REM   Equivalent to: .venv\Scripts\pyinstaller.exe pinstock.spec --noconfirm
REM   Output: dist\Pinstock\Pinstock.exe (and the whole folder)
REM   Version comes from pinstock\__version__.py as-is.
REM   NOTE: ASCII-only on purpose. cmd.exe reads .bat in the OEM codepage
REM         (CP949 on Korean Windows), so non-ASCII comments get garbled.
REM ====================================================================

setlocal

REM Move to this .bat file's folder (= project root)
cd /d "%~dp0"

set "PYI=.venv\Scripts\pyinstaller.exe"

if not exist "%PYI%" (
    echo [ERROR] %PYI% not found. Check the venv / PyInstaller install.
    echo         pip install pyinstaller
    exit /b 1
)

"%PYI%" pinstock.spec --noconfirm
if errorlevel 1 (
    echo [ERROR] PyInstaller build failed.
    exit /b 1
)

echo.
echo [DONE] dist\Pinstock\Pinstock.exe
endlocal
