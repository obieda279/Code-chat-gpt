from __future__ import annotations

import asyncio
import io
import json
import logging
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

import aiohttp
import discord
from discord import app_commands

from .automod import AutoModerator
from .config import GuildConfig, MODULES, Template
from .db import Database
from .domain import RuleError, Service

log = logging.getLogger(__name__)


def has_roles(member, ids):
    return bool({str(r.id) for r in member.roles} & set(ids))


def admin(member):
    return member.guild_permissions.administrator


def protected_roles(cfg):
    """Bot authority matters even when a role has no native Discord permissions."""
    ids = set(cfg.moderation.roles + cfg.alliances.management_roles + cfg.alliances.r4_roles + cfg.alliances.r5_roles + cfg.presidents.appointment_roles + cfg.tickets.support_roles + cfg.suggestions.manage_roles + cfg.giveaways.manager_roles + cfg.apc.leaderboard_roles)
    if cfg.presidents.role_id:
        ids.add(cfg.presidents.role_id)
    if cfg.messages.send_permission == "custom":
        ids.update(cfg.messages.send_roles)
    if cfg.polls.create_permission == "custom":
        ids.update(cfg.polls.create_roles)
    return ids


def role_safe(role, guild):
    if not role or role.is_default() or role.managed or not guild.me or role >= guild.me.top_role:
        raise RuleError("The role is missing, managed, or above the bot's role.")
    p = role.permissions
    if any(getattr(p, x, False) for x in ("administrator", "manage_guild", "manage_roles", "manage_channels", "ban_members", "kick_members", "moderate_members", "manage_webhooks", "manage_messages", "manage_threads", "mention_everyone")):
        raise RuleError("Automated role assignment cannot grant staff or mass-mention permissions.")


def hierarchy(guild, actor, target):
    if target.id in (guild.owner_id, actor.id, guild.me.id):
        raise RuleError("You cannot perform this action on yourself, the server owner, or this bot.")
    if actor.id != guild.owner_id and actor.top_role <= target.top_role:
        raise RuleError("Your highest role must be above the target's role.")
    if guild.me.top_role <= target.top_role:
        raise RuleError("Move the bot's role above the target's role.")


async def reply(interaction, text=None, **kwargs):
    text = kwargs.pop("content", text)
    kwargs.setdefault("ephemeral", True)
    kwargs.setdefault("allowed_mentions", discord.AllowedMentions.none())
    if interaction.response.is_done():
        return await interaction.followup.send(content=text, **kwargs)
    return await interaction.response.send_message(content=text, **kwargs)


class Tree(app_commands.CommandTree):
    async def interaction_check(self, interaction):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            raise app_commands.CheckFailure("Use this command in a server.")
        command = interaction.command
        name = command.root_parent.name if isinstance(command, app_commands.Command) and command.root_parent else command.name
        module = self.client.command_modules.get(name)
        if module and not getattr(self.client.cfg(interaction.guild_id), module).enabled:
            raise app_commands.CheckFailure("This module is disabled. Enable it in the dashboard.")
        key = (interaction.guild_id, interaction.user.id)
        now = time.monotonic()
        if now - self.client.cooldowns.get(key, 0) < 1:
            raise app_commands.CheckFailure("Please wait a second before using another command.")
        self.client.cooldowns[key] = now
        if len(self.client.cooldowns) > 10000:
            self.client.cooldowns = {k: v for k, v in self.client.cooldowns.items() if now - v < 60}
        return True

    async def on_error(self, interaction, error):
        exc = getattr(error, "original", error)
        if isinstance(exc, (RuleError, app_commands.CheckFailure, ValueError)):
            message = str(exc)
        elif isinstance(exc, discord.Forbidden):
            message = "Discord denied this action. Check the bot's permissions and role position."
        else:
            log.exception("Command failed: %s", interaction.command, exc_info=exc)
            message = "The operation could not complete. Check the server logs before retrying."
        await reply(interaction, message[:1900])


