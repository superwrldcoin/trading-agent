"""App data store: one SQLite file (memory/app.db, private, gitignored) that survives restarts and browser resets.

  python tools/store.py info                       users, documents, settings, last backup
  python tools/store.py export [--out FILE]        everything for a user as JSON (default: stdout)
  python tools/store.py import FILE                restore a JSON export (current data is backed up first)
  python tools/store.py backup                     copy the database to memory/backups/ now

Tables (multi-user ready; today there is one user, "local"):
  users(id, name, created)
  settings(user_id, key, value)      UI settings and switches such as rules_enabled (JSON values)
  docs(user_id, name, body)          whole documents, e.g. "paper" = the paper account and journal
Writes are transactional (WAL mode), and the first write of each UTC day snapshots the database to
memory/backups/app-YYYY-MM-DD.db (the last BACKUP_KEEP days are kept). memory/paper.json from earlier versions is
imported automatically on first use and left in place.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.environ.get("TA_DB_PATH") or ROOT / "memory" / "app.db")
BACKUP_SUBDIR = "backups"            # next to the database file
BACKUP_KEEP = 14
DEFAULT_USER = "local"
LEGACY_DOCS = {"paper": ROOT / "memory" / "paper.json"}
SCHEMA_VERSION = 1
_LOCK = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY, name TEXT NOT NULL, created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS settings(user_id TEXT NOT NULL REFERENCES users(id), key TEXT NOT NULL, value TEXT NOT NULL,
                                    updated TEXT NOT NULL, PRIMARY KEY(user_id, key));
CREATE TABLE IF NOT EXISTS docs(user_id TEXT NOT NULL REFERENCES users(id), name TEXT NOT NULL, body TEXT NOT NULL,
                                updated TEXT NOT NULL, PRIMARY KEY(user_id, name));
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def backup_dir() -> Path:
    return path().parent / BACKUP_SUBDIR


def path() -> Path:
    return DB_PATH


@contextmanager
def _conn(write: bool):
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        con = sqlite3.connect(p, timeout=10)
        try:
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("PRAGMA foreign_keys=ON")
            con.executescript(SCHEMA)
            con.execute("INSERT OR IGNORE INTO meta VALUES('schema_version', ?)", (str(SCHEMA_VERSION),))
            con.execute("INSERT OR IGNORE INTO users VALUES(?, ?, ?)", (DEFAULT_USER, "Local player", _now()))
            con.commit()   # no open transaction before a backup: SQLite's backup would wait on it forever
            if write:
                _daily_backup(con)     # snapshot taken before the day's first change
            yield con
            con.commit()
        finally:
            con.close()


def _ensure_user(con, user: str) -> None:
    con.execute("INSERT OR IGNORE INTO users VALUES(?, ?, ?)", (user, user, _now()))


# ---------- documents ----------

def get_doc(name: str, default=None, user: str = DEFAULT_USER):
    """A stored document, or (once) the legacy JSON file it replaces, or `default`. Never creates the database
    just to read: a missing database means nothing has been saved yet."""
    if path().exists():
        with _conn(write=False) as con:
            row = con.execute("SELECT body FROM docs WHERE user_id=? AND name=?", (user, name)).fetchone()
        if row:
            return json.loads(row[0])
    legacy = LEGACY_DOCS.get(name)
    if user == DEFAULT_USER and legacy is not None and legacy.exists():
        body = json.loads(legacy.read_text(encoding="utf-8"))
        put_doc(name, body, user)          # one-time migration; the old file stays as a backup
        return body
    return default


def put_doc(name: str, body, user: str = DEFAULT_USER) -> None:
    with _conn(write=True) as con:
        _ensure_user(con, user)
        con.execute("INSERT INTO docs VALUES(?, ?, ?, ?) ON CONFLICT(user_id, name) DO UPDATE SET body=excluded.body, "
                    "updated=excluded.updated", (user, name, json.dumps(body), _now()))


# ---------- settings ----------

SETTING_DEFAULTS = {"rules_enabled": True}


def get_settings(user: str = DEFAULT_USER) -> dict:
    out = dict(SETTING_DEFAULTS)
    if path().exists():
        with _conn(write=False) as con:
            out.update({k: json.loads(v) for k, v in
                        con.execute("SELECT key, value FROM settings WHERE user_id=?", (user,)).fetchall()})
    return out


def set_settings(values: dict, user: str = DEFAULT_USER) -> dict:
    if not isinstance(values, dict):
        raise ValueError("settings must be an object")
    with _conn(write=True) as con:
        _ensure_user(con, user)
        for k, v in values.items():
            if not isinstance(k, str) or len(k) > 60:
                raise ValueError(f"bad setting name {k!r}")
            con.execute("INSERT INTO settings VALUES(?, ?, ?, ?) ON CONFLICT(user_id, key) DO UPDATE SET "
                        "value=excluded.value, updated=excluded.updated", (user, k, json.dumps(v), _now()))
    return get_settings(user)


def setting(key: str, user: str = DEFAULT_USER):
    return get_settings(user).get(key, SETTING_DEFAULTS.get(key))


# ---------- users (one today; the tables are ready for more) ----------

def users() -> list[dict]:
    if not path().exists():
        return [{"id": DEFAULT_USER, "name": "Local player"}]
    with _conn(write=False) as con:
        return [{"id": i, "name": n, "created": c} for i, n, c in con.execute("SELECT id, name, created FROM users")]


# ---------- export / import / backups ----------

def export(user: str = DEFAULT_USER) -> dict:
    docs = {}
    if path().exists():
        with _conn(write=False) as con:
            docs = {n: json.loads(b) for n, b in con.execute("SELECT name, body FROM docs WHERE user_id=?", (user,))}
    for name in LEGACY_DOCS:
        if name not in docs:
            d = get_doc(name, None, user)
            if d is not None:
                docs[name] = d
    return {"format": "trading-agent-export", "version": SCHEMA_VERSION, "exported": _now(), "user": user,
            "settings": get_settings(user), "docs": docs}


def import_(data: dict, user: str = DEFAULT_USER) -> dict:
    if not isinstance(data, dict) or data.get("format") != "trading-agent-export":
        raise ValueError("not a trading-agent export file")
    if not isinstance(data.get("docs", {}), dict) or not isinstance(data.get("settings", {}), dict):
        raise ValueError("export file is damaged (docs/settings)")
    backup(force=True)
    for name, body in data.get("docs", {}).items():
        put_doc(str(name), body, user)
    set_settings(data.get("settings", {}), user)
    return {"docs": sorted(data.get("docs", {})), "settings": sorted(data.get("settings", {}))}


def _daily_backup(con) -> None:
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    target = backup_dir() / f"app-{day}.db"
    if target.exists():
        return
    backup_dir().mkdir(parents=True, exist_ok=True)
    dst = sqlite3.connect(target)
    try:
        con.backup(dst)
    finally:
        dst.close()
    for old in sorted(backup_dir().glob("app-*.db"))[:-BACKUP_KEEP]:
        old.unlink(missing_ok=True)


def backup(force: bool = False) -> Path | None:
    """Snapshot the database now (force) or only if today's snapshot is missing."""
    if not path().exists():
        return None
    with _conn(write=False) as con:
        if force:
            backup_dir().mkdir(parents=True, exist_ok=True)
            target = backup_dir() / f"app-{datetime.now(timezone.utc):%Y-%m-%d_%H%M%S}.db"
            dst = sqlite3.connect(target)
            try:
                con.backup(dst)
            finally:
                dst.close()
            return target
        _daily_backup(con)
    return backup_dir() / f"app-{datetime.now(timezone.utc):%Y-%m-%d}.db"


