"""Адреса TON: перевод между raw (0:hex) и «дружелюбным» видом (EQ…/UQ…).

Внутри программы все адреса хранятся в raw-виде в нижнем регистре — так их можно
сравнивать. Человеку показываем так же, как Tonviewer: кошельки — UQ…, контракты — EQ….
"""
from __future__ import annotations

import base64
import binascii


def _crc16(data: bytes) -> bytes:
    """Контрольная сумма CRC16-XMODEM — последние 2 байта дружелюбного адреса."""
    reg = 0
    for byte in data + b"\x00\x00":
        mask = 0x80
        while mask:
            reg <<= 1
            if byte & mask:
                reg += 1
            mask >>= 1
            if reg > 0xFFFF:
                reg &= 0xFFFF
                reg ^= 0x1021
    return reg.to_bytes(2, "big")


def is_raw(addr: str) -> bool:
    return ":" in addr


def raw_to_friendly(raw: str, bounceable: bool = True, testnet: bool = False) -> str:
    wc, h = raw.split(":")
    tag = 0x11 if bounceable else 0x51
    if testnet:
        tag |= 0x80
    body = bytes([tag, int(wc) & 0xFF]) + bytes.fromhex(h)
    return base64.urlsafe_b64encode(body + _crc16(body)).decode()


def friendly_to_raw(addr: str) -> str:
    try:
        b = base64.urlsafe_b64decode(addr.replace("+", "-").replace("/", "_") + "==")
    except (binascii.Error, ValueError):
        raise ValueError(f"Это не похоже на адрес TON: {addr}") from None
    if len(b) != 36:
        raise ValueError(f"Это не похоже на адрес TON: {addr}")
    # Проверяем контрольную сумму: опечатка в адресе не должна запускать расследование
    # чужого кошелька.
    if _crc16(b[:34]) != b[34:]:
        raise ValueError(f"В адресе опечатка (не сходится контрольная сумма): {addr}")
    wc = b[1] if b[1] < 128 else b[1] - 256
    return f"{wc}:{b[2:34].hex()}"


def norm(addr: str) -> str:
    """Каноническая форма для сравнения: raw в нижнем регистре."""
    addr = (addr or "").strip()
    if not addr:
        raise ValueError("Адрес не указан")
    if is_raw(addr):
        wc, _, h = addr.partition(":")
        if len(h) != 64 or not wc.lstrip("-").isdigit():
            raise ValueError(f"Это не похоже на адрес TON: {addr}")
        try:
            bytes.fromhex(h)
        except ValueError:
            raise ValueError(f"Это не похоже на адрес TON: {addr}") from None
        return addr.lower()
    return friendly_to_raw(addr).lower()


def is_real(raw: str) -> bool:
    """Настоящий адрес блокчейна (а не служебная вершина графа вроде «dex:stonfi»)."""
    wc, _, h = raw.partition(":")
    return len(h) == 64 and wc.lstrip("-").isdigit()


def pretty(raw: str, is_wallet: bool = True) -> str:
    if not is_real(raw):
        return raw
    try:
        return raw_to_friendly(raw, bounceable=not is_wallet)
    except Exception:
        return raw


def short(raw: str, is_wallet: bool = True) -> str:
    p = pretty(raw, is_wallet)
    return f"{p[:5]}…{p[-4:]}" if len(p) > 12 else p


def tonviewer(raw: str) -> str:
    return f"https://tonviewer.com/{pretty(raw, True)}" if is_real(raw) else ""


def tonviewer_tx(event_id: str) -> str:
    return f"https://tonviewer.com/transaction/{event_id}"
