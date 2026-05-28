@echo off
setlocal
cd /d "%~dp0"
start "" "http://127.0.0.1:8001"
uv run python schedule_web_app.py --input csv_demo_input --output solution.json --port 8001 %*
