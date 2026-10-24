@echo off
rem Jarvis'i yazılı modda (klavyeyle) başlatır
chcp 65001 >nul
title Jarvis (yazılı)
cd /d "%~dp0"
.venv\Scripts\python main.py --yazi
pause
