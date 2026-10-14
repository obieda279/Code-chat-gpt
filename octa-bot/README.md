# Sentinel

An independent Discord community and Dark War Survival bot, written from scratch using Arc Bot's public documentation as a functional reference.

**Status: runnable v0.2 implementation, not a verified one-to-one Arc Bot replacement.** This project includes a real Discord adapter, persistent SQLite storage, a Discord OAuth dashboard, a local demo, and automated checks. The signed-in Arc Bot dashboard, its private source, and live behavior were not accessed. Differences and unfinished parity items are recorded in [docs/PARITY.md](docs/PARITY.md).

## Included

- 19 configurable feature families; 67 slash commands and two message context actions.
- Moderation, automatic filtering, welcome/goodbye, audit channels, self-service roles, suggestions, polls, starboard, giveaways, message templates, support tickets, and birthdays.
- Alliance membership and R1/R4/R5 ranks, succession, presidents, weekly armory assignments, APC power reports, duel reminders, and recurring event schedules.
- Discord login, current membership/administrator checks, server isolation, CSRF protection, session expiry, configuration version conflicts, and a failed-job view with retry.
- Timers, votes, alliance state, and job retries persist across normal restarts.
- Optional DeepL text translation and Groq audio transcription adapters. These require separate provider credentials.

The bot and dashboard run together as **one Python process**. This is a self-hosted application; no service has been deployed and no Discord messages have been sent during development.

## Try the local demo on Windows

Install Python 3.12 or newer, unzip the project, and open PowerShell inside `sentinel-bot`:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m sentinel --demo
```

Open <http://localhost:8080> and choose **Explore the local demo**. The demo creates a separate `data/demo.db`, binds only to the local computer, and never connects to Discord. Stop it with Ctrl+C. Node.js is not needed to run either the bot or dashboard.

On macOS/Linux:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m sentinel --demo
```

## Connect your own Discord application

