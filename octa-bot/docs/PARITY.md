# Public-reference coverage and differences

Research date: October 14, 2026. This is a feature-oriented reimplementation from public documentation. The requested dashboard URL was not verified in a signed-in session. This document distinguishes implemented behavior from exact product parity, which remains unverified.

## Feature map

| Feature family | Implemented here | Remaining difference or qualification |
| --- | --- | --- |
| Core/dashboard | Discord OAuth, server selection, 19 module switches, editable configuration, version conflicts, audit and failed-job views | Original UI, English only; complex collections use JSON instead of a complete visual editor. Delegates are read-only. No operator-wide broadcast/admin console. |
| Moderation | Ban, kick, timeout, untimeout, warnings, cleanup, nickname/role changes, slowmode restoration, channel message locks, user/server info | Administrators have a fallback permission path. Locks support exemptions but do not expose an informational allowed-command list. |
| Automatic moderation | Spam, duplicate messages, caps, mention, URL/domain and invitation filters; delete/warn/timeout actions | In-memory frequency windows reset on restart. Some unusual obfuscated links are not detected. |
| Welcome/goodbye | Text templates, join roles, departure messages | No separate configurable rich welcome/goodbye embed schema. |
| Logging | Durable internal audit records and per-category outgoing log jobs | Not every automatic state transition emits a Discord log; internal records and process logs provide additional evidence. |
| Self-service roles | Buttons, multi-select menus, emoji reactions, toggle/add-only/exclusive rules; saved-panel updates | Alliance roles cannot be self-assigned. Old emoji decorations may remain after editing mappings. |
| Suggestions | Public suggestions, optional anonymity, reaction voting, approve/reject buttons, optional discussion threads | Approval/rejection presentation is simpler; no configurable per-status colors. |
| Polls | 2–10 choices, persisted votes, change/withdraw, multiple choice, optional voter visibility, timed closure | Choices are supplied in one pipe-separated parameter. All polls have a duration. Voter listing is limited to 100 selections. |
| Starboard | Configurable emoji, threshold, self-star behavior, removal below threshold, source links and image | Only public source channels are mirrored. Uncached message deletion and reaction-clear events do not immediately remove old highlights. |
| Giveaways | Entry buttons, timed drawing, member eligibility, persisted results, reroll | This behavior is independently designed from the public feature summary; the detailed giveaways documentation URL returned 404 during research. No weighted entries or eligibility roles. |
| Message builder | Stored embed templates with fields, image, thumbnail, footer, channel/role restrictions | JSON collection editor; no full visual drag-and-drop editor. No author/icon fields or mass-pings. Sending uses Discord slash commands. |
| Translation | DeepL text translation, Groq audio transcription, private results, global preferred language, supported-language lookup | Keys are operator-wide environment variables. No per-guild encrypted key store, language selection modal, or localized bot interface. Provider calls not exercised live. |
| Duel reminders | Per-weekday templates, guild timezone, daily posting, prior-message cleanup, manual send/preview | Strategies must be authored by the operator; no copied default game strategies or mass-pings. |
| Events | Dashboard event builder, named definitions, custom minute/hour/day/week recurrence, durable series, pause/resume/edit/stop, per-occurrence cancellation, reusable reminders, in-event notifications, Discord calendar and cleanup choice | Elapsed intervals rather than calendar-month/cron rules. No native Discord Scheduled Event objects or visual month calendar. Duration determines end time. |
| Tickets | Private channels, categories, caps, participant management, close confirmation, text transcript, inactivity closure | No archive-in-place mode, pre-close warning, or unlimited HTML transcript. Controls are slash commands plus panel interactions. |
| Alliances | Create/import/delete, invite/leave/kick, promote/demote/transfer/set leader, role/nickname synchronization, succession | R1/R4/R5 model follows command docs, whose ranks conflict with the broader R1–R5 overview. Default capacity is 100. Cross-alliance leader reassignment requires a prior leave/transfer. No automatic reaction-role membership. Dashboard roster is read-only. |
| Presidents | Appointment/removal, current president, term history, decrees, role sync | One announcement destination rather than per-decree-type destinations. |
| Armory | Eight slots per UTC week, role-aware assignments, persistent announcement | Exact alliance name/tag match, with retained historical database rows. No slot-buff imagery or dashboard history editor. |
| APC | K/M/G parsing, faction persistence, self-report, manual corrections, optional private ranking, live public message | `/apc submit` resolves conflicting root-command/subcommand documentation. Deletion targets a member instead of a moving ranking position. Display capped at top 30. No faction artwork. |
| Birthdays | Set once, moderator corrections, self-removal, upcoming dates, timezone-aware daily notices, durable daily keys | No age-specific templates or configurable role/everyone pings. February 29 stays on February 29. |

