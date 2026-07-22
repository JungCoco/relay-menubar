@echo off
REM Relay 트레이 위젯 실행 (Windows) — 콘솔창 없이 백그라운드
set ROOT=%~dp0
start "" "%ROOT%.venv\Scripts\pythonw.exe" "%ROOT%menubar.py" %*