1. Create an application in the [Discord Developer Portal](https://discord.com/developers/applications). Create its bot and obtain its token, application/client ID, and OAuth client secret.
2. In the bot settings, enable **Server Members Intent** and **Message Content Intent**. Membership synchronization, welcome events, and message filters depend on these. Do not enable Presence Intent; this project does not use it.
3. Copy `.env.example` to `.env`. Set `DISCORD_TOKEN`, `DISCORD_CLIENT_ID`, and `DISCORD_CLIENT_SECRET`. Keep this file private.
4. For local use, keep `PUBLIC_URL=http://localhost:8080`. Add exactly `http://localhost:8080/auth/callback` to the application's OAuth redirect URLs.
5. Print an installation link:

```powershell
.\.venv\Scripts\python.exe -m scripts.invite
```

6. Open that link and install the bot in a test server you administer. The link requests the permissions needed by the feature set; it does not request Administrator. Reduce permissions if you are disabling the corresponding modules.
7. Move the bot's role above the roles and members it must manage. Discord role hierarchy still applies to administrators and to this bot.
8. Run:

```powershell
.\.venv\Scripts\python.exe -m sentinel
```

9. Visit the dashboard and sign in with Discord. Select your server, enable your modules, configure channels/roles, and save. New servers start with every feature module disabled. `/dashboard` and `/status` remain available for setup.
10. Open Discord and type `/`. Command synchronization checks enabled modules every five seconds; Discord client refresh and API limits can add delay.

Dashboard login uses the `identify` OAuth scope. Server access is checked using the bot's installed servers and a fresh member lookup. Discord access/refresh tokens are not stored in the database or browser. Dashboard sessions expire after eight hours. Administrators can edit; explicitly delegated dashboard users can view.

## Configure a first alliance server

1. In **Server settings**, set the server timezone, for example `Asia/Amman`.
2. In **Moderation**, add moderator role IDs. Native Discord administrators are also allowed to moderate in this implementation.
3. In **Alliances**, set management, R4, and R5 roles. Use dedicated roles without native staff permissions for automated assignment.
4. Enable **APC rankings**, select a leaderboard channel, and decide whether the ranking should be private.
5. Enable **Presidents** and **Armory rotation**, then choose their announcement channels.
6. Use these commands in Discord:

```text
/alliance create name:Night Watch tag:WATCH
/alliance invite member:@Player
/alliance promote member:@Player
/apc submit main:1.5G second:800M
/president appoint member:@Leader
/armory assign slot:1 alliance_name:WATCH
```

All game records are maintained from Discord input. There is no connection to a private Dark War Survival game API and no automatic game-account scraping.

## Templates, panels, and schedules

The dashboard has direct controls for flags, channels, timezones, and numeric settings. Advanced collections use JSON editors. Use [docs/example-config.json](docs/example-config.json) for working structures and [docs/config-schema.json](docs/config-schema.json) for validation rules. Replace sample IDs with your server's IDs; the dashboard provides a channel/role reference.

Create named role panels under **Self-service roles**, then deploy with `/roles panel name:Interests`. Buttons, dropdowns, and classic reactions are supported. Changing a saved panel updates its existing messages through reconciliation. Removing a panel disables its saved interactions. Renaming it retires the old panel; deploy the new name. Stale emoji reactions may remain visually on an edited message, but removed mappings no longer grant roles.

Add templates under **Message templates**, then use `/embed send name:Alliance briefing`. Images must be HTTPS URLs. Content is rendered with mention suppression by default; templates cannot mass-ping your server.

Open **Events** in the dashboard to create an event without JSON. Enable **Scheduled events** under **Event settings** and save first. Choose a channel, first start time, message, duration, and reminder offsets. Turn on **Repeat automatically**, then choose any whole-number interval in minutes, hours, days, or weeks (5 minutes to 365 days). For example: start on December 1 at 20:00, then repeat every 2 weeks. Reminders are reused on every run.

The same feature is available in Discord:

```text
/event create name:Alliance briefing channel:#announcements when:2026-12-01T20:00 message:Gather {countdown} repeat_every:2w
/event series
/event repeat series_id:1 action:pause
/event repeat series_id:1 action:resume
/event repeat series_id:1 action:edit every:12h
/event repeat series_id:1 action:stop
```

Use a future date. An input without an offset uses the saved server timezone. An explicit offset in a command, such as `2026-12-01T20:00+03:00`, takes precedence. Ambiguous or nonexistent daylight-saving times are rejected. Recurrence uses elapsed time: a day is 24 hours and a week is 7 days. The displayed local clock time can change across daylight-saving transitions.

**Pause**, **Resume**, **Edit timing**, and **Stop series** are also available on each dashboard schedule. Cancelling an individual occurrence leaves its series running. Existing runs finish normally when a series is paused or stopped. The schedule persists across restarts and stays anchored to its first start, independent of delivery delays or event duration. Missed cycles are skipped after downtime. The bot must remain running to send notifications; local demo mode saves schedules but sends nothing.

Saved named definitions still work with `/event schedule name:Alliance briefing when:2026-12-01T20:00 repeat_every:7d`. The old `repeat_hours` field remains supported when no command override is supplied. Use `repeat_every:off` for a one-time occurrence. See [docs/RECURRENCE.md](docs/RECURRENCE.md) for upgrade and timing details.

Duel template keys are `"1"` through `"7"` for Monday through Sunday. Author your own strategies. `/duel preview` shows today's template privately; `/duel send` publishes it. No proprietary strategy templates are included.

## Tickets and privacy

Configure support roles before deploying `/ticket panel`. Ticket channels deny `@everyone` and explicitly grant the owner, support staff, and bot access. Discord administrators can always see them. Owners and support staff can add/remove participants; unrelated members cannot manage a ticket.

If you configure a transcript channel, it must deny `@everyone` View Channel. Restrict its other roles appropriately. The bot saves a plain-text transcript before deleting a closed ticket; an archive failure leaves the source channel available for retry. Transcripts contain the most recent 10,000 messages in reverse chronological order and attachment links, not permanent copies of attachments. A 7.5 MB limit prevents an oversized transcript from causing silent truncation. Without a configured transcript channel, closing deletes the channel without an archive.

Birthday years are optional and are not displayed by `/birthday upcoming`. Members can remove their own birthday. February 29 birthdays are celebrated only on February 29. Birthday greetings can mention the birthday member; general bot messages suppress mentions.

Translation sends the selected text to DeepL; voice translation also sends the selected audio attachment to Groq. These modules are disabled until you enable them and configure operator-owned API keys in `.env`. Usage is limited per user per server per hour while the process is running. This is not a provider spending cap. Check provider billing and account limits before enabling translation widely.

## Hosting

For an always-on host, use Python directly under a process manager or the included Docker setup:

```bash
docker compose up --build -d
```

The compose service exposes port 8080 on the host's loopback interface only and retains data in a named volume. For remote access, place an HTTPS reverse proxy in front of it. Set `PUBLIC_URL=https://your-bot-domain.example`, register that exact origin plus `/auth/callback` in Discord, and preserve the public Host header through the proxy. Public HTTP origins are rejected. For a reverse proxy in another container, adapt network routing accordingly.

Run exactly **one application instance per database**. This project is not configured for horizontal replicas, multiple gateway sessions, or sharding. SQLite writes are short synchronous transactions; a high-volume public bot should move these operations to an asynchronous database layer and a separate worker design.

Keep `.env` and `data/` outside source control. Back up a running SQLite database with its backup API rather than copying only the main file:

```bash
python -m scripts.backup data/sentinel.db backups/sentinel-2026-12-01.db
```

Stop the application before restoring a backup. Keep backups private: the database contains membership, warning, poll, ticket, and birthday records. Audit rows are retained for 90 days. Closed panels, job history, and historical game records remain until an operator removes them. No retention UI is provided yet.

## Verification

```bash
python -m unittest discover -s tests -v
node --check sentinel/static/app.js
```

Optional dashboard DOM checks require Node 22 or newer:

```bash
npm install
npm run test:ui
```

See [docs/TEST_REPORT.md](docs/TEST_REPORT.md) for the checks actually run and the untested integration boundaries. Tests use local databases, HTTP test clients, and simulated Discord objects. They do not send real messages, ban anyone, create real roles, or call paid providers.

## Project layout

| Path | Responsibility |
| --- | --- |
| `sentinel/config.py` | Validated configuration and dashboard JSON schema |
| `sentinel/db.py` | SQLite schema, transactions, audit records, job leases |
| `sentinel/domain.py` | Alliance, voting, APC, birthday and scheduling rules |
| `sentinel/bot.py` | Discord lifecycle, events, components, permissions and private tickets |
| `sentinel/commands.py` | Slash commands and permission checks |
| `sentinel/jobs.py` | Durable delivery, recurrence, cleanup and reconciliation |
| `sentinel/automod.py` | Message filter rules |
| `sentinel/translation.py` | Optional external provider adapters |
| `sentinel/web.py` | Dashboard HTTP API, sessions and Discord OAuth |
| `sentinel/static/` | Original dashboard HTML, CSS and JavaScript |
| `tests/` | Domain, authorization, command-contract, worker and DOM checks |
| `docs/COMMANDS.md` | Generated reference for the actual registered commands |
| `docs/PARITY.md` | Public-reference coverage and remaining differences |

The source is licensed under MIT. Sentinel is independent of Arc Bot and has no affiliation with its operator. Public references informed behavior; Arc Bot's source code, visual assets, and private data are not included.
