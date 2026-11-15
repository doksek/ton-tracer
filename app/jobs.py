"""Фоновые задачи: расследование идёт в отдельном потоке, а окно остаётся отзывчивым.

Интерфейс раз в секунду спрашивает состояние (сколько адресов пройдено, что сейчас проверяется).
Одновременно идёт одно расследование: без ключа TonAPI всё равно пускает около одного запроса в секунду.
"""
from __future__ import annotations

import logging
import threading
import time
import traceback

from .collectors.tonapi import Cancelled, TonAPIClient, TonAPIError
from .core.address import pretty
from .core.paths import data_dir
from .demo import FakeClient
from .pipeline import run_trace
from .storage import Storage

log = logging.getLogger("tontrace")


class JobManager:
    def __init__(self, storage: Storage):
        self.storage = storage
        self._lock = threading.Lock()
        self.current: dict | None = None   # состояние идущего расследования

    def progress(self, inv_id: int) -> dict | None:
        with self._lock:
            if self.current and self.current["id"] == inv_id:
                p = dict(self.current["progress"])
                p["elapsed"] = round(time.time() - self.current["started"])
                return p
        return None

    def busy(self) -> int | None:
        with self._lock:
            return self.current["id"] if self.current else None

    def cancel(self, inv_id: int) -> bool:
        with self._lock:
            if self.current and self.current["id"] == inv_id:
                self.current["stop"].set()
                return True
        return False

    def start(self, address: str, params: dict, title: str = "", demo: bool = False) -> int:
        with self._lock:
            if self.current:
                raise RuntimeError("Уже идёт другое расследование. Дождитесь его завершения или остановите.")
            inv_id = self.storage.create_investigation(address, title, params, is_demo=demo)
            stop = threading.Event()
            self.current = dict(id=inv_id, stop=stop, started=time.time(),
                                progress=dict(nodes=0, transfers=0, api_calls=0, cache_hits=0, current=""))
        threading.Thread(target=self._run, args=(inv_id, address, params, demo, stop), daemon=True).start()
        return inv_id

    def _run(self, inv_id: int, address: str, params: dict, demo: bool, stop: threading.Event) -> None:
        def on_progress(p: dict) -> None:
            if p.get("current"):
                p["current"] = pretty(p["current"])
            with self._lock:
                if self.current and self.current["id"] == inv_id:
                    self.current["progress"].update(p)

        try:
            if demo:
                client = FakeClient(should_stop=stop.is_set, delay=0.05)
            else:
                client = TonAPIClient(api_key=self.storage.get_setting("tonapi_key") or None,
                                      cache_dir=data_dir() / "cache",
                                      cache_ttl=0 if params.get("fresh") else 6 * 3600,
                                      should_stop=stop.is_set)
            result = run_trace(address, params, client, self.storage.labels(), on_progress)
            self.storage.finish_investigation(inv_id, "done", result)
        except Cancelled:
            self.storage.finish_investigation(inv_id, "cancelled", error="Остановлено вами")
        except (TonAPIError, ValueError) as e:
            self.storage.finish_investigation(inv_id, "error", error=str(e))
        except Exception as e:  # непредвиденная ошибка: сохраняем её, чтобы показать человеку
            log.error("Расследование %s упало:\n%s", inv_id, traceback.format_exc())
            self.storage.finish_investigation(inv_id, "error", error=f"Внутренняя ошибка: {e.__class__.__name__}: {e}")
        finally:
            with self._lock:
                self.current = None
