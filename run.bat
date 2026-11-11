@echo off
title AeroSplit Launcher
echo Starting AeroSplit...
echo.

:: Check for Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not added to your system PATH.
    echo Please download and install Python from https://www.python.org/
    echo Make sure to check the box "Add Python to PATH" during installation.
    echo.
    pause
    exit /b
)

:: Verify dependencies and run
echo Starting application window...
start "" pythonw app.py

if %errorlevel% neq 0 (
    echo Failed to launch using pythonw. Attempting with standard python...
    python app.py
)
