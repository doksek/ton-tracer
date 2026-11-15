"""Запуск приложения:  python -m app

По умолчанию программа открывается отдельным окном (если установлен pywebview),
иначе — вкладкой в браузере. Сервер слушает только 127.0.0.1: из сети его не видно.

  python -m app --browser      всегда открывать в браузере
  python -m app --port 9000    другой порт
  python -m app --no-open      только запустить сервер (адрес будет напечатан)
"""
from __future__ import annotations

import argparse
import logging
import os
import socket
import sys
import threading
import webbrowser

from werkzeug.serving import make_server

from . import APP_NAME, __version__
from .web.server import create_app

HOST = "127.0.0.1"


def free_port(preferred: int) -> int:
    """Берём привычный порт, а если он занят — любой свободный."""
    for port in (preferred, 0):
        with socket.socket() as s:
            try:
                s.bind((HOST, port))
                return s.getsockname()[1]
            except OSError:
                continue
    raise RuntimeError("Не удалось найти свободный порт")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app", description=f"{APP_NAME} {__version__}")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--browser", action="store_true", help="открыть в браузере, а не в отдельном окне")
    ap.add_argument("--no-open", action="store_true", help="ничего не открывать, только запустить сервер")
    ap.add_argument("-v", "--verbose", action="store_true", help="подробный журнал в консоли")
    a = ap.parse_args(argv)

    # В собранном приложении без консоли печатать некуда — без этого print() упал бы.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")

    logging.basicConfig(level=logging.INFO if a.verbose else logging.WARNING, format="%(message)s")
    logging.getLogger("werkzeug").setLevel(logging.INFO if a.verbose else logging.ERROR)

    port = free_port(a.port)
    url = f"http://{HOST}:{port}/"
    server = make_server(HOST, port, create_app(), threaded=True)
    print(f"{APP_NAME} {__version__} запущен: {url}")

    webview = None
    if not a.browser and not a.no_open:
        try:
            import webview  # pywebview — необязательная зависимость
        except Exception:
            webview = None

    if webview is None:
        if not a.no_open:
            threading.Timer(0.6, webbrowser.open, args=(url,)).start()
        print("Чтобы закрыть программу, нажмите Ctrl+C в этом окне.")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        return 0

    # Режим отдельного окна: сервер в фоне, окно — в главном потоке (так требует pywebview).
    threading.Thread(target=server.serve_forever, daemon=True).start()
    webview.settings["ALLOW_DOWNLOADS"] = True                  # кнопки «Скачать отчёт»
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True   # ссылки на Tonviewer — в обычный браузер
    webview.create_window(APP_NAME, url, width=1360, height=880, min_size=(900, 620), text_select=True)
    webview.start()
    server.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
