"""Обход графа переводов от скомпрометированного кошелька.

Главная идея версии 2 — «бюджет украденного». Старая версия шла по ВСЕМ исходящим
переводам каждого кошелька, поэтому граф быстро зарастал чужими деньгами: если вор
отправил 10 TON на чей-то кошелёк, все дальнейшие траты этого кошелька считались
«украденными». Теперь у каждого кошелька есть баланс отслеживаемых средств:

    пришло 100 TON украденных  →  кошелёк может «вынести» дальше не больше 100 TON.

Исходящие переводы списывают этот баланс по порядку времени (сначала уходит украденное —
самое осторожное допущение для поиска). Когда баланс исчерпан, дальнейшие траты кошелька
нас не интересуют. Обмен на DEX переводит баланс из одного актива в другой.

Режим mode="all" возвращает старое поведение (идти по всем переводам).
"""
from __future__ import annotations

import logging
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Callable

import networkx as nx

from ..collectors.tonapi import Cancelled
from ..core.address import is_real, norm, pretty
from ..core.labels import BRIDGE, CONTRACT, DEX, HUB, SOURCE, Label, Labeler

log = logging.getLogger("tontrace")

NANO = 10 ** 9
TON = "TON"
EPS = 1e-9


@dataclass
class Transfer:
    """Одно движение ценности между двумя адресами."""
    event_id: str
    timestamp: int
    lt: int
    idx: int               # порядковый номер действия внутри события
    sender: str
    recipient: str
    kind: str              # ton | jetton | nft | nft_purchase | swap | burn
    asset: str             # символ для показа: TON / USDT / NFT …
    asset_key: str         # по чему сравниваем: "TON", адрес мастер-контракта жетона, "NFT:<адрес>"
    amount: float          # полная сумма перевода
    comment: str = ""
    note: str = ""
    depth: int = 0
    traced: float = 0.0    # какая часть суммы относится к украденным средствам
    swap: dict | None = None            # для kind="swap": что на что обменяли
    hint: dict | None = field(default=None, repr=False)  # сведения о получателе из события

    @property
    def key(self) -> tuple:
        return (self.event_id, self.idx, self.sender, self.recipient, self.asset_key, self.kind)


@dataclass
class TraceConfig:
    max_depth: int = 5
    min_ton: float = 1.0
    min_jetton: float = 1.0           # в единицах жетона (для USDT ≈ доллары)
    incident_ts: int = 0
    end_ts: int | None = None
    max_events_per_account: int = 1000  # больше событий — считаем адрес хабом и не раскрываем
    follow_nft: bool = True
    follow_contracts: bool = False
    jettons: set[str] | None = None   # фильтр по символам/адресам жетонов; None — все
    max_nodes: int = 2000
    mode: str = "budget"              # budget — в пределах украденного; all — все переводы


@dataclass
class NodeInfo:
    address: str
    label: Label
    depth: int = 10 ** 6
    first_seen: int = 0
    expanded: bool = False
    fetched_from: int | None = None   # с какого момента уже загружены события
    events_scanned: int = 0
    stop_reason: str = ""
    tags: set[str] = field(default_factory=set)
    candidates: dict[tuple, Transfer] = field(default_factory=dict)  # все исходящие (не только отслеживаемые)
    holding: dict[str, float] = field(default_factory=dict)          # отслеживаемые средства, оставшиеся на кошельке
    untraced_out: int = 0             # исходящих переводов вне бюджета (чужие деньги)
    runs: int = 0


def _addr(obj: dict | None) -> str:
    try:
        return norm(obj["address"]) if obj and obj.get("address") else ""
    except ValueError:
        return ""


def _comment(a: dict) -> str:
    if a.get("comment"):
        return str(a["comment"])
    if a.get("encrypted_comment"):
        return "[зашифрованный комментарий]"
    return ""


def _num(v) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


