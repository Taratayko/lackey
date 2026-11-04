@echo off
title Lackey

cd /d "%~dp0"

echo ==================================================
echo Starting Commune Mesh Lackey
echo Project dir: %cd%
echo ==================================================

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" bot.py
) else (
    py -3.13 bot.py
)

pause
