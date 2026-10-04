@echo off
rem Double-click to start the trading agent web interface.
cd /d "%~dp0"
".venv\Scripts\python.exe" app\server.py %*
