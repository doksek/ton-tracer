"""Командная строка — для тех, кому окно не нужно (скрипты, сервер).

  python -m app.cli UQBx... --incident "2026-10-29 14:00" --depth 6 --out out
  python -m app.cli --demo --out demo_out

В папке --out появятся: отчёт.html, сводка.md, обращения-на-биржи.txt и таблицы CSV.
"""
from __future__ import annotations

import argparse
import os
import sys
import zipfile
import io
from datetime import datetime, timezone
from pathlib import Path

from .collectors.tonapi import TonAPIClient, TonAPIError
from .core.paths import data_dir
from .demo import T0, VICTIM, FakeClient
from .pipeline import run_trace
from .report import export_zip, summary_md


def parse_time(s: str | None) -> int:
    """'2026-10-29 14:00' (UTC) или unix-время -> секунды."""
    if not s:
        return 0
    if s.isdigit():
        return int(s)
    for f in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            return int(datetime.strptime(s, f).replace(tzinfo=timezone.utc).timestamp())
        except ValueError:
            pass
    raise SystemExit(f"Не понял время «{s}». Пример: 2026-10-29 14:00 (UTC)")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.cli", description="Трассировка средств в TON без окна")
    ap.add_argument("address", nargs="?", help="скомпрометированный кошелёк (UQ…/EQ…/0:…)")
    ap.add_argument("--demo", action="store_true", help="прогнать выдуманный сценарий без интернета")
    ap.add_argument("--incident", help="время кражи, UTC")
    ap.add_argument("--until", help="не учитывать переводы позже этого времени, UTC")
    ap.add_argument("--depth", type=int, default=5)
    ap.add_argument("--min-ton", type=float, default=1.0)
    ap.add_argument("--min-jetton", type=float, default=1.0)
    ap.add_argument("--jettons", help="только эти жетоны: USDT,NOT или адреса мастер-контрактов")
    ap.add_argument("--all-transfers", action="store_true", help="идти по всем переводам, а не в пределах украденного")
    ap.add_argument("--no-nft", action="store_true")
    ap.add_argument("--follow-contracts", action="store_true")
    ap.add_argument("--max-events", type=int, default=1000)
    ap.add_argument("--fresh", action="store_true", help="не использовать кэш")
    ap.add_argument("--out", default="out")
    a = ap.parse_args(argv)

    if a.demo:
        address, client = VICTIM, FakeClient()
        params = dict(incident_ts=T0, max_depth=6, min_ton=1, min_jetton=1)
    else:
        if not a.address:
            ap.error("укажите адрес кошелька или --demo")
        address = a.address
        # Ключ необязателен; берётся только из переменной окружения, чтобы не попасть в историю команд.
        client = TonAPIClient(api_key=os.getenv("TONAPI_KEY"), cache_dir=data_dir() / "cache",
                              cache_ttl=0 if a.fresh else 6 * 3600)
        params = dict(incident_ts=parse_time(a.incident), end_ts=parse_time(a.until), max_depth=a.depth,
                      min_ton=a.min_ton, min_jetton=a.min_jetton, jettons=a.jettons or "",
                      mode="all" if a.all_transfers else "budget", follow_nft=not a.no_nft,
                      follow_contracts=a.follow_contracts, max_events=a.max_events)

    def progress(p: dict) -> None:
        print(f"\r  адресов: {p['nodes']}, переводов: {p['transfers']}, запросов: {p['api_calls']}   ",
              end="", file=sys.stderr)

    try:
        res = run_trace(address, params, client, on_progress=progress)
    except (ValueError, TonAPIError) as e:
        print(f"\nОшибка: {e}", file=sys.stderr)
        return 1
    print(file=sys.stderr)

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(export_zip(res))) as z:
        z.extractall(out)
    print(summary_md(res))
    print(f"Файлы отчёта: {out.resolve()}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