def info(user: str = DEFAULT_USER) -> dict:
    out = {"path": str(path()), "exists": path().exists(), "users": users(), "settings": get_settings(user),
           "docs": [], "backups": sorted(p.name for p in backup_dir().glob("app-*.db"))[-3:] if backup_dir().exists() else []}
    if path().exists():
        with _conn(write=False) as con:
            out["docs"] = [{"name": n, "updated": u, "bytes": len(b)} for n, u, b in
                           con.execute("SELECT name, updated, body FROM docs WHERE user_id=?", (user,))]
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="App data store (memory/app.db).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("info")
    sub.add_parser("backup")
    e = sub.add_parser("export")
    e.add_argument("--out")
    i = sub.add_parser("import")
    i.add_argument("file")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "info":
            print(json.dumps(info(), indent=1))
        elif a.cmd == "backup":
            print(f"Backup: {backup(force=True) or 'nothing to back up yet'}")
        elif a.cmd == "export":
            text = json.dumps(export(), indent=1)
            if a.out:
                Path(a.out).write_text(text, encoding="utf-8")
                print(f"Exported to {a.out}")
            else:
                print(text)
        else:
            r = import_(json.loads(Path(a.file).read_text(encoding="utf-8")))
            print(f"Imported docs {r['docs']} and settings {r['settings']} (previous data backed up first)")
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
