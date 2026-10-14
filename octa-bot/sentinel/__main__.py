import argparse
import asyncio
import logging
import signal
import time

from aiohttp import web
from .config import GuildConfig, Template, EventDefinition
from .db import Database
from .domain import Service
from .settings import Settings
from .web import Dashboard, DEMO_GUILD, DEMO_USER


def seed_demo(db):
    if db.config(DEMO_GUILD)[1]:
        return
    config = GuildConfig(timezone="Asia/Amman")
    for module in ["moderation", "automod", "welcome", "logs", "polls", "tickets", "alliances", "presidents", "armory", "apc", "duel", "events"]:
        getattr(config, module).enabled = True
    config.moderation.roles = ["200000000000000001"]
    config.welcome.channel_id = "300000000000000001"
    config.events.definitions = [EventDefinition(name="Alliance briefing", template=Template(title="Alliance briefing", description="Meet the team {countdown}."))]
    config.messages.templates = [Template(name="Weekly update", title="A new week together", description="Add your community news here.")]
    config.duel.templates = {str(day): Template(name=f"Day {day}", title="Daily duel plan", description="Add your alliance's strategy for today.") for day in range(1, 8)}
    db.save_config(DEMO_GUILD, config, 0, DEMO_USER)
    Service(db).create_alliance(DEMO_GUILD, DEMO_USER, "Night Watch", "WATCH", "200000000000000005")
    Service(db).create_alliance(DEMO_GUILD, "100000000000000003", "North Star", "NORTH", "200000000000000006")
    # Fake queued role actions are intentionally not executed in demo mode.
    db.execute("UPDATE jobs SET state='done'")


async def run(settings):
    db = Database(settings.database)
    bot = None
    if settings.demo:
        seed_demo(db)
    else:
        from .bot import Sentinel
        bot = Sentinel(db, settings)
    dashboard = Dashboard(db, settings, bot)
    runner = web.AppRunner(dashboard.app(), access_log=None)
    await runner.setup()
    await web.TCPSite(runner, settings.host, settings.port).start()
    logging.info("Dashboard available at %s%s", settings.public_url, " (local demo)" if settings.demo else "")
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stopped.set)
        except (NotImplementedError, RuntimeError):
            pass
    wait_stop = asyncio.create_task(stopped.wait())
    bot_task = asyncio.create_task(bot.start(settings.token)) if bot else None
    try:
        tasks = [wait_stop] + ([bot_task] if bot_task else [])
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        if bot_task in done:
            bot_task.result()
    finally:
        wait_stop.cancel()
        if bot:
            await bot.close()
        if bot_task:
            bot_task.cancel()
            await asyncio.gather(bot_task, return_exceptions=True)
        await runner.cleanup()
        db.close()


def main():
    parser = argparse.ArgumentParser(description="Sentinel Discord bot and dashboard")
    parser.add_argument("--demo", action="store_true", help="Local dashboard demo; no Discord connection")
    parser.add_argument("--port", type=int, help="Dashboard listening port")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        asyncio.run(run(Settings.from_env(args.demo, args.port)))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
