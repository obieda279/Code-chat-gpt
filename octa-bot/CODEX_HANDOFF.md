# Octa — Codex handoff

## Start here

This is the existing runnable source release 0.2.0, originally named Sentinel. Its 40 original files are unchanged in this bundle. The outer folder is now `octa-bot`; Python imports and the run command still use `sentinel`. These notes and the logo draft were added for continuity.

Suggested first instruction to Codex:

> Read AGENTS.md and CODEX_HANDOFF.md. Continue Octa from this codebase. Review the current state, run the existing checks, and help me get it running locally. Preserve recurring events and work through the remaining Octa branding.

## User requirements and preferences

- Public name: **Octa**.
- Purpose: help survival-game leaders manage Discord communities, alliances, and events, especially Dark War: Survival.
- Original ambition: build an independent Arc Bot replacement from scratch. Public references: https://www.arcbot.pro/dashboard and https://docs.arcbot.pro/en. The private dashboard and source were not accessed. Full parity is unfinished; see docs/PARITY.md.
- Events must repeat automatically at a user-configured interval, similar to Carl-bot autofeeds, without entering each occurrence again.
- Logo preference: a cool abstract design inspired by Arc's visual style. The latest draft is assets/octa-logo-draft.png: a white octagonal O with an orange-to-purple swoosh on a dark background. Final approval has not been recorded. Do not replace it with a robot mascot.
- Communicate directly and honor the user's inputs. For short app descriptions, provide plain copyable text.

App description:

Octa is a Discord bot that helps leaders in Dark War: Survival and other survival games manage their communities, coordinate alliances, organize events, and set up recurring schedules and automated reminders.

## Architecture and current implementation

- Python 3.12+, discord.py, aiohttp dashboard, SQLite with WAL. Bot, web server, and workers run in one process.
- It uses a real Discord bot connection and Discord API calls, not channel webhooks as its scheduler.
- Discord OAuth dashboard, server membership / administrator checks, CSRF protection, and server-separated configuration.
- 19 feature families; 67 slash-command leaves and two message context actions. See README.md and docs/COMMANDS.md for details.
- Features include moderation, automod, roles, polls, suggestions, tickets, giveaways, birthdays, alliances, presidents, armory, APC, duel, and events. Game data comes from Discord input; there is no private game API integration.
- Persistent jobs use retries and leases. Delivery is at least once: a crash around a Discord send can cause a duplicate.
- Keep one application instance per SQLite database. Do not await within synchronous database transactions.

## Recurring events

Implemented and documented in docs/RECURRENCE.md:

- First start plus `repeat_every` in minutes, hours, days, or weeks; supported intervals from five minutes through 365 days.
- Persisted series and occurrences survive restart. Missed cycles are skipped, rather than replayed in a burst.
- Pause, resume, edit timing, or stop a series. Cancelling one occurrence leaves the series active.
- Recurrence follows fixed elapsed intervals. Daylight-saving changes can shift the local wall-clock hour.
- Message, channel, duration, and reminders are captured in the series definition. Changing content currently requires stopping and recreating the series.
- SQLite schema version 2 adds series support. Existing records are preserved; legacy repeat jobs are adopted on their next successful repeat.
- The demo saves configuration and schedules but does not send Discord messages or run the live maintenance loop.

## Run locally on Windows

Open PowerShell in this `octa-bot` folder:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m sentinel --demo
```

Visit http://localhost:8080. Stop with Ctrl+C. Node.js is not required to run the application.

Optional dashboard DOM check (Node 22+):

```powershell
npm install
npm run test:ui
```

For macOS/Linux, create a Python 3.12 virtual environment and use `.venv/bin/python` in place of the Windows interpreter path. See README.md.

## Real Discord setup

No credentials, active deployment, live server verification, or connected Git repository are included. The user chose the application name Octa; completion of Developer Portal registration is unverified.

Follow README.md and verify current official Discord requirements when setting up a live application. Copy `.env.example` to `.env` locally and configure DISCORD_TOKEN, DISCORD_CLIENT_ID, and DISCORD_CLIENT_SECRET. Never ask the user to paste secrets into chat.

For local OAuth, PUBLIC_URL defaults to http://localhost:8080 and the callback is http://localhost:8080/auth/callback. The current implementation needs Server Members and Message Content intents. Use `python -m scripts.invite` to print the installation link and `python -m sentinel` to run the real bot. Install in a server the user administers. New guilds start with feature modules disabled; configure them in the dashboard.

For eventual 24/7 hosting, the repository includes Dockerfile and compose.yaml. Preserve the SQLite volume; public dashboard hosting needs HTTPS and matching PUBLIC_URL / OAuth callback configuration. Hosting selection and deployment remain future work.

## Verification and next work

The implementation's last recorded validation passed 67 Python tests, command serialization, the dashboard DOM flow, Python compilation, and JavaScript syntax checks. See docs/TEST_REPORT.md. Those results predate this packaging step; the handoff packaging only verifies source byte identity and ZIP integrity.

Not verified: live Discord behavior, Windows-native execution, Docker image build, paid provider integrations, load behavior, and full browser visual checks.

Suggested continuation:

1. Review existing code and run checks in the new environment.
2. Apply Octa public branding safely; preserve package imports and existing database paths unless deliberately migrated.
3. Integrate the latest logo draft into the dashboard and check small-size readability.
4. Complete local setup and a real test-server trial once credentials are configured locally.
5. Continue feature parity using docs/PARITY.md and authorized public references.

These are suggested next steps, not claims of completed work or an instruction to provision paid hosting.
