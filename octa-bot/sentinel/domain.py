from __future__ import annotations

import json
import math
import re
import secrets
import time
from datetime import datetime, date, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo
from .db import Database
from .config import EventDefinition


class RuleError(ValueError):
    """An expected domain rejection, safe to show to the user."""


def power(value: str) -> int:
    match = re.fullmatch(r"\s*(\d+(?:\.\d{1,6})?)\s*([KMGkmg]?)\s*", value)
    if not match:
        raise RuleError("Use a positive number such as 800M, 1.5G, or 200K.")
    result = Decimal(match[1]) * {"": 1, "K": 1000, "M": 1000000, "G": 1000000000}[match[2].upper()]
    if result != result.to_integral_value() or not 0 < result <= 9_000_000_000_000_000:
        raise RuleError("Strength must be a whole, positive value below 9 quadrillion.")
    return int(result)


def duration(value: str) -> int:
    match = re.fullmatch(r"([1-9]\d{0,6})([mhdw])", value.strip().lower())
    if not match:
        raise RuleError("Use a duration such as 30m, 24h, 7d, or 2w.")
    seconds = int(match[1]) * {"m": 60, "h": 3600, "d": 86400, "w": 604800}[match[2]]
    if seconds > 365 * 86400:
        raise RuleError("Maximum duration is one year.")
    return seconds


def local_timestamp(value: str, zone: str) -> float:
    """Explicit offsets resolve ambiguous DST times; impossible local times fail."""
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo:
            return dt.timestamp()
        tz = ZoneInfo(zone)
        first, second = dt.replace(tzinfo=tz, fold=0), dt.replace(tzinfo=tz, fold=1)
        if datetime.fromtimestamp(first.timestamp(), tz).replace(tzinfo=None) != dt:
            raise RuleError("That local time does not exist because of daylight saving time.")
        if first.utcoffset() != second.utcoffset():
            raise RuleError("That time occurs twice. Include its UTC offset, for example +02:00.")
        return first.timestamp()
    except (ValueError, KeyError) as exc:
        if isinstance(exc, RuleError):
            raise
        raise RuleError("Use YYYY-MM-DDTHH:MM, optionally with a UTC offset.") from exc


def week_key(timestamp=None):
    current = datetime.fromtimestamp(timestamp if timestamp is not None else time.time(), timezone.utc).date()
    return (current - timedelta(days=current.weekday())).isoformat()


