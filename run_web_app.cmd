@echo off
setlocal
cd /d "%~dp0"
uv run python schedule_web_app.py --input csv_demo_input --output solution.json --port 8000 %*
