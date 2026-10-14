from __future__ import annotations

import hashlib
import json
import logging
import secrets
import time
from collections import defaultdict, deque
from pathlib import Path
from urllib.parse import urlencode, urlsplit

import aiohttp
from aiohttp import web
import discord
from pydantic import ValidationError
from .config import GuildConfig, MODULES, EventCreate, SeriesChange
from .db import Conflict
from .domain import RuleError, Service, local_timestamp

STATIC = Path(__file__).parent / "static"
DEMO_GUILD = "100000000000000001"
DEMO_USER = "100000000000000002"
logger = logging.getLogger(__name__)


class Dashboard:
    def __init__(self, db, settings, bot=None):
        self.db, self.settings, self.bot = db, settings, bot
        self.states = {}
        self.rate = defaultdict(lambda: deque(maxlen=121))
        self.http = None

    @web.middleware
    async def guard(self, request, handler):
        expected = urlsplit(self.settings.public_url).netloc
        allowed_hosts = {expected}
        if self.settings.demo:
            allowed_hosts.add(f"127.0.0.1:{self.settings.port}")
        if request.host not in allowed_hosts:
            raise web.HTTPForbidden(text="Unrecognized host")
        now = time.time()
        key = request.remote or "unknown"
        recent = self.rate[key]
        while recent and recent[0] < now - 60:
            recent.popleft()
        if len(recent) >= 120:
            raise web.HTTPTooManyRequests(text="Too many requests")
        recent.append(now)
        if len(self.rate) > 5000:
            self.rate = defaultdict(lambda: deque(maxlen=121), {k: v for k, v in self.rate.items() if v and v[-1] >= now - 60})
        try:
            if request.method in ("POST", "PUT", "PATCH", "DELETE"):
                session = self.session(request)
                origin = request.headers.get("Origin")
                allowed_origins = {self.settings.public_url}
                if self.settings.demo:
                    allowed_origins |= {f"http://localhost:{self.settings.port}", f"http://127.0.0.1:{self.settings.port}"}
                if origin not in allowed_origins:
                    raise web.HTTPForbidden(text="Invalid request origin")
                if not secrets.compare_digest(request.headers.get("X-CSRF-Token", ""), session["csrf"]):
                    raise web.HTTPForbidden(text="Invalid CSRF token")
            response = await handler(request)
        except ValidationError as exc:
            response = web.json_response({"error": "; ".join(".".join(map(str, e["loc"])) + ": " + e["msg"] for e in exc.errors(include_input=False))}, status=400)
        except Conflict as exc:
            response = web.json_response({"error": str(exc)}, status=409)
        except (RuleError, ValueError, json.JSONDecodeError) as exc:
            response = web.json_response({"error": str(exc)[:1000]}, status=400)
        except web.HTTPException as exc:
            if 300 <= exc.status < 400:
                response = exc
            else:
                response = web.json_response({"error": exc.text}, status=exc.status)
        except Exception:
            logger.exception("Dashboard request failed: %s", request.path)
            response = web.json_response({"error": "Request failed. Check the application logs."}, status=500)
        response.headers.update({"X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer", "Cache-Control": "no-store", "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"})
        if self.settings.secure:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response

    def session(self, request):
        raw = request.cookies.get("sentinel_session", "")
        if not raw:
            raise web.HTTPUnauthorized(text="Sign in with Discord first")
        row = self.db.one("SELECT * FROM sessions WHERE id=? AND expires>?", (hashlib.sha256(raw.encode()).hexdigest(), time.time()))
        if not row:
            raise web.HTTPUnauthorized(text="Your session expired. Sign in again.")
        return row

    def issue_session(self, response, user):
        raw, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        self.db.execute("INSERT INTO sessions VALUES(?,?,?,?)", (hashlib.sha256(raw.encode()).hexdigest(), str(user), csrf, time.time() + 8 * 3600))
        response.set_cookie("sentinel_session", raw, httponly=True, secure=self.settings.secure, samesite="Lax", max_age=8 * 3600, path="/")
        return csrf

    async def access(self, request, guild_id, write=False):
        session = self.session(request)
        if self.settings.demo:
            if guild_id != DEMO_GUILD:
                raise web.HTTPNotFound(text="Server not found")
            return session, None, True
        guild = self.bot.get_guild(int(guild_id))
        if not guild:
            raise web.HTTPNotFound(text="Bot is not installed in this server")
        # Fetch on each protected request: a removed administrator loses access immediately.
        try:
            member = await guild.fetch_member(int(session["user"]))
        except discord.NotFound:
            raise web.HTTPForbidden(text="You must be a current server member")
        is_admin = member.guild_permissions.administrator
        if not is_admin and session["user"] not in self.db.config(guild_id)[0].dashboard_users:
            raise web.HTTPForbidden(text="You cannot access this server")
        if write and not is_admin:
            raise web.HTTPForbidden(text="Only server administrators can change configuration")
        return session, guild, is_admin

    async def home(self, request):
        return web.FileResponse(STATIC / "index.html")

    async def asset(self, request):
        name = request.match_info["name"]
        if name not in {"app.js", "style.css"}:
            raise web.HTTPNotFound()
        return web.FileResponse(STATIC / name)

    async def health(self, request):
        return web.json_response({"status": "ok", "demo": self.settings.demo, "bot_online": bool(self.bot and self.bot.is_ready())})

    async def login(self, request):
        if self.settings.demo:
            response = web.Response(status=302, headers={"Location": "/"})
            self.issue_session(response, DEMO_USER)
            return response
        now = time.time()
        self.states = {k: v for k, v in self.states.items() if v["expires"] > now}
        if len(self.states) >= 1000:
            raise web.HTTPTooManyRequests(text="Please try signing in later")
        state, browser_nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        self.states[state] = {"binding": hashlib.sha256(browser_nonce.encode()).hexdigest(), "expires": now + 300}
        query = urlencode({"client_id": self.settings.client_id, "redirect_uri": self.settings.callback, "response_type": "code", "scope": "identify", "state": state})
        response = web.Response(status=302, headers={"Location": "https://discord.com/oauth2/authorize?" + query})
        response.set_cookie("sentinel_oauth", browser_nonce, httponly=True, secure=self.settings.secure, samesite="Lax", max_age=300, path="/auth")
        return response

    async def callback(self, request):
        state = self.states.pop(request.query.get("state", ""), None)
        binding = hashlib.sha256(request.cookies.get("sentinel_oauth", "").encode()).hexdigest()
        if not state or state["expires"] < time.time() or not secrets.compare_digest(state["binding"], binding):
            raise web.HTTPForbidden(text="Invalid or expired OAuth state")
        code = request.query.get("code")
        if not code or len(code) > 2048:
            raise web.HTTPBadRequest(text="Discord login was not completed")
        async with self.http.post("https://discord.com/api/oauth2/token", data={"grant_type": "authorization_code", "code": code, "redirect_uri": self.settings.callback, "client_id": self.settings.client_id, "client_secret": self.settings.client_secret}) as response:
            if response.status != 200:
                raise web.HTTPBadGateway(text="Discord rejected the login. Try signing in again.")
            token = await response.json()
        async with self.http.get("https://discord.com/api/v10/users/@me", headers={"Authorization": "Bearer " + token["access_token"]}) as response:
            if response.status != 200:
                raise web.HTTPBadGateway(text="Discord profile lookup failed")
            user = await response.json()
        # No Discord access token or refresh token is persisted or sent to the browser.
        result = web.Response(status=302, headers={"Location": "/"})
        old = request.cookies.get("sentinel_session", "")
        if old:
            self.db.execute("DELETE FROM sessions WHERE id=?", (hashlib.sha256(old.encode()).hexdigest(),))
        self.issue_session(result, user["id"])
        result.del_cookie("sentinel_oauth", path="/auth")
        return result

    async def logout(self, request):
        session = self.session(request)
        self.db.execute("DELETE FROM sessions WHERE id=?", (session["id"],))
        response = web.json_response({"ok": True})
        response.del_cookie("sentinel_session", path="/")
        return response

    async def me(self, request):
        session = self.session(request)
        return web.json_response({"user": session["user"], "csrf": session["csrf"], "demo": self.settings.demo, "bot_online": bool(self.bot and self.bot.is_ready()), "providers": {"translation": bool(self.settings.deepl_key), "transcription": bool(self.settings.groq_key)}})

    async def guilds(self, request):
        self.session(request)
        if self.settings.demo:
            return web.json_response([{"id": DEMO_GUILD, "name": "Alliance HQ · Demo", "members": 128, "admin": True}])
        rows = []
        for guild in self.bot.guilds:
            try:
                _, _, is_admin = await self.access(request, str(guild.id))
                rows.append({"id": str(guild.id), "name": guild.name, "members": guild.member_count, "admin": is_admin})
            except (web.HTTPForbidden, discord.Forbidden):
                continue
        return web.json_response(rows)

    async def get_config(self, request):
        gid = request.match_info["guild"]
        _, guild, is_admin = await self.access(request, gid)
        config, version = self.db.config(gid)
        roles, channels = [], []
        if guild:
            roles = [{"id": str(r.id), "name": r.name} for r in guild.roles if not r.is_default()]
            channels = [{"id": str(c.id), "name": c.name, "type": str(c.type)} for c in guild.channels]
        else:
            roles = [{"id": "200000000000000001", "name": "Moderators"}, {"id": "200000000000000002", "name": "Alliance R4"}]
            channels = [{"id": "300000000000000001", "name": "announcements", "type": "text"}, {"id": "300000000000000002", "name": "support", "type": "category"}]
        return web.json_response({"config": config.model_dump(), "version": version, "roles": roles, "channels": channels, "can_edit": is_admin})

    async def save_config(self, request):
        gid = request.match_info["guild"]
        session, guild, _ = await self.access(request, gid, write=True)
        payload = await request.json()
        if not isinstance(payload, dict) or set(payload) != {"config", "version"} or type(payload["version"]) is not int:
            raise RuleError("Send config and its integer version")
        config = GuildConfig.model_validate(payload["config"])
        from .bot import protected_roles
        self_assigned = set(config.welcome.auto_roles) | {m.role_id for panel in config.reaction_roles.panels for m in panel.mappings}
        if self_assigned & protected_roles(config):
            raise RuleError("Self-service and welcome roles cannot grant bot management or private-data access")
        if guild:
            from .bot import role_safe
            ids = set(config.welcome.auto_roles + config.alliances.r4_roles + config.alliances.r5_roles)
            ids |= {m.role_id for panel in config.reaction_roles.panels for m in panel.mappings}
            ids |= {r for r in [config.presidents.role_id, config.alliances.traveler_role_id] if r}
            for rid in ids:
                role_safe(guild.get_role(int(rid)), guild)
            if str(guild.id) in config.tickets.support_roles:
                raise RuleError("@everyone cannot be a ticket support role")
            if config.tickets.transcript_channel_id:
                channel = self.bot.channel(guild, config.tickets.transcript_channel_id)
                if channel.permissions_for(guild.default_role).view_channel:
                    raise RuleError("Transcript channel must deny @everyone View Channel")
        old = self.db.config(gid)[0]
        def queue_reconciliation():
            if self.settings.demo:
                return
            self.db.enqueue(gid, "reconcile", {}, time.time())
            if config.alliances != old.alliances:
                before = set(old.alliances.r4_roles + old.alliances.r5_roles + ([old.alliances.traveler_role_id] if old.alliances.traveler_role_id else []))
                after = set(config.alliances.r4_roles + config.alliances.r5_roles + ([config.alliances.traveler_role_id] if config.alliances.traveler_role_id else []))
                for row in self.db.all("SELECT user FROM members WHERE guild=?", (gid,)):
                    self.db.enqueue(gid, "sync_member", {"user": row["user"], "retired_roles": sorted(before-after)}, time.time())
            if config.presidents.role_id != old.presidents.role_id:
                self.db.enqueue(gid, "president_sync", {"retired_roles": [old.presidents.role_id] if old.presidents.role_id else []}, time.time())
        version = self.db.save_config(gid, config, payload["version"], session["user"], after_save=queue_reconciliation)
        return web.json_response({"version": version, "config": config.model_dump()})

    async def schema(self, request):
        self.session(request)
        return web.json_response({"schema": GuildConfig.model_json_schema(), "modules": MODULES})

    async def overview(self, request):
        gid = request.match_info["guild"]
        await self.access(request, gid)
        tables = {"alliances": "alliances", "members": "members", "open_tickets": "tickets", "active_polls": "panels", "scheduled_events": "events", "failed_jobs": "jobs"}
        filters = {"open_tickets": " AND status IN ('open','closing')", "active_polls": " AND kind='polls' AND closed=0", "scheduled_events": " AND status='scheduled'", "failed_jobs": " AND state='failed'"}
        counts = {key: self.db.one(f"SELECT COUNT(*) n FROM {table} WHERE guild=?" + filters.get(key, ""), (gid,))["n"] for key, table in tables.items()}
        return web.json_response({"counts": counts, "audit": self.db.all("SELECT action,actor,created,detail FROM audit WHERE guild=? ORDER BY id DESC LIMIT 25", (gid,)), "jobs": self.db.all("SELECT id,kind,state,attempts,error FROM jobs WHERE guild=? AND state='failed' ORDER BY id DESC LIMIT 20", (gid,)), "alliances": self.db.all("SELECT a.id,a.name,a.tag,COUNT(m.user) members FROM alliances a LEFT JOIN members m ON m.alliance=a.id WHERE a.guild=? GROUP BY a.id", (gid,))})

    async def retry_job(self, request):
        gid = request.match_info["guild"]
        session, _, _ = await self.access(request, gid, write=True)
        job_id = int(request.match_info["job"])
        count = self.db.execute("UPDATE jobs SET state='pending',attempts=0,due=?,error=NULL WHERE guild=? AND id=? AND state='failed'", (time.time(), gid, job_id)).rowcount
        if not count:
            raise web.HTTPNotFound(text="Failed job not found")
        self.db.audit(gid, session["user"], "jobs.retried", {"job": job_id})
        return web.json_response({"ok": True})

    async def events(self, request):
        gid = request.match_info["guild"]
        await self.access(request, gid)
        return web.json_response({"series": Service(self.db).series_list(gid), "occurrences": self.db.all("SELECT id,name,channel,starts,ends,status,series_id FROM events WHERE guild=? AND status IN ('scheduled','started') AND ends>? ORDER BY starts LIMIT 50", (gid, time.time()))})

    async def create_event(self, request):
        gid = request.match_info["guild"]
        session, guild, _ = await self.access(request, gid, write=True)
        payload = EventCreate.model_validate(await request.json())
        cfg = self.db.config(gid)[0]
        if not cfg.events.enabled:
            raise RuleError("Enable Scheduled events and save your settings first.")
        channel_id = payload.definition.channel_id or cfg.events.channel_id
        if guild:
            channel = guild.get_channel(int(channel_id)) if channel_id else None
            if not isinstance(channel, discord.TextChannel) or not channel.permissions_for(guild.me).send_messages or not channel.permissions_for(guild.me).embed_links:
                raise RuleError("Choose a text channel where the bot can send messages and embeds.")
        elif channel_id != "300000000000000001":
            raise RuleError("Choose the announcements channel in the demo.")
        starts = local_timestamp(payload.when, cfg.timezone)
        eid = Service(self.db).create_event(gid, payload.definition, starts, session["user"], payload.repeat_every)
        row = self.db.one("SELECT id,series_id FROM events WHERE id=? AND guild=?", (eid, gid))
        return web.json_response(row, status=201)

    async def change_series(self, request):
        gid = request.match_info["guild"]
        session, _, _ = await self.access(request, gid, write=True)
        payload = SeriesChange.model_validate(await request.json())
        cfg = self.db.config(gid)[0]
        starts = local_timestamp(payload.when, cfg.timezone) if payload.when else None
        Service(self.db).control_series(gid, int(request.match_info["series"]), payload.action, session["user"], repeat_every=payload.repeat_every, starts=starts, version=payload.version)
        return web.json_response({"ok": True})

    async def cancel_event(self, request):
        gid = request.match_info["guild"]
        session, _, _ = await self.access(request, gid, write=True)
        Service(self.db).cancel_event(gid, int(request.match_info["event"]), session["user"])
        return web.json_response({"ok": True})

    async def context(self, app):
        self.http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20))
        yield
        await self.http.close()

    def app(self):
        app = web.Application(middlewares=[self.guard], client_max_size=128 * 1024)
        app.cleanup_ctx.append(self.context)
        app.add_routes([web.get("/", self.home), web.get("/static/{name}", self.asset), web.get("/healthz", self.health), web.get("/auth/login", self.login), web.get("/auth/callback", self.callback), web.post("/auth/logout", self.logout), web.get("/api/me", self.me), web.get("/api/guilds", self.guilds), web.get("/api/schema", self.schema), web.get("/api/guilds/{guild}/config", self.get_config), web.put("/api/guilds/{guild}/config", self.save_config), web.get("/api/guilds/{guild}/overview", self.overview), web.post("/api/guilds/{guild}/jobs/{job}/retry", self.retry_job)])
        app.add_routes([web.get("/api/guilds/{guild}/events", self.events), web.post("/api/guilds/{guild}/events", self.create_event), web.post("/api/guilds/{guild}/events/{event}/cancel", self.cancel_event), web.patch("/api/guilds/{guild}/event-series/{series}", self.change_series)])
        return app
