@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo Missing .venv at project root. Create it first.
  pause
  exit /b 1
)

".\.venv\Scripts\pythonw.exe" ".\backend\desktop_app.py"
