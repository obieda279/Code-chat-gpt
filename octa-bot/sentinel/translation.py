"""Optional adapters. Keys live only in the operator's environment."""
import io
import re
import time
import aiohttp
import discord
from discord import app_commands
from .bot import reply
from .domain import RuleError


def register_translation(bot, add):
    languages_cache = {"expires": 0, "languages": []}

    def endpoint():
        key = bot.settings.deepl_key
        if not key:
            raise RuleError("The operator must configure DEEPL_API_KEY first.")
        return "https://api-free.deepl.com/v2" if key.endswith(":fx") else "https://api.deepl.com/v2"

    async def languages():
        if languages_cache["expires"] > time.time():
            return languages_cache["languages"]
        async with bot.session.get(endpoint() + "/languages", params={"type": "target"}, headers={"Authorization": "DeepL-Auth-Key " + bot.settings.deepl_key}) as response:
            if response.status != 200:
                raise RuleError(f"Translation provider returned HTTP {response.status}. Ask the operator to check its key or quota.")
            result = await response.json()
        languages_cache.update(expires=time.time() + 3600, languages=result)
        return result

    def rate_limit(i):
        now = time.time()
        key = (i.guild_id, i.user.id)
        recent = [t for t in bot.translation_usage[key] if now - t < 3600]
        if len(recent) >= bot.cfg(i.guild_id).translation.per_user_hourly_limit:
            raise RuleError("You have reached this server's hourly translation limit.")
        bot.translation_usage[key] = recent + [now]

    async def translate(i, text):
        if not text or len(text) > 5000:
            raise RuleError("Text must contain 1–5000 characters.")
        preference = bot.db.one("SELECT language FROM languages WHERE user=?", (str(i.user.id),))
        target = preference["language"] if preference else bot.cfg(i.guild_id).translation.default_language
        async with bot.session.post(endpoint() + "/translate", json={"text": [text], "target_lang": target}, headers={"Authorization": "DeepL-Auth-Key " + bot.settings.deepl_key}) as response:
            if response.status != 200:
                raise RuleError(f"Translation provider returned HTTP {response.status}. Check its language, key, or quota.")
            result = await response.json()
        return result["translations"][0]["text"]

    @add("translation", "setlang", "Set your global preferred translation language")
    async def setlang(i: discord.Interaction, language: str):
        language = language.upper()
        await i.response.defer(ephemeral=True)
        choices = await languages()
        if language not in {item["language"] for item in choices}:
            raise RuleError("Choose a provider-supported language code from /languages.")
        bot.db.execute("INSERT INTO languages VALUES(?,?) ON CONFLICT(user) DO UPDATE SET language=excluded.language", (str(i.user.id), language))
        await reply(i, "Preferred language saved: " + language)

    @add("translation", "languages", "List target languages currently supported by DeepL")
    async def list_languages(i: discord.Interaction):
        await i.response.defer(ephemeral=True)
        choices = await languages()
        await reply(i, embed=bot.embed(i.guild, "Translation languages", "\n".join(f"{item['language']} · {item['name']}" for item in choices)))

    async def translate_message(i: discord.Interaction, message: discord.Message):
        rate_limit(i)
        text = message.content
        for embed in message.embeds:
            text += "\n" + (embed.title or "") + "\n" + (embed.description or "")
            text += "\n".join(f.name + ": " + f.value for f in embed.fields)
        await i.response.defer(ephemeral=True)
        result = await translate(i, text.strip())
        if len(result) > 4000:
            await reply(i, "Translation attached.", file=discord.File(io.BytesIO(result.encode()), filename="translation.txt"))
        else:
            await reply(i, embed=bot.embed(i.guild, "Translation", result))

    async def translate_voice(i: discord.Interaction, message: discord.Message):
        rate_limit(i)
        if not bot.settings.groq_key:
            raise RuleError("The operator must configure GROQ_API_KEY first.")
        audio = next((a for a in message.attachments if (a.content_type or "").startswith(("audio/", "video/"))), None)
        if not audio or audio.size > 25 * 1024 * 1024:
            raise RuleError("Select a message containing an audio/video attachment no larger than 25 MB.")
        await i.response.defer(ephemeral=True)
        # Read a Discord attachment, never a user-supplied arbitrary URL.
        raw = await audio.read()
        if len(raw) > 25 * 1024 * 1024:
            raise RuleError("Audio file exceeds 25 MB.")
        form = aiohttp.FormData()
        form.add_field("file", raw, filename=re.sub(r"[^a-zA-Z0-9._-]", "_", audio.filename), content_type=audio.content_type or "application/octet-stream")
        form.add_field("model", "whisper-large-v3-turbo")
        async with bot.session.post("https://api.groq.com/openai/v1/audio/transcriptions", data=form, headers={"Authorization": "Bearer " + bot.settings.groq_key}, timeout=aiohttp.ClientTimeout(total=120)) as response:
            if response.status != 200:
                raise RuleError(f"Transcription provider returned HTTP {response.status}.")
            result = await response.json()
        original = result["text"]
        translated = await translate(i, original)
        combined = f"Original\n{original}\n\nTranslation\n{translated}"
        await reply(i, "Transcript and translation attached.", file=discord.File(io.BytesIO(combined.encode()), filename="voice-translation.txt"))

    for title, callback in [("Translate Message", translate_message), ("Translate Voice", translate_voice)]:
        menu = app_commands.ContextMenu(name=title, callback=callback)
        menu.guild_only = True
        bot.tree.add_command(menu)
        bot.command_modules[title] = "translation"
