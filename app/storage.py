"""Хранилище: расследования, ручные метки и настройки в одном файле SQLite.

Файл лежит в ~/.ton-tracer/tracer.db — вне папки с кодом, поэтому в git не попадёт.
SQLite встроен в Python, отдельный сервер базы данных не нужен.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS investigations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at INTEGER NOT NULL,
    finished_at INTEGER,
    address TEXT NOT NULL,          -- как ввёл пользователь
    title TEXT NOT NULL DEFAULT '',
    params TEXT NOT NULL,           -- JSON с параметрами запуска
    status TEXT NOT NULL,           -- running | done | error | cancelled
    error TEXT NOT NULL DEFAULT '',
    is_demo INTEGER NOT NULL DEFAULT 0,
    result TEXT                     -- JSON с результатом (большой)
);
CREATE TABLE IF NOT EXISTS labels (
    address TEXT PRIMARY KEY,       -- raw-адрес в нижнем регистре
    role TEXT NOT NULL,
    entity TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


class Storage:
    def __init__(self, path: str | Path):
        self.path = str(path)
        self._lock = threading.Lock()   # к базе обращаются и веб-сервер, и фоновая задача
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.executescript(SCHEMA)
            # Если программу закрыли посреди расследования, оно навсегда осталось бы «в работе».
            self._db.execute("UPDATE investigations SET status='error', error='Программа была закрыта до завершения' "
                             "WHERE status='running'")
            self._db.commit()

    def _exec(self, sql: str, args: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._db.execute(sql, args)
            self._db.commit()
            return cur

    def _rows(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._db.execute(sql, args).fetchall()

    # ---------- расследования ----------
    def create_investigation(self, address: str, title: str, params: dict, is_demo: bool = False) -> int:
        cur = self._exec("INSERT INTO investigations (created_at, address, title, params, status, is_demo) "
                         "VALUES (?,?,?,?,?,?)",
                         (int(time.time()), address, title, json.dumps(params, ensure_ascii=False), "running",
                          int(is_demo)))
        return int(cur.lastrowid)

    def finish_investigation(self, inv_id: int, status: str, result: dict | None = None, error: str = "") -> None:
        self._exec("UPDATE investigations SET status=?, result=?, error=?, finished_at=? WHERE id=?",
                   (status, json.dumps(result, ensure_ascii=False) if result is not None else None, error,
                    int(time.time()), inv_id))

    @staticmethod
    def _meta(r: sqlite3.Row) -> dict:
        return dict(id=r["id"], created_at=r["created_at"], finished_at=r["finished_at"], address=r["address"],
                    title=r["title"], params=json.loads(r["params"]), status=r["status"], error=r["error"],
                    is_demo=bool(r["is_demo"]))

    def list_investigations(self) -> list[dict]:
        out = []
        for r in self._rows("SELECT id, created_at, finished_at, address, title, params, status, error, is_demo, "
                            "json_extract(result, '$.headline') AS headline FROM investigations ORDER BY id DESC"):
            m = self._meta(r)
            m["headline"] = r["headline"] or ""
            out.append(m)
        return out

    def get_investigation(self, inv_id: int, with_result: bool = True) -> dict | None:
        rows = self._rows("SELECT * FROM investigations WHERE id=?", (inv_id,))
        if not rows:
            return None
        m = self._meta(rows[0])
        if with_result:
            m["result"] = json.loads(rows[0]["result"]) if rows[0]["result"] else None
        return m

    def rename_investigation(self, inv_id: int, title: str) -> None:
        self._exec("UPDATE investigations SET title=? WHERE id=?", (title[:200], inv_id))

    def delete_investigation(self, inv_id: int) -> None:
        self._exec("DELETE FROM investigations WHERE id=?", (inv_id,))

    # ---------- метки ----------
    def labels(self) -> dict[str, dict]:
        return {r["address"]: dict(role=r["role"], entity=r["entity"], note=r["note"], updated_at=r["updated_at"])
                for r in self._rows("SELECT * FROM labels ORDER BY updated_at DESC")}

    def set_label(self, address: str, role: str, entity: str, note: str) -> None:
        self._exec("INSERT INTO labels (address, role, entity, note, updated_at) VALUES (?,?,?,?,?) "
                   "ON CONFLICT(address) DO UPDATE SET role=excluded.role, entity=excluded.entity, "
                   "note=excluded.note, updated_at=excluded.updated_at",
                   (address, role, entity[:120], note[:500], int(time.time())))

    def delete_label(self, address: str) -> None:
        self._exec("DELETE FROM labels WHERE address=?", (address,))

    # ---------- настройки ----------
    def get_setting(self, key: str, default: str = "") -> str:
        rows = self._rows("SELECT value FROM settings WHERE key=?", (key,))
        return rows[0]["value"] if rows else default

    def set_setting(self, key: str, value: str) -> None:
        if value == "":
            self._exec("DELETE FROM settings WHERE key=?", (key,))
        else:
            self._exec("INSERT INTO settings (key, value) VALUES (?,?) "
                       "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
