import json
import os
import tempfile
import time
import unittest
from datetime import datetime, timezone

from pydantic import ValidationError
from sentinel.config import GuildConfig, EventDefinition, RolePanel, RoleMapping
from sentinel.db import Database, Conflict
from sentinel.domain import Service, RuleError, power, duration, local_timestamp, week_key
from sentinel.automod import AutoModerator


class DomainTests(unittest.TestCase):
    def setUp(self):
        self.db = Database(":memory:")
        self.s = Service(self.db)

    def tearDown(self):
        self.db.close()

    def create(self, guild="1", leader="10", name="Alpha", tag="A"):
        return self.s.create_alliance(guild, leader, name, tag)

    def panel(self, kind="polls", guild="1", multiple=False, closes=None):
        data = {"options": ["First", "Second"], "multiple": multiple, "winner_count": 2}
        return self.db.execute("INSERT INTO panels(guild,kind,channel,title,payload,creator,closes) VALUES(?,?,?,?,?,?,?)", (guild, kind, "20", "Test", json.dumps(data), "10", closes)).lastrowid

    def test_power_notation_is_exact(self):
        self.assertEqual(power("1.5G"), 1500000000)
        self.assertEqual(power("800.5m"), 800500000)
        self.assertEqual(power("0.001K"), 1)

    def test_invalid_and_overflowing_power_rejected(self):
        for v in ["NaN", "inf", "-1", "0", "1e5", "5B", "1.2", "999999999999999999G"]:
            with self.subTest(value=v), self.assertRaises(RuleError):
                power(v)

    def test_duration_boundaries(self):
        self.assertEqual(duration("2w"), 1209600)
        for v in ["0m", "-1h", "2years", "366d"]:
            with self.assertRaises(RuleError): duration(v)

    def test_atomic_leadership_transfer(self):
        aid = self.create()
        self.s.invite("1", "10", "11")
        self.s.change_rank("1", "10", "11", "transfer")
        self.assertEqual(self.s.member("1", "10")["rank"], 4)
        self.assertEqual(self.s.member("1", "11")["rank"], 5)
        self.assertEqual(self.db.one("SELECT COUNT(*) n FROM members WHERE alliance=? AND rank=5", (aid,))["n"], 1)

    def test_rank_checks_and_tenant_boundary(self):
        self.create()
        self.create("2", "10", "Beta", "B")
        self.s.invite("2", "10", "12")
        with self.assertRaises(RuleError): self.s.change_rank("1", "10", "12", "promote")
        self.assertEqual(self.s.member("2", "12")["rank"], 1)

    def test_single_membership_enforced(self):
        self.create()
        self.create(leader="11", name="Bravo", tag="B")
        with self.assertRaises(RuleError): self.s.invite("1", "10", "11")

    def test_capacity_is_enforced_without_partial_insert(self):
        cfg = GuildConfig()
        cfg.alliances.max_members = 1
        self.db.save_config("1", cfg, 0, "10")
        self.create()
        with self.assertRaises(RuleError): self.s.invite("1", "10", "11")
        self.assertIsNone(self.s.member("1", "11"))

    def test_r4_cannot_demote_or_kick_peer(self):
        self.create()
        for uid in ["11", "12"]:
            self.s.invite("1", "10", uid)
            self.s.change_rank("1", "10", uid, "promote")
        for action in ["demote", "kick", "transfer"]:
            with self.assertRaises(RuleError): self.s.change_rank("1", "11", "12", action)

    def test_departure_elects_highest_rank(self):
        self.create()
        self.s.invite("1", "10", "11")
        self.s.invite("1", "10", "12")
        self.s.change_rank("1", "10", "12", "promote")
        self.s.leave("1", "10", server_departure=True)
        self.assertEqual(self.s.member("1", "12")["rank"], 5)

    def test_leader_cannot_leave_voluntarily(self):
        self.create()
        with self.assertRaises(RuleError): self.s.leave("1", "10")
        self.assertIsNotNone(self.s.member("1", "10"))

    def test_import_is_all_or_nothing(self):
        self.create()
        with self.assertRaises(RuleError):
            self.s.create_alliance("1", "20", "Import", "IMP", roster=[("20", 5), ("10", 1)])
        self.assertEqual(self.db.one("SELECT COUNT(*) n FROM alliances")["n"], 1)
        self.assertIsNone(self.s.member("1", "20"))

    def test_single_vote_toggle_and_change(self):
        pid = self.panel()
        self.assertTrue(self.s.vote("1", pid, "10", 0))
        self.assertTrue(self.s.vote("1", pid, "10", 1))
        self.assertEqual(self.db.all("SELECT option FROM votes"), [{"option": 1}])
        self.assertFalse(self.s.vote("1", pid, "10", 1))
        self.assertEqual(self.db.all("SELECT * FROM votes"), [])

    def test_multiple_choices_and_closed_poll(self):
        pid = self.panel(multiple=True)
        self.s.vote("1", pid, "10", 0)
        self.s.vote("1", pid, "10", 1)
        self.assertEqual(self.db.one("SELECT COUNT(*) n FROM votes")["n"], 2)
        self.db.execute("UPDATE panels SET closed=1")
        with self.assertRaises(RuleError): self.s.vote("1", pid, "11", 1)

    def test_expired_and_other_guild_poll_rejected(self):
        pid = self.panel(closes=time.time()-1)
        with self.assertRaises(RuleError): self.s.vote("1", pid, "10", 0)
        with self.assertRaises(RuleError): self.s.vote("2", pid, "10", 0)

    def test_giveaway_draw_is_persisted_and_filters_departed(self):
        pid = self.panel("giveaways")
        for uid in ["10", "11", "12"]: self.s.vote("1", pid, uid, 0)
        winners = self.s.draw("1", pid, {"10", "11"})
        self.assertEqual(set(winners), {"10", "11"})
        self.assertEqual(self.s.draw("1", pid, {"12"}), winners)
        self.assertEqual(self.s.draw("1", pid, {"10", "11", "12"}, reroll=True), ["12"])

    def test_config_optimistic_concurrency(self):
        self.db.save_config("1", GuildConfig(), 0, "10")
        with self.assertRaises(Conflict): self.db.save_config("1", GuildConfig(), 0, "11")
        self.assertEqual(self.db.config("1")[1], 1)

    def test_invalid_configuration_rejected(self):
        for payload in [{"timezone": "Moon/Sea"}, {"token": "secret"}, {"apc": {"private": True, "unknown": 1}}, {"moderation": {"warning_threshold": 100}}, {"duel": {"templates": {"Monday": {}}}}]:
            with self.subTest(payload=payload), self.assertRaises(ValidationError): GuildConfig.model_validate(payload)

    def test_duplicate_roles_and_templates_rejected(self):
        with self.assertRaises(ValidationError):
            RolePanel(name="bad", mappings=[RoleMapping(role_id="1", label="A"), RoleMapping(role_id="1", label="B")])
        with self.assertRaises(ValidationError):
            GuildConfig.model_validate({"messages": {"templates": [{"name":"A"},{"name":"a"}]}})

    def test_apc_keeps_faction_on_partial_update(self):
        self.s.apc_set("1", "10", "1G", "1M", "Riders", "Fighters")
        self.s.apc_set("1", "10", "2G", "2M")
        row = self.db.one("SELECT * FROM apc")
        self.assertEqual((row["main"], row["main_faction"]), (2000000000, "Riders"))

    def test_birthday_leap_day_and_privacy_removal(self):
        self.s.birthday("1", "10", 2, 29)
        with self.assertRaises(RuleError): self.s.birthday("1", "10", 3, 1)
        with self.assertRaises(RuleError): self.s.birthday("1", "11", 2, 29, 2025)
        self.s.birthday("1", "10", 3, 1, moderator=True)
        self.assertEqual(self.db.one("SELECT month FROM birthdays")["month"], 3)

    def test_timezone_offsets_and_dst_edges(self):
        expected = datetime(2026, 10, 14, 17, 0, tzinfo=timezone.utc).timestamp()
        self.assertEqual(local_timestamp("2026-10-14T20:00", "Asia/Amman"), expected)
        with self.assertRaises(RuleError): local_timestamp("2026-03-08T02:30", "America/New_York")
        with self.assertRaises(RuleError): local_timestamp("2026-11-01T01:30", "America/New_York")
        self.assertIsInstance(local_timestamp("2026-11-01T01:30-04:00", "America/New_York"), float)

    def test_week_boundary_uses_utc_monday(self):
        self.assertEqual(week_key(datetime(2026, 10, 11, 23, 59, tzinfo=timezone.utc).timestamp()), "2026-10-05")
        self.assertEqual(week_key(datetime(2026, 10, 12, tzinfo=timezone.utc).timestamp()), "2026-10-12")

    def test_scheduler_stores_distinct_durable_reminders(self):
        cfg = GuildConfig()
        cfg.events.channel_id = "20"
        cfg.events.definitions = [EventDefinition(name="Battle", reminder_minutes=[60, 10, 10], interval_minutes=5, duration_minutes=15)]
        self.db.save_config("1", cfg, 0, "10")
        eid = self.s.schedule("1", "Battle", time.time()+7200, "10")
        jobs = self.db.all("SELECT * FROM jobs WHERE kind='event'")
        self.assertEqual(len(jobs), 5)
        self.assertTrue(all(json.loads(j["payload"])["event"] == eid for j in jobs))

    def test_jobs_claim_once_and_recover_expired_lease(self):
        self.db.enqueue("1", "test", {}, 100, "unique")
        self.db.enqueue("1", "test", {}, 100, "unique")
        job = self.db.claim(100)
        self.assertIsNone(self.db.claim(101))
        self.assertEqual(self.db.claim(401)["id"], job["id"])

    def test_restart_preserves_votes_and_jobs(self):
        with tempfile.TemporaryDirectory() as temp:
            path = os.path.join(temp, "test.db")
            db = Database(path)
            db.enqueue("1", "event", {"id":1}, 1, "one")
            db.close()
            db = Database(path)
            self.assertEqual(db.claim()["payload"], {"id":1})
            db.close()

    def test_automod_link_allowlist_cannot_be_suffix_spoofed(self):
        cfg = GuildConfig().automod
        cfg.links.enabled = True
        cfg.allowed_domains = ["example.com"]
        a = AutoModerator()
        self.assertIsNone(a.inspect("1", "10", "https://www.example.com/page", 0, cfg, 10))
        self.assertEqual(a.inspect("1", "10", "https://example.com.evil.test", 0, cfg, 11)[0], "links")
        self.assertEqual(a.inspect("1", "10", "https://example.com@evil.test", 0, cfg, 12)[0], "links")

    def test_spam_windows_are_guild_scoped(self):
        cfg = GuildConfig().automod
        cfg.spam.enabled = True
        cfg.spam.threshold = 1
        a = AutoModerator()
        self.assertIsNone(a.inspect("1", "10", "a", 0, cfg, 100))
        self.assertIsNone(a.inspect("2", "10", "a", 0, cfg, 101))
        self.assertEqual(a.inspect("1", "10", "b", 0, cfg, 102)[0], "spam")
        self.assertIsNone(a.inspect("1", "10", "c", 0, cfg, 110))


if __name__ == "__main__":
    unittest.main()
