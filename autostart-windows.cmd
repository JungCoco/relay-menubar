@echo off
REM 로그인 시 Relay 위젯 자동 실행 (시작프로그램에 등록, 콘솔창 없이)
set ROOT=%~dp0
set VBS=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\relay-menubar.vbs
> "%VBS%" echo CreateObject("Wscript.Shell").Run """%ROOT%.venv\Scripts\pythonw.exe"" ""%ROOT%menubar.py""", 0, False
echo 자동 실행 등록 완료: %VBS%
echo 해제하려면 위 파일을 삭제하세요.
