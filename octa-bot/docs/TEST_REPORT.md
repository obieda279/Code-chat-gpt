# Verification report

Date: October 14, 2026. Release: 0.2.0.

## Executed successfully

| Check | Result | What it establishes |
| --- | --- | --- |
| Python unittest suite | 67 tests passed | Domain rules, permissions, isolation, dashboard authorization, recurring job behavior and local startup |
| Discord command serialization | Passed within the suite | All 67 slash-command leaves and two context actions construct and serialize under the installed discord.py version |
| Local CLI demo | Passed within the suite | A fresh subprocess starts, serves health and login routes, issues a session, lists a sample guild, and saves validated settings |
| Dashboard DOM flow | Passed | Initialization, navigation, safe text rendering, settings save/discard, event creation, custom recurrence, pause, resume, timing edits and stopping a series |
| JavaScript syntax | Passed | `node --check sentinel/static/app.js` |
| Python compilation | Passed | Application and utility modules compile |

Runtime used: Python 3.12.14, discord.py 2.7.1, aiohttp 3.14.5, Pydantic 2.13.5, Node.js 24.19.0. The optional DOM checks use jsdom 30.1.2. The runtime requirements also include tzdata so IANA timezones work on Windows.

## Meaningful failure cases covered

- Cross-server configuration reads/writes, poll interaction IDs and alliance membership operations.
- A revoked administrator and a delegated viewer attempting to modify settings.
- Missing/forged CSRF values, wrong origins, unrecognized Hosts, expired sessions, and replayed/unbound OAuth state.
- A self-service role that would grant bot authority, despite having no native Discord administrator permission.
- Role hierarchy failures, self-targeting, peer moderation, alliance capacity, conflicting membership, invalid import rosters, and R4 privilege boundaries.
- Atomic R5 transfers, succession on departure, one leader per alliance, and preserved APC faction values.
- Single-choice vote replacement, withdrawal, multi-choice voting, expired voting, persisted giveaway results and departed entrants.
- Job lease recovery, unique daily keys, recurring-event deduplication, cancellation, and cleanup failure before end-state commit.
- Minute/hour/day/week recurrence, actual database reopen, missed-cycle skipping without drift, long reminder lead times, overlapping events, independent scheduling during cleanup failure, pause/resume of all queued cycles, timing changes and optimistic version conflicts.
- Additive v1 database migration, legacy repeat-hours compatibility, stored definition snapshots, two consecutive delivered cycles with simulated Discord, legacy job adoption, module disable/enable, event API authentication/CSRF/server isolation, and administrator-only schedule controls.
- Invalid numbers, overflow, impossible dates, daylight-saving gaps/folds, UTC week boundaries and spoofed URL allowlist domains.

## Not verified here

- Real Discord login, installation, command registration, gateway events, role creation, bans, channel permissions, or message delivery. No Discord credentials were supplied and no real server actions were performed.
- Live DeepL or Groq calls, quotas, audio quality, provider outages, or billing behavior. API adapters use documented endpoints and require operator testing.
- Full visual rendering, mobile layout, keyboard accessibility in real browsers, or comparison with Arc Bot's private dashboard. Chromium installation was attempted but the downloadable archive was unavailable in this environment. The DOM checks do not replace a real browser layout inspection.
- Docker build, Windows-native execution, high-volume traffic, or multi-instance deployment. The Windows instructions use standard Python virtual-environment commands; they still need a run on the target computer.
- Every possible Discord partial-failure scenario. Transaction and retry tests cover identified risks; this is not an external security audit or production certification.

Run the listed test commands from README before modifying the project, then perform the live sandbox checklist in PARITY.md before adopting it as your server's primary bot.
