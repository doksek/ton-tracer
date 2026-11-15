"""Где программа хранит свои данные.

Расследования, кэш и настройки лежат в домашней папке пользователя (~/.ton-tracer),
а не рядом с кодом: так они не попадут в git и переживут обновление программы.
Папку можно переопределить переменной окружения TON_TRACER_HOME.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def data_dir() -> Path:
    p = Path(os.environ.get("TON_TRACER_HOME") or Path.home() / ".ton-tracer")
    p.mkdir(parents=True, exist_ok=True)
    return p


def resource_dir() -> Path:
    """Папка app/ с шаблонами и статикой.

    В собранном PyInstaller-приложении файлы распакованы во временную папку sys._MEIPASS.
    """
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(base) / "app"
    return Path(__file__).resolve().parents[1]
