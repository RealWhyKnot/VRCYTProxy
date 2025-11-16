@echo off
setlocal

set "LOG_FILE=build_log.txt"
set "BUILD_DIR=build"
del "%LOG_FILE%" 2>nul

echo Running build... this may take a moment.
echo Real-time output will be shown below, and a full log saved to %LOG_FILE%.
echo ========================================================================
echo.

:: Use PowerShell to execute the internal script and provide "tee" functionality.
powershell -NoProfile -Command "cmd /c _build_internal.bat | ForEach-Object { $_; Add-Content -Path '%LOG_FILE%' -Value $_ }"
set BUILD_EXIT_CODE=%errorlevel%

:: Final cleanup
if exist "%BUILD_DIR%" (
    echo Cleaning up intermediate build files...
    rmdir /s /q "%BUILD_DIR%"
)
del Building 2>nul
del patcher 2>nul
del redirector 2>nul


echo.
echo ========================================================================
echo --- Build process finished. Full log saved to %LOG_FILE% ---

if %BUILD_EXIT_CODE% neq 0 (
    echo.
    echo *** BUILD FAILED ***
    pause
) else (
    echo.
    echo *** BUILD SUCCEEDED ***
)

endlocal
exit /b %BUILD_EXIT_CODE%


