"""Демо-сценарий: выдуманная кража, на которой можно посмотреть программу без интернета.

Все адреса здесь — хэши от слов («victim», «thiefA»…), в настоящем блокчейне их нет.
Этот же сценарий используют тесты: FakeClient отвечает так же, как TonAPI.

Сюжет:
  жертва → вор A: 1000 TON, 5000 USDT и NFT
  TON:  A → B → D → E → Binance (memo 123456789), на каждом шаге отщипывается немного
  USDT: A → C → дробление на F1..F3 → сбор на G → OKX (memo) + обмен остатка на STON.fi → пул DeDust
  шум:  кошелёк p1 получил 85 TON украденных, а потом потратил 500 TON своих — дальше идут только 85
"""
from __future__ import annotations

import hashlib

from .core.address import raw_to_friendly

T0 = 1_761_800_000  # момент «кражи» (2025-10-30 04:53:20 UTC)


def A(name: str) -> str:
    return "0:" + hashlib.sha256(name.encode()).hexdigest()


VICTIM = raw_to_friendly(A("victim"), bounceable=False)
USDT = {"address": A("usdt-master"), "symbol": "USDT", "name": "Tether USD", "decimals": 6}

ACCOUNTS: dict[str, dict] = {
    A("binance"): {"address": A("binance"), "is_wallet": True, "name": "Binance Deposit", "memo_required": True},
    A("okx"): {"address": A("okx"), "is_wallet": True, "name": "OKX"},
    A("stonfi-router"): {"address": A("stonfi-router"), "is_wallet": False, "name": "STON.fi Router",
                         "interfaces": ["stonfi_router"], "status": "active"},
    A("dedust-pool"): {"address": A("dedust-pool"), "is_wallet": False, "status": "active",
                       "interfaces": ["dedust_pool"]},
    A("mystery"): {"address": A("mystery"), "is_wallet": False, "status": "active", "interfaces": []},
}

EVENTS: list[dict] = []


def _ev(t: int, *actions: dict) -> None:
    n = len(EVENTS) + 1
    EVENTS.append({"event_id": hashlib.sha256(str(n).encode()).hexdigest(), "timestamp": T0 + t,
                   "lt": 1000 + n, "in_progress": False, "actions": list(actions)})


def _who(key: str) -> dict:
    acc = ACCOUNTS.get(A(key), {})
    return {"address": A(key), "is_wallet": acc.get("is_wallet", True),
            **({"name": acc["name"]} if acc.get("name") else {})}


def ton(s: str, r: str, amount: float, comment: str | None = None) -> dict:
    return {"type": "TonTransfer", "status": "ok",
            "TonTransfer": {"sender": _who(s), "recipient": _who(r), "amount": int(amount * 1e9),
                            **({"comment": comment} if comment else {})}}


def usdt(s: str, r: str, amount: float, comment: str | None = None) -> dict:
    return {"type": "JettonTransfer", "status": "ok",
            "JettonTransfer": {"sender": _who(s), "recipient": _who(r), "amount": str(int(amount * 1e6)),
                               "jetton": USDT, **({"comment": comment} if comment else {})}}


def nft(s: str, r: str, item: str) -> dict:
    return {"type": "NftItemTransfer", "status": "ok",
            "NftItemTransfer": {"sender": _who(s), "recipient": _who(r), "nft": A(item)}}


def _build() -> None:
    if EVENTS:
        return
    _ev(-86400, ton("victim", "friend", 50))                 # до инцидента — не учитывается
    _ev(0, ton("victim", "thiefA", 1000), usdt("victim", "thiefA", 5000))
    _ev(10, ton("victim", "spam", 0.01))                     # пыль — отсеивается
    _ev(60, nft("victim", "thiefA", "nft-1"))
    _ev(300, ton("thiefA", "B", 990))                        # транзит
    _ev(400, usdt("thiefA", "C", 5000))
    _ev(500, nft("thiefA", "H", "nft-1"))
    # цепочка отщипываний B → D → E → Binance
    _ev(1000, ton("B", "D", 900), ton("B", "p1", 85))
    _ev(2000, ton("D", "E", 820), ton("D", "p2", 75))
    _ev(3000, ton("E", "binance", 800, "123456789"), ton("E", "p3", 15))
    # шум: p1 позже тратит 500 TON собственных денег — к краже относятся только 85 из них
    _ev(9000, ton("p1", "shop", 500))
    _ev(9500, ton("shop", "supplier", 480))
    # p2 отправляет всё на неизвестный контракт
    _ev(4000, ton("p2", "mystery", 74))
    # дробление C → F1..F3 → сбор на G → OKX + обмен
    for i, amt in enumerate((1700, 1650, 1650), 1):
        _ev(1100 + i, usdt("C", f"F{i}", amt))
        _ev(5000 + i, usdt(f"F{i}", "G", amt - 1))
    _ev(7000, usdt("G", "okx", 4000, "UID 88990011"))
    _ev(7100, {"type": "JettonSwap", "status": "ok",
               "JettonSwap": {"dex": "stonfi", "amount_in": str(997 * 10 ** 6), "amount_out": "",
                              "ton_out": str(880 * 10 ** 9), "user_wallet": _who("G"),
                              "router": _who("stonfi-router"), "jetton_master_in": USDT}})
    _ev(7200, ton("G", "dedust-pool", 875))


def _involves(e: dict, a: str) -> bool:
    for act in e["actions"]:
        body = act[act["type"]]
        for k in ("sender", "recipient", "user_wallet"):
            if isinstance(body.get(k), dict) and body[k]["address"] == a:
                return True
    return False


class FakeClient:
    """Отвечает как TonAPIClient, но из памяти. Нужен для демо и тестов."""

    def __init__(self, should_stop=None, delay: float = 0.0):
        _build()
        self.calls = 0
        self.cache_hits = 0
        self.retries = 0
        self.should_stop = should_stop or (lambda: False)
        self.delay = delay   # небольшая пауза, чтобы в демо было видно ход работы

    def _tick(self) -> None:
        self.calls += 1
        if self.delay:
            import time
            time.sleep(self.delay)

    def account(self, a: str) -> dict:
        self._tick()
        return ACCOUNTS.get(a, {"address": a, "is_wallet": True, "status": "active"})

    def iter_events(self, a, start_date=None, end_date=None, max_events=1000, page_size=100):
        self._tick()
        res = [e for e in EVENTS if _involves(e, a)
               and (not start_date or e["timestamp"] >= start_date)
               and (not end_date or e["timestamp"] <= end_date)]
        return iter(sorted(res, key=lambda e: -e["lt"])[:max_events])

    def rates(self, tokens: list[str]) -> dict[str, float]:
        self._tick()
        known = {"ton": 1.13, A("usdt-master"): 1.0}
        return {t.lower(): known[t.lower()] for t in tokens if t.lower() in known}
