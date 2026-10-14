from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from .config import GuildConfig

SCHEMA = """
CREATE TABLE IF NOT EXISTS guilds (id TEXT PRIMARY KEY, config TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY, guild TEXT NOT NULL, actor TEXT, action TEXT NOT NULL, detail TEXT NOT NULL, created REAL NOT NULL);
CREATE INDEX IF NOT EXISTS audit_guild ON audit(guild, id DESC);
CREATE TABLE IF NOT EXISTS warnings (id INTEGER PRIMARY KEY, guild TEXT NOT NULL, user TEXT NOT NULL, actor TEXT NOT NULL, reason TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS alliances (id INTEGER PRIMARY KEY, guild TEXT NOT NULL, name TEXT NOT NULL COLLATE NOCASE, tag TEXT NOT NULL COLLATE NOCASE, role TEXT, owned INTEGER NOT NULL DEFAULT 1, active INTEGER NOT NULL DEFAULT 1, created REAL NOT NULL, UNIQUE(guild,name), UNIQUE(guild,tag), UNIQUE(guild,role));
CREATE TABLE IF NOT EXISTS members (guild TEXT NOT NULL, user TEXT NOT NULL, alliance INTEGER NOT NULL REFERENCES alliances(id) ON DELETE CASCADE, rank INTEGER NOT NULL CHECK(rank IN(1,4,5)), joined REAL NOT NULL, PRIMARY KEY(guild,user));
CREATE UNIQUE INDEX IF NOT EXISTS single_leader ON members(alliance) WHERE rank=5;
CREATE TABLE IF NOT EXISTS presidents (id INTEGER PRIMARY KEY, guild TEXT NOT NULL, user TEXT NOT NULL, started REAL NOT NULL, ended REAL);
CREATE UNIQUE INDEX IF NOT EXISTS single_president ON presidents(guild) WHERE ended IS NULL;
CREATE TABLE IF NOT EXISTS armory (guild TEXT NOT NULL, week TEXT NOT NULL, slot INTEGER NOT NULL CHECK(slot BETWEEN 1 AND 8), alliance INTEGER NOT NULL REFERENCES alliances(id) ON DELETE CASCADE, PRIMARY KEY(guild,week,slot));
CREATE TABLE IF NOT EXISTS apc (guild TEXT NOT NULL, user TEXT NOT NULL, main INTEGER NOT NULL, second INTEGER NOT NULL, main_faction TEXT, second_faction TEXT, updated REAL NOT NULL, PRIMARY KEY(guild,user));
CREATE TABLE IF NOT EXISTS birthdays (guild TEXT NOT NULL, user TEXT NOT NULL, month INTEGER NOT NULL, day INTEGER NOT NULL, year INTEGER, PRIMARY KEY(guild,user));
CREATE TABLE IF NOT EXISTS panels (id INTEGER PRIMARY KEY, guild TEXT NOT NULL, kind TEXT NOT NULL, channel TEXT NOT NULL, message TEXT UNIQUE, title TEXT NOT NULL, payload TEXT NOT NULL, creator TEXT NOT NULL, closes REAL, closed INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS votes (panel INTEGER NOT NULL REFERENCES panels(id) ON DELETE CASCADE, user TEXT NOT NULL, option INTEGER NOT NULL, PRIMARY KEY(panel,user,option));
CREATE TABLE IF NOT EXISTS tickets (id INTEGER PRIMARY KEY, guild TEXT NOT NULL, owner TEXT NOT NULL, channel TEXT UNIQUE, status TEXT NOT NULL DEFAULT 'creating', category TEXT NOT NULL, updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS jobs (id INTEGER PRIMARY KEY, guild TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL, due REAL NOT NULL, state TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0, lease REAL, key TEXT UNIQUE, error TEXT);
CREATE INDEX IF NOT EXISTS jobs_due ON jobs(state,due);
CREATE TABLE IF NOT EXISTS event_series (id INTEGER PRIMARY KEY, guild TEXT NOT NULL, name TEXT NOT NULL, channel TEXT NOT NULL, definition TEXT NOT NULL, anchor REAL NOT NULL, interval_seconds INTEGER NOT NULL, next_start REAL NOT NULL, status TEXT NOT NULL DEFAULT 'active', version INTEGER NOT NULL DEFAULT 1, created REAL NOT NULL);
CREATE INDEX IF NOT EXISTS series_guild ON event_series(guild,status);
CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, guild TEXT NOT NULL, name TEXT NOT NULL, channel TEXT NOT NULL, starts REAL NOT NULL, ends REAL NOT NULL, status TEXT NOT NULL DEFAULT 'scheduled', definition TEXT NOT NULL, series_id INTEGER REFERENCES event_series(id));
CREATE TABLE IF NOT EXISTS refs (guild TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL, PRIMARY KEY(guild,key));
CREATE TABLE IF NOT EXISTS languages (user TEXT PRIMARY KEY, language TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, user TEXT NOT NULL, csrf TEXT NOT NULL, expires REAL NOT NULL);
"""


