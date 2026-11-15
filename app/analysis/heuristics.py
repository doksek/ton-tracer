"""Эвристики отмывания: peel chain, дробление, консолидация, транзит.

Каждая находка — это наблюдение («кошелёк переслал 99% за 5 минут»), а не обвинение.
Считаем только отслеживаемую часть переводов (поле traced).
"""
from __future__ import annotations

from collections import defaultdict

import networkx as nx

from ..core.labels import EXIT_ROLES, SOURCE

VALUE_KINDS = ("ton", "jetton", "nft_purchase", "burn")

PATTERN_RU = {
    "peel_chain": "Цепочка отщипываний",
    "peel_step": "Отщипывание",
    "split": "Дробление",
    "consolidation": "Консолидация",
    "transit": "Транзит",
}
PATTERN_HELP = {
    "peel_chain": "Основная сумма идёт по цепочке кошельков, на каждом шаге отделяется небольшая часть. "
                  "Типичный способ запутать след.",
    "peel_step": "Один крупный перевод идёт дальше, мелкие суммы отводятся в стороны.",
    "split": "Сумма разбита на несколько сопоставимых частей.",
    "consolidation": "На один кошелёк сходятся средства с нескольких отслеживаемых адресов.",
    "transit": "Кошелёк-прокладка: почти всё пришедшее ушло дальше в течение часа.",
}


def _flows(g: nx.MultiDiGraph, node: str, direction: str) -> dict[str, list[dict]]:
    """Рёбра узла по активам: {ключ актива: [ребро, ...]}."""
    edges = g.in_edges(node, data=True) if direction == "in" else g.out_edges(node, data=True)
    res: dict[str, list[dict]] = defaultdict(list)
    for _, _, d in edges:
        if d["kind"] in VALUE_KINDS and d["traced"] > 0:
            res[d["asset_key"]].append(d)
    return res


def detect(g: nx.MultiDiGraph, peel_ratio: float = 0.75, consolidation_min: int = 3,
           split_min: int = 3, transit_seconds: int = 3600) -> list[dict]:
    """Помечает узлы тегами (g.nodes[n]['tags']) и возвращает список найденных паттернов."""
    findings: list[dict] = []
    peel_next: dict[str, str] = {}

    for n, nd in g.nodes(data=True):
        if nd["role"] in EXIT_ROLES:
            continue
        ins, outs = _flows(g, n, "in"), _flows(g, n, "out")

        for key, out_edges in outs.items():
            asset = out_edges[0]["asset"]
            total_out = sum(e["traced"] for e in out_edges)
            in_edges = ins.get(key, [])
            total_in = sum(e["traced"] for e in in_edges)

            # Отщипывание: один крупный «хвост» идёт дальше, мелкие куски отводятся.
            if len(out_edges) >= 2 and total_out > 0:
                big = max(out_edges, key=lambda e: e["traced"])
                if big["traced"] / total_out >= peel_ratio:
                    nd["tags"].add("peel_step")
                    peel_next[n] = big["recipient"]
                    findings.append(dict(pattern="peel_step", node=n, asset=asset, amount=round(total_out, 4),
                                         detail=f"{len(out_edges) - 1} мелких отводов; основная часть "
                                                f"{big['traced']:g} {asset} ({big['traced'] / total_out:.0%}) ушла дальше"))

            # Дробление: сумма разбита на несколько сопоставимых частей.
            if len(out_edges) >= split_min:
                amts = sorted(e["traced"] for e in out_edges)
                if amts[-1] > 0 and amts[len(amts) // 2] / amts[-1] >= 0.3:
                    nd["tags"].add("split")
                    findings.append(dict(pattern="split", node=n, asset=asset, amount=round(total_out, 4),
                                         detail=f"разбито на {len(out_edges)} переводов"))

            # Транзит: всё пришедшее быстро ушло дальше.
            if in_edges and nd["role"] != SOURCE and total_in > 0 and total_out / total_in >= 0.9:
                dt = min(e["timestamp"] for e in out_edges) - min(e["timestamp"] for e in in_edges)
                if 0 <= dt <= transit_seconds:
                    nd["tags"].add("transit")
                    findings.append(dict(pattern="transit", node=n, asset=asset, amount=round(total_out, 4),
                                         detail=f"переслал {min(total_out / total_in, 1):.0%} полученного "
                                                f"через {max(dt // 60, 1)} мин"))

        # Консолидация: на один кошелёк сходятся средства с нескольких адресов.
        for key, in_edges in ins.items():
            senders = {e["sender"] for e in in_edges}
            if len(senders) >= consolidation_min:
                nd["tags"].add("consolidation")
                findings.append(dict(pattern="consolidation", node=n, asset=in_edges[0]["asset"],
                                     amount=round(sum(e["traced"] for e in in_edges), 4),
                                     detail=f"собрано с {len(senders)} адресов"))

    # Склеиваем отдельные отщипывания в цепочки из двух и более шагов.
    heads = [n for n in peel_next if not any(peel_next.get(p) == n for p in peel_next)]
    for h in heads:
        chain, cur = [h], h
        while cur in peel_next and peel_next[cur] not in chain:
            cur = peel_next[cur]
            chain.append(cur)
        if len(chain) >= 3:
            findings.append(dict(pattern="peel_chain", node=h, asset="", amount=0,
                                 detail=f"{len(chain) - 1} отщипываний подряд", chain=chain))
            for c in chain:
                g.nodes[c]["tags"].add("peel_chain")
    order = {p: i for i, p in enumerate(PATTERN_RU)}
    findings.sort(key=lambda f: order.get(f["pattern"], 99))
    return findings
