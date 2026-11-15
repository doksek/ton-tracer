"""Точка входа для сборки в исполняемый файл (PyInstaller). Обычный запуск: python -m app"""
import sys

from app.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