class Tracer:
    def __init__(self, client, labeler: Labeler, cfg: TraceConfig,
                 on_progress: Callable[[dict], None] | None = None):
        self.client, self.labeler, self.cfg = client, labeler, cfg
        self.on_progress = on_progress or (lambda p: None)
        self.nodes: dict[str, NodeInfo] = {}
        self.incoming: dict[str, list[Transfer]] = defaultdict(list)  # отслеживаемые входящие по адресу
        self.symbols: dict[str, str] = {TON: TON}                     # asset_key -> символ
        self.warnings: list[str] = []
        self.source = ""

    # ---------- разметка ----------
    def _node(self, raw: str, hint: dict | None = None) -> NodeInfo:
        n = self.nodes.get(raw)
        if n is None:
            acc = {}
            if is_real(raw):
                acc = dict(self.client.account(raw) or {})
            if hint:  # AccountAddress из события тоже несёт name / is_scam / is_wallet
                for k in ("name", "is_scam", "is_wallet"):
                    if acc.get(k) is None and hint.get(k) is not None:
                        acc[k] = hint[k]
            n = NodeInfo(raw, self.labeler.classify(raw, acc))
            self.nodes[raw] = n
        return n

    # ---------- разбор событий ----------
    def _min_for(self, kind: str) -> float:
        if kind in ("ton", "nft_purchase"):
            return self.cfg.min_ton
        if kind in ("jetton", "burn", "swap"):
            return self.cfg.min_jetton
        return 0.0

    def _jetton_allowed(self, j: dict) -> bool:
        if not self.cfg.jettons:
            return True
        keys = {(j.get("symbol") or "").upper(), _addr(j)}
        return bool(keys & self.cfg.jettons)

    def _jetton(self, j: dict | None) -> tuple[str, str, int]:
        """(ключ, символ, число знаков) жетона. Ключ — адрес мастер-контракта: символ можно подделать."""
        j = j or {}
        key = _addr(j) or f"jetton:{j.get('symbol', '?')}"
        sym = j.get("symbol") or "JETTON"
        self.symbols.setdefault(key, sym)
        return key, sym, _num(j.get("decimals", 9)) if j.get("decimals") is not None else 9

    def _outgoing(self, me: str, ev: dict) -> list[Transfer]:
        """Достаёт из события все исходящие от `me` движения ценности."""
        out: list[Transfer] = []
        base = dict(event_id=ev["event_id"], timestamp=_num(ev.get("timestamp")), lt=_num(ev.get("lt")))
        for idx, act in enumerate(ev.get("actions") or []):
            if act.get("status", "ok") != "ok":
                continue
            t = act.get("type")
            a = act.get(t) or {}
            b = dict(base, idx=idx, sender=me)

            if t == "TonTransfer" and _addr(a.get("sender")) == me:
                out.append(Transfer(**b, recipient=_addr(a.get("recipient")), kind="ton", asset=TON,
                                    asset_key=TON, amount=_num(a.get("amount")) / NANO,
                                    comment=_comment(a), hint=a.get("recipient")))

            elif t == "JettonTransfer" and _addr(a.get("sender")) == me and a.get("recipient"):
                j = a.get("jetton") or {}
                if not self._jetton_allowed(j):
                    continue
                key, sym, dec = self._jetton(j)
                out.append(Transfer(**b, recipient=_addr(a["recipient"]), kind="jetton", asset=sym,
                                    asset_key=key, amount=_num(a.get("amount")) / 10 ** dec,
                                    comment=_comment(a), hint=a["recipient"]))

            elif t == "NftItemTransfer" and self.cfg.follow_nft and _addr(a.get("sender")) == me \
                    and a.get("recipient"):
                try:
                    nft = norm(a["nft"]) if a.get("nft") else ""
                except ValueError:
                    nft = ""
                out.append(Transfer(**b, recipient=_addr(a["recipient"]), kind="nft", asset="NFT",
                                    asset_key=f"NFT:{nft}", amount=1, comment=_comment(a),
                                    note=f"NFT {pretty(nft, False)}" if nft else "", hint=a["recipient"]))

            elif t == "NftPurchase" and _addr(a.get("buyer")) == me:
                amt = a.get("amount") or {}
                nft = a.get("nft") or {}
                out.append(Transfer(**b, recipient=_addr(a.get("seller")), kind="nft_purchase", asset=TON,
                                    asset_key=TON, amount=_num(amt.get("value")) / NANO,
                                    note="покупка NFT: средства ушли продавцу", hint=a.get("seller")))

            elif t == "JettonSwap" and _addr(a.get("user_wallet")) == me:
                # Обмен на DEX: деньги возвращаются этому же кошельку в другом активе.
                dex = a.get("dex") or "dex"
                router = _addr(a.get("router")) or f"dex:{dex}"
                if a.get("ton_in"):
                    in_key, in_sym, in_amt = TON, TON, _num(a["ton_in"]) / NANO
                else:
                    in_key, in_sym, dec = self._jetton(a.get("jetton_master_in"))
                    in_amt = _num(a.get("amount_in")) / 10 ** dec
                if a.get("ton_out"):
                    out_key, out_sym, out_amt = TON, TON, _num(a["ton_out"]) / NANO
                else:
                    out_key, out_sym, dec = self._jetton(a.get("jetton_master_out"))
                    out_amt = _num(a.get("amount_out")) / 10 ** dec
                out.append(Transfer(**b, recipient=router, kind="swap", asset=in_sym, asset_key=in_key,
                                    amount=in_amt, note=f"обмен на {dex}: {in_amt:g} {in_sym} → {out_amt:g} {out_sym}",
                                    swap=dict(in_key=in_key, in_sym=in_sym, in_amt=in_amt,
                                              out_key=out_key, out_sym=out_sym, out_amt=out_amt, dex=dex),
                                    hint={"address": router, "name": dex, "is_wallet": False}))

            elif t == "JettonBurn" and _addr(a.get("sender")) == me:
                j = a.get("jetton") or {}
                if not self._jetton_allowed(j):
                    continue
                key, sym, dec = self._jetton(j)
                out.append(Transfer(**b, recipient=_addr(j) or f"burn:{sym}", kind="burn", asset=sym,
                                    asset_key=key, amount=_num(a.get("amount")) / 10 ** dec,
                                    note="сжигание жетона (обычно так выводят через мост в другую сеть)",
                                    hint={"address": j.get("address"), "name": f"Сжигание {sym}",
                                          "is_wallet": False}))
        return out

    # ---------- загрузка событий кошелька ----------
    def _fetch(self, node: NodeInfo, start: int) -> None:
        me = node.address
        is_src = node.label.role == SOURCE
        log.info("[глубина %d] %s с %s", node.depth, pretty(me), start)
        self._report(current=me)
        events = list(self.client.iter_events(me, start_date=start or None, end_date=self.cfg.end_ts,
                                              max_events=self.cfg.max_events_per_account))
        node.fetched_from = start
        node.events_scanned = len(events)
        if len(events) >= self.cfg.max_events_per_account:
            if is_src:
                # События идут от новых к старым, поэтому при обрезке теряются самые ранние —
                # как раз те, что ближе всего к моменту кражи. Честно предупреждаем.
                self.warnings.append(
                    f"У исходного кошелька больше {len(events)} событий после инцидента — просмотрены только "
                    f"самые свежие. Сузьте окно полем «Не позже» или увеличьте лимит событий.")
            else:
                node.label.role = HUB
                node.tags.add("hub")
                node.stop_reason = (f"больше {len(events)} событий после поступления средств — похоже на горячий "
                                    f"кошелёк биржи или сервиса; дальше деньги смешиваются с чужими")
                return
        for ev in events:
            for tr in self._outgoing(me, ev):
                if not tr.recipient or tr.recipient == me:
                    continue
                if tr.timestamp < start or tr.amount < self._min_for(tr.kind):
                    continue
                node.candidates.setdefault(tr.key, tr)

    # ---------- распределение бюджета ----------
    def _allocate(self, node: NodeInfo) -> list[Transfer]:
        """Проходит по хронологии кошелька и решает, какая часть каждого исходящего перевода — украденное.

        Возвращает переводы, у которых отслеживаемая сумма выросла (их получателей надо пересчитать).
        """
        full = node.label.role == SOURCE or self.cfg.mode == "all"
        timeline = [(tr.timestamp, tr.lt, 0, tr.idx, tr) for tr in self.incoming[node.address] if tr.kind != "swap"]
        timeline += [(c.timestamp, c.lt, 1, c.idx, c) for c in node.candidates.values()]
        timeline.sort(key=lambda x: x[:4])

        bal: dict[str, float] = defaultdict(float)
        changed, untraced = [], 0
        for _, _, is_out, _, tr in timeline:
            if not is_out:
                bal[tr.asset_key] += tr.traced
                continue
            need = tr.amount
            if full:
                got = need
            else:
                got = min(need, bal[tr.asset_key])
                if got + EPS < min(need, self._min_for(tr.kind)) or got <= EPS:
                    got = 0.0   # остаток меньше порога пыли — не тянем его дальше
            if got > 0 and not full:
                bal[tr.asset_key] -= got
            if got > 0 and tr.swap and need > 0:
                bal[tr.swap["out_key"]] += tr.swap["out_amt"] * got / need
            if got <= 0:
                untraced += 1
            if got > tr.traced + EPS:
                tr.traced = got
                changed.append(tr)
        node.untraced_out = untraced
        node.holding = {} if full else {k: v for k, v in bal.items() if v > 1e-6}
        return changed

    # ---------- обход ----------
    def _report(self, **extra) -> None:
        if getattr(self.client, "should_stop", lambda: False)():
            raise Cancelled()
        self.on_progress(dict(nodes=len(self.nodes), transfers=sum(len(v) for v in self.incoming.values()),
                              api_calls=getattr(self.client, "calls", 0),
                              cache_hits=getattr(self.client, "cache_hits", 0), **extra))

    def _can_expand(self, node: NodeInfo) -> bool:
        if node.label.role == SOURCE:
            return True
        if node.label.role == HUB:
            return False
        if node.label.is_exit:
            node.stop_reason = "конечная точка"
            return False
        if node.label.role == CONTRACT and not self.cfg.follow_contracts:
            node.stop_reason = "неизвестный смарт-контракт — не раскрывается (можно включить в настройках запуска)"
            return False
        if not is_real(node.address):
            return False
        if node.depth >= self.cfg.max_depth:
            node.stop_reason = f"достигнута глубина {self.cfg.max_depth} — дальше не проверялось"
            return False
        if not node.expanded and len(self.nodes) >= self.cfg.max_nodes:
            node.stop_reason = "достигнут лимит числа адресов — дальше не проверялось"
            return False
        node.stop_reason = ""
        return True

    def run(self, source: str) -> nx.MultiDiGraph:
        src = norm(source)
        self.source = src
        s = self._node(src)
        s.label.role, s.label.reason, s.depth, s.first_seen = SOURCE, "указан вами", 0, self.cfg.incident_ts
        queue, queued = deque([src]), {src}
        while queue:
            me = queue.popleft()
            queued.discard(me)
            node = self.nodes[me]
            if not self._can_expand(node):
                continue
            node.runs += 1
            if node.runs > 50:   # защита от бесконечного пересчёта на циклах
                continue
            if node.label.role == SOURCE:
                start = self.cfg.incident_ts
            else:
                times = [t.timestamp for t in self.incoming[me] if t.kind != "swap"]
                if not times:
                    continue
                start = min(times)
            if node.fetched_from is None or start < node.fetched_from:
                self._fetch(node, start)
                if node.label.role == HUB:
                    continue
            node.expanded = True
            for tr in self._allocate(node):
                child = self._node(tr.recipient, tr.hint)
                if not any(x is tr for x in self.incoming[tr.recipient]):
                    self.incoming[tr.recipient].append(tr)
                if tr.kind == "swap":
                    child.label.role = DEX
                    child.label.reason = child.label.reason or "через этот адрес прошёл обмен"
                elif tr.kind == "burn":
                    child.label.role = BRIDGE
                    child.label.reason = "жетоны сожжены — обычно это вывод через мост"
                child.depth = min(child.depth, node.depth + 1)
                child.first_seen = min(child.first_seen or tr.timestamp, tr.timestamp)
                tr.depth = node.depth + 1
                if tr.kind != "swap" and tr.recipient not in queued:
                    queue.append(tr.recipient)
                    queued.add(tr.recipient)
            self._report(current=me)
        # Узлы, до которых очередь не дошла с проверкой (например, конечные точки), получают причину остановки.
        for n in self.nodes.values():
            if not n.expanded and not n.stop_reason:
                self._can_expand(n)
        return self.graph()

    @property
    def transfers(self) -> list[Transfer]:
        seen, out = set(), []
        for lst in self.incoming.values():
            for t in lst:
                if id(t) not in seen:
                    seen.add(id(t))
                    out.append(t)
        return sorted(out, key=lambda t: (t.timestamp, t.lt, t.idx))

    def graph(self) -> nx.MultiDiGraph:
        g = nx.MultiDiGraph()
        g.graph.update(source=self.source, symbols=dict(self.symbols), warnings=list(self.warnings),
                       mode=self.cfg.mode)
        for raw, n in self.nodes.items():
            g.add_node(raw, role=n.label.role, entity=n.label.entity, reason=n.label.reason,
                       confidence=n.label.confidence, depth=n.depth, is_wallet=n.label.is_wallet,
                       is_scam=n.label.is_scam, memo_required=n.label.memo_required,
                       expanded=n.expanded, stop_reason=n.stop_reason, events_scanned=n.events_scanned,
                       tags=set(n.tags), holding=dict(n.holding), untraced_out=n.untraced_out,
                       first_seen=n.first_seen)
        for i, tr in enumerate(self.transfers):
            g.add_edge(tr.sender, tr.recipient, key=i, event_id=tr.event_id, timestamp=tr.timestamp,
                       lt=tr.lt, sender=tr.sender, recipient=tr.recipient, kind=tr.kind, asset=tr.asset,
                       asset_key=tr.asset_key, amount=tr.amount, traced=tr.traced, comment=tr.comment,
                       note=tr.note, depth=tr.depth, swap=tr.swap)
        return g
