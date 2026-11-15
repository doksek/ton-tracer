@echo off
rem Запуск TON Tracer на Windows: двойной клик по этому файлу.
rem При первом запуске создаётся папка .venv и ставятся зависимости (нужен Python 3.10+ с python.org).
cd /d "%~dp0"
if not exist .venv (
  python -m venv .venv || goto :nopython
  .venv\Scripts\python -m pip install --upgrade pip
  .venv\Scripts\python -m pip install -r requirements-window.txt || .venv\Scripts\python -m pip install -r requirements.txt
)
.venv\Scripts\python -m app
goto :eof
:nopython
echo Python ne naiden. Ustanovite Python 3.10+ s https://www.python.org/downloads/ i otmet'te "Add python.exe to PATH".
pause
