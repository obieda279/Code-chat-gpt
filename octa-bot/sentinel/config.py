from __future__ import annotations

from typing import Annotated, Literal
from zoneinfo import ZoneInfo
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Snowflake = Annotated[str, Field(pattern=r"^[0-9]{1,20}$")]
Color = Annotated[str, Field(pattern=r"^#[0-9a-fA-F]{6}$")]
Text = Annotated[str, Field(max_length=2000)]
Permission = Literal["everyone", "moderators", "custom", "administrators"]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class EmbedField(Model):
    name: Annotated[str, Field(min_length=1, max_length=256)]
    value: Annotated[str, Field(min_length=1, max_length=1024)]
    inline: bool = False


class Template(Model):
    name: Annotated[str, Field(min_length=1, max_length=80)] = "Announcement"
    content: Text = ""
    title: Annotated[str, Field(max_length=256)] = "Announcement"
    description: Annotated[str, Field(max_length=4096)] = ""
    color: Color = "#38BDF8"
    image: str = ""
    thumbnail: str = ""
    footer: Annotated[str, Field(max_length=2048)] = ""
    fields: Annotated[list[EmbedField], Field(max_length=25)] = []

    @field_validator("image", "thumbnail")
    @classmethod
    def url(cls, value):
        if value and (not value.startswith("https://") or len(value) > 2048):
            raise ValueError("Images must use an HTTPS URL (maximum 2048 characters)")
        return value

    @model_validator(mode="after")
    def length(self):
        if len(self.title + self.description + self.footer) + sum(len(f.name + f.value) for f in self.fields) > 6000:
            raise ValueError("An embed can contain at most 6000 characters")
        return self


class Module(Model):
    enabled: bool = False


class Lock(Model):
    channel_id: Snowflake
    exempt_roles: list[Snowflake] = []
    allow_bots: bool = True
    violation: Literal["silent", "reply", "dm"] = "silent"


class Moderation(Module):
    roles: list[Snowflake] = []
    dm_on_action: bool = True
    warning_threshold: Annotated[int, Field(ge=1, le=10)] = 3
    expire_warning_days: Annotated[int, Field(ge=0, le=3650)] = 0
    locks: Annotated[list[Lock], Field(max_length=100)] = []


class Filter(Model):
    enabled: bool = False
    action: Literal["delete", "warn", "mute"] = "delete"
    minutes: Annotated[int, Field(ge=1, le=1440)] = 10
    threshold: Annotated[int, Field(ge=1, le=100)] = 5
    window: Annotated[int, Field(ge=1, le=120)] = 5


class Automod(Module):
    exempt_roles: list[Snowflake] = []
    exempt_channels: list[Snowflake] = []
    spam: Filter = Filter()
    flood: Filter = Filter(threshold=3, window=30)
    caps: Filter = Filter(threshold=70)
    mentions: Filter = Filter()
    links: Filter = Filter()
    invites: Filter = Filter()
    allowed_domains: list[str] = []


class Welcome(Module):
    channel_id: Snowflake | None = None
    goodbye_channel_id: Snowflake | None = None
    message: Text = "Welcome {user} to {server}!"
    goodbye: Text = "{name} has left {server}."
    auto_roles: list[Snowflake] = []


class LogTarget(Model):
    category: Annotated[str, Field(max_length=40)] = "moderation"
    channel_id: Snowflake
    color: Color = "#38BDF8"


class Logs(Module):
    targets: Annotated[list[LogTarget], Field(max_length=30)] = []


class RoleMapping(Model):
    role_id: Snowflake
    label: Annotated[str, Field(min_length=1, max_length=80)]
    emoji: Annotated[str, Field(max_length=100)] = ""


class RolePanel(Model):
    name: Annotated[str, Field(min_length=1, max_length=80)]
    title: Annotated[str, Field(min_length=1, max_length=256)] = "Choose your roles"
    behavior: Literal["toggle", "add_only", "exclusive"] = "toggle"
    mode: Literal["buttons", "dropdown", "reactions"] = "buttons"
    mappings: Annotated[list[RoleMapping], Field(min_length=1, max_length=25)]

    @model_validator(mode="after")
    def unique(self):
        if len({m.role_id for m in self.mappings}) != len(self.mappings):
            raise ValueError("Roles must be unique within a panel")
        if self.mode == "reactions" and (any(not m.emoji for m in self.mappings) or len({m.emoji for m in self.mappings}) != len(self.mappings)):
            raise ValueError("Reaction panels need one unique emoji per role")
        return self


class ReactionRoles(Module):
    panels: Annotated[list[RolePanel], Field(max_length=30)] = []


class Polls(Module):
    create_permission: Permission = "moderators"
    create_roles: list[Snowflake] = []
    default_minutes: Annotated[int, Field(ge=1, le=525600)] = 1440
    multiple: bool = False
    anonymous: bool = False


class Suggestions(Module):
    channel_id: Snowflake | None = None
    create_thread: bool = False
    anonymous: bool = False
    manage_roles: list[Snowflake] = []


class Starboard(Module):
    channel_id: Snowflake | None = None
    emoji: str = "⭐"
    threshold: Annotated[int, Field(ge=1, le=1000)] = 3
    self_star: bool = False
    ignore_bots: bool = True
    ignore_channels: list[Snowflake] = []


class Giveaways(Module):
    manager_roles: list[Snowflake] = []


