import json
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from sentinel.config import GuildConfig, EventDefinition
from sentinel.db import Database
from sentinel.domain import Service
from sentinel.jobs import execute_event, execute_job, maintain


class JobsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = Database(":memory:")
        self.service = Service(self.db)
        self.guild = SimpleNamespace(id=1, get_channel=lambda _: None)
        self.bot = SimpleNamespace(db=self.db, service=self.service, cfg=lambda g:self.db.config(g)[0])
        cfg = GuildConfig()
        cfg.events.enabled = True
        cfg.events.channel_id = "20"
        cfg.events.definitions = [EventDefinition(name="Weekly", repeat_hours=168)]
        self.db.save_config("1", cfg, 0, "10")

    async def asyncTearDown(self):
        self.db.close()

    async def test_recurring_schedule_deduplicates_transactionally(self):
        starts=time.time()+3600
        a=self.service.schedule("1","Weekly",starts,"scheduler",dedupe_key="repeat:old")
        b=self.service.schedule("1","Weekly",starts,"scheduler",dedupe_key="repeat:old")
        self.assertEqual(a,b)
        self.assertEqual(self.db.one("SELECT COUNT(*) n FROM events")["n"],1)

    async def test_event_cleanup_failure_does_not_mark_end_complete(self):
        eid=self.service.schedule("1","Weekly",time.time()+3600,"10")
        self.db.execute("UPDATE events SET status='started' WHERE id=?",(eid,))
        self.db.ref("1",f"event:{eid}",{"channel":"20","message":"30"})
        delete=AsyncMock(side_effect=RuntimeError("Discord unavailable"))
        self.guild.get_channel=lambda _:SimpleNamespace(get_partial_message=lambda _:SimpleNamespace(delete=delete))
        job={"payload":{"event":eid,"phase":"end"},"due":time.time()}
        with self.assertRaises(RuntimeError):await execute_event(self.bot,self.guild,job)
        self.assertEqual(self.db.one("SELECT status FROM events WHERE id=?",(eid,))["status"],"started")
        self.service.advance_series("1",time.time()+3600)
        self.assertEqual(self.db.one("SELECT COUNT(*) n FROM events")["n"],2)
        delete.side_effect=None
        await execute_event(self.bot,self.guild,job)
        self.assertEqual(self.db.one("SELECT status FROM events WHERE id=?",(eid,))["status"],"ended")
        self.assertEqual(self.db.one("SELECT COUNT(*) n FROM jobs WHERE kind='repeat_event'")["n"],0)

    async def test_cancelled_event_never_sends(self):
        eid=self.service.schedule("1","Weekly",time.time()+3600,"10")
        self.db.execute("UPDATE events SET status='cancelled' WHERE id=?",(eid,))
        self.bot.channel=lambda *_:self.fail("Cancelled event attempted delivery")
        await execute_event(self.bot,self.guild,{"payload":{"event":eid,"phase":"start"},"due":time.time()})

    async def test_restart_daily_jobs_do_not_duplicate(self):
        cfg,version=self.db.config("1")
        cfg.birthdays.enabled=True
        cfg.birthdays.channel_id="20"
        cfg.birthdays.send_hour=0
        self.db.save_config("1",cfg,version,"10")
        from datetime import datetime
        from zoneinfo import ZoneInfo
        now=datetime.now(ZoneInfo(cfg.timezone))
        self.service.birthday("1","10",now.month,now.day)
        await maintain(self.bot,self.guild)
        await maintain(self.bot,self.guild)
        self.assertEqual(self.db.one("SELECT COUNT(*) n FROM jobs WHERE kind='birthday'")["n"],1)

    async def test_legacy_repeat_job_adopts_series_without_duplicates(self):
        eid=self.service.schedule('1','Weekly',time.time()+3600,'10',repeat_every='off')
        legacy=EventDefinition(name='Weekly',repeat_hours=168).model_dump_json()
        self.db.execute("UPDATE events SET definition=?,status='ended' WHERE id=?",(legacy,eid))
        job={'kind':'repeat_event','payload':{'event':eid}}
        await execute_job(self.bot,self.guild,job)
        await execute_job(self.bot,self.guild,job)
        self.assertEqual(self.db.one('SELECT COUNT(*) n FROM events')['n'],2)
        self.assertEqual(self.db.one('SELECT COUNT(*) n FROM event_series')['n'],1)

    async def test_disabled_reminder_does_not_cancel_a_future_occurrence(self):
        eid=self.service.schedule('1','Weekly',time.time()+3600,'10')
        cfg,v=self.db.config('1');cfg.events.enabled=False
        self.db.save_config('1',cfg,v,'10')
        self.bot.channel=lambda *_:self.fail('Disabled event attempted delivery')
        await execute_event(self.bot,self.guild,{'payload':{'event':eid,'phase':'reminder'},'due':time.time()})
        self.assertEqual(self.db.one('SELECT status FROM events')['status'],'scheduled')

    async def test_expired_start_is_skipped(self):
        eid=self.service.schedule('1','Weekly',time.time()+3600,'10')
        row=self.db.one('SELECT * FROM events WHERE id=?',(eid,))
        self.bot.channel=lambda *_:self.fail('Expired event attempted delivery')
        with patch('sentinel.jobs.time.time',return_value=row['ends']+1):
            await execute_event(self.bot,self.guild,{'payload':{'event':eid,'phase':'start'},'due':row['starts']})
        self.assertEqual(self.db.one('SELECT status FROM events')['status'],'missed')

    async def test_two_consecutive_cycles_deliver_and_clean_up(self):
        import discord
        start=time.time()+600
        definition=EventDefinition(name='Rally',channel_id='20',reminder_minutes=[],duration_minutes=1)
        eid=self.service.create_event('1',definition,start,'10','5m')
        send=AsyncMock(return_value=SimpleNamespace(id=30))
        self.bot.channel=lambda *_:SimpleNamespace(send=send)
        self.bot.template=lambda *_:{'embed':discord.Embed(title='Rally')}
        for cycle in (start,start+300):
            self.service.advance_series('1',cycle)
            row=self.db.one('SELECT * FROM events WHERE starts=?',(cycle,))
            for phase,now in [('start',cycle),('end',cycle+60)]:
                with patch('sentinel.jobs.time.time',return_value=now):
                    await execute_event(self.bot,self.guild,{'payload':{'event':row['id'],'phase':phase},'due':now})
            self.assertEqual(self.db.one('SELECT status FROM events WHERE id=?',(row['id'],))['status'],'ended')
        self.assertEqual(send.await_count,2)
        self.assertEqual(self.db.one('SELECT status FROM event_series')['status'],'active')
        self.assertEqual(self.db.one("SELECT COUNT(*) n FROM events WHERE status='scheduled'")['n'],1)


if __name__=='__main__':unittest.main()
