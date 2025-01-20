@echo off

:: Virtual environment directory name
set "VENV_DIR=venv"

:: Check if Python is installed
where python >nul 2>nul
if %errorlevel% neq 0 (
    echo Python is not installed. Please install Python before running this script.
    pause
    exit /b 1
)

:: Create virtual environment if it doesn't exist
if not exist "%VENV_DIR%" (
    echo Creating virtual environment in directory %VENV_DIR%
    python -m venv %VENV_DIR%
) else (
    echo Virtual environment already exists in directory %VENV_DIR%
)

:: Activate virtual environment
call "%VENV_DIR%\Scripts\activate.bat"

:: Upgrade pip and install required packages
echo Upgrading pip and installing dependencies...
pip install --upgrade pip
pip install -r requirements.txt
:: Pause before exit
echo Successfully installed
pause