class Messages(Module):
    send_permission: Permission = "moderators"
    send_roles: list[Snowflake] = []
    allowed_channels: list[Snowflake] = []
    templates: Annotated[list[Template], Field(max_length=50)] = []


class Translation(Module):
    default_language: Annotated[str, Field(pattern=r"^[A-Z]{2}(-[A-Z]{2})?$", max_length=5)] = "EN-US"
    per_user_hourly_limit: Annotated[int, Field(ge=1, le=200)] = 20


class Duel(Module):
    channel_id: Snowflake | None = None
    send_hour: Annotated[int, Field(ge=0, le=23)] = 8
    send_minute: Annotated[int, Field(ge=0, le=59)] = 0
    clean_previous: bool = True
    # ISO weekday: Monday=1 through Sunday=7. User-authored strategies only.
    templates: dict[str, Template] = {}

    @field_validator("templates")
    @classmethod
    def weekdays(cls, value):
        if any(k not in [str(i) for i in range(1, 8)] for k in value):
            raise ValueError("Duel template keys must be 1 through 7 (Monday through Sunday)")
        return value


class EventDefinition(Model):
    name: Annotated[str, Field(min_length=1, max_length=80)]
    channel_id: Snowflake | None = None
    template: Template = Template()
    reminder_minutes: Annotated[list[Annotated[int, Field(ge=1, le=10080)]], Field(max_length=10)] = [60, 10]
    duration_minutes: Annotated[int, Field(ge=1, le=43200)] = 15
    repeat_hours: Annotated[int, Field(ge=1, le=8760)] | None = None
    interval_minutes: Annotated[int, Field(ge=5, le=10080)] | None = None
    keep_messages: bool = False


class Events(Module):
    channel_id: Snowflake | None = None
    calendar_channel_id: Snowflake | None = None
    definitions: Annotated[list[EventDefinition], Field(max_length=50)] = []


class EventCreate(Model):
    definition: EventDefinition
    when: Annotated[str, Field(min_length=10, max_length=64)]
    repeat_every: Annotated[str, Field(min_length=1, max_length=16)] = "off"


class SeriesChange(Model):
    action: Literal["pause", "resume", "stop", "edit"]
    version: Annotated[int, Field(ge=1)]
    repeat_every: Annotated[str, Field(min_length=1, max_length=16)] | None = None
    when: Annotated[str, Field(min_length=10, max_length=64)] | None = None


class Tickets(Module):
    category_id: Snowflake | None = None
    support_roles: list[Snowflake] = []
    transcript_channel_id: Snowflake | None = None
    categories: Annotated[list[Annotated[str, Field(min_length=1, max_length=80)]], Field(max_length=25)] = []
    max_per_user: Annotated[int, Field(ge=1, le=10)] = 3
    close_confirmation: bool = True
    auto_close_hours: Annotated[int, Field(ge=0, le=8760)] = 0


class Alliances(Module):
    management_roles: list[Snowflake] = []
    r4_roles: list[Snowflake] = []
    r5_roles: list[Snowflake] = []
    traveler_role_id: Snowflake | None = None
    max_alliances: Annotated[int, Field(ge=1, le=100)] = 10
    max_members: Annotated[int, Field(ge=1, le=1000)] = 100
    auto_nick_tag: bool = False


class Presidents(Module):
    role_id: Snowflake | None = None
    appointment_roles: list[Snowflake] = []
    channel_id: Snowflake | None = None


class Armory(Module):
    channel_id: Snowflake | None = None


class APC(Module):
    channel_id: Snowflake | None = None
    private: bool = False
    leaderboard_roles: list[Snowflake] = []


class Birthdays(Module):
    channel_id: Snowflake | None = None
    send_hour: Annotated[int, Field(ge=0, le=23)] = 8
    send_minute: Annotated[int, Field(ge=0, le=59)] = 0
    message: Text = "Happy birthday {user}! 🎂"


class GuildConfig(Model):
    timezone: str = "UTC"
    color: Color = "#38BDF8"
    dashboard_users: list[Snowflake] = []
    moderation: Moderation = Moderation()
    automod: Automod = Automod()
    welcome: Welcome = Welcome()
    logs: Logs = Logs()
    reaction_roles: ReactionRoles = ReactionRoles()
    suggestions: Suggestions = Suggestions()
    polls: Polls = Polls()
    starboard: Starboard = Starboard()
    giveaways: Giveaways = Giveaways()
    messages: Messages = Messages()
    translation: Translation = Translation()
    duel: Duel = Duel()
    events: Events = Events()
    tickets: Tickets = Tickets()
    alliances: Alliances = Alliances()
    presidents: Presidents = Presidents()
    armory: Armory = Armory()
    apc: APC = APC()
    birthdays: Birthdays = Birthdays()

    @field_validator("timezone")
    @classmethod
    def timezone_valid(cls, value):
        try:
            ZoneInfo(value)
        except (KeyError, ValueError):
            raise ValueError("Use a valid IANA timezone, such as Asia/Amman")
        return value

    @model_validator(mode="after")
    def names_unique(self):
        for items in (self.reaction_roles.panels, self.messages.templates, self.events.definitions):
            if len({x.name.casefold() for x in items}) != len(items):
                raise ValueError("Panel, template and event names must be unique")
        return self


MODULES = [name for name, field in GuildConfig.model_fields.items() if isinstance(field.default, Module)]
