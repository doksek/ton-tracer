"""Клиент TonAPI v2 (https://tonapi.io/v2) — единственный источник данных программы.

TonAPI — публичный индексатор блокчейна TON (на нём работает Tonviewer). Ключ не нужен:
без ключа сервис пускает примерно 1 запрос в секунду, поэтому клиент «вежливый» —
сам выдерживает паузы, повторяет запрос при ошибке и кэширует ответы на диске.
С бесплатным ключом из https://tonconsole.com всё идёт быстрее, но это необязательно.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Callable, Iterator

import requests

from .. import __version__

BASE_URL = "https://tonapi.io/v2"
USER_AGENT = f"ton-tracer/{__version__} (open-source TON fund tracing tool)"


class TonAPIError(RuntimeError):
    """Источник ответил ошибкой или недоступен."""


class Cancelled(Exception):
    """Пользователь остановил расследование."""


class TonAPIClient:
    def __init__(self, api_key: str | None = None, rps: float | None = None,
                 cache_dir: str | Path | None = None, cache_ttl: int = 6 * 3600,
                 timeout: int = 30, should_stop: Callable[[], bool] | None = None):
        self.api_key = api_key or None
        self.min_interval = 1.0 / (rps or (8 if self.api_key else 1))
        self.timeout = timeout
        self.cache_ttl = cache_ttl
        self.should_stop = should_stop or (lambda: False)
        self.session = requests.Session()
        self.session.headers["Accept"] = "application/json"
        self.session.headers["User-Agent"] = USER_AGENT
        if self.api_key:
            self.session.headers["Authorization"] = f"Bearer {self.api_key}"
        self.cache_dir = Path(cache_dir) if cache_dir else None
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._last_call = 0.0
        self.calls = 0          # сколько раз реально ходили в сеть
        self.cache_hits = 0
        self.retries = 0
        self.last_error = ""

    # ---------- низкий уровень ----------
    def _cache_path(self, path: str, params: dict | None) -> Path | None:
        if not self.cache_dir:
            return None
        key = path + "?" + json.dumps(params or {}, sort_keys=True)
        return self.cache_dir / (hashlib.sha1(key.encode()).hexdigest() + ".json")

    def _sleep(self, seconds: float) -> None:
        """Пауза, которую можно прервать кнопкой «Остановить»."""
        end = time.monotonic() + seconds
        while True:
            if self.should_stop():
                raise Cancelled()
            left = end - time.monotonic()
            if left <= 0:
                return
            time.sleep(min(0.2, left))

    def get(self, path: str, params: dict | None = None, use_cache: bool = True) -> Any:
        cp = self._cache_path(path, params) if use_cache else None
        if cp and cp.exists() and time.time() - cp.stat().st_mtime < self.cache_ttl:
            try:
                data = json.loads(cp.read_text(encoding="utf-8"))
                self.cache_hits += 1
                return data
            except (OSError, ValueError):
                pass  # битый файл кэша — просто запросим заново

        last = "нет ответа"
        for attempt in range(6):
            self._sleep(max(0.0, self.min_interval - (time.monotonic() - self._last_call)))
            self._last_call = time.monotonic()
            self.calls += 1
            try:
                r = self.session.get(BASE_URL + path, params=params, timeout=self.timeout)
            except requests.RequestException as e:
                last = f"сеть: {e.__class__.__name__}"
                self.retries += 1
                self._sleep(min(2 ** attempt, 20))
                continue
            if r.status_code == 429 or r.status_code >= 500:
                last = f"HTTP {r.status_code}"
                self.retries += 1
                # Если сервер сам сказал, сколько ждать, — слушаемся.
                retry_after = r.headers.get("Retry-After", "")
                wait = float(retry_after) if retry_after.isdigit() else min(2 ** attempt, 30)
                self._sleep(wait)
                continue
            if r.status_code == 404:
                return None
            if r.status_code in (401, 403):
                raise TonAPIError("TonAPI отклонил ключ API. Проверьте ключ в настройках или удалите его.")
            if r.status_code != 200:
                raise TonAPIError(f"TonAPI: HTTP {r.status_code} на {path}: {r.text[:200]}")
            try:
                data = r.json()
            except ValueError:
                last = "ответ не в формате JSON"
                continue
            if cp:
                try:
                    cp.write_text(json.dumps(data), encoding="utf-8")
                except OSError:
                    pass
            return data
        self.last_error = last
        raise TonAPIError(f"TonAPI не отвечает ({last}). Проверьте интернет и попробуйте позже.")

    # ---------- эндпоинты ----------
    def account(self, account_id: str) -> dict | None:
        """GET /accounts/{id}: name, is_scam, memo_required, interfaces, is_wallet, status."""
        return self.get(f"/accounts/{account_id}")

    def iter_events(self, account_id: str, start_date: int | None = None,
                    end_date: int | None = None, max_events: int = 1000,
                    page_size: int = 100) -> Iterator[dict]:
        """GET /accounts/{id}/events — события кошелька, от новых к старым.

        Событие — это одна операция глазами человека («перевод», «обмен», «покупка NFT»),
        уже разобранная TonAPI из цепочки транзакций. Страницы листаются через before_lt.
        subject_only=true убирает события, где кошелёк не участник (спам-уведомления).
        """
        before_lt = None
        got = 0
        while got < max_events:
            params: dict[str, Any] = {"limit": min(page_size, max_events - got), "subject_only": "true"}
            if start_date:
                params["start_date"] = int(start_date)
            if end_date:
                params["end_date"] = int(end_date)
            if before_lt:
                params["before_lt"] = before_lt
            data = self.get(f"/accounts/{account_id}/events", params)
            if not data:
                return
            events = data.get("events") or []
            for ev in events:
                if ev.get("in_progress"):
                    continue
                yield ev
            got += len(events)
            nxt = data.get("next_from") or 0
            if not events or not nxt:
                return
            before_lt = nxt

    def rates(self, tokens: list[str]) -> dict[str, float]:
        """GET /rates — текущие курсы к доллару. tokens: 'ton' и адреса мастер-контрактов жетонов.

        Возвращает {токен в нижнем регистре: цена в USD}. Курсы нужны только для наглядности
        («примерно $X»), поэтому любая ошибка здесь не должна ронять расследование.
        """
        out: dict[str, float] = {}
        for i in range(0, len(tokens), 50):
            chunk = tokens[i:i + 50]
            try:
                data = self.get("/rates", {"tokens": ",".join(chunk), "currencies": "usd"})
            except TonAPIError:
                continue
            for tok, info in ((data or {}).get("rates") or {}).items():
                price = ((info or {}).get("prices") or {}).get("USD")
                if isinstance(price, (int, float)) and price > 0:
                    out[tok.lower()] = float(price)
        return out
