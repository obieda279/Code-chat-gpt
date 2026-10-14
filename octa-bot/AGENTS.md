# Octa project instructions

Read CODEX_HANDOFF.md and the existing README.md before making changes. Continue this implementation; do not restart it from scratch.

The user chose **Octa** as the public bot name. The existing Python package is still `sentinel` and the source release is 0.2.0. Public branding remains a pending task. Keep startup commands and stored data compatible when changing branding.

The goal is an independent Discord community bot for leaders in Dark War: Survival and other survival games, using Arc Bot's public features as a reference. Full Arc parity has not been established. Consult docs/PARITY.md; do not describe incomplete or unverified features as finished.

Configurable recurring events are a core requirement: set the first start and an interval once, reuse reminders, and allow series management without manually recreating events. Preserve persistence, cancellation semantics, and restart behavior documented in docs/RECURRENCE.md.

Use the existing Python / discord.py / aiohttp / SQLite architecture. One process owns the bot, dashboard, and workers. Never put credentials in source, logs, or chat; use a local .env. No live Discord deployment has been verified.

For relevant code changes, run the existing Python tests and, for dashboard changes, the UI check where dependencies are available. Report what was actually tested and any blockers. Keep updates concise and follow the user's current request directly.
