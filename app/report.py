"""Экспорт расследования: таблицы CSV, сводка, тексты обращений и самостоятельный HTML-отчёт.

HTML-отчёт — один файл без внешних ссылок: его можно отправить юристу или приложить к заявлению,
он откроется в любом браузере без интернета и без установленной программы.
"""
from __future__ import annotations

import csv
import io
import json
import zipfile
from datetime import datetime, timezone

from . import __version__
from .analysis.summary import fmt, ts
from .core.paths import resource_dir


def _safe(v):
    """Защита от «формул» в CSV: комментарий к переводу пишет посторонний человек,
    и Excel выполнил бы строку, начинающуюся с = + - @, как формулу."""
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + v
    return v


def _csv(header: list[str], rows: list[list]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    for r in rows:
        w.writerow([_safe(x) for x in r])
    return ("﻿" + buf.getvalue()).encode("utf-8")   # BOM — чтобы Excel правильно открыл кириллицу


def _amounts(items: list[dict]) -> str:
    return "; ".join(f"{fmt(a['amount'])} {a['asset']}" for a in items)


def transfers_csv(res: dict) -> bytes:
    nodes = {n["id"]: n for n in res["nodes"]}
    return _csv(
        ["шаг", "время_utc", "от", "роль_отправителя", "кому", "роль_получателя", "название_получателя", "тип",
         "актив", "сумма_перевода", "из_них_отслеживается", "memo", "примечание", "транзакция"],
        [[e["depth"], e["time_utc"], nodes[e["from"]]["address"], nodes[e["from"]]["role_ru"],
          nodes[e["to"]]["address"], nodes[e["to"]]["role_ru"], nodes[e["to"]]["entity"], e["kind"], e["asset"],
          e["amount"], e["traced"], e["memo"], e["note"], e["tx_url"]] for e in res["edges"]])


def exits_csv(res: dict) -> bytes:
    return _csv(
        ["время_utc", "тип", "название", "адрес", "memo", "memo_обязателен", "актив", "отслеживаемая_сумма",
         "сумма_перевода", "от_адреса", "шаг", "уверенность", "основание_метки", "транзакция"],
        [[r["time_utc"], r["role_ru"], r["entity"], r["address"], r["memo"], "да" if r["memo_required"] else "",
          r["asset"], r["traced"], r["amount"], r["from_address"], r["depth"], r["confidence"], r["reason"],
          r["tx_url"]] for r in res["exits"]])


def chains_csv(res: dict) -> bytes:
    nodes = {n["id"]: n for n in res["nodes"]}
    return _csv(
        ["номер", "чем_закончилась", "тип", "название", "конечный_адрес", "шагов", "дошло", "memo", "паттерны",
         "первый_перевод", "последний_перевод", "почему_остановились", "путь"],
        [[c["id"], c["status"], c["role_ru"], c["entity"], c["end_address"], c["hops"], _amounts(c["amounts"]),
          "; ".join(c["memos"]), ", ".join(c["patterns"]), c["first_tx"], c["last_tx"], c["stop_reason"],
          " → ".join(nodes[n]["address"] for n in c["path"])] for c in res["chains"]])


def patterns_csv(res: dict) -> bytes:
    nodes = {n["id"]: n for n in res["nodes"]}
    return _csv(["паттерн", "адрес", "роль", "актив", "сумма", "описание", "цепочка"],
                [[p["title"], nodes[p["node"]]["address"], nodes[p["node"]]["role_ru"], p["asset"], p["amount"],
                  p["detail"], " → ".join(nodes[n]["address"] for n in p["chain"])] for p in res["patterns"]])


def summary_md(res: dict, title: str = "") -> str:
    d = res["distribution"]
    L = [f"# {title or 'Отчёт по трассировке TON'}", "",
         "## Вывод", res["headline"], "",
         "## Параметры",
         f"- Исходный кошелёк: `{res['source_address']}`",
         f"- Время инцидента: {ts(res['incident_ts']) or 'не задано'}",
         f"- Глубина: {res['params']['max_depth']}, пороги: {res['params']['min_ton']} TON / "
         f"{res['params']['min_jetton']} в единицах жетона",
         f"- Режим: {'в пределах украденной суммы' if res['params']['mode'] == 'budget' else 'все исходящие переводы'}",
         f"- Адресов в графе: {len(res['nodes'])}, переводов: {len(res['edges'])}",
         f"- Отчёт составлен: {ts(res['generated_at'])}", "",
         "## Что выведено с исходного кошелька",
         "- " + (_amounts(d["stolen"]) or "ничего"), "",
         "## Куда ушли средства"]
    for r in d["rows"]:
        share = f" — {r['share']:.0%}" if d["basis"] else ""
        L.append(f"- **{r['title']}**{share}: {_amounts(r['amounts'])}")
    if not d["rows"]:
        L.append("Нет данных.")
    L += ["", "## Конечные точки"]
    for r in res["exits"]:
        L.append(f"- {r['time_utc']} · {r['role_ru']} {r['entity'] or r['address']} · {fmt(r['traced'])} {r['asset']}"
                 + (f" · memo `{r['memo']}`" if r["memo"] else "") + f" · метка: {r['confidence']} ({r['reason']})"
                 + f" · {r['tx_url']}")
    if not res["exits"]:
        L.append("Не найдены.")
    L += ["", "## Паттерны"]
    for p in res["patterns"]:
        L.append(f"- {p['title']}: {p['label']} — {p['detail']}")
    if not res["patterns"]:
        L.append("Не найдены.")
    L += ["", "## Что не удалось выяснить"]
    gaps = [f"- {n['address']} ({n['role_ru']}): {n['stop_reason']}" for n in res["nodes"]
            if not n["expanded"] and n["stop_reason"] and n["role"] in ("wallet", "contract", "hub", "scam")]
    L += gaps or ["Все кошельки в пределах заданной глубины раскрыты."]
    L += [f"- {w}" for w in res["warnings"]]
    L += ["", "## Как читать этот отчёт",
          "- Это граф переводов, а не доказательство владения: промежуточный кошелёк может принадлежать "
          "постороннему человеку (обменник, продавец, P2P-сделка).",
          "- «Отслеживается» — оценка: считается, что украденные средства уходят с кошелька первыми.",
          "- Метки бирж и сервисов взяты из TonAPI и могут быть неполными. Уровень уверенности указан у каждой.",
          "- Источник данных: TonAPI (tonapi.io). Каждую транзакцию можно проверить по ссылке на Tonviewer.", ""]
    return "\n".join(L)


def letters_txt(res: dict) -> str:
    if not res["letters"]:
        return "Депозитов на биржи не найдено — обращаться пока некуда.\n"
    parts = []
    for l in res["letters"]:
        parts.append(f"{'=' * 70}\n{l['entity']} — {l['address']}\nМетка: {l['confidence']} ({l['reason']})\n"
                     f"{'=' * 70}\n\n{l['ru']}\n\n{'-' * 70}\n\n{l['en']}\n")
    return "\n\n".join(parts)


def standalone_html(res: dict, title: str = "") -> str:
    """Собирает один HTML-файл: стили, скрипты и данные внутри."""
    static = resource_dir() / "web" / "static"
    css = (static / "app.css").read_text(encoding="utf-8")
    js = "\n".join((static / f).read_text(encoding="utf-8") for f in ("flowmap.js", "view.js"))
    # «</» внутри JSON сломал бы тег <script>, а комментарии к переводам пишут посторонние.
    data = json.dumps(dict(title=title, result=res, version=__version__), ensure_ascii=False).replace("</", "<\\/")
    made = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    safe_title = (title or "Отчёт TON Tracer").replace("&", "&amp;").replace("<", "&lt;")
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{safe_title}</title>
<style>{css}</style></head>
<body class="standalone">
<main class="main"><div id="view"></div>
<p class="report-foot">Отчёт составлен {made} программой TON Tracer {__version__}. Данные: TonAPI.
Это граф переводов, а не доказательство владения кошельками.</p></main>
<div id="drawer" class="drawer" hidden></div>
<script>window.STANDALONE = {data};</script>
<script>{js}</script>
<script>TT.renderResult(document.getElementById('view'), window.STANDALONE.result,
  {{title: window.STANDALONE.title, standalone: true}});</script>
</body></html>"""


def export_zip(res: dict, title: str = "") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("отчёт.html", standalone_html(res, title))
        z.writestr("сводка.md", summary_md(res, title))
        z.writestr("обращения-на-биржи.txt", letters_txt(res))
        z.writestr("конечные-точки.csv", exits_csv(res))
        z.writestr("цепочки.csv", chains_csv(res))
        z.writestr("переводы.csv", transfers_csv(res))
        z.writestr("паттерны.csv", patterns_csv(res))
        z.writestr("данные.json", json.dumps(res, ensure_ascii=False, indent=1))
    return buf.getvalue()