## Reliability boundaries

- Jobs are leased, retried with bounded backoff, and surfaced when permanently failed. Database transitions and queued reconciliation are transactional where possible.
- Network sends and database commits cannot be one atomic transaction. A crash after Discord accepts a message but before its identifier is saved can duplicate that message on retry. Delivery is **at least once**, not exactly once. Normal daily scheduling and recurrence use persistent deduplication keys.
- Commands may commit the requested record before a subsequent Discord refresh fails. The error does not always mean the database operation was undone. Check the saved state and job view before repeating a destructive command.
- Role synchronization runs through the queue. A role hierarchy or permission failure leaves the database as the intended state and a retryable/failed job. It does not falsely roll back a completed Discord operation.
- The bot must have permission to delete an existing public APC message when switching to private mode. A deletion failure appears as a failed reconciliation job; previously posted information cannot be made private by a database flag alone.
- Scheduled reminders more than two minutes late are skipped. An event start after its end time or more than an hour late is marked missed. Recurring series advance independently during the once-per-minute maintenance pass; missing historical cycles are skipped. Disabling the events module suppresses delivery and series generation until it is enabled again. Pausing or stopping a series suppresses future starts; runs already in progress finish normally.
- Run one process for this SQLite database. Leases alone do not make duplicate Discord gateway instances safe.

## What is needed for a verified replacement

1. Capture a read-only inventory of the actual dashboard, available modules, forms, command autocomplete, default templates and permissions for a test server you control.
2. Agree which differences above are deliberate and which require matching.
3. Exercise every command and component in a sandbox guild, including restarts, deleted channels, changed roles, denied permissions, and module disable/enable.
4. Verify the real Discord OAuth round trip and optional provider adapters with operator-owned credentials stored locally.
5. Complete visual/localization parity and any required migration path. There is no migration tool for Arc Bot's private database.

## Public references

- [Arc Bot public documentation](https://docs.arcbot.pro/en)
- [Command overview and module boundaries](https://docs.arcbot.pro/en/docs/commands/overview)
- [Dashboard overview](https://docs.arcbot.pro/en/docs/dashboard/overview)
- [Moderation](https://docs.arcbot.pro/en/docs/cogs/moderation)
- [Auto moderation](https://docs.arcbot.pro/en/docs/cogs/automod)
- [Reaction roles](https://docs.arcbot.pro/en/docs/cogs/reaction-roles)
- [Polls](https://docs.arcbot.pro/en/docs/cogs/polls)
- [Suggestions](https://docs.arcbot.pro/en/docs/cogs/suggestions)
- [Starboard](https://docs.arcbot.pro/en/docs/cogs/starboard)
- [Message builder](https://docs.arcbot.pro/en/docs/cogs/message-builder)
- [Translation](https://docs.arcbot.pro/en/docs/cogs/translation)
- [Duel reminders](https://docs.arcbot.pro/en/docs/cogs/duel-reminder)
- [Scheduled events](https://docs.arcbot.pro/en/docs/cogs/schedule-events)
- [Tickets](https://docs.arcbot.pro/en/docs/commands/tickets)
- [Alliances](https://docs.arcbot.pro/en/docs/commands/alliances)
- [Presidents](https://docs.arcbot.pro/en/docs/commands/presidents)
- [Armory](https://docs.arcbot.pro/en/docs/commands/armory)
- [APC](https://docs.arcbot.pro/en/docs/cogs/apc)
- [Birthdays](https://docs.arcbot.pro/en/docs/cogs/birthdays)
- [Discord OAuth2](https://docs.discord.com/developers/topics/oauth2)
- [Discord permissions](https://docs.discord.com/developers/topics/permissions)
- [discord.py interactions](https://discordpy.readthedocs.io/en/stable/interactions/api.html)
- [DeepL translation API](https://developers.deepl.com/api-reference/translate/request-translation)
- [Groq speech-to-text API](https://console.groq.com/docs/speech-to-text)
