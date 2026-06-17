@echo off
setlocal
cd /d "%~dp0"
if not exist "csv_runtime_input\config.csv" (
    echo Creating csv_runtime_input from csv_demo_input...
    xcopy "csv_demo_input" "csv_runtime_input" /E /I /Y >nul
)
start "" "http://127.0.0.1:8001"
uv run python schedule_web_app.py --input csv_runtime_input --output solution.json --port 8001 %*
