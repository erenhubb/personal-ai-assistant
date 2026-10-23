@echo off
rem Jarvis'i çift tıklayarak başlatır
chcp 65001 >nul
title Jarvis
cd /d "%~dp0"
.venv\Scripts\python main.py
pause
