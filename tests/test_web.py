"""Проверка веб-сервера и экспорта на демо-сценарии (без интернета)."""
from __future__ import annotations

import io
import time
import zipfile

import pytest

from app.demo import A, T0, VICTIM, FakeClient
from app.pipeline import run_trace
from app.report import export_zip, standalone_html, transfers_csv
from app.storage import Storage
from app.web.server import create_app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("TON_TRACER_HOME", str(tmp_path))
    app = create_app(Storage(tmp_path / "t.db"))
    return app.test_client()


def wait_done(client, inv_id, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        inv = client.get(f"/api/investigations/{inv_id}").get_json()
        if inv["status"] != "running":
            return inv
        time.sleep(0.1)
    raise AssertionError("расследование не завершилось")


def test_index_and_static(client):
    assert client.get("/").status_code == 200
    for f in ("app.css", "app.js", "view.js", "flowmap.js", "theme.js", "icon.svg"):
        assert client.get("/static/" + f).status_code == 200, f
    assert "script-src 'self'" in client.get("/").headers["Content-Security-Policy"]


def test_demo_run_and_exports(client):
    r = client.post("/api/investigations", json={"demo": True})
    assert r.status_code == 201
    inv = wait_done(client, r.get_json()["id"])
    assert inv["status"] == "done" and inv["result"]["exits"]
    assert client.get("/api/investigations").get_json()["items"][0]["headline"]

    z = client.get(f"/api/investigations/{inv['id']}/export.zip")
    names = set(zipfile.ZipFile(io.BytesIO(z.data)).namelist())
    assert {"отчёт.html", "сводка.md", "обращения-на-биржи.txt", "конечные-точки.csv", "переводы.csv"} <= names
    html = client.get(f"/api/investigations/{inv['id']}/report.html").data.decode()
    assert "TT.renderResult" in html and "http://" not in html.split("<script>window.STANDALONE")[0]

    assert client.delete(f"/api/investigations/{inv['id']}").status_code == 200
    assert client.get(f"/api/investigations/{inv['id']}").status_code == 404


def test_bad_input_is_explained(client):
    r = client.post("/api/investigations", json={"address": "не адрес", "params": {}})
    assert r.status_code == 400 and "адрес" in r.get_json()["error"].lower()
    r = client.post("/api/investigations", json={"address": VICTIM, "params": {"max_depth": 500}})
    assert r.status_code == 400


def test_foreign_site_cannot_call_api(client):
    assert client.post("/api/investigations", json={"demo": True}, headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.get("/api/meta", headers={"Host": "evil.example"}).status_code == 403
    assert client.post("/api/investigations", data="demo=1").status_code == 415


def test_labels_and_settings(client):
    assert client.post("/api/labels", json={"address": A("B"), "role": "cex", "entity": "Тест"}).status_code == 200
    items = client.get("/api/labels").get_json()["items"]
    assert items[0]["entity"] == "Тест"
    assert client.post("/api/labels", json={"address": "мусор", "role": "cex"}).status_code == 400
    assert client.delete("/api/labels/" + A("B")).status_code == 200
    assert client.get("/api/labels").get_json()["items"] == []

    assert client.post("/api/settings", json={"tonapi_key": "плохой ключ"}).status_code == 400
    key = "A" * 40
    assert client.post("/api/settings", json={"tonapi_key": key}).get_json()["has_key"] is True
    assert key not in client.get("/api/meta").get_data(as_text=True)   # ключ наружу не отдаётся


def test_hostile_memo_is_neutralised():
    """Комментарий к переводу пишет посторонний: он не должен ломать отчёт или исполняться в Excel."""
    res = run_trace(VICTIM, dict(incident_ts=T0, max_depth=6), FakeClient())
    res["edges"][0]["memo"] = "</script><script>alert(1)</script>"
    res["edges"][1]["memo"] = "=HYPERLINK(\"http://x\")"
    html = standalone_html(res)
    assert "</script><script>alert(1)" not in html
    csv_text = transfers_csv(res).decode("utf-8-sig")
    assert "'=HYPERLINK" in csv_text
    assert export_zip(res)
