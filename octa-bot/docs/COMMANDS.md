# Sentinel command reference

Commands are synchronized per server after their module is enabled. All confirmations are private unless a command explicitly publishes a panel, announcement, or other message.

| Command | Module | Parameters |
| --- | --- | --- |
| `/dashboard` | core | — |
| `/status` | core | — |
| `/ban` | moderation | target, reason (optional) |
| `/kick` | moderation | target, reason (optional) |
| `/warn` | moderation | target, reason (optional) |
| `/unmute` | moderation | target, reason (optional) |
| `/mute` | moderation | target, minutes (optional), reason (optional) |
| `/warnings` | moderation | target |
| `/clear` | moderation | amount |
| `/setnick` | moderation | target, nickname (optional) |
| `/role add` | moderation | target, role |
| `/role remove` | moderation | target, role |
| `/slowmode` | moderation | seconds, channel (optional), minutes (optional) |
| `/lockdown` | moderation | channel, allow_bots (optional), violation (optional) |
| `/unlock` | moderation | channel |
| `/userinfo` | moderation | member (optional) |
| `/serverinfo` | moderation | — |
| `/poll` | polls | question, options, ends_in (optional), multiple (optional), anonymous (optional) |
| `/polls` | polls | — |
| `/suggest` | suggestions | content |
| `/giveaway start` | giveaways | prize, ends_in, winners (optional) |
| `/giveaway reroll` | giveaways | panel_id |
| `/roles panel` | reaction_roles | name, channel (optional) |
| `/embed list` | messages | — |
| `/embed send` | messages | name, channel (optional) |
| `/alliance create` | alliances | name, tag, color (optional) |
| `/alliance import` | alliances | role, leader, name, tag |
| `/alliance delete` | alliances | name, confirm (optional) |
| `/alliance invite` | alliances | member |
| `/alliance kick` | alliances | member |
| `/alliance promote` | alliances | member |
| `/alliance demote` | alliances | member |
| `/alliance transfer` | alliances | member |
| `/alliance leave` | alliances | — |
| `/alliance setleader` | alliances | name, member |
| `/alliance list` | alliances | — |
| `/alliance info` | alliances | name (optional) |
| `/president appoint` | presidents | member |
| `/president remove` | presidents | — |
| `/president info` | presidents | — |
| `/president history` | presidents | — |
| `/president decree` | presidents | message, decree_type (optional) |
| `/armory assign` | armory | slot, alliance_name |
| `/armory clear` | armory | slot |
| `/apc submit` | apc | main, second, main_faction (optional), second_faction (optional) |
| `/apc view` | apc | — |
| `/apc leaderboard` | apc | — |
| `/apc-set` | apc | member, main, second, main_faction (optional), second_faction (optional) |
| `/apc-remove` | apc | member |
| `/event schedule` | events | name, when, repeat_every (optional) |
| `/event create` | events | name, channel, when, message, repeat_every (optional), duration_minutes (optional), reminder_minutes (optional), keep_messages (optional) |
| `/event series` | events | — |
| `/event repeat` | events | series_id, action, every (optional), when (optional) |
| `/event list` | events | — |
| `/event cancel` | events | event_id |
| `/duel preview` | duel | — |
| `/duel send` | duel | — |
| `/ticket panel` | tickets | — |
| `/ticket close` | tickets | — |
| `/ticket add` | tickets | member |
| `/ticket remove` | tickets | member |
| `/birthday set` | birthdays | month, day, year (optional) |
| `/birthday set-user` | birthdays | member, month, day, year (optional) |
| `/birthday remove-user` | birthdays | member |
| `/birthday upcoming` | birthdays | — |
| `/setlang` | translation | language |
| `/languages` | translation | — |
| `/Translate Message` | translation | — |
| `/Translate Voice` | translation | — |
