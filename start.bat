@echo off
cd /d "%~dp0"
if exist ".runtime\python\python.exe" (
  ".runtime\python\python.exe" app.py
) else if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" app.py
) else (
  echo Run setup.ps1 first: right-click it and choose Run with PowerShell.
)
pause
