"""Проверка ядра на выдуманном сценарии кражи (без интернета). Запуск: pytest"""
from __future__ import annotations

import pytest

from app.core.address import friendly_to_raw, norm, raw_to_friendly
from app.core.labels import Labeler
from app.demo import A, T0, VICTIM, FakeClient
from app.pipeline import make_config, run_trace

PARAMS = dict(incident_ts=T0, max_depth=6, min_ton=1, min_jetton=1)


def by_label(res):
    return {n["id"]: n for n in res["nodes"]}


def edge(res, a, b, asset=None):
    return [e for e in res["edges"] if e["from"] == A(a) and e["to"] == A(b) and (asset is None or e["asset"] == asset)]


@pytest.fixture(scope="module")
def res():
    return run_trace(VICTIM, PARAMS, FakeClient())


def test_address_roundtrip():
    raw = "0:83dfd552e63729b472fcbcc8c45ebcc6691702558b68ec7527e1ba403a0f31a8"
    friendly = "EQCD39VS5jcptHL8vMjEXrzGaRcCVYto7HUn4bpAOg8xqB2N"
    assert raw_to_friendly(raw) == friendly
    assert friendly_to_raw(friendly) == raw
    assert norm(raw.upper().replace("0:", "0:")) == raw


def test_address_typo_is_rejected():
    with pytest.raises(ValueError):
        norm("EQCD39VS5jcptHL8vMjEXrzGaRcCVYto7HUn4bpAOg8xqB2M")  # последняя буква изменена
    with pytest.raises(ValueError):
        norm("просто текст")
    with pytest.raises(ValueError):
        norm("")


def test_exits_and_memos(res):
    memos = {r["memo"] for r in res["exits"]}
    assert {"123456789", "UID 88990011"} <= memos
    roles = {r["role"] for r in res["exits"]}
    assert {"cex", "dex"} <= roles


def test_dust_and_pre_incident_are_ignored(res):
    assert not edge(res, "victim", "spam")
    assert not edge(res, "victim", "friend")


def test_patterns(res):
    pats = {p["pattern"] for p in res["patterns"]}
    assert {"peel_chain", "consolidation", "split", "transit"} <= pats


def test_budget_limits_foreign_money(res):
    """p1 получил 85 TON украденных и потратил 500 своих: дальше прослеживается не больше 85."""
    e = edge(res, "p1", "shop")
    assert len(e) == 1 and e[0]["amount"] == 500 and e[0]["traced"] == 85
    e2 = edge(res, "shop", "supplier")
    assert e2 and e2[0]["traced"] == 85


def test_traced_never_exceeds_inflow(res):
    src = res["source"]
    for n in res["nodes"]:
        if n["id"] == src:
            continue
        got = {a["key"]: a["amount"] for a in n["received"]}
        for a in n["sent"]:
            if a["key"] in ("TON", "NFT") and n["id"] == A("G"):
                continue  # G получил TON из обмена — приход здесь не из переводов
            assert a["amount"] <= got.get(a["key"], 0) + 1e-6, (n["label"], a)


def test_swap_moves_budget_to_new_asset(res):
    """G обменял 997 USDT на 880 TON и отправил 875 TON в пул — эти TON считаются украденными."""
    e = edge(res, "G", "dedust-pool")
    assert e and e[0]["traced"] == 875
    g = by_label(res)[A("G")]
    assert any(h["asset"] == "TON" and abs(h["amount"] - 5) < 1e-6 for h in g["holding"])


def test_holding_and_unexplored(res):
    nodes = by_label(res)
    a = nodes[A("thiefA")]
    assert any(h["asset"] == "TON" and abs(h["amount"] - 10) < 1e-6 for h in a["holding"])
    m = nodes[A("mystery")]
    assert m["role"] == "contract" and not m["expanded"] and m["stop_reason"]
    codes = {r["code"] for r in res["distribution"]["rows"]}
    assert {"cex", "dex", "holding", "unexplored"} <= codes


def test_distribution_shares_sum_to_one(res):
    d = res["distribution"]
    assert d["basis"] == "usd"
    assert abs(sum(r["share"] for r in d["rows"]) - 1) < 1e-6
    stolen = {a["asset"]: a["amount"] for a in d["stolen"]}
    assert stolen == {"TON": 1000, "USDT": 5000, "NFT": 1}


def test_letters_only_for_exchanges(res):
    names = {l["entity"] for l in res["letters"]}
    assert names == {"Binance Deposit", "OKX"}
    binance = next(l for l in res["letters"] if l["entity"] == "Binance Deposit")
    assert "123456789" in binance["ru"] and "123456789" in binance["en"]


def test_chains_reach_source(res):
    assert res["chains"]
    for c in res["chains"]:
        assert c["path"][0] == res["source"] and c["path"][-1] == c["end"]
    binance = next(c for c in res["chains"] if c["entity"] == "Binance Deposit")
    assert binance["hops"] == 5 and "peel_chain" in binance["patterns"]


def test_mode_all_follows_everything():
    r = run_trace(VICTIM, dict(PARAMS, mode="all"), FakeClient())
    e = edge(r, "p1", "shop")
    assert e and e[0]["traced"] == 500


def test_depth_limit():
    r = run_trace(VICTIM, dict(PARAMS, max_depth=2), FakeClient())
    assert max(n["depth"] for n in r["nodes"]) == 2
    assert any(row["code"] == "unexplored" for row in r["distribution"]["rows"])


def test_manual_label_wins():
    r = run_trace(VICTIM, PARAMS, FakeClient(), manual_labels={A("B"): {"role": "cex", "entity": "Моя биржа"}})
    b = by_label(r)[A("B")]
    assert b["role"] == "cex" and b["entity"] == "Моя биржа" and not b["expanded"]
    assert not edge(r, "B", "D")


def test_jetton_filter():
    r = run_trace(VICTIM, dict(PARAMS, jettons="NOT"), FakeClient())
    assert not [e for e in r["edges"] if e["asset"] == "USDT"]


def test_no_outgoing_gives_clear_headline():
    r = run_trace(VICTIM, dict(PARAMS, incident_ts=T0 + 10 ** 7), FakeClient())
    assert not r["edges"] and "не найдено" in r["headline"]


def test_bad_params():
    with pytest.raises(ValueError):
        make_config({"max_depth": 99})
    with pytest.raises(ValueError):
        make_config({"min_ton": "много"})
    with pytest.raises(ValueError):
        make_config({"incident_ts": 100, "end_ts": 50})


def test_labeler_memo_required_is_exchange():
    lab = Labeler().classify("0:" + "0" * 64, {"memo_required": True})
    assert lab.role == "cex" and lab.confidence == "вероятно"