class Sentinel(discord.Client):
    def __init__(self, db: Database, settings):
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True
        super().__init__(intents=intents, allowed_mentions=discord.AllowedMentions.none(), max_messages=1000)
        self.db, self.settings, self.service = db, settings, Service(db)
        self.tree = Tree(self)
        self.command_modules = {}
        self.cooldowns = {}
        self.locks = defaultdict(asyncio.Lock)
        self.automod = AutoModerator()
        self.workers = []
        self.session = None
        self.sync_signatures = {}
        self.translation_usage = defaultdict(list)
        from .commands import register
        register(self)

    def cfg(self, guild) -> GuildConfig:
        return self.db.config(guild)[0]

    def require(self, interaction, permission="moderators", roles=None):
        member = interaction.user
        if not isinstance(member, discord.Member):
            raise RuleError("Use this in a server.")
        if admin(member):
            return
        ids = self.cfg(interaction.guild_id).moderation.roles if roles is None else roles
        if permission == "everyone" or (permission in ("moderators", "custom") and has_roles(member, ids)):
            return
        raise RuleError("You do not have permission to do this.")

    def channel(self, guild, channel_id):
        channel = guild.get_channel(int(channel_id)) if channel_id else None
        if not isinstance(channel, discord.TextChannel):
            raise RuleError("Configure a valid text channel in the dashboard.")
        return channel

    async def setup_hook(self):
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=40))
        self.workers = [asyncio.create_task(self.sync_loop()), asyncio.create_task(self.job_loop()), asyncio.create_task(self.maintenance_loop())]

    async def close(self):
        for worker in self.workers:
            worker.cancel()
        await asyncio.gather(*self.workers, return_exceptions=True)
        if self.session:
            await self.session.close()
        await super().close()

    async def on_ready(self):
        log.info("Sentinel ready in %s server(s)", len(self.guilds))

    async def sync_loop(self):
        await self.wait_until_ready()
        while not self.is_closed():
            for guild in self.guilds:
                try:
                    cfg = self.cfg(guild.id)
                    signature = tuple(m for m in MODULES if getattr(cfg, m).enabled)
                    if self.sync_signatures.get(guild.id) == signature:
                        continue
                    self.tree.clear_commands(guild=guild)
                    for command in self.tree.get_commands():
                        if self.command_modules.get(command.name) in signature or command.name not in self.command_modules:
                            self.tree.add_command(command, guild=guild)
                    await self.tree.sync(guild=guild)
                    self.sync_signatures[guild.id] = signature
                except Exception:
                    log.exception("Command sync failed for server %s", guild.id)
            await asyncio.sleep(5)

    async def emit(self, guild, actor, category, action, detail):
        self.db.audit(guild.id, actor, category + "." + action, detail)
        cfg = self.cfg(guild.id).logs
        if cfg.enabled:
            for target in cfg.targets:
                if target.category in (category, "all"):
                    self.db.enqueue(guild.id, "log", {"channel": target.channel_id, "title": category + " · " + action, "description": json.dumps(detail, ensure_ascii=False)[:3500], "color": target.color}, time.time())

    def embed(self, guild, title, description="", color=None):
        return discord.Embed(title=title[:256], description=description[:4096] or None, color=int((color or self.cfg(guild.id).color).lstrip("#"), 16), timestamp=datetime.now(timezone.utc))

    def template(self, template, replacements=None):
        t = template if isinstance(template, Template) else Template.model_validate(template)
        def render(value):
            for key, replacement in (replacements or {}).items():
                value = value.replace("{" + key + "}", str(replacement))
            return value
        embed = discord.Embed(title=render(t.title)[:256], description=render(t.description)[:4096] or None, color=int(t.color[1:], 16))
        for field in t.fields:
            embed.add_field(name=render(field.name)[:256], value=render(field.value)[:1024], inline=field.inline)
        if t.image:
            embed.set_image(url=t.image)
        if t.thumbnail:
            embed.set_thumbnail(url=t.thumbnail)
        if t.footer:
            embed.set_footer(text=render(t.footer)[:2048])
        if len(embed) > 6000:
            raise RuleError("The rendered embed exceeds Discord's 6000-character limit.")
        return {"content": render(t.content)[:2000] or None, "embed": embed}

    async def create_panel(self, interaction, kind, channel, title, payload, closes=None):
        pid = self.db.execute("INSERT INTO panels(guild,kind,channel,title,payload,creator,closes) VALUES(?,?,?,?,?,?,?)", (str(interaction.guild_id), kind, str(channel.id), title, json.dumps(payload), str(interaction.user.id), closes)).lastrowid
        try:
            panel = self.service.panel(interaction.guild_id, pid)
            message = await channel.send(embed=self.panel_embed(panel), view=self.panel_view(panel))
            self.db.execute("UPDATE panels SET message=? WHERE id=?", (str(message.id), pid))
            if closes:
                self.db.enqueue(interaction.guild_id, "close_panel", {"panel": pid}, closes, f"close:{pid}")
            await self.emit(interaction.guild, interaction.user.id, kind, "created", {"panel": pid})
            return message, pid
        except Exception:
            self.db.execute("DELETE FROM panels WHERE id=?", (pid,))
            raise

    def panel_embed(self, panel):
        payload = panel["payload"]
        guild = self.get_guild(int(panel["guild"]))
        title = ("Closed · " if panel["closed"] else "") + panel["title"]
        desc = payload.get("description", "")
        if panel["kind"] == "polls":
            counts = {r["option"]: r["n"] for r in self.db.all("SELECT option,COUNT(*) n FROM votes WHERE panel=? GROUP BY option", (panel["id"],))}
            desc = "\n".join(f"**{idx + 1}. {option}** — {counts.get(idx, 0)}" for idx, option in enumerate(payload["options"]))
            if panel["closes"]:
                desc += f"\n\nEnds <t:{int(panel['closes'])}:R>"
            desc += "\nMultiple choices" if payload.get("multiple") else "\nOne choice per person"
        elif panel["kind"] == "giveaways":
            count = self.db.one("SELECT COUNT(*) n FROM votes WHERE panel=?", (panel["id"],))["n"]
            desc = f"{count} entries · {payload['winner_count']} winner(s)\nEnds <t:{int(panel['closes'])}:R>"
            if panel["closed"]:
                desc += "\nSelected: " + (", ".join(f"<@{u}>" for u in payload.get("winners", [])) or "No eligible entries")
        elif panel["kind"] == "suggestions":
            desc += "\n\nStatus: " + payload.get("status", "Pending")
        return self.embed(guild, title, desc)

    def panel_view(self, panel):
        view = discord.ui.View(timeout=None)
        if panel["closed"]:
            return view
        kind, pid, data = panel["kind"], panel["id"], panel["payload"]
        if kind in ("polls", "giveaways"):
            for idx, option in enumerate(data.get("options", ["Enter / withdraw"])):
                view.add_item(discord.ui.Button(label=option[:80], custom_id=f"s:vote:{pid}:{idx}", style=discord.ButtonStyle.secondary))
            if kind == "polls" and not data.get("anonymous", False):
                view.add_item(discord.ui.Button(label="View voters", custom_id=f"s:voters:{pid}:0"))
        elif kind == "suggestions":
            for action, style in [("approve", discord.ButtonStyle.success), ("reject", discord.ButtonStyle.danger)]:
                view.add_item(discord.ui.Button(label=action.title(), custom_id=f"s:{action}:{pid}:0", style=style))
        elif kind == "tickets":
            categories = data.get("categories", [])
            if categories:
                view.add_item(discord.ui.Select(custom_id=f"s:ticket:{pid}:0", placeholder="Choose a support category", options=[discord.SelectOption(label=c, value=c) for c in categories]))
            else:
                view.add_item(discord.ui.Button(label="Open ticket", custom_id=f"s:ticket:{pid}:0", style=discord.ButtonStyle.primary))
        elif kind == "reaction_roles":
            if data["mode"] == "dropdown":
                view.add_item(discord.ui.Select(custom_id=f"s:roles:{pid}:0", placeholder="Choose your roles", min_values=0, max_values=1 if data["behavior"] == "exclusive" else len(data["mappings"]), options=[discord.SelectOption(label=m["label"], value=m["role_id"], emoji=m["emoji"] or None) for m in data["mappings"]]))
            elif data["mode"] == "buttons":
                for mapping in data["mappings"]:
                    view.add_item(discord.ui.Button(label=mapping["label"], emoji=mapping["emoji"] or None, custom_id=f"s:roles:{pid}:{mapping['role_id']}"))
        return view

    async def update_panel(self, guild, pid):
        panel = self.service.panel(guild.id, pid)
        if panel["message"]:
            message = self.channel(guild, panel["channel"]).get_partial_message(int(panel["message"]))
            await message.edit(embed=self.panel_embed(panel), view=self.panel_view(panel))

    async def on_interaction(self, interaction):
        if interaction.type != discord.InteractionType.component or not interaction.guild:
            return
        custom_id = interaction.data.get("custom_id", "")
        if not custom_id.startswith("s:"):
            return
        try:
            _, action, pid, value = custom_id.split(":")
            if action == "close_ticket":
                await interaction.response.defer(ephemeral=True)
                await self.close_ticket(interaction, int(pid))
                return
            panel = self.service.panel(interaction.guild_id, int(pid))
            if str(interaction.message.id) != panel["message"] or str(interaction.channel_id) != panel["channel"]:
                raise RuleError("This panel does not match the saved message.")
            if not getattr(self.cfg(interaction.guild_id), panel["kind"]).enabled or panel["closed"]:
                raise RuleError("This panel is disabled or closed.")
            await interaction.response.defer(ephemeral=True)
            async with self.locks[(interaction.guild_id, "panel", int(pid))]:
                if action == "vote" and panel["kind"] in ("polls", "giveaways"):
                    added = self.service.vote(interaction.guild_id, int(pid), interaction.user.id, int(value))
                    await self.update_panel(interaction.guild, int(pid))
                    await reply(interaction, "Entry recorded." if added else "Entry withdrawn.")
                elif action == "voters" and panel["kind"] == "polls":
                    if panel["payload"].get("anonymous", False):
                        raise RuleError("This poll hides voter identities.")
                    rows = self.db.all("SELECT user,option FROM votes WHERE panel=? ORDER BY option,user LIMIT 100", (int(pid),))
                    text = "\n".join(f"{r['option'] + 1}. <@{r['user']}>" for r in rows) or "No votes yet."
                    await reply(interaction, embed=self.embed(interaction.guild, "Voters · first 100 selections", text))
                elif action in ("approve", "reject") and panel["kind"] == "suggestions":
                    self.require(interaction, roles=self.cfg(interaction.guild_id).suggestions.manage_roles or None)
                    data = panel["payload"]
                    data["status"] = f"{'Approved' if action == 'approve' else 'Rejected'} by {interaction.user.display_name}"
                    self.db.execute("UPDATE panels SET closed=1,payload=? WHERE id=?", (json.dumps(data), int(pid)))
                    await self.update_panel(interaction.guild, int(pid))
                    if data.get("thread"):
                        thread = interaction.guild.get_thread(int(data["thread"])) or await interaction.guild.fetch_channel(int(data["thread"]))
                        await thread.edit(archived=True, locked=True)
                    await self.emit(interaction.guild, interaction.user.id, "suggestions", action, {"panel": int(pid)})
                    await reply(interaction, data["status"])
                elif action == "roles" and panel["kind"] == "reaction_roles":
                    selected = interaction.data.get("values", [value])
                    await self.apply_roles(interaction.guild, interaction.user, panel["payload"], selected, dropdown="values" in interaction.data)
                    await reply(interaction, "Your roles have been updated.")
                elif action == "ticket" and panel["kind"] == "tickets":
                    category = interaction.data.get("values", ["General"])[0]
                    if category not in panel["payload"].get("categories", []) and category != "General":
                        raise RuleError("Unknown support category.")
                    await self.open_ticket(interaction, category)
                else:
                    raise RuleError("Unknown panel action.")
        except Exception as exc:
            await self.tree.on_error(interaction, exc)

    async def apply_roles(self, guild, member, panel, selected, dropdown=False, remove=False):
        allowed = {m["role_id"] for m in panel["mappings"]}
        if not set(selected) <= allowed:
            raise RuleError("This role is not offered by the panel.")
        current = {str(r.id) for r in member.roles} & allowed
        chosen = set(selected)
        if panel["behavior"] == "add_only":
            desired = current if remove else current | chosen
        elif panel["behavior"] == "exclusive":
            desired = current - chosen if remove else chosen
            if len(desired) > 1:
                raise RuleError("Choose only one role.")
        elif dropdown:
            desired = chosen
        else:
            desired = current - chosen if remove else current ^ chosen
        for rid in current ^ desired:
            role = guild.get_role(int(rid))
            role_safe(role, guild)
            if rid in protected_roles(self.cfg(guild.id)):
                raise RuleError("This role grants bot management or private-data access and cannot be self-assigned.")
            # Alliance membership must go through the alliance commands.
            if self.db.one("SELECT 1 FROM alliances WHERE guild=? AND role=?", (str(guild.id), rid)):
                raise RuleError("Alliance roles are managed through /alliance commands.")
        for rid in current - desired:
            await member.remove_roles(guild.get_role(int(rid)), reason="Self-service role selection")
        for rid in desired - current:
            await member.add_roles(guild.get_role(int(rid)), reason="Self-service role selection")
        await self.emit(guild, member.id, "reaction_roles", "updated", {"added": sorted(desired-current), "removed": sorted(current-desired)})

    async def open_ticket(self, interaction, category):
        guild, user = interaction.guild, interaction.user
        cfg = self.cfg(guild.id).tickets
        async with self.locks[(guild.id, "tickets", user.id)]:
            count = self.db.one("SELECT COUNT(*) n FROM tickets WHERE guild=? AND owner=? AND status IN ('open','creating','closing')", (str(guild.id), str(user.id)))["n"]
            if count >= cfg.max_per_user:
                raise RuleError("You have reached the open ticket limit.")
            if not cfg.support_roles:
                raise RuleError("Configure at least one support role first.")
            roles = [guild.get_role(int(r)) for r in cfg.support_roles]
            if any(not r or r.is_default() for r in roles):
                raise RuleError("A support role is missing or points to @everyone.")
            category_channel = guild.get_channel(int(cfg.category_id)) if cfg.category_id else None
            if cfg.category_id and not isinstance(category_channel, discord.CategoryChannel):
                raise RuleError("Ticket category no longer exists.")
            tid = self.db.execute("INSERT INTO tickets(guild,owner,category,updated) VALUES(?,?,?,?)", (str(guild.id), str(user.id), category, time.time())).lastrowid
            channel = None
            try:
                overwrites = {guild.default_role: discord.PermissionOverwrite(view_channel=False), user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True), guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True, read_message_history=True)}
                for role in roles:
                    overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
                channel = await guild.create_text_channel(f"ticket-{tid}", category=category_channel, overwrites=overwrites, topic=f"Sentinel ticket {tid} · {category}", reason="Support ticket")
                self.db.execute("UPDATE tickets SET channel=?,status='open' WHERE id=?", (str(channel.id), tid))
                await channel.send(f"{user.mention} Your {category} ticket is open. Use /ticket close when resolved.")
                await reply(interaction, f"Ticket created: {channel.mention}")
                await self.emit(guild, user.id, "tickets", "opened", {"ticket": tid})
            except Exception:
                if channel is None:
                    self.db.execute("DELETE FROM tickets WHERE id=?", (tid,))
                raise

    def ticket_access(self, interaction, tid=None):
        row = self.db.one("SELECT * FROM tickets WHERE guild=? AND channel=? AND status IN ('open','closing')", (str(interaction.guild_id), str(interaction.channel_id)))
        if not row or (tid is not None and row["id"] != tid):
            raise RuleError("Use this command in the corresponding open ticket.")
        cfg = self.cfg(interaction.guild_id).tickets
        if str(interaction.user.id) != row["owner"] and not admin(interaction.user) and not has_roles(interaction.user, cfg.support_roles):
            raise RuleError("Only the ticket owner or support staff can manage this ticket.")
        return row

    async def close_ticket(self, interaction, tid):
        if not self.cfg(interaction.guild_id).tickets.enabled:
            raise RuleError("The tickets module is disabled.")
        row = self.ticket_access(interaction, tid)
        await reply(interaction, "Closing this ticket and preparing its transcript.")
        await self.archive_ticket(interaction.guild, row, interaction.user.id)

    async def archive_ticket(self, guild, row, actor):
        async with self.locks[(guild.id, "ticket-close", row["id"])]:
            row = self.db.one("SELECT * FROM tickets WHERE id=? AND guild=?", (row["id"], str(guild.id)))
            if not row or row["status"] == "closed":
                return
            channel = guild.get_channel(int(row["channel"]))
            if not channel:
                self.db.execute("UPDATE tickets SET status='closed' WHERE id=?", (row["id"],))
                return
            self.db.execute("UPDATE tickets SET status='closing' WHERE id=?", (row["id"],))
            cfg = self.cfg(guild.id).tickets
            # Archive first. Failure never destroys the source channel.
            if cfg.transcript_channel_id and not self.db.ref(guild.id, f"transcript:{row['id']}"):
                out = io.StringIO()
                out.write("Sentinel ticket transcript (last 10,000 messages; attachment links only)\n")
                async for message in channel.history(limit=10000, oldest_first=False):
                    out.write(f"{message.created_at.isoformat()} | {message.author} ({message.author.id}): {message.content}\n")
                    for attachment in message.attachments:
                        out.write(f"Attachment: {attachment.url}\n")
                raw = out.getvalue().encode("utf-8")
                destination = self.channel(guild, cfg.transcript_channel_id)
                # Ensure transcripts are never knowingly placed in a public channel.
                if destination.permissions_for(guild.default_role).view_channel:
                    raise RuleError("Transcript channel must deny @everyone View Channel.")
                if len(raw) > 7_500_000:
                    raise RuleError("Transcript exceeds 7.5 MB. Archive this ticket manually.")
                msg = await destination.send(f"Ticket #{row['id']} · owner <@{row['owner']}>", file=discord.File(io.BytesIO(raw), filename=f"ticket-{row['id']}.txt"))
                self.db.ref(guild.id, f"transcript:{row['id']}", str(msg.id))
            await channel.delete(reason=f"Support ticket closed by {actor}")
            self.db.execute("UPDATE tickets SET status='closed',updated=? WHERE id=?", (time.time(), row["id"]))
            await self.emit(guild, actor, "tickets", "closed", {"ticket": row["id"]})

    async def on_message(self, message):
        if not message.guild:
            return
        cfg = self.cfg(message.guild.id)
        if not message.author.bot:
            self.db.execute("UPDATE tickets SET updated=? WHERE guild=? AND channel=? AND status='open'", (time.time(), str(message.guild.id), str(message.channel.id)))
        if not isinstance(message.author, discord.Member):
            return
        try:
            if cfg.moderation.enabled:
                for lock in cfg.moderation.locks:
                    if str(message.channel.id) == lock.channel_id and not (message.author.id == self.user.id or admin(message.author) or has_roles(message.author, lock.exempt_roles) or has_roles(message.author, cfg.moderation.roles) or (message.author.bot and lock.allow_bots)):
                        await message.delete()
                        if lock.violation == "reply":
                            await message.channel.send("This channel is reserved for bot commands.", delete_after=5)
                        elif lock.violation == "dm":
                            try:
                                await message.author.send("This channel is reserved for bot commands.")
                            except discord.Forbidden:
                                pass
                        return
            if message.author.bot or not cfg.automod.enabled or admin(message.author) or has_roles(message.author, cfg.moderation.roles + cfg.automod.exempt_roles) or str(message.channel.id) in cfg.automod.exempt_channels:
                return
            result = self.automod.inspect(message.guild.id, message.author.id, message.content, len(message.mentions) + len(message.role_mentions) + int(message.mention_everyone), cfg.automod)
            if result:
                name, rule = result
                await message.delete()
                if rule.action == "warn":
                    self.db.execute("INSERT INTO warnings(guild,user,actor,reason,created) VALUES(?,?,?,?,?)", (str(message.guild.id), str(message.author.id), str(self.user.id), "Automatic filter: " + name, time.time()))
                elif rule.action == "mute" and message.author.top_role < message.guild.me.top_role:
                    from datetime import timedelta
                    await message.author.timeout(timedelta(minutes=rule.minutes), reason="Automatic filter: " + name)
                await self.emit(message.guild, message.author.id, "automod", name, {"user": str(message.author.id), "action": rule.action})
        except discord.HTTPException:
            log.exception("Message rule failed")

    async def on_message_edit(self, before, after):
        if before.content != after.content:
            await self.on_message(after)

    async def on_member_join(self, member):
        cfg = self.cfg(member.guild.id).welcome
        if not cfg.enabled:
            return
        try:
            for rid in cfg.auto_roles:
                role = member.guild.get_role(int(rid))
                role_safe(role, member.guild)
                if rid in protected_roles(self.cfg(member.guild.id)):
                    raise RuleError("Welcome roles cannot grant bot management or private-data access.")
                await member.add_roles(role, reason="Welcome role")
            if cfg.channel_id:
                text = cfg.message.replace("{user}", member.mention).replace("{name}", member.display_name).replace("{server}", member.guild.name)
                await self.channel(member.guild, cfg.channel_id).send(text[:2000])
            await self.emit(member.guild, member.id, "welcome", "joined", {"user": str(member.id)})
        except Exception:
            log.exception("Welcome failed")

    async def on_member_remove(self, member):
        self.service.leave(member.guild.id, member.id, server_departure=True)
        self.db.execute("DELETE FROM apc WHERE guild=? AND user=?", (str(member.guild.id), str(member.id)))
        self.db.execute("DELETE FROM birthdays WHERE guild=? AND user=?", (str(member.guild.id), str(member.id)))
        if self.db.one("SELECT id FROM presidents WHERE guild=? AND user=? AND ended IS NULL", (str(member.guild.id), str(member.id))):
            self.db.execute("UPDATE presidents SET ended=? WHERE guild=? AND user=? AND ended IS NULL", (time.time(), str(member.guild.id), str(member.id)))
        cfg = self.cfg(member.guild.id).welcome
        if cfg.enabled and cfg.goodbye_channel_id:
            try:
                await self.channel(member.guild, cfg.goodbye_channel_id).send(cfg.goodbye.replace("{name}", member.display_name).replace("{server}", member.guild.name).replace("{user}", member.mention)[:2000])
            except discord.HTTPException:
                log.exception("Goodbye failed")

    async def on_raw_reaction_add(self, payload):
        await self.reaction(payload, False)

    async def on_raw_reaction_remove(self, payload):
        await self.reaction(payload, True)

    async def reaction(self, payload, removed):
        guild = self.get_guild(payload.guild_id)
        if not guild or payload.user_id == self.user.id:
            return
        try:
            cfg = self.cfg(guild.id)
            row = self.db.one("SELECT id FROM panels WHERE guild=? AND message=? AND kind='reaction_roles' AND closed=0", (str(guild.id), str(payload.message_id)))
            if cfg.reaction_roles.enabled and row:
                panel = self.service.panel(guild.id, row["id"])["payload"]
                if panel["mode"] == "reactions":
                    role_ids = [m["role_id"] for m in panel["mappings"] if str(payload.emoji) == m["emoji"]]
                    member = guild.get_member(payload.user_id) or await guild.fetch_member(payload.user_id)
                    if role_ids and not member.bot:
                        # Reaction additions are idempotent; removals only toggle in toggle mode.
                        if removed and panel["behavior"] != "toggle":
                            return
                        if not removed and role_ids[0] in {str(r.id) for r in member.roles}:
                            return
                        await self.apply_roles(guild, member, panel, role_ids, remove=removed)
            if cfg.starboard.enabled and str(payload.emoji) == cfg.starboard.emoji and str(payload.channel_id) not in cfg.starboard.ignore_channels:
                async with self.locks[(guild.id, "star", payload.message_id)]:
                    await self.refresh_starboard(guild, payload.channel_id, payload.message_id)
        except (discord.NotFound, discord.Forbidden, RuleError):
            log.warning("Reaction could not be processed for guild %s", guild.id)
        except Exception:
            log.exception("Reaction failed")

    async def refresh_starboard(self, guild, channel_id, message_id):
        cfg = self.cfg(guild.id).starboard
        if not cfg.channel_id or str(channel_id) == cfg.channel_id:
            return
        source = self.channel(guild, channel_id)
        # Avoid mirroring content from private channels into a broader audience.
        if not source.permissions_for(guild.default_role).view_channel:
            return
        message = await source.fetch_message(message_id)
        if message.author.bot and cfg.ignore_bots:
            return
        count = 0
        for reaction in message.reactions:
            if str(reaction.emoji) == cfg.emoji:
                async for user in reaction.users(limit=None):
                    if not user.bot and (cfg.self_star or user.id != message.author.id):
                        count += 1
        key = f"star:{message_id}"
        previous = self.db.ref(guild.id, key)
        destination = self.channel(guild, cfg.channel_id)
        if count < cfg.threshold:
            if previous:
                try:
                    await self.channel(guild, previous["channel"]).get_partial_message(int(previous["message"])).delete()
                except discord.NotFound:
                    pass
                self.db.execute("DELETE FROM refs WHERE guild=? AND key=?", (str(guild.id), key))
            return
        embed = self.embed(guild, f"{cfg.emoji} {count} · {message.author.display_name}", message.content[:2048] + f"\n[Jump to message]({message.jump_url})")
        embed.set_author(name=message.author.display_name, icon_url=message.author.display_avatar.url)
        if message.attachments and (message.attachments[0].content_type or "").startswith("image/"):
            embed.set_image(url=message.attachments[0].url)
        await self.upsert_message(guild, key, destination, embed)

    async def upsert_message(self, guild, key, channel, embed):
        previous = self.db.ref(guild.id, key)
        if previous:
            old_channel = guild.get_channel(int(previous["channel"]))
            if old_channel:
                try:
                    msg = old_channel.get_partial_message(int(previous["message"]))
                    if old_channel.id == channel.id:
                        await msg.edit(embed=embed)
                        return
                    await msg.delete()
                except discord.NotFound:
                    pass
        message = await channel.send(embed=embed)
        self.db.ref(guild.id, key, {"channel": str(channel.id), "message": str(message.id)})

    async def refresh_apc(self, guild):
        cfg = self.cfg(guild.id).apc
        previous = self.db.ref(guild.id, "apc")
        if cfg.private or not cfg.enabled or not cfg.channel_id:
            if previous:
                channel = guild.get_channel(int(previous["channel"]))
                if channel:
                    try:
                        await channel.get_partial_message(int(previous["message"])).delete()
                    except discord.NotFound:
                        pass
                self.db.execute("DELETE FROM refs WHERE guild=? AND key='apc'", (str(guild.id),))
            return
        await self.upsert_message(guild, "apc", self.channel(guild, cfg.channel_id), self.apc_embed(guild))

    def apc_embed(self, guild):
        rows = self.db.all("SELECT * FROM apc WHERE guild=? ORDER BY main DESC,second DESC,user LIMIT 30", (str(guild.id),))
        lines = [f"**{idx}.** <@{r['user']}> · {r['main']:,} / {r['second']:,}" for idx, r in enumerate(rows, 1)]
        return self.embed(guild, "APC leaderboard · top 30", "\n".join(lines) or "No strength reports yet.")

    async def sync_member(self, guild, user_id, retired_roles=None):
        try:
            member = guild.get_member(int(user_id)) or await guild.fetch_member(int(user_id))
        except discord.NotFound:
            return
        cfg = self.cfg(guild.id).alliances
        membership = self.service.member(guild.id, user_id)
        alliance = self.db.one("SELECT * FROM alliances WHERE guild=? AND id=?", (str(guild.id), membership["alliance"])) if membership else None
        alliance_roles = {r["role"] for r in self.db.all("SELECT role FROM alliances WHERE guild=? AND role IS NOT NULL", (str(guild.id),))}
        managed = alliance_roles | set(cfg.r4_roles + cfg.r5_roles) | set(retired_roles or [])
        if cfg.traveler_role_id:
            managed.add(cfg.traveler_role_id)
        desired = set()
        if alliance and alliance["role"]:
            desired.add(alliance["role"])
        if membership:
            desired |= set(cfg.r5_roles if membership["rank"] == 5 else cfg.r4_roles if membership["rank"] == 4 else [])
        elif cfg.traveler_role_id:
            desired.add(cfg.traveler_role_id)
        current = {str(r.id) for r in member.roles}
        for rid in (current & managed) - desired:
            role = guild.get_role(int(rid))
            role_safe(role, guild)
            await member.remove_roles(role, reason="Alliance membership sync")
        for rid in desired - current:
            role = guild.get_role(int(rid))
            role_safe(role, guild)
            await member.add_roles(role, reason="Alliance membership sync")
        if cfg.auto_nick_tag and alliance:
            import re
            name = re.sub(r"^\[[A-Za-z0-9]{1,5}\]\s*", "", member.display_name)
            nickname = f"[{alliance['tag']}] {name}"[:32]
            if member.display_name != nickname:
                await member.edit(nick=nickname, reason="Alliance tag sync")

    async def job_loop(self):
        await self.wait_until_ready()
        from .jobs import execute_job
        while not self.is_closed():
            for _ in range(20):
                job = self.db.claim()
                if not job:
                    break
                guild = self.get_guild(int(job["guild"]))
                if not guild:
                    self.db.fail(job, "Bot is not in the server")
                    continue
                try:
                    await execute_job(self, guild, job)
                    self.db.complete(job)
                except Exception as exc:
                    log.exception("Background job %s failed", job["id"])
                    self.db.fail(job, type(exc).__name__ + ": " + str(exc))
            await asyncio.sleep(2)

    async def maintenance_loop(self):
        await self.wait_until_ready()
        from .jobs import maintain
        while not self.is_closed():
            for guild in self.guilds:
                try:
                    await maintain(self, guild)
                except Exception:
                    log.exception("Maintenance failed for guild %s", guild.id)
            await asyncio.sleep(60)
