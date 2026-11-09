@echo off
rem Starts the Hotel Management System on Windows.
rem First run: creates a virtual environment and installs Flask.
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Setting up for the first time. This needs an internet connection and takes a minute...
    py -m venv .venv
    if errorlevel 1 (
        python -m venv .venv
    )
    if not exist ".venv\Scripts\python.exe" (
        echo.
        echo Could not create the virtual environment. Please install Python 3.10 or newer from
        echo https://www.python.org/downloads/ and tick "Add python.exe to PATH" in the installer.
        pause
        exit /b 1
    )
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo Installing Flask failed. Check your internet connection and try again.
        pause
        exit /b 1
    )
)

echo.
echo Open http://127.0.0.1:5000 in your browser. Close this window or press Ctrl+C to stop.
echo.
".venv\Scripts\python.exe" app.py
pause
