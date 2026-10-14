from __future__ import annotations

import json
import re
import time
from datetime import timedelta
from typing import Literal

import discord
from discord import app_commands

from .bot import admin, has_roles, hierarchy, reply, role_safe, protected_roles
from .config import Lock, Template, EventDefinition
from .domain import RuleError, duration, local_timestamp, week_key

Faction = Literal["Fighters", "Shooters", "Riders"]


def register(bot):
    def add(module, name, description):
        def decorator(callback):
            command = app_commands.Command(name=name, description=description, callback=callback)
            command.guild_only = True
            bot.tree.add_command(command)
            if module:
                bot.command_modules[name] = module
            return command
        return decorator

    def group(module, name, description):
        g = app_commands.Group(name=name, description=description, guild_only=True)
        bot.tree.add_command(g)
        bot.command_modules[name] = module
        return g

    async def defer(i):
        await i.response.defer(ephemeral=True)

    def check_channel(i, channel):
        if channel.guild.id != i.guild_id or not channel.permissions_for(i.user).view_channel or not channel.permissions_for(i.user).send_messages:
            raise RuleError("Choose a channel where you can view and send messages.")

    @add(None, "dashboard", "Open the Sentinel configuration dashboard")
    async def dashboard(i: discord.Interaction):
        await reply(i, bot.settings.public_url)

    @add(None, "status", "Show bot status and enabled modules")
    async def status(i: discord.Interaction):
        cfg = bot.cfg(i.guild_id)
        from .config import MODULES
        modules = [m.replace("_", " ") for m in MODULES if getattr(cfg, m).enabled]
        await reply(i, f"Sentinel is online. Latency: {bot.latency * 1000:.0f} ms.\nEnabled: {', '.join(modules) or 'None yet. Open /dashboard.'}")

    def moderation_action(action):
        async def run(i: discord.Interaction, target: discord.Member, reason: str = "No reason provided"):
            bot.require(i)
            hierarchy(i.guild, i.user, target)
            if len(reason) > 512:
                raise RuleError("Reason must be at most 512 characters.")
            await defer(i)
            if action == "ban":
                await target.ban(reason=f"{i.user.id}: {reason}", delete_message_seconds=0)
            elif action == "kick":
                await target.kick(reason=f"{i.user.id}: {reason}")
            elif action == "unmute":
                await target.timeout(None, reason=f"{i.user.id}: {reason}")
            elif action == "warn":
                bot.db.execute("INSERT INTO warnings(guild,user,actor,reason,created) VALUES(?,?,?,?,?)", (str(i.guild_id), str(target.id), str(i.user.id), reason, time.time()))
            if bot.cfg(i.guild_id).moderation.dm_on_action:
                try:
                    await target.send(f"{action.title()} in {i.guild.name}. Reason: {reason}")
                except discord.HTTPException:
                    pass
            await bot.emit(i.guild, i.user.id, "moderation", action, {"user": str(target.id), "reason": reason})
            if action == "warn":
                count = bot.db.one("SELECT COUNT(*) n FROM warnings WHERE guild=? AND user=?", (str(i.guild_id), str(target.id)))["n"]
                if count >= bot.cfg(i.guild_id).moderation.warning_threshold:
                    await bot.emit(i.guild, i.user.id, "moderation", "warning_threshold", {"user": str(target.id), "count": count})
            await reply(i, f"{action.title()} completed for {target.display_name}.")
        return run

    for action in ("ban", "kick", "warn", "unmute"):
        add("moderation", action, f"{action.title()} a server member")(moderation_action(action))

    @add("moderation", "mute", "Time out a member for up to 28 days")
    async def mute(i: discord.Interaction, target: discord.Member, minutes: app_commands.Range[int, 1, 40320] = 10, reason: str = "No reason provided"):
        bot.require(i)
        hierarchy(i.guild, i.user, target)
        if target.guild_permissions.administrator:
            raise RuleError("Discord does not allow timing out administrators.")
        if len(reason) > 512:
            raise RuleError("Reason must be at most 512 characters.")
        await defer(i)
        await target.timeout(timedelta(minutes=minutes), reason=f"{i.user.id}: {reason}")
        if bot.cfg(i.guild_id).moderation.dm_on_action:
            try:
                await target.send(f"Timed out in {i.guild.name} for {minutes} minutes. Reason: {reason}")
            except discord.HTTPException:
                pass
        await bot.emit(i.guild, i.user.id, "moderation", "mute", {"user": str(target.id), "minutes": minutes, "reason": reason})
        await reply(i, f"Timed out {target.display_name} for {minutes} minutes.")

    @add("moderation", "warnings", "Show a member's last ten warnings")
    async def warnings(i: discord.Interaction, target: discord.Member):
        bot.require(i)
        rows = bot.db.all("SELECT * FROM warnings WHERE guild=? AND user=? ORDER BY id DESC LIMIT 10", (str(i.guild_id), str(target.id)))
        await reply(i, embed=bot.embed(i.guild, "Warnings", "\n".join(f"#{r['id']} · {r['reason']}" for r in rows) or "No warnings."))

    @add("moderation", "clear", "Remove up to 100 recent messages from this channel")
    async def clear(i: discord.Interaction, amount: app_commands.Range[int, 1, 100]):
        bot.require(i)
        await defer(i)
        deleted = await i.channel.purge(limit=amount, reason=f"Clear requested by {i.user.id}")
        await bot.emit(i.guild, i.user.id, "moderation", "clear", {"channel": str(i.channel_id), "count": len(deleted)})
        await reply(i, f"Deleted {len(deleted)} messages.")

    @add("moderation", "setnick", "Change or reset a member's nickname")
    async def setnick(i: discord.Interaction, target: discord.Member, nickname: str | None = None):
        bot.require(i)
        hierarchy(i.guild, i.user, target)
        if nickname is not None and not 1 <= len(nickname) <= 32:
            raise RuleError("Nickname must contain 1–32 characters.")
        await defer(i)
        await target.edit(nick=nickname, reason=f"Requested by {i.user.id}")
        await bot.emit(i.guild, i.user.id, "moderation", "setnick", {"user": str(target.id)})
        await reply(i, "Nickname updated.")

    role_group = group("moderation", "role", "Manage member roles")
    def role_action(remove):
        async def run(i: discord.Interaction, target: discord.Member, role: discord.Role):
            bot.require(i)
            hierarchy(i.guild, i.user, target)
            if role.managed or role.is_default() or role >= i.guild.me.top_role or (i.user.id != i.guild.owner_id and role >= i.user.top_role):
                raise RuleError("You or the bot cannot manage that role.")
            if not admin(i.user):
                role_safe(role, i.guild)
                if str(role.id) in protected_roles(bot.cfg(i.guild_id)):
                    raise RuleError("Only an administrator may change a role that grants bot management or private-data access.")
            await defer(i)
            await (target.remove_roles(role, reason=f"Requested by {i.user.id}") if remove else target.add_roles(role, reason=f"Requested by {i.user.id}"))
            await bot.emit(i.guild, i.user.id, "moderation", "role_removed" if remove else "role_added", {"user": str(target.id), "role": str(role.id)})
            await reply(i, "Role updated.")
        return run
    role_group.command(name="add", description="Assign a role")(role_action(False))
    role_group.command(name="remove", description="Remove a role")(role_action(True))

    @add("moderation", "slowmode", "Set channel slowmode, optionally with a removal timer")
    async def slowmode(i: discord.Interaction, seconds: app_commands.Range[int, 0, 21600], channel: discord.TextChannel | None = None, minutes: app_commands.Range[int, 1, 40320] | None = None):
        bot.require(i)
        channel = channel or bot.channel(i.guild, i.channel_id)
        check_channel(i, channel)
        await defer(i)
        old = channel.slowmode_delay
        await channel.edit(slowmode_delay=seconds, reason=f"Requested by {i.user.id}")
        generation = str(time.time_ns())
        bot.db.ref(i.guild_id, f"slowmode:{channel.id}", generation)
        if minutes:
            bot.db.enqueue(i.guild_id, "slowmode", {"channel": str(channel.id), "old": old, "expected": seconds, "generation": generation}, time.time() + minutes * 60)
        await bot.emit(i.guild, i.user.id, "moderation", "slowmode", {"channel": str(channel.id), "seconds": seconds})
        await reply(i, f"Slowmode set to {seconds} seconds.")

    @add("moderation", "lockdown", "Limit a text channel to bot commands and configured roles")
    async def lockdown(i: discord.Interaction, channel: discord.TextChannel, allow_bots: bool = True, violation: Literal["silent", "reply", "dm"] = "silent"):
        bot.require(i)
        check_channel(i, channel)
        cfg, version = bot.db.config(i.guild_id)
        cfg.moderation.locks = [x for x in cfg.moderation.locks if x.channel_id != str(channel.id)] + [Lock(channel_id=str(channel.id), allow_bots=allow_bots, violation=violation)]
        bot.db.save_config(i.guild_id, cfg, version, i.user.id)
        await reply(i, "Channel lock enabled. Slash commands remain available.")

    @add("moderation", "unlock", "Remove a channel's bot-command-only lock")
    async def unlock(i: discord.Interaction, channel: discord.TextChannel):
        bot.require(i)
        cfg, version = bot.db.config(i.guild_id)
        cfg.moderation.locks = [x for x in cfg.moderation.locks if x.channel_id != str(channel.id)]
        bot.db.save_config(i.guild_id, cfg, version, i.user.id)
        await reply(i, "Channel lock removed.")

    @add("moderation", "userinfo", "Show server membership details")
    async def userinfo(i: discord.Interaction, member: discord.Member | None = None):
        member = member or i.user
        await reply(i, embed=bot.embed(i.guild, member.display_name, f"ID: {member.id}\nAccount created: <t:{int(member.created_at.timestamp())}:D>\nJoined: {member.joined_at}\nRoles: {', '.join(r.name for r in member.roles[1:])}"))

    @add("moderation", "serverinfo", "Show server details")
    async def serverinfo(i: discord.Interaction):
        await reply(i, embed=bot.embed(i.guild, i.guild.name, f"Members: {i.guild.member_count}\nChannels: {len(i.guild.channels)}\nRoles: {len(i.guild.roles)}\nBoost level: {i.guild.premium_tier}"))

    @add("polls", "poll", "Create a persistent poll with 2–10 choices")
    async def poll(i: discord.Interaction, question: str, options: str, ends_in: str | None = None, multiple: bool | None = None, anonymous: bool | None = None):
        cfg = bot.cfg(i.guild_id).polls
        bot.require(i, cfg.create_permission, cfg.create_roles if cfg.create_permission == "custom" else None)
        choices = [x.strip() for x in options.split("|")]
        if not 2 <= len(choices) <= 10 or any(not x or len(x) > 80 for x in choices) or len(set(choices)) != len(choices):
            raise RuleError("Separate 2–10 unique choices with |. Each choice must contain 1–80 characters.")
        if not 1 <= len(question) <= 256:
            raise RuleError("Question must contain 1–256 characters.")
        await defer(i)
        message, _ = await bot.create_panel(i, "polls", bot.channel(i.guild, i.channel_id), question, {"options": choices, "multiple": cfg.multiple if multiple is None else multiple, "anonymous": cfg.anonymous if anonymous is None else anonymous}, time.time() + (duration(ends_in) if ends_in else cfg.default_minutes * 60))
        await reply(i, f"Poll created: {message.jump_url}")

    @add("polls", "polls", "List active polls in channels you can access")
    async def polls(i: discord.Interaction):
        rows = bot.db.all("SELECT * FROM panels WHERE guild=? AND kind='polls' AND closed=0 ORDER BY id DESC LIMIT 30", (str(i.guild_id),))
        rows = [r for r in rows if i.guild.get_channel(int(r["channel"])) and i.guild.get_channel(int(r["channel"])).permissions_for(i.user).view_channel][:10]
        await reply(i, embed=bot.embed(i.guild, "Active polls", "\n".join(f"#{r['id']} · {r['title']} · <#{r['channel']}>" for r in rows) or "No active polls."))

    @add("suggestions", "suggest", "Submit a suggestion for community discussion")
    async def suggest(i: discord.Interaction, content: str):
        if not 1 <= len(content) <= 2000:
            raise RuleError("Suggestions must contain 1–2000 characters.")
        cfg = bot.cfg(i.guild_id).suggestions
        channel = bot.channel(i.guild, cfg.channel_id)
        if not channel.permissions_for(i.user).view_channel:
            raise RuleError("You cannot access the configured suggestions channel.")
        await defer(i)
        author = "Anonymous" if cfg.anonymous else i.user.display_name
        message, pid = await bot.create_panel(i, "suggestions", channel, f"Suggestion · {author}", {"description": content})
        await message.add_reaction("👍")
        await message.add_reaction("👎")
        if cfg.create_thread:
            thread = await message.create_thread(name=f"Suggestion {pid}", auto_archive_duration=1440)
            data = bot.service.panel(i.guild_id, pid)["payload"]
            data["thread"] = str(thread.id)
            bot.db.execute("UPDATE panels SET payload=? WHERE id=?", (json.dumps(data), pid))
        await reply(i, f"Suggestion submitted: {message.jump_url}")

    giveaway = group("giveaways", "giveaway", "Run giveaways")
    @giveaway.command(name="start", description="Create a giveaway with persistent entry buttons")
    async def giveaway_start(i: discord.Interaction, prize: str, ends_in: str, winners: app_commands.Range[int, 1, 20] = 1):
        bot.require(i, roles=bot.cfg(i.guild_id).giveaways.manager_roles or None)
        if not 1 <= len(prize) <= 200:
            raise RuleError("Prize must contain 1–200 characters.")
        await defer(i)
        message, pid = await bot.create_panel(i, "giveaways", bot.channel(i.guild, i.channel_id), prize, {"winner_count": winners}, time.time() + duration(ends_in))
        await reply(i, f"Giveaway #{pid}: {message.jump_url}")

    @giveaway.command(name="reroll", description="Choose new winners from a closed giveaway")
    async def reroll(i: discord.Interaction, panel_id: int):
        bot.require(i, roles=bot.cfg(i.guild_id).giveaways.manager_roles or None)
        panel = bot.service.panel(i.guild_id, panel_id)
        if not panel["closed"]:
            raise RuleError("Wait until the giveaway closes.")
        await defer(i)
        eligible = {str(m.id) for m in i.guild.members if not m.bot}
        winners = bot.service.draw(i.guild_id, panel_id, eligible, reroll=True)
        await bot.update_panel(i.guild, panel_id)
        await bot.emit(i.guild, i.user.id, "giveaways", "rerolled", {"panel": panel_id, "winners": winners})
        await reply(i, "New winners selected." if winners else "No additional eligible entrants.")

    roles = group("reaction_roles", "roles", "Deploy self-service role panels")
    @roles.command(name="panel", description="Deploy a saved role panel")
    async def role_panel(i: discord.Interaction, name: str, channel: discord.TextChannel | None = None):
        bot.require(i, "administrators")
        cfg = bot.cfg(i.guild_id).reaction_roles
        matches = [p for p in cfg.panels if p.name.casefold() == name.casefold()]
        if not matches:
            raise RuleError("Create a panel with this name in the dashboard first.")
        panel = matches[0]
        for mapping in panel.mappings:
            role_safe(i.guild.get_role(int(mapping.role_id)), i.guild)
        await defer(i)
        message, _ = await bot.create_panel(i, "reaction_roles", channel or bot.channel(i.guild, i.channel_id), panel.title, panel.model_dump())
        if panel.mode == "reactions":
            for mapping in panel.mappings:
                await message.add_reaction(mapping.emoji)
        await reply(i, f"Panel deployed: {message.jump_url}")

    embed_group = group("messages", "embed", "Use saved message templates")
    @embed_group.command(name="list", description="List saved embed templates")
    async def embed_list(i: discord.Interaction):
        cfg = bot.cfg(i.guild_id).messages
        bot.require(i, cfg.send_permission, cfg.send_roles if cfg.send_permission == "custom" else None)
        await reply(i, "\n".join(t.name for t in cfg.templates) or "No saved templates.")

    @embed_group.command(name="send", description="Send a saved template")
    async def embed_send(i: discord.Interaction, name: str, channel: discord.TextChannel | None = None):
        cfg = bot.cfg(i.guild_id).messages
        bot.require(i, cfg.send_permission, cfg.send_roles if cfg.send_permission == "custom" else None)
        channel = channel or bot.channel(i.guild, i.channel_id)
        check_channel(i, channel)
        if cfg.allowed_channels and str(channel.id) not in cfg.allowed_channels:
            raise RuleError("This channel is not allowed for templates.")
        templates = [t for t in cfg.templates if t.name.casefold() == name.casefold()]
        if not templates:
            raise RuleError("Unknown template.")
        await defer(i)
        message = await channel.send(**bot.template(templates[0]))
        await bot.emit(i.guild, i.user.id, "messages", "sent", {"template": name, "message": str(message.id)})
        await reply(i, f"Sent: {message.jump_url}")

    alliance = group("alliances", "alliance", "Manage Dark War Survival alliances")
    @alliance.command(name="create", description="Create an alliance and its Discord role")
    async def alliance_create(i: discord.Interaction, name: str, tag: str, color: str = "#38BDF8"):
        bot.require(i, roles=bot.cfg(i.guild_id).alliances.management_roles)
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
            raise RuleError("Color must use #RRGGBB format.")
        await defer(i)
        role = await i.guild.create_role(name=f"[{tag}] {name}"[:100], color=discord.Color(int(color[1:], 16)), permissions=discord.Permissions.none(), reason=f"Alliance created by {i.user.id}")
        try:
            aid = bot.service.create_alliance(i.guild_id, i.user.id, name, tag, str(role.id))
        except Exception:
            await role.delete(reason="Alliance creation rolled back")
            raise
        await reply(i, f"Alliance #{aid} created. Roles will synchronize shortly.")

    @alliance.command(name="import", description="Import an existing role with an explicit leader")
    async def alliance_import(i: discord.Interaction, role: discord.Role, leader: discord.Member, name: str, tag: str):
        bot.require(i, roles=bot.cfg(i.guild_id).alliances.management_roles)
        role_safe(role, i.guild)
        if leader.bot or role not in leader.roles:
            raise RuleError("The leader must be a human member who already has this role.")
        await defer(i)
        await i.guild.chunk(cache=True)
        cfg = bot.cfg(i.guild_id).alliances
        roster = [(str(m.id), 5 if m.id == leader.id else 4 if has_roles(m, cfg.r4_roles) else 1) for m in role.members if not m.bot]
        aid = bot.service.create_alliance(i.guild_id, i.user.id, name, tag, str(role.id), owned=False, roster=roster)
        await reply(i, f"Imported {len(roster)} members into alliance #{aid}.")

    @alliance.command(name="delete", description="Delete an alliance after explicit confirmation")
    async def alliance_delete(i: discord.Interaction, name: str, confirm: bool = False):
        bot.require(i, "administrators")
        if not confirm:
            raise RuleError("Set confirm to True to delete this alliance and remove its member roles.")
        row = bot.service.alliance(i.guild_id, name)
        users = [r["user"] for r in bot.db.all("SELECT user FROM members WHERE alliance=? AND guild=?", (row["id"], str(i.guild_id)))]
        with bot.db.transaction():
            bot.db.execute("DELETE FROM alliances WHERE id=? AND guild=?", (row["id"], str(i.guild_id)))
            bot.db.enqueue(i.guild_id, "remove_alliance_role", {"role": row["role"], "owned": row["owned"], "users": users}, time.time())
            bot.service.sync_members(i.guild_id, users)
            bot.db.audit(i.guild_id, i.user.id, "alliances.deleted", {"alliance": row["id"]})
        await reply(i, "Alliance deleted. Role cleanup is queued; imported roles are preserved.")

    @alliance.command(name="invite", description="Add a member to your alliance as R1")
    async def alliance_invite(i: discord.Interaction, member: discord.Member):
        if member.bot:
            raise RuleError("Alliance members must be human users.")
        bot.service.invite(i.guild_id, i.user.id, member.id)
        await reply(i, "Member added. Roles will synchronize shortly.")

    def rank_action(action):
        async def run(i: discord.Interaction, member: discord.Member):
            bot.service.change_rank(i.guild_id, i.user.id, member.id, action)
            await reply(i, "Alliance updated. Roles will synchronize shortly.")
        return run
    for action in ("kick", "promote", "demote", "transfer"):
        alliance.command(name=action, description=f"{action.title()} a member in your alliance")(rank_action(action))

    @alliance.command(name="leave", description="Leave your current alliance")
    async def alliance_leave(i: discord.Interaction):
        bot.service.leave(i.guild_id, i.user.id)
        await reply(i, "You are no longer in an alliance.")

    @alliance.command(name="setleader", description="Set an alliance's R5 leader")
    async def alliance_setleader(i: discord.Interaction, name: str, member: discord.Member):
        bot.require(i, roles=bot.cfg(i.guild_id).alliances.management_roles)
        if member.bot:
            raise RuleError("The leader must be a human member.")
        row = bot.service.alliance(i.guild_id, name)
        bot.service.set_leader(i.guild_id, row["id"], member.id, i.user.id)
        await reply(i, "Leadership updated.")

    @alliance.command(name="list", description="List alliances in this server")
    async def alliance_list(i: discord.Interaction):
        rows = bot.db.all("SELECT a.*,COUNT(m.user) n FROM alliances a LEFT JOIN members m ON m.alliance=a.id WHERE a.guild=? GROUP BY a.id ORDER BY a.name", (str(i.guild_id),))
        await reply(i, embed=bot.embed(i.guild, "Alliances", "\n".join(f"[{r['tag']}] {r['name']} · {r['n']} members" for r in rows) or "No alliances yet."))

    @alliance.command(name="info", description="Show an alliance and its members")
    async def alliance_info(i: discord.Interaction, name: str | None = None):
        if name:
            row = bot.service.alliance(i.guild_id, name)
        else:
            member = bot.service.member(i.guild_id, i.user.id)
            row = bot.db.one("SELECT * FROM alliances WHERE id=? AND guild=?", (member["alliance"], str(i.guild_id))) if member else None
        if not row:
            raise RuleError("Specify an alliance, or join one first.")
        members = bot.db.all("SELECT * FROM members WHERE alliance=? AND guild=? ORDER BY rank DESC,joined", (row["id"], str(i.guild_id)))
        await reply(i, embed=bot.embed(i.guild, f"[{row['tag']}] {row['name']}", "\n".join(f"R{m['rank']} · <@{m['user']}>" for m in members) or "No members."))

    president = group("presidents", "president", "Manage the server presidency")
    @president.command(name="appoint", description="Appoint a president and end the previous term")
    async def appoint(i: discord.Interaction, member: discord.Member):
        cfg = bot.cfg(i.guild_id).presidents
        bot.require(i, roles=cfg.appointment_roles)
        if member.bot:
            raise RuleError("The president must be a human member.")
        if cfg.role_id:
            role_safe(i.guild.get_role(int(cfg.role_id)), i.guild)
        with bot.db.transaction():
            bot.db.execute("UPDATE presidents SET ended=? WHERE guild=? AND ended IS NULL", (time.time(), str(i.guild_id)))
            bot.db.execute("INSERT INTO presidents(guild,user,started) VALUES(?,?,?)", (str(i.guild_id), str(member.id), time.time()))
            bot.db.enqueue(i.guild_id, "president_sync", {}, time.time())
            bot.db.audit(i.guild_id, i.user.id, "presidents.appointed", {"user": str(member.id)})
        await reply(i, "President appointed. Role and announcement synchronization is queued.")

    @president.command(name="remove", description="End the current president's term")
    async def remove_president(i: discord.Interaction):
        bot.require(i, roles=bot.cfg(i.guild_id).presidents.appointment_roles)
        with bot.db.transaction():
            bot.db.execute("UPDATE presidents SET ended=? WHERE guild=? AND ended IS NULL", (time.time(), str(i.guild_id)))
            bot.db.enqueue(i.guild_id, "president_sync", {}, time.time())
            bot.db.audit(i.guild_id, i.user.id, "presidents.removed", {})
        await reply(i, "President's term ended.")

    @president.command(name="info", description="Show the current president")
    async def president_info(i: discord.Interaction):
        row = bot.db.one("SELECT * FROM presidents WHERE guild=? AND ended IS NULL", (str(i.guild_id),))
        await reply(i, f"President: <@{row['user']}> since <t:{int(row['started'])}:D>" if row else "No current president.")

    @president.command(name="history", description="Show the last ten presidential terms")
    async def president_history(i: discord.Interaction):
        rows = bot.db.all("SELECT * FROM presidents WHERE guild=? ORDER BY id DESC LIMIT 10", (str(i.guild_id),))
        await reply(i, embed=bot.embed(i.guild, "Presidential history", "\n".join(f"<@{r['user']}> · <t:{int(r['started'])}:D> · {'Active' if r['ended'] is None else 'Ended'}" for r in rows) or "No history."))

    @president.command(name="decree", description="Publish a presidential decree")
    async def decree(i: discord.Interaction, message: str, decree_type: Literal["General", "Economic", "Military", "Diplomatic"] = "General"):
        row = bot.db.one("SELECT * FROM presidents WHERE guild=? AND ended IS NULL", (str(i.guild_id),))
        if not row or row["user"] != str(i.user.id):
            raise RuleError("Only the current president can publish decrees.")
        if not 1 <= len(message) <= 2000:
            raise RuleError("Decree must contain 1–2000 characters.")
        await defer(i)
        await bot.channel(i.guild, bot.cfg(i.guild_id).presidents.channel_id).send(embed=bot.embed(i.guild, f"{decree_type} decree", message))
        await bot.emit(i.guild, i.user.id, "presidents", "decree", {"type": decree_type, "message": message})
        await reply(i, "Decree published.")

    armory = group("armory", "armory", "Manage weekly armory assignments")
    def armory_access(i):
        row = bot.db.one("SELECT user FROM presidents WHERE guild=? AND ended IS NULL", (str(i.guild_id),))
        if not admin(i.user) and (not row or row["user"] != str(i.user.id)):
            raise RuleError("Only administrators or the current president can assign armory slots.")

    @armory.command(name="assign", description="Assign an alliance to a slot in the current UTC week")
    async def armory_assign(i: discord.Interaction, slot: app_commands.Range[int, 1, 8], alliance_name: str):
        armory_access(i)
        row = bot.service.alliance(i.guild_id, alliance_name)
        bot.db.execute("INSERT INTO armory VALUES(?,?,?,?) ON CONFLICT(guild,week,slot) DO UPDATE SET alliance=excluded.alliance", (str(i.guild_id), week_key(), slot, row["id"]))
        await defer(i)
        from .jobs import refresh_armory
        await refresh_armory(bot, i.guild)
        await bot.emit(i.guild, i.user.id, "armory", "assigned", {"slot": slot, "alliance": row["id"]})
        await reply(i, "Armory slot assigned.")

    @armory.command(name="clear", description="Clear a slot in the current UTC week")
    async def armory_clear(i: discord.Interaction, slot: app_commands.Range[int, 1, 8]):
        armory_access(i)
        bot.db.execute("DELETE FROM armory WHERE guild=? AND week=? AND slot=?", (str(i.guild_id), week_key(), slot))
        await defer(i)
        from .jobs import refresh_armory
        await refresh_armory(bot, i.guild)
        await bot.emit(i.guild, i.user.id, "armory", "cleared", {"slot": slot})
        await reply(i, "Armory slot cleared.")

    apc = group("apc", "apc", "Report and view APC strength")
    @apc.command(name="submit", description="Report your main and second APC strength")
    async def apc_submit(i: discord.Interaction, main: str, second: str, main_faction: Faction | None = None, second_faction: Faction | None = None):
        bot.service.apc_set(i.guild_id, i.user.id, main, second, main_faction, second_faction)
        await defer(i)
        await bot.refresh_apc(i.guild)
        await bot.emit(i.guild, i.user.id, "apc", "updated", {"user": str(i.user.id)})
        await reply(i, "Your APC values have been saved.")

    @apc.command(name="view", description="Privately view your own strength")
    async def apc_view(i: discord.Interaction):
        row = bot.db.one("SELECT * FROM apc WHERE guild=? AND user=?", (str(i.guild_id), str(i.user.id)))
        await reply(i, f"Main: {row['main']:,}\nSecond: {row['second']:,}" if row else "No strength report yet.")

    @apc.command(name="leaderboard", description="Show the leaderboard, respecting its privacy setting")
    async def apc_leaderboard(i: discord.Interaction):
        cfg = bot.cfg(i.guild_id).apc
        if cfg.private:
            bot.require(i, roles=cfg.leaderboard_roles or None)
        await reply(i, embed=bot.apc_embed(i.guild))

    @add("apc", "apc-set", "Set a member's APC strength")
    async def apc_set(i: discord.Interaction, member: discord.Member, main: str, second: str, main_faction: Faction | None = None, second_faction: Faction | None = None):
        bot.require(i)
        bot.service.apc_set(i.guild_id, member.id, main, second, main_faction, second_faction)
        await defer(i)
        await bot.refresh_apc(i.guild)
        await bot.emit(i.guild, i.user.id, "apc", "manual_set", {"user": str(member.id)})
        await reply(i, "APC values updated.")

    @add("apc", "apc-remove", "Remove a specific member's APC entry")
    async def apc_remove(i: discord.Interaction, member: discord.Member):
        bot.require(i)
        bot.db.execute("DELETE FROM apc WHERE guild=? AND user=?", (str(i.guild_id), str(member.id)))
        await defer(i)
        await bot.refresh_apc(i.guild)
        await bot.emit(i.guild, i.user.id, "apc", "removed", {"user": str(member.id)})
        await reply(i, "APC entry removed.")

    event = group("events", "event", "Schedule game events")
    @event.command(name="schedule", description="Schedule a saved event using server-local or offset ISO time")
    @app_commands.describe(repeat_every="Repeat every 30m, 24h, 7d, or 2w; off for one occurrence")
    async def event_schedule(i: discord.Interaction, name: str, when: str, repeat_every: str | None = None):
        bot.require(i, "administrators")
        starts = local_timestamp(when, bot.cfg(i.guild_id).timezone)
        eid = bot.service.schedule(i.guild_id, name, starts, i.user.id, repeat_every=repeat_every)
        row = bot.db.one("SELECT series_id FROM events WHERE id=?", (eid,))
        suffix = f" Recurring series #{row['series_id']}; manage it with /event repeat." if row["series_id"] else ""
        await reply(i, f"Event #{eid} scheduled for <t:{int(starts)}:F>." + suffix)

    @event.command(name="create", description="Create an event and optionally repeat it automatically")
    @app_commands.describe(when="First start: YYYY-MM-DDTHH:MM in server timezone, or include a UTC offset", repeat_every="off, 30m, 24h, 7d, or 2w (minimum 5m)", reminder_minutes="Comma-separated minutes before each run; none disables reminders")
    async def event_create(i: discord.Interaction, name: str, channel: discord.TextChannel, when: str, message: str, repeat_every: str = "off", duration_minutes: app_commands.Range[int, 1, 43200] = 15, reminder_minutes: str = "60,10", keep_messages: bool = False):
        bot.require(i, "administrators")
        check_channel(i, channel)
        from pydantic import ValidationError
        try:
            offsets = [] if reminder_minutes.strip().lower() in ("", "none") else [int(v.strip()) for v in reminder_minutes.split(",")]
            definition = EventDefinition(name=name, channel_id=str(channel.id), template=Template(title=name, description=message), duration_minutes=duration_minutes, reminder_minutes=offsets, keep_messages=keep_messages)
        except (ValueError, ValidationError) as exc:
            raise RuleError("Use a name up to 80 characters, a message up to 4096, and up to 10 reminder offsets (1–10080 minutes).") from exc
        starts = local_timestamp(when, bot.cfg(i.guild_id).timezone)
        eid = bot.service.create_event(i.guild_id, definition, starts, i.user.id, repeat_every)
        sid = bot.db.one("SELECT series_id FROM events WHERE id=?", (eid,))["series_id"]
        await reply(i, f"Event #{eid} scheduled for <t:{int(starts)}:F>." + (f" Repeats every {repeat_every}. Series #{sid}; use /event repeat to manage it." if sid else ""))

    @event.command(name="series", description="List recurring event schedules and their next run")
    async def event_series(i: discord.Interaction):
        bot.require(i, "administrators")
        rows = bot.service.series_list(i.guild_id)
        lines = [f"#{r['id']} · {r['name']} · every {r['interval_seconds']//60}m · {r['status']}" + (f" · <t:{int(r['next_run'])}:F>" if r["next_run"] else "") for r in rows]
        await reply(i, embed=bot.embed(i.guild, "Recurring events", "\n".join(lines)[:4000] or "No recurring schedules."))

    @event.command(name="repeat", description="Pause, resume, stop, or change a recurring event schedule")
    @app_commands.describe(every="New interval for edit: 30m, 24h, 7d, 2w", when="Optional new first start for edit; otherwise keep the original anchor")
    async def event_repeat(i: discord.Interaction, series_id: int, action: Literal["pause", "resume", "stop", "edit"], every: str | None = None, when: str | None = None):
        bot.require(i, "administrators")
        starts = local_timestamp(when, bot.cfg(i.guild_id).timezone) if when else None
        bot.service.control_series(i.guild_id, series_id, action, i.user.id, repeat_every=every, starts=starts)
        await reply(i, f"Series #{series_id} updated ({action}). Runs already in progress finish normally.")

    @event.command(name="list", description="List upcoming scheduled events")
    async def event_list(i: discord.Interaction):
        rows = bot.db.all("SELECT * FROM events WHERE guild=? AND status='scheduled' AND starts>? ORDER BY starts LIMIT 10", (str(i.guild_id), time.time()))
        rows = [r for r in rows if i.guild.get_channel(int(r["channel"])) and i.guild.get_channel(int(r["channel"])).permissions_for(i.user).view_channel]
        await reply(i, embed=bot.embed(i.guild, "Upcoming events", "\n".join(f"#{r['id']} · {r['name']} · <t:{int(r['starts'])}:F>" for r in rows) or "No upcoming events."))

    @event.command(name="cancel", description="Cancel a scheduled or active event by ID")
    async def event_cancel(i: discord.Interaction, event_id: int):
        bot.require(i, "administrators")
        bot.service.cancel_event(i.guild_id, event_id, i.user.id)
        await reply(i, "This occurrence is cancelled. A recurring series continues; use /event repeat action:stop to stop the whole series.")

    duel = group("duel", "duel", "Daily alliance-duel reminders")
    @duel.command(name="preview", description="Privately preview today's configured duel strategy")
    async def duel_preview(i: discord.Interaction):
        from .jobs import duel_template
        template = duel_template(bot, i.guild)
        await reply(i, **bot.template(template))

    @duel.command(name="send", description="Send today's duel strategy now")
    async def duel_send(i: discord.Interaction):
        bot.require(i, "administrators")
        await defer(i)
        from .jobs import send_duel
        await send_duel(bot, i.guild)
        await reply(i, "Today's duel strategy sent.")

    ticket = group("tickets", "ticket", "Manage private support tickets")
    @ticket.command(name="panel", description="Deploy the support ticket panel")
    async def ticket_panel(i: discord.Interaction):
        bot.require(i, "administrators")
        await defer(i)
        message, _ = await bot.create_panel(i, "tickets", bot.channel(i.guild, i.channel_id), "Support desk", {"description": "Open a private conversation with the support team.", "categories": bot.cfg(i.guild_id).tickets.categories})
        await reply(i, f"Panel deployed: {message.jump_url}")

    @ticket.command(name="close", description="Close this ticket and save its configured transcript")
    async def ticket_close(i: discord.Interaction):
        row = bot.ticket_access(i)
        if bot.cfg(i.guild_id).tickets.close_confirmation:
            view = discord.ui.View(timeout=None)
            view.add_item(discord.ui.Button(label="Close this ticket", style=discord.ButtonStyle.danger, custom_id=f"s:close_ticket:{row['id']}:0"))
            await reply(i, "This closes and deletes the ticket channel. The configured transcript is saved first.", view=view)
        else:
            await defer(i)
            await bot.close_ticket(i, row["id"])

    def ticket_member(remove):
        async def run(i: discord.Interaction, member: discord.Member):
            row = bot.ticket_access(i)
            if member.bot or str(member.id) == row["owner"] or has_roles(member, bot.cfg(i.guild_id).tickets.support_roles):
                raise RuleError("This command cannot change access for the owner, bots, or support staff.")
            await defer(i)
            await i.channel.set_permissions(member, overwrite=discord.PermissionOverwrite(view_channel=False) if remove else discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True), reason=f"Ticket access changed by {i.user.id}")
            await bot.emit(i.guild, i.user.id, "tickets", "access_changed", {"ticket": row["id"], "user": str(member.id), "remove": remove})
            await reply(i, "Ticket access updated.")
        return run
    ticket.command(name="add", description="Add a participant to this ticket")(ticket_member(False))
    ticket.command(name="remove", description="Remove a participant from this ticket")(ticket_member(True))

    birthday = group("birthdays", "birthday", "Birthday calendar")
    @birthday.command(name="set", description="Set your birthday once; birth year is optional")
    async def birthday_set(i: discord.Interaction, month: app_commands.Range[int, 1, 12], day: app_commands.Range[int, 1, 31], year: app_commands.Range[int, 1900, 2100] | None = None):
        bot.service.birthday(i.guild_id, i.user.id, month, day, year)
        await reply(i, "Birthday saved.")

    @birthday.command(name="set-user", description="Set or correct another member's birthday")
    async def birthday_set_user(i: discord.Interaction, member: discord.Member, month: app_commands.Range[int, 1, 12], day: app_commands.Range[int, 1, 31], year: app_commands.Range[int, 1900, 2100] | None = None):
        bot.require(i)
        bot.service.birthday(i.guild_id, member.id, month, day, year, moderator=True)
        await bot.emit(i.guild, i.user.id, "birthdays", "corrected", {"user": str(member.id)})
        await reply(i, "Birthday updated.")

    @birthday.command(name="remove-user", description="Remove a birthday record")
    async def birthday_remove(i: discord.Interaction, member: discord.Member):
        if member.id != i.user.id:
            bot.require(i)
        bot.db.execute("DELETE FROM birthdays WHERE guild=? AND user=?", (str(i.guild_id), str(member.id)))
        await reply(i, "Birthday removed.")

    @birthday.command(name="upcoming", description="Show the next ten birthdays without birth years")
    async def birthday_upcoming(i: discord.Interaction):
        from datetime import date
        today = date.today()
        rows = bot.db.all("SELECT user,month,day FROM birthdays WHERE guild=?", (str(i.guild_id),))
        def offset(row):
            for year in range(today.year, today.year + 5):
                try:
                    candidate = date(year, row["month"], row["day"])
                except ValueError:
                    continue
                if candidate >= today:
                    return (candidate - today).days
            return 9999
        rows.sort(key=offset)
        await reply(i, "\n".join(f"<@{r['user']}> · {r['month']:02}/{r['day']:02}" for r in rows[:10]) or "No birthdays set.")

    from .translation import register_translation
    register_translation(bot, add)
