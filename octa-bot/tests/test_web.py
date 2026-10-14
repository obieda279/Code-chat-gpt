import hashlib
import json
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from sentinel.db import Database
from sentinel.config import GuildConfig
from sentinel.settings import Settings
from sentinel.web import Dashboard, DEMO_GUILD, DEMO_USER


class WebTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = Database(":memory:")
        self.settings = Settings(demo=True)
        self.dashboard = Dashboard(self.db, self.settings)
        server = TestServer(self.dashboard.app())
        self.client = TestClient(server, cookie_jar=aiohttp.CookieJar(unsafe=True))
        await self.client.start_server()
        self.settings.port = server.port
        self.settings.public_url = str(server.make_url("")).rstrip("/")

    async def asyncTearDown(self):
        await self.client.close()
        self.db.close()

    async def login(self):
        response = await self.client.get("/auth/login")
        self.assertEqual(response.status, 200)
        response = await self.client.get("/api/me")
        self.me = await response.json()
        return {"Origin": self.settings.public_url, "X-CSRF-Token": self.me["csrf"]}

    async def test_unauthenticated_api_is_denied(self):
        response = await self.client.get("/api/guilds")
        self.assertEqual(response.status, 401)

    async def test_csrf_and_origin_are_both_required(self):
        headers = await self.login()
        payload = {"config": GuildConfig().model_dump(), "version": 0}
        for h in [{}, {"Origin": self.settings.public_url}, {"X-CSRF-Token": headers["X-CSRF-Token"], "Origin": "https://evil.test"}]:
            response = await self.client.put(f"/api/guilds/{DEMO_GUILD}/config", json=payload, headers=h)
            self.assertEqual(response.status, 403)
        self.assertEqual(self.db.config(DEMO_GUILD)[1], 0)

    async def test_config_roundtrip_and_stale_version(self):
        headers = await self.login()
        cfg = GuildConfig(timezone="Asia/Amman").model_dump()
        response = await self.client.put(f"/api/guilds/{DEMO_GUILD}/config", json={"config": cfg, "version": 0}, headers=headers)
        self.assertEqual(response.status, 200)
        response = await self.client.put(f"/api/guilds/{DEMO_GUILD}/config", json={"config": cfg, "version": 0}, headers=headers)
        self.assertEqual(response.status, 409)

    async def test_cross_server_access_and_write_are_denied(self):
        headers = await self.login()
        for endpoint in ["config", "overview"]:
            response = await self.client.get("/api/guilds/999/"+endpoint)
            self.assertEqual(response.status, 404)
        response = await self.client.put("/api/guilds/999/config", json={"config": {}, "version": 0}, headers=headers)
        self.assertEqual(response.status, 404)

    async def test_security_headers_and_static_path_allowlist(self):
        response = await self.client.get("/")
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")
        self.assertIn("script-src 'self'", response.headers["Content-Security-Policy"])
        self.assertNotIn("unsafe-inline", response.headers["Content-Security-Policy"])
        response = await self.client.get("/static/settings.py")
        self.assertEqual(response.status, 404)

    async def test_logout_invalidates_server_side_session(self):
        headers = await self.login()
        cookie = self.client.session.cookie_jar.filter_cookies(self.client.make_url("/"))["sentinel_session"].value
        response = await self.client.post("/auth/logout", json={}, headers=headers)
        self.assertEqual(response.status, 200)
        self.assertIsNone(self.db.one("SELECT * FROM sessions WHERE id=?", (hashlib.sha256(cookie.encode()).hexdigest(),)))

    async def test_expired_session_rejected(self):
        await self.login()
        self.db.execute("UPDATE sessions SET expires=?", (time.time()-1,))
        self.assertEqual((await self.client.get("/api/me")).status, 401)

    async def test_oauth_state_is_browser_bound_and_single_use(self):
        self.settings.demo = False
        response = await self.client.get("/auth/login", allow_redirects=False)
        self.assertEqual(response.status, 302)
        self.assertIn("scope=identify", response.headers["Location"])
        state = next(iter(self.dashboard.states))
        self.client.session.cookie_jar.clear()
        response = await self.client.get(f"/auth/callback?state={state}&code=fake")
        self.assertEqual(response.status, 403)
        self.assertNotIn(state, self.dashboard.states)

    async def test_revoked_administrator_cannot_write(self):
        headers = await self.login()
        self.settings.demo = False
        member = SimpleNamespace(guild_permissions=SimpleNamespace(administrator=False))
        guild = SimpleNamespace(fetch_member=AsyncMock(return_value=member))
        self.dashboard.bot = SimpleNamespace(get_guild=lambda _: guild)
        response = await self.client.put(f"/api/guilds/{DEMO_GUILD}/config", json={"config": {}, "version": 0}, headers=headers)
        self.assertEqual(response.status, 403)
        guild.fetch_member.assert_awaited_once()

    async def test_delegated_user_can_read_but_cannot_self_promote(self):
        headers = await self.login()
        cfg = GuildConfig(dashboard_users=[DEMO_USER])
        self.db.save_config(DEMO_GUILD, cfg, 0, "owner")
        self.settings.demo = False
        member = SimpleNamespace(guild_permissions=SimpleNamespace(administrator=False))
        guild = SimpleNamespace(fetch_member=AsyncMock(return_value=member), roles=[], channels=[])
        self.dashboard.bot = SimpleNamespace(get_guild=lambda _: guild)
        response = await self.client.get(f"/api/guilds/{DEMO_GUILD}/config")
        self.assertEqual(response.status, 200)
        self.assertFalse((await response.json())["can_edit"])
        response = await self.client.put(f"/api/guilds/{DEMO_GUILD}/config", json={"config": {}, "version": 1}, headers=headers)
        self.assertEqual(response.status, 403)

    async def test_host_header_rebinding_rejected(self):
        response = await self.client.get("/", headers={"Host": "evil.test"})
        self.assertEqual(response.status, 403)

    async def test_bot_authority_role_cannot_be_self_assigned(self):
        headers=await self.login()
        cfg=GuildConfig().model_dump()
        cfg['moderation']['roles']=['123']
        cfg['welcome']['auto_roles']=['123']
        response=await self.client.put(f"/api/guilds/{DEMO_GUILD}/config",json={'config':cfg,'version':0},headers=headers)
        self.assertEqual(response.status,400)
        self.assertEqual(self.db.config(DEMO_GUILD)[1],0)

    async def event_setup(self):
        headers = await self.login()
        cfg = GuildConfig(timezone="Asia/Amman")
        cfg.events.enabled = True
        self.db.save_config(DEMO_GUILD, cfg, 0, "owner")
        payload = {"when": "2090-12-01T20:00", "repeat_every": "2w", "definition": {"name": "Alliance battle", "channel_id": "300000000000000001", "template": {"title": "Battle", "description": "Gather {countdown}"}}}
        return headers, payload

    async def test_recurring_event_create_read_edit_pause_resume_stop(self):
        headers, payload = await self.event_setup()
        base = f"/api/guilds/{DEMO_GUILD}"
        response = await self.client.post(base+"/events", json=payload, headers=headers)
        self.assertEqual(response.status, 201)
        created = await response.json()
        data = await (await self.client.get(base+"/events")).json()
        series = data["series"][0]
        self.assertEqual(series["interval_seconds"], 14*86400)
        from datetime import datetime, timezone
        self.assertEqual(datetime.fromtimestamp(series["anchor"], timezone.utc).hour, 17)
        for action, version in [("pause",1),("resume",2),("edit",3),("stop",4)]:
            response = await self.client.patch(base+f"/event-series/{created['series_id']}", json={"action":action,"version":version,"repeat_every":"12h" if action=="edit" else None}, headers=headers)
            self.assertEqual(response.status,200)
        data = await (await self.client.get(base+"/events")).json()
        self.assertEqual(data["series"],[])
        self.assertEqual(data["occurrences"],[])

    async def test_event_mutation_authorization_csrf_tenant_and_conflict(self):
        headers,payload=await self.event_setup()
        base=f"/api/guilds/{DEMO_GUILD}"
        self.assertEqual((await self.client.post(base+"/events",json=payload)).status,403)
        self.assertEqual((await self.client.post('/api/guilds/999/events',json=payload,headers=headers)).status,404)
        created=await (await self.client.post(base+"/events",json=payload,headers=headers)).json()
        path=base+f"/event-series/{created['series_id']}"
        self.assertEqual((await self.client.patch(path,json={"action":"pause","version":20},headers=headers)).status,409)
        self.assertEqual((await self.client.get('/api/guilds/999/events')).status,404)
        self.settings.demo=False
        cfg,v=self.db.config(DEMO_GUILD);cfg.dashboard_users=[DEMO_USER]
        self.db.save_config(DEMO_GUILD,cfg,v,'owner')
        member=SimpleNamespace(guild_permissions=SimpleNamespace(administrator=False))
        guild=SimpleNamespace(fetch_member=AsyncMock(return_value=member))
        self.dashboard.bot=SimpleNamespace(get_guild=lambda _:guild)
        self.assertEqual((await self.client.get(base+'/events')).status,200)
        self.assertEqual((await self.client.post(base+'/events',json=payload,headers=headers)).status,403)
        self.assertEqual((await self.client.patch(path,json={"action":"pause","version":1},headers=headers)).status,403)
        self.assertEqual((await self.client.post(base+f"/events/{created['id']}/cancel",json={},headers=headers)).status,403)

    async def test_event_validation_and_single_occurrence_cancel(self):
        headers,payload=await self.event_setup()
        base=f"/api/guilds/{DEMO_GUILD}"
        for value in ['2m','-1w','366d']:
            response=await self.client.post(base+'/events',json={**payload,'repeat_every':value},headers=headers)
            self.assertEqual(response.status,400)
        self.assertEqual(self.db.one('SELECT COUNT(*) n FROM events')['n'],0)
        created=await (await self.client.post(base+'/events',json=payload,headers=headers)).json()
        response=await self.client.post(base+f"/events/{created['id']}/cancel",json={},headers=headers)
        self.assertEqual(response.status,200)
        self.assertEqual(self.db.one('SELECT status FROM event_series')['status'],'active')
        cfg,v=self.db.config(DEMO_GUILD);cfg.events.enabled=False
        self.db.save_config(DEMO_GUILD,cfg,v,'owner')
        self.assertEqual((await self.client.post(base+'/events',json=payload,headers=headers)).status,400)


if __name__ == "__main__":
    unittest.main()
