@echo off
setlocal enabledelayedexpansion

:: This is the internal build script. It is called by build.bat.

echo =================================================
echo      VRCYTProxy Development Build Script
echo =================================================
echo.

set "PYTHON_EXE=python"
set "BUILD_DIR=build"
set "DIST_DIR=dist"
set "REDIRECTOR_BUILD_DIR=%BUILD_DIR%\redirector_build"
set "PATCHER_BUILD_DIR=%BUILD_DIR%\patcher_build"

:: --- Safety Checks ---
if /I not "%BUILD_DIR%"=="build" (
    echo SAFETY FAIL: BUILD_DIR is not 'build'. Aborting.
    exit /b 1
)
if /I not "%DIST_DIR%"=="dist" (
    echo SAFETY FAIL: DIST_DIR is not 'dist'. Aborting.
    exit /b 1
)

:: --- Step 1: Environment Setup ---
echo [1/5] Setting up Python environment...
if not exist .venv (
    %PYTHON_EXE% -m venv .venv
)
call .venv\Scripts\activate.bat

set "VENV_PYTHON=.venv\Scripts\python.exe"

echo.
echo [Step 2/5] Installing dependencies (with custom bootloader)...
echo NOTE: This step compiles the PyInstaller bootloader and requires a C compiler.
echo For Windows, please ensure you have the Visual C++ build tools installed.
echo.
set "PYINSTALLER_COMPILE_BOOTLOADER=1"
%VENV_PYTHON% -m pip install --upgrade pip
pip install --force-reinstall --no-cache-dir pyinstaller
if !errorlevel! neq 0 (
    echo ERROR: Failed to install dependencies.
    exit /b 1
)
echo Environment ready.
echo.

:: --- Step 3: Clean Directories ---
echo [3/5] Cleaning previous build and dist directories...
if exist "%BUILD_DIR%" ( rmdir /s /q "%BUILD_DIR%" )
if exist "%DIST_DIR%" ( rmdir /s /q "%DIST_DIR%" )
mkdir "%BUILD_DIR%"
mkdir "%DIST_DIR%"
echo Directories cleaned.
echo.

:: --- Step 4: Build Components ---
echo [4/5] Building executables (folder mode)...
echo   -> Building Redirector...
pyinstaller --noconfirm --noupx --distpath "%REDIRECTOR_BUILD_DIR%" --workpath "%BUILD_DIR%\redirector_work" --specpath "%BUILD_DIR%" --name "main" src/yt_dlp_redirect/main.py
if !errorlevel! neq 0 (
    echo ERROR: Failed to build the redirector.
    exit /b 1
)
echo   -> Redirector build complete.

echo   -> Building Patcher...
pyinstaller --noconfirm --noupx --distpath "%PATCHER_BUILD_DIR%" --workpath "%BUILD_DIR%\patcher_work" --specpath "%BUILD_DIR%" --name "patcher" src/patcher/main.py
if !errorlevel! neq 0 (
    echo ERROR: Failed to build the patcher.
    exit /b 1
)
echo   -> Patcher build complete.
echo.

:: --- Step 5: Assemble Final Application ---
echo [5/5] Assembling final application in '%DIST_DIR%'...
robocopy "%PATCHER_BUILD_DIR%\patcher" "%DIST_DIR%" /E > nul
mkdir "%DIST_DIR%\resources"
robocopy "%REDIRECTOR_BUILD_DIR%\main" "%DIST_DIR%\resources\wrapper_files" /E > nul
if !errorlevel! gtr 1 (
    echo ERROR: Failed to copy files into the final directory.
    exit /b 1
)
echo Assembly complete.
echo.

:: Final cleanup is now part of the main build.bat script
exit /b 0
