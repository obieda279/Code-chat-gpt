import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sentinel.config import EventDefinition, GuildConfig
from sentinel.db import Conflict, Database
from sentinel.domain import RuleError, Service


class RecurringEventTests(unittest.TestCase):
    def setUp(self):
        self.db = Database(':memory:')
        self.service = Service(self.db)
        cfg = GuildConfig()
        cfg.events.enabled = True
        cfg.events.channel_id = '20'
        cfg.events.definitions = [EventDefinition(name='Battle', reminder_minutes=[60, 10])]
        self.db.save_config('1', cfg, 0, 'admin')
        self.now = 1_800_000_000

    def tearDown(self):
        self.db.close()

    def create(self, every='2h', start=None, **kwargs):
        eid = self.service.schedule('1', 'Battle', start or self.now+7200, 'admin', now=self.now, repeat_every=every, **kwargs)
        return self.db.one('SELECT * FROM events WHERE id=?', (eid,))

    def test_custom_intervals_and_one_time_override(self):
        for value, seconds in [('5m',300),('12h',43200),('3d',259200),('2w',1209600)]:
            row = self.create(value)
            series = self.db.one('SELECT * FROM event_series WHERE id=?', (row['series_id'],))
            self.assertEqual(series['interval_seconds'], seconds)
        self.assertIsNone(self.create('off')['series_id'])
        for invalid in ['0m', '1m', '-2h', '1.5d', '366d', 'monthly']:
            with self.assertRaises(RuleError): self.create(invalid)

    def test_series_survives_reopen_without_duplicate_cycles(self):
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp)/'state.db')
            self.db.conn.backup(sqlite3.connect(path))
            first = Database(path)
            s = Service(first)
            eid = s.schedule('1','Battle',self.now+600,'admin',now=self.now,repeat_every='1h')
            first.close()
            reopened = Database(path)
            try:
                Service(reopened).advance_series('1',self.now+600)
                Service(reopened).advance_series('1',self.now+600)
                rows = reopened.all('SELECT * FROM events ORDER BY starts')
                self.assertEqual(len(rows),2)
                self.assertEqual(rows[1]['starts']-rows[0]['starts'],3600)
                self.assertEqual(rows[0]['id'],eid)
                self.assertEqual(reopened.one('SELECT COUNT(*) n FROM jobs WHERE key LIKE ?', (f"event:{rows[1]['id']}:%",))['n'],4)
            finally: reopened.close()

    def test_long_reminders_and_overlapping_events_are_scheduled_ahead(self):
        cfg,version = self.db.config('1')
        cfg.events.definitions[0].duration_minutes=180
        self.db.save_config('1',cfg,version,'admin')
        first=self.create('30m', self.now+600)
        self.service.advance_series('1',self.now)
        rows=self.db.all('SELECT * FROM events ORDER BY starts')
        self.assertEqual(len(rows),2)
        self.assertEqual(rows[1]['starts'], first['starts']+1800)
        self.assertLess(rows[1]['starts'],first['ends'])
        # The next event exists before any end/delivery job has run.
        self.assertEqual(first['status'],'scheduled')
        self.service.advance_series('1',self.now+600)
        third=self.db.all('SELECT * FROM events ORDER BY starts')[-1]
        self.assertEqual(third['starts'],self.now+4200)
        self.assertIsNotNone(self.db.one('SELECT id FROM jobs WHERE key=?',(f"event:{third['id']}:60",)))

    def test_downtime_skips_missed_cycles_and_preserves_anchor(self):
        first=self.create('2h')
        now=first['starts']+10*7200+1800
        self.service.advance_series('1',now)
        rows=self.db.all('SELECT * FROM events ORDER BY starts')
        self.assertEqual(len(rows),2)
        self.assertEqual(rows[-1]['starts'],first['starts']+11*7200)
        self.assertTrue(all(r['starts']>now for r in rows[1:]))

    def test_pause_resume_and_stop_suppress_future_occurrences(self):
        first=self.create('2h')
        sid=first['series_id']
        self.service.control_series('1',sid,'pause','admin',now=self.now+1)
        self.service.advance_series('1',self.now+86400)
        self.assertEqual(self.db.one('SELECT status FROM events')['status'],'suspended')
        self.service.control_series('1',sid,'resume','admin',now=self.now+100)
        self.assertEqual(self.db.one('SELECT status FROM events')['status'],'scheduled')
        self.service.control_series('1',sid,'stop','admin',now=self.now+101)
        self.service.advance_series('1',self.now+172800)
        self.assertEqual(self.db.one('SELECT status FROM events')['status'],'cancelled')
        self.assertEqual(self.db.one('SELECT COUNT(*) n FROM events')['n'],1)
        with self.assertRaises(RuleError):self.service.control_series('1',sid,'resume','admin',now=self.now+200)

    def test_resume_reactivates_all_future_suspended_cycles(self):
        first=self.create('5m',self.now+300)
        self.service.advance_series('1',self.now)
        total=self.db.one('SELECT COUNT(*) n FROM events')['n']
        self.assertGreater(total,2)
        self.service.control_series('1',first['series_id'],'pause','admin',now=self.now)
        self.service.control_series('1',first['series_id'],'resume','admin',now=self.now+10)
        self.service.advance_series('1',self.now+10)
        self.assertEqual(self.db.one("SELECT COUNT(*) n FROM events WHERE status='scheduled'")['n'],total)

    def test_edit_timing_replaces_future_only_and_checks_version(self):
        first=self.create('2h',self.now+600)
        sid=first['series_id']
        self.service.advance_series('1',self.now+600)
        self.db.execute("UPDATE events SET status='started' WHERE id=?",(first['id'],))
        self.service.control_series('1',sid,'edit','admin',now=self.now+601,repeat_every='3h',starts=self.now+1800,version=1)
        rows=self.db.all("SELECT * FROM events WHERE status IN ('started','scheduled') ORDER BY starts")
        self.assertEqual([r['starts'] for r in rows],[self.now+600,self.now+1800])
        self.assertEqual(rows[0]['status'],'started')
        with self.assertRaises(Conflict):self.service.control_series('1',sid,'pause','admin',version=1)

    def test_cancel_one_occurrence_does_not_stop_series(self):
        first=self.create('2h')
        self.service.cancel_event('1',first['id'],'admin')
        self.service.advance_series('1',first['starts'])
        rows=self.db.all('SELECT * FROM events ORDER BY starts')
        self.assertEqual(rows[0]['status'],'cancelled')
        self.assertEqual(rows[1]['status'],'scheduled')
        self.assertEqual(rows[1]['starts'],first['starts']+7200)

    def test_guild_isolation_and_module_disable(self):
        first=self.create('2h')
        for action in ('pause','resume','stop','edit'):
            with self.assertRaises(RuleError):self.service.control_series('2',first['series_id'],action,'admin',repeat_every='4h')
        with self.assertRaises(RuleError):self.service.cancel_event('2',first['id'],'admin')
        self.assertEqual(self.service.series_list('2'),[])
        cfg,v=self.db.config('1');cfg.events.enabled=False
        self.db.save_config('1',cfg,v,'admin')
        self.service.advance_series('1',self.now+100000)
        self.assertEqual(self.db.one('SELECT COUNT(*) n FROM events')['n'],1)
        cfg,v=self.db.config('1');cfg.events.enabled=True
        self.db.save_config('1',cfg,v,'admin')
        self.service.advance_series('1',self.now+100000)
        self.assertGreater(self.db.one('SELECT MAX(starts) t FROM events')['t'],self.now+100000)

    def test_bad_edit_rolls_back_without_cancelling_events(self):
        first=self.create('2h')
        with self.assertRaises(RuleError):self.service.control_series('1',first['series_id'],'edit','admin',now=self.now,repeat_every='2m')
        self.assertEqual(self.db.one('SELECT status FROM events')['status'],'scheduled')
        self.assertEqual(self.db.one('SELECT version FROM event_series')['version'],1)

    def test_v1_database_upgrade_preserves_existing_event_and_jobs(self):
        with tempfile.TemporaryDirectory() as temp:
            path=str(Path(temp)/'old.db');old=sqlite3.connect(path)
            old.executescript("CREATE TABLE events (id INTEGER PRIMARY KEY,guild TEXT,name TEXT,channel TEXT,starts REAL,ends REAL,status TEXT,definition TEXT); PRAGMA user_version=1;")
            old.execute('INSERT INTO events VALUES(1,?,?,?,?,?,?,?)',('1','Legacy','20',self.now+600,self.now+900,'scheduled',EventDefinition(name='Legacy',repeat_hours=24).model_dump_json()))
            old.commit();old.close()
            upgraded=Database(path)
            try:
                self.assertEqual(upgraded.one('SELECT * FROM events')['name'],'Legacy')
                self.assertIsNone(upgraded.one('SELECT * FROM events')['series_id'])
                self.assertEqual(upgraded.execute('PRAGMA user_version').fetchone()[0],2)
            finally:upgraded.close()

    def test_series_definition_is_a_snapshot_and_legacy_hours_are_supported(self):
        cfg,v=self.db.config('1');cfg.events.definitions[0].repeat_hours=24
        self.db.save_config('1',cfg,v,'admin')
        first=self.create(every=None)
        self.assertIsNotNone(first['series_id'])
        cfg,v=self.db.config('1');cfg.events.definitions=[]
        self.db.save_config('1',cfg,v,'admin')
        self.service.advance_series('1',first['starts'])
        self.assertEqual(self.db.one('SELECT COUNT(*) n FROM events')['n'],2)
        self.assertEqual(json.loads(first['definition'])['repeat_hours'],None)


if __name__=='__main__':unittest.main()
