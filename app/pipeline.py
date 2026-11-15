"""Одно расследование от начала до конца: параметры → обход → эвристики → итоговый результат.

Этой функцией пользуются и окно приложения (через фоновую задачу), и командная строка, и тесты.
"""
from __future__ import annotations

import time
from typing import Callable

from .analysis import heuristics
from .analysis.summary import build_result
from .analysis.tracer import TON, TraceConfig, Tracer
from .core.address import norm
from .core.labels import Labeler


def make_config(p: dict) -> TraceConfig:
    """Проверяет параметры из формы и собирает TraceConfig. Ошибки — понятным языком."""
    def num(name, default, lo, hi, cast=float):
        try:
            v = cast(p.get(name, default) if p.get(name) not in (None, "") else default)
        except (TypeError, ValueError):
            raise ValueError(f"Параметр «{name}» должен быть числом") from None
        if not lo <= v <= hi:
            raise ValueError(f"Параметр «{name}» должен быть от {lo} до {hi}")
        return v

    jettons = None
    raw = p.get("jettons") or ""
    items = raw if isinstance(raw, list) else str(raw).replace(";", ",").split(",")
    items = [j.strip() for j in items if j and j.strip()]
    if items:
        jettons = set()
        for j in items:
            # Короткая строка — символ (USDT), длинная — адрес мастер-контракта.
            jettons.add(norm(j) if (":" in j or len(j) >= 40) else j.upper())
    mode = p.get("mode") or "budget"
    if mode not in ("budget", "all"):
        raise ValueError("Неизвестный режим обхода")
    incident = int(num("incident_ts", 0, 0, 4_102_444_800, int))
    end = int(num("end_ts", 0, 0, 4_102_444_800, int)) or None
    if end and incident and end <= incident:
        raise ValueError("«Не позже» должно быть позже времени инцидента")
    return TraceConfig(
        max_depth=int(num("max_depth", 5, 1, 12, int)),
        min_ton=num("min_ton", 1.0, 0, 1e12), min_jetton=num("min_jetton", 1.0, 0, 1e15),
        incident_ts=incident, end_ts=end,
        max_events_per_account=int(num("max_events", 1000, 100, 20000, int)),
        follow_nft=bool(p.get("follow_nft", True)), follow_contracts=bool(p.get("follow_contracts", False)),
        jettons=jettons, max_nodes=int(num("max_nodes", 2000, 10, 20000, int)), mode=mode)


def run_trace(address: str, params: dict, client, manual_labels: dict | None = None,
              on_progress: Callable[[dict], None] | None = None) -> dict:
    cfg = make_config(params)
    source = norm(address)
    started = time.time()
    tracer = Tracer(client, Labeler(manual_labels), cfg, on_progress)
    g = tracer.run(source)
    findings = heuristics.detect(g)

    # Курсы — справочно. Если источник не ответил, просто не показываем доллары.
    rates, rates_error = {}, ""
    tokens = ["ton"] + [k for k in tracer.symbols if k != TON and ":" in k and not k.startswith(("NFT:", "jetton:"))]
    try:
        rates = client.rates(tokens)
    except Exception as e:  # курсы не должны ронять готовое расследование
        rates_error = str(e)
    stats = dict(api_calls=getattr(client, "calls", 0), cache_hits=getattr(client, "cache_hits", 0),
                 retries=getattr(client, "retries", 0), seconds=round(time.time() - started, 1),
                 rates="ok" if rates else ("ошибка" if rates_error else "нет данных"))
    return build_result(g, findings, cfg, rates, stats)
