from __future__ import annotations

import json
import time
from datetime import datetime
from zoneinfo import ZoneInfo
import discord
from .bot import role_safe
from .domain import RuleError, week_key


async def delete_ref(bot, guild, key):
    ref = bot.db.ref(guild.id, key)
    if ref:
        channel = guild.get_channel(int(ref["channel"]))
        if channel:
            try:
                await channel.get_partial_message(int(ref["message"])).delete()
            except discord.NotFound:
                pass
        bot.db.execute("DELETE FROM refs WHERE guild=? AND key=?", (str(guild.id), key))


def duel_template(bot, guild):
    cfg = bot.cfg(guild.id)
    day = str(datetime.now(ZoneInfo(cfg.timezone)).isoweekday())
    template = cfg.duel.templates.get(day)
    if not template:
        raise RuleError("Configure a duel template for today's weekday in the dashboard.")
    return template


async def send_duel(bot, guild):
    cfg = bot.cfg(guild.id)
    template = duel_template(bot, guild)
    channel = bot.channel(guild, cfg.duel.channel_id)
    if cfg.duel.clean_previous:
        await delete_ref(bot, guild, "duel")
    message = await channel.send(**bot.template(template))
    bot.db.ref(guild.id, "duel", {"channel": str(channel.id), "message": str(message.id)})
    bot.db.ref(guild.id, "duel_date", datetime.now(ZoneInfo(cfg.timezone)).date().isoformat())


async def refresh_armory(bot, guild):
    cfg = bot.cfg(guild.id).armory
    if not cfg.enabled or not cfg.channel_id:
        return
    rows = bot.db.all("SELECT s.slot,a.name,a.tag FROM armory s JOIN alliances a ON a.id=s.alliance WHERE s.guild=? AND s.week=? ORDER BY s.slot", (str(guild.id), week_key()))
    assigned = {r["slot"]: f"[{r['tag']}] {r['name']}" for r in rows}
    text = "\n".join(f"**Slot {i}** · {assigned.get(i, 'Unassigned')}" for i in range(1, 9))
    await bot.upsert_message(guild, "armory", bot.channel(guild, cfg.channel_id), bot.embed(guild, "Armory · week of " + week_key(), text))


async def execute_event(bot, guild, job):
    payload = job["payload"]
    row = bot.db.one("SELECT * FROM events WHERE id=? AND guild=?", (payload["event"], str(guild.id)))
    if not row or row["status"] == "ended":
        return
    definition = json.loads(row["definition"])
    now, phase = time.time(), payload["phase"]
    if row["status"] in ("cancelled", "suspended"):
        if phase == "end" and not definition["keep_messages"]:
            await delete_ref(bot, guild, f"event:{row['id']}")
        return
    if not bot.cfg(guild.id).events.enabled:
        if phase == "end":
            if not definition["keep_messages"]:
                await delete_ref(bot, guild, f"event:{row['id']}")
            bot.db.execute("UPDATE events SET status='ended' WHERE id=?", (row["id"],))
        elif phase == "start":
            bot.db.execute("UPDATE events SET status='missed' WHERE id=?", (row["id"],))
        return
    if phase == "end":
        if not definition["keep_messages"]:
            await delete_ref(bot, guild, f"event:{row['id']}")
        with bot.db.transaction():
            bot.db.execute("UPDATE events SET status='ended' WHERE id=?", (row["id"],))
            # Scheduling the next occurrence and closing this one must commit together.
            # A separate job lets Service.schedule own its transaction.
            if not row["series_id"] and definition.get("repeat_hours"):
                bot.db.enqueue(guild.id, "repeat_event", {"event": row["id"]}, now, f"repeat:{row['id']}")
        return
    if row["status"] == "missed":
        return
    if phase == "reminder" and (now >= row["starts"] or now - job["due"] > 120):
        return
    if phase == "start" and (now >= row["ends"] or now - row["starts"] > 3600):
        bot.db.execute("UPDATE events SET status='missed' WHERE id=?", (row["id"],))
        return
    if phase == "interval" and (now >= row["ends"] or row["status"] != "started"):
        return
    key = f"event:{row['id']}"
    if not definition["keep_messages"]:
        await delete_ref(bot, guild, key)
    content = bot.template(definition["template"], {"countdown": f"<t:{int(row['starts'])}:R>", "event": row["name"]})
    content["embed"].set_footer(text="Starting now" if phase == "start" else "Event in progress" if phase == "interval" else "Upcoming event")
    message = await bot.channel(guild, row["channel"]).send(**content)
    bot.db.ref(guild.id, key, {"channel": row["channel"], "message": str(message.id)})
    if phase == "start":
        bot.db.execute("UPDATE events SET status='started' WHERE id=?", (row["id"],))
    if phase == "interval" and definition.get("interval_minutes"):
        due = max(job["due"] + definition["interval_minutes"] * 60, now + definition["interval_minutes"] * 60)
        if due < row["ends"]:
            bot.db.enqueue(guild.id, "event", {"event": row["id"], "phase": "interval"}, due, f"event:{row['id']}:interval:{int(due)}")


