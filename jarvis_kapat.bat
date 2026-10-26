@echo off
rem Stops Jarvis running in the background (hidden or visible)
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -like '*personal-ai-assistant\.venv\*' -and $_.CommandLine -like '*main.py*' } | ForEach-Object { taskkill /PID $_.ProcessId /T /F | Out-Null }"
