"""Сборка в один исполняемый файл:  python build_app.py

Нужны: pip install -r requirements-window.txt pyinstaller
Файл появится в папке dist/. Собирать нужно на той системе, для которой он предназначен
(Windows-файл — на Windows, и так далее). На GitHub это делает .github/workflows/release.yml.
"""
import PyInstaller.__main__

PyInstaller.__main__.run([
    "run.py",
    "--name", "TON-Tracer",
    "--onefile",
    "--windowed",                      # без чёрного окна консоли
    "--add-data", "app/web:app/web",   # страницы, стили и скрипты интерфейса
    "--collect-all", "webview",        # pywebview подгружает части по имени — берём пакет целиком
    "--noconfirm",
])