async def execute_job(bot, guild, job):
    kind, data = job["kind"], job["payload"]
    if kind == "sync_member":
        await bot.sync_member(guild, data["user"], data.get("retired_roles"))
    elif kind == "remove_alliance_role":
        role = guild.get_role(int(data["role"])) if data["role"] else None
        if role:
            if data["owned"]:
                await role.delete(reason="Alliance removed")
            else:
                for uid in data["users"]:
                    member = guild.get_member(int(uid))
                    if member and role in member.roles:
                        await member.remove_roles(role, reason="Alliance removed; imported role preserved")
    elif kind == "log":
        if bot.cfg(guild.id).logs.enabled:
            await bot.channel(guild, data["channel"]).send(embed=bot.embed(guild, data["title"], data["description"], data["color"]))
    elif kind == "slowmode":
        channel = bot.channel(guild, data["channel"])
        if bot.db.ref(guild.id, f"slowmode:{channel.id}") == data["generation"] and channel.slowmode_delay == data["expected"]:
            await channel.edit(slowmode_delay=data["old"], reason="Scheduled slowmode restoration")
    elif kind == "close_panel":
        panel = bot.service.panel(guild.id, data["panel"])
        if panel["kind"] == "giveaways":
            if not guild.chunked:
                await guild.chunk(cache=True)
            bot.service.draw(guild.id, panel["id"], {str(m.id) for m in guild.members if not m.bot})
        else:
            bot.db.execute("UPDATE panels SET closed=1 WHERE id=?", (panel["id"],))
        try:
            await bot.update_panel(guild, panel["id"])
        except discord.NotFound:
            pass
    elif kind == "render_panel":
        panel = bot.service.panel(guild.id, data["panel"])
        try:
            await bot.update_panel(guild, panel["id"])
            if not panel["closed"] and panel["kind"] == "reaction_roles" and panel["payload"]["mode"] == "reactions":
                message = bot.channel(guild, panel["channel"]).get_partial_message(int(panel["message"]))
                for mapping in panel["payload"]["mappings"]:
                    await message.add_reaction(mapping["emoji"])
        except discord.NotFound:
            bot.db.execute("UPDATE panels SET closed=1 WHERE id=?", (panel["id"],))
    elif kind == "president_sync":
        cfg = bot.cfg(guild.id).presidents
        current = bot.db.one("SELECT * FROM presidents WHERE guild=? AND ended IS NULL", (str(guild.id),))
        for rid in data.get("retired_roles", []):
            role = guild.get_role(int(rid))
            if role and rid != cfg.role_id:
                role_safe(role, guild)
                if not guild.chunked:
                    await guild.chunk(cache=True)
                for member in role.members:
                    await member.remove_roles(role, reason="President role configuration changed")
        if cfg.role_id:
            role = guild.get_role(int(cfg.role_id))
            role_safe(role, guild)
            if not guild.chunked:
                await guild.chunk(cache=True)
            for member in role.members:
                if not current or str(member.id) != current["user"]:
                    await member.remove_roles(role, reason="Presidential term ended")
            if current:
                member = guild.get_member(int(current["user"])) or await guild.fetch_member(int(current["user"]))
                if role not in member.roles:
                    await member.add_roles(role, reason="President appointed")
        if cfg.channel_id:
            await bot.upsert_message(guild, "president", bot.channel(guild, cfg.channel_id), bot.embed(guild, "Server president", f"<@{current['user']}>" if current else "No current president."))
    elif kind == "duel":
        cfg = bot.cfg(guild.id)
        today = datetime.now(ZoneInfo(cfg.timezone)).date().isoformat()
        if cfg.duel.enabled and data["date"] == today and bot.db.ref(guild.id, "duel_date") != today:
            await send_duel(bot, guild)
    elif kind == "birthday":
        cfg = bot.cfg(guild.id)
        if not cfg.birthdays.enabled or datetime.now(ZoneInfo(cfg.timezone)).date().isoformat() != data["date"]:
            return
        row = bot.db.one("SELECT * FROM birthdays WHERE guild=? AND user=?", (str(guild.id), data["user"]))
        if not row:
            return
        try:
            member = guild.get_member(int(data["user"])) or await guild.fetch_member(int(data["user"]))
        except discord.NotFound:
            return
        message = cfg.birthdays.message.replace("{user}", member.mention).replace("{server}", guild.name)
        await bot.channel(guild, cfg.birthdays.channel_id).send(message[:2000], allowed_mentions=discord.AllowedMentions(users=[member], roles=False, everyone=False))
    elif kind == "event":
        await execute_event(bot, guild, job)
    elif kind == "repeat_event":
        old = bot.db.one("SELECT * FROM events WHERE guild=? AND id=?", (str(guild.id), data["event"]))
        if not old or old["series_id"] or old["status"] == "cancelled" or not bot.cfg(guild.id).events.enabled:
            return
        # The original event's interval anchors repeats; skip missed historical cycles.
        interval = json.loads(old["definition"])["repeat_hours"] * 3600
        starts = old["starts"] + interval
        if starts <= time.time():
            starts += (int((time.time() - starts) // interval) + 1) * interval
        key = f"repeated:{old['id']}"
        if not bot.db.ref(guild.id, key):
            # Repeats use the current named definition, so edits apply to the next cycle.
            bot.service.schedule(guild.id, old["name"], starts, "scheduler", dedupe_key=key)
    elif kind == "ticket_autoclose":
        cfg = bot.cfg(guild.id).tickets
        row = bot.db.one("SELECT * FROM tickets WHERE guild=? AND id=?", (str(guild.id), data["ticket"]))
        if cfg.enabled and cfg.auto_close_hours and row and row["status"] in ("open", "closing") and row["updated"] <= time.time() - cfg.auto_close_hours * 3600:
            await bot.archive_ticket(guild, row, "inactivity timer")
    elif kind == "reconcile":
        await bot.refresh_apc(guild)
        await refresh_armory(bot, guild)
        cfg = bot.cfg(guild.id)
        # Existing interactive role panels follow edits by their saved names.
        for row in bot.db.all("SELECT id FROM panels WHERE guild=? AND kind='reaction_roles' AND closed=0", (str(guild.id),)):
            panel = bot.service.panel(guild.id, row["id"])
            replacement = next((p for p in cfg.reaction_roles.panels if p.name == panel["payload"].get("name")), None)
            if replacement is not None and replacement.model_dump() == panel["payload"]:
                continue
            with bot.db.transaction():
                if replacement is None:
                    bot.db.execute("UPDATE panels SET closed=1 WHERE id=?", (row["id"],))
                else:
                    bot.db.execute("UPDATE panels SET title=?,payload=? WHERE id=?", (replacement.title, replacement.model_dump_json(), row["id"]))
                bot.db.enqueue(guild.id, "render_panel", {"panel": row["id"]}, time.time())
        if cfg.events.enabled and cfg.events.calendar_channel_id:
            channel = bot.channel(guild, cfg.events.calendar_channel_id)
            rows = bot.db.all("SELECT * FROM events WHERE guild=? AND status='scheduled' AND starts>? ORDER BY starts LIMIT 30", (str(guild.id), time.time()))
            # Only same-channel or public events may enter this calendar.
            rows = [r for r in rows if guild.get_channel(int(r["channel"])) and (r["channel"] == str(channel.id) or guild.get_channel(int(r["channel"])).permissions_for(guild.default_role).view_channel)][:10]
            await bot.upsert_message(guild, "calendar", channel, bot.embed(guild, "Upcoming events", "\n".join(f"{r['name']} · <t:{int(r['starts'])}:R>" for r in rows) or "No upcoming events."))
    else:
        raise RuleError("Unrecognized background job type.")


async def maintain(bot, guild):
    cfg = bot.cfg(guild.id)
    now = time.time()
    bot.service.advance_series(guild.id, now)
    local = datetime.now(ZoneInfo(cfg.timezone))
    today = local.date().isoformat()
    for module in ("duel", "birthdays"):
        settings = getattr(cfg, module)
        if settings.enabled and settings.channel_id and (local.hour, local.minute) >= (settings.send_hour, settings.send_minute):
            if module == "duel" and str(local.isoweekday()) in settings.templates:
                bot.db.enqueue(guild.id, "duel", {"date": today}, now, f"duel:{guild.id}:{today}")
            elif module == "birthdays":
                for row in bot.db.all("SELECT user FROM birthdays WHERE guild=? AND month=? AND day=?", (str(guild.id), local.month, local.day)):
                    bot.db.enqueue(guild.id, "birthday", {"date": today, "user": row["user"]}, now, f"birthday:{guild.id}:{today}:{row['user']}")
    if cfg.moderation.expire_warning_days:
        bot.db.execute("DELETE FROM warnings WHERE guild=? AND created<?", (str(guild.id), now - cfg.moderation.expire_warning_days * 86400))
    if cfg.tickets.enabled and cfg.tickets.auto_close_hours:
        for row in bot.db.all("SELECT * FROM tickets WHERE guild=? AND status IN ('open','closing') AND updated<?", (str(guild.id), now - cfg.tickets.auto_close_hours * 3600)):
            bot.db.enqueue(guild.id, "ticket_autoclose", {"ticket": row["id"]}, now, f"ticket-close:{row['id']}:{int(row['updated'])}")
    bot.db.enqueue(guild.id, "reconcile", {}, now, f"reconcile:{guild.id}:{int(now // 180)}")
    bot.db.execute("DELETE FROM sessions WHERE expires<?", (now,))
    bot.db.execute("DELETE FROM audit WHERE guild=? AND created<?", (str(guild.id), now - 90 * 86400))