class Service:
    def __init__(self, db: Database):
        self.db = db

    def member(self, guild, user):
        return self.db.one("SELECT * FROM members WHERE guild=? AND user=?", (str(guild), str(user)))

    def alliance(self, guild, name):
        rows = self.db.all("SELECT * FROM alliances WHERE guild=? AND (name=? COLLATE NOCASE OR tag=? COLLATE NOCASE)", (str(guild), name, name.strip("[]")))
        if len(rows) != 1:
            raise RuleError("No unique alliance matches. Use its exact name or tag.")
        return rows[0]

    def sync_members(self, guild, users):
        for user in set(str(u) for u in users):
            self.db.enqueue(guild, "sync_member", {"user": user}, time.time())

    def create_alliance(self, guild, user, name, tag, role=None, owned=True, roster=None):
        if not re.fullmatch(r"[\w -]{2,32}", name) or not re.fullmatch(r"[A-Za-z0-9]{1,5}", tag):
            raise RuleError("Alliance name: 2–32 letters/numbers/spaces/hyphens. Tag: 1–5 letters/numbers.")
        guild, user = str(guild), str(user)
        cfg = self.db.config(guild)[0].alliances
        roster = roster if roster is not None else [(user, 5)]
        if len(roster) > cfg.max_members or sum(rank == 5 for _, rank in roster) != 1:
            raise RuleError("The roster must fit the member limit and have exactly one R5.")
        with self.db.transaction():
            if self.db.one("SELECT COUNT(*) n FROM alliances WHERE guild=?", (guild,))["n"] >= cfg.max_alliances:
                raise RuleError("The alliance limit has been reached.")
            if self.db.one("SELECT id FROM alliances WHERE guild=? AND (name=? COLLATE NOCASE OR tag=? COLLATE NOCASE OR role=?)", (guild, name, tag, role)):
                raise RuleError("That alliance name, tag, or role is already in use.")
            for member, _ in roster:
                if self.member(guild, member):
                    raise RuleError("A roster member already belongs to another alliance.")
            aid = self.db.execute("INSERT INTO alliances(guild,name,tag,role,owned,created) VALUES(?,?,?,?,?,?)", (guild, name, tag.upper(), str(role) if role else None, int(owned), time.time())).lastrowid
            for member, rank in roster:
                self.db.execute("INSERT INTO members VALUES(?,?,?,?,?)", (guild, str(member), aid, rank, time.time()))
            self.sync_members(guild, [m for m, _ in roster])
            self.db.audit(guild, user, "alliances.created", {"alliance": aid})
        return aid

    def invite(self, guild, actor, target):
        guild, actor, target = map(str, (guild, actor, target))
        with self.db.transaction():
            membership = self.member(guild, actor)
            if not membership or membership["rank"] < 4:
                raise RuleError("Only an alliance R4 or R5 can invite members.")
            if self.member(guild, target):
                raise RuleError("This member is already in an alliance.")
            n = self.db.one("SELECT COUNT(*) n FROM members WHERE alliance=?", (membership["alliance"],))["n"]
            if n >= self.db.config(guild)[0].alliances.max_members:
                raise RuleError("The alliance is full.")
            self.db.execute("INSERT INTO members VALUES(?,?,?,?,?)", (guild, target, membership["alliance"], 1, time.time()))
            self.sync_members(guild, [target])
            self.db.audit(guild, actor, "alliances.invited", {"user": target})

    def change_rank(self, guild, actor, target, action):
        guild, actor, target = map(str, (guild, actor, target))
        with self.db.transaction():
            source, dest = self.member(guild, actor), self.member(guild, target)
            if not source or not dest or source["alliance"] != dest["alliance"] or source["rank"] < 4 or actor == target:
                raise RuleError("You must manage that member's alliance and cannot target yourself.")
            if action == "promote" and dest["rank"] == 1:
                self.db.execute("UPDATE members SET rank=4 WHERE guild=? AND user=?", (guild, target))
            elif action == "demote" and source["rank"] == 5 and dest["rank"] == 4:
                self.db.execute("UPDATE members SET rank=1 WHERE guild=? AND user=?", (guild, target))
            elif action == "transfer" and source["rank"] == 5:
                self.db.execute("UPDATE members SET rank=4 WHERE guild=? AND user=?", (guild, actor))
                self.db.execute("UPDATE members SET rank=5 WHERE guild=? AND user=?", (guild, target))
            elif action == "kick" and source["rank"] > dest["rank"]:
                self.db.execute("DELETE FROM members WHERE guild=? AND user=?", (guild, target))
            else:
                raise RuleError("Your rank does not permit this change, or the target already has that rank.")
            self.sync_members(guild, [actor, target])
            self.db.audit(guild, actor, "alliances." + action, {"user": target})

    def leave(self, guild, user, server_departure=False):
        guild, user = str(guild), str(user)
        with self.db.transaction():
            row = self.member(guild, user)
            if not row:
                return
            if row["rank"] == 5 and not server_departure:
                raise RuleError("Transfer leadership before leaving your alliance.")
            self.db.execute("DELETE FROM members WHERE guild=? AND user=?", (guild, user))
            if row["rank"] == 5:
                successor = self.db.one("SELECT * FROM members WHERE alliance=? ORDER BY rank DESC,joined,user LIMIT 1", (row["alliance"],))
                if successor:
                    self.db.execute("UPDATE members SET rank=5 WHERE guild=? AND user=?", (guild, successor["user"]))
                    self.sync_members(guild, [successor["user"]])
                else:
                    self.db.execute("UPDATE alliances SET active=0 WHERE id=?", (row["alliance"],))
            if not server_departure:
                self.sync_members(guild, [user])
            self.db.audit(guild, user, "alliances.left", {"alliance": row["alliance"]})

    def set_leader(self, guild, alliance, target, actor):
        guild, target = str(guild), str(target)
        with self.db.transaction():
            current = self.member(guild, target)
            if current and current["alliance"] != alliance:
                raise RuleError("Move this user out of their current alliance first; transfer any leadership.")
            previous = self.db.all("SELECT user FROM members WHERE guild=? AND alliance=? AND rank=5", (guild, alliance))
            if not current:
                count = self.db.one("SELECT COUNT(*) n FROM members WHERE alliance=? AND guild=?", (alliance, guild))["n"]
                if count >= self.db.config(guild)[0].alliances.max_members:
                    raise RuleError("The alliance is full.")
            if not self.db.one("SELECT id FROM alliances WHERE id=? AND guild=?", (alliance, guild)):
                raise RuleError("Alliance not found.")
            self.db.execute("UPDATE members SET rank=4 WHERE guild=? AND alliance=? AND rank=5", (guild, alliance))
            self.db.execute("INSERT INTO members VALUES(?,?,?,?,?) ON CONFLICT(guild,user) DO UPDATE SET rank=5", (guild, target, alliance, 5, time.time()))
            self.db.execute("UPDATE alliances SET active=1 WHERE id=?", (alliance,))
            self.sync_members(guild, [target] + [r["user"] for r in previous])
            self.db.audit(guild, actor, "alliances.leader_set", {"user": target, "alliance": alliance})

    def apc_set(self, guild, user, main, second, main_faction=None, second_faction=None):
        for faction in (main_faction, second_faction):
            if faction is not None and faction not in ("Fighters", "Shooters", "Riders"):
                raise RuleError("Choose Fighters, Shooters, or Riders.")
        self.db.execute("""INSERT INTO apc VALUES(?,?,?,?,?,?,?) ON CONFLICT(guild,user) DO UPDATE SET
            main=excluded.main,second=excluded.second,main_faction=COALESCE(excluded.main_faction,apc.main_faction),
            second_faction=COALESCE(excluded.second_faction,apc.second_faction),updated=excluded.updated""",
            (str(guild), str(user), power(main), power(second), main_faction, second_faction, time.time()))

    def panel(self, guild, panel_id):
        row = self.db.one("SELECT * FROM panels WHERE guild=? AND id=?", (str(guild), panel_id))
        if not row:
            raise RuleError("Panel not found in this server.")
        row["payload"] = json.loads(row["payload"])
        return row

    def vote(self, guild, panel_id, user, option):
        with self.db.transaction():
            row = self.panel(guild, panel_id)
            if row["kind"] not in ("polls", "giveaways") or row["closed"] or (row["closes"] and row["closes"] <= time.time()):
                raise RuleError("Voting has ended.")
            if option not in range(len(row["payload"].get("options", ["Enter"]))):
                raise RuleError("Unknown option.")
            existing = self.db.one("SELECT 1 FROM votes WHERE panel=? AND user=? AND option=?", (panel_id, str(user), option))
            if existing:
                self.db.execute("DELETE FROM votes WHERE panel=? AND user=? AND option=?", (panel_id, str(user), option))
            else:
                if not row["payload"].get("multiple", False):
                    self.db.execute("DELETE FROM votes WHERE panel=? AND user=?", (panel_id, str(user)))
                self.db.execute("INSERT INTO votes VALUES(?,?,?)", (panel_id, str(user), option))
        return not bool(existing)

    def draw(self, guild, panel_id, eligible, reroll=False):
        with self.db.transaction():
            row = self.panel(guild, panel_id)
            if row["kind"] != "giveaways":
                raise RuleError("This is not a giveaway.")
            if row["closed"] and not reroll:
                return row["payload"].get("winners", [])
            entrants = {r["user"] for r in self.db.all("SELECT DISTINCT user FROM votes WHERE panel=?", (panel_id,))}
            candidates = sorted(entrants & set(eligible))
            if reroll:
                candidates = [u for u in candidates if u not in row["payload"].get("winners", [])]
            count = min(row["payload"].get("winner_count", 1), len(candidates))
            winners = secrets.SystemRandom().sample(candidates, count)
            row["payload"]["winners"] = winners
            self.db.execute("UPDATE panels SET closed=1,payload=? WHERE id=?", (json.dumps(row["payload"]), panel_id))
        return winners

    def birthday(self, guild, user, month, day, year=None, moderator=False):
        try:
            date(year if year is not None else 2000, month, day)
        except ValueError as exc:
            raise RuleError("That is not a valid birthday.") from exc
        if year is not None and not 1900 <= year <= date.today().year:
            raise RuleError("Birth year is outside the supported range.")
        if not moderator and self.db.one("SELECT 1 FROM birthdays WHERE guild=? AND user=?", (str(guild), str(user))):
            raise RuleError("Your birthday is already set. Ask a moderator to correct it.")
        self.db.execute("INSERT INTO birthdays VALUES(?,?,?,?,?) ON CONFLICT(guild,user) DO UPDATE SET month=excluded.month,day=excluded.day,year=excluded.year", (str(guild), str(user), month, day, year))

    def schedule(self, guild, name, starts, actor, now=None, dedupe_key=None, repeat_every=None):
        config = self.db.config(guild)[0]
        definitions = [d for d in config.events.definitions if d.name.casefold() == name.casefold()]
        if not definitions:
            raise RuleError("Create this event definition in the dashboard first.")
        return self.create_event(guild, definitions[0], starts, actor, repeat_every, now, dedupe_key)

    def create_event(self, guild, definition, starts, actor, repeat_every=None, now=None, dedupe_key=None):
        """Create one event or a durable series; all scheduling commits atomically."""
        now = now if now is not None else time.time()
        config = self.db.config(guild)[0]
        definition = EventDefinition.model_validate(definition).model_copy(deep=True)
        channel = definition.channel_id or config.events.channel_id
        if not channel or not math.isfinite(starts) or starts <= now:
            raise RuleError("Choose a future time and configure an event channel.")
        interval = self.repeat_interval(repeat_every) if repeat_every is not None else (definition.repeat_hours or 0) * 3600
        # Legacy repeat_hours remains an input alias. Series own recurrence now.
        definition.repeat_hours = None
        definition.channel_id = channel
        with self.db.transaction():
            if dedupe_key:
                previous = self.db.ref(guild, dedupe_key)
                if previous:
                    return previous
            sid = None
            if interval:
                if self.db.one("SELECT COUNT(*) n FROM event_series WHERE guild=? AND status!='stopped'", (str(guild),))["n"] >= 50:
                    raise RuleError("Stop an existing series before adding more than 50 recurring schedules.")
                sid = self.db.execute("INSERT INTO event_series(guild,name,channel,definition,anchor,interval_seconds,next_start,created) VALUES(?,?,?,?,?,?,?,?)", (str(guild), definition.name, channel, definition.model_dump_json(), starts, interval, starts + interval, now)).lastrowid
            eid = self._event_occurrence(guild, definition, starts, now, sid)
            self.db.audit(guild, actor, "events.scheduled", {"event": eid})
            if dedupe_key:
                self.db.ref(guild, dedupe_key, eid)
        return eid

    @staticmethod
    def repeat_interval(value):
        if value.strip().lower() in ("off", "none", "once"):
            return 0
        seconds = duration(value)
        if seconds < 300:
            raise RuleError("Recurring events must be at least 5 minutes apart.")
        return seconds

    def _event_occurrence(self, guild, definition, starts, now, series_id=None):
        """Called inside a transaction. A unique index prevents duplicate cycles."""
        if series_id:
            row = self.db.one("SELECT * FROM events WHERE series_id=? AND starts=?", (series_id, starts))
            if row:
                if row["status"] == "suspended":
                    self._reactivate_occurrence(row, now)
                return row["id"]
        ends = starts + definition.duration_minutes * 60
        eid = self.db.execute("INSERT INTO events(guild,name,channel,starts,ends,definition,series_id) VALUES(?,?,?,?,?,?,?)", (str(guild), definition.name, definition.channel_id, starts, ends, definition.model_dump_json(), series_id)).lastrowid
        for offset in [0] + sorted(set(definition.reminder_minutes)):
            due = starts - offset * 60
            # A maintenance tick may be up to a minute late. Never backfill old reminders.
            if due >= now - 60:
                self.db.enqueue(guild, "event", {"event": eid, "phase": "start" if offset == 0 else "reminder"}, due, f"event:{eid}:{offset}")
        if definition.interval_minutes and starts + definition.interval_minutes * 60 < ends:
            due = starts + definition.interval_minutes * 60
            self.db.enqueue(guild, "event", {"event": eid, "phase": "interval"}, due, f"event:{eid}:interval:{int(due)}")
        self.db.enqueue(guild, "event", {"event": eid, "phase": "end"}, ends, f"event:{eid}:end")
        return eid

    def advance_series(self, guild, now=None):
        """Materialize upcoming cycles independently of message delivery and cleanup."""
        now = now if now is not None else time.time()
        if not self.db.config(guild)[0].events.enabled:
            return
        with self.db.transaction():
            for row in self.db.all("SELECT * FROM event_series WHERE guild=? AND status='active'", (str(guild),)):
                definition = EventDefinition.model_validate_json(row["definition"])
                interval, starts = row["interval_seconds"], row["next_start"]
                if starts <= now:
                    starts += (math.floor((now - starts) / interval) + 1) * interval
                # Enough lead time for reminders, and at least one upcoming cycle.
                horizon = now + max(interval, max(definition.reminder_minutes, default=0) * 60 + 60)
                for _ in range(100):  # Bound each maintenance transaction after long downtime.
                    if starts > horizon:
                        break
                    self._event_occurrence(guild, definition, starts, now, row["id"])
                    starts += interval
                self.db.execute("UPDATE event_series SET next_start=? WHERE id=?", (starts, row["id"]))

    def series_list(self, guild):
        rows = self.db.all("SELECT * FROM event_series WHERE guild=? AND status!='stopped' ORDER BY id DESC", (str(guild),))
        for row in rows:
            upcoming = self.db.one("SELECT MIN(starts) starts FROM events WHERE guild=? AND series_id=? AND status='scheduled' AND starts>?", (str(guild), row["id"], time.time()))
            candidate = upcoming["starts"] or row["next_start"]
            if candidate <= time.time():
                candidate += (math.floor((time.time() - candidate) / row["interval_seconds"]) + 1) * row["interval_seconds"]
            row["next_run"] = candidate if row["status"] == "active" else None
            row["definition"] = json.loads(row["definition"])
        return rows

    def control_series(self, guild, series_id, action, actor, now=None, repeat_every=None, starts=None, version=None):
        """Pause/stop cancel upcoming deliveries; active runs finish their cleanup."""
        now = now if now is not None else time.time()
        if action not in ("pause", "resume", "stop", "edit"):
            raise RuleError("Choose pause, resume, stop, or edit.")
        with self.db.transaction():
            row = self.db.one("SELECT * FROM event_series WHERE guild=? AND id=? AND status!='stopped'", (str(guild), series_id))
            if not row:
                raise RuleError("Recurring series not found in this server.")
            if version is not None and row["version"] != version:
                from .db import Conflict
                raise Conflict("This schedule changed in another session. Refresh before editing.")
            interval, anchor = row["interval_seconds"], row["anchor"]
            status = {"pause": "paused", "resume": "active", "stop": "stopped", "edit": row["status"]}[action]
            if action == "edit":
                if repeat_every is None:
                    raise RuleError("Provide a new repeat interval.")
                interval = self.repeat_interval(repeat_every)
                if not interval:
                    raise RuleError("Use Stop series to disable recurrence.")
                if starts is not None:
                    if not math.isfinite(starts) or starts <= now:
                        raise RuleError("Choose a future first run.")
                    anchor = starts
            if action == "resume" and row["status"] == "active":
                return
            # Do not cancel a run whose start has already arrived.
            self.db.execute("UPDATE events SET status=? WHERE guild=? AND series_id=? AND status IN ('scheduled','suspended') AND starts>?", ("cancelled" if action == "stop" else "suspended", str(guild), series_id, now))
            next_start = anchor
            if next_start <= now:
                next_start += (math.floor((now - next_start) / interval) + 1) * interval
            self.db.execute("UPDATE event_series SET status=?,interval_seconds=?,anchor=?,next_start=?,version=version+1 WHERE id=?", (status, interval, anchor, next_start, series_id))
            if status == "active":
                self._event_occurrence(guild, EventDefinition.model_validate_json(row["definition"]), next_start, now, series_id)
                self.db.execute("UPDATE event_series SET next_start=? WHERE id=?", (next_start + interval, series_id))
            self.db.audit(guild, actor, "events.series_" + action, {"series": series_id})

    def _reactivate_occurrence(self, row, now):
        self.db.execute("UPDATE events SET status='scheduled' WHERE id=?", (row["id"],))
        # Completed future jobs (e.g. handled while paused) must become deliverable again.
        self.db.execute("UPDATE jobs SET state='pending',attempts=0,lease=NULL,error=NULL WHERE guild=? AND kind='event' AND json_extract(payload,'$.event')=? AND due>=?", (row["guild"], row["id"], now))

    def cancel_event(self, guild, event_id, actor):
        count = self.db.execute("UPDATE events SET status='cancelled' WHERE guild=? AND id=? AND status IN ('scheduled','started')", (str(guild), event_id)).rowcount
        if not count:
            raise RuleError("That event is not active in this server.")
        self.db.audit(guild, actor, "events.cancelled", {"event": event_id})