class Conflict(ValueError):
    pass


class Database:
    """One connection per application process. Never hold a transaction over await."""

    def __init__(self, path: str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=5000")
        if self.conn.execute("PRAGMA user_version").fetchone()[0] > 2:
            raise RuntimeError("Database is newer than this application")
        self.conn.executescript(SCHEMA)
        # Additive upgrade: retain v0.1 events, jobs, settings, and history.
        with self.transaction():
            if "series_id" not in {r[1] for r in self.execute("PRAGMA table_info(events)")}:
                self.execute("ALTER TABLE events ADD COLUMN series_id INTEGER REFERENCES event_series(id)")
            self.execute("CREATE UNIQUE INDEX IF NOT EXISTS series_occurrence ON events(series_id,starts) WHERE series_id IS NOT NULL")
            self.execute("PRAGMA user_version=2")

    def close(self):
        self.conn.close()

    def execute(self, sql, args=()):
        return self.conn.execute(sql, args)

    def one(self, sql, args=()):
        row = self.execute(sql, args).fetchone()
        return dict(row) if row else None

    def all(self, sql, args=()):
        return [dict(row) for row in self.execute(sql, args).fetchall()]

    @contextmanager
    def transaction(self):
        self.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.execute("COMMIT")
        except BaseException:
            self.execute("ROLLBACK")
            raise

    def config(self, guild) -> tuple[GuildConfig, int]:
        row = self.one("SELECT * FROM guilds WHERE id=?", (str(guild),))
        return (GuildConfig.model_validate_json(row["config"]), row["version"]) if row else (GuildConfig(), 0)

    def save_config(self, guild, config: GuildConfig, version: int, actor, after_save=None):
        with self.transaction():
            _, current = self.config(guild)
            if current != version:
                raise Conflict("Settings changed in another session. Reload before saving.")
            self.execute("INSERT INTO guilds VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET config=excluded.config,version=excluded.version", (str(guild), config.model_dump_json(), current + 1))
            self.audit(guild, actor, "settings.saved", {"version": current + 1})
            if after_save:
                after_save()
        return current + 1

    def audit(self, guild, actor, action, detail):
        self.execute("INSERT INTO audit(guild,actor,action,detail,created) VALUES(?,?,?,?,?)", (str(guild), str(actor), action, json.dumps(detail), time.time()))

    def ref(self, guild, key, value=None):
        if value is not None:
            self.execute("INSERT INTO refs VALUES(?,?,?) ON CONFLICT(guild,key) DO UPDATE SET value=excluded.value", (str(guild), key, json.dumps(value)))
            return value
        row = self.one("SELECT value FROM refs WHERE guild=? AND key=?", (str(guild), key))
        return json.loads(row["value"]) if row else None

    def enqueue(self, guild, kind, payload, due, key=None):
        return self.execute("INSERT OR IGNORE INTO jobs(guild,kind,payload,due,key) VALUES(?,?,?,?,?)", (str(guild), kind, json.dumps(payload), due, key)).lastrowid

    def claim(self, now=None):
        now = now if now is not None else time.time()
        with self.transaction():
            row = self.one("SELECT * FROM jobs WHERE (state='pending' AND due<=?) OR (state='running' AND lease<=?) ORDER BY due,id LIMIT 1", (now, now))
            if not row:
                return None
            self.execute("UPDATE jobs SET state='running',lease=?,attempts=attempts+1 WHERE id=?", (now + 300, row["id"]))
            row["attempts"] += 1
        row["payload"] = json.loads(row["payload"])
        return row

    def complete(self, job):
        self.execute("UPDATE jobs SET state='done',lease=NULL,error=NULL WHERE id=?", (job["id"],))

    def fail(self, job, error):
        self.execute("UPDATE jobs SET state=?,due=?,lease=NULL,error=? WHERE id=?", ("failed" if job["attempts"] >= 5 else "pending", time.time() + min(300, 2 ** job["attempts"] * 5), str(error)[:300], job["id"]))
