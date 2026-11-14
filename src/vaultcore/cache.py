"""SQLite result cache — re-running on unchanged notes costs nothing."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from pathlib import Path

from .settings import STATE_DIR


def make_key(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8", "surrogatepass")).hexdigest()


class Cache:
    def __init__(self, vault: str | Path | None):
        self._lock = threading.Lock()
        if vault is None:
            self._db = sqlite3.connect(":memory:", check_same_thread=False)
        else:
            d = Path(vault) / STATE_DIR
            d.mkdir(parents=True, exist_ok=True)
            self._db = sqlite3.connect(str(d / "cache.sqlite"), check_same_thread=False)
            try:
                self._db.execute("PRAGMA journal_mode=WAL")
            except sqlite3.Error:
                pass
        self._db.execute("CREATE TABLE IF NOT EXISTS results (key TEXT PRIMARY KEY, value TEXT NOT NULL, ts REAL DEFAULT (strftime('%s','now')))")
        self._db.commit()
        self.hits = 0
        self.misses = 0

    def get(self, key: str):
        with self._lock:
            row = self._db.execute("SELECT value FROM results WHERE key=?", (key,)).fetchone()
        if row is None:
            self.misses += 1
            return None
        self.hits += 1
        try:
            return json.loads(row[0])
        except ValueError:
            return None

    def put(self, key: str, value) -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO results(key, value) VALUES (?, ?)", (key, json.dumps(value, ensure_ascii=False)))
            self._db.commit()

    def clear(self) -> int:
        with self._lock:
            n = self._db.execute("SELECT COUNT(*) FROM results").fetchone()[0]
            self._db.execute("DELETE FROM results")
            self._db.commit()
        self.hits = self.misses = 0
        return n

    def size(self) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM results").fetchone()[0]

    def close(self) -> None:
        with self._lock:
            self._db.close()
