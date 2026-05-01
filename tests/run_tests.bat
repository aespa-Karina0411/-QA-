@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

set PYTHON="C:\Users\lichengjun\AppData\Local\Programs\Python\Python313\python.exe"
set TESTS_DIR=C:\Users\lichengjun\Desktop\edge-visionQA\tests

cd /d "%TESTS_DIR%"

echo ============================================================
echo   Step 1: Running simulate_log.py
echo ============================================================
%PYTHON% simulate_log.py
echo.
echo simulate_log.py exit code: %ERRORLEVEL%
echo.

echo ============================================================
echo   Step 2: Running run_validation.py
echo ============================================================
%PYTHON% run_validation.py
echo.
echo run_validation.py exit code: %ERRORLEVEL%
echo.

echo ============================================================
echo   Both scripts completed
echo ============================================================

pause
