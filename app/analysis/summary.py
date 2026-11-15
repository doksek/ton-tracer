"""Сводит граф в ответ на главный вопрос: «куда ушли деньги?».

На выходе — обычный словарь (его можно сохранить в JSON и отдать интерфейсу):
узлы, переводы, конечные точки, цепочки, распределение средств, тексты обращений на биржи.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

import networkx as nx

from ..core.address import pretty, short, tonviewer, tonviewer_tx
from ..core.labels import (BRIDGE, CEX, CONTRACT, DEX, EXIT_ROLES, HUB, MIXER, ROLE_RU, SERVICE, SOURCE)
from .heuristics import PATTERN_HELP, PATTERN_RU

# Категории распределения — в том порядке, в котором их важно видеть.
BUCKETS = [
    ("cex", "На биржах", "Депозитные адреса бирж. Биржа знает владельца аккаунта — сюда пишут обращение."),
    ("mixer", "В миксерах", "После миксера след обычно теряется."),
    ("bridge", "Ушло в другие сети", "Мосты и сжигание жетонов: средства выведены в другой блокчейн."),
    ("dex", "В пулах DEX", "Переводы на контракты обменников (кроме обменов, после которых средства вернулись)."),
    ("service", "В сервисах", "Известные сервисы: боты, маркетплейсы, игры."),
    ("hub", "На хабах", "Очень активные адреса — вероятно, горячие кошельки сервисов без подписи."),
    ("holding", "Лежит на кошельках", "Остаток отслеживаемых средств на промежуточных кошельках на момент проверки."),
    ("unexplored", "Не раскрыто", "Адреса, которые не проверялись: предел глубины или неизвестный контракт."),
]


def ts(t: int) -> str:
    return datetime.fromtimestamp(int(t), tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC") if t else ""


def fmt(v: float) -> str:
    """1234567.891 -> '1 234 567.89' (узкий неразрывный пробел не используем — CSV и письма читаются везде)."""
    if abs(v) >= 100:
        s = f"{v:,.2f}"
    elif abs(v) >= 1:
        s = f"{v:,.4f}"
    else:
        s = f"{v:.6f}"
    s = s.rstrip("0").rstrip(".") if "." in s else s
    return s.replace(",", " ")


def _label(g, n) -> str:
    d = g.nodes[n]
    return d.get("entity") or short(n, d.get("is_wallet", True))


def _sym(g, key: str) -> str:
    return "NFT" if key.startswith("NFT:") else g.graph["symbols"].get(key, key)


def _usd(rates: dict, key: str, amount: float) -> float | None:
    price = rates.get("ton" if key == "TON" else key.lower())
    return amount * price if price else None


def _amounts(g, pairs: dict[str, float], rates: dict) -> list[dict]:
    """{ключ актива: сумма} -> список для интерфейса, NFT считаем штуками."""
    out, nft = [], 0
    for key, v in pairs.items():
        if key.startswith("NFT:"):
            nft += int(round(v))
            continue
        out.append(dict(asset=_sym(g, key), key=key, amount=round(v, 6), usd=_usd(rates, key, v)))
    out.sort(key=lambda x: -(x["usd"] or x["amount"]))
    if nft:
        out.append(dict(asset="NFT", key="NFT", amount=nft, usd=None))
    return out


def _bucket_of(nd: dict) -> str | None:
    role = nd["role"]
    if role in (CEX, DEX, BRIDGE, MIXER, SERVICE, HUB):
        return role
    if role != SOURCE and not nd["expanded"]:
        return "unexplored"
    return None


def distribution(g: nx.MultiDiGraph, rates: dict) -> dict:
    src = g.graph["source"]
    buckets: dict[str, dict[str, float]] = {b[0]: defaultdict(float) for b in BUCKETS}
    stolen: dict[str, float] = defaultdict(float)
    for u, v, d in g.edges(data=True):
        if d["kind"] == "swap":
            continue
        if u == src:
            stolen[d["asset_key"]] += d["traced"]
        b = _bucket_of(g.nodes[v])
        if b and v != src:
            buckets[b][d["asset_key"]] += d["traced"]
    for n, nd in g.nodes(data=True):
        if n != src and nd["expanded"]:
            for key, v in nd["holding"].items():
                buckets["holding"][key] += v

    rows, total_usd, all_priced = [], 0.0, True
    for code, title, help_ in BUCKETS:
        amounts = _amounts(g, buckets[code], rates)
        if not amounts:
            continue
        usd = 0.0
        for a in amounts:
            if a["key"] == "NFT":
                continue
            if a["usd"] is None:
                all_priced = False
            else:
                usd += a["usd"]
        total_usd += usd
        rows.append(dict(code=code, title=title, help=help_, amounts=amounts, usd=usd))

    # Доли считаем в долларах, если знаем курс всех активов; если актив один — в нём самом.
    keys = {a["key"] for r in rows for a in r["amounts"] if a["key"] != "NFT"}
    basis = None
    if rows and all_priced and total_usd > 0:
        basis = "usd"
        for r in rows:
            r["share"] = r["usd"] / total_usd
    elif len(keys) == 1:
        basis = "asset"
        total = sum(a["amount"] for r in rows for a in r["amounts"] if a["key"] != "NFT")
        for r in rows:
            r["share"] = sum(a["amount"] for a in r["amounts"] if a["key"] != "NFT") / total if total else 0
    stolen_list = _amounts(g, stolen, rates)
    stolen_usd = sum(a["usd"] for a in stolen_list if a["usd"]) if stolen_list and all(
        a["usd"] is not None for a in stolen_list if a["key"] != "NFT") else None
    return dict(rows=rows, basis=basis, stolen=stolen_list, stolen_usd=stolen_usd,
                mode=g.graph.get("mode", "budget"))


def exits(g: nx.MultiDiGraph) -> list[dict]:
    """Поступления на конечные точки. Memo здесь — главный след к аккаунту на бирже."""
    rows = []
    for u, v, d in g.edges(data=True):
        nd = g.nodes[v]
        if d["kind"] == "swap" or not (nd["role"] in EXIT_ROLES or nd["role"] == HUB):
            continue
        rows.append(dict(ts=d["timestamp"], time_utc=ts(d["timestamp"]), role=nd["role"],
                         role_ru=ROLE_RU.get(nd["role"], nd["role"]), entity=nd.get("entity", ""),
                         node=v, address=pretty(v, nd.get("is_wallet", True)), memo=d.get("comment", ""),
                         memo_required=nd.get("memo_required", False), asset=d["asset"],
                         traced=round(d["traced"], 6), amount=round(d["amount"], 6), from_node=u,
                         from_address=pretty(u, g.nodes[u].get("is_wallet", True)), depth=d["depth"],
                         note=d.get("note", ""), confidence=nd.get("confidence", ""),
                         reason=nd.get("reason", ""), tx_url=tonviewer_tx(d["event_id"])))
    rows.sort(key=lambda r: r["ts"])
    return rows


def chains(g: nx.MultiDiGraph, min_ton: float = 0.0, min_jetton: float = 0.0) -> list[dict]:
    """Для каждой конечной точки — основной путь от исходного кошелька.

    Идём от конца назад, каждый раз выбирая самое крупное входящее поступление.
    Так получается одна понятная цепочка на точку, а не тысячи вариантов обхода.
    """
    src = g.graph["source"]
    rows = []
    for t, nd in g.nodes(data=True):
        if t == src or g.in_degree(t) == 0:
            continue
        is_exit = nd["role"] in EXIT_ROLES
        # «Средства лежат» — только если остаток заметный (не меньше порога пыли).
        held = {k: v for k, v in nd["holding"].items()
                if k.startswith("NFT:") or v >= (min_ton if k == "TON" else min_jetton)}
        is_end = is_exit or nd["role"] == HUB or not nd["expanded"] or bool(held)
        if not is_end or all(d["kind"] == "swap" for _, _, d in g.in_edges(t, data=True)):
            continue
        path, cur, times = [t], t, []
        while cur != src and len(path) < 64:
            best = None
            for u, _, d in g.in_edges(cur, data=True):
                if d["kind"] == "swap" or u in path:
                    continue
                # Сравниваем по времени и сумме: берём самое крупное поступление.
                if best is None or d["traced"] > best[1]["traced"]:
                    best = (u, d)
            if best is None:
                break
            times.append(best[1]["timestamp"])
            cur = best[0]
            path.append(cur)
        if cur != src:
            continue
        path.reverse()
        into = defaultdict(float)
        memos = set()
        for _, _, d in g.in_edges(t, data=True):
            if d["kind"] != "swap":
                into[d["asset_key"]] += d["traced"]
                if d.get("comment"):
                    memos.add(d["comment"])
        patterns = sorted({tag for n in path for tag in g.nodes[n].get("tags", []) if tag in PATTERN_RU})
        if is_exit:
            status = "конечная точка"
        elif nd["role"] == HUB:
            status = "хаб"
        elif not nd["expanded"]:
            status = "не раскрыто"
        else:
            status = "средства лежат"
        rows.append(dict(
            end=t, role=nd["role"], role_ru=ROLE_RU.get(nd["role"], nd["role"]), entity=nd.get("entity", ""),
            end_address=pretty(t, nd.get("is_wallet", True)), hops=len(path) - 1,
            amounts=[dict(asset=_sym(g, k), amount=round(v, 6))
                     for k, v in (held if status == "средства лежат" else into).items()],
            memos=sorted(memos), patterns=patterns, first_tx=ts(min(times)) if times else "",
            last_tx=ts(max(times)) if times else "", status=status, stop_reason=nd.get("stop_reason", ""),
            path=path, path_labels=[_label(g, n) for n in path]))
    order = {"конечная точка": 0, "хаб": 1, "средства лежат": 2, "не раскрыто": 3}
    rows.sort(key=lambda r: (order[r["status"]], r["hops"]))
    for i, r in enumerate(rows, 1):
        r["id"] = i
    return rows


def letters(g: nx.MultiDiGraph, exit_rows: list[dict], incident_ts: int) -> list[dict]:
    """Готовые тексты обращений на биржи — по одному на каждую биржу.

    Биржа не вернёт деньги по письму, но может заморозить депозит и сохранить данные
    до официального запроса полиции. Поэтому в письме — только проверяемые факты.
    """
    src = g.graph["source"]
    by_node: dict[str, list[dict]] = defaultdict(list)
    for r in exit_rows:
        if r["role"] == CEX:
            by_node[r["node"]].append(r)
    out = []
    for node, rows in by_node.items():
        name = rows[0]["entity"] or "биржа (название не определено)"
        dep_ru = "\n".join(
            f"  {i}. {r['time_utc']} — {fmt(r['traced'])} {r['asset']}"
            + (f", memo/tag: {r['memo']}" if r["memo"] else ", без memo")
            + f"\n     {r['tx_url']}" for i, r in enumerate(rows, 1))
        dep_en = "\n".join(
            f"  {i}. {r['time_utc']} — {fmt(r['traced'])} {r['asset']}"
            + (f", memo/tag: {r['memo']}" if r["memo"] else ", no memo")
            + f"\n     {r['tx_url']}" for i, r in enumerate(rows, 1))
        inc = ts(incident_ts) or "[дата и время]"
        ru = f"""Тема: Украденные средства поступили на ваш депозитный адрес в сети TON — просьба заморозить

