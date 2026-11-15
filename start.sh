#!/bin/sh
# Запуск TON Tracer на macOS и Linux:  sh start.sh
# При первом запуске создаётся папка .venv и ставятся зависимости.
cd "$(dirname "$0")" || exit 1
if [ ! -d .venv ]; then
  python3 -m venv .venv || { echo "Нужен Python 3.10+"; exit 1; }
  .venv/bin/python -m pip install --upgrade pip
  # Отдельное окно — по возможности; если pywebview не ставится, программа откроется в браузере.
  .venv/bin/python -m pip install -r requirements-window.txt || .venv/bin/python -m pip install -r requirements.txt
fi
exec .venv/bin/python -m app "$@"
