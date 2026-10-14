# Recurring events — v0.2

Create an event once and repeat it until you pause or stop its series. This feature uses the recurring-interval behavior of [Carl-bot autofeeds](https://github.com/CarlGroth/carlbot-docs/blob/master/utilities/announcements.md) as a functional reference; the code is original.

## Dashboard

1. Open **Events → Event settings**, enable Scheduled events, and save changes.
2. Enter the event name, text channel, first start, message, duration, and reminders. The first start uses the saved server timezone shown beside the field.
3. Enable **Repeat automatically**. Choose an amount and unit: minutes, hours, days, or weeks. Supported intervals range from 5 minutes to 365 days.
4. Select **Create event**. The schedule and first occurrence are committed immediately. You do not need to save global settings again.
5. The recurring schedule card shows its series ID, interval, status, and next run. Use **Refresh schedules** to reload changes from another session.

Reminders are comma-separated minutes before the start, such as `60, 10`. Leave the field empty to send only the start message. A reminder whose time has already passed is skipped when creating an event. The same reminders apply to subsequent occurrences. Messages support `{event}` and `{countdown}`. General templates suppress Discord mentions.

## Controls

| Control | Behavior |
| --- | --- |
| Pause | Suspends future occurrences. In-progress runs finish and clean up normally. |
| Resume | Continues at the next future slot on the original cadence. Missed slots are not replayed. |
| Edit timing | Changes the interval; optionally sets a new first start. Replaces upcoming slots while preserving in-progress runs. A paused series stays paused. |
| Stop series | Permanently ends future recurrence. Its history remains in the database; create a new series to start again. |
| Cancel occurrence | Skips only that occurrence. Other occurrences in its series continue. |

Reminders already posted are cleaned up at the occurrence's original end when **Keep event messages** is off. A message already being sent cannot be recalled by pausing. Up to 50 active or paused series are supported per server.

## Timing and persistence

The first start is an anchor. Each next start equals `anchor + n × interval`, rather than the previous delivery or cleanup time plus an interval. A day is exactly 24 hours and a week is 7 days. These are elapsed intervals, not weekday/monthly calendar rules. Local clock time may shift when daylight-saving time changes; changing the server display timezone does not move an existing series.

The database stores each series separately from its occurrences and notification jobs. A maintenance pass runs every minute, generating enough upcoming occurrences for the configured reminder lead time, and at least one upcoming cycle. Each pass creates at most 100 cycles per series. A unique database index and short transactions prevent duplicate cycle records. Message delivery and end cleanup do not gate the next run. Overlapping events are allowed.

On restart, the scheduler advances past historical cycles. Already queued reminders over two minutes late are skipped. Starts after the event's end or over one hour late are marked missed; a still-valid queued start can arrive late. Discord/network delays can affect actual delivery. Discord sends retain the application's at-least-once retry behavior: a crash after delivery but before saving its result can cause a duplicate message.

The process must run to deliver events. Demo mode permits creating and managing schedules locally, but does not run Discord delivery or the maintenance loop. Disabling the Events module suppresses new generation and messages. Once enabled again, active series continue at future slots. Explicitly paused series require Resume.

## Discord examples

Replace the name, channel, future date, and returned IDs with your own values.

```text
/event create name:Rally channel:#events when:2026-12-01T20:00+03:00 message:Meet at {countdown} repeat_every:12h reminder_minutes:60,10
/event create name:Weekly briefing channel:#events when:2026-12-06T19:00 message:Bring your reports repeat_every:1w
/event schedule name:Saved event when:2026-12-01T20:00 repeat_every:3d
/event series
/event repeat series_id:1 action:edit every:2w when:2026-12-15T20:00
/event repeat series_id:1 action:pause
/event repeat series_id:1 action:resume
/event cancel event_id:17
/event repeat series_id:1 action:stop
```

`repeat_every:off` creates a one-time event. `/event schedule` uses an existing named definition; `/event create` provides the message directly. Commands and dashboard mutations require server administrator permission.

## Upgrade from v0.1

Back up the database, stop the old process, replace the source, and restart the new version. Database schema 2 is an additive migration: it adds the series table and an optional series reference to existing events. Do not run v0.1 against an upgraded database.

The legacy `repeat_hours` setting remains accepted. Newly scheduled events with that setting become durable series. An existing v0.1 event retains its old end job; its next successful repeat becomes a series. Until then, cancel that old occurrence if you want to terminate its recurrence. Existing ended events with no pending repeat job are not retroactively restarted.

Each new series snapshots its event definition, including message, channel, duration, and reminders. Changing or deleting a saved named definition does not silently modify an existing series. Timing can be edited directly; to change the content or channel, stop the series and create a new one.

## HTTP API

These routes use the existing authenticated dashboard session, server access checks, Origin validation, and CSRF header. No separate service credentials are introduced.

| Method and route | Purpose |
| --- | --- |
| `GET /api/guilds/{guild}/events` | Active/paused series and up to 50 upcoming or in-progress occurrences |
| `POST /api/guilds/{guild}/events` | Create `{definition, when, repeat_every}`; returns event ID and optional series ID |
| `PATCH /api/guilds/{guild}/event-series/{series}` | `{action, version, repeat_every?, when?}`; stale versions return HTTP 409 |
| `POST /api/guilds/{guild}/events/{event}/cancel` | Cancel a single occurrence |

`definition` follows `EventDefinition` in the configuration schema. `when` is ISO date/time, optionally with a UTC offset. `repeat_every` accepts values such as `30m`, `24h`, `7d`, `2w`, or `off` on creation. The `edit` action requires a nonzero repeat interval. Pausing, resuming, stopping, or editing returns a new version in the next list response.