Здравствуйте.

{inc} с моего кошелька в сети TON были похищены средства.
Мой кошелёк: {pretty(src, True)}

Отслеживание переводов в блокчейне показывает, что часть этих средств поступила на адрес,
который относится к вашей площадке:
Адрес: {rows[0]['address']}

Поступления:
{dep_ru}

Прошу:
1. Проверить аккаунт, на который зачислены эти депозиты, и по возможности приостановить вывод средств.
2. Сохранить данные об аккаунте и операциях для передачи правоохранительным органам по их запросу.
3. Сообщить, какие документы нужны с моей стороны.

Заявление в полицию: [номер и дата — приложите копию].
Готов(а) подтвердить владение кошельком подписью или тестовой транзакцией.

[Имя, контакт для связи]"""
        en = f"""Subject: Stolen funds deposited to your TON address — freeze request

Hello,

On {ts(incident_ts) or '[date and time]'} funds were stolen from my TON wallet.
My wallet: {pretty(src, True)}

On-chain tracing shows that part of these funds was deposited to an address that belongs to your platform:
Address: {rows[0]['address']}

Deposits:
{dep_en}

I kindly ask you to:
1. Review the account credited with these deposits and, if possible, suspend withdrawals.
2. Preserve the account and transaction data for law enforcement upon their request.
3. Let me know which documents you need from me.

