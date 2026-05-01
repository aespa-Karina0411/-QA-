@echo off
cd /d "C:\Users\lichengjun\Desktop\edge-visionQA\tests"
echo ============================================================
echo   Step 1: Running simulate_log.py
echo ============================================================
python.exe simulate_log.py
if %errorlevel% neq 0 (
    echo.
    echo simulate_log.py exited with error code %errorlevel%
    echo.
    echo ============================================================
    echo   Step 2: Running run_validation.py
    echo ============================================================
) else (
    echo.
    echo ============================================================
    echo   Step 2: Running run_validation.py
    echo ============================================================
)
python.exe run_validation.py
echo.
echo ============================================================
echo   Scripts completed
echo ============================================================
pause
