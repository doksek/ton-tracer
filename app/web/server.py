"""Локальный веб-сервер приложения (Flask).

Интерфейс — обычная веб-страница, которую сервер отдаёт только этому компьютеру (127.0.0.1).
Страница общается с сервером через адреса /api/…, перечисленные ниже.
"""
from __future__ import annotations

import io
import re
from urllib.parse import quote

from flask import Flask, abort, jsonify, request, send_file

from .. import APP_NAME, __version__
from ..core.address import norm, pretty
from ..core.labels import MANUAL_ROLES, ROLE_RU
from ..core.paths import data_dir, resource_dir
from ..demo import T0, VICTIM
from ..jobs import JobManager
from ..pipeline import make_config
from ..report import export_zip, standalone_html
from ..storage import Storage

ALLOWED_HOSTS = re.compile(r"^(127\.0\.0\.1|localhost)(:\d+)?$")


def create_app(storage: Storage | None = None) -> Flask:
    web = resource_dir() / "web"
    app = Flask(__name__, static_folder=str(web / "static"), static_url_path="/static")
    app.json.ensure_ascii = False
    store = storage or Storage(data_dir() / "tracer.db")
    jobs = JobManager(store)
    app.config.update(store=store, jobs=jobs)

    @app.before_request
    def guard():
        # Защита от чужих сайтов: открытая в браузере посторонняя страница не должна
        # иметь возможность командовать локальной программой.
        if not ALLOWED_HOSTS.match(request.host or ""):
            abort(403)
        if request.method in ("POST", "PUT", "DELETE"):
            origin = request.headers.get("Origin")
            if origin and not ALLOWED_HOSTS.match(origin.split("://", 1)[-1]):
                abort(403)
            if request.method != "DELETE" and not request.is_json:
                abort(415)

    @app.after_request
    def headers(resp):
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
            "script-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        if request.path.startswith(("/api/", "/static/")) or request.path == "/":
            resp.headers["Cache-Control"] = "no-store"
        return resp

    def fail(msg: str, code: int = 400):
        return jsonify(error=msg), code

    @app.get("/")
    def index():
        return send_file(web / "templates" / "index.html")

    @app.get("/api/meta")
    def meta():
        return jsonify(name=APP_NAME, version=__version__, data_dir=str(data_dir()),
                       has_key=bool(store.get_setting("tonapi_key")), busy=jobs.busy(),
                       roles=[dict(code=r, title=ROLE_RU[r]) for r in MANUAL_ROLES])

    # ---------- расследования ----------
    @app.get("/api/investigations")
    def inv_list():
        return jsonify(items=store.list_investigations(), busy=jobs.busy())

    @app.post("/api/investigations")
    def inv_create():
        body = request.get_json(silent=True) or {}
        demo = bool(body.get("demo"))
        params = dict(body.get("params") or {})
        if demo:
            address, params = VICTIM, dict(incident_ts=T0, max_depth=6, min_ton=1, min_jetton=1)
            title = "Демо: выдуманная кража"
        else:
            address = str(body.get("address") or "").strip()
            title = str(body.get("title") or "").strip()[:200]
        try:
            norm(address)
            make_config(params)
            inv_id = jobs.start(address, params, title, demo)
        except ValueError as e:
            return fail(str(e))
        except RuntimeError as e:
            return fail(str(e), 409)
        return jsonify(id=inv_id), 201

    @app.get("/api/investigations/<int:inv_id>")
    def inv_get(inv_id: int):
        inv = store.get_investigation(inv_id)
        if not inv:
            return fail("Расследование не найдено", 404)
        inv["progress"] = jobs.progress(inv_id)
        return jsonify(inv)

    @app.post("/api/investigations/<int:inv_id>/cancel")
    def inv_cancel(inv_id: int):
        return jsonify(ok=jobs.cancel(inv_id))

    @app.post("/api/investigations/<int:inv_id>/rename")
    def inv_rename(inv_id: int):
        store.rename_investigation(inv_id, str((request.get_json(silent=True) or {}).get("title") or "").strip())
        return jsonify(ok=True)

    @app.delete("/api/investigations/<int:inv_id>")
    def inv_delete(inv_id: int):
        if jobs.busy() == inv_id:
            return fail("Сначала остановите расследование", 409)
        store.delete_investigation(inv_id)
        return jsonify(ok=True)

    def _done(inv_id: int) -> dict:
        inv = store.get_investigation(inv_id)
        if not inv or not inv.get("result"):
            abort(404)
        return inv

    def _title(inv: dict) -> str:
        return inv["title"] or f"Расследование {pretty(norm(inv['address']))[:10]}…"

    @app.get("/api/investigations/<int:inv_id>/export.zip")
    def inv_zip(inv_id: int):
        inv = _done(inv_id)
        name = f"ton-tracer-{inv_id}.zip"
        return send_file(io.BytesIO(export_zip(inv["result"], _title(inv))), mimetype="application/zip",
                         as_attachment=True, download_name=name)

    @app.get("/api/investigations/<int:inv_id>/report.html")
    def inv_html(inv_id: int):
        inv = _done(inv_id)
        resp = send_file(io.BytesIO(standalone_html(inv["result"], _title(inv)).encode("utf-8")),
                         mimetype="text/html", as_attachment=True, download_name=f"ton-tracer-{inv_id}.html")
        resp.headers["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(f'ton-tracer-{inv_id}.html')}"
        return resp

    # ---------- метки ----------
    @app.get("/api/labels")
    def labels_list():
        return jsonify(items=[dict(raw=a, address=pretty(a, v["role"] == "wallet"), **v)
                              for a, v in store.labels().items()])

    @app.post("/api/labels")
    def labels_set():
        b = request.get_json(silent=True) or {}
        try:
            raw = norm(str(b.get("address") or ""))
        except ValueError as e:
            return fail(str(e))
        role = b.get("role")
        if role not in MANUAL_ROLES:
            return fail("Выберите тип адреса")
        store.set_label(raw, role, str(b.get("entity") or "").strip(), str(b.get("note") or "").strip())
        return jsonify(ok=True)

    @app.delete("/api/labels/<path:address>")
    def labels_delete(address: str):
        try:
            store.delete_label(norm(address))
        except ValueError as e:
            return fail(str(e))
        return jsonify(ok=True)

    # ---------- настройки ----------
    @app.post("/api/settings")
    def settings_set():
        b = request.get_json(silent=True) or {}
        if "tonapi_key" in b:
            key = str(b["tonapi_key"] or "").strip()
            if key and not re.fullmatch(r"[A-Za-z0-9_\-]{20,200}", key):
                return fail("Ключ выглядит неправильно: в нём только латинские буквы, цифры, «-» и «_»")
            store.set_setting("tonapi_key", key)   # сам ключ обратно в интерфейс не отдаём
        return jsonify(ok=True, has_key=bool(store.get_setting("tonapi_key")))

    return app