Police report: [number and date — attach a copy].
I can prove ownership of the wallet with a signature or a test transaction.

[Name, contact details]"""
        out.append(dict(node=node, entity=name, address=rows[0]["address"], deposits=len(rows),
                        confidence=rows[0]["confidence"], reason=rows[0]["reason"], ru=ru, en=en))
    return out


def headline(dist: dict, exit_rows: list[dict], n_wallets: int) -> str:
    """Ответ на вопрос расследования одной-двумя фразами."""
    rows = {r["code"]: r for r in dist["rows"]}
    if not dist["stolen"]:
        return ("После указанного времени исходящих переводов крупнее порога не найдено. "
                "Проверьте адрес, время инцидента и пороги сумм.")
    parts = []
    for code, text in (("cex", "дошло до бирж"), ("mixer", "ушло в миксеры"), ("bridge", "выведено в другие сети"),
                       ("dex", "ушло в пулы DEX"), ("service", "ушло в сервисы"), ("hub", "осело на хабах"),
                       ("holding", "лежит на промежуточных кошельках"), ("unexplored", "не раскрыто")):
        r = rows.get(code)
        if not r:
            continue
        if dist["basis"]:
            parts.append(f"{r['share']:.0%} {text}")
        else:
            parts.append(text + ": " + ", ".join(f"{fmt(a['amount'])} {a['asset']}" for a in r["amounts"]))
    memos = sum(1 for r in exit_rows if r["role"] == CEX and r["memo"])
    s = "Отслеживаемые средства: " + "; ".join(parts) + "." if parts else "Средства дальше первого перевода не прослежены."
    if memos:
        s += f" Найдено депозитов на биржи с memo: {memos} — это прямой след к аккаунту."
    return s


def build_result(g: nx.MultiDiGraph, findings: list[dict], cfg, rates: dict | None = None,
                 stats: dict | None = None) -> dict:
    rates = rates or {}
    src = g.graph["source"]
    received: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    sent: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    edges = []
    for i, (u, v, d) in enumerate(sorted(g.edges(data=True), key=lambda e: (e[2]["timestamp"], e[2]["lt"]))):
        if d["kind"] != "swap":
            received[v][d["asset_key"]] += d["traced"]
            sent[u][d["asset_key"]] += d["traced"]
        edges.append(dict(id=i, **{"from": u, "to": v}, kind=d["kind"], asset=d["asset"], key=d["asset_key"],
                          amount=round(d["amount"], 6), traced=round(d["traced"], 6), ts=d["timestamp"],
                          time_utc=ts(d["timestamp"]), memo=d.get("comment", ""), note=d.get("note", ""),
                          depth=d["depth"], usd=_usd(rates, d["asset_key"], d["traced"]) if d["kind"] != "swap" else None,
                          tx_url=tonviewer_tx(d["event_id"])))
    nodes = []
    for n, nd in g.nodes(data=True):
        w = nd.get("is_wallet", True)
        nodes.append(dict(
            id=n, address=pretty(n, w), short=short(n, w), label=_label(g, n), role=nd["role"],
            role_ru=ROLE_RU.get(nd["role"], nd["role"]), entity=nd.get("entity", ""), reason=nd.get("reason", ""),
            confidence=nd.get("confidence", ""), depth=nd["depth"], is_wallet=w, is_scam=nd.get("is_scam", False),
            memo_required=nd.get("memo_required", False), expanded=nd["expanded"],
            stop_reason=nd.get("stop_reason", ""), events_scanned=nd.get("events_scanned", 0),
            tags=sorted(t for t in nd.get("tags", []) if t in PATTERN_RU),
            received=_amounts(g, received[n], rates), sent=_amounts(g, sent[n], rates),
            holding=_amounts(g, nd.get("holding", {}), rates) if n != src else [],
            untraced_out=nd.get("untraced_out", 0), url=tonviewer(n)))
    nodes.sort(key=lambda x: (x["depth"], x["id"]))
    exit_rows = exits(g)
    dist = distribution(g, rates)
    pats = [dict(pattern=f["pattern"], title=PATTERN_RU[f["pattern"]], help=PATTERN_HELP[f["pattern"]],
                 node=f["node"], label=_label(g, f["node"]), asset=f["asset"], amount=f["amount"],
                 detail=f["detail"], chain=f.get("chain", []),
                 chain_labels=[_label(g, n) for n in f.get("chain", [])]) for f in findings]
    return dict(
        source=src, source_address=pretty(src, True), incident_ts=cfg.incident_ts, end_ts=cfg.end_ts,
        params=dict(max_depth=cfg.max_depth, min_ton=cfg.min_ton, min_jetton=cfg.min_jetton, mode=cfg.mode,
                    follow_nft=cfg.follow_nft, follow_contracts=cfg.follow_contracts,
                    max_events=cfg.max_events_per_account, jettons=sorted(cfg.jettons) if cfg.jettons else []),
        headline=headline(dist, exit_rows, len(nodes)), distribution=dist, nodes=nodes, edges=edges,
        exits=exit_rows, chains=chains(g, cfg.min_ton, cfg.min_jetton), patterns=pats, letters=letters(g, exit_rows, cfg.incident_ts),
        warnings=list(g.graph.get("warnings", [])), rates_known=bool(rates), stats=stats or {},
        generated_at=int(datetime.now(tz=timezone.utc).timestamp()))
