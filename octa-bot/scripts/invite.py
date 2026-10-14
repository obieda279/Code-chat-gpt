"""Print an installation link. This script never installs the bot itself."""
import os
from urllib.parse import urlencode
import discord
from sentinel.settings import load_env

load_env()
client_id=os.getenv("DISCORD_CLIENT_ID","")
if not client_id.isdigit():
    raise SystemExit("Set DISCORD_CLIENT_ID in .env first.")
permissions=discord.Permissions(
    view_channel=True,send_messages=True,embed_links=True,attach_files=True,
    read_message_history=True,add_reactions=True,manage_messages=True,
    manage_roles=True,manage_nicknames=True,kick_members=True,ban_members=True,
    moderate_members=True,manage_channels=True,create_public_threads=True,
    send_messages_in_threads=True,manage_threads=True)
print("https://discord.com/oauth2/authorize?"+urlencode({"client_id":client_id,"scope":"bot applications.commands","permissions":permissions.value,"integration_type":0}))
