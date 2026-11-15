"""Разметка адресов: биржи (CEX), DEX, мосты, миксеры, сервисы.

Источники по приоритету:
  1. Ручные метки пользователя (вкладка «Метки» в приложении).
  2. Метаданные TonAPI: name, is_scam, memo_required, interfaces.
  3. Ключевые слова в имени.

Важно: метка — это вывод, а не факт. Поэтому у каждой метки есть `reason` (на чём она
основана) и `confidence` (насколько ей можно верить).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# Роли узлов
SOURCE = "source"
WALLET = "wallet"          # промежуточный кошелёк
CEX = "cex"
DEX = "dex"
BRIDGE = "bridge"
MIXER = "mixer"
SERVICE = "service"        # известный, но не классифицированный сервис (бот, игра, P2P…)
CONTRACT = "contract"      # неизвестный смарт-контракт (не кошелёк)
HUB = "hub"                # слишком активный адрес: вероятно, горячий кошелёк сервиса
SCAM = "scam"

EXIT_ROLES = {CEX, DEX, BRIDGE, MIXER, SERVICE}
ALL_ROLES = [SOURCE, WALLET, CEX, DEX, BRIDGE, MIXER, SERVICE, CONTRACT, HUB, SCAM]
MANUAL_ROLES = [CEX, DEX, BRIDGE, MIXER, SERVICE, WALLET]

ROLE_RU = {
    SOURCE: "Исходный кошелёк", WALLET: "Кошелёк", CEX: "Биржа", DEX: "DEX",
    BRIDGE: "Мост / сжигание", MIXER: "Миксер", SERVICE: "Сервис", CONTRACT: "Неизвестный контракт",
    HUB: "Хаб", SCAM: "Помечен как scam",
}

# Уровни уверенности — как в OSINT-отчётах.
CONFIRMED, PROBABLE, UNVERIFIED = "подтверждено", "вероятно", "не проверено"

KEYWORDS = [
    (MIXER, r"mixer|tornado|tumbler|tonmix|anon.?pool|privacy pool"),
    (BRIDGE, r"bridge|orbit|symbiosis|layerzero|stargate|wormhole|teleport|xp\.network|"
             r"rubic|changenow|fixedfloat|simpleswap|exolix|swapuz|letsexchange|stealthex"),
    (DEX, r"ston\.?fi|dedust|megaton|tonco|swap\.coffee|tonstakers|bemo|evaa|storm|"
          r"\bdex\b|liquidity pool|router|vault"),
    (CEX, r"binance|bybit|okx|okex|bitget|kucoin|huobi|\bhtx\b|gate\.?io|mexc|bingx|"
          r"crypto\.com|kraken|coinbase|bitfinex|bitstamp|whitebit|exmo|garantex|"
          r"bitpapa|lbank|bitmart|poloniex|coinex|xt\.com|ascendex|bitvavo|upbit|"
          r"wallet ?bot|telegram wallet|@wallet|cryptobot|crypto bot|xrocket|"
          r"tonhub ?pay|cex|exchange|deposit|hot ?wallet"),
    (SERVICE, r"fragment|getgems|tonkeeper|tonnel|game|casino|bot\b|nft marketplace|p2p"),
]

DEX_INTERFACES = ("stonfi", "dedust", "megaton", "tonco", "pool", "vault", "router")


@dataclass
class Label:
    role: str
    entity: str = ""
    reason: str = ""
    confidence: str = UNVERIFIED
    is_wallet: bool = True
    is_scam: bool = False
    memo_required: bool = False
    extra: dict = field(default_factory=dict)

    @property
    def is_exit(self) -> bool:
        return self.role in EXIT_ROLES


class Labeler:
    def __init__(self, manual: dict[str, dict] | None = None):
        # manual: {raw-адрес: {"role": "cex", "entity": "Bybit", "note": "..."}}
        self.manual = {k.lower(): v for k, v in (manual or {}).items()}

    def classify(self, raw: str, acc: dict | None) -> Label:
        acc = acc or {}
        name = (acc.get("name") or "").strip()
        is_wallet = acc.get("is_wallet")
        is_wallet = True if is_wallet is None else bool(is_wallet)
        interfaces = [str(i).lower() for i in (acc.get("interfaces") or [])]
        lab = Label(role=WALLET, entity=name, is_wallet=is_wallet,
                    is_scam=bool(acc.get("is_scam")),
                    memo_required=bool(acc.get("memo_required")),
                    extra={"interfaces": interfaces, "status": acc.get("status")})

        m = self.manual.get(raw.lower())
        if m:
            lab.role = m.get("role") or CEX
            lab.entity = m.get("entity") or name
            lab.reason = "ваша метка" + (f": {m['note']}" if m.get("note") else "")
            lab.confidence = CONFIRMED
            return lab

        if lab.memo_required:
            lab.role = CEX
            lab.reason = "TonAPI: адрес требует memo — так устроены депозитные адреса бирж"
            lab.confidence = PROBABLE
            return lab

        low = name.lower()
        if name and re.search(r"\.(ton|t\.me)$", low) and is_wallet:
            # DNS-имя (alice.ton / alice.t.me) — личный кошелёк, идём дальше.
            lab.reason = f"DNS-имя {name}"
            return lab

        if name:
            for role, pat in KEYWORDS:
                if re.search(pat, low):
                    lab.role = role
                    lab.reason = f"имя «{name}» в TonAPI"
                    lab.confidence = PROBABLE
                    return lab

        if any(k in i for i in interfaces for k in DEX_INTERFACES):
            lab.role = DEX
            lab.reason = f"интерфейс контракта: {', '.join(interfaces)}"
            lab.confidence = PROBABLE
            return lab

        if name:
            lab.role = SERVICE
            lab.reason = f"TonAPI знает имя «{name}», тип сервиса не определён"
            lab.confidence = PROBABLE
            return lab

        if not is_wallet and acc.get("status") == "active":
            lab.role, lab.reason = CONTRACT, "смарт-контракт без имени"
            return lab

        if lab.is_scam:
            lab.role, lab.reason, lab.confidence = SCAM, "TonAPI: is_scam", PROBABLE
        return lab
