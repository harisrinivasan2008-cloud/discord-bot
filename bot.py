import os
import sys
import json
import time
import asyncio
import traceback
import random
import re
import wave
import struct
import io
import datetime
import threading
import logging
from collections import defaultdict, deque
import discord
from discord.ext import commands, tasks
from discord import app_commands
from dotenv import load_dotenv
from aiohttp import web

# Configure logging for Discord gateway monitoring
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logging.getLogger("discord.gateway").setLevel(logging.INFO)
logging.getLogger("discord.client").setLevel(logging.INFO)
logging.getLogger("discord.http").setLevel(logging.WARNING)

# Initialize Opus for Discord voice connections
if not discord.opus.is_loaded():
    try:
        discord.opus._load_default()
    except Exception:
        pass

# Force unbuffered stdout/stderr so logs appear immediately (and handle pythonw where stdout is None)
try:
    if sys.stdout is None:
        sys.stdout = open(os.path.join(os.path.dirname(__file__), "bot.log"), "a", encoding="utf-8", buffering=1)
    else:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    if sys.stderr is None:
        sys.stderr = open(os.path.join(os.path.dirname(__file__), "bot_error.log"), "a", encoding="utf-8", buffering=1)
    else:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
except Exception:
    pass

# Load credentials from .env file
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN", "").strip()

# ==============================================================================
# BOT ADMIN MANAGEMENT & PERSISTENCE
# ==============================================================================
ADMINS_FILE = os.path.join(os.path.dirname(__file__), "admins.json")
SERVER_CONFIG_FILE = os.path.join(os.path.dirname(__file__), "server_config.json")
BOT_OWNER_ID = int(os.getenv("BOT_OWNER_ID", "1506483667604734183"))

def load_admin_ids() -> set:
    if os.path.exists(ADMINS_FILE):
        try:
            with open(ADMINS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return set(data.get("admin_ids", []))
        except Exception as e:
            print(f"Error loading admins.json: {e}", flush=True)
    return set()

def save_admin_ids(admin_ids: set):
    try:
        with open(ADMINS_FILE, "w", encoding="utf-8") as f:
            json.dump({"admin_ids": sorted(list(admin_ids))}, f, indent=2)
    except Exception as e:
        print(f"Error saving admins.json: {e}", flush=True)

def load_server_config() -> dict:
    if os.path.exists(SERVER_CONFIG_FILE):
        try:
            with open(SERVER_CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading server_config.json: {e}", flush=True)
    return {}

def save_server_config(config: dict):
    try:
        with open(SERVER_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
    except Exception as e:
        print(f"Error saving server_config.json: {e}", flush=True)

def get_server_admin_ids(guild_id: int) -> set:
    config = load_server_config()
    return set(config.get(str(guild_id), {}).get("admin_ids", []))

def add_server_admin(guild_id: int, user_id: int):
    config = load_server_config()
    g_key = str(guild_id)
    if g_key not in config:
        config[g_key] = {}
    admins = set(config[g_key].get("admin_ids", []))
    admins.add(user_id)
    config[g_key]["admin_ids"] = sorted(list(admins))
    save_server_config(config)

def remove_server_admin(guild_id: int, user_id: int) -> bool:
    config = load_server_config()
    g_key = str(guild_id)
    if g_key in config and "admin_ids" in config[g_key]:
        admins = set(config[g_key]["admin_ids"])
        if user_id in admins:
            admins.remove(user_id)
            config[g_key]["admin_ids"] = sorted(list(admins))
            save_server_config(config)
            return True
    return False

def get_default_prefix(guild_id: int) -> str | None:
    config = load_server_config()
    return config.get(str(guild_id), {}).get("default_prefix")

def set_default_prefix(guild_id: int, prefix: str):
    config = load_server_config()
    guild_key = str(guild_id)
    if guild_key not in config:
        config[guild_key] = {}
    config[guild_key]["default_prefix"] = prefix
    save_server_config(config)

def remove_default_prefix(guild_id: int) -> bool:
    config = load_server_config()
    guild_key = str(guild_id)
    if guild_key in config and "default_prefix" in config[guild_key]:
        del config[guild_key]["default_prefix"]
        save_server_config(config)
        return True
    return False

def get_bye_channel(guild_id: int) -> int | None:
    config = load_server_config()
    return config.get(str(guild_id), {}).get("bye_channel_id")

def set_bye_channel(guild_id: int, channel_id: int):
    config = load_server_config()
    guild_key = str(guild_id)
    if guild_key not in config:
        config[guild_key] = {}
    config[guild_key]["bye_channel_id"] = channel_id
    save_server_config(config)

def remove_bye_channel(guild_id: int) -> bool:
    config = load_server_config()
    guild_key = str(guild_id)
    if guild_key in config and "bye_channel_id" in config[guild_key]:
        del config[guild_key]["bye_channel_id"]
        save_server_config(config)
        return True
    return False

def get_stay_vc_config(guild_id: int) -> dict:
    config = load_server_config()
    return config.get(str(guild_id), {}).get("stay_vc", {})

def set_stay_vc_config(guild_id: int, channel_id: int, enabled: bool = True):
    config = load_server_config()
    g_key = str(guild_id)
    if g_key not in config:
        config[g_key] = {}
    config[g_key]["stay_vc"] = {
        "enabled": enabled,
        "channel_id": channel_id
    }
    save_server_config(config)

def disable_stay_vc(guild_id: int):
    config = load_server_config()
    g_key = str(guild_id)
    if g_key in config and "stay_vc" in config[g_key]:
        config[g_key]["stay_vc"]["enabled"] = False
        save_server_config(config)

def get_bump_config(guild_id: int) -> dict:
    config = load_server_config()
    return config.get(str(guild_id), {}).get("bump", {})

def save_bump_config(guild_id: int, bump_cfg: dict):
    config = load_server_config()
    guild_key = str(guild_id)
    if guild_key not in config:
        config[guild_key] = {}
    config[guild_key]["bump"] = bump_cfg
    save_server_config(config)

def disable_bump_config(guild_id: int) -> bool:
    config = load_server_config()
    guild_key = str(guild_id)
    if guild_key in config and "bump" in config[guild_key]:
        config[guild_key]["bump"]["enabled"] = False
        save_server_config(config)
        return True
    return False

# ==============================================================================
# EXTRA OWNERS & WHITELIST HELPERS
# ==============================================================================
def get_extraowner_ids(guild_id: int) -> set:
    config = load_server_config()
    return set(config.get(str(guild_id), {}).get("extraowner_ids", []))

def add_extraowner(guild_id: int, user_id: int):
    config = load_server_config()
    g_key = str(guild_id)
    if g_key not in config:
        config[g_key] = {}
    extraowners = set(config[g_key].get("extraowner_ids", []))
    extraowners.add(user_id)
    config[g_key]["extraowner_ids"] = sorted(list(extraowners))
    save_server_config(config)

def remove_extraowner(guild_id: int, user_id: int) -> bool:
    config = load_server_config()
    g_key = str(guild_id)
    if g_key in config and "extraowner_ids" in config[g_key]:
        extraowners = set(config[g_key]["extraowner_ids"])
        if user_id in extraowners:
            extraowners.remove(user_id)
            config[g_key]["extraowner_ids"] = sorted(list(extraowners))
            save_server_config(config)
            return True
    return False

def is_server_owner_or_extraowner(guild: discord.Guild, user_id: int) -> bool:
    if user_id == BOT_OWNER_ID:
        return True
    if bot.owner_id and user_id == bot.owner_id:
        return True
    if not guild:
        return False
    if guild.owner_id and user_id == guild.owner_id:
        return True
    return user_id in get_extraowner_ids(guild.id)

def get_whitelist_ids(guild_id: int) -> set:
    config = load_server_config()
    return set(config.get(str(guild_id), {}).get("whitelist_ids", []))

def add_whitelist_user(guild_id: int, user_id: int):
    config = load_server_config()
    g_key = str(guild_id)
    if g_key not in config:
        config[g_key] = {}
    wl = set(config[g_key].get("whitelist_ids", []))
    wl.add(user_id)
    config[g_key]["whitelist_ids"] = sorted(list(wl))
    save_server_config(config)

def remove_whitelist_user(guild_id: int, user_id: int) -> bool:
    config = load_server_config()
    g_key = str(guild_id)
    if g_key in config and "whitelist_ids" in config[g_key]:
        wl = set(config[g_key]["whitelist_ids"])
        if user_id in wl:
            wl.remove(user_id)
            config[g_key]["whitelist_ids"] = sorted(list(wl))
            save_server_config(config)
            return True
    return False

def is_whitelisted(guild: discord.Guild, user_id: int) -> bool:
    if user_id == BOT_OWNER_ID:
        return True
    if not guild:
        return False
    if guild.owner_id and user_id == guild.owner_id:
        return True
    if user_id in get_extraowner_ids(guild.id):
        return True
    return user_id in get_whitelist_ids(guild.id)

# ==============================================================================
# AUTOMOD, ANTINUKE, AUTOROLE & GREET CONFIG HELPERS
# ==============================================================================
def get_automod_config(guild_id: int) -> dict:
    config = load_server_config()
    return config.get(str(guild_id), {}).get("automod", {"enabled": True, "channels": []})

def set_automod_config(guild_id: int, automod_cfg: dict):
    config = load_server_config()
    g_key = str(guild_id)
    if g_key not in config:
        config[g_key] = {}
    config[g_key]["automod"] = automod_cfg
    save_server_config(config)

def get_antinuke_config(guild_id: int) -> dict:
    config = load_server_config()
    return config.get(str(guild_id), {}).get("antinuke", {"enabled": False})

def set_antinuke_config(guild_id: int, enabled: bool):
    config = load_server_config()
    g_key = str(guild_id)
    if g_key not in config:
        config[g_key] = {}
    config[g_key]["antinuke"] = {"enabled": enabled}
    save_server_config(config)

def get_autorole_config(guild_id: int) -> dict:
    config = load_server_config()
    return config.get(str(guild_id), {}).get("autorole", {"human_role_id": None, "bot_role_id": None})

def set_autorole_config(guild_id: int, autorole_cfg: dict):
    config = load_server_config()
    g_key = str(guild_id)
    if g_key not in config:
        config[g_key] = {}
    config[g_key]["autorole"] = autorole_cfg
    save_server_config(config)

def get_greet_config(guild_id: int) -> dict:
    config = load_server_config()
    return config.get(str(guild_id), {}).get("greet", {
        "enabled": False,
        "channel_ids": [],
        "banner_url": None,
        "message": None,
        "template": 1
    })

def set_greet_config(guild_id: int, greet_cfg: dict):
    config = load_server_config()
    g_key = str(guild_id)
    if g_key not in config:
        config[g_key] = {}
    config[g_key]["greet"] = greet_cfg
    save_server_config(config)

# ==============================================================================
# BIRTHDAY, REACTION ROLES & LEVELING CONFIG HELPERS
# ==============================================================================
def get_birthday_config(guild_id: int) -> dict:
    config = load_server_config()
    return config.get(str(guild_id), {}).get("birthdays", {
        "enabled": True,
        "channel_id": None,
        "input_channel_id": None,
        "users": {},
        "last_wished": {}
    })

def set_birthday_config(guild_id: int, bday_cfg: dict):
    config = load_server_config()
    g_key = str(guild_id)
    if g_key not in config:
        config[g_key] = {}
    config[g_key]["birthdays"] = bday_cfg
    save_server_config(config)

def get_reaction_roles_config(guild_id: int) -> dict:
    config = load_server_config()
    return config.get(str(guild_id), {}).get("reaction_roles", {})

def set_reaction_roles_config(guild_id: int, rr_cfg: dict):
    config = load_server_config()
    g_key = str(guild_id)
    if g_key not in config:
        config[g_key] = {}
    config[g_key]["reaction_roles"] = rr_cfg
    save_server_config(config)

def get_leveling_config(guild_id: int) -> dict:
    config = load_server_config()
    return config.get(str(guild_id), {}).get("leveling", {
        "enabled": True,
        "channel_id": None,
        "users": {}
    })

def set_leveling_config(guild_id: int, lvl_cfg: dict):
    config = load_server_config()
    g_key = str(guild_id)
    if g_key not in config:
        config[g_key] = {}
    config[g_key]["leveling"] = lvl_cfg
    save_server_config(config)

def xp_needed_for_level(level: int) -> int:
    return 100 + (level * 75)

def is_exempt_from_leveling(member: discord.Member) -> bool:
    if not isinstance(member, discord.Member) or member.bot:
        return True
    if member.id == BOT_OWNER_ID:
        return True
    if bot.owner_id and member.id == bot.owner_id:
        return True
    guild = member.guild
    if not guild:
        return True
    if guild.owner_id and member.id == guild.owner_id:
        return True
    if is_server_owner_or_extraowner(guild, member.id):
        return True
    if is_user_admin(member.id, guild):
        return True
    if member.guild_permissions.administrator:
        return True
    return False

# In-memory cooldowns for XP gains (Guild ID -> Member ID -> Timestamp)
user_xp_cooldowns = defaultdict(dict)

def get_guild_showcase_channels(guild: discord.Guild) -> str:
    if not guild:
        return "• 📜 *Check server channel list*"

    rules_ch = guild.rules_channel
    if not rules_ch:
        rules_ch = discord.utils.find(lambda c: any(k in c.name.lower() for k in ["rule", "guideline", "info"]), guild.text_channels)

    chat_ch = discord.utils.find(lambda c: any(k in c.name.lower() for k in ["general", "chat", "lounge", "talk"]), guild.text_channels)
    news_ch = discord.utils.find(lambda c: any(k in c.name.lower() for k in ["announcement", "news", "update"]), guild.text_channels)

    greet_cfg = get_greet_config(guild.id)
    c_ids = greet_cfg.get("channel_ids", [])
    welcome_chs = [f"<#{cid}>" for cid in c_ids if guild.get_channel(cid)]

    lines = []
    if welcome_chs:
        lines.append(f"• 👋 **Welcome Lounge**: {', '.join(welcome_chs)}")
    if rules_ch:
        lines.append(f"• 📜 **Rules & Info**: {rules_ch.mention}")
    if chat_ch:
        lines.append(f"• 💬 **Main Chat**: {chat_ch.mention}")
    if news_ch and news_ch != chat_ch and news_ch != rules_ch:
        lines.append(f"• 📢 **Announcements**: {news_ch.mention}")

    if not lines:
        sample_chs = [c.mention for c in guild.text_channels[:3] if c.permissions_for(guild.default_role).view_channel]
        if sample_chs:
            lines.append(f"• 📌 **Featured Channels**: {', '.join(sample_chs)}")
        else:
            lines.append("• 📜 *Explore all channels in the channel sidebar!*")

    return "\n".join(lines)

def build_welcome_card(member: discord.Member | discord.User, template_id: int = 1, custom_msg: str = None, banner_url: str = None) -> tuple[str, discord.Embed]:
    guild = getattr(member, "guild", None)
    avatar = getattr(member, "display_avatar", None)
    avatar_url = avatar.url if avatar else "https://cdn.discordapp.com/embed/avatars/0.png"

    created_at = getattr(member, "created_at", None)
    created_ts = int(created_at.timestamp()) if created_at else int(time.time())
    member_count = guild.member_count if guild else 1

    final_banner = banner_url or (guild.banner.url if guild and guild.banner else None)
    channels_display = get_guild_showcase_channels(guild)

    # Transmission / Custom message formatted in BOLD
    if custom_msg and custom_msg.strip():
        msg_clean = f"> **\"{custom_msg.strip()}\"**"
    else:
        msg_clean = None

    if template_id == 2:
        # Template 2: Monarch Royal Deluxe (Regal Imperial Gold)
        embed = discord.Embed(
            title=f"👑 ［ IMPERIAL COURT • WELCOME ］ 👑",
            description=(
                f"### ⚜️ **Proclamation of Arrival: {member.mention}**\n"
                f"👑 ═════════════════════════════════ 👑\n"
                f"By decree of the server court, your entrance marks noble citizen **#{member_count}** in **{guild.name if guild else 'the Kingdom'}**!"
            ),
            color=discord.Color.from_rgb(255, 183, 3)
        )
        embed.set_thumbnail(url=avatar_url)
        embed.add_field(name="🛡️ **Honored Citizen**", value=f"**{member.display_name}** (`@{member.name}`)", inline=True)
        embed.add_field(name="📅 **Account Registered**", value=f"<t:{created_ts}:D>\n*(<t:{created_ts}:R>)*", inline=True)
        embed.add_field(name="🏰 **Kingdom Navigation**", value=channels_display, inline=False)
        embed.add_field(
            name="💬 **Imperial Proclamation**",
            value=msg_clean or "> **\"May your reign here be honorable, prosperous, and legendary! Welcome to the court.\"**",
            inline=False
        )
        if final_banner:
            embed.set_image(url=final_banner)
        embed.set_footer(text=f"⚜️ Royal Court Registry • Citizen #{member_count} • {guild.name if guild else 'Server'}")
        content = f"👑 **All hail and welcome** {member.mention} to **{guild.name if guild else 'the server'}**!"

    elif template_id == 3:
        # Template 3: Aesthetic Velvet Twilight (Deep Violet / Neon Amethyst)
        embed = discord.Embed(
            title=f"✨ ［ AESTHETIC SANCTUARY • WELCOME ］ ✨",
            description=(
                f"### 🌸 **Welcome to the family, {member.mention}!**\n"
                f"✦ · · ──────────────────────── · · ✦\n"
                f"We are beyond excited to welcome you to **{guild.name if guild else 'our community'}**!\n"
                f"You have arrived as our cherished member **#{member_count}**."
            ),
            color=discord.Color.from_rgb(131, 56, 236)
        )
        embed.set_thumbnail(url=avatar_url)
        embed.add_field(name="👤 **New Friend**", value=f"**{member.name}** ({member.mention})", inline=True)
        embed.add_field(name="⏰ **Joined Discord**", value=f"<t:{created_ts}:D>\n*(<t:{created_ts}:R>)*", inline=True)
        embed.add_field(name="📌 **Explore Our Spaces**", value=channels_display, inline=False)
        embed.add_field(
            name="💭 **Heartfelt Note**",
            value=msg_clean or "> **\"Make yourself at home, grab some roles, and enjoy the cozy vibes!\"**",
            inline=False
        )
        if final_banner:
            embed.set_image(url=final_banner)
        embed.set_footer(text=f"✨ Aesthetic Sanctuary • Member #{member_count} • {guild.name if guild else 'Server'}")
        content = f"✨ **Sending warm hugs and welcome to** {member.mention}!"

    else:
        # Template 1: Cyberpunk Neon Matrix (Electric Cyan / Neon)
        embed = discord.Embed(
            title=f"⚡ ［ SYSTEM INGRESS: ACCESS GRANTED ］ ⚡",
            description=(
                f"### 🌐 **Welcome to {guild.name.upper() if guild else 'THE MAINFRAME'}, {member.mention}!**\n"
                f"◈━━━━━━━━━━━━━━━━━━━━━━━━━━━━━◈\n"
                f"🛰️ Access clearance verified. You are connection sequence **#{member_count}** on the grid."
            ),
            color=discord.Color.from_rgb(0, 240, 255)
        )
        embed.set_thumbnail(url=avatar_url)
        embed.add_field(name="🏷️ **Operative Identity**", value=f"**{member.name}**\n`ID: {member.id}`", inline=True)
        embed.add_field(name="📅 **Account Registered**", value=f"<t:{created_ts}:D>\n*(<t:{created_ts}:R>)*", inline=True)
        embed.add_field(name="📍 **Server Access Points**", value=channels_display, inline=False)
        embed.add_field(
            name="💬 **Transmission**",
            value=msg_clean or "> **\"Suit up, check the mainframe channels, and enjoy your journey with us!\"**",
            inline=False
        )
        if final_banner:
            embed.set_image(url=final_banner)
        embed.set_footer(text=f"⚡ Cyberpunk Ingress • Node #{member_count} • {guild.name if guild else 'Server'}")
        content = f"⚡ **Transmission incoming** for {member.mention}! Welcome to **{guild.name if guild else 'the server'}**!"

    return content, embed

def build_farewell_card(member: discord.Member | discord.User, guild: discord.Guild, custom_message: str = None) -> tuple[str, discord.Embed]:
    server_nick = getattr(member, "nick", None) or getattr(member, "display_name", getattr(member, "name", "Member"))
    username = f"@{member.name}"
    
    avatar = getattr(member, "display_avatar", None)
    avatar_url = avatar.url if avatar else "https://cdn.discordapp.com/embed/avatars/0.png"
    
    joined_at = getattr(member, "joined_at", None)
    if joined_at:
        joined_timestamp = int(joined_at.timestamp())
        joined_str = f"<t:{joined_timestamp}:D> (<t:{joined_timestamp}:R>)"
    else:
        joined_str = "*Unknown*"
        
    # Paging Main Founder: Server Owner takes highest precedence over role mentions
    if guild and guild.owner:
        founder_tag = guild.owner.mention
    else:
        # Fallback to exact "founder" role (not "es founder" or "co-founder")
        exact_founder = discord.utils.find(lambda r: r.name.lower().strip() == "founder", guild.roles) if guild else None
        if exact_founder:
            founder_tag = exact_founder.mention
        else:
            founder_tag = f"<@{BOT_OWNER_ID}>"

    embed = discord.Embed(
        title="👋 FAREWELL • GOODBYE 👋",
        description=f"### A member has left **{guild.name if guild else 'the server'}**!",
        color=discord.Color.dark_red()
    )
    embed.set_thumbnail(url=avatar_url)
    embed.add_field(name="🏷️ Server Nickname", value=f"**{server_nick}**", inline=True)
    embed.add_field(name="👤 Username", value=f"`{username}` ({member.mention})", inline=True)
    embed.add_field(name="📅 Date Joined", value=joined_str, inline=False)
    embed.add_field(name="👑 Founder", value=founder_tag, inline=True)

    if custom_message and custom_message.strip():
        msg_clean = custom_message.strip()
        embed.add_field(name="💬 Message", value=f"> *\"{msg_clean}\"*", inline=False)

    embed.set_footer(text=f"Farewell Card • {guild.name if guild else 'Server'}")
    content = f"👋 **Farewell** {member.mention} | Paging Founder: {founder_tag}"
    return content, embed

def is_user_admin(user_id: int, guild: discord.Guild = None) -> bool:
    # 1. Supreme Bot Owner is ALWAYS permanently authorized globally across all servers
    if user_id == BOT_OWNER_ID:
        return True
    if bot.owner_id and user_id == bot.owner_id:
        return True

    if not guild:
        # Fallback for DM or unknown guild context
        return user_id in load_admin_ids()

    # 2. Server Owner is the DEFAULT administrator for their server
    if guild.owner_id and user_id == guild.owner_id:
        return True

    # 3. Extra Owners have full access to the bot and server
    if user_id in get_extraowner_ids(guild.id):
        return True

    # 4. Check per-server authorized administrators
    server_admins = get_server_admin_ids(guild.id)
    if user_id in server_admins:
        return True

    # 5. Global fallback
    admin_ids = load_admin_ids()
    return user_id in admin_ids

def is_authorized_admin(interaction: discord.Interaction) -> bool:
    return is_user_admin(interaction.user.id, interaction.guild)

def check_bot_admin():
    async def predicate(interaction: discord.Interaction) -> bool:
        if is_authorized_admin(interaction):
            return True
        await interaction.response.send_message(
            "🔒 **Access Denied**: You are not an authorized bot administrator.\n"
            "Only the Bot Owner and designated bot admins can access these commands.",
            ephemeral=True
        )
        return False
    return app_commands.check(predicate)

def is_automod_exempt(member: discord.Member) -> bool:
    if not isinstance(member, discord.Member):
        return False

    # 1. Supreme Bot Owner, Server Owner, Extra Owners, Bot Admins, and Whitelisted members are exempt
    if is_user_admin(member.id, member.guild) or is_whitelisted(member.guild, member.id):
        return True

    # 2. Members with the "monarch" role (case-insensitive) are exempt
    for role in member.roles:
        if role.name.lower() == "monarch":
            return True

    return False

def is_spam_exempt(member: discord.Member) -> bool:
    return is_automod_exempt(member)


# Helper function to get list of target user IDs (supports comma or space separated)
def get_target_user_ids():
    raw = os.getenv("TARGET_USER_IDS", "") or os.getenv("TARGET_USER_ID", "")
    target_ids = set()
    for item in raw.replace(",", " ").split():
        if item.isdigit():
            target_ids.add(int(item))
    return target_ids

# Configure the emojis to react with when any target user is mentioned
REACTION_EMOJIS = ["🔥", "👑", "👀"]

# Anti-spam tracking dictionaries
user_message_times = defaultdict(list)
user_last_messages = defaultdict(list)
user_warn_counts = {}          # Tracks how many spam warnings each user has received
guardian_response_times = {}   # Tracks cooldown to prevent repeating DM/mention messages

# Global task cancellation tracking for /stop command
active_spam_count = defaultdict(int)
stop_spam_flag = defaultdict(bool)
active_formatall_flag = defaultdict(bool)
stop_formatall_flag = defaultdict(bool)

# Configure bot intents
intents = discord.Intents.default()
intents.message_content = True  # Required to read mentions in messages
intents.members = True          # Required for roles inspection, join dates, and member management
intents.voice_states = True     # Required for voice channel management (mute, deafen, move)
intents.reactions = True        # Required for reaction roles assignment

bot = commands.Bot(
    command_prefix="!",
    intents=intents,
    heartbeat_timeout=120.0,
    max_messages=1000
)

_commands_synced = False

# Optional Web server for cloud health checks (only runs if PORT is explicitly set)
async def start_web_server():
    port_env = os.getenv("PORT")
    if not port_env:
        return

    async def handle_ping(request):
        return web.Response(text="Discord Bot is active and running!")

    try:
        app = web.Application()
        app.router.add_get("/", handle_ping)
        app.router.add_get("/health", handle_ping)
        runner = web.AppRunner(app)
        await runner.setup()
        port = int(port_env)
        site = web.TCPSite(runner, "0.0.0.0", port)
        await site.start()
        print(f"Health-check web server active on port {port}", flush=True)
    except Exception as e:
        print(f"Web server notice: {e}", flush=True)

@bot.event
async def on_connect():
    print(f"🟢 [Gateway] Connected to Discord Gateway. Latency: {bot.latency * 1000:.1f}ms", flush=True)

@bot.event
async def on_disconnect():
    print("🔴 [Gateway] Disconnected from Discord Gateway! Auto-reconnecting...", flush=True)

@bot.event
async def on_resumed():
    print(f"🔄 [Gateway] Session successfully resumed. Latency: {bot.latency * 1000:.1f}ms", flush=True)

@bot.event
async def on_ready():
    global _commands_synced
    print("=" * 45, flush=True)
    print(f"Logged in as: {bot.user.name} (ID: {bot.user.id})", flush=True)
    print(f"Connected to {len(bot.guilds)} server(s)", flush=True)
    target_ids = get_target_user_ids()
    if target_ids:
        print(f"Tracking {len(target_ids)} target user(s) for auto-reaction: {list(target_ids)}", flush=True)
    else:
        print("[Notice] No TARGET_USER_IDS configured yet in .env", flush=True)
    admin_ids = load_admin_ids()
    print(f"Loaded {len(admin_ids)} custom bot admin(s) from admins.json", flush=True)
    print("=" * 45, flush=True)
    if not auto_bump_loop.is_running():
        auto_bump_loop.start()
    if not stay_vc_watchdog_loop.is_running():
        stay_vc_watchdog_loop.start()
    if not birthday_check_loop.is_running():
        birthday_check_loop.start()
    if not voice_xp_loop.is_running():
        voice_xp_loop.start()

    # Sync slash commands once per process lifecycle to prevent hitting Discord rate limits (429)
    if not _commands_synced:
        try:
            synced = await bot.tree.sync()
            _commands_synced = True
            print(f"Successfully synced {len(synced)} slash command(s).", flush=True)
        except Exception as e:
            print(f"Failed to sync slash commands: {e}", flush=True)

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CheckFailure):
        # Already handled by custom permission check
        return
    print(f"App command error in /{interaction.command.name if interaction.command else 'unknown'}: {error}", flush=True)
    traceback.print_exc()
    error_msg = f"❌ An error occurred: {error}"
    try:
        if interaction.response.is_done():
            await interaction.followup.send(error_msg, ephemeral=True)
        else:
            await interaction.response.send_message(error_msg, ephemeral=True)
    except Exception:
        pass

# ==============================================================================
# ANTINUKE SECURITY ENGINE & AUDIT LOG RESOLVER
# ==============================================================================
async def get_audit_log_entry(guild: discord.Guild, action: discord.AuditLogAction, target_id: int = None, max_age_seconds: float = 20.0, retries: int = 5):
    """Safely poll Discord audit logs with backoff to handle gateway vs audit log latency."""
    for attempt in range(retries):
        await asyncio.sleep(0.4 if attempt == 0 else 0.7)
        try:
            now_utc = datetime.datetime.now(datetime.timezone.utc)
            async for entry in guild.audit_logs(action=action, limit=6):
                age = (now_utc - entry.created_at).total_seconds()
                if age > max_age_seconds:
                    continue
                if target_id is not None:
                    entry_target_id = None
                    try:
                        entry_target_id = getattr(entry.target, "id", None)
                    except Exception:
                        entry_target_id = getattr(entry, "_target_id", None)
                    if entry_target_id != target_id:
                        continue
                return entry
        except Exception as e:
            print(f"[Audit Log Fetch Warning] {e}", flush=True)
    return None

async def antinuke_dispatch_punishment(guild: discord.Guild, actor: discord.User | discord.Member, violation_type: str, details: str):
    """Ban an unauthorized attacker, log action, and notify the server and owner."""
    if not actor or actor.id == bot.user.id or is_whitelisted(guild, actor.id):
        return

    banned = False
    try:
        await guild.ban(actor, reason=f"[Antinuke Security] {violation_type}: {details}")
        banned = True
        print(f"[Antinuke] BANNED attacker {actor.name} ({actor.id}) for {violation_type}", flush=True)
    except discord.Forbidden:
        print(f"[Antinuke Forbidden] Bot role hierarchy is lower than {actor.name} or lacks Ban permission!", flush=True)
        if isinstance(actor, discord.Member):
            try:
                quarantine_roles = [r for r in actor.roles if r.permissions.administrator or r.permissions.manage_guild or r.permissions.manage_roles or r.permissions.manage_channels or r.permissions.ban_members or r.permissions.kick_members]
                if quarantine_roles:
                    await actor.remove_roles(*quarantine_roles, reason=f"[Antinuke Quarantine] {violation_type}")
            except Exception:
                pass
    except Exception as e:
        print(f"[Antinuke Ban Error] {e}", flush=True)

    embed = discord.Embed(
        title="🚨 ANTINUKE DEFENSE PROTOCOL TRIGGERED",
        description=(
            f"🛡️ **Unauthorized hostile action blocked on {guild.name}!**\n\n"
            f"• **Perpetrator**: {actor.mention} (`{actor.name}` | `ID: {actor.id}`)\n"
            f"• **Violation**: **{violation_type}**\n"
            f"• **Action Details**: {details}\n"
            f"• **Security Status**: {'🔨 **Perpetrator BANNED**' if banned else '⚠️ **Attacker Quarantined & Permissions Stripped**'}\n"
            f"• **Server Defense**: Full Quarantine & Restoration Applied."
        ),
        color=discord.Color.red()
    )
    if hasattr(actor, "display_avatar"):
        embed.set_thumbnail(url=actor.display_avatar.url)
    embed.set_footer(text="Antinuke System • High-Security Perimeter")

    target_channel = guild.system_channel
    if not target_channel or not target_channel.permissions_for(guild.me).send_messages:
        for ch in guild.text_channels:
            if any(term in ch.name.lower() for term in ["log", "security", "mod", "admin", "audit", "general", "chat"]):
                if ch.permissions_for(guild.me).send_messages:
                    target_channel = ch
                    break
    if not target_channel or not target_channel.permissions_for(guild.me).send_messages:
        for ch in guild.text_channels:
            if ch.permissions_for(guild.me).send_messages:
                target_channel = ch
                break

    if target_channel:
        try:
            await target_channel.send(content=f"🚨 @everyone **Antinuke Security Alert**", embed=embed)
        except Exception:
            pass

    if guild.owner:
        try:
            await guild.owner.send(embed=embed)
        except Exception:
            pass

# ==============================================================================
# AUTO DEFAULT PREFIX, AUTOROLE, GREET & ANTINUKE ON MEMBER JOIN
# ==============================================================================
@bot.event
async def on_member_join(member: discord.Member):
    guild = member.guild
    if not guild:
        return

    # 1. Antinuke: Detect unauthorized bot addition
    if member.bot:
        anti_cfg = get_antinuke_config(guild.id)
        if anti_cfg.get("enabled"):
            entry = await get_audit_log_entry(guild, discord.AuditLogAction.bot_add, target_id=member.id, max_age_seconds=20.0)
            if entry and entry.user:
                actor = entry.user
                if actor.id != bot.user.id and not is_whitelisted(guild, actor.id):
                    try:
                        await guild.ban(member, reason="[Antinuke] Unauthorized bot added by rogue user")
                    except Exception:
                        pass
                    await antinuke_dispatch_punishment(guild, actor, "Unauthorized Bot Addition", f"Invited unauthorized bot @{member.name}")
                    return

        # 2. Bot Autorole
        ar_cfg = get_autorole_config(guild.id)
        bot_role_id = ar_cfg.get("bot_role_id")
        if bot_role_id:
            bot_role = guild.get_role(bot_role_id)
            if bot_role:
                try:
                    await member.add_roles(bot_role, reason="Autorole: New Bot Account")
                    print(f"[Autorole] Assigned bot role {bot_role.name} to {member.name}.", flush=True)
                except Exception as e:
                    print(f"[Autorole Error] {e}", flush=True)
        return

    # 3. Human Autorole
    ar_cfg = get_autorole_config(guild.id)
    human_role_id = ar_cfg.get("human_role_id")
    if human_role_id:
        human_role = guild.get_role(human_role_id)
        if human_role:
            try:
                await member.add_roles(human_role, reason="Autorole: New Member Join")
                print(f"[Autorole] Assigned human role {human_role.name} to {member.name}.", flush=True)
            except Exception as e:
                print(f"[Autorole Error] {e}", flush=True)

    # 4. Greet / Welcome System (up to 3 channels)
    greet_cfg = get_greet_config(guild.id)
    if greet_cfg.get("enabled"):
        channel_ids = greet_cfg.get("channel_ids", [])
        for cid in channel_ids[:3]:
            ch = guild.get_channel(cid)
            if ch is None:
                try:
                    ch = await bot.fetch_channel(cid)
                except Exception:
                    ch = None
            if ch and isinstance(ch, discord.TextChannel):
                try:
                    content, embed = build_welcome_card(
                        member,
                        template_id=greet_cfg.get("template", 1),
                        custom_msg=greet_cfg.get("message"),
                        banner_url=greet_cfg.get("banner_url")
                    )
                    await ch.send(content=content, embed=embed)
                    print(f"[Greet] Posted welcome card for {member.name} in #{ch.name} ({guild.name}).", flush=True)
                except Exception as e:
                    print(f"[Greet Send Error] {e}", flush=True)

    # 5. Auto Default Prefix
    prefix = get_default_prefix(guild.id)
    if prefix:
        bot_member = guild.get_member(bot.user.id)
        if bot_member and member.top_role < bot_member.top_role:
            new_nick = f"{prefix} {member.display_name}".strip()
            if len(new_nick) > 32:
                new_nick = new_nick[:32]
            try:
                await member.edit(nick=new_nick)
                print(f"[Auto-Prefix] Applied prefix '{prefix}' to {member.name} in {guild.name}.", flush=True)
            except Exception as e:
                print(f"[Auto-Prefix Error] {e}", flush=True)

# ==============================================================================
# AUTOMATIC FAREWELL ON MEMBER REMOVE / LEAVE
# ==============================================================================
@bot.event
async def on_member_remove(member: discord.Member):
    # Ignore bot accounts leaving
    if getattr(member, "bot", False):
        return

    guild = member.guild
    if not guild:
        return

    # Antinuke: Detect unauthorized member kicks
    anti_cfg = get_antinuke_config(guild.id)
    if anti_cfg.get("enabled"):
        try:
            kick_entry = await get_audit_log_entry(guild, discord.AuditLogAction.kick, target_id=member.id, max_age_seconds=12.0, retries=2)
            if kick_entry and kick_entry.user:
                actor = kick_entry.user
                if actor.id != bot.user.id and not is_whitelisted(guild, actor.id):
                    await antinuke_dispatch_punishment(guild, actor, "Unauthorized Member Kick", f"Kicked {member.mention} ({member.name})")
        except Exception as e:
            print(f"[Antinuke Kick Check Warning] {e}", flush=True)

    channel_id = get_bye_channel(guild.id)
    if not channel_id:
        return

    channel = guild.get_channel(channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except Exception:
            channel = None

    if not channel or not isinstance(channel, discord.TextChannel):
        print(f"[Auto-Bye] Targeted channel {channel_id} not found in {guild.name}.", flush=True)
        return

    try:
        content, embed = build_farewell_card(member, guild)
        await channel.send(content=content, embed=embed)
        print(f"[Auto-Bye] Successfully posted farewell card for {member.name} ({member.id}) in #{channel.name} ({guild.name}).", flush=True)
    except discord.Forbidden:
        print(f"[Auto-Bye] Forbidden: Missing send or embed permissions in #{channel.name} ({guild.name}).", flush=True)
    except Exception as e:
        print(f"[Auto-Bye] Error sending farewell card for {member.name}: {e}", flush=True)

# ==============================================================================
# ANTINUKE & SERVER SECURITY AUDIT EVENT HANDLERS
# ==============================================================================
@bot.event
async def on_guild_channel_delete(channel: discord.abc.GuildChannel):
    """Antinuke: Detect unauthorized channel deletion, ban attacker, and restore channel."""
    guild = channel.guild
    cfg = get_antinuke_config(guild.id)
    if not cfg.get("enabled"):
        return

    entry = await get_audit_log_entry(guild, discord.AuditLogAction.channel_delete, target_id=channel.id)
    if entry and entry.user:
        actor = entry.user
        if actor.id != bot.user.id and not is_whitelisted(guild, actor.id):
            await antinuke_dispatch_punishment(guild, actor, "Unauthorized Channel Deletion", f"Deleted channel #{channel.name}")
            # Recreate channel
            try:
                if isinstance(channel, discord.TextChannel):
                    await guild.create_text_channel(name=channel.name, category=channel.category, topic=channel.topic, reason="[Antinuke] Restoring deleted channel")
                elif isinstance(channel, discord.VoiceChannel):
                    await guild.create_voice_channel(name=channel.name, category=channel.category, reason="[Antinuke] Restoring deleted channel")
            except Exception as err:
                print(f"[Antinuke] Failed to restore channel: {err}", flush=True)


@bot.event
async def on_guild_channel_create(channel: discord.abc.GuildChannel):
    """Antinuke: Detect unauthorized channel creation spam, delete rogue channel, and ban attacker."""
    guild = channel.guild
    cfg = get_antinuke_config(guild.id)
    if not cfg.get("enabled"):
        return

    entry = await get_audit_log_entry(guild, discord.AuditLogAction.channel_create, target_id=channel.id)
    if entry and entry.user:
        actor = entry.user
        if actor.id != bot.user.id and not is_whitelisted(guild, actor.id):
            try:
                await channel.delete(reason="[Antinuke] Unauthorized channel creation")
            except Exception:
                pass
            await antinuke_dispatch_punishment(guild, actor, "Unauthorized Channel Creation", f"Spammed channel #{channel.name}")


@bot.event
async def on_guild_role_delete(role: discord.Role):
    """Antinuke: Detect unauthorized role deletion and ban attacker."""
    guild = role.guild
    cfg = get_antinuke_config(guild.id)
    if not cfg.get("enabled"):
        return

    entry = await get_audit_log_entry(guild, discord.AuditLogAction.role_delete, target_id=role.id)
    if entry and entry.user:
        actor = entry.user
        if actor.id != bot.user.id and not is_whitelisted(guild, actor.id):
            await antinuke_dispatch_punishment(guild, actor, "Unauthorized Role Deletion", f"Deleted role @{role.name}")
            # Recreate role
            try:
                await guild.create_role(name=role.name, permissions=role.permissions, colour=role.colour, hoist=role.hoist, mentionable=role.mentionable, reason="[Antinuke] Restoring deleted role")
            except Exception as err:
                print(f"[Antinuke] Failed to restore role: {err}", flush=True)


@bot.event
async def on_guild_role_create(role: discord.Role):
    """Antinuke: Detect unauthorized role creation spam, delete rogue role, and ban attacker."""
    guild = role.guild
    cfg = get_antinuke_config(guild.id)
    if not cfg.get("enabled"):
        return

    entry = await get_audit_log_entry(guild, discord.AuditLogAction.role_create, target_id=role.id)
    if entry and entry.user:
        actor = entry.user
        if actor.id != bot.user.id and not is_whitelisted(guild, actor.id):
            try:
                await role.delete(reason="[Antinuke] Unauthorized role creation")
            except Exception:
                pass
            await antinuke_dispatch_punishment(guild, actor, "Unauthorized Role Creation", f"Spammed role @{role.name}")


@bot.event
async def on_member_ban(guild: discord.Guild, user: discord.User | discord.Member):
    """Antinuke: Detect unauthorized member bans, ban perpetrator, and unban victim."""
    cfg = get_antinuke_config(guild.id)
    if not cfg.get("enabled"):
        return

    entry = await get_audit_log_entry(guild, discord.AuditLogAction.ban, target_id=user.id)
    if entry and entry.user:
        actor = entry.user
        if actor.id != bot.user.id and not is_whitelisted(guild, actor.id):
            await antinuke_dispatch_punishment(guild, actor, "Unauthorized Member Ban", f"Banned member {user.name} ({user.id})")
            try:
                await guild.unban(user, reason="[Antinuke] Reversing unauthorized ban")
            except Exception:
                pass


@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    """Antinuke: Detect unauthorized role assignment / removal and ban perpetrator."""
    guild = after.guild
    if not guild:
        return
    cfg = get_antinuke_config(guild.id)
    if not cfg.get("enabled"):
        return

    if before.roles != after.roles:
        entry = await get_audit_log_entry(guild, discord.AuditLogAction.member_role_update, target_id=after.id, max_age_seconds=12.0)
        if entry and entry.user:
            actor = entry.user
            if actor.id != bot.user.id and not is_whitelisted(guild, actor.id):
                try:
                    await after.edit(roles=before.roles, reason="[Antinuke] Reverting unauthorized role modification")
                except Exception as err:
                    print(f"[Antinuke] Failed to revert member roles: {err}", flush=True)
                await antinuke_dispatch_punishment(guild, actor, "Unauthorized Role Assignment", f"Modified roles for {after.mention} without Whitelist authorization")


@bot.event
async def on_guild_role_update(before: discord.Role, after: discord.Role):
    """Antinuke: Detect unauthorized role permission or hierarchy tampering, revert changes, and ban attacker."""
    guild = after.guild
    if not guild:
        return
    cfg = get_antinuke_config(guild.id)
    if not cfg.get("enabled"):
        return

    if before.permissions != after.permissions or before.name != after.name:
        entry = await get_audit_log_entry(guild, discord.AuditLogAction.role_update, target_id=after.id, max_age_seconds=12.0)
        if entry and entry.user:
            actor = entry.user
            if actor.id != bot.user.id and not is_whitelisted(guild, actor.id):
                try:
                    await after.edit(name=before.name, permissions=before.permissions, colour=before.colour, hoist=before.hoist, mentionable=before.mentionable, reason="[Antinuke] Reverting unauthorized role modification")
                except Exception as err:
                    print(f"[Antinuke] Failed to revert role edit: {err}", flush=True)
                await antinuke_dispatch_punishment(guild, actor, "Unauthorized Role Tampering", f"Tampered with @{after.name} permissions or hierarchy")


@bot.event
async def on_guild_update(before: discord.Guild, after: discord.Guild):
    """Antinuke: Detect unauthorized server settings modification and ban perpetrator."""
    cfg = get_antinuke_config(after.id)
    if not cfg.get("enabled"):
        return

    entry = await get_audit_log_entry(after, discord.AuditLogAction.guild_update, max_age_seconds=12.0)
    if entry and entry.user:
        actor = entry.user
        if actor.id != bot.user.id and not is_whitelisted(after, actor.id):
            await antinuke_dispatch_punishment(after, actor, "Unauthorized Server Settings Tampering", "Modified server configuration or security settings")
        print(f"[Antinuke Error] Ban audit check: {e}", flush=True)

# ==============================================================================
# ACTION & ROLEPLAY SYSTEM (kiss, hug, slap, chase, challenge)
# ==============================================================================
ACTION_GIFS = {
    "kiss": [
        "https://media.giphy.com/media/G3va31oEEnIkM/giphy.gif",
        "https://media.giphy.com/media/FqBTvSNjNzeZG/giphy.gif",
        "https://media.giphy.com/media/nyGFcsP0kAobm/giphy.gif",
        "https://media.giphy.com/media/bm2O3nXTcKJeU/giphy.gif",
        "https://media.giphy.com/media/OSq9JVzsacLXG/giphy.gif",
        "https://media.giphy.com/media/hnNyVPIXgLdle/giphy.gif",
        "https://media.giphy.com/media/jR22gdcPiOLaE/giphy.gif",
    ],
    "hug": [
        "https://media.giphy.com/media/od5H3PmEG5EVq/giphy.gif",
        "https://media.giphy.com/media/l2QDM9Jnim1YVjTQA/giphy.gif",
        "https://media.giphy.com/media/u9BxQbM5bxvwY/giphy.gif",
        "https://media.giphy.com/media/PHZ7vmgmgZcss/giphy.gif",
        "https://media.giphy.com/media/VGACXbkf0AeGs/giphy.gif",
        "https://media.giphy.com/media/xjlC6nomocZhVXuZgM/giphy.gif",
        "https://media.giphy.com/media/sUIZWMnfd4Mb6/giphy.gif",
    ],
    "slap": [
        "https://media.giphy.com/media/Gf3AUz3eBNbTW/giphy.gif",
        "https://media.giphy.com/media/jEY642MMzxsEH445Wn/giphy.gif",
        "https://media.giphy.com/media/mEtSQlxqBtWWA/giphy.gif",
        "https://media.giphy.com/media/Zau0yrl17uzdK/giphy.gif",
        "https://media.giphy.com/media/xUO4t2gkWBxDi/giphy.gif",
        "https://media.giphy.com/media/k1uEYPE9777k4/giphy.gif",
        "https://media.giphy.com/media/AlsIdbROVJ2Lu/giphy.gif",
        "https://media.giphy.com/media/tX29X2Dx3sAXS/giphy.gif",
        "https://media.giphy.com/media/3XlEk2RxPS1m8/giphy.gif",
    ],
    "chasing": [
        "https://media.giphy.com/media/Sux3kjeP2ox1e/giphy.gif",
        "https://media.giphy.com/media/3o7ZetIsjgoYKlVe9y/giphy.gif",
        "https://media.giphy.com/media/26n6WywJyh39n1pBu/giphy.gif",
        "https://media.giphy.com/media/l41JGlWa1xOjJSsV2/giphy.gif",
        "https://media.giphy.com/media/2bUpP71bbVnZ3x7N24/giphy.gif",
        "https://media.giphy.com/media/3o6Zt9BZNiXLU8Mp9K/giphy.gif",
        "https://media.giphy.com/media/l0MYEqEzwMWFCg8rm/giphy.gif",
    ],
    "chase": [
        "https://media.giphy.com/media/Sux3kjeP2ox1e/giphy.gif",
        "https://media.giphy.com/media/3o7ZetIsjgoYKlVe9y/giphy.gif",
        "https://media.giphy.com/media/26n6WywJyh39n1pBu/giphy.gif",
        "https://media.giphy.com/media/l41JGlWa1xOjJSsV2/giphy.gif",
        "https://media.giphy.com/media/2bUpP71bbVnZ3x7N24/giphy.gif",
        "https://media.giphy.com/media/3o6Zt9BZNiXLU8Mp9K/giphy.gif",
        "https://media.giphy.com/media/l0MYEqEzwMWFCg8rm/giphy.gif",
    ],
    "challenge": [
        "https://media.giphy.com/media/13HgwGsXF0aiGY/giphy.gif",
        "https://media.giphy.com/media/2y98KScHKeaQM/giphy.gif",
        "https://media.giphy.com/media/arbHBDAq954qY/giphy.gif",
        "https://media.giphy.com/media/k59Be2mUb0sa4/giphy.gif",
        "https://media.giphy.com/media/uCow7L41IL0zS/giphy.gif",
        "https://media.giphy.com/media/bDWdJRY2IZCX6/giphy.gif",
        "https://media.giphy.com/media/yXVO50FJIJMSQ/giphy.gif",
    ]
}

ACTION_CONFIG = {
    "kiss": {
        "verb": "kissed",
        "emoji": "💋",
        "text": "{source} gave {target} a sweet kiss! 💕"
    },
    "hug": {
        "verb": "hugged",
        "emoji": "🤗",
        "text": "{source} wrapped {target} in a warm, loving hug! ✨"
    },
    "slap": {
        "verb": "slapped",
        "emoji": "👋",
        "text": "{source} slapped {target} right across the face! 💥"
    },
    "chasing": {
        "verb": "is chasing",
        "emoji": "🏃💨",
        "text": "{source} is chasing after {target} at full speed! 💨"
    },
    "chase": {
        "verb": "is chasing",
        "emoji": "🏃💨",
        "text": "{source} is chasing after {target} at full speed! 💨"
    },
    "challenge": {
        "verb": "challenged",
        "emoji": "⚔️",
        "text": "{source} has stepped up and challenged {target} to a battle! 💥"
    }
}

ACTION_WORDS = {
    "kiss": "kiss",
    "hug": "hug",
    "slap": "slap",
    "chasing": "chase",
    "chase": "chase",
    "challenge": "challenge",
    "fight": "challenge"
}

# Deduplication cache to prevent any double-sending of actions
recent_action_events = {}

def is_duplicate_action(channel_id: int, action_key: str, actor_id: int, target_id: int) -> bool:
    now = time.time()
    # Expire entries older than 8 seconds
    expired = [k for k, t in recent_action_events.items() if now - t > 8.0]
    for k in expired:
        recent_action_events.pop(k, None)

    key = (channel_id, action_key, actor_id, target_id)
    if key in recent_action_events and now - recent_action_events[key] < 3.5:
        return True
    recent_action_events[key] = now
    return False

def make_action_payload(action_key: str, source, target):
    cfg = ACTION_CONFIG.get(action_key, ACTION_CONFIG["kiss"])
    gif_list = ACTION_GIFS.get(action_key, ACTION_GIFS["kiss"])
    gif_url = random.choice(gif_list)

    # Clean mention formatting without deprecated '!'
    source_mention = f"<@{source.id}>" if hasattr(source, "id") else f"@{getattr(source, 'display_name', getattr(source, 'name', 'Someone'))}"
    target_mention = f"<@{target.id}>" if hasattr(target, "id") else f"@{getattr(target, 'display_name', getattr(target, 'name', 'Someone'))}"

    text_line = cfg["text"].format(source=source_mention, target=target_mention)
    content = f"### {cfg['emoji']} {text_line}"

    embed = discord.Embed()
    embed.set_image(url=gif_url)

    return content, embed

def make_challenge_payload(source, target, custom_shout: str = None):
    shout = custom_shout.strip() if custom_shout and custom_shout.strip() else "COME LETS FIGHT"
    shout = shout.strip('"`')
    gif_list = ACTION_GIFS.get("challenge", [
        "https://media.giphy.com/media/13HgwGsXF0aiGY/giphy.gif",
        "https://media.giphy.com/media/2y98KScHKeaQM/giphy.gif",
        "https://media.giphy.com/media/arbHBDAq954qY/giphy.gif",
        "https://media.giphy.com/media/k59Be2mUb0sa4/giphy.gif",
        "https://media.giphy.com/media/uCow7L41IL0zS/giphy.gif",
        "https://media.giphy.com/media/bDWdJRY2IZCX6/giphy.gif",
        "https://media.giphy.com/media/yXVO50FJIJMSQ/giphy.gif",
    ])
    gif_url = random.choice(gif_list)

    source_mention = f"<@{source.id}>" if hasattr(source, "id") else f"@{getattr(source, 'display_name', getattr(source, 'name', 'Someone'))}"
    target_mention = f"<@{target.id}>" if hasattr(target, "id") else f"@{getattr(target, 'display_name', getattr(target, 'name', 'Someone'))}"

    content = f"### ⚔️ {source_mention} HAS CHALLENGED {target_mention} TO A DUEL! 🥊\n> 💥 **\"{shout.upper()}\"**"

    embed = discord.Embed(
        title="⚔️ COMBAT CHALLENGE ISSUED ⚔️",
        description=f"### 🥊 {source_mention} ⚔️ **VS** ⚔️ {target_mention} 🥊\n\n> 🔥 **\"{shout.upper()}\"**\n\n*Will {target_mention} step into the arena or flee? Respond below!*",
        color=discord.Color.brand_red()
    )
    embed.set_author(name="𝐇𝐄𝐋𝐋𝐅𝐈𝐑𝐄 𝐀𝐑𝐄𝐍𝐀 • DUEL SHOWDOWN", icon_url="https://cdn.discordapp.com/emojis/853304564887355412.gif")
    embed.set_image(url=gif_url)
    embed.set_footer(text="Hellfire Combat Arena • Targeted member has 2 minutes to respond")

    return content, embed

def make_hp_bar(current: int, total: int = 100, length: int = 10) -> str:
    current = max(0, min(current, total))
    filled = int(round(length * (current / total)))
    empty = length - filled
    if current > 50:
        bar = "🟩" * filled + "⬜" * empty
    elif current > 20:
        bar = "🟨" * filled + "⬜" * empty
    else:
        bar = "🟥" * filled + "⬜" * empty
    return f"`[{bar}]` **{current}/{total} HP**"

class CombatGameView(discord.ui.View):
    def __init__(self, challenger_id: int, target_id: int):
        super().__init__(timeout=180)
        self.p1 = challenger_id
        self.p2 = target_id
        self.turn = challenger_id
        self.hp = {challenger_id: 100, target_id: 100}
        self.shielded = {challenger_id: False, target_id: False}
        self.potions = {challenger_id: 1, target_id: 1}
        self.round_num = 1
        self.log = []

    def opponent_id(self, user_id: int) -> int:
        return self.p2 if user_id == self.p1 else self.p1

    def make_embed(self, last_action: str = None) -> discord.Embed:
        if last_action:
            self.log.append(last_action)
            if len(self.log) > 4:
                self.log.pop(0)

        p1_bar = make_hp_bar(self.hp[self.p1])
        p2_bar = make_hp_bar(self.hp[self.p2])

        is_game_over = self.hp[self.p1] <= 0 or self.hp[self.p2] <= 0
        if is_game_over:
            winner = self.p1 if self.hp[self.p1] > 0 else self.p2
            loser = self.p2 if winner == self.p1 else self.p1
            embed = discord.Embed(
                title="🏆 DUEL DECIDED • VICTORY! 👑",
                description=(
                    f"### 👑 <@{winner}> HAS DEFEATED <@{loser}> IN COMBAT! 🥊\n\n"
                    f"**Remaining HP:** `{max(0, self.hp[winner])}/100`\n"
                    f"**Rounds Fought:** `{self.round_num}`\n\n"
                    f"🎉 **HAIL THE ARENA CHAMPION!**"
                ),
                color=discord.Color.green()
            )
            embed.set_thumbnail(url="https://media.giphy.com/media/26u4cqiYI30juCOGY/giphy.gif")
            embed.set_footer(text="Hellfire Combat Arena • Match Concluded")
            return embed

        turn_mention = f"<@{self.turn}>"
        embed = discord.Embed(
            title=f"⚔️ COMBAT ARENA • ROUND {self.round_num} 💥",
            description=(
                f"**<@{self.p1}>**\n{p1_bar}\n\n"
                f"**<@{self.p2}>**\n{p2_bar}\n\n"
                f"👉 **Current Turn:** {turn_mention}\n"
                f"*Choose an action below before timeout!*"
            ),
            color=discord.Color.red() if self.round_num % 2 == 1 else discord.Color.blue()
        )
        if self.log:
            embed.add_field(name="📜 Combat Log", value="\n".join(f"> {entry}" for entry in self.log), inline=False)
        embed.set_footer(text="Hellfire Combat Arena • Turn-based battle")
        return embed

    async def _handle_action(self, interaction: discord.Interaction, action_type: str):
        if interaction.user.id not in (self.p1, self.p2):
            await interaction.response.send_message("❌ You are not part of this duel!", ephemeral=True)
            return

        if interaction.user.id != self.turn:
            await interaction.response.send_message("⏳ It's not your turn yet! Wait for your opponent.", ephemeral=True)
            return

        attacker = self.turn
        defender = self.opponent_id(attacker)
        action_msg = ""

        if action_type == "strike":
            dmg = random.randint(18, 28)
            if self.shielded[defender]:
                dmg = max(1, dmg // 2)
                reflect = random.randint(8, 14)
                self.hp[attacker] = max(0, self.hp[attacker] - reflect)
                action_msg = f"🛡️ <@{defender}> blocked! <@{attacker}> dealt **{dmg}** DMG, but took **{reflect}** reflected DMG!"
                self.shielded[defender] = False
            else:
                action_msg = f"⚔️ <@{attacker}> struck <@{defender}> for **{dmg}** DMG!"
            self.hp[defender] = max(0, self.hp[defender] - dmg)

        elif action_type == "shield":
            self.shielded[attacker] = True
            action_msg = f"🛡️ <@{attacker}> braced with their Shield! Next attack will be halved & countered."

        elif action_type == "heavy":
            if random.random() < 0.65:
                dmg = random.randint(35, 50)
                if self.shielded[defender]:
                    dmg = max(1, dmg // 2)
                    self.shielded[defender] = False
                    action_msg = f"⚡ <@{attacker}> CRUSHED through shield with Heavy Slam for **{dmg}** DMG!"
                else:
                    action_msg = f"💥 **CRITICAL SMASH!** <@{attacker}> dealt **{dmg}** massive DMG to <@{defender}>!"
                self.hp[defender] = max(0, self.hp[defender] - dmg)
            else:
                action_msg = f"💨 <@{attacker}> swung wildly with Heavy Slam and **MISSED**!"

        elif action_type == "potion":
            if self.potions[attacker] <= 0:
                await interaction.response.send_message("❌ You have already used your Health Potion!", ephemeral=True)
                return
            heal = random.randint(25, 35)
            self.hp[attacker] = min(100, self.hp[attacker] + heal)
            self.potions[attacker] -= 1
            action_msg = f"🧪 <@{attacker}> drank a Health Potion and healed **+{heal} HP**!"

        self.turn = defender
        self.round_num += 1

        is_game_over = self.hp[self.p1] <= 0 or self.hp[self.p2] <= 0
        if is_game_over:
            for child in self.children:
                child.disabled = True
            embed = self.make_embed(action_msg)
            winner = self.p1 if self.hp[self.p1] > 0 else self.p2
            await interaction.response.edit_message(content=f"### 🏆 ARENA VICTOR: <@{winner}>! 🥊", embed=embed, view=self)
        else:
            embed = self.make_embed(action_msg)
            await interaction.response.edit_message(content=f"### ⚔️ ARENA DUEL: <@{self.p1}> VS <@{self.p2}>", embed=embed, view=self)

    @discord.ui.button(label="Strike", style=discord.ButtonStyle.danger, emoji="⚔️", row=0)
    async def btn_strike(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_action(interaction, "strike")

    @discord.ui.button(label="Shield / Counter", style=discord.ButtonStyle.secondary, emoji="🛡️", row=0)
    async def btn_shield(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_action(interaction, "shield")

    @discord.ui.button(label="Heavy Slam", style=discord.ButtonStyle.primary, emoji="⚡", row=0)
    async def btn_heavy(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_action(interaction, "heavy")

    @discord.ui.button(label="Potion", style=discord.ButtonStyle.success, emoji="🧪", row=0)
    async def btn_potion(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_action(interaction, "potion")

    @discord.ui.button(label="Surrender", style=discord.ButtonStyle.secondary, emoji="🏳️", row=1)
    async def btn_forfeit(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id not in (self.p1, self.p2):
            await interaction.response.send_message("❌ You are not part of this duel!", ephemeral=True)
            return
        surrenderer = interaction.user.id
        winner = self.opponent_id(surrenderer)
        for child in self.children:
            child.disabled = True
        embed = discord.Embed(
            title="🏳️ MATCH CONCLUDED • SURRENDER",
            description=f"### 🏆 <@{winner}> WINS BY FORFEIT!\n<@{surrenderer}> surrendered the match! 🏳️",
            color=discord.Color.light_grey()
        )
        embed.set_footer(text="Hellfire Combat Arena • Match Concluded")
        await interaction.response.edit_message(content=f"### 🏆 WINNER: <@{winner}> (By Forfeit)", embed=embed, view=self)

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True


class RPSGameView(discord.ui.View):
    def __init__(self, challenger_id: int, target_id: int):
        super().__init__(timeout=120)
        self.p1 = challenger_id
        self.p2 = target_id
        self.choices = {}
        self.round_num = 1

    def make_embed(self, status: str = "") -> discord.Embed:
        p1_status = "✅ Locked In" if self.p1 in self.choices else "⏳ Choosing..."
        p2_status = "✅ Locked In" if self.p2 in self.choices else "⏳ Choosing..."

        embed = discord.Embed(
            title=f"✂️ ROCK-PAPER-SCISSORS DUEL • ROUND {self.round_num} 🪨",
            description=(
                f"### <@{self.p1}> ⚔️ **VS** ⚔️ <@{self.p2}>\n\n"
                f"**<@{self.p1}>**: {p1_status}\n"
                f"**<@{self.p2}>**: {p2_status}\n\n"
                f"*{status}*"
            ),
            color=discord.Color.purple()
        )
        embed.set_footer(text="Click a button below to choose your move secretly")
        return embed

    async def _choose(self, interaction: discord.Interaction, choice: str):
        if interaction.user.id not in (self.p1, self.p2):
            await interaction.response.send_message("❌ You are not part of this duel!", ephemeral=True)
            return

        self.choices[interaction.user.id] = choice
        emoji_map = {"rock": "🪨", "paper": "📄", "scissors": "✂️"}
        await interaction.response.send_message(f"🔒 You picked **{emoji_map[choice]} {choice.upper()}**! Locked in secretly.", ephemeral=True)

        if self.p1 in self.choices and self.p2 in self.choices:
            c1 = self.choices[self.p1]
            c2 = self.choices[self.p2]
            e1 = emoji_map[c1]
            e2 = emoji_map[c2]

            if c1 == c2:
                self.choices.clear()
                self.round_num += 1
                embed = self.make_embed(f"🤝 **TIE!** Both picked {e1} {c1.upper()}!\nResetting for **Sudden Death Round {self.round_num}**! Choose again below!")
                await interaction.message.edit(embed=embed, view=self)
            else:
                wins = {"rock": "scissors", "scissors": "paper", "paper": "rock"}
                winner = self.p1 if wins[c1] == c2 else self.p2
                w_choice = c1 if winner == self.p1 else c2
                l_choice = c2 if winner == self.p1 else c1

                for child in self.children:
                    child.disabled = True

                embed = discord.Embed(
                    title="🏆 DUEL DECIDED • VICTORY! 👑",
                    description=(
                        f"### 👑 <@{winner}> IS THE WINNER! 🎉\n\n"
                        f"• <@{self.p1}> chose **{e1} {c1.upper()}**\n"
                        f"• <@{self.p2}> chose **{e2} {c2.upper()}**\n\n"
                        f"> 💥 **{emoji_map[w_choice]} {w_choice.upper()} BEATS {emoji_map[l_choice]} {l_choice.upper()}!**\n\n"
                        f"🏆 Hail <@{winner}>!"
                    ),
                    color=discord.Color.green()
                )
                embed.set_footer(text="Hellfire RPS Duel • Match Concluded")
                await interaction.message.edit(content=f"### 🏆 WINNER: <@{winner}>! 🪨📄✂️", embed=embed, view=self)
        else:
            embed = self.make_embed("One fighter has locked in! Waiting for opponent...")
            await interaction.message.edit(embed=embed, view=self)

    @discord.ui.button(label="Rock", style=discord.ButtonStyle.secondary, emoji="🪨")
    async def btn_rock(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._choose(interaction, "rock")

    @discord.ui.button(label="Paper", style=discord.ButtonStyle.secondary, emoji="📄")
    async def btn_paper(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._choose(interaction, "paper")

    @discord.ui.button(label="Scissors", style=discord.ButtonStyle.secondary, emoji="✂️")
    async def btn_scissors(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._choose(interaction, "scissors")


class DiceGameView(discord.ui.View):
    def __init__(self, challenger_id: int, target_id: int):
        super().__init__(timeout=120)
        self.p1 = challenger_id
        self.p2 = target_id
        self.rolls = {}
        self.round_num = 1

    def make_embed(self, status: str = "") -> discord.Embed:
        p1_status = f"🎲 Rolled **{self.rolls[self.p1][2]}** ({self.rolls[self.p1][0]} + {self.rolls[self.p1][1]})" if self.p1 in self.rolls else "⏳ Waiting to roll..."
        p2_status = f"🎲 Rolled **{self.rolls[self.p2][2]}** ({self.rolls[self.p2][0]} + {self.rolls[self.p2][1]})" if self.p2 in self.rolls else "⏳ Waiting to roll..."

        embed = discord.Embed(
            title=f"🎲 HIGH-ROLLER DICE DUEL • ROUND {self.round_num} 🎲",
            description=(
                f"### <@{self.p1}> ⚔️ **VS** ⚔️ <@{self.p2}>\n\n"
                f"**<@{self.p1}>**: {p1_status}\n"
                f"**<@{self.p2}>**: {p2_status}\n\n"
                f"*{status}*"
            ),
            color=discord.Color.gold()
        )
        embed.set_footer(text="Click 'Roll Dice' to roll your 2d20 dice pair!")
        return embed

    @discord.ui.button(label="🎲 Roll 2d20 Dice", style=discord.ButtonStyle.success)
    async def btn_roll(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id not in (self.p1, self.p2):
            await interaction.response.send_message("❌ You are not part of this duel!", ephemeral=True)
            return

        if interaction.user.id in self.rolls:
            await interaction.response.send_message(f"⚠️ You already rolled **{self.rolls[interaction.user.id][2]}**! Waiting for opponent.", ephemeral=True)
            return

        d1 = random.randint(1, 20)
        d2 = random.randint(1, 20)
        tot = d1 + d2
        self.rolls[interaction.user.id] = (d1, d2, tot)

        if self.p1 in self.rolls and self.p2 in self.rolls:
            r1 = self.rolls[self.p1][2]
            r2 = self.rolls[self.p2][2]

            if r1 == r2:
                self.rolls.clear()
                self.round_num += 1
                embed = self.make_embed(f"🤝 **TIE!** Both rolled **{r1}**!\nRolling again for **Sudden Death Round {self.round_num}**!")
                await interaction.response.edit_message(embed=embed, view=self)
            else:
                winner = self.p1 if r1 > r2 else self.p2
                w_tot = max(r1, r2)
                l_tot = min(r1, r2)

                for child in self.children:
                    child.disabled = True

                embed = discord.Embed(
                    title="🏆 DUEL DECIDED • VICTORY! 👑",
                    description=(
                        f"### 👑 <@{winner}> HAS WON THE DICE DUEL! 🎲\n\n"
                        f"• <@{self.p1}>: 🎲 **{r1}** ({self.rolls[self.p1][0]} + {self.rolls[self.p1][1]})\n"
                        f"• <@{self.p2}>: 🎲 **{r2}** ({self.rolls[self.p2][0]} + {self.rolls[self.p2][1]})\n\n"
                        f"> 💥 **Winning Roll: {w_tot} vs {l_tot}!**\n\n"
                        f"🏆 Congratulations <@{winner}>!"
                    ),
                    color=discord.Color.green()
                )
                embed.set_footer(text="Hellfire Dice Duel • Match Concluded")
                await interaction.response.edit_message(content=f"### 🏆 WINNER: <@{winner}> with a roll of {w_tot}! 🎲", embed=embed, view=self)
        else:
            embed = self.make_embed(f"<@{interaction.user.id}> rolled **{tot}**! Waiting for opponent...")
            await interaction.response.edit_message(embed=embed, view=self)


class ChallengeSelectGameView(discord.ui.View):
    def __init__(self, challenger_id: int, target_id: int, shout: str = "COME LETS FIGHT"):
        super().__init__(timeout=120)
        self.challenger_id = challenger_id
        self.target_id = target_id
        self.shout = shout

    def check_player(self, user_id: int) -> bool:
        return user_id in (self.challenger_id, self.target_id)

    @discord.ui.button(label="⚔️ HP Combat Duel", style=discord.ButtonStyle.danger, emoji="🥊")
    async def btn_combat(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.check_player(interaction.user.id):
            await interaction.response.send_message("❌ You are not part of this duel!", ephemeral=True)
            return
        view = CombatGameView(self.challenger_id, self.target_id)
        embed = view.make_embed("🔥 Combat Arena started! Challenger strikes first.")
        await interaction.response.edit_message(content=f"### ⚔️ ARENA DUEL: <@{self.challenger_id}> VS <@{self.target_id}>", embed=embed, view=view)

    @discord.ui.button(label="✂️ Rock-Paper-Scissors", style=discord.ButtonStyle.primary, emoji="🪨")
    async def btn_rps(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.check_player(interaction.user.id):
            await interaction.response.send_message("❌ You are not part of this duel!", ephemeral=True)
            return
        view = RPSGameView(self.challenger_id, self.target_id)
        embed = view.make_embed("Lock in your move secretly using the buttons below!")
        await interaction.response.edit_message(content=f"### 🪨 ROCK-PAPER-SCISSORS DUEL: <@{self.challenger_id}> VS <@{self.target_id}>", embed=embed, view=view)

    @discord.ui.button(label="🎲 High-Roller Dice", style=discord.ButtonStyle.success, emoji="🎲")
    async def btn_dice(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.check_player(interaction.user.id):
            await interaction.response.send_message("❌ You are not part of this duel!", ephemeral=True)
            return
        view = DiceGameView(self.challenger_id, self.target_id)
        embed = view.make_embed("Roll your 20-sided dice! Highest roll takes the crown.")
        await interaction.response.edit_message(content=f"### 🎲 HIGH-ROLLER DICE DUEL: <@{self.challenger_id}> VS <@{self.target_id}>", embed=embed, view=view)


class ChallengeResponseView(discord.ui.View):
    def __init__(self, challenger_id: int, target_id: int, shout: str = "COME LETS FIGHT", game: str = "select"):
        super().__init__(timeout=120)
        self.challenger_id = challenger_id
        self.target_id = target_id
        self.shout = shout
        self.game = game

    @discord.ui.button(label="⚔️ Accept Battle", style=discord.ButtonStyle.danger, custom_id="challenge_accept")
    async def accept_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.target_id:
            await interaction.response.send_message("❌ Only the challenged opponent can accept this battle!", ephemeral=True)
            return

        for child in self.children:
            child.disabled = True

        if self.game == "combat":
            view = CombatGameView(self.challenger_id, self.target_id)
            embed = view.make_embed("🔥 Combat Arena started! Challenger strikes first.")
            await interaction.response.edit_message(content=f"### ⚔️ ARENA DUEL: <@{self.challenger_id}> VS <@{self.target_id}>", embed=embed, view=view)
        elif self.game == "rps":
            view = RPSGameView(self.challenger_id, self.target_id)
            embed = view.make_embed("Lock in your move secretly using the buttons below!")
            await interaction.response.edit_message(content=f"### 🪨 ROCK-PAPER-SCISSORS DUEL: <@{self.challenger_id}> VS <@{self.target_id}>", embed=embed, view=view)
        elif self.game == "dice":
            view = DiceGameView(self.challenger_id, self.target_id)
            embed = view.make_embed("Roll your 20-sided dice! Highest roll takes the crown.")
            await interaction.response.edit_message(content=f"### 🎲 HIGH-ROLLER DICE DUEL: <@{self.challenger_id}> VS <@{self.target_id}>", embed=embed, view=view)
        else:
            select_view = ChallengeSelectGameView(self.challenger_id, self.target_id, self.shout)
            embed = discord.Embed(
                title="⚔️ BATTLE ACCEPTED • CHOOSE A GAME MODE! 🎮",
                description=(
                    f"### 🥊 <@{self.challenger_id}> ⚔️ **VS** ⚔️ {interaction.user.mention}\n\n"
                    f"Both warriors are in the ring! Select a mini-game below to fight:\n\n"
                    f"• 🥊 **⚔️ HP Combat Duel**: 100 HP Turn-based RPG fight with Strikes, Shields & Potions\n"
                    f"• 🪨 **✂️ Rock-Paper-Scissors**: Secret move duel with instant reveal & sudden death\n"
                    f"• 🎲 **High-Roller Dice**: Roll 2d20 dice showdown — highest roll wins\n"
                ),
                color=discord.Color.gold()
            )
            embed.set_footer(text="Hellfire Combat Arena • Either fighter can select a game mode")
            await interaction.response.edit_message(
                content=f"### 🔥 CHALLENGE ACCEPTED! 🥊 <@{self.challenger_id}> ⚔️ {interaction.user.mention}",
                embed=embed,
                view=select_view
            )

    @discord.ui.button(label="🏳️ Decline / Flee", style=discord.ButtonStyle.secondary, custom_id="challenge_decline")
    async def decline_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.target_id:
            await interaction.response.send_message("❌ Only the challenged opponent can decline this battle!", ephemeral=True)
            return

        for child in self.children:
            child.disabled = True

        embed = interaction.message.embeds[0] if interaction.message.embeds else discord.Embed()
        embed.color = discord.Color.light_grey()
        embed.title = "🏳️ CHALLENGE DECLINED"
        embed.set_footer(text="Hellfire Combat Arena • Opponent Fled")

        result_content = (
            f"### 🏳️ CHALLENGE DECLINED\n"
            f"{interaction.user.mention} backed down and fled from <@{self.challenger_id}>! 🏃💨\n"
            f"> *Maybe next time...*"
        )
        await interaction.response.edit_message(content=result_content, embed=embed, view=self)

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True


# ==============================================================================
# 1. AUTO-REACT ON MENTION & AUTOMATED ANTI-SPAM DETECTION
# ==============================================================================
# ==============================================================================
# AUTOMATIC SERVER BUMP BACKGROUND TASK
# ==============================================================================
@tasks.loop(seconds=30)
async def auto_bump_loop():
    now = time.time()
    config = load_server_config()
    for guild_id_str, g_data in list(config.items()):
        bump_cfg = g_data.get("bump")
        if not bump_cfg or not bump_cfg.get("enabled"):
            continue

        next_bump = bump_cfg.get("next_bump_timestamp", 0)
        if now >= next_bump:
            try:
                guild_id = int(guild_id_str)
            except ValueError:
                continue

            guild = bot.get_guild(guild_id)
            if not guild:
                continue

            channel_id = bump_cfg.get("channel_id")
            channel = guild.get_channel(channel_id)
            if not channel or not isinstance(channel, discord.TextChannel):
                continue

            interval_mins = bump_cfg.get("interval_minutes", 120)
            bump_text = bump_cfg.get("bump_text", "/bump")
            ping_role_id = bump_cfg.get("ping_role_id")

            # Advance next bump timestamp
            bump_cfg["next_bump_timestamp"] = now + (interval_mins * 60)
            config[guild_id_str]["bump"] = bump_cfg
            save_server_config(config)

            # Post the clean bump reminder card with ping
            ping_str = f"<@&{ping_role_id}> " if ping_role_id else ""
            embed = discord.Embed(
                title="🚀 BUMP TIME! It is time to bump the server!",
                description=(
                    f"The **{interval_mins}-minute** cooldown has ended!\n\n"
                    f"👉 Run Disboard's **`/bump`** in {channel.mention} now to keep **{guild.name}** at the top of the list!\n\n"
                    f"• **Next Reminder**: <t:{int(now + (interval_mins * 60))}:R> (<t:{int(now + (interval_mins * 60))}:t>)"
                ),
                color=discord.Color.from_rgb(255, 115, 250)
            )
            embed.set_footer(text=f"Bump Notifier • Next reminder in {interval_mins} mins")
            try:
                await channel.send(content=ping_str if ping_str else None, embed=embed)
                print(f"[Auto-Bump] Sent bump notification in #{channel.name} ({guild.name})", flush=True)
            except Exception as e:
                print(f"[Auto-Bump] Error sending reminder card: {e}", flush=True)

@auto_bump_loop.before_loop
async def before_auto_bump_loop():
    await bot.wait_until_ready()

# ==============================================================================
# SINGLE-FLIGHT 24/7 VOICE ENGINE (Zero Race Conditions)
# ==============================================================================
voice_connect_locks = defaultdict(asyncio.Lock)
voice_connecting_flag = defaultdict(bool)

async def safe_voice_connect(guild: discord.Guild, channel: discord.VoiceChannel | discord.StageChannel, reason: str = "24/7 Engine") -> bool:
    """Safely connects to a voice channel with strict single-flight locking to prevent race conditions and timeouts."""
    if not guild or not channel:
        return False

    if voice_connecting_flag[guild.id]:
        return False  # Handshake already in progress, avoid collision!

    async with voice_connect_locks[guild.id]:
        # Double check if already connected to target channel
        vc = guild.voice_client
        if vc and vc.is_connected() and vc.channel and vc.channel.id == channel.id:
            return True

        voice_connecting_flag[guild.id] = True
        try:
            if vc and vc.is_connected():
                # Already connected in another channel, move cleanly
                await vc.move_to(channel)
                print(f"[{reason}] Moved bot to #{channel.name} in {guild.name}.", flush=True)
                return True

            # Force disconnect stale voice clients
            if vc:
                try:
                    await vc.disconnect(force=True)
                except Exception:
                    pass
                await asyncio.sleep(0.5)

            print(f"[{reason}] Connecting bot to #{channel.name} in {guild.name} (DAVE E2EE)...", flush=True)
            await channel.connect(timeout=25.0, reconnect=True, self_deaf=True)
            print(f"[{reason}] Successfully connected bot to #{channel.name} in {guild.name}!", flush=True)
            return True
        except Exception as e:
            print(f"[{reason} Error] Could not connect to #{channel.name}: {e}", flush=True)
            return False
        finally:
            voice_connecting_flag[guild.id] = False

# ==============================================================================
# 24/7 STAY-IN-VOICE RECONNECT WATCHDOG LOOP
# ==============================================================================
@tasks.loop(seconds=60)
async def stay_vc_watchdog_loop():
    """Keeps the bot connected in 24/7 voice channels automatically."""
    config = load_server_config()
    for guild_id_str, g_data in list(config.items()):
        stay_cfg = g_data.get("stay_vc")
        if not stay_cfg or not stay_cfg.get("enabled"):
            continue

        try:
            guild_id = int(guild_id_str)
        except ValueError:
            continue

        guild = bot.get_guild(guild_id)
        if not guild or voice_connecting_flag[guild.id]:
            continue

        channel_id = stay_cfg.get("channel_id")
        channel = guild.get_channel(channel_id)
        if not channel or not isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
            continue

        vc = guild.voice_client
        if vc is None or not vc.is_connected() or (vc.channel and vc.channel.id != channel.id):
            await safe_voice_connect(guild, channel, reason="24/7 Watchdog")

@stay_vc_watchdog_loop.before_loop
async def before_stay_vc_watchdog_loop():
    await bot.wait_until_ready()


# ==============================================================================
# SNIPE & DELETED MESSAGES CACHE (Last 20 per channel)
# ==============================================================================
deleted_messages_cache = defaultdict(lambda: deque(maxlen=20))

@bot.event
async def on_message_delete(message: discord.Message):
    if not message.guild or message.author.bot:
        return
    deleted_messages_cache[message.channel.id].appendleft({
        "author_id": message.author.id,
        "author_name": message.author.display_name,
        "author_avatar": message.author.display_avatar.url,
        "author_mention": message.author.mention,
        "content": message.content or "*[No text content]*",
        "created_at": int(message.created_at.timestamp()) if message.created_at else None,
        "deleted_at": int(time.time()),
        "attachments": [a.url for a in message.attachments if a.url]
    })


@bot.event
async def on_message(message: discord.Message):
    # Detect bump confirmation or cooldown messages from Bump Bots (Disboard, Olympus, etc.)
    if message.guild and message.author.bot:
        bump_cfg = get_bump_config(message.guild.id)
        if bump_cfg and bump_cfg.get("enabled"):
            full_text = (message.content or "").lower()
            for emb in message.embeds:
                if emb.title:
                    full_text += " " + emb.title.lower()
                if emb.description:
                    full_text += " " + emb.description.lower()
                for field in emb.fields:
                    full_text += f" {field.name.lower()} {field.value.lower()}"

            # Check if bump was successful
            if any(k in full_text for k in ["bump done", "bumped", "check it on disboard", "disboard.org"]):
                interval_mins = bump_cfg.get("interval_minutes", 120)
                bump_cfg["next_bump_timestamp"] = time.time() + (interval_mins * 60)
                save_bump_config(message.guild.id, bump_cfg)
                print(f"[Auto-Bump] Bump success detected in {message.guild.name}. Next bump in {interval_mins}m.", flush=True)

            # Check if cooldown wait message was returned (e.g. 'wait another 45 minutes')
            wait_match = re.search(r"wait(?: another)? (\d+)\s*minute", full_text)
            if wait_match:
                wait_mins = int(wait_match.group(1))
                bump_cfg["next_bump_timestamp"] = time.time() + (wait_mins * 60)
                save_bump_config(message.guild.id, bump_cfg)
                print(f"[Auto-Bump] Bump cooldown detected in {message.guild.name}: {wait_mins}m remaining.", flush=True)
        return

    # Ignore messages sent by bots
    if message.author.bot:
        return

    # 0. Handle Direct Messages (DM) or Text Mentions to the Bot
    is_dm = message.guild is None or isinstance(message.channel, discord.DMChannel)
    is_bot_mention = False
    if message.guild and bot.user in message.mentions and not message.mention_everyone:
        is_bot_mention = True

    if is_dm or is_bot_mention:
        now = time.time()
        uid = message.author.id

        # 60s cooldown per user to prevent repeating the message again and again
        if now - guardian_response_times.get(uid, 0) < 60.0:
            if is_dm:
                return
        else:
            guardian_response_times[uid] = now

            # Above the banner image
            content_top = (
                "🔱 𝐇𝐄𝐋𝐋𝐅𝐈𝐑𝐄 𝐒𝐘𝐒𝐓𝐄𝐌 𝐀𝐂𝐓𝐈𝐕𝐀𝐓𝐄𝐃 🔱\n"
                " 𖤍𓆰𝐆𝐫𝐞𝐞𝐭𝐢𝐧𝐠𝐬, 𝐌𝐨𝐫𝐭𝐚𝐥. 𝐘𝐨𝐮 𝐡𝐚𝐯𝐞 𝐞𝐧𝐭𝐞𝐫𝐞𝐝 𝐭𝐡𝐞 𝐝𝐨𝐦𝐚𝐢𝐧 𝐨𝐟 𝐇𝐄𝐋𝐋𝐅𝐈𝐑𝐄 𝐂𝐋𝐔𝐁. 👁️⃤ 𝐖𝐡𝐚𝐭 𝐛𝐫𝐢𝐧𝐠𝐬 𝐲𝐨𝐮 𝐡𝐞𝐫𝐞? 𝐂𝐡𝐨𝐨𝐬𝐞 𝐲𝐨𝐮𝐫 𝐩𝐚𝐭𝐡 𝐛𝐞𝐥𝐨𝐰.  ⚔️ 𝐒𝐞𝐫𝐯𝐞𝐫 𝐒𝐮𝐩𝐩𝐨𝐫𝐭 ⚜️ 𝐇𝐄𝐋𝐋𝐅𝐈𝐑𝐄 𝐂𝐨𝐦𝐦𝐮𝐧𝐢𝐭𝐲 ⚜️ 𝐎𝐰𝐧𝐞𝐫 𝐂𝐨𝐧𝐭𝐚𝐜𝐭  𝐓𝐡𝐞 𝐟𝐥𝐚𝐦𝐞𝐬 𝐚𝐫𝐞 𝐰𝐚𝐭𝐜𝐡𝐢𝐧𝐠 𝐲𝐨𝐮.🕯️"
            )

            # The banner image only (no text on the banner embed)
            embed_banner = discord.Embed(color=discord.Color.from_rgb(180, 0, 0))
            embed_banner.set_image(url="https://cdn.discordapp.com/banners/1437070336335282258/a_9ee491ae60616f9ff0007b5382f210ca.gif?size=1024")

            # Below the banner image
            embed_bottom = discord.Embed(
                description=(
                    "🔱 𝐎𝐖𝐍𝐄𝐑 𝐀𝐂𝐂𝐄𝐒𝐒  𝐒𝐞𝐚𝐫𝐜𝐡𝐢𝐧𝐠 𝐟𝐨𝐫 𝐭𝐡𝐞 𝐎𝐯𝐞𝐫𝐥𝐨𝐫𝐝... 🕷️ 𝐓𝐡𝐞 𝐭𝐡𝐫𝐨𝐧𝐞 𝐛𝐞𝐥𝐨𝐧𝐠𝐬 𝐭𝐨 𝐭𝐡𝐞 𝐜𝐫𝐞𝐚𝐭𝐨𝐫 𝐨𝐟 🩸𝐇𝐄𝐋𝐋𝐅𝐈𝐑𝐄 𝐂𝐋𝐔𝐁🩸📩 𝐍𝐞𝐞𝐝 𝐚𝐬𝐬𝐢𝐬𝐭𝐚𝐧𝐜𝐞? 𝐂𝐨𝐧𝐭𝐚𝐜𝐭 𝐭𝐡𝐞 𝐬𝐞𝐫𝐯𝐞𝐫 𝐬𝐭𝐚𝐟𝐟.\n"
                    f"𝐁𝐨𝐭 𝐎𝐰𝐧𝐞𝐫 : <@{BOT_OWNER_ID}>"
                ),
                color=discord.Color.from_rgb(180, 0, 0)
            )

            try:
                await message.channel.send(content=content_top, embeds=[embed_banner, embed_bottom])
            except Exception as e:
                print(f"[Guardian Response Error] {e}", flush=True)

            if is_dm:
                return

    # ================= AUTOMATED ANTI-SPAM & AUTOMOD SYSTEM =================
    # Members with "monarch" role, extra owners, whitelisted members, or bot admins are exempt from punishment
    if message.guild and isinstance(message.author, discord.Member):
        automod_cfg = get_automod_config(message.guild.id)
        if automod_cfg.get("enabled"):
            target_channels = automod_cfg.get("channels", [])
            if not target_channels or message.channel.id in target_channels:
                is_exempt = is_automod_exempt(message.author)
                automod_violation = None
                timeout_mins = 0

                # 1. Check Discord Invite Links (Delete invite link; 12m timeout for non-exempt)
                if re.search(r"(discord\.gg|discord\.com/invite|discordapp\.com/invite)/[a-zA-Z0-9]+", message.content):
                    try:
                        await message.delete()
                    except Exception:
                        pass
                    if is_exempt:
                        embed = discord.Embed(
                            title="🛡️ Automod Security Action",
                            description=f"⚠️ {message.author.mention}, Discord invite links are prohibited.\n*(Message removed. Timeout bypassed for Monarch / Admin / Owner)*",
                            color=discord.Color.gold()
                        )
                        embed.set_footer(text="Automod System • Server Security Defense")
                        try:
                            alert = await message.channel.send(content=message.author.mention, embed=embed)
                            await asyncio.sleep(6)
                            await alert.delete()
                        except Exception:
                            pass
                        return
                    else:
                        automod_violation = "Unauthorized Discord Invite Link"
                        timeout_mins = 12

                # For non-exempt members, check other violations
                elif not is_exempt:
                    now = time.time()
                    uid = message.author.id

                    # 2. Check Mass Mentions (4+ users or 3+ roles -> 12 mins)
                    if len(message.mentions) >= 4 or len(message.role_mentions) >= 3:
                        automod_violation = f"Mass Mentions ({len(message.mentions)} users tagged)"
                        timeout_mins = 12

                    # 3. Check Rapid Message Flooding (4+ messages in 4s -> 12 mins)
                    else:
                        user_message_times[uid] = [t for t in user_message_times[uid] if now - t < 4.0]
                        user_message_times[uid].append(now)
                        if len(user_message_times[uid]) >= 4:
                            automod_violation = "Rapid Message Flooding (4+ messages in 4s)"
                            timeout_mins = 12
                            user_message_times[uid].clear()

                    # 4. Check Excessive Emojis (10+ emojis -> 5 mins)
                    if not automod_violation and len(re.findall(r"<a?:\w+:\d+>|[\U00010000-\U0010ffff]", message.content)) >= 10:
                        automod_violation = "Excessive Emoji Spam (10+ emojis)"
                        timeout_mins = 5

                    # 5. Check Repeated Text Spam (3 identical messages in 10s -> 5 mins)
                    if not automod_violation:
                        cleaned_text = message.content.strip().lower()
                        if len(cleaned_text) > 1:
                            history = [entry for entry in user_last_messages[uid] if now - entry[1] < 10.0]
                            history.append((cleaned_text, now))
                            user_last_messages[uid] = history
                            if sum(1 for text, _ in history if text == cleaned_text) >= 3:
                                automod_violation = "Repeated Text Spam (3 identical messages in 10s)"
                                timeout_mins = 5
                                user_last_messages[uid].clear()

                if automod_violation and timeout_mins > 0:
                    try:
                        await message.delete()
                    except Exception:
                        pass
                    try:
                        await message.author.timeout(datetime.timedelta(minutes=timeout_mins), reason=f"Automod: {automod_violation}")
                    except Exception as err:
                        print(f"[Automod Timeout Error] {err}", flush=True)

                    embed = discord.Embed(
                        title="🛡️ Automod Security Action",
                        description=(
                            f"⚠️ {message.author.mention} was **timed out for {timeout_mins} minutes**.\n\n"
                            f"• **Violation**: {automod_violation}\n"
                            f"• **Channel**: {message.channel.mention}\n"
                            f"• **Duration**: `{timeout_mins} Minutes`\n"
                            f"• **Enforcement**: Immediate Removal & Timeout"
                        ),
                        color=discord.Color.red()
                    )
                    embed.set_footer(text=f"Automod System • {timeout_mins}m Defense Penalty")
                    try:
                        alert = await message.channel.send(content=message.author.mention, embed=embed)
                        await asyncio.sleep(8)
                        await alert.delete()
                    except Exception:
                        pass

                    try:
                        dm_embed = discord.Embed(
                            title=f"🛡️ Automod Timeout Notification • {message.guild.name}",
                            description=(
                                f"You have been timed out in **{message.guild.name}** for **{timeout_mins} minutes**.\n\n"
                                f"• **Violation**: {automod_violation}\n"
                                f"• **Channel**: {message.channel.name}\n"
                                f"• **Duration**: `{timeout_mins} Minutes`\n\n"
                                "Please follow server guidelines to avoid further administrative penalties."
                            ),
                            color=discord.Color.red()
                        )
                        await message.author.send(embed=dm_embed)
                    except Exception:
                        pass
                    return

        now = time.time()
        uid = message.author.id
        spam_detected = False
        spam_reason = ""

        # 1. Check Mass Mentions (Tagging 4 or more users in 1 message)
        if len(message.mentions) >= 4:
            spam_detected = True
            spam_reason = f"Mass Mentions ({len(message.mentions)} users tagged in a single message)"

        # 2. Check Rapid Message Flooding (4 or more messages within 4 seconds)
        user_message_times[uid] = [t for t in user_message_times[uid] if now - t < 4.0]
        user_message_times[uid].append(now)
        if len(user_message_times[uid]) >= 4:
            spam_detected = True
            spam_reason = "Message Flooding (4+ messages sent in under 4 seconds)"
            user_message_times[uid].clear()

        # 3. Check Repeated Text Spam (Sending identical message 3 times within 10 seconds)
        cleaned_text = message.content.strip().lower()
        if len(cleaned_text) > 1 and not spam_detected:
            history = [entry for entry in user_last_messages[uid] if now - entry[1] < 10.0]
            history.append((cleaned_text, now))
            user_last_messages[uid] = history

            if sum(1 for text, _ in history if text == cleaned_text) >= 3:
                spam_detected = True
                spam_reason = f"Repeated Message Spam (repeated '{message.content[:30]}' 3 times)"
                user_last_messages[uid].clear()

        # If spam condition met, issue automatic warning card + track warning count!
        if spam_detected:
            # Increment warning count for this user
            user_warn_counts[uid] = user_warn_counts.get(uid, 0) + 1
            current_warn = user_warn_counts[uid]
            TIMEOUT_AFTER_WARNS = 3         # Timeout after 3 warnings
            TIMEOUT_DURATION_MINS = 5       # Timeout for 5 minutes

            # Try to delete the spam message first
            try:
                await message.delete()
            except (discord.Forbidden, discord.NotFound):
                pass

            # ---- CASE: 3rd warning → Apply Timeout + Send DM ----
            if current_warn >= TIMEOUT_AFTER_WARNS:
                user_warn_counts[uid] = 0   # Reset count after timeout

                timeout_duration = discord.utils.utcnow() + __import__('datetime').timedelta(minutes=TIMEOUT_DURATION_MINS)

                # Apply timeout (requires Moderate Members permission)
                timeout_applied = False
                try:
                    await message.author.timeout(timeout_duration, reason=f"Anti-Spam: {spam_reason}")
                    timeout_applied = True
                except (discord.Forbidden, discord.HTTPException) as e:
                    print(f"Could not apply timeout: {e}", flush=True)

                # Send timeout warning embed in channel
                timeout_embed = discord.Embed(
                    title="🚫 TIMEOUT ISSUED — 3 Spam Warnings Reached",
                    description=f"{message.author.mention} has been **timed out for {TIMEOUT_DURATION_MINS} minutes** after receiving 3 spam warnings.",
                    color=discord.Color.dark_red()
                )
                timeout_embed.set_thumbnail(url=message.author.display_avatar.url)
                timeout_embed.add_field(name="Member", value=f"{message.author.name} (`{message.author.id}`)", inline=True)
                timeout_embed.add_field(name="Timeout Duration", value=f"{TIMEOUT_DURATION_MINS} minutes", inline=True)
                timeout_embed.add_field(name="Final Violation", value=spam_reason, inline=False)
                timeout_embed.add_field(name="Status", value="✅ Timeout Applied" if timeout_applied else "❌ Could not apply timeout (check bot permissions)", inline=False)
                timeout_embed.set_footer(text="Automated Anti-Spam Moderation — Warning limit reached")

                try:
                    await message.channel.send(content=message.author.mention, embed=timeout_embed)
                except discord.HTTPException as e:
                    print(f"Failed to send timeout embed: {e}", flush=True)

                # Send DM to the timed-out member
                try:
                    dm_embed = discord.Embed(
                        title="🚫 You have been Timed Out",
                        description=(
                            f"You have been **timed out for {TIMEOUT_DURATION_MINS} minutes** "
                            f"in **{message.guild.name}** for repeated spamming."
                        ),
                        color=discord.Color.dark_red()
                    )
                    dm_embed.add_field(name="Server", value=message.guild.name, inline=True)
                    dm_embed.add_field(name="Duration", value=f"{TIMEOUT_DURATION_MINS} minutes", inline=True)
                    dm_embed.add_field(name="Reason", value=f"You received 3 spam warnings.\nFinal violation: {spam_reason}", inline=False)
                    dm_embed.add_field(
                        name="What happens now?",
                        value=(
                            f"You cannot send messages or join voice channels for {TIMEOUT_DURATION_MINS} minutes.\n"
                            f"After the timeout expires, please follow the server rules to avoid permanent action."
                        ),
                        inline=False
                    )
                    dm_embed.set_footer(text="This is an automated message from the server moderation bot.")
                    await message.author.send(embed=dm_embed)
                except discord.Forbidden:
                    # User has DMs disabled — silently skip
                    pass
                except discord.HTTPException as e:
                    print(f"Failed to send timeout DM: {e}", flush=True)

            # ---- CASE: Warning 1 or 2 → Show standard warning card ----
            else:
                warns_remaining = TIMEOUT_AFTER_WARNS - current_warn
                warning_embed = discord.Embed(
                    title=f"⚠️ SPAM WARNING ({current_warn}/{TIMEOUT_AFTER_WARNS})",
                    description=f"**{message.author.mention}, you have triggered Anti-Spam protection!**\n"
                                f"Please stop spamming to maintain chat quality.\n\n"
                                f"⚠️ **{warns_remaining} more warning(s) before you are timed out for {TIMEOUT_DURATION_MINS} minutes!**",
                    color=discord.Color.orange() if current_warn == 1 else discord.Color.red()
                )
                warning_embed.set_thumbnail(url=message.author.display_avatar.url)
                warning_embed.add_field(name="Member", value=f"{message.author.name} (`{message.author.id}`)", inline=True)
                warning_embed.add_field(name="Channel", value=message.channel.mention, inline=True)
                warning_embed.add_field(name="Warnings", value=f"{current_warn}/{TIMEOUT_AFTER_WARNS}", inline=True)
                warning_embed.add_field(name="Violation Detected", value=spam_reason, inline=False)
                warning_embed.set_footer(text="Automated Anti-Spam Moderation System")

                try:
                    await message.channel.send(content=message.author.mention, embed=warning_embed)
                except discord.HTTPException as e:
                    print(f"Failed to send spam warning card: {e}", flush=True)

    # Roleplay / Action Detection in Chat (e.g. "@someone challenge @someone2", "hug @someone", etc.)
    if message.guild and message.mentions and not message.mention_everyone:
        # Ignore messages starting with "/" as they are intended as slash commands
        if not message.content.strip().startswith("/"):
            content_lower = message.content.lower()
            matched_act = None
            for word, key in ACTION_WORDS.items():
                if re.search(rf"\b{word}\b", content_lower):
                    matched_act = key
                    break

            if matched_act:
                act_user = None
                tgt_user = None
                if len(message.mentions) >= 2:
                    act_user = message.mentions[0]
                    tgt_user = message.mentions[1]
                elif len(message.mentions) == 1:
                    act_user = message.author
                    tgt_user = message.mentions[0]

                if act_user and tgt_user:
                    # Prevent double-sending
                    if not is_duplicate_action(message.channel.id, matched_act, act_user.id, tgt_user.id):
                        if matched_act == "challenge":
                            content, embed = make_challenge_payload(act_user, tgt_user, "COME LETS FIGHT")
                            view = ChallengeResponseView(act_user.id, tgt_user.id, "COME LETS FIGHT")
                            try:
                                await message.channel.send(content=content, embed=embed, view=view)
                            except Exception as e:
                                print(f"[Challenge Error] Could not send challenge message: {e}", flush=True)
                        else:
                            content, embed = make_action_payload(matched_act, act_user, tgt_user)
                            try:
                                await message.channel.send(content=content, embed=embed)
                            except Exception as e:
                                print(f"[Action Error] Could not send action message: {e}", flush=True)

    # Auto-reactions: Only react when target ID is explicitly tagged/mentioned in message content
    # Do NOT react to messages that reply to the target user
    target_ids = get_target_user_ids()
    if target_ids:
        is_reply_to_target = False
        if message.reference:
            ref = message.reference
            resolved = getattr(ref, "resolved", None)
            if isinstance(resolved, discord.Message):
                if resolved.author.id in target_ids:
                    is_reply_to_target = True
            elif getattr(ref, "cached_message", None) and ref.cached_message.author.id in target_ids:
                is_reply_to_target = True
            elif ref.message_id:
                try:
                    orig_msg = await message.channel.fetch_message(ref.message_id)
                    if orig_msg.author.id in target_ids:
                        is_reply_to_target = True
                except Exception:
                    pass

        if not is_reply_to_target:
            has_explicit_mention = False
            for tid in target_ids:
                if re.search(rf"<@!?{tid}>|\b{tid}\b", message.content):
                    has_explicit_mention = True
                    break

            if has_explicit_mention:
                for emoji in REACTION_EMOJIS:
                    try:
                        await message.add_reaction(emoji)
                    except discord.Forbidden:
                        print(f"Missing permission to add reaction in channel {message.channel.name}", flush=True)
                        break
                    except discord.HTTPException as e:
                        print(f"Failed to add reaction: {e}", flush=True)

    # Leveling System: Award cute XP to regular server members (not bot admins/owners/extraowners)
    try:
        await handle_message_leveling(message)
    except Exception as lvl_err:
        print(f"[Leveling Error] {lvl_err}", flush=True)

    await bot.process_commands(message)


# ==============================================================================
# 2. ADMIN ACCESS MANAGEMENT COMMANDS (/admin add, /admin remove, /admin list)
# ==============================================================================
# ==============================================================================
# BUMP — Bump Notification & Cooldown Reminder (/bump notify, /bump status)
# ==============================================================================
bump_group = app_commands.Group(name="bump", description="Server bump reminder and cooldown notification system")

@bump_group.command(name="notify", description="Configure bump reminder notifications and ping role")
@app_commands.describe(
    channel="The text channel where bump reminders and cooldown alerts will be sent",
    ping_role="Role to ping when it is time to bump (optional)",
    interval_minutes="Cooldown between bumps in minutes (default: 120 for Disboard)"
)
@check_bot_admin()
async def bump_notify(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
    ping_role: discord.Role = None,
    interval_minutes: int = 120
):
    guild = interaction.guild
    if not guild:
        await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)
        return

    if interval_minutes < 5:
        await interaction.response.send_message("❌ Interval must be at least 5 minutes.", ephemeral=True)
        return

    current_bump = get_bump_config(guild.id)
    if current_bump.get("enabled") and current_bump.get("channel_id") == channel.id and current_bump.get("interval_minutes") == interval_minutes and current_bump.get("ping_role_id") == (ping_role.id if ping_role else None):
        embed = discord.Embed(
            title="ℹ️ Bump Status",
            description=f"⚠️ **Bump notifications are already TURNED ON in {channel.mention}!**",
            color=discord.Color.gold()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    now = time.time()
    next_bump = now + (interval_minutes * 60)

    bump_cfg = {
        "enabled": True,
        "channel_id": channel.id,
        "interval_minutes": interval_minutes,
        "bump_text": "/bump",
        "ping_role_id": ping_role.id if ping_role else None,
        "next_bump_timestamp": next_bump
    }
    save_bump_config(guild.id, bump_cfg)

    embed = discord.Embed(
        title="🔔 Bump Notification Configured!",
        description=f"Bump reminder notifications are now active for **{guild.name}**.",
        color=discord.Color.green()
    )
    embed.add_field(name="📌 Notification Channel", value=channel.mention, inline=True)
    embed.add_field(name="⏱️ Cooldown Interval", value=f"Every **{interval_minutes}** minutes ({(interval_minutes/60):.1f} hrs)", inline=True)
    embed.add_field(name="🔔 Ping Role", value=ping_role.mention if ping_role else "*None*", inline=True)
    embed.add_field(name="⏳ Next Scheduled Alert", value=f"<t:{int(next_bump)}:R> (<t:{int(next_bump)}:t>)", inline=False)
    embed.set_footer(text="Use /bump status to inspect remaining cooldown anytime.")

    await interaction.response.send_message(embed=embed)


@bump_group.command(name="status", description="Check current bump timer, channel, and countdown")
@check_bot_admin()
async def bump_status(interaction: discord.Interaction):
    guild = interaction.guild
    if not guild:
        await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)
        return

    bump_cfg = get_bump_config(guild.id)
    if not bump_cfg or not bump_cfg.get("channel_id"):
        await interaction.response.send_message(
            f"ℹ️ Bump notification is currently **not configured** for **{guild.name}**.\n"
            f"Use `/bump notify channel: #channel` to activate it.",
            ephemeral=True
        )
        return

    channel = guild.get_channel(bump_cfg.get("channel_id"))
    interval_mins = bump_cfg.get("interval_minutes", 120)
    next_bump = bump_cfg.get("next_bump_timestamp", 0)
    ping_role = guild.get_role(bump_cfg.get("ping_role_id")) if bump_cfg.get("ping_role_id") else None
    is_active = bump_cfg.get("enabled", False)

    now = time.time()
    if now >= next_bump:
        countdown_str = "🟢 **Ready to bump right now!**"
    else:
        countdown_str = f"⏳ <t:{int(next_bump)}:R> (<t:{int(next_bump)}:t>)"

    embed = discord.Embed(
        title="📊 Server Bump Notification Status",
        description=f"Bump cooldown timer and notification settings for **{guild.name}**.",
        color=discord.Color.green() if is_active else discord.Color.greyple()
    )
    embed.add_field(name="⚙️ Status", value="🟢 **Active**" if is_active else "🔴 **Disabled**", inline=True)
    embed.add_field(name="📌 Notification Channel", value=channel.mention if channel else "*Not found*", inline=True)
    embed.add_field(name="⏱️ Interval", value=f"Every **{interval_mins}** mins ({(interval_mins/60):.1f} hrs)", inline=True)
    embed.add_field(name="🔔 Ping Role", value=ping_role.mention if ping_role else "*None*", inline=True)
    embed.add_field(name="⏰ Next Bump Alert", value=countdown_str, inline=False)
    embed.set_footer(text="Timer automatically updates when Disboard is bumped.")

    await interaction.response.send_message(embed=embed)

bot.tree.add_command(bump_group)

admin_group = app_commands.Group(name="admin", description="Manage authorized bot administrators for this server")

@admin_group.command(name="add", description="Authorize a member as a bot administrator in this server")
@app_commands.describe(member="The member to authorize as a bot administrator")
@check_bot_admin()
async def admin_add(interaction: discord.Interaction, member: discord.Member):
    guild = interaction.guild
    if not guild:
        await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)
        return

    # Only Server Owner, Extra Owners, or Supreme Bot Owner can add admins
    if not is_server_owner_or_extraowner(guild, interaction.user.id):
        await interaction.response.send_message(
            f"🔒 **Access Denied**: Only the **Server Owner**, **Extra Owners**, or the **Supreme Bot Owner** can add administrators for this server.",
            ephemeral=True
        )
        return

    if member.id == BOT_OWNER_ID:
        await interaction.response.send_message(
            f"👑 {member.mention} is the Supreme Bot Owner and already has permanent global access.",
            ephemeral=True
        )
        return

    if member.id == guild.owner_id:
        await interaction.response.send_message(
            f"👑 {member.mention} is the Server Owner and is already an administrator by default.",
            ephemeral=True
        )
        return

    server_admins = get_server_admin_ids(guild.id)
    if member.id in server_admins:
        await interaction.response.send_message(
            f"⚠️ {member.mention} is already an authorized bot administrator in **{guild.name}**.",
            ephemeral=True
        )
        return

    add_server_admin(guild.id, member.id)
    await interaction.response.send_message(
        f"✅ Successfully granted bot administrator access to {member.mention} for **{guild.name}**!"
    )


@admin_group.command(name="remove", description="Revoke a member's bot administrator access in this server")
@app_commands.describe(member="The member whose bot administrator access should be revoked")
@check_bot_admin()
async def admin_remove(interaction: discord.Interaction, member: discord.Member):
    guild = interaction.guild
    if not guild:
        await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)
        return

    # Only Server Owner, Extra Owners, or Supreme Bot Owner can remove admins
    if not is_server_owner_or_extraowner(guild, interaction.user.id):
        await interaction.response.send_message(
            f"🔒 **Access Denied**: Only the **Server Owner**, **Extra Owners**, or the **Supreme Bot Owner** can remove administrators for this server.",
            ephemeral=True
        )
        return

    if member.id == BOT_OWNER_ID:
        await interaction.response.send_message(
            "❌ Cannot revoke access from the Supreme Bot Owner (permanent global administrator).",
            ephemeral=True
        )
        return

    if member.id == guild.owner_id:
        await interaction.response.send_message(
            "❌ Cannot revoke access from the Server Owner (permanent default administrator for this server).",
            ephemeral=True
        )
        return

    if member.id in get_extraowner_ids(guild.id):
        await interaction.response.send_message(
            "❌ Cannot revoke access from an Extra Owner using `/admin remove`. Use `/extraowner remove` instead.",
            ephemeral=True
        )
        return

    if not remove_server_admin(guild.id, member.id):
        await interaction.response.send_message(
            f"⚠️ {member.mention} is not in the bot administrator list for **{guild.name}**.",
            ephemeral=True
        )
        return

    await interaction.response.send_message(
        f"✅ Successfully revoked bot administrator access from {member.mention} for **{guild.name}**."
    )


@admin_group.command(name="list", description="Show all users authorized to manage the bot in this server")
@check_bot_admin()
async def admin_list(interaction: discord.Interaction):
    guild = interaction.guild
    if not guild:
        await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)
        return

    server_admins = get_server_admin_ids(guild.id)
    server_owner_str = f"👑 <@{guild.owner_id}> (`{guild.owner_id}`) *(Server Owner - Default Admin)*"
    supreme_owner_str = f"👑 <@{BOT_OWNER_ID}> (`{BOT_OWNER_ID}`) *(Supreme Bot Owner - Global)*"

    admin_list_text = []
    for uid in sorted(server_admins):
        if uid == BOT_OWNER_ID or uid == guild.owner_id:
            continue
        admin_list_text.append(f"🛡️ <@{uid}> (`{uid}`)")

    embed = discord.Embed(
        title=f"🔐 Bot Administrators • {guild.name}",
        description="Users authorized to execute bot administration commands in this server.",
        color=discord.Color.gold()
    )
    embed.add_field(name="Server Owner (Default Admin)", value=server_owner_str, inline=False)
    embed.add_field(name="Supreme Bot Owner (Global)", value=supreme_owner_str, inline=False)

    if admin_list_text:
        embed.add_field(
            name=f"Server Admins ({len(admin_list_text)})",
            value="\n".join(admin_list_text),
            inline=False
        )
    else:
        embed.add_field(
            name="Server Admins",
            value="*No additional admins added yet. The Server Owner can use `/admin add` to authorize members.*",
            inline=False
        )
    embed.set_footer(text=f"Server: {guild.name} • Total: {len(admin_list_text) + 2} Authorized")

    await interaction.response.send_message(embed=embed, ephemeral=True)

bot.tree.add_command(admin_group)

# ==============================================================================
# 3. CHANGE NICKNAME IN THE SERVER
# ==============================================================================
@bot.tree.command(name="changenick", description="Change a member's nickname in this server")
@app_commands.describe(
    member="The member whose nickname should be changed",
    new_nickname="The new nickname to apply (leave empty to reset to original name)"
)
@check_bot_admin()
async def changenick(
    interaction: discord.Interaction,
    member: discord.Member,
    new_nickname: str = None
):
    if interaction.guild.owner_id == member.id:
        await interaction.response.send_message(
            "❌ Discord does not permit bots to change the server owner's nickname.",
            ephemeral=True
        )
        return

    bot_member = interaction.guild.get_member(bot.user.id)
    if member.top_role >= bot_member.top_role:
        await interaction.response.send_message(
            f"❌ Cannot change {member.mention}'s nickname: their highest role ({member.top_role.name}) "
            f"is higher than or equal to my bot role ({bot_member.top_role.name}).\n"
            f"💡 **Fix:** In **Server Settings -> Roles**, drag my bot role above their role.",
            ephemeral=True
        )
        return

    try:
        old_name = member.display_name
        await member.edit(nick=new_nickname)
        display_after = new_nickname if new_nickname else member.name
        await interaction.response.send_message(
            f"✅ Successfully updated {member.mention}'s nickname from `{old_name}` to **`{display_after}`**."
        )
    except discord.Forbidden:
        await interaction.response.send_message(
            "❌ Permission Denied: Ensure the bot has 'Manage Nicknames' permission and higher role hierarchy.",
            ephemeral=True
        )
    except discord.HTTPException as e:
        await interaction.response.send_message(
            f"❌ Failed to change nickname: {e}",
            ephemeral=True
        )

# ==============================================================================
# MASS NICKNAME FORMATTING & PREFIX (/formatall, /resetallnicks)
# ==============================================================================
@bot.tree.command(name="formatall", description="Set a prefix and format nicknames for all members in the server")
@app_commands.describe(
    prefix="The prefix tag to add to everyone (e.g. [TAG] or ⚡)",
    format_case="Text case formatting style for members' names",
    include_bots="Whether to also rename bots (default: False)"
)
@app_commands.choices(format_case=[
    app_commands.Choice(name="Keep Original Case", value="none"),
    app_commands.Choice(name="Title Case (e.g. John Doe)", value="title"),
    app_commands.Choice(name="UPPERCASE (e.g. JOHN DOE)", value="upper"),
    app_commands.Choice(name="lowercase (e.g. john doe)", value="lower")
])
@check_bot_admin()
async def formatall_nicks(
    interaction: discord.Interaction,
    prefix: str,
    format_case: str = "none",
    include_bots: bool = False
):
    await interaction.response.defer()

    guild = interaction.guild
    bot_member = guild.get_member(bot.user.id)
    success_count = 0
    skipped_count = 0

    prefix = prefix.strip()
    guild_id = guild.id if guild else 0
    stop_formatall_flag[guild_id] = False
    active_formatall_flag[guild_id] = True

    try:
        for member in guild.members:
            if stop_formatall_flag.get(guild_id, False):
                break

            # 1. Skip bots if not requested
            if member.bot and not include_bots:
                continue

            # 2. Skip server owner (Discord API does not allow bots to change owner's nickname)
            if guild.owner_id == member.id:
                skipped_count += 1
                continue

            # 3. Skip members with role higher than or equal to bot
            if bot_member and member.top_role >= bot_member.top_role:
                skipped_count += 1
                continue

            # Use current per-server profile name (display_name), DO NOT replace with base username!
            current_name = member.display_name

            # Avoid duplicate prefixes if already prefixed with this exact prefix
            if current_name.startswith(f"{prefix} "):
                if format_case == "none":
                    continue
                core_name = current_name[len(prefix) + 1:]
            else:
                core_name = current_name

            # Apply chosen text case formatting
            if format_case == "title":
                formatted_name = core_name.title()
            elif format_case == "upper":
                formatted_name = core_name.upper()
            elif format_case == "lower":
                formatted_name = core_name.lower()
            else:
                formatted_name = core_name

            # Construct new formatted nickname: e.g. "HFC Alex"
            new_nick = f"{prefix} {formatted_name}".strip()

            # Discord nickname limit is 32 characters
            if len(new_nick) > 32:
                new_nick = new_nick[:32]

            # Only edit if different
            if member.nick == new_nick or (member.nick is None and member.name == new_nick):
                continue

            try:
                await member.edit(nick=new_nick)
                success_count += 1
                await asyncio.sleep(0.2)  # Safe rate limit delay
            except Exception:
                skipped_count += 1
    finally:
        active_formatall_flag[guild_id] = False

    msg = f"🏷️ Successfully updated **{success_count}** member nickname(s) with prefix **`{prefix}`**!"
    if stop_formatall_flag.get(guild_id, False):
        msg += "\n*(Formatting was stopped early by `/stop`)*"
    elif skipped_count > 0:
        msg += f"\n*(Skipped {skipped_count} member(s) due to higher roles or server ownership)*"

    try:
        await interaction.followup.send(msg)
    except Exception:
        if interaction.channel:
            await interaction.channel.send(f"{interaction.user.mention} {msg}")

@bot.tree.command(name="resetallnicks", description="Reset all members' nicknames back to their default Discord usernames")
@check_bot_admin()
async def reset_all_nicks(interaction: discord.Interaction):
    await interaction.response.defer()

    guild = interaction.guild
    bot_member = guild.get_member(bot.user.id)
    success_count = 0
    skipped_count = 0

    for member in guild.members:
        # Skip if member doesn't have a custom nickname
        if member.nick is None:
            continue

        # Skip server owner and higher roles
        if guild.owner_id == member.id or (bot_member and member.top_role >= bot_member.top_role):
            skipped_count += 1
            continue

        try:
            await member.edit(nick=None)
            success_count += 1
            await asyncio.sleep(0.2)
        except Exception:
            skipped_count += 1

    msg = f"🔄 Successfully reset **{success_count}** member nickname(s) to default usernames!"
    if skipped_count > 0:
        msg += f"\n*(Skipped {skipped_count} member(s) due to higher roles or server ownership)*"

    await interaction.followup.send(msg)

# ==============================================================================
# DEFAULT PREFIX COMMANDS (/setprefix, /removeprefix, /viewprefix)
# ==============================================================================
@bot.tree.command(name="setprefix", description="Set the default prefix automatically applied to new members joining the server")
@app_commands.describe(prefix="The prefix string to prepend to new members' names (e.g. [TAG] or 🔥)")
@check_bot_admin()
async def set_prefix(interaction: discord.Interaction, prefix: str):
    clean_prefix = prefix.strip()
    if not clean_prefix:
        await interaction.response.send_message("❌ Prefix cannot be empty.", ephemeral=True)
        return
    if len(clean_prefix) > 15:
        await interaction.response.send_message("❌ Prefix must be 15 characters or fewer.", ephemeral=True)
        return

    set_default_prefix(interaction.guild.id, clean_prefix)
    await interaction.response.send_message(
        f"✅ Default prefix for new members has been set to: **`{clean_prefix}`**\n"
        f"Anyone who joins **{interaction.guild.name}** will automatically have this prefix applied (e.g. `{clean_prefix} Username`)."
    )

@bot.tree.command(name="removeprefix", description="Remove the default prefix applied to new members")
@check_bot_admin()
async def remove_prefix(interaction: discord.Interaction):
    if remove_default_prefix(interaction.guild.id):
        await interaction.response.send_message(
            f"✅ Default prefix removed for **{interaction.guild.name}**.\nNew members will now join with their original username."
        )
    else:
        await interaction.response.send_message(
            f"⚠️ No default prefix was set for **{interaction.guild.name}**.",
            ephemeral=True
        )

@bot.tree.command(name="viewprefix", description="View the current default prefix configured for new members")
@check_bot_admin()
async def view_prefix(interaction: discord.Interaction):
    prefix = get_default_prefix(interaction.guild.id)
    if prefix:
        await interaction.response.send_message(
            f"🏷️ Current default prefix for **{interaction.guild.name}** is: **`{prefix}`**\n"
            f"Example for a new member: `{prefix} Username`"
        )
    else:
        await interaction.response.send_message(
            f"ℹ️ No default prefix is currently set for **{interaction.guild.name}**.\n"
            f"Use `/setprefix <prefix>` to configure one."
        )

# ==============================================================================
# 4. DISPLAY MEMBER INFO, ROLES & DATES (/info)
# ==============================================================================
@bot.tree.command(name="info", description="Show detailed member info, join dates, and roles")
@app_commands.describe(member="The member whose info you want to view (defaults to yourself)")
@check_bot_admin()
async def show_info(interaction: discord.Interaction, member: discord.Member = None):
    target = member or interaction.user
    member_roles = [role for role in target.roles if role.name != "@everyone"]
    member_roles.reverse()

    role_mentions = [role.mention for role in member_roles]
    role_count = len(member_roles)

    created_ts = int(target.created_at.timestamp())
    joined_ts = int(target.joined_at.timestamp()) if target.joined_at else None

    embed_color = target.color if target.color.value != 0 else discord.Color.blue()
    embed = discord.Embed(
        title=f"👤 Member Information: {target.display_name}",
        color=embed_color
    )
    embed.set_thumbnail(url=target.display_avatar.url)
    embed.add_field(name="Username", value=f"{target.name} (`{target.id}`)", inline=True)
    embed.add_field(name="Highest Role", value=target.top_role.mention, inline=True)
    embed.add_field(name="Total Roles", value=str(role_count), inline=True)
    embed.add_field(name="Joined Discord", value=f"<t:{created_ts}:D> (<t:{created_ts}:R>)", inline=True)

    if joined_ts:
        embed.add_field(name="Joined Server", value=f"<t:{joined_ts}:D> (<t:{joined_ts}:R>)", inline=True)
    else:
        embed.add_field(name="Joined Server", value="*Unknown*", inline=True)

    if role_mentions:
        roles_text = " ".join(role_mentions)
        if len(roles_text) > 1024:
            roles_text = f"{roles_text[:1000]}... *(and more)*"
        embed.add_field(name=f"Assigned Roles ({role_count})", value=roles_text, inline=False)
    else:
        embed.add_field(name="Assigned Roles", value="*No roles assigned*", inline=False)

    embed.set_footer(text=f"Requested by {interaction.user.display_name}")
    await interaction.response.send_message(embed=embed)

# ==============================================================================
# 5. AVATAR & BANNER COMMANDS (User & Server)
# ==============================================================================
@bot.tree.command(name="avatar", description="Display a member's avatar or the server's icon in full resolution")
@app_commands.describe(
    member="The member whose avatar you want to view (defaults to yourself)",
    server_icon="Set to True to view this server's icon instead of a user avatar"
)
@check_bot_admin()
async def avatar(interaction: discord.Interaction, member: discord.Member = None, server_icon: bool = False):
    if server_icon:
        guild = interaction.guild
        if not guild or not guild.icon:
            await interaction.response.send_message("❌ This server does not have an icon set.", ephemeral=True)
            return
        icon_url = guild.icon.url
        embed = discord.Embed(
            title=f"🏛️ Server Icon • {guild.name}",
            color=discord.Color.blurple()
        )
        embed.set_image(url=icon_url)
        embed.add_field(name="Direct Link", value=f"[Open in Browser]({icon_url})", inline=False)
        embed.set_footer(text=f"Requested by {interaction.user.display_name}")
        await interaction.response.send_message(embed=embed)
        return

    target = member or interaction.user
    avatar_url = target.display_avatar.url
    embed = discord.Embed(
        title=f"🖼️ Avatar of {target.display_name}",
        color=target.color if target.color.value != 0 else discord.Color.blue()
    )
    embed.set_image(url=avatar_url)

    links = [f"[Display Avatar]({avatar_url})"]
    if getattr(target, "guild_avatar", None):
        links.append(f"[Server Avatar]({target.guild_avatar.url})")
    if getattr(target, "avatar", None):
        links.append(f"[Global Avatar]({target.avatar.url})")

    embed.add_field(name="Direct Links", value=" • ".join(links), inline=False)
    embed.set_footer(text=f"Requested by {interaction.user.display_name}")
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="banner", description="Display a user's banner or the server's banner in full resolution")
@app_commands.describe(
    member="The member whose banner you want to view (defaults to yourself)",
    server_banner="Set to True to view this server's banner instead of a user banner"
)
@check_bot_admin()
async def banner(interaction: discord.Interaction, member: discord.Member = None, server_banner: bool = False):
    if server_banner:
        guild = interaction.guild
        if not guild or not guild.banner:
            await interaction.response.send_message("❌ This server does not have a banner set.", ephemeral=True)
            return
        banner_url = guild.banner.url
        embed = discord.Embed(
            title=f"🚩 Server Banner • {guild.name}",
            color=discord.Color.blurple()
        )
        embed.set_image(url=banner_url)
        embed.add_field(name="Direct Link", value=f"[Open in Browser]({banner_url})", inline=False)
        embed.set_footer(text=f"Requested by {interaction.user.display_name}")
        await interaction.response.send_message(embed=embed)
        return

    target = member or interaction.user
    await interaction.response.defer()
    try:
        user = await bot.fetch_user(target.id)
    except Exception as e:
        await interaction.followup.send(f"❌ Failed to fetch user profile: {e}", ephemeral=True)
        return

    if not user.banner:
        color_info = f"Hex: `#{user.accent_color.value:06x}`" if user.accent_color else "No custom banner or accent color."
        embed = discord.Embed(
            title=f"🎨 Profile Banner • {target.display_name}",
            description=f"**{target.display_name}** does not have an image banner set.\n• **Accent Color**: {color_info}",
            color=user.accent_color or discord.Color.blue()
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        await interaction.followup.send(embed=embed)
        return

    banner_url = user.banner.url
    embed = discord.Embed(
        title=f"🚩 Banner of {target.display_name}",
        color=user.accent_color or discord.Color.blue()
    )
    embed.set_image(url=banner_url)
    embed.add_field(name="Direct Link", value=f"[Open in Browser]({banner_url})", inline=False)
    embed.set_footer(text=f"Requested by {interaction.user.display_name}")
    await interaction.followup.send(embed=embed)

# ==============================================================================
# 6. SPAM MESSAGES COMMAND (/spam)
# ==============================================================================
@bot.tree.command(name="spam", description="Send a message multiple times")
@app_commands.describe(
    amount="How many times to send the message (1 to 25)",
    message="The text message to spam"
)
async def spam_messages(interaction: discord.Interaction, amount: int, message: str):
    member = interaction.user if isinstance(interaction.user, discord.Member) else (interaction.guild.get_member(interaction.user.id) if interaction.guild else None)
    if not (is_authorized_admin(interaction) or (member and is_spam_exempt(member))):
        await interaction.response.send_message(
            "🔒 **Access Denied**: You must be a bot administrator, have the **Monarch** role, or carry the server tag to use `/spam`.",
            ephemeral=True
        )
        return

    if amount < 1:
        await interaction.response.send_message("❌ Amount must be at least 1.", ephemeral=True)
        return
    if amount > 25:
        await interaction.response.send_message("❌ Amount cannot exceed 25 to protect the bot from Discord rate limits.", ephemeral=True)
        return

    guild_id = interaction.guild_id if interaction.guild else 0
    stop_spam_flag[guild_id] = False
    active_spam_count[guild_id] += 1

    await interaction.response.send_message(f"🚀 Spamming {amount} message(s)...", ephemeral=True)
    try:
        for _ in range(amount):
            if stop_spam_flag.get(guild_id, False):
                break
            try:
                await interaction.channel.send(message)
                await asyncio.sleep(0.5)  # Safe delay to prevent Discord API 429 rate limit
            except Exception as e:
                print(f"Error during spam command: {e}", flush=True)
                break
    finally:
        active_spam_count[guild_id] = max(0, active_spam_count[guild_id] - 1)

# ==============================================================================
# 7. MANUAL WARNING CARD COMMAND (/warn)
# ==============================================================================
@bot.tree.command(name="warn", description="Issue a formal warning card to a member")
@app_commands.describe(
    member="The member to issue a warning card to",
    reason="Reason for the warning (e.g. Spamming)"
)
@check_bot_admin()
async def warn_member(interaction: discord.Interaction, member: discord.Member, reason: str = "Spamming in chat"):
    embed = discord.Embed(
        title="⚠️ OFFICIAL WARNING CARD",
        description=f"**{member.mention} has received a formal warning!**",
        color=discord.Color.orange()
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="Member", value=f"{member.name} (`{member.id}`)", inline=True)
    embed.add_field(name="Warned By", value=interaction.user.mention, inline=True)
    embed.add_field(name="Reason", value=reason, inline=False)
    embed.set_footer(text="Please adhere to server guidelines to avoid further moderation.")

    await interaction.response.send_message(content=member.mention, embed=embed)

# ==============================================================================
# CLEAR — Delete 1 to 100 recent messages from a channel
# ==============================================================================
@bot.tree.command(name="clear", description="Delete recent messages from this channel (1–100 messages)")
@app_commands.describe(
    amount="Number of messages to delete (1 to 100, default: 10)",
    target="Optional: only delete messages from this specific member"
)
@check_bot_admin()
async def clear_messages(
    interaction: discord.Interaction,
    amount: int = 10,
    target: discord.Member = None
):
    if not interaction.guild or not interaction.channel:
        await interaction.response.send_message("❌ This command can only be used in a server channel.", ephemeral=True)
        return

    if amount < 1:
        await interaction.response.send_message("❌ Minimum amount is **1** message.", ephemeral=True)
        return
    if amount > 100:
        await interaction.response.send_message("❌ Maximum amount is **100** messages (Discord bulk delete limit).", ephemeral=True)
        return

    channel = interaction.channel
    if not hasattr(channel, "purge"):
        await interaction.response.send_message("❌ Messages cannot be cleared in this channel type.", ephemeral=True)
        return

    # Check bot permissions in channel
    bot_member = interaction.guild.me or interaction.guild.get_member(bot.user.id)
    if bot_member and hasattr(channel, "permissions_for"):
        perms = channel.permissions_for(bot_member)
        if not perms.manage_messages:
            await interaction.response.send_message(
                "❌ **Missing Permission**: I need the **Manage Messages** permission in this channel to delete messages.\n"
                "💡 *Fix:* Go to **Channel Settings -> Permissions** or **Server Settings -> Roles** and grant me **Manage Messages**.",
                ephemeral=True
            )
            return

    try:
        await interaction.response.defer(ephemeral=True)
    except Exception:
        pass

    def check_target(msg: discord.Message):
        if target:
            return msg.author.id == target.id
        return True

    async def send_clear_reply(msg_text):
        try:
            if interaction.response.is_done():
                await interaction.followup.send(msg_text, ephemeral=True)
            else:
                await interaction.response.send_message(msg_text, ephemeral=True)
        except Exception:
            pass

    try:
        deleted = await channel.purge(limit=amount, check=check_target, bulk=True)
        actual = len(deleted)

        if actual == 0:
            await send_clear_reply(f"⚠️ No messages found{f' from {target.mention}' if target else ''} to delete.")
            return

        await send_clear_reply(
            f"🧹 Successfully deleted **{actual}** message(s)"
            f"{f' from {target.mention}' if target else ''} in {channel.mention}."
        )

    except discord.Forbidden:
        await send_clear_reply("❌ Missing Permissions: Ensure the bot has **Manage Messages** permission in this channel.")
    except discord.HTTPException as e:
        await send_clear_reply(f"❌ Failed to delete messages: {e}")
    except Exception as e:
        await send_clear_reply(f"❌ Unexpected error while deleting messages: {e}")


# ==============================================================================
# UNBAN & BANLIST COMMANDS (/unban, /banlist)
# ==============================================================================
async def unban_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    if not interaction.guild:
        return []
    choices = []
    try:
        async for ban_entry in interaction.guild.bans(limit=50):
            u = ban_entry.user
            label = f"{u.name} (ID: {u.id})"
            if current.lower() in label.lower() or current in str(u.id):
                choices.append(app_commands.Choice(name=label[:100], value=str(u.id)))
    except Exception:
        pass
    return choices[:25]

class UnbanSelect(discord.ui.Select):
    def __init__(self, banned_entries, reason: str, moderator: discord.Member):
        options = []
        for entry in banned_entries[:25]:
            u = entry.user
            b_reason = entry.reason or "No ban reason recorded"
            if len(b_reason) > 60:
                b_reason = b_reason[:57] + "..."
            options.append(discord.SelectOption(
                label=f"{u.name}"[:100],
                description=f"ID: {u.id} • {b_reason}"[:100],
                value=str(u.id),
                emoji="🔓"
            ))
        super().__init__(placeholder="Select a banned member to unban...", min_values=1, max_values=1, options=options)
        self.reason = reason
        self.moderator = moderator

    async def callback(self, interaction: discord.Interaction):
        target_id = int(self.values[0])
        guild = interaction.guild
        try:
            target_user = await bot.fetch_user(target_id)
        except Exception:
            target_user = discord.Object(id=target_id)

        try:
            await guild.unban(target_user, reason=f"{self.reason} (Moderator: {self.moderator.display_name})")
            user_name = getattr(target_user, "name", f"User `{target_id}`")
            embed = discord.Embed(
                title="🔓 User Successfully Unbanned",
                description=f"**{user_name}** (`{target_id}`) has been unbanned from **{guild.name}**.",
                color=discord.Color.green()
            )
            embed.add_field(name="Responsible Moderator", value=self.moderator.mention, inline=True)
            embed.add_field(name="Reason", value=self.reason, inline=False)
            await interaction.response.edit_message(content=None, embed=embed, view=None)
        except discord.Forbidden:
            await interaction.response.send_message("❌ Missing Permissions: The bot requires the **Ban Members** permission to unban users.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Failed to unban user: {e}", ephemeral=True)

class UnbanView(discord.ui.View):
    def __init__(self, banned_entries, reason: str, moderator: discord.Member):
        super().__init__(timeout=120.0)
        self.add_item(UnbanSelect(banned_entries, reason, moderator))

@bot.tree.command(name="unban", description="Unban a user by selecting from banned members or entering their ID")
@app_commands.describe(
    user="Select a banned member from the autocomplete list or enter their user ID (leave empty to pick from menu)",
    reason="Optional reason for unbanning"
)
@app_commands.autocomplete(user=unban_autocomplete)
@check_bot_admin()
async def unban_user(interaction: discord.Interaction, user: str = None, reason: str = "Unbanned by moderator"):
    guild = interaction.guild
    if not guild:
        await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)
        return

    # If user argument provided directly (via autocomplete or typed ID)
    if user and user.strip():
        clean_id = re.sub(r"\D", "", user.strip())
        if not clean_id:
            await interaction.response.send_message("❌ Please provide a valid numeric user ID or select from the list.", ephemeral=True)
            return

        target_id = int(clean_id)
        await interaction.response.defer()

        try:
            target_user = await bot.fetch_user(target_id)
        except Exception:
            target_user = discord.Object(id=target_id)

        try:
            await guild.unban(target_user, reason=f"{reason} (Moderator: {interaction.user.display_name})")
            user_name = getattr(target_user, "name", f"User ID `{target_id}`")
            embed = discord.Embed(
                title="🔓 User Successfully Unbanned",
                description=f"**{user_name}** (`{target_id}`) has been unbanned from **{guild.name}**.",
                color=discord.Color.green()
            )
            embed.add_field(name="Responsible Moderator", value=interaction.user.mention, inline=True)
            embed.add_field(name="Reason", value=reason, inline=False)
            await interaction.followup.send(embed=embed)
            return
        except discord.NotFound:
            await interaction.followup.send(f"❌ User with ID `{target_id}` is not banned in this server.", ephemeral=True)
            return
        except discord.Forbidden:
            await interaction.followup.send("❌ Missing Permissions: The bot requires the **Ban Members** permission to unban users.", ephemeral=True)
            return
        except Exception as e:
            await interaction.followup.send(f"❌ Failed to unban user: {e}", ephemeral=True)
            return

    # If no user specified, fetch banned members and display interactive dropdown menu
    await interaction.response.defer()
    banned_entries = []
    try:
        async for ban_entry in guild.bans(limit=50):
            banned_entries.append(ban_entry)
    except discord.Forbidden:
        await interaction.followup.send("❌ Missing Permissions: The bot requires the **Ban Members** permission to view the ban list.", ephemeral=True)
        return
    except Exception as e:
        await interaction.followup.send(f"❌ Failed to fetch ban list: {e}", ephemeral=True)
        return

    if not banned_entries:
        embed = discord.Embed(
            title="🔓 Ban List Empty",
            description="There are currently **no banned users** in this server.",
            color=discord.Color.blue()
        )
        await interaction.followup.send(embed=embed)
        return

    embed = discord.Embed(
        title="🔓 Select a Member to Unban",
        description=f"Found **{len(banned_entries)}** banned user(s). Select a member from the dropdown menu below to unban them from **{guild.name}**:",
        color=discord.Color.gold()
    )
    view = UnbanView(banned_entries, reason, interaction.user)
    await interaction.followup.send(embed=embed, view=view)


@bot.tree.command(name="banlist", description="List all users currently banned from this server")
@app_commands.describe(limit="Maximum number of bans to display (1 to 100, default: 25)")
@check_bot_admin()
async def list_bans(interaction: discord.Interaction, limit: int = 25):
    guild = interaction.guild
    if not guild:
        await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)
        return

    await interaction.response.defer()
    try:
        bans = [entry async for entry in guild.bans(limit=limit)]
    except discord.Forbidden:
        await interaction.followup.send("❌ Missing Permissions: The bot requires the **Ban Members** permission to view the audit ban list.", ephemeral=True)
        return
    except Exception as e:
        await interaction.followup.send(f"❌ Failed to fetch ban list: {e}", ephemeral=True)
        return

    if not bans:
        await interaction.followup.send("🛡️ There are currently **no banned users** in this server.")
        return

    lines = []
    for i, ban in enumerate(bans, 1):
        reason = ban.reason or "*No reason provided*"
        if len(reason) > 40:
            reason = reason[:37] + "..."
        lines.append(f"`{i}.` **{ban.user.name}** (`{ban.user.id}`) — *{reason}*")

    embed = discord.Embed(
        title=f"🔨 Banned Users in {guild.name}",
        description=f"Showing **{len(bans)}** banned user(s):\n\n" + "\n".join(lines[:25]),
        color=discord.Color.red()
    )
    if len(bans) > 25:
        embed.set_footer(text=f"Truncated. {len(bans) - 25} more bans not shown.")
    else:
        embed.set_footer(text=f"Total listed: {len(bans)}")

    await interaction.followup.send(embed=embed)


# ==============================================================================
# UNTIMEOUT COMMAND (/untimeout)
# ==============================================================================
@bot.tree.command(name="untimeout", description="Remove timeout from a timed-out member")
@app_commands.describe(
    member="The member whose timeout you want to remove",
    reason="Optional reason for removing the timeout"
)
@check_bot_admin()
async def untimeout_member(interaction: discord.Interaction, member: discord.Member, reason: str = "Timeout removed by moderator"):
    guild = interaction.guild
    if not guild:
        await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)
        return

    if not member.is_timed_out():
        await interaction.response.send_message(f"ℹ️ {member.mention} is not currently timed out.", ephemeral=True)
        return

    try:
        await member.timeout(None, reason=f"{reason} (Moderator: {interaction.user.display_name})")
        embed = discord.Embed(
            title="🔊 Timeout Removed",
            description=f"Timeout has been removed for **{member.display_name}** ({member.mention})!",
            color=discord.Color.green()
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
        embed.add_field(name="Reason", value=reason, inline=False)
        await interaction.response.send_message(embed=embed)
    except discord.Forbidden:
        await interaction.response.send_message("❌ Missing Permissions: The bot needs the **Moderate Members** permission and a role higher than the target.", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"❌ Failed to remove timeout: {e}", ephemeral=True)


# ==============================================================================
# ROLE MEMBERS COMMAND (/rolemembers)
# ==============================================================================
@bot.tree.command(name="rolemembers", description="List all server members who have a specific role")
@app_commands.describe(role="The role whose members you want to list")
@check_bot_admin()
async def role_members(interaction: discord.Interaction, role: discord.Role):
    guild = interaction.guild
    if not guild:
        await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)
        return

    await interaction.response.defer()

    members = role.members
    total = len(members)

    if total == 0:
        await interaction.followup.send(f"ℹ️ No members currently have the **{role.name}** role.", ephemeral=True)
        return

    pct = (total / max(1, guild.member_count)) * 100
    embed = discord.Embed(
        title=f"👥 Members with {role.name}",
        description=f"Total: **{total}** member(s) ({pct:.1f}% of server)\nRole Color: `#{role.color.value:06x}`\n\n",
        color=role.color if role.color.value != 0 else discord.Color.blue()
    )

    lines = []
    for i, m in enumerate(members[:60], 1):
        nick_str = f" (**{m.nick}**)" if m.nick else ""
        lines.append(f"`{i}.` {m.mention}{nick_str} • `{m.name}`")

    content_str = "\n".join(lines)
    if len(content_str) > 3800:
        content_str = content_str[:3750] + "\n*... [List truncated due to size]*"

    embed.description += content_str
    if total > 60:
        embed.set_footer(text=f"Showing first 60 of {total} members.")
    else:
        embed.set_footer(text=f"Total members: {total}")

    await interaction.followup.send(embed=embed)


# ==============================================================================
# SNIPE COMMAND — Show recently deleted messages (/snipe)
# ==============================================================================
@bot.tree.command(name="snipe", description="Display recently deleted messages in this or a targeted channel")
@app_commands.describe(
    channel="The channel to inspect (defaults to current channel)",
    index="Which deleted message to view (1 = most recent, 2 = 2nd most recent, up to 10)"
)
@check_bot_admin()
async def snipe_message(interaction: discord.Interaction, channel: discord.TextChannel = None, index: int = 1):
    target_channel = channel or interaction.channel
    if not isinstance(target_channel, discord.TextChannel):
        await interaction.response.send_message("❌ Please select a valid text channel.", ephemeral=True)
        return

    channel_msgs = deleted_messages_cache.get(target_channel.id)
    if not channel_msgs or len(channel_msgs) == 0:
        await interaction.response.send_message(f"🔍 No recently deleted messages found in {target_channel.mention}.", ephemeral=True)
        return

    if index < 1 or index > len(channel_msgs):
        await interaction.response.send_message(f"❌ Index must be between 1 and {len(channel_msgs)} (available deleted messages in this channel).", ephemeral=True)
        return

    entry = channel_msgs[index - 1]

    embed = discord.Embed(
        title="🗑️ Recently Deleted Message",
        description=f"> {entry['content']}",
        color=discord.Color.dark_red()
    )
    embed.set_author(name=entry["author_name"], icon_url=entry["author_avatar"])
    embed.add_field(name="👤 Author", value=f"{entry['author_mention']} (`{entry['author_id']}`)", inline=True)
    embed.add_field(name="📍 Channel", value=target_channel.mention, inline=True)

    time_str = f"<t:{entry['deleted_at']}:R>"
    if entry.get("created_at"):
        time_str += f"\n*Sent: <t:{entry['created_at']}:R>*"
    embed.add_field(name="🕒 Deleted At", value=time_str, inline=False)

    if entry.get("attachments"):
        attach_links = [f"[Attachment {i+1}]({u})" for i, u in enumerate(entry["attachments"])]
        embed.add_field(name="📎 Attachments", value=" • ".join(attach_links), inline=False)
        for u in entry["attachments"]:
            if any(u.lower().endswith(ext) for ext in [".png", ".jpg", ".jpeg", ".gif", ".webp"]):
                embed.set_image(url=u)
                break

    embed.set_footer(text=f"Deleted Message {index}/{len(channel_msgs)} • Snipe System")
    await interaction.response.send_message(embed=embed)

# ==============================================================================
# BYE — Automatic Farewell Card System (/bye, /byestatus, /byedisable)
# ==============================================================================
@bot.tree.command(name="bye", description="Set target channel for automatic farewell cards when members leave, or send preview")
@app_commands.describe(
    target_channel="The text channel where automatic farewell cards will be sent whenever a member leaves",
    preview_member="Optional: Immediately post a farewell card for this member as a preview",
    custom_message="Optional note to include on the preview card"
)
@check_bot_admin()
async def bye(
    interaction: discord.Interaction,
    target_channel: discord.TextChannel,
    preview_member: discord.Member = None,
    custom_message: str = None
):
    guild = interaction.guild
    if not guild:
        await interaction.response.send_message("❌ This command can only be used inside a server.", ephemeral=True)
        return

    perms = target_channel.permissions_for(guild.me)
    if not perms.send_messages or not perms.embed_links:
        await interaction.response.send_message(
            f"❌ The bot lacks `Send Messages` and `Embed Links` permissions in {target_channel.mention}.",
            ephemeral=True
        )
        return

    # Set as the automatic bye channel for this guild
    set_bye_channel(guild.id, target_channel.id)

    reply_msg = (
        f"✅ **Automatic Farewell Channel Configured!**\n\n"
        f"🎯 **Target Channel**: {target_channel.mention}\n"
        f"Whenever any member leaves **{guild.name}**, the bot will **automatically** post their farewell card with:\n"
        f"• 🏷️ Server Nickname\n"
        f"• 👤 Username\n"
        f"• 🖼️ Profile picture on the top-right corner\n"
        f"• 📅 Date of joined\n"
        f"• 👑 Founder ping\n\n"
        f"*Use `/byestatus` to check status or `/byedisable` to turn it off.*"
    )

    if preview_member:
        try:
            content, embed = build_farewell_card(preview_member, guild, custom_message=custom_message)
            await target_channel.send(content=content, embed=embed)
            reply_msg += f"\n\n🚀 **Preview farewell card sent** to {target_channel.mention} for {preview_member.mention}!"
        except Exception as e:
            reply_msg += f"\n\n⚠️ Failed to send preview: {e}"

    await interaction.response.send_message(reply_msg)

@bot.tree.command(name="byestatus", description="Check the current automatic farewell channel configuration")
@check_bot_admin()
async def bye_status(interaction: discord.Interaction):
    guild = interaction.guild
    if not guild:
        await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)
        return

    channel_id = get_bye_channel(guild.id)
    if channel_id:
        channel = guild.get_channel(channel_id)
        mention = channel.mention if channel else f"<#{channel_id}>"
        await interaction.response.send_message(
            f"👋 **Automatic Farewell Status for {guild.name}**\n"
            f"• **Status**: Active ✅\n"
            f"• **Target Channel**: {mention}\n"
            f"Whenever a member leaves, their farewell card will be posted automatically in {mention}."
        )
    else:
        await interaction.response.send_message(
            f"ℹ️ Automatic farewell cards are currently **not configured** for **{guild.name}**.\n"
            f"Use `/bye target_channel: #channel` to configure one."
        )

@bot.tree.command(name="byedisable", description="Disable automatic farewell cards when members leave")
@check_bot_admin()
async def bye_disable(interaction: discord.Interaction):
    guild = interaction.guild
    if not guild:
        await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)
        return

    if remove_bye_channel(guild.id):
        await interaction.response.send_message(
            f"✅ Automatic farewell cards have been **disabled** for **{guild.name}**."
        )
    else:
        await interaction.response.send_message(
            f"⚠️ Automatic farewell cards were not active in **{guild.name}**.",
            ephemeral=True
        )


# ==============================================================================
# SERVER INFO — Full server dashboard with all statistics
# ==============================================================================
@bot.tree.command(name="serverinfo", description="Display comprehensive server statistics and information")
@check_bot_admin()
async def serverinfo(interaction: discord.Interaction):
    guild = interaction.guild
    if not guild:
        await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)
        return

    await interaction.response.defer()

    # ---- Member Counts ----
    total_members = guild.member_count
    all_members = guild.members  # Requires members intent

    online_members = sum(1 for m in all_members if not m.bot and m.status != discord.Status.offline)
    total_bots = sum(1 for m in all_members if m.bot)
    online_bots = sum(1 for m in all_members if m.bot and m.status != discord.Status.offline)
    total_humans = total_members - total_bots

    # ---- Voice Channel Users ----
    vc_users = 0
    for vc in guild.voice_channels:
        vc_users += len(vc.members)
    for sc in guild.stage_channels:
        vc_users += len(sc.members)

    # ---- Channel Counts ----
    text_channels = len(guild.text_channels)
    voice_channels = len(guild.voice_channels)
    categories = len(guild.categories)
    stage_channels = len(guild.stage_channels)
    forum_channels = len(guild.forum_channels) if hasattr(guild, 'forum_channels') else 0

    # ---- Roles, Emojis, Stickers ----
    total_roles = len(guild.roles) - 1  # Exclude @everyone
    custom_emojis = len(guild.emojis)
    custom_stickers = len(guild.stickers)

    # ---- Soundboards ----
    try:
        soundboards = len(guild.soundboard_sounds) if hasattr(guild, 'soundboard_sounds') else 0
    except Exception:
        soundboards = 0

    # ---- Bot Commands Count ----
    total_commands = len(bot.tree.get_commands())

    # ---- Server Info ----
    created_ts = int(guild.created_at.timestamp())
    boost_level = guild.premium_tier
    boost_count = guild.premium_subscription_count or 0
    verification = str(guild.verification_level).replace("_", " ").title()

    # ---- Build Embed ----
    embed = discord.Embed(
        title=f"📊 Server Dashboard — {guild.name}",
        description=f"Created <t:{created_ts}:D> (<t:{created_ts}:R>)",
        color=discord.Color.blurple()
    )

    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)

    # Members Section
    embed.add_field(
        name="👥 Members",
        value=(
            f"**Total:** {total_members}\n"
            f"**Humans:** {total_humans}\n"
            f"**Online Members:** {online_members}"
        ),
        inline=True
    )

    # Bots Section
    embed.add_field(
        name="🤖 Bots",
        value=(
            f"**Total Bots:** {total_bots}\n"
            f"**Active Bots:** {online_bots}"
        ),
        inline=True
    )

    # Voice Section
    embed.add_field(
        name="🎙️ Voice Activity",
        value=(
            f"**Users in VC:** {vc_users}"
        ),
        inline=True
    )

    # Channels Section
    channel_text = (
        f"💬 Text: **{text_channels}**\n"
        f"🔊 Voice: **{voice_channels}**\n"
        f"📁 Categories: **{categories}**"
    )
    if stage_channels > 0:
        channel_text += f"\n🎤 Stage: **{stage_channels}**"
    if forum_channels > 0:
        channel_text += f"\n📋 Forums: **{forum_channels}**"
    embed.add_field(
        name=f"📌 Channels ({text_channels + voice_channels + stage_channels + forum_channels})",
        value=channel_text,
        inline=True
    )

    # Roles
    embed.add_field(
        name="🏷️ Roles",
        value=f"**Total:** {total_roles}",
        inline=True
    )

    # Customization Section
    embed.add_field(
        name="🎨 Customization",
        value=(
            f"😀 Emojis: **{custom_emojis}**\n"
            f"🎭 Stickers: **{custom_stickers}**\n"
            f"🔊 Soundboards: **{soundboards}**"
        ),
        inline=True
    )

    # Server Features
    embed.add_field(
        name="⚙️ Server Info",
        value=(
            f"**Boost Level:** {'⭐' * boost_level if boost_level > 0 else 'None'} (Tier {boost_level})\n"
            f"**Boosts:** {boost_count}\n"
            f"**Verification:** {verification}"
        ),
        inline=True
    )

    # Bot Commands
    embed.add_field(
        name="🤖 Bot Commands",
        value=f"**Total Slash Commands:** {total_commands}",
        inline=True
    )

    # Owner
    owner = guild.owner
    embed.add_field(
        name="👑 Server Owner",
        value=f"{owner.mention}" if owner else "Unknown",
        inline=True
    )

    embed.set_footer(text=f"Server ID: {guild.id} • Requested by {interaction.user.display_name}")

    await interaction.followup.send(embed=embed)


# ==============================================================================
# AUDIT — View recent server audit log entries
# ==============================================================================
@bot.tree.command(name="audit", description="Fetch and display the most recent server audit log messages")
@app_commands.describe(
    count="Number of audit log entries to retrieve (1 to 50, default: 10)"
)
@check_bot_admin()
async def audit(interaction: discord.Interaction, count: int = 10):
    await interaction.response.defer()
    guild = interaction.guild
    if not guild:
        await interaction.followup.send("❌ This command can only be used inside a server.")
        return

    bot_member = guild.me or guild.get_member(bot.user.id)
    if bot_member and not bot_member.guild_permissions.view_audit_log:
        await interaction.followup.send("❌ **Missing Permissions**: The bot needs the **View Audit Log** permission to read audit entries.")
        return

    count = max(1, min(count, 50))

    ACTION_ICONS = {
        "kick": "👢",
        "ban": "🔨",
        "unban": "🔓",
        "member_update": "👤",
        "member_role_update": "🏷️",
        "member_move": "🚚",
        "member_disconnect": "🔌",
        "bot_add": "🤖",
        "role_create": "➕",
        "role_delete": "➖",
        "role_update": "✏️",
        "channel_create": "📁",
        "channel_delete": "🗑️",
        "channel_update": "⚙️",
        "message_delete": "🗑️",
        "message_bulk_delete": "🧹",
        "message_pin": "📌",
        "message_unpin": "📍",
        "invite_create": "🔗",
        "invite_delete": "❌",
        "webhook_create": "🪝",
        "webhook_delete": "🪝",
    }

    entries = []
    try:
        async for entry in guild.audit_logs(limit=count):
            entries.append(entry)
    except Exception as e:
        await interaction.followup.send(f"❌ Failed to fetch audit logs: {e}")
        return

    if not entries:
        await interaction.followup.send("📋 No audit log entries found.")
        return

    embed = discord.Embed(
        title="🛡️ Server Audit Logs",
        description=f"Showing the last **{len(entries)}** action(s) recorded in **{guild.name}**:",
        color=discord.Color.blue()
    )
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)

    for i, entry in enumerate(entries, 1):
        try:
            act_key = entry.action.name.lower()
            icon = ACTION_ICONS.get(act_key, "📝")
            act_name = str(entry.action.name).replace("_", " ").title()
            
            user_str = "Unknown Moderator"
            try:
                if entry.user:
                    user_str = entry.user.mention
            except Exception:
                user_str = "Unknown Moderator"

            target_str = ""
            try:
                target = entry.target
                if target:
                    if hasattr(target, "mention"):
                        target_str = f" ➔ {target.mention}"
                    elif hasattr(target, "name"):
                        target_str = f" ➔ **{target.name}**"
                    else:
                        target_str = f" ➔ `{target}`"
            except Exception:
                raw_target_id = getattr(entry, "_target_id", None)
                if raw_target_id:
                    target_str = f" ➔ ID `{raw_target_id}`"
                else:
                    target_str = ""

            try:
                time_str = f"<t:{int(entry.created_at.timestamp())}:R>"
            except Exception:
                time_str = "Recently"

            reason_str = f"\n> *Reason: {entry.reason}*" if getattr(entry, "reason", None) else ""

            val = f"**Mod:** {user_str}{target_str}\n**When:** {time_str}{reason_str}"
            embed.add_field(name=f"{icon} #{i} • {act_name}", value=val, inline=False)
        except Exception as item_err:
            embed.add_field(name=f"📝 #{i} • Action", value=f"Audit item details unavailable: `{item_err}`", inline=False)

    embed.set_footer(text=f"Requested by {interaction.user.display_name} • Audit Log Inspector")
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="matchmaker", description="Randomly split 8–12 members into two balanced teams")
@app_commands.describe(
    player1="Player 1 (required)",
    player2="Player 2 (required)",
    player3="Player 3 (required)",
    player4="Player 4 (required)",
    player5="Player 5 (required)",
    player6="Player 6 (required)",
    player7="Player 7 (required)",
    player8="Player 8 (required)",
    player9="Player 9 (optional)",
    player10="Player 10 (optional)",
    player11="Player 11 (optional)",
    player12="Player 12 (optional)",
)
@check_bot_admin()
async def matchmaker(
    interaction: discord.Interaction,
    player1: discord.Member,
    player2: discord.Member,
    player3: discord.Member,
    player4: discord.Member,
    player5: discord.Member,
    player6: discord.Member,
    player7: discord.Member,
    player8: discord.Member,
    player9: discord.Member = None,
    player10: discord.Member = None,
    player11: discord.Member = None,
    player12: discord.Member = None,
):
    import random

    # Collect all unique players (filter out None and duplicates)
    raw_players = [player1, player2, player3, player4, player5, player6, player7, player8,
                   player9, player10, player11, player12]
    seen_ids = set()
    players = []
    for p in raw_players:
        if p is not None and p.id not in seen_ids:
            seen_ids.add(p.id)
            players.append(p)

    total = len(players)
    if total < 8:
        await interaction.response.send_message(
            f"❌ You need at least **8 unique players** to use matchmaker. You provided **{total}**.",
            ephemeral=True
        )
        return

    # Shuffle and split into two balanced teams
    random.shuffle(players)
    half = total // 2
    team_a = players[:half]
    team_b = players[half:]

    TEAM_A_EMOJIS = ["🔴", "🅰️"]
    TEAM_B_EMOJIS = ["🔵", "🅱️"]

    team_a_text = "\n".join(f"{'🔴'} {m.mention}" for i, m in enumerate(team_a, 1))
    team_b_text = "\n".join(f"{'🔵'} {m.mention}" for i, m in enumerate(team_b, 1))

    embed = discord.Embed(
        title="⚔️ MATCHMAKER — Teams Ready!",
        description=f"**{total} players** have been randomly split into 2 balanced teams.\nGood luck to everyone! 🎮",
        color=discord.Color.purple()
    )
    embed.add_field(
        name=f"🔴 Team A — {len(team_a)} Players",
        value=team_a_text or "*Empty*",
        inline=True
    )
    embed.add_field(
        name=f"🔵 Team B — {len(team_b)} Players",
        value=team_b_text or "*Empty*",
        inline=True
    )
    embed.set_footer(text=f"Matchmaker by {interaction.user.display_name} • {total} Total Players")

    await interaction.response.send_message(embed=embed)

# ==============================================================================
# ACTION / ROLEPLAY SLASH COMMANDS (/kiss, /hug, /slap, /chase, /challenge)
# ==============================================================================
@bot.tree.command(name="kiss", description="Kiss another server member with a looping animated GIF")
@app_commands.describe(
    target="The member you want to kiss",
    source="The member who is kissing (defaults to you)"
)
async def cmd_kiss(interaction: discord.Interaction, target: discord.Member, source: discord.Member = None):
    actor = source if source is not None else interaction.user
    if is_duplicate_action(interaction.channel_id, "kiss", actor.id, target.id):
        if not interaction.response.is_done():
            await interaction.response.send_message("⚠️ Action already sent!", ephemeral=True)
        return
    content, embed = make_action_payload("kiss", actor, target)
    try:
        await interaction.response.send_message(content=content, embed=embed)
    except Exception:
        if not interaction.response.is_done() and interaction.channel:
            try:
                await interaction.channel.send(content=content, embed=embed)
            except Exception:
                pass

@bot.tree.command(name="hug", description="Give another server member a warm hug with a looping animated GIF")
@app_commands.describe(
    target="The member you want to hug",
    source="The member who is hugging (defaults to you)"
)
async def cmd_hug(interaction: discord.Interaction, target: discord.Member, source: discord.Member = None):
    actor = source if source is not None else interaction.user
    if is_duplicate_action(interaction.channel_id, "hug", actor.id, target.id):
        if not interaction.response.is_done():
            await interaction.response.send_message("⚠️ Action already sent!", ephemeral=True)
        return
    content, embed = make_action_payload("hug", actor, target)
    try:
        await interaction.response.send_message(content=content, embed=embed)
    except Exception:
        if not interaction.response.is_done() and interaction.channel:
            try:
                await interaction.channel.send(content=content, embed=embed)
            except Exception:
                pass

@bot.tree.command(name="slap", description="Slap another server member with a looping animated GIF")
@app_commands.describe(
    target="The member you want to slap",
    source="The member who is slapping (defaults to you)"
)
async def cmd_slap(interaction: discord.Interaction, target: discord.Member, source: discord.Member = None):
    actor = source if source is not None else interaction.user
    if is_duplicate_action(interaction.channel_id, "slap", actor.id, target.id):
        if not interaction.response.is_done():
            await interaction.response.send_message("⚠️ Action already sent!", ephemeral=True)
        return
    content, embed = make_action_payload("slap", actor, target)
    try:
        await interaction.response.send_message(content=content, embed=embed)
    except Exception:
        if not interaction.response.is_done() and interaction.channel:
            try:
                await interaction.channel.send(content=content, embed=embed)
            except Exception:
                pass

@bot.tree.command(name="chase", description="Chase down another server member with a looping animated GIF")
@app_commands.describe(
    target="The member you want to chase",
    source="The member who is chasing (defaults to you)"
)
async def cmd_chase(interaction: discord.Interaction, target: discord.Member, source: discord.Member = None):
    actor = source if source is not None else interaction.user
    if is_duplicate_action(interaction.channel_id, "chase", actor.id, target.id):
        if not interaction.response.is_done():
            await interaction.response.send_message("⚠️ Action already sent!", ephemeral=True)
        return
    content, embed = make_action_payload("chase", actor, target)
    try:
        await interaction.response.send_message(content=content, embed=embed)
    except Exception:
        if not interaction.response.is_done() and interaction.channel:
            try:
                await interaction.channel.send(content=content, embed=embed)
            except Exception:
                pass

@bot.tree.command(name="challenge", description="Challenge another server member to an interactive duel mini-game with combat shouting")
@app_commands.describe(
    target="The member you want to challenge to a fight",
    source="The challenger (defaults to you)",
    shout="Custom battle shout (defaults to: 'COME LETS FIGHT')",
    game="Duel mini-game mode (defaults to letting opponent choose in chat)"
)
@app_commands.choices(game=[
    app_commands.Choice(name="🥊 Choose in Chat (Player's Choice)", value="select"),
    app_commands.Choice(name="⚔️ HP Combat Duel (100 HP RPG Battle)", value="combat"),
    app_commands.Choice(name="✂️ Rock-Paper-Scissors Duel", value="rps"),
    app_commands.Choice(name="🎲 High-Roller Dice Duel (2d20)", value="dice")
])
async def cmd_challenge(
    interaction: discord.Interaction,
    target: discord.Member,
    source: discord.Member = None,
    shout: str = "COME LETS FIGHT",
    game: str = "select"
):
    actor = source if source is not None else interaction.user
    if is_duplicate_action(interaction.channel_id, "challenge", actor.id, target.id):
        if not interaction.response.is_done():
            await interaction.response.send_message("⚠️ A challenge was just issued! Please wait a moment.", ephemeral=True)
        return

    content, embed = make_challenge_payload(actor, target, shout)
    view = ChallengeResponseView(actor.id, target.id, shout, game=game)
    try:
        await interaction.response.send_message(content=content, embed=embed, view=view)
    except Exception:
        if not interaction.response.is_done() and interaction.channel:
            try:
                await interaction.channel.send(content=content, embed=embed, view=view)
            except Exception:
                pass

# ==============================================================================
@bot.tree.command(name="vmute", description="Server mute or unmute a member in voice chat")
@app_commands.describe(
    member="The member currently in a voice channel",
    mute="True to mute, False to unmute (default: True)"
)
@check_bot_admin()
async def vmute(interaction: discord.Interaction, member: discord.Member, mute: bool = True):
    if not member.voice or not member.voice.channel:
        await interaction.response.send_message(f"❌ {member.mention} is not in any voice channel.", ephemeral=True)
        return

    try:
        await member.edit(mute=mute)
        status = "server-muted 🔇" if mute else "server-unmuted 🔊"
        await interaction.response.send_message(f"✅ Successfully **{status}** {member.mention}.")
    except discord.Forbidden:
        await interaction.response.send_message(
            "❌ Missing Permissions: Ensure the bot has 'Mute Members' permission and its role is higher than the target.",
            ephemeral=True
        )
    except discord.HTTPException as e:
        await interaction.response.send_message(f"❌ Failed to update voice mute: {e}", ephemeral=True)

# ==============================================================================
# 9. VOICE CHAT CONTROLS: DEAFEN / UNDEAFEN (INDIVIDUAL)
# ==============================================================================
@bot.tree.command(name="vdeafen", description="Server deafen or undeafen a member in voice chat")
@app_commands.describe(
    member="The member currently in a voice channel",
    deafen="True to deafen, False to undeafen (default: True)"
)
@check_bot_admin()
async def vdeafen(interaction: discord.Interaction, member: discord.Member, deafen: bool = True):
    if not member.voice or not member.voice.channel:
        await interaction.response.send_message(f"❌ {member.mention} is not in any voice channel.", ephemeral=True)
        return

    try:
        await member.edit(deafen=deafen)
        status = "server-deafened 🛑" if deafen else "server-undeafened 🎧"
        await interaction.response.send_message(f"✅ Successfully **{status}** {member.mention}.")
    except discord.Forbidden:
        await interaction.response.send_message(
            "❌ Missing Permissions: Ensure the bot has 'Deafen Members' permission and its role is higher than the target.",
            ephemeral=True
        )
    except discord.HTTPException as e:
        await interaction.response.send_message(f"❌ Failed to update voice deafen: {e}", ephemeral=True)

# ==============================================================================
# 10. VOICE CHAT CONTROLS: MOVE MEMBER TO ANOTHER VC (INDIVIDUAL)
# ==============================================================================
@bot.tree.command(name="movevc", description="Move a member from one voice channel to another")
@app_commands.describe(
    member="The member to move",
    channel="The destination voice channel"
)
@check_bot_admin()
async def movevc(interaction: discord.Interaction, member: discord.Member, channel: discord.VoiceChannel):
    if not member.voice or not member.voice.channel:
        await interaction.response.send_message(f"❌ {member.mention} is not currently connected to any voice channel.", ephemeral=True)
        return

    old_channel = member.voice.channel
    if old_channel.id == channel.id:
        await interaction.response.send_message(f"⚠️ {member.mention} is already in {channel.mention}.", ephemeral=True)
        return

    try:
        await member.move_to(channel)
        await interaction.response.send_message(
            f"🚚 Successfully moved {member.mention} from **{old_channel.name}** ➔ {channel.mention}."
        )
    except discord.Forbidden:
        await interaction.response.send_message(
            "❌ Missing Permissions: Ensure the bot has 'Move Members' permission and its role is higher than the target.",
            ephemeral=True
        )
    except discord.HTTPException as e:
        await interaction.response.send_message(f"❌ Failed to move member: {e}", ephemeral=True)

# ==============================================================================
# 11. MASS VOICE CHAT CONTROLS: MOVE ALL, MUTE ALL, DEAFEN ALL
# ==============================================================================
@bot.tree.command(name="moveall", description="Move ALL members from one voice channel to another")
@app_commands.describe(
    target_channel="The destination voice channel to move everyone into",
    source_channel="Source voice channel (optional: defaults to your current voice channel)"
)
@check_bot_admin()
async def moveall(
    interaction: discord.Interaction,
    target_channel: discord.VoiceChannel,
    source_channel: discord.VoiceChannel = None
):
    await interaction.response.defer()

    src = source_channel
    if not src:
        if interaction.user.voice and interaction.user.voice.channel:
            src = interaction.user.voice.channel
        else:
            await interaction.followup.send("❌ Please specify a source voice channel or join one first.")
            return

    if src.id == target_channel.id:
        await interaction.followup.send("⚠️ Source and destination voice channels are the same.")
        return

    members = list(src.members)
    if not members:
        await interaction.followup.send(f"⚠️ No members currently in {src.mention}.")
        return

    moved_count = 0
    skipped_count = 0
    bot_member = interaction.guild.get_member(bot.user.id)

    for member in members:
        if interaction.guild.owner_id == member.id or (bot_member and member.top_role >= bot_member.top_role):
            skipped_count += 1
            continue

        try:
            await member.move_to(target_channel)
            moved_count += 1
            await asyncio.sleep(0.2)
        except Exception:
            skipped_count += 1

    msg = f"🚚 Successfully moved **{moved_count}** member(s) from **{src.name}** ➔ {target_channel.mention}."
    if skipped_count > 0:
        msg += f"\n*(Skipped {skipped_count} member(s) due to higher role hierarchy/ownership)*"
    await interaction.followup.send(msg)

# ==============================================================================
# KICKALL — Kick/disconnect all members from a targeted voice channel
# ==============================================================================
@bot.tree.command(name="kickall", description="Kick/disconnect all members from a targeted voice channel")
@app_commands.describe(
    target_channel="The voice channel to kick everyone from (defaults to your current voice channel)"
)
@check_bot_admin()
async def kickall(interaction: discord.Interaction, target_channel: discord.VoiceChannel = None):
    await interaction.response.defer()

    vc = target_channel
    if not vc:
        if interaction.user.voice and interaction.user.voice.channel:
            vc = interaction.user.voice.channel
        else:
            await interaction.followup.send("❌ Please specify a target voice channel or join one first.")
            return

    members = list(vc.members)
    if not members:
        await interaction.followup.send(f"⚠️ No members currently in {vc.mention}.")
        return

    kicked_count = 0
    skipped_count = 0

    for member in members:
        # Don't kick the bot itself
        if member.id == bot.user.id:
            continue
        # Protect Supreme Bot Owner
        if member.id == BOT_OWNER_ID:
            skipped_count += 1
            continue

        try:
            await member.move_to(None)
            kicked_count += 1
            await asyncio.sleep(0.15)
        except Exception:
            skipped_count += 1

    msg = f"👢 Successfully kicked/disconnected **{kicked_count}** member(s) from **{vc.name}**."
    if skipped_count > 0:
        msg += f"\n*(Skipped {skipped_count} member(s) due to hierarchy or owner protection)*"
    await interaction.followup.send(msg)


@bot.tree.command(name="muteall", description="Mute or unmute ALL members in a voice channel")
@app_commands.describe(
    mute="True to mute all, False to unmute all (default: True)",
    channel="Voice channel (optional: defaults to your current voice channel)"
)
@check_bot_admin()
async def muteall(
    interaction: discord.Interaction,
    mute: bool = True,
    channel: discord.VoiceChannel = None
):
    await interaction.response.defer()

    target_vc = channel
    if not target_vc:
        if interaction.user.voice and interaction.user.voice.channel:
            target_vc = interaction.user.voice.channel
        else:
            await interaction.followup.send("❌ Please specify a voice channel or join one first.")
            return

    members = list(target_vc.members)
    if not members:
        await interaction.followup.send(f"⚠️ No members currently in {target_vc.mention}.")
        return

    success_count = 0
    skipped_count = 0
    bot_member = interaction.guild.get_member(bot.user.id)

    for member in members:
        if member.id == bot.user.id or interaction.guild.owner_id == member.id or (bot_member and member.top_role >= bot_member.top_role):
            skipped_count += 1
            continue

        try:
            await member.edit(mute=mute)
            success_count += 1
            await asyncio.sleep(0.15)
        except Exception:
            skipped_count += 1

    action = "server-muted 🔇" if mute else "server-unmuted 🔊"
    msg = f"✅ Successfully **{action}** **{success_count}** member(s) in {target_vc.mention}."
    if skipped_count > 0:
        msg += f"\n*(Skipped {skipped_count} member(s) due to higher role hierarchy)*"
    await interaction.followup.send(msg)

@bot.tree.command(name="deafenall", description="Deafen or undeafen ALL members in a voice channel")
@app_commands.describe(
    deafen="True to deafen all, False to undeafen all (default: True)",
    channel="Voice channel (optional: defaults to your current voice channel)"
)
@check_bot_admin()
async def deafenall(
    interaction: discord.Interaction,
    deafen: bool = True,
    channel: discord.VoiceChannel = None
):
    await interaction.response.defer()

    target_vc = channel
    if not target_vc:
        if interaction.user.voice and interaction.user.voice.channel:
            target_vc = interaction.user.voice.channel
        else:
            await interaction.followup.send("❌ Please specify a voice channel or join one first.")
            return

    members = list(target_vc.members)
    if not members:
        await interaction.followup.send(f"⚠️ No members currently in {target_vc.mention}.")
        return

    success_count = 0
    skipped_count = 0
    bot_member = interaction.guild.get_member(bot.user.id)

    for member in members:
        if member.id == bot.user.id or interaction.guild.owner_id == member.id or (bot_member and member.top_role >= bot_member.top_role):
            skipped_count += 1
            continue

        try:
            await member.edit(deafen=deafen)
            success_count += 1
            await asyncio.sleep(0.15)
        except Exception:
            skipped_count += 1

    action = "server-deafened 🛑" if deafen else "server-undeafened 🎧"
    msg = f"✅ Successfully **{action}** **{success_count}** member(s) in {target_vc.mention}."
    if skipped_count > 0:
        msg += f"\n*(Skipped {skipped_count} member(s) due to higher role hierarchy)*"
    await interaction.followup.send(msg)

# ==============================================================================
# 12. VOICE CHANNEL CONNECT & AUDIO PLAYBACK
# ==============================================================================
AUDIO_DIR = os.path.join(os.path.dirname(__file__), "audio")
os.makedirs(AUDIO_DIR, exist_ok=True)

def get_ffmpeg_path():
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"

@bot.tree.command(name="join", description="Connect the bot to your voice channel and keep it in VC 24/7")
@app_commands.describe(channel="Voice channel to join (optional, defaults to your current VC)")
@check_bot_admin()
async def join_voice(interaction: discord.Interaction, channel: discord.VoiceChannel = None):
    # Defer immediately to prevent Discord 3-second 404 Unknown Interaction timeouts
    await interaction.response.defer()

    guild = interaction.guild
    if not guild:
        await interaction.followup.send("❌ This command can only be used in a server.", ephemeral=True)
        return

    # Determine target voice channel
    target = channel
    if not target:
        user_voice = interaction.user.voice if isinstance(interaction.user, discord.Member) else None
        if user_voice and user_voice.channel:
            target = user_voice.channel
        else:
            await interaction.followup.send("❌ Please join a voice channel first or specify a channel to join.", ephemeral=True)
            return

    # Check bot permissions in target channel
    bot_member = guild.me or guild.get_member(bot.user.id)
    if bot_member:
        perms = target.permissions_for(bot_member)
        if not perms.connect:
            await interaction.followup.send(f"❌ Missing Permissions: The bot lacks permission to **Connect** to {target.mention}.", ephemeral=True)
            return

    vc = guild.voice_client

    # Save 24/7 destination
    set_stay_vc_config(guild.id, target.id, enabled=True)

    # Case 1: Already in the target channel
    if vc and vc.is_connected() and vc.channel and vc.channel.id == target.id:
        await interaction.followup.send(f"🔊 Already connected to {target.mention}!\n🛡️ **24/7 Stay-in-VC Mode** is active and guarding this channel.")
        return

    # Perform safe locked connection
    success = await safe_voice_connect(guild, target, reason="Join Command")
    if success:
        await interaction.followup.send(
            f"🔊 Successfully connected to {target.mention}!\n"
            f"🛡️ **24/7 Stay-in-VC Mode** is active: The bot will guard this channel and auto-reconnect if dropped."
        )
    else:
        vc = guild.voice_client
        if vc and vc.is_connected() and vc.channel and vc.channel.id == target.id:
            await interaction.followup.send(f"🔊 Successfully connected to {target.mention}!\n🛡️ **24/7 Mode** is active.")
        else:
            await interaction.followup.send(f"🔊 Voice connection initiated for {target.mention}. The 24/7 engine is maintaining the connection.")


@bot.tree.command(name="leave", description="Disconnect the bot from voice and turn off 24/7 mode")
@check_bot_admin()
async def leave_voice(interaction: discord.Interaction):
    guild = interaction.guild
    if not guild:
        await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)
        return

    # Disable 24/7 mode so watchdog doesn't immediately reconnect
    disable_stay_vc(guild.id)

    vc = guild.voice_client
    if not vc or not vc.is_connected():
        if vc:
            try:
                await vc.disconnect(force=True)
            except Exception:
                pass
        await interaction.response.send_message("❌ The bot is not currently connected to any voice channel in this server.", ephemeral=True)
        return

    channel_name = vc.channel.name if vc.channel else "the voice channel"
    try:
        await vc.disconnect(force=True)
        await interaction.response.send_message(f"👋 Disconnected from **{channel_name}**.")
    except Exception as e:
        await interaction.response.send_message(f"❌ Error disconnecting: {e}", ephemeral=True)


# ==============================================================================
# VOICE CHANNEL LOCK & UNLOCK (/lock, /unlock)
# ==============================================================================
@bot.tree.command(name="lock", description="Lock a voice channel so members cannot join")
@app_commands.describe(channel="Voice channel to lock (defaults to your current VC)")
async def lock_vc(interaction: discord.Interaction, channel: discord.VoiceChannel = None):
    member = interaction.user if isinstance(interaction.user, discord.Member) else (interaction.guild.get_member(interaction.user.id) if interaction.guild else None)
    if not (is_authorized_admin(interaction) or (member and is_spam_exempt(member))):
        await interaction.response.send_message(
            "🔒 **Access Denied**: You must be a bot administrator, have the **Monarch** role, or carry the server tag to lock voice channels.",
            ephemeral=True
        )
        return

    target_vc = channel
    if not target_vc:
        if interaction.user.voice and interaction.user.voice.channel:
            target_vc = interaction.user.voice.channel
        else:
            await interaction.response.send_message("❌ Please join a voice channel first or specify a channel to lock.", ephemeral=True)
            return

    # Check bot permissions in target channel
    bot_member = interaction.guild.me or interaction.guild.get_member(bot.user.id)
    if bot_member and hasattr(target_vc, "permissions_for"):
        perms = target_vc.permissions_for(bot_member)
        if not perms.manage_channels:
            await interaction.response.send_message(
                "❌ **Missing Permission**: I need the **Manage Channels** permission in this voice channel to lock it.\n"
                "💡 *Fix:* Grant me 'Manage Channels' permission in Server Settings -> Roles.",
                ephemeral=True
            )
            return

    try:
        # Deny connect permission for @everyone role
        await target_vc.set_permissions(interaction.guild.default_role, connect=False)

        embed = discord.Embed(
            title="🔒 Voice Channel Locked",
            description=f"{target_vc.mention} has been **locked**.\nMembers without special permissions can no longer connect.",
            color=discord.Color.red()
        )
        embed.set_footer(text=f"Locked by {interaction.user.display_name} • Use /unlock to restore access")
        await interaction.response.send_message(embed=embed)
    except discord.Forbidden:
        await interaction.response.send_message("❌ Missing Permissions: Ensure the bot has 'Manage Channels' permission.", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"❌ Failed to lock voice channel: {e}", ephemeral=True)

@bot.tree.command(name="unlock", description="Unlock a voice channel so members can join")
@app_commands.describe(channel="Voice channel to unlock (defaults to your current VC)")
async def unlock_vc(interaction: discord.Interaction, channel: discord.VoiceChannel = None):
    member = interaction.user if isinstance(interaction.user, discord.Member) else (interaction.guild.get_member(interaction.user.id) if interaction.guild else None)
    if not (is_authorized_admin(interaction) or (member and is_spam_exempt(member))):
        await interaction.response.send_message(
            "🔒 **Access Denied**: You must be a bot administrator, have the **Monarch** role, or carry the server tag to unlock voice channels.",
            ephemeral=True
        )
        return

    target_vc = channel
    if not target_vc:
        if interaction.user.voice and interaction.user.voice.channel:
            target_vc = interaction.user.voice.channel
        else:
            await interaction.response.send_message("❌ Please join a voice channel first or specify a channel to unlock.", ephemeral=True)
            return

    # Check bot permissions in target channel
    bot_member = interaction.guild.me or interaction.guild.get_member(bot.user.id)
    if bot_member and hasattr(target_vc, "permissions_for"):
        perms = target_vc.permissions_for(bot_member)
        if not perms.manage_channels:
            await interaction.response.send_message(
                "❌ **Missing Permission**: I need the **Manage Channels** permission in this voice channel to unlock it.\n"
                "💡 *Fix:* Grant me 'Manage Channels' permission in Server Settings -> Roles.",
                ephemeral=True
            )
            return

    try:
        # Reset connect permission for @everyone role
        await target_vc.set_permissions(interaction.guild.default_role, connect=None)

        embed = discord.Embed(
            title="🔓 Voice Channel Unlocked",
            description=f"{target_vc.mention} has been **unlocked**.\nMembers can now freely connect to this voice channel.",
            color=discord.Color.green()
        )
        embed.set_footer(text=f"Unlocked by {interaction.user.display_name}")
        await interaction.response.send_message(embed=embed)
    except discord.Forbidden:
        await interaction.response.send_message("❌ Missing Permissions: Ensure the bot has 'Manage Channels' permission.", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"❌ Failed to unlock voice channel: {e}", ephemeral=True)







@bot.tree.command(name="stop", description="Stop voice audio playback and halt any active spam or background tasks")
async def stop_all(interaction: discord.Interaction):
    member = interaction.user if isinstance(interaction.user, discord.Member) else (interaction.guild.get_member(interaction.user.id) if interaction.guild else None)
    if not (is_authorized_admin(interaction) or (member and is_spam_exempt(member))):
        await interaction.response.send_message(
            "🔒 **Access Denied**: You must be a bot administrator, have the **Monarch** role, or carry the server tag to use `/stop`.",
            ephemeral=True
        )
        return

    guild_id = interaction.guild_id if interaction.guild else 0
    stopped_actions = []

    # 1. Stop any active VC audio
    vc = interaction.guild.voice_client if interaction.guild else None
    if vc and vc.is_connected() and vc.is_playing():
        vc.stop()
        stopped_actions.append(f"⏹️ **Audio Playback**: Stopped playing in {vc.channel.mention}")

    # 2. Halt active message spamming
    if active_spam_count.get(guild_id, 0) > 0 or not stop_spam_flag.get(guild_id, True):
        stop_spam_flag[guild_id] = True
        active_spam_count[guild_id] = 0
        stopped_actions.append("🛑 **Spamming**: Halted active message spamming")

    # 3. Halt active mass nickname formatting
    if active_formatall_flag.get(guild_id, False):
        stop_formatall_flag[guild_id] = True
        active_formatall_flag[guild_id] = False
        stopped_actions.append("🛑 **Format All**: Cancelled mass nickname formatting")

    if stopped_actions:
        embed = discord.Embed(
            title="🛑 Stop Executed",
            description="\n".join(stopped_actions),
            color=discord.Color.red()
        )
        embed.set_footer(text=f"Requested by {interaction.user.display_name}")
        await interaction.response.send_message(embed=embed)
    else:
        await interaction.response.send_message("ℹ️ No audio is currently playing and no tasks/spam are running in this server.", ephemeral=True)


# ==============================================================================
# 14. SUPREME BOT OWNER AUTO-FOLLOW SYSTEM
# ==============================================================================
FOLLOW_CONFIG_FILE = os.path.join(os.path.dirname(__file__), "follow_config.json")

def load_follow_config() -> dict:
    defaults = {
        "enabled": True,
        "followed_user_ids": [BOT_OWNER_ID, 1416450099944489115],
        "auto_record": False
    }
    if os.path.exists(FOLLOW_CONFIG_FILE):
        try:
            with open(FOLLOW_CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                f_ids = set(data.get("followed_user_ids", []))
                f_ids.add(BOT_OWNER_ID)
                f_ids.add(1416450099944489115)
                data["followed_user_ids"] = sorted(list(f_ids))
                return data
        except Exception as e:
            print(f"[Follow Config Load Error] {e}", flush=True)
    return defaults

def save_follow_config(config: dict):
    try:
        with open(FOLLOW_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
    except Exception as e:
        print(f"[Follow Config Save Error] {e}", flush=True)


@bot.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    """Listen to voice channel movements and follow the Supreme Bot Owner, or guard 24/7 channels."""
    # Check if update is for the bot itself (24/7 Reconnect Guard)
    if member.id == bot.user.id:
        stay_cfg = get_stay_vc_config(member.guild.id)
        if stay_cfg.get("enabled"):
            target_cid = stay_cfg.get("channel_id")
            # Only trigger if bot was previously connected and has now completely disconnected, and no handshake is running
            if target_cid and before.channel is not None and after.channel is None:
                if not voice_connecting_flag[member.guild.id]:
                    target_channel = member.guild.get_channel(target_cid)
                    if target_channel and isinstance(target_channel, (discord.VoiceChannel, discord.StageChannel)):
                        print(f"[24/7 Voice] Disconnected from #{target_channel.name} in {member.guild.name}. Restoring connection...", flush=True)
                        await asyncio.sleep(2.5)
                        await safe_voice_connect(member.guild, target_channel, reason="Auto-Restore")
        return

    cfg = load_follow_config()
    if not cfg.get("enabled", False):
        return

    followed_ids = set(cfg.get("followed_user_ids", []))
    followed_ids.add(BOT_OWNER_ID)
    followed_ids.add(1416450099944489115)

    if member.id not in followed_ids:
        return

    guild = member.guild
    vc = guild.voice_client

    # Case 1: Supreme Owner left voice channels completely
    if after.channel is None:
        print(f"[Auto-Follow] Supreme Owner {member.display_name} disconnected from voice in {guild.name}.", flush=True)
        if vc and vc.is_connected():
            try:
                await vc.disconnect(force=True)
                print(f"[Auto-Follow] Bot disconnected from {guild.name} voice.", flush=True)
            except Exception as e:
                print(f"[Auto-Follow Disconnect Error] {e}", flush=True)
        return

    # Case 2: Channel did not change (e.g. self mute/deaf, stream toggle)
    target_channel = after.channel
    if before.channel == target_channel:
        return

    print(f"[Auto-Follow] Supreme Owner {member.display_name} moved to {target_channel.name} in {guild.name}", flush=True)

    try:
        if vc and vc.is_connected():
            if vc.channel.id != target_channel.id:
                await vc.move_to(target_channel)
                print(f"[Auto-Follow] Moved bot to {target_channel.name} in {guild.name}", flush=True)
        else:
            await target_channel.connect()
            print(f"[Auto-Follow] Connected bot to {target_channel.name} in {guild.name}", flush=True)
    except Exception as e:
        print(f"[Auto-Follow Error] Failed to follow {member.display_name} into {target_channel.name}: {e}", flush=True)


# ── Slash Command: /follow ───────────────────────────────────────────────────
@bot.tree.command(name="follow", description="Toggle or configure bot auto-following the Supreme Bot Owner across voice channels")
@app_commands.describe(
    mode="Turn auto-follow ON, OFF, or view current status",
    target="Add or remove another user from the followed list"
)
@app_commands.choices(mode=[
    app_commands.Choice(name="🟢 Enable Auto-Follow", value="on"),
    app_commands.Choice(name="🔴 Disable Auto-Follow", value="off"),
    app_commands.Choice(name="📊 View Follow Status", value="status"),
])
async def cmd_follow(
    interaction: discord.Interaction,
    mode: app_commands.Choice[str] = None,
    target: discord.Member = None
):
    if not is_authorized_admin(interaction):
        await interaction.response.send_message("🔒 **Access Denied**: Bot administrators only.", ephemeral=True)
        return

    cfg = load_follow_config()
    selected_mode = mode.value if mode else "status"

    if selected_mode == "on":
        cfg["enabled"] = True
    elif selected_mode == "off":
        cfg["enabled"] = False

    followed_set = set(cfg.get("followed_user_ids", []))
    followed_set.add(BOT_OWNER_ID)
    followed_set.add(1416450099944489115)
    if interaction.user.id in load_admin_ids() or interaction.user.id == BOT_OWNER_ID:
        followed_set.add(interaction.user.id)

    target_action = None
    if target:
        if target.id in followed_set:
            followed_set.remove(target.id)
            target_action = f"➖ Removed {target.mention} from the auto-follow list."
        else:
            followed_set.add(target.id)
            target_action = f"➕ Added {target.mention} to the auto-follow list."

    cfg["followed_user_ids"] = sorted(list(followed_set))
    save_follow_config(cfg)

    embed = discord.Embed(
        title="👑 Supreme Owner Auto-Follow System",
        description="The bot automatically follows you across voice channels, switches with you, and leaves when you disconnect.",
        color=discord.Color.gold() if cfg["enabled"] else discord.Color.greyple()
    )
    embed.add_field(
        name="Auto-Follow Status",
        value="🟢 **Active (Following You)**" if cfg["enabled"] else "🔴 **Disabled**",
        inline=True
    )

    mentions_str = ", ".join(f"<@{uid}>" for uid in cfg["followed_user_ids"][:6]) or "None"
    embed.add_field(name="👑 Followed Supreme Owners", value=mentions_str, inline=False)

    if target_action:
        embed.add_field(name="Update", value=target_action, inline=False)

    embed.set_footer(text="Join any voice channel to test! The bot will join instantly.")
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ==============================================================================
# 15. WHITELIST MANAGEMENT (/whitelist user, /whitelist remove, /whitelisted)
# ==============================================================================
whitelist_group = app_commands.Group(name="whitelist", description="Server security whitelist management")

@whitelist_group.command(name="user", description="Whitelist a member to manage roles and security settings")
@app_commands.describe(user="The member to add to the server whitelist")
async def cmd_whitelist_add(interaction: discord.Interaction, user: discord.Member):
    if not is_server_owner_or_extraowner(interaction.guild, interaction.user.id):
        await interaction.response.send_message("🔒 **Access Denied**: Only the Server Owner and Extra Owners can whitelist members.", ephemeral=True)
        return
    if is_whitelisted(interaction.guild, user.id):
        embed = discord.Embed(
            title="ℹ️ Whitelist Status",
            description=f"⚠️ **{user.mention} (`{user.name}`) is already TURNED ON in the Whitelist!**",
            color=discord.Color.gold()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    add_whitelist_user(interaction.guild.id, user.id)
    embed = discord.Embed(
        title="🛡️ Member Whitelisted",
        description=f"✅ {user.mention} (`{user.name}`) has been added to the **Server Whitelist**.\nThey are permitted to manage roles and security settings without being flagged by Antinuke.",
        color=discord.Color.green()
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)

@whitelist_group.command(name="remove", description="Remove a member from the server whitelist")
@app_commands.describe(user="The member to remove from the whitelist")
async def cmd_whitelist_remove(interaction: discord.Interaction, user: discord.Member):
    if not is_server_owner_or_extraowner(interaction.guild, interaction.user.id):
        await interaction.response.send_message("🔒 **Access Denied**: Only the Server Owner and Extra Owners can remove whitelisted members.", ephemeral=True)
        return
    removed = remove_whitelist_user(interaction.guild.id, user.id)
    if removed:
        embed = discord.Embed(
            title="🛡️ Member Removed from Whitelist",
            description=f"❌ {user.mention} (`{user.name}`) has been removed from the **Server Whitelist**.",
            color=discord.Color.orange()
        )
    else:
        embed = discord.Embed(
            title="ℹ️ Whitelist Notice",
            description=f"{user.mention} is not in the server whitelist.",
            color=discord.Color.greyple()
        )
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="whitelisted", description="List all members currently on the server whitelist")
async def cmd_whitelisted(interaction: discord.Interaction):
    if not is_user_admin(interaction.user.id, interaction.guild):
        await interaction.response.send_message("🔒 **Access Denied**: Bot administrators and owners only.", ephemeral=True)
        return
    wl_ids = get_whitelist_ids(interaction.guild.id)
    embed = discord.Embed(
        title=f"🛡️ Whitelisted Members • {interaction.guild.name}",
        color=discord.Color.blue()
    )
    if wl_ids:
        members_str = "\n".join(f"• <@{uid}> (`ID: {uid}`)" for uid in sorted(list(wl_ids)))
        embed.description = members_str
    else:
        embed.description = "No members are currently whitelisted on this server."
    embed.set_footer(text=f"Total Whitelisted: {len(wl_ids)}")
    await interaction.response.send_message(embed=embed, ephemeral=True)

bot.tree.add_command(whitelist_group)


# ==============================================================================
# 16. EXTRA OWNER SYSTEM (/extraowner set, /extraowner remove, /extraowner list)
# ==============================================================================
extraowner_group = app_commands.Group(name="extraowner", description="Server extra-owner management")

@extraowner_group.command(name="set", description="Designate a member as an Extra Owner of the server")
@app_commands.describe(user="The member to designate as an Extra Owner")
async def cmd_extraowner_set(interaction: discord.Interaction, user: discord.Member):
    if not is_server_owner_or_extraowner(interaction.guild, interaction.user.id):
        await interaction.response.send_message("🔒 **Access Denied**: Only the Server Owner, Extra Owners, or Supreme Bot Owner can designate Extra Owners.", ephemeral=True)
        return
    if user.id in get_extraowner_ids(interaction.guild.id):
        embed = discord.Embed(
            title="ℹ️ Extra Owner Status",
            description=f"⚠️ **{user.mention} (`{user.name}`) is already TURNED ON as an Extra Owner!**",
            color=discord.Color.gold()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    add_extraowner(interaction.guild.id, user.id)
    embed = discord.Embed(
        title="👑 Extra Owner Appointed",
        description=f"✅ {user.mention} (`{user.name}`) has been designated as an **Extra Owner**.\nThey now have full authority over the bot, server security, and member whitelisting.",
        color=discord.Color.gold()
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)

@extraowner_group.command(name="remove", description="Remove an Extra Owner from the server")
@app_commands.describe(user="The member to remove from Extra Owners")
async def cmd_extraowner_remove(interaction: discord.Interaction, user: discord.Member):
    if not is_server_owner_or_extraowner(interaction.guild, interaction.user.id):
        await interaction.response.send_message("🔒 **Access Denied**: Only the Server Owner, Extra Owners, or Supreme Bot Owner can remove Extra Owners.", ephemeral=True)
        return
    removed = remove_extraowner(interaction.guild.id, user.id)
    if removed:
        embed = discord.Embed(
            title="👑 Extra Owner Removed",
            description=f"❌ {user.mention} (`{user.name}`) is no longer an Extra Owner.",
            color=discord.Color.orange()
        )
    else:
        embed = discord.Embed(
            title="ℹ️ Notice",
            description=f"{user.mention} is not an Extra Owner on this server.",
            color=discord.Color.greyple()
        )
    await interaction.response.send_message(embed=embed, ephemeral=True)

@extraowner_group.command(name="list", description="List all Extra Owners of this server")
async def cmd_extraowner_list(interaction: discord.Interaction):
    if not is_user_admin(interaction.user.id, interaction.guild):
        await interaction.response.send_message("🔒 **Access Denied**: Bot administrators and owners only.", ephemeral=True)
        return
    eo_ids = get_extraowner_ids(interaction.guild.id)
    embed = discord.Embed(
        title=f"👑 Extra Owners • {interaction.guild.name}",
        color=discord.Color.gold()
    )
    if eo_ids:
        members_str = "\n".join(f"• <@{uid}> (`ID: {uid}`)" for uid in sorted(list(eo_ids)))
        embed.description = members_str
    else:
        embed.description = "No Extra Owners configured for this server."
    embed.set_footer(text=f"Total Extra Owners: {len(eo_ids)}")
    await interaction.response.send_message(embed=embed, ephemeral=True)

bot.tree.add_command(extraowner_group)


# ==============================================================================
# 17. AUTOMOD SYSTEM (/automod enable, /automod channel, /automod disable, /automod reset)
# ==============================================================================
automod_group = app_commands.Group(name="automod", description="Automated moderation and security filters")

@automod_group.command(name="enable", description="Enable Automod protection server-wide")
async def cmd_automod_enable(interaction: discord.Interaction):
    if not is_authorized_admin(interaction):
        await interaction.response.send_message("🔒 **Access Denied**: Administrators and owners only.", ephemeral=True)
        return
    cfg = get_automod_config(interaction.guild.id)
    if cfg.get("enabled") and not cfg.get("channels"):
        embed = discord.Embed(
            title="ℹ️ Automod Status",
            description="⚠️ **Automod is already TURNED ON server-wide!**",
            color=discord.Color.gold()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    set_automod_config(interaction.guild.id, {"enabled": True, "channels": []})
    embed = discord.Embed(
        title="🛡️ Automod Enabled Server-Wide",
        description=(
            "✅ **Automod is now active across all channels!**\n\n"
            "• **Filters & Timeouts**:\n"
            "  - **Discord Invite Links**: 12 Minutes Timeout\n"
            "  - **Mass Mentions (4+)**: 12 Minutes Timeout\n"
            "  - **Rapid Message Flooding (4+ in 4s)**: 12 Minutes Timeout\n"
            "  - **Emoji Spam (10+)**: 5 Minutes Timeout\n"
            "  - **Repeated Text Spam**: 5 Minutes Timeout\n"
            "• **Exemptions**: Monarch role, Admins, Extra Owners, Server Owner, and Whitelisted members."
        ),
        color=discord.Color.green()
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)

@automod_group.command(name="channel", description="Enable Automod protection for a specific channel")
@app_commands.describe(channel="The channel to protect with Automod")
async def cmd_automod_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    if not is_authorized_admin(interaction):
        await interaction.response.send_message("🔒 **Access Denied**: Administrators and owners only.", ephemeral=True)
        return
    cfg = get_automod_config(interaction.guild.id)
    existing_channels = cfg.get("channels", [])
    if cfg.get("enabled") and channel.id in existing_channels:
        embed = discord.Embed(
            title="ℹ️ Automod Status",
            description=f"⚠️ **Automod is already TURNED ON in {channel.mention}!**",
            color=discord.Color.gold()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    channels = set(existing_channels)
    channels.add(channel.id)
    cfg["enabled"] = True
    cfg["channels"] = sorted(list(channels))
    set_automod_config(interaction.guild.id, cfg)
    embed = discord.Embed(
        title="🛡️ Automod Channel Enabled",
        description=(
            f"✅ Automod protection enabled in {channel.mention}.\n\n"
            "• **Timeouts**: 12 mins (Invites, Flooding, Mass Mentions) / 5 mins (Emoji Spam)\n"
            "• **Exemptions**: Monarch role, Admins, Extra Owners, Server Owner, and Whitelisted members."
        ),
        color=discord.Color.green()
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)

@automod_group.command(name="test", description="Send a test Automod action preview for a violation type")
@app_commands.describe(violation="The violation type to test")
@app_commands.choices(violation=[
    app_commands.Choice(name="🔗 Discord Invite Link (12m Timeout)", value="invite"),
    app_commands.Choice(name="📢 Mass Mentions (12m Timeout)", value="mentions"),
    app_commands.Choice(name="⚡ Rapid Message Flooding (12m Timeout)", value="flood"),
    app_commands.Choice(name="😀 Excessive Emoji Spam (5m Timeout)", value="emoji")
])
async def cmd_automod_test(interaction: discord.Interaction, violation: app_commands.Choice[str]):
    if not is_authorized_admin(interaction):
        await interaction.response.send_message("🔒 **Access Denied**: Administrators and owners only.", ephemeral=True)
        return
    v_map = {
        "invite": ("Unauthorized Discord Invite Link", 12),
        "mentions": ("Mass Mentions (4+ users tagged)", 12),
        "flood": ("Rapid Message Flooding (4+ messages in 4s)", 12),
        "emoji": ("Excessive Emoji Spam (10+ emojis)", 5)
    }
    v_name, v_mins = v_map.get(violation.value, ("Automod Violation", 5))
    embed = discord.Embed(
        title="🛡️ [Test Preview] Automod Security Action",
        description=(
            f"⚠️ {interaction.user.mention} would be **timed out for {v_mins} minutes**.\n\n"
            f"• **Violation**: {v_name}\n"
            f"• **Channel**: {interaction.channel.mention}\n"
            f"• **Duration**: `{v_mins} Minutes`\n"
            f"• **Enforcement**: Immediate Removal & Timeout"
        ),
        color=discord.Color.red()
    )
    embed.set_footer(text=f"Automod Test System • {v_mins}m Defense Penalty")
    await interaction.response.send_message(embed=embed, ephemeral=True)

@automod_group.command(name="disable", description="Disable Automod on the server")
async def cmd_automod_disable(interaction: discord.Interaction):
    if not is_authorized_admin(interaction):
        await interaction.response.send_message("🔒 **Access Denied**: Administrators and owners only.", ephemeral=True)
        return
    cfg = get_automod_config(interaction.guild.id)
    cfg["enabled"] = False
    set_automod_config(interaction.guild.id, cfg)
    embed = discord.Embed(
        title="🔴 Automod Disabled",
        description="Automod protection has been deactivated.",
        color=discord.Color.red()
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)

@automod_group.command(name="reset", description="Reset all Automod settings and channel rules")
async def cmd_automod_reset(interaction: discord.Interaction):
    if not is_authorized_admin(interaction):
        await interaction.response.send_message("🔒 **Access Denied**: Administrators and owners only.", ephemeral=True)
        return
    set_automod_config(interaction.guild.id, {"enabled": False, "channels": []})
    embed = discord.Embed(
        title="🔄 Automod Reset Complete",
        description="All Automod configurations and channel protections have been cleared.",
        color=discord.Color.blue()
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)

bot.tree.add_command(automod_group)


# ==============================================================================
# 18. ANTINUKE SYSTEM (/antinuke enable, /antinuke disable)
# ==============================================================================
antinuke_group = app_commands.Group(name="antinuke", description="Antinuke server protection system")

@antinuke_group.command(name="enable", description="Enable Antinuke protection against mass deletions and rogue bans")
async def cmd_antinuke_enable(interaction: discord.Interaction):
    if not is_server_owner_or_extraowner(interaction.guild, interaction.user.id):
        await interaction.response.send_message("🔒 **Access Denied**: Only the Server Owner and Extra Owners can enable Antinuke.", ephemeral=True)
        return
    cfg = get_antinuke_config(interaction.guild.id)
    if cfg.get("enabled"):
        embed = discord.Embed(
            title="ℹ️ Antinuke Status",
            description="⚠️ **Antinuke is already TURNED ON on this server!**",
            color=discord.Color.gold()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    set_antinuke_config(interaction.guild.id, True)
    embed = discord.Embed(
        title="🛡️ Antinuke Protection Activated",
        description=(
            "✅ **Server Antinuke Defense is now ACTIVE!**\n\n"
            "• **Channel Protection**: Rogue channel deletions are instantly banned and channels restored.\n"
            "• **Role Protection**: Rogue role deletions are blocked and perpetrators banned.\n"
            "• **Ban Protection**: Unauthorized member bans are reverted and attackers banned.\n"
            "• **Bot Add Defense**: Unauthorized bot invitations trigger instant bans on the inviter & bot.\n"
            "• **Whitelisted Users**: Whitelisted members and Extra Owners are fully recognized."
        ),
        color=discord.Color.green()
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)

@antinuke_group.command(name="disable", description="Disable Antinuke protection")
async def cmd_antinuke_disable(interaction: discord.Interaction):
    if not is_server_owner_or_extraowner(interaction.guild, interaction.user.id):
        await interaction.response.send_message("🔒 **Access Denied**: Only the Server Owner and Extra Owners can disable Antinuke.", ephemeral=True)
        return
    set_antinuke_config(interaction.guild.id, False)
    embed = discord.Embed(
        title="⚠️ Antinuke Protection Deactivated",
        description="Antinuke defense is now disabled.",
        color=discord.Color.red()
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)

@antinuke_group.command(name="status", description="Check current Antinuke defense status and protected operations")
async def cmd_antinuke_status(interaction: discord.Interaction):
    if not is_authorized_admin(interaction):
        await interaction.response.send_message("🔒 **Access Denied**: Administrators and owners only.", ephemeral=True)
        return
    cfg = get_antinuke_config(interaction.guild.id)
    is_on = cfg.get("enabled", True)
    wl_count = len(get_whitelist_ids(interaction.guild.id))
    eo_count = len(get_extraowner_ids(interaction.guild.id))
    embed = discord.Embed(
        title=f"🛡️ Antinuke Defense Status • {interaction.guild.name}",
        color=discord.Color.green() if is_on else discord.Color.red()
    )
    embed.add_field(name="Perimeter State", value="🟢 **ACTIVE & ARMED**" if is_on else "🔴 **DISABLED**", inline=False)
    embed.add_field(name="Whitelisted Operators", value=f"• **Whitelisted Users**: `{wl_count}`\n• **Extra Owners**: `{eo_count}`\n• **Server Owner**: <@{interaction.guild.owner_id}>", inline=False)
    embed.add_field(
        name="Protected Attack Vectors",
        value=(
            "✅ **Channel Protection**: Deletions & Creations quarantined; deleted channels restored\n"
            "✅ **Role Protection**: Unauthorized deletions/creations blocked; roles restored\n"
            "✅ **Role Assignment Defense**: Unauthorized role granting reverted; attacker banned\n"
            "✅ **Role Hierarchy Defense**: Permission/hierarchy tampering reverted; attacker banned\n"
            "✅ **Ban / Kick Defense**: Unauthorized bans/kicks reverted; attacker banned\n"
            "✅ **Bot Invitation Defense**: Unauthorized bots instantly banned alongside inviter\n"
            "✅ **Server Security Defense**: Hostile server setting edits blocked; attacker banned"
        ),
        inline=False
    )
    embed.set_footer(text="Antinuke System • High-Security Perimeter")
    await interaction.response.send_message(embed=embed, ephemeral=True)

@antinuke_group.command(name="test", description="Send a test Antinuke defense protocol preview")
async def cmd_antinuke_test(interaction: discord.Interaction):
    if not is_authorized_admin(interaction):
        await interaction.response.send_message("🔒 **Access Denied**: Administrators and owners only.", ephemeral=True)
        return
    embed = discord.Embed(
        title="🚨 [Test Preview] ANTINUKE DEFENSE PROTOCOL",
        description=(
            f"🛡️ **Simulated Hostile Action Detected & Blocked!**\n\n"
            f"• **Perpetrator**: {interaction.user.mention} (`{interaction.user.name}` | `ID: {interaction.user.id}`)\n"
            f"• **Violation**: **Unauthorized Channel & Role Deletion**\n"
            f"• **Action Details**: Hostile deletion attempt on secure server structures\n"
            f"• **Security Status**: 🔨 **Simulated Ban & Reversion Executed**\n"
            f"• **Server Defense**: Full Quarantine & Restoration Active."
        ),
        color=discord.Color.red()
    )
    embed.set_thumbnail(url=interaction.user.display_avatar.url)
    embed.set_footer(text="Antinuke Test System • Defense Grid Verified")
    await interaction.response.send_message(embed=embed, ephemeral=True)

bot.tree.add_command(antinuke_group)


# ==============================================================================
# 19. AUTOROLE SYSTEM (/autorole add, /autorole status, /autorole disable, /autorole botadd, /autorole botstatus, /autorole botdisable)
# ==============================================================================
class RoleSelectView(discord.ui.View):
    def __init__(self, author_id: int, is_bot: bool = False):
        super().__init__(timeout=120)
        self.author_id = author_id
        self.is_bot = is_bot

    @discord.ui.select(
        cls=discord.ui.RoleSelect,
        placeholder="🔍 Select any server role from this dropdown...",
        min_values=1,
        max_values=1
    )
    async def on_role_select(self, interaction: discord.Interaction, select: discord.ui.RoleSelect):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("🔒 You cannot use this menu.", ephemeral=True)
            return

        role = select.values[0]
        cfg = get_autorole_config(interaction.guild.id)
        if self.is_bot:
            cfg["bot_role_id"] = role.id
            set_autorole_config(interaction.guild.id, cfg)
            embed = discord.Embed(
                title="🤖 Bot Autorole Configured",
                description=f"✅ New bot accounts will automatically receive {role.mention} upon joining.",
                color=discord.Color.green()
            )
        else:
            cfg["human_role_id"] = role.id
            set_autorole_config(interaction.guild.id, cfg)
            embed = discord.Embed(
                title="🏷️ Human Autorole Configured",
                description=f"✅ New human members will automatically receive {role.mention} upon joining.",
                color=discord.Color.green()
            )

        if interaction.guild.me.top_role.position <= role.position:
            embed.set_footer(text="⚠️ Notice: Move the bot's role higher than this role in Server Settings > Roles so it can assign it!")

        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(embed=embed, view=self)

async def autorole_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    guild = interaction.guild
    if not guild:
        return []

    choices = []
    current_lower = current.strip().lower()

    # Filter out divider roles, @everyone, and bot-managed roles
    valid_roles = []
    for r in guild.roles:
        if r.is_default() or r.managed:
            continue
        cleaned = re.sub(r"[\s\-_=~*#|─—–•·═]+", "", r.name)
        if len(cleaned) == 0:
            continue
        valid_roles.append(r)

    # Sort roles from highest position to lowest
    valid_roles.sort(key=lambda r: r.position, reverse=True)

    for r in valid_roles:
        if not current_lower or current_lower in r.name.lower():
            display_name = f"🏷️ {r.name}"[:100]
            choices.append(app_commands.Choice(name=display_name, value=str(r.id)))
            if len(choices) >= 25:
                break

    return choices

autorole_group = app_commands.Group(name="autorole", description="Automatically assign roles to joining users and bots")

@autorole_group.command(name="add", description="Set the default role automatically assigned to joining users")
@app_commands.describe(role="Choose a role from autocomplete, or leave empty to use interactive role selector")
@app_commands.autocomplete(role=autorole_autocomplete)
async def cmd_autorole_add(interaction: discord.Interaction, role: str = None):
    if not is_authorized_admin(interaction):
        await interaction.response.send_message("🔒 **Access Denied**: Bot administrators and owners only.", ephemeral=True)
        return

    guild = interaction.guild
    if not role:
        embed = discord.Embed(
            title="🏷️ Select Human Autorole",
            description=(
                "Choose the default role for new human members using the selector below.\n\n"
                "• 💡 *You can select any server role from the dropdown menu.*\n"
                "• ⚠️ *Make sure the bot's role is higher than the selected role in Server Settings > Roles.*"
            ),
            color=discord.Color.blue()
        )
        view = RoleSelectView(interaction.user.id, is_bot=False)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        return

    resolved_role = None
    cleaned = role.strip("<@&> ")
    if cleaned.isdigit():
        resolved_role = guild.get_role(int(cleaned))
    if not resolved_role:
        resolved_role = discord.utils.find(lambda r: r.name.lower() == role.lower(), guild.roles)

    if not resolved_role:
        await interaction.response.send_message(f"❌ Could not find role `{role}` in this server.", ephemeral=True)
        return

    cfg = get_autorole_config(guild.id)
    if cfg.get("human_role_id") == resolved_role.id:
        embed = discord.Embed(
            title="ℹ️ Autorole Status",
            description=f"⚠️ **Human Autorole is already TURNED ON for {resolved_role.mention}!**",
            color=discord.Color.gold()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    cfg["human_role_id"] = resolved_role.id
    set_autorole_config(guild.id, cfg)

    embed = discord.Embed(
        title="🏷️ Human Autorole Configured",
        description=f"✅ New human members will automatically receive {resolved_role.mention} upon joining.",
        color=discord.Color.green()
    )
    if guild.me.top_role.position <= resolved_role.position:
        embed.set_footer(text="⚠️ Notice: Move the bot's role higher than this role in Server Settings > Roles so it can assign it!")
    await interaction.response.send_message(embed=embed, ephemeral=True)

@autorole_group.command(name="status", description="Show the current autorole configuration")
async def cmd_autorole_status(interaction: discord.Interaction):
    if not is_authorized_admin(interaction):
        await interaction.response.send_message("🔒 **Access Denied**: Bot administrators and owners only.", ephemeral=True)
        return
    cfg = get_autorole_config(interaction.guild.id)
    h_role = interaction.guild.get_role(cfg.get("human_role_id")) if cfg.get("human_role_id") else None
    b_role = interaction.guild.get_role(cfg.get("bot_role_id")) if cfg.get("bot_role_id") else None
    embed = discord.Embed(
        title=f"🏷️ Autorole Configuration • {interaction.guild.name}",
        color=discord.Color.blue()
    )
    embed.add_field(name="👤 Human Member Autorole", value=h_role.mention if h_role else "*Not configured (Disabled)*", inline=True)
    embed.add_field(name="🤖 Bot Autorole", value=b_role.mention if b_role else "*Not configured (Disabled)*", inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@autorole_group.command(name="disable", description="Disable automatic role assignment for new human members")
async def cmd_autorole_disable(interaction: discord.Interaction):
    if not is_authorized_admin(interaction):
        await interaction.response.send_message("🔒 **Access Denied**: Bot administrators and owners only.", ephemeral=True)
        return
    cfg = get_autorole_config(interaction.guild.id)
    if not cfg.get("human_role_id"):
        await interaction.response.send_message("ℹ️ Human Autorole is already disabled.", ephemeral=True)
        return
    cfg["human_role_id"] = None
    set_autorole_config(interaction.guild.id, cfg)
    embed = discord.Embed(
        title="🏷️ Human Autorole Disabled",
        description="✅ Automatic role assignment for new human members has been turned off.",
        color=discord.Color.red()
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)

@autorole_group.command(name="botadd", description="Set the default role automatically assigned to joining bots")
@app_commands.describe(role="Choose a role from autocomplete, or leave empty to use interactive role selector")
@app_commands.autocomplete(role=autorole_autocomplete)
async def cmd_autorole_botadd(interaction: discord.Interaction, role: str = None):
    if not is_authorized_admin(interaction):
        await interaction.response.send_message("🔒 **Access Denied**: Bot administrators and owners only.", ephemeral=True)
        return

    guild = interaction.guild
    if not role:
        embed = discord.Embed(
            title="🤖 Select Bot Autorole",
            description=(
                "Choose the default role for newly invited bot accounts using the selector below.\n\n"
                "• 💡 *You can select any server role from the dropdown menu.*\n"
                "• ⚠️ *Make sure the bot's role is higher than the selected role in Server Settings > Roles.*"
            ),
            color=discord.Color.blue()
        )
        view = RoleSelectView(interaction.user.id, is_bot=True)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        return

    resolved_role = None
    cleaned = role.strip("<@&> ")
    if cleaned.isdigit():
        resolved_role = guild.get_role(int(cleaned))
    if not resolved_role:
        resolved_role = discord.utils.find(lambda r: r.name.lower() == role.lower(), guild.roles)

    if not resolved_role:
        await interaction.response.send_message(f"❌ Could not find role `{role}` in this server.", ephemeral=True)
        return

    cfg = get_autorole_config(guild.id)
    if cfg.get("bot_role_id") == resolved_role.id:
        embed = discord.Embed(
            title="ℹ️ Autorole Status",
            description=f"⚠️ **Bot Autorole is already TURNED ON for {resolved_role.mention}!**",
            color=discord.Color.gold()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    cfg["bot_role_id"] = resolved_role.id
    set_autorole_config(guild.id, cfg)

    embed = discord.Embed(
        title="🤖 Bot Autorole Configured",
        description=f"✅ New bot accounts will automatically receive {resolved_role.mention} upon joining.",
        color=discord.Color.green()
    )
    if guild.me.top_role.position <= resolved_role.position:
        embed.set_footer(text="⚠️ Notice: Move the bot's role higher than this role in Server Settings > Roles so it can assign it!")
    await interaction.response.send_message(embed=embed, ephemeral=True)

@autorole_group.command(name="botstatus", description="Show the current bot autorole status")
async def cmd_autorole_botstatus(interaction: discord.Interaction):
    if not is_authorized_admin(interaction):
        await interaction.response.send_message("🔒 **Access Denied**: Bot administrators and owners only.", ephemeral=True)
        return
    cfg = get_autorole_config(interaction.guild.id)
    b_role = interaction.guild.get_role(cfg.get("bot_role_id")) if cfg.get("bot_role_id") else None
    embed = discord.Embed(
        title=f"🤖 Bot Autorole Status • {interaction.guild.name}",
        description=f"Active Bot Role: {b_role.mention if b_role else '*Not configured (Disabled)*'}",
        color=discord.Color.blue()
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)

@autorole_group.command(name="botdisable", description="Disable automatic role assignment for new bot accounts")
async def cmd_autorole_botdisable(interaction: discord.Interaction):
    if not is_authorized_admin(interaction):
        await interaction.response.send_message("🔒 **Access Denied**: Bot administrators and owners only.", ephemeral=True)
        return
    cfg = get_autorole_config(interaction.guild.id)
    if not cfg.get("bot_role_id"):
        await interaction.response.send_message("ℹ️ Bot Autorole is already disabled.", ephemeral=True)
        return
    cfg["bot_role_id"] = None
    set_autorole_config(interaction.guild.id, cfg)
    embed = discord.Embed(
        title="🤖 Bot Autorole Disabled",
        description="✅ Automatic role assignment for new bot accounts has been turned off.",
        color=discord.Color.red()
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)

bot.tree.add_command(autorole_group)


# ==============================================================================
# 20. HIDE ALL & UNHIDE ALL (/hideall, /unhideall)
# ==============================================================================
@bot.tree.command(name="hideall", description="Hide all text and voice channels in the server from @everyone")
async def cmd_hideall(interaction: discord.Interaction):
    if not is_server_owner_or_extraowner(interaction.guild, interaction.user.id):
        await interaction.response.send_message("🔒 **Access Denied**: Only the Server Owner and Extra Owners can execute Hide All.", ephemeral=True)
        return
    guild = interaction.guild
    if guild.channels and all(ch.overwrites_for(guild.default_role).view_channel is False for ch in guild.channels):
        embed = discord.Embed(
            title="ℹ️ Hide Status",
            description="⚠️ **All channels are already HIDDEN (TURNED ON)!**",
            color=discord.Color.gold()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    count = 0
    for ch in guild.channels:
        try:
            await ch.set_permissions(guild.default_role, view_channel=False, reason=f"Hide All by {interaction.user}")
            count += 1
            await asyncio.sleep(0.04)
        except Exception:
            pass
    embed = discord.Embed(
        title="👁️‍🗨️ Hide All Complete",
        description=f"🔒 Successfully hid **{count}** channels across **{guild.name}** from `@everyone`.",
        color=discord.Color.dark_grey()
    )
    await interaction.followup.send(embed=embed, ephemeral=True)

@bot.tree.command(name="unhideall", description="Unhide all channels in the server so @everyone can view them")
async def cmd_unhideall(interaction: discord.Interaction):
    if not is_server_owner_or_extraowner(interaction.guild, interaction.user.id):
        await interaction.response.send_message("🔒 **Access Denied**: Only the Server Owner and Extra Owners can execute Unhide All.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    count = 0
    guild = interaction.guild
    for ch in guild.channels:
        try:
            await ch.set_permissions(guild.default_role, view_channel=None, reason=f"Unhide All by {interaction.user}")
            count += 1
            await asyncio.sleep(0.04)
        except Exception:
            pass
    embed = discord.Embed(
        title="👁️ Unhide All Complete",
        description=f"🔓 Successfully restored visibility for **{count}** channels across **{guild.name}**.",
        color=discord.Color.green()
    )
    await interaction.followup.send(embed=embed, ephemeral=True)


# ==============================================================================
# 21. LOCK ALL & UNLOCK ALL (/lockall, /unlockall)
# ==============================================================================
@bot.tree.command(name="lockall", description="Lock all voice channels in the server so members cannot join")
async def cmd_lockall(interaction: discord.Interaction):
    if not is_authorized_admin(interaction):
        await interaction.response.send_message("🔒 **Access Denied**: Administrators and owners only.", ephemeral=True)
        return
    guild = interaction.guild
    if guild.voice_channels and all(vc.overwrites_for(guild.default_role).connect is False for vc in guild.voice_channels):
        embed = discord.Embed(
            title="ℹ️ Lock Status",
            description="⚠️ **All voice channels are already LOCKED (TURNED ON)!**",
            color=discord.Color.gold()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    count = 0
    for vc in guild.voice_channels:
        try:
            await vc.set_permissions(guild.default_role, connect=False, reason=f"Lock All VCs by {interaction.user}")
            count += 1
            await asyncio.sleep(0.04)
        except Exception:
            pass
    embed = discord.Embed(
        title="🔒 Lock All Complete",
        description=f"🛑 Successfully locked **{count}** voice channels across **{guild.name}**.",
        color=discord.Color.red()
    )
    await interaction.followup.send(embed=embed, ephemeral=True)

@bot.tree.command(name="unlockall", description="Unlock all voice channels in the server so members can join")
async def cmd_unlockall(interaction: discord.Interaction):
    if not is_authorized_admin(interaction):
        await interaction.response.send_message("🔒 **Access Denied**: Administrators and owners only.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    guild = interaction.guild
    count = 0
    for vc in guild.voice_channels:
        try:
            await vc.set_permissions(guild.default_role, connect=None, reason=f"Unlock All VCs by {interaction.user}")
            count += 1
            await asyncio.sleep(0.04)
        except Exception:
            pass
    embed = discord.Embed(
        title="🔓 Unlock All Complete",
        description=f"✅ Successfully unlocked **{count}** voice channels across **{guild.name}**.",
        color=discord.Color.green()
    )
    await interaction.followup.send(embed=embed, ephemeral=True)


# ==============================================================================
# 22. SLOWMODE CONTROLS (/slowmode enable, /slowmode disable)
# ==============================================================================
slowmode_group = app_commands.Group(name="slowmode", description="Server channel slowmode controls")

@slowmode_group.command(name="enable", description="Enable slowmode in all text channels or a specific channel")
@app_commands.describe(
    seconds="Slowmode interval in seconds (1 to 21600)",
    channel="Channel to apply slowmode to (optional, defaults to all text channels)"
)
async def cmd_slowmode_enable(interaction: discord.Interaction, seconds: app_commands.Range[int, 1, 21600], channel: discord.TextChannel = None):
    if not is_authorized_admin(interaction):
        await interaction.response.send_message("🔒 **Access Denied**: Administrators and owners only.", ephemeral=True)
        return
    if channel:
        if channel.slowmode_delay == seconds:
            embed = discord.Embed(
                title="ℹ️ Slowmode Status",
                description=f"⚠️ **Slowmode ({seconds}s) is already TURNED ON in {channel.mention}!**",
                color=discord.Color.gold()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await channel.edit(slowmode_delay=seconds)
        embed = discord.Embed(
            title="⏱️ Slowmode Enabled",
            description=f"✅ Set slowmode to **{seconds}s** in {channel.mention}.",
            color=discord.Color.green()
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
    else:
        if interaction.guild.text_channels and all(ch.slowmode_delay == seconds for ch in interaction.guild.text_channels):
            embed = discord.Embed(
                title="ℹ️ Slowmode Status",
                description=f"⚠️ **Server-Wide Slowmode ({seconds}s) is already TURNED ON across all channels!**",
                color=discord.Color.gold()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        count = 0
        for ch in interaction.guild.text_channels:
            try:
                await ch.edit(slowmode_delay=seconds)
                count += 1
                await asyncio.sleep(0.04)
            except Exception:
                pass
        embed = discord.Embed(
            title="⏱️ Server-Wide Slowmode Enabled",
            description=f"✅ Set slowmode to **{seconds}s** across **{count}** text channels in **{interaction.guild.name}**.",
            color=discord.Color.green()
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

@slowmode_group.command(name="disable", description="Disable slowmode in all channels or a specific channel")
@app_commands.describe(channel="Channel to disable slowmode on (optional, defaults to all text channels)")
async def cmd_slowmode_disable(interaction: discord.Interaction, channel: discord.TextChannel = None):
    if not is_authorized_admin(interaction):
        await interaction.response.send_message("🔒 **Access Denied**: Administrators and owners only.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    if channel:
        await channel.edit(slowmode_delay=0)
        embed = discord.Embed(
            title="⏱️ Slowmode Disabled",
            description=f"✅ Disabled slowmode in {channel.mention}.",
            color=discord.Color.blue()
        )
    else:
        count = 0
        for ch in interaction.guild.text_channels:
            try:
                await ch.edit(slowmode_delay=0)
                count += 1
                await asyncio.sleep(0.04)
            except Exception:
                pass
        embed = discord.Embed(
            title="⏱️ Server-Wide Slowmode Disabled",
            description=f"✅ Disabled slowmode across **{count}** text channels in **{interaction.guild.name}**.",
            color=discord.Color.blue()
        )
    await interaction.followup.send(embed=embed, ephemeral=True)

bot.tree.add_command(slowmode_group)


# ==============================================================================
# 23. GREET / WELCOME SYSTEM (/greet setup, /greet channel, /greet test, /greet reset, /greet templates)
# ==============================================================================
greet_group = app_commands.Group(name="greet", description="Custom welcome card greeting system")

@greet_group.command(name="setup", description="Configure welcome cards for joining members with up to 3 channels")
@app_commands.describe(
    channel_1="Primary welcome channel",
    channel_2="Secondary welcome channel (optional)",
    channel_3="Tertiary welcome channel (optional)",
    banner_url="Image/banner URL to display on the welcome card (optional)",
    template="Card design template (1: Cyberpunk Neon, 2: Monarch Royal, 3: Aesthetic Luxe)",
    custom_message="Custom welcome greeting or rules announcement"
)
@app_commands.choices(template=[
    app_commands.Choice(name="⚡ Template 1: Cyberpunk Neon", value=1),
    app_commands.Choice(name="👑 Template 2: Monarch Royal Deluxe", value=2),
    app_commands.Choice(name="✨ Template 3: Aesthetic Modern Luxe", value=3)
])
async def cmd_greet_setup(
    interaction: discord.Interaction,
    channel_1: discord.TextChannel,
    channel_2: discord.TextChannel = None,
    channel_3: discord.TextChannel = None,
    banner_url: str = None,
    template: app_commands.Choice[int] = None,
    custom_message: str = None
):
    if not is_authorized_admin(interaction):
        await interaction.response.send_message("🔒 **Access Denied**: Administrators and owners only.", ephemeral=True)
        return

    c_ids = [channel_1.id]
    if channel_2:
        c_ids.append(channel_2.id)
    if channel_3:
        c_ids.append(channel_3.id)

    selected_tmpl = template.value if template else 1

    greet_cfg = {
        "enabled": True,
        "channel_ids": c_ids,
        "banner_url": banner_url.strip() if banner_url else None,
        "message": custom_message.strip() if custom_message else None,
        "template": selected_tmpl
    }
    set_greet_config(interaction.guild.id, greet_cfg)

    ch_mentions = ", ".join(f"<#{cid}>" for cid in c_ids)
    msg_display = f'> *"{custom_message}"*' if custom_message else "Default greeting"
    banner_display = "Custom URL provided" if banner_url else "Server default banner"
    embed = discord.Embed(
        title="🎉 Welcome / Greet System Configured",
        description=(
            f"✅ **Welcome cards are now active!**\n\n"
            f"• **Welcome Channels ({len(c_ids)})**: {ch_mentions}\n"
            f"• **Selected Template**: Template {selected_tmpl}\n"
            f"• **Banner**: {banner_display}\n"
            f"• **Custom Message**: {msg_display}"
        ),
        color=discord.Color.green()
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)

@greet_group.command(name="channel", description="Set or change the primary welcome channel")
@app_commands.describe(channel="The channel where welcome cards will be sent")
async def cmd_greet_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    if not is_authorized_admin(interaction):
        await interaction.response.send_message("🔒 **Access Denied**: Administrators and owners only.", ephemeral=True)
        return
    cfg = get_greet_config(interaction.guild.id)
    if cfg.get("enabled") and cfg.get("channel_ids") == [channel.id]:
        embed = discord.Embed(
            title="ℹ️ Welcome Status",
            description=f"⚠️ **Welcome cards are already TURNED ON in {channel.mention}!**",
            color=discord.Color.gold()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    cfg["enabled"] = True
    cfg["channel_ids"] = [channel.id]
    set_greet_config(interaction.guild.id, cfg)
    embed = discord.Embed(
        title="🎉 Welcome Channel Set",
        description=f"✅ Welcome cards will be posted into {channel.mention}.",
        color=discord.Color.green()
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)

@greet_group.command(name="test", description="Send a live test preview of the configured welcome card")
@app_commands.describe(channel="Channel to post the test in (optional, defaults to current channel)")
async def cmd_greet_test(interaction: discord.Interaction, channel: discord.TextChannel = None):
    if not is_authorized_admin(interaction):
        await interaction.response.send_message("🔒 **Access Denied**: Administrators and owners only.", ephemeral=True)
        return
    cfg = get_greet_config(interaction.guild.id)
    target_channel = channel or interaction.channel
    content, embed = build_welcome_card(
        interaction.user,
        template_id=cfg.get("template", 1),
        custom_msg=cfg.get("message"),
        banner_url=cfg.get("banner_url")
    )
    await target_channel.send(content=f"🧪 **[Test Preview]** {content}", embed=embed)
    await interaction.response.send_message(f"✅ Test welcome card sent to {target_channel.mention}!", ephemeral=True)

@greet_group.command(name="reset", description="Reset and disable the welcome card system")
async def cmd_greet_reset(interaction: discord.Interaction):
    if not is_authorized_admin(interaction):
        await interaction.response.send_message("🔒 **Access Denied**: Administrators and owners only.", ephemeral=True)
        return
    set_greet_config(interaction.guild.id, {"enabled": False, "channel_ids": [], "banner_url": None, "message": None, "template": 1})
    embed = discord.Embed(
        title="🔄 Welcome System Reset",
        description="Welcome greetings have been disabled and configuration cleared.",
        color=discord.Color.blue()
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)

@greet_group.command(name="templates", description="View previews of all 3 available welcome card templates")
async def cmd_greet_templates(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    t1_content, t1_embed = build_welcome_card(interaction.user, template_id=1, custom_msg="Suit up and enjoy your stay!")
    t2_content, t2_embed = build_welcome_card(interaction.user, template_id=2, custom_msg="Honored to welcome you to the imperial court.")
    t3_content, t3_embed = build_welcome_card(interaction.user, template_id=3, custom_msg="Make yourself comfortable and have fun.")

    overview_embed = discord.Embed(
        title="🎨 Available Welcome Card Templates",
        description=(
            "Here are the **3 Extraordinary Welcome Templates** available for `/greet setup`:\n\n"
            "• **Template 1: ⚡ Cyberpunk Neon** — High-tech electric cyan aesthetic, account age & ID badge.\n"
            "• **Template 2: 👑 Monarch Royal Deluxe** — Imperial gold styling, Founder spotlight & kingdom decree.\n"
            "• **Template 3: ✨ Aesthetic Modern Luxe** — Minimalist Blurple/Velvet elegance & join stats.\n\n"
            "👇 *See the samples below:*"
        ),
        color=discord.Color.gold()
    )
    await interaction.followup.send(embed=overview_embed, ephemeral=True)
    await interaction.followup.send(content="**Template 1: Cyberpunk Neon**", embed=t1_embed, ephemeral=True)
    await interaction.followup.send(content="**Template 2: Monarch Royal Deluxe**", embed=t2_embed, ephemeral=True)
    await interaction.followup.send(content="**Template 3: Aesthetic Modern Luxe**", embed=t3_embed, ephemeral=True)

bot.tree.add_command(greet_group)


# ==============================================================================
# LEVELING SYSTEM ENGINE (Cute notifications, Member-only XP, /rank, /leaderboard)
# ==============================================================================
async def handle_message_leveling(message: discord.Message):
    if not message.guild or message.author.bot:
        return
    if is_exempt_from_leveling(message.author):
        return

    lvl_cfg = get_leveling_config(message.guild.id)
    if not lvl_cfg.get("enabled", True):
        return

    now = time.time()
    last_time = user_xp_cooldowns[message.guild.id].get(message.author.id, 0)
    # 45-second cooldown to prevent spamming
    if now - last_time < 45.0:
        return
    user_xp_cooldowns[message.guild.id][message.author.id] = now

    users = lvl_cfg.get("users", {})
    u_key = str(message.author.id)
    u_data = users.get(u_key, {"xp": 0, "level": 0, "total_xp": 0})

    current_level = u_data.get("level", 0)
    current_xp = u_data.get("xp", 0)
    total_xp = u_data.get("total_xp", 0)

    earned = random.randint(15, 25)
    current_xp += earned
    total_xp += earned

    needed = xp_needed_for_level(current_level)
    leveled_up = False

    while current_xp >= needed:
        current_xp -= needed
        current_level += 1
        needed = xp_needed_for_level(current_level)
        leveled_up = True

    u_data["xp"] = current_xp
    u_data["level"] = current_level
    u_data["total_xp"] = total_xp
    users[u_key] = u_data
    lvl_cfg["users"] = users
    set_leveling_config(message.guild.id, lvl_cfg)

    if leveled_up:
        cute_phrases = [
            "🌸 *Level up! You're shining brighter than ever!* ✨",
            "🧁 *Yaaay! Look at you go, sweetest chatter!* 💖",
            "🎀 *Ding ding! Super cute milestone unlocked!* 🌷",
            "🍡 *Squishy sweet progress! Keep glowing bright!* 🌈",
            "🍓 *Berry amazing job! You leveled up!* 🍰",
            "⭐ *Sparkle sparkle! You're leveling up so fast!* 🍬"
        ]
        phrase = random.choice(cute_phrases)

        # Very cute, small, catchy embed that only mentions the particular user
        embed = discord.Embed(
            description=f"✨ **Yay {message.author.mention}!** You just reached **Level {current_level}**! 🌸\n{phrase}",
            color=discord.Color.from_rgb(255, 182, 193)
        )
        if message.author.display_avatar:
            embed.set_thumbnail(url=message.author.display_avatar.url)
        embed.set_footer(text=f"Total XP: {total_xp:,} 💖")

        target_ch = message.channel
        channel_id = lvl_cfg.get("channel_id")
        if channel_id:
            dest_ch = message.guild.get_channel(channel_id)
            if dest_ch and isinstance(dest_ch, discord.TextChannel):
                target_ch = dest_ch

        try:
            await target_ch.send(content=message.author.mention, embed=embed)
        except Exception as e:
            print(f"[Leveling Send Warning] {e}", flush=True)


@bot.tree.command(name="rank", description="Display your or another member's cute rank, level, and XP status")
@app_commands.describe(member="Member to view (defaults to yourself)")
async def cmd_rank(interaction: discord.Interaction, member: discord.Member = None):
    target = member or interaction.user
    guild = interaction.guild
    if not guild:
        await interaction.response.send_message("❌ This command must be used in a server.", ephemeral=True)
        return

    # If member is an owner/extraowner/admin who is exempt from leveling
    if is_exempt_from_leveling(target):
        embed = discord.Embed(
            title="👑 Sovereign Guardian 👑",
            description=f"✨ {target.mention} is an **Imperial Server Leader / Guardian** and reigns supreme above regular levels! 💖👑",
            color=discord.Color.gold()
        )
        if target.display_avatar:
            embed.set_thumbnail(url=target.display_avatar.url)
        await interaction.response.send_message(embed=embed)
        return

    lvl_cfg = get_leveling_config(guild.id)
    users = lvl_cfg.get("users", {})
    u_key = str(target.id)
    u_data = users.get(u_key, {"xp": 0, "level": 0, "total_xp": 0})

    level = u_data.get("level", 0)
    current_xp = u_data.get("xp", 0)
    total_xp = u_data.get("total_xp", 0)
    needed = xp_needed_for_level(level)

    percent = min(100, int((current_xp / max(1, needed)) * 100))
    filled = int(percent / 10)
    bar = "▰" * filled + "▱" * (10 - filled)

    sorted_users = sorted(users.items(), key=lambda x: x[1].get("total_xp", 0), reverse=True)
    rank_pos = 1
    for i, (uid, _) in enumerate(sorted_users, 1):
        if uid == u_key:
            rank_pos = i
            break
    else:
        rank_pos = len(sorted_users) + 1

    embed = discord.Embed(
        title="🌸 Cute Level Status 🌸",
        description=(
            f"🎀 **Member:** {target.mention}\n"
            f"👑 **Server Rank:** `#{rank_pos}` of `{max(1, len(sorted_users))}`\n"
            f"✨ **Level:** `{level}`\n"
            f"💖 **XP:** `{current_xp} / {needed}` (`{percent}%`)\n"
            f"`{bar}`\n"
            f"⭐ **Total XP:** `{total_xp:,}`"
        ),
        color=discord.Color.from_rgb(255, 182, 193)
    )
    if target.display_avatar:
        embed.set_thumbnail(url=target.display_avatar.url)
    embed.set_footer(text="Keep chatting cutie to level up! 🧁✨")
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="leaderboard", description="Display the top ranked chatters in this server")
async def cmd_leaderboard(interaction: discord.Interaction):
    guild = interaction.guild
    if not guild:
        await interaction.response.send_message("❌ This command must be used in a server.", ephemeral=True)
        return

    lvl_cfg = get_leveling_config(guild.id)
    users = lvl_cfg.get("users", {})
    if not users:
        await interaction.response.send_message("🧁 No members have earned XP yet. Start chatting to climb the ranks! ✨", ephemeral=True)
        return

    sorted_users = sorted(users.items(), key=lambda x: x[1].get("total_xp", 0), reverse=True)[:10]

    lines = []
    medals = ["🥇", "🥈", "🥉", "🌸", "🌸", "🌸", "🌸", "🌸", "🌸", "🌸"]
    for i, (uid_str, data) in enumerate(sorted_users):
        medal = medals[i] if i < len(medals) else "🌸"
        lvl = data.get("level", 0)
        txp = data.get("total_xp", 0)
        lines.append(f"{medal} **#{i+1}** <@{uid_str}> • **Level {lvl}** (`{txp:,}` XP)")

    embed = discord.Embed(
        title="🏆 Server Chat Leaderboard 🏆",
        description="\n".join(lines),
        color=discord.Color.from_rgb(255, 182, 193)
    )
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    embed.set_footer(text="Only regular members gain levels • Admins & Owners reign supreme! 👑")
    await interaction.response.send_message(embed=embed)


leveling_group = app_commands.Group(name="leveling", description="Server leveling system management")

@leveling_group.command(name="setup", description="Configure where cute level up announcements are sent")
@app_commands.describe(channel="Text channel for level up cards (leave empty to send in current chat)")
async def cmd_leveling_setup(interaction: discord.Interaction, channel: discord.TextChannel = None):
    if not is_server_owner_or_extraowner(interaction.guild, interaction.user.id):
        await interaction.response.send_message("🔒 **Access Denied**: Only the Server Owner, Extra Owners, or Supreme Bot Owner can configure leveling.", ephemeral=True)
        return

    lvl_cfg = get_leveling_config(interaction.guild.id)
    lvl_cfg["channel_id"] = channel.id if channel else None
    set_leveling_config(interaction.guild.id, lvl_cfg)

    dest = channel.mention if channel else "the chat where the member leveled up"
    await interaction.response.send_message(f"✅ Level up announcement channel updated! Cute level cards will be sent to **{dest}**.")

@leveling_group.command(name="toggle", description="Enable or disable the leveling system in this server")
@app_commands.describe(enabled="True to enable leveling, False to disable")
async def cmd_leveling_toggle(interaction: discord.Interaction, enabled: bool):
    if not is_server_owner_or_extraowner(interaction.guild, interaction.user.id):
        await interaction.response.send_message("🔒 **Access Denied**: Only the Server Owner, Extra Owners, or Supreme Bot Owner can configure leveling.", ephemeral=True)
        return

    lvl_cfg = get_leveling_config(interaction.guild.id)
    lvl_cfg["enabled"] = enabled
    set_leveling_config(interaction.guild.id, lvl_cfg)

    status = "enabled 🌸" if enabled else "disabled ⏸️"
    await interaction.response.send_message(f"✅ Leveling system is now **{status}** in **{interaction.guild.name}**.")


# ==============================================================================
# VOICE CHANNEL LEVELING ENGINE (Awards XP to active voice members)
# ==============================================================================
@tasks.loop(seconds=60)
async def voice_xp_loop():
    """Awards XP every minute to eligible regular server members who are active in voice channels."""
    for guild in bot.guilds:
        try:
            lvl_cfg = get_leveling_config(guild.id)
            if not lvl_cfg.get("enabled", True):
                continue

            users = lvl_cfg.get("users", {})
            dirty = False

            for vc in guild.voice_channels:
                # Skip AFK voice channels
                if guild.afk_channel and vc.id == guild.afk_channel.id:
                    continue

                for member in vc.members:
                    # Regular members only: skip bots, server owner, extraowners, admins
                    if is_exempt_from_leveling(member):
                        continue

                    # Must not be self-deafened or server-deafened (must be actively listening/participating)
                    if member.voice and (member.voice.self_deaf or member.voice.deaf):
                        continue

                    u_key = str(member.id)
                    u_data = users.get(u_key, {"xp": 0, "level": 0, "total_xp": 0})

                    current_level = u_data.get("level", 0)
                    current_xp = u_data.get("xp", 0)
                    total_xp = u_data.get("total_xp", 0)

                    # Award 10-15 XP per minute in VC
                    earned = random.randint(10, 15)
                    current_xp += earned
                    total_xp += earned

                    needed = xp_needed_for_level(current_level)
                    leveled_up = False

                    while current_xp >= needed:
                        current_xp -= needed
                        current_level += 1
                        needed = xp_needed_for_level(current_level)
                        leveled_up = True

                    u_data["xp"] = current_xp
                    u_data["level"] = current_level
                    u_data["total_xp"] = total_xp
                    users[u_key] = u_data
                    dirty = True

                    if leveled_up:
                        cute_phrases = [
                            "🌸 *Level up! Your voice is making the kingdom shine!* ✨",
                            "🧁 *Yaaay! Chilling in VC leveled you up, sweetest chatter!* 💖",
                            "🎀 *Ding ding! Super cute Voice milestone unlocked!* 🌷",
                            "🍡 *Squishy sweet VC progress! Keep glowing bright!* 🌈",
                            "🍓 *Berry amazing job! Voice level up achieved!* 🍰",
                            "⭐ *Sparkle sparkle! You're leveling up so fast in VC!* 🍬"
                        ]
                        phrase = random.choice(cute_phrases)

                        embed = discord.Embed(
                            description=f"✨ **Yay {member.mention}!** You just reached **Level {current_level}** in Voice! 🌸🎙️\n{phrase}",
                            color=discord.Color.from_rgb(255, 182, 193)
                        )
                        if member.display_avatar:
                            embed.set_thumbnail(url=member.display_avatar.url)
                        embed.set_footer(text=f"Total XP: {total_xp:,} 💖 • Voice Activity")

                        # Send to configured leveling channel or active text channel
                        target_ch = None
                        channel_id = lvl_cfg.get("channel_id")
                        if channel_id:
                            ch = guild.get_channel(channel_id)
                            if ch and isinstance(ch, discord.TextChannel):
                                target_ch = ch

                        if not target_ch:
                            target_ch = guild.system_channel
                            if not target_ch or not target_ch.permissions_for(guild.me).send_messages:
                                for tc in guild.text_channels:
                                    if tc.permissions_for(guild.me).send_messages:
                                        target_ch = tc
                                        break

                        if target_ch:
                            try:
                                await target_ch.send(content=member.mention, embed=embed)
                            except Exception as e:
                                print(f"[VC Leveling Send Warning] {e}", flush=True)

            if dirty:
                lvl_cfg["users"] = users
                set_leveling_config(guild.id, lvl_cfg)
        except Exception as g_err:
            print(f"[VC XP Loop Error] {g_err}", flush=True)

@voice_xp_loop.before_loop
async def before_voice_xp_loop():
    await bot.wait_until_ready()


# ==============================================================================
# BIRTHDAY SYSTEM (/birthday set, /birthday setup, /birthday view, /birthday upcoming)
# ==============================================================================
birthday_group = app_commands.Group(name="birthday", description="Birthday celebration and automated wishes system")

MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
DAYS_IN_MONTH = [31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]

@birthday_group.command(name="setup", description="Configure birthday announcement and input channels")
@app_commands.describe(
    announcement_channel="The channel where birthday celebrations and wishes will be posted",
    input_channel="Optional: restrict /birthday set commands to this specific channel"
)
async def cmd_birthday_setup(
    interaction: discord.Interaction,
    announcement_channel: discord.TextChannel,
    input_channel: discord.TextChannel = None
):
    if not is_server_owner_or_extraowner(interaction.guild, interaction.user.id):
        await interaction.response.send_message(
            "🔒 **Access Denied**: Only the Server Owner, Extra Owners, or Supreme Bot Owner can configure the Birthday System channels.",
            ephemeral=True
        )
        return

    bday_cfg = get_birthday_config(interaction.guild.id)
    bday_cfg["enabled"] = True
    bday_cfg["channel_id"] = announcement_channel.id
    bday_cfg["input_channel_id"] = input_channel.id if input_channel else None
    set_birthday_config(interaction.guild.id, bday_cfg)

    desc = f"🎉 **Birthday Announcement Channel:** {announcement_channel.mention}\n"
    if input_channel:
        desc += f"📝 **Birthday Input Channel:** {input_channel.mention} *(Members must register birthdays here)*\n"
    else:
        desc += "📝 **Birthday Input Channel:** *Any server channel*\n"

    embed = discord.Embed(
        title="🎂 Birthday System Configured",
        description=desc + "\nMembers can now register their special day using `/birthday set`!",
        color=discord.Color.from_rgb(255, 105, 180)
    )
    await interaction.response.send_message(embed=embed)

@birthday_group.command(name="set", description="Register your birthday so the server can celebrate your special day!")
@app_commands.describe(
    month="Your birth month (1-12)",
    day="Your birth day (1-31)",
    year="Optional: birth year (1920-2026)"
)
async def cmd_birthday_set(
    interaction: discord.Interaction,
    month: app_commands.Range[int, 1, 12],
    day: app_commands.Range[int, 1, 31],
    year: app_commands.Range[int, 1920, 2026] = None
):
    guild = interaction.guild
    if not guild:
        await interaction.response.send_message("❌ This command must be used in a server.", ephemeral=True)
        return

    bday_cfg = get_birthday_config(guild.id)
    input_ch_id = bday_cfg.get("input_channel_id")
    if input_ch_id and interaction.channel_id != input_ch_id:
        if not is_server_owner_or_extraowner(guild, interaction.user.id):
            await interaction.response.send_message(
                f"⚠️ Please register your birthday in the designated channel: <#{input_ch_id}>",
                ephemeral=True
            )
            return

    max_days = DAYS_IN_MONTH[month - 1]
    if day > max_days:
        await interaction.response.send_message(
            f"❌ Invalid date: **{MONTH_NAMES[month - 1]}** only has up to **{max_days}** days.",
            ephemeral=True
        )
        return

    users = bday_cfg.get("users", {})
    users[str(interaction.user.id)] = {
        "month": month,
        "day": day,
        "year": year
    }
    bday_cfg["users"] = users
    set_birthday_config(guild.id, bday_cfg)

    m_name = MONTH_NAMES[month - 1]
    year_str = f", {year}" if year else ""
    embed = discord.Embed(
        title="🎂 Birthday Saved! 🎈",
        description=f"✨ **{interaction.user.mention}**, your birthday has been saved as **{m_name} {day}{year_str}**!\nWe can't wait to celebrate with you on your special day! 🥳🍰",
        color=discord.Color.from_rgb(255, 105, 180)
    )
    if interaction.user.display_avatar:
        embed.set_thumbnail(url=interaction.user.display_avatar.url)
    await interaction.response.send_message(embed=embed)

@birthday_group.command(name="view", description="Check your or another member's registered birthday")
@app_commands.describe(member="The member whose birthday you want to check (defaults to yourself)")
async def cmd_birthday_view(interaction: discord.Interaction, member: discord.Member = None):
    target = member or interaction.user
    guild = interaction.guild
    if not guild:
        await interaction.response.send_message("❌ This command must be used in a server.", ephemeral=True)
        return

    bday_cfg = get_birthday_config(guild.id)
    users = bday_cfg.get("users", {})
    u_data = users.get(str(target.id))
    if not u_data:
        msg = f"{target.mention} hasn't registered their birthday yet! Use `/birthday set` to save it." if target != interaction.user else "You haven't registered your birthday yet! Use `/birthday set` to register."
        await interaction.response.send_message(f"🍰 {msg}", ephemeral=True)
        return

    month = u_data.get("month")
    day = u_data.get("day")
    year = u_data.get("year")
    m_name = MONTH_NAMES[month - 1]

    now_utc = datetime.datetime.now(datetime.timezone.utc)
    this_year_bday = datetime.datetime(now_utc.year, month, day, tzinfo=datetime.timezone.utc)
    if this_year_bday.date() < now_utc.date():
        next_bday = datetime.datetime(now_utc.year + 1, month, day, tzinfo=datetime.timezone.utc)
    else:
        next_bday = this_year_bday

    days_left = (next_bday.date() - now_utc.date()).days
    if days_left == 0:
        countdown_str = "🎉 **IT IS TODAY! HAPPY BIRTHDAY!** 🎂🥳"
    elif days_left == 1:
        countdown_str = "⏳ **Tomorrow is the big day!** 🎈"
    else:
        countdown_str = f"⏳ **{days_left}** days remaining until their special day!"

    embed = discord.Embed(
        title=f"🎂 {target.display_name}'s Birthday",
        description=f"🎈 **Date:** **{m_name} {day}**{f' ({year})' if year else ''}\n{countdown_str}",
        color=discord.Color.from_rgb(255, 105, 180)
    )
    if target.display_avatar:
        embed.set_thumbnail(url=target.display_avatar.url)
    await interaction.response.send_message(embed=embed)

@birthday_group.command(name="upcoming", description="View upcoming birthdays in this server")
async def cmd_birthday_upcoming(interaction: discord.Interaction):
    guild = interaction.guild
    if not guild:
        await interaction.response.send_message("❌ This command must be used in a server.", ephemeral=True)
        return

    bday_cfg = get_birthday_config(guild.id)
    users = bday_cfg.get("users", {})
    if not users:
        await interaction.response.send_message("🎂 No birthdays registered in this server yet! Use `/birthday set` to add yours.", ephemeral=True)
        return

    now_utc = datetime.datetime.now(datetime.timezone.utc)
    upcoming_list = []

    for uid_str, data in users.items():
        month = data.get("month")
        day = data.get("day")
        if not month or not day:
            continue
        try:
            this_bday = datetime.datetime(now_utc.year, month, day, tzinfo=datetime.timezone.utc)
            if this_bday.date() < now_utc.date():
                next_bday = datetime.datetime(now_utc.year + 1, month, day, tzinfo=datetime.timezone.utc)
            else:
                next_bday = this_bday
            days_diff = (next_bday.date() - now_utc.date()).days
            upcoming_list.append((days_diff, uid_str, month, day))
        except Exception:
            continue

    upcoming_list.sort(key=lambda x: x[0])
    lines = []
    for days_diff, uid_str, m, d in upcoming_list[:10]:
        m_name = MONTH_NAMES[m - 1]
        if days_diff == 0:
            when = "🎉 **TODAY!**"
        elif days_diff == 1:
            when = "Tomorrow"
        else:
            when = f"in {days_diff} days"
        lines.append(f"• <@{uid_str}> — **{m_name} {d}** ({when})")

    embed = discord.Embed(
        title="🎈 Upcoming Server Birthdays 🎂",
        description="\n".join(lines) if lines else "No upcoming birthdays found.",
        color=discord.Color.from_rgb(255, 105, 180)
    )
    await interaction.response.send_message(embed=embed)

@birthday_group.command(name="remove", description="Remove your registered birthday from this server")
async def cmd_birthday_remove(interaction: discord.Interaction):
    bday_cfg = get_birthday_config(interaction.guild.id)
    users = bday_cfg.get("users", {})
    u_key = str(interaction.user.id)
    if u_key in users:
        del users[u_key]
        bday_cfg["users"] = users
        set_birthday_config(interaction.guild.id, bday_cfg)
        await interaction.response.send_message("🗑️ Your birthday has been removed from the server registry.", ephemeral=True)
    else:
        await interaction.response.send_message("ℹ️ You don't have a registered birthday.", ephemeral=True)

@birthday_group.command(name="disable", description="Disable birthday announcements in this server")
async def cmd_birthday_disable(interaction: discord.Interaction):
    if not is_server_owner_or_extraowner(interaction.guild, interaction.user.id):
        await interaction.response.send_message("🔒 **Access Denied**: Only the Server Owner, Extra Owners, or Supreme Bot Owner can disable birthday announcements.", ephemeral=True)
        return

    bday_cfg = get_birthday_config(interaction.guild.id)
    bday_cfg["enabled"] = False
    set_birthday_config(interaction.guild.id, bday_cfg)
    await interaction.response.send_message("🛑 Birthday celebration announcements have been **disabled**.")

@birthday_group.command(name="channel", description="View the current birthday channels configuration")
async def cmd_birthday_channel(interaction: discord.Interaction):
    bday_cfg = get_birthday_config(interaction.guild.id)
    out_id = bday_cfg.get("channel_id")
    in_id = bday_cfg.get("input_channel_id")
    out_str = f"<#{out_id}>" if out_id else "*Not configured*"
    in_str = f"<#{in_id}>" if in_id else "*Any channel*"
    status_str = "Enabled ✅" if bday_cfg.get("enabled", True) else "Disabled ❌"

    embed = discord.Embed(
        title="🎂 Birthday Channel Configuration",
        description=(
            f"• **Status:** {status_str}\n"
            f"• **Announcement Channel:** {out_str}\n"
            f"• **Input Channel:** {in_str}\n"
            f"• **Registered Members:** `{len(bday_cfg.get('users', {}))}`"
        ),
        color=discord.Color.from_rgb(255, 105, 180)
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)

def build_hellfire_birthday_card(member: discord.Member) -> discord.Embed:
    embed = discord.Embed(
        title="🔱 🔥 𝐇𝐄𝐋𝐋𝐅𝐈𝐑𝐄 𝐁𝐈𝐑𝐓𝐇𝐃𝐀𝐘 𝐃𝐄𝐂𝐑𝐄𝐄 🔥 🔱",
        description=(
            f"👑 ═══════════════════════════════════ 👑\n"
            f"🩸 **THE GATES OF HELLFIRE OPEN IN TRIBUTE!** 🩸\n\n"
            f"🕯️ *On this sacred day, the sovereign inferno blazes in honor of our esteemed kin* {member.mention}!\n\n"
            f"⚔️ **SACRED HELLFIRE BLESSINGS BESTOWED:**\n"
            f"🔥 **Flames of Invincibility:** May your spirit remain unyielding against every adversary and dark obstacle.\n"
            f"⚜️ **Crown of Sovereign Glory:** May supreme honor, boundless prosperity, and victory accompany your reign.\n"
            f"🍷 **The Crimson Elixir:** May vitality, enduring strength, health, and dominion bless your path in the kingdom.\n\n"
            f"👹 *The throne acknowledges your greatness. Rise, champion of Hellfire, and conquer the year ahead!* 🥂🕯️\n"
            f"👑 ═══════════════════════════════════ 👑"
        ),
        color=discord.Color.from_rgb(178, 34, 34)  # Deep Hellfire Crimson Red
    )
    if member.display_avatar:
        embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_image(url="https://cdn.discordapp.com/banners/1437070336335282258/a_9ee491ae60616f9ff0007b5382f210ca.gif?size=1024")
    embed.set_footer(text="🩸 𝐇𝐄𝐋𝐋𝐅𝐈𝐑𝐄 𝐂𝐋𝐔𝐁 🩸 • Official Royal Birthday Decree & Sacred Blessings")
    return embed

@birthday_group.command(name="test", description="Send a test preview of the Hellfire birthday card with sacred blessings")
@app_commands.describe(member="Member to preview for (defaults to yourself)", channel="Channel to test in (defaults to current)")
async def cmd_birthday_test(interaction: discord.Interaction, member: discord.Member = None, channel: discord.TextChannel = None):
    if not is_server_owner_or_extraowner(interaction.guild, interaction.user.id):
        await interaction.response.send_message("🔒 **Access Denied**: Only the Server Owner, Extra Owners, or Supreme Bot Owner can send test previews.", ephemeral=True)
        return

    target = member or interaction.user
    dest_ch = channel or interaction.channel
    embed = build_hellfire_birthday_card(target)
    await dest_ch.send(content=f"🎉 🔥 **Hail the Birthday Champion!** {target.mention} *(Test Preview)* 🕯️🥂", embed=embed)
    await interaction.response.send_message(f"✅ Sent Hellfire Birthday card preview to {dest_ch.mention}!", ephemeral=True)

@bot.tree.command(name="setbirthday", description="Register your birthday date for the server to celebrate your special day!")
@app_commands.describe(
    month="Your birth month (1-12)",
    day="Your birth day (1-31)",
    year="Optional: birth year (1920-2026)"
)
async def cmd_setbirthday_toplevel(
    interaction: discord.Interaction,
    month: app_commands.Range[int, 1, 12],
    day: app_commands.Range[int, 1, 31],
    year: app_commands.Range[int, 1920, 2026] = None
):
    await cmd_birthday_set(interaction, month, day, year)

@tasks.loop(minutes=15)
async def birthday_check_loop():
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    cur_year = now_utc.year
    cur_month = now_utc.month
    cur_day = now_utc.day

    config = load_server_config()
    for g_id_str, g_data in list(config.items()):
        bday_cfg = g_data.get("birthdays")
        if not bday_cfg or not bday_cfg.get("enabled", True):
            continue

        ch_id = bday_cfg.get("channel_id")
        if not ch_id:
            continue

        try:
            guild = bot.get_guild(int(g_id_str))
        except Exception:
            continue
        if not guild:
            continue

        channel = guild.get_channel(ch_id)
        if not channel or not isinstance(channel, discord.TextChannel):
            continue

        users = bday_cfg.get("users", {})
        last_wished = bday_cfg.get("last_wished", {})
        dirty = False

        for uid_str, data in list(users.items()):
            if data.get("month") == cur_month and data.get("day") == cur_day:
                if last_wished.get(uid_str) != cur_year:
                    try:
                        member = guild.get_member(int(uid_str)) or await guild.fetch_member(int(uid_str))
                    except Exception:
                        member = None

                    if member:
                        embed = build_hellfire_birthday_card(member)
                        try:
                            await channel.send(content=f"🎉 🔥 **Hail the Birthday Champion!** {member.mention} 🕯️🥂", embed=embed)
                            last_wished[uid_str] = cur_year
                            dirty = True
                        except Exception as send_err:
                            print(f"[Birthday Send Error] {send_err}", flush=True)

        if dirty:
            bday_cfg["last_wished"] = last_wished
            config[g_id_str]["birthdays"] = bday_cfg
            save_server_config(config)

@birthday_check_loop.before_loop
async def before_birthday_check_loop():
    await bot.wait_until_ready()


# ==============================================================================
# REACTION ROLES SYSTEM (/reactionrole create, /reactionrole add, /reactionrole list, /reactionrole remove)
# ==============================================================================
reactionrole_group = app_commands.Group(name="reactionrole", description="Self-assignable reaction roles system")

@reactionrole_group.command(name="create", description="Create a reaction role message in a channel with up to 6 roles")
@app_commands.describe(
    channel="The channel where the reaction role message will be posted",
    title="Title of the reaction role panel",
    description="Instructions or explanation of the roles",
    emoji1="First emoji",
    role1="First role to assign",
    emoji2="Second emoji (optional)",
    role2="Second role (optional)",
    emoji3="Third emoji (optional)",
    role3="Third role (optional)",
    emoji4="Fourth emoji (optional)",
    role4="Fourth role (optional)",
    emoji5="Fifth emoji (optional)",
    role5="Fifth role (optional)",
    emoji6="Sixth emoji (optional)",
    role6="Sixth role (optional)"
)
async def cmd_reactionrole_create(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
    title: str,
    description: str,
    emoji1: str,
    role1: discord.Role,
    emoji2: str = None,
    role2: discord.Role = None,
    emoji3: str = None,
    role3: discord.Role = None,
    emoji4: str = None,
    role4: discord.Role = None,
    emoji5: str = None,
    role5: discord.Role = None,
    emoji6: str = None,
    role6: discord.Role = None
):
    await interaction.response.defer(ephemeral=True)

    if not is_server_owner_or_extraowner(interaction.guild, interaction.user.id):
        await interaction.followup.send(
            "🔒 **Access Denied**: Only the Server Owner, Extra Owners, or Supreme Bot Owner can set up reaction roles.",
            ephemeral=True
        )
        return

    bot_member = interaction.guild.get_member(bot.user.id)
    if not interaction.guild.me.guild_permissions.manage_roles:
        await interaction.followup.send("❌ I need the **Manage Roles** permission to assign reaction roles.", ephemeral=True)
        return

    pairs = [(emoji1, role1)]
    if emoji2 and role2: pairs.append((emoji2, role2))
    if emoji3 and role3: pairs.append((emoji3, role3))
    if emoji4 and role4: pairs.append((emoji4, role4))
    if emoji5 and role5: pairs.append((emoji5, role5))
    if emoji6 and role6: pairs.append((emoji6, role6))

    for em, ro in pairs:
        if bot_member and ro >= bot_member.top_role:
            await interaction.followup.send(
                f"❌ Hierarchy error: My bot role ({bot_member.top_role.name}) must be placed above **{ro.name}** in Server Settings -> Roles.",
                ephemeral=True
            )
            return

    role_lines = []
    roles_map = {}
    for em, ro in pairs:
        clean_em = em.strip()
        role_lines.append(f"• {clean_em} ➔ {ro.mention}")
        roles_map[clean_em] = ro.id

    embed = discord.Embed(
        title=f"🎭 {title}",
        description=f"{description}\n\n**React below to claim your roles:**\n" + "\n".join(role_lines),
        color=discord.Color.purple()
    )
    embed.set_footer(text="Click a reaction to get the role • Unreact to remove it")

    try:
        msg = await channel.send(embed=embed)
    except Exception as e:
        await interaction.followup.send(f"❌ Failed to send message in {channel.mention}: {e}", ephemeral=True)
        return

    for em, _ in pairs:
        clean_em = em.strip()
        try:
            await msg.add_reaction(clean_em)
            await asyncio.sleep(0.3)
        except Exception as e:
            resolved = False
            custom_match = re.search(r":(\w+):(\d+)>?", clean_em)
            if custom_match:
                e_id = int(custom_match.group(2))
                custom_obj = bot.get_emoji(e_id)
                if custom_obj:
                    try:
                        await msg.add_reaction(custom_obj)
                        resolved = True
                    except Exception:
                        pass
            if not resolved:
                print(f"[Reaction Role] Warning: could not add reaction {clean_em}: {e}", flush=True)

    rr_config = get_reaction_roles_config(interaction.guild.id)
    rr_config[str(msg.id)] = {
        "channel_id": channel.id,
        "guild_id": interaction.guild.id,
        "title": title,
        "roles": roles_map
    }
    set_reaction_roles_config(interaction.guild.id, rr_config)

    await interaction.followup.send(f"✅ Reaction role panel created successfully in {channel.mention}! Message ID: `{msg.id}`", ephemeral=True)

@reactionrole_group.command(name="add", description="Add an emoji and role to an existing message")
@app_commands.describe(
    message_id="The ID of the message to attach the reaction role to",
    emoji="The emoji for members to react with",
    role="The role to assign when reacted",
    channel="The channel where the message exists (defaults to current channel)"
)
async def cmd_reactionrole_add(
    interaction: discord.Interaction,
    message_id: str,
    emoji: str,
    role: discord.Role,
    channel: discord.TextChannel = None
):
    await interaction.response.defer(ephemeral=True)

    if not is_server_owner_or_extraowner(interaction.guild, interaction.user.id):
        await interaction.followup.send("🔒 **Access Denied**: Only the Server Owner, Extra Owners, or Supreme Bot Owner can configure reaction roles.", ephemeral=True)
        return

    bot_member = interaction.guild.get_member(bot.user.id)
    if bot_member and role >= bot_member.top_role:
        await interaction.followup.send(f"❌ Cannot assign {role.mention}: My bot role must be higher than this role.", ephemeral=True)
        return

    target_channel = channel or interaction.channel
    try:
        msg = await target_channel.fetch_message(int(message_id))
    except Exception:
        await interaction.followup.send(f"❌ Could not find message `{message_id}` in {target_channel.mention}.", ephemeral=True)
        return

    clean_emoji = emoji.strip()
    try:
        await msg.add_reaction(clean_emoji)
    except Exception as e:
        await interaction.followup.send(f"❌ Failed to add reaction `{clean_emoji}` to message: {e}", ephemeral=True)
        return

    rr_config = get_reaction_roles_config(interaction.guild.id)
    msg_key = str(msg.id)
    if msg_key not in rr_config:
        rr_config[msg_key] = {
            "channel_id": target_channel.id,
            "guild_id": interaction.guild.id,
            "roles": {}
        }
    rr_config[msg_key]["roles"][clean_emoji] = role.id
    set_reaction_roles_config(interaction.guild.id, rr_config)

    await interaction.followup.send(f"✅ Reaction role attached! Reacting with {clean_emoji} on message `{message_id}` will grant {role.mention}.", ephemeral=True)

@reactionrole_group.command(name="list", description="List all active reaction role panels in this server")
async def cmd_reactionrole_list(interaction: discord.Interaction):
    rr_config = get_reaction_roles_config(interaction.guild.id)
    if not rr_config:
        await interaction.response.send_message("ℹ️ No active reaction role messages found in this server.", ephemeral=True)
        return

    lines = []
    for msg_id, data in rr_config.items():
        ch_id = data.get("channel_id")
        roles = data.get("roles", {})
        pair_str = ", ".join([f"{em} ➔ <@&{rid}>" for em, rid in roles.items()])
        lines.append(f"• **Msg ID `{msg_id}`** in <#{ch_id}>:\n  {pair_str}")

    embed = discord.Embed(
        title="🎭 Active Reaction Roles",
        description="\n\n".join(lines),
        color=discord.Color.purple()
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)

@reactionrole_group.command(name="remove", description="Remove reaction role mappings from a message")
@app_commands.describe(
    message_id="The message ID to remove mappings for",
    emoji="Optional: remove only this specific emoji from the message"
)
async def cmd_reactionrole_remove(interaction: discord.Interaction, message_id: str, emoji: str = None):
    await interaction.response.defer(ephemeral=True)

    if not is_server_owner_or_extraowner(interaction.guild, interaction.user.id):
        await interaction.followup.send("🔒 **Access Denied**: Only the Server Owner, Extra Owners, or Supreme Bot Owner can remove reaction roles.", ephemeral=True)
        return

    rr_config = get_reaction_roles_config(interaction.guild.id)
    msg_key = str(message_id).strip()
    if msg_key not in rr_config:
        await interaction.followup.send(f"❌ Message `{message_id}` is not registered as a reaction role panel.", ephemeral=True)
        return

    if emoji:
        clean_emoji = emoji.strip()
        if clean_emoji in rr_config[msg_key].get("roles", {}):
            del rr_config[msg_key]["roles"][clean_emoji]
            set_reaction_roles_config(interaction.guild.id, rr_config)
            await interaction.followup.send(f"✅ Removed reaction role for {clean_emoji} from message `{message_id}`.", ephemeral=True)
        else:
            await interaction.followup.send(f"❌ Emoji `{clean_emoji}` was not registered on message `{message_id}`.", ephemeral=True)
    else:
        del rr_config[msg_key]
        set_reaction_roles_config(interaction.guild.id, rr_config)
        await interaction.followup.send(f"✅ All reaction roles for message `{message_id}` have been deleted.", ephemeral=True)

@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    if payload.user_id == bot.user.id or not payload.guild_id:
        return

    config = load_server_config()
    rr_config = config.get(str(payload.guild_id), {}).get("reaction_roles", {})
    msg_key = str(payload.message_id)
    if msg_key not in rr_config:
        return

    roles_map = rr_config[msg_key].get("roles", {})
    matched_role_id = None
    for em_str, r_id in roles_map.items():
        if em_str == str(payload.emoji) or em_str == payload.emoji.name or (payload.emoji.id and str(payload.emoji.id) in em_str):
            matched_role_id = r_id
            break

    if not matched_role_id:
        return

    guild = bot.get_guild(payload.guild_id)
    if not guild:
        return

    role = guild.get_role(matched_role_id)
    if not role:
        return

    member = payload.member
    if not member:
        try:
            member = await guild.fetch_member(payload.user_id)
        except Exception:
            return

    if member and role not in member.roles:
        try:
            await member.add_roles(role, reason="[Reaction Role] Auto-assigned via reaction")
        except Exception as e:
            print(f"[Reaction Role Add Warning] {e}", flush=True)

@bot.event
async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent):
    if payload.user_id == bot.user.id or not payload.guild_id:
        return

    config = load_server_config()
    rr_config = config.get(str(payload.guild_id), {}).get("reaction_roles", {})
    msg_key = str(payload.message_id)
    if msg_key not in rr_config:
        return

    roles_map = rr_config[msg_key].get("roles", {})
    matched_role_id = None
    for em_str, r_id in roles_map.items():
        if em_str == str(payload.emoji) or em_str == payload.emoji.name or (payload.emoji.id and str(payload.emoji.id) in em_str):
            matched_role_id = r_id
            break

    if not matched_role_id:
        return

    guild = bot.get_guild(payload.guild_id)
    if not guild:
        return

    role = guild.get_role(matched_role_id)
    if not role:
        return

    member = guild.get_member(payload.user_id)
    if not member:
        try:
            member = await guild.fetch_member(payload.user_id)
        except Exception:
            return

    if member and role in member.roles:
        try:
            await member.remove_roles(role, reason="[Reaction Role] Auto-removed via unreaction")
        except Exception as e:
            print(f"[Reaction Role Remove Warning] {e}", flush=True)


# ==============================================================================
# HELP COMMAND SYSTEM (/help with interactive category selection)
# ==============================================================================
def get_help_embed(category: str, guild: discord.Guild = None) -> discord.Embed:
    if category == "security":
        embed = discord.Embed(
            title="🛡️ Security & Antinuke Modules",
            description="Enterprise server protection, rogue admin prevention, and emergency lockdown controls:",
            color=discord.Color.red()
        )
        embed.add_field(name="🛡️ /antinuke enable", value="Enable real-time Antinuke defense protocol.", inline=False)
        embed.add_field(name="🛑 /antinuke disable", value="Disable Antinuke protection.", inline=False)
        embed.add_field(name="📊 /antinuke status", value="View Antinuke status, triggers, and active safeguards.", inline=False)
        embed.add_field(name="🧪 /antinuke test", value="Preview Antinuke punishment actions.", inline=False)
        embed.add_field(name="🤖 /automod enable", value="Enable server-wide automod (links, mentions, flooding, emoji spam).", inline=False)
        embed.add_field(name="📍 /automod channel", value="Enable automod for a specific channel.", inline=False)
        embed.add_field(name="⏸️ /automod disable", value="Disable automod protection.", inline=False)
        embed.add_field(name="🔄 /automod reset", value="Reset all automod settings and rules.", inline=False)
        embed.add_field(name="🧪 /automod test", value="Simulate an automod violation & timeout preview.", inline=False)
        embed.add_field(name="📜 /whitelist add | remove | list", value="Manage whitelisted admins and bots immune to Antinuke.", inline=False)
        embed.add_field(name="🔒 /hideall | /unhideall", value="Instantly hide or unhide all channels from `@everyone`.", inline=False)
        embed.add_field(name="🔊 /lockall | /unlockall", value="Lock or unlock all server voice channels.", inline=False)
        embed.add_field(name="⏱️ /slowmode enable | disable", value="Toggle slowmode across all channels or a target channel.", inline=False)

    elif category == "admin":
        embed = discord.Embed(
            title="👑 Administration & Authority Modules",
            description="Manage bot administrators, Extra Owners, autoroles, and member formatting:",
            color=discord.Color.gold()
        )
        embed.add_field(name="👑 /extraowner set | remove | list", value="Designate or remove Extra Owners. Extra Owners share full bot authority with Server Owner.", inline=False)
        embed.add_field(name="🛡️ /admin add | remove | list", value="Authorize or revoke bot administrators in this server.", inline=False)
        embed.add_field(name="🎭 /autorole add | disable | status", value="Configure default role assigned to new human members.", inline=False)
        embed.add_field(name="🤖 /autorole botadd | botdisable | botstatus", value="Configure default role assigned to new bot accounts.", inline=False)
        embed.add_field(name="🏷️ /setprefix | /removeprefix | /viewprefix", value="Configure automatic name prefixes for new members.", inline=False)
        embed.add_field(name="✏️ /formatall", value="Batch format existing members with a server prefix.", inline=False)
        embed.add_field(name="🔄 /resetallnicks", value="Reset all members back to default Discord usernames.", inline=False)

    elif category == "mod":
        embed = discord.Embed(
            title="⚖️ Moderation & Server Utility",
            description="Essential commands for daily server management, member discipline, and auditing:",
            color=discord.Color.blue()
        )
        embed.add_field(name="⚠️ /warn", value="Issue a formal warning card to a member.", inline=False)
        embed.add_field(name="🧹 /clear", value="Purge 1–100 recent messages from the current channel.", inline=False)
        embed.add_field(name="🔓 /unban", value="Unban a member using their Discord ID.", inline=False)
        embed.add_field(name="📜 /banlist", value="List all banned users in this server.", inline=False)
        embed.add_field(name="🔊 /untimeout", value="Remove an active timeout from a member.", inline=False)
        embed.add_field(name="✏️ /changenick", value="Change or reset a member's server nickname.", inline=False)
        embed.add_field(name="👥 /rolemembers", value="List all members carrying a specific role.", inline=False)
        embed.add_field(name="🎯 /snipe", value="Inspect recently deleted messages in the channel.", inline=False)
        embed.add_field(name="📋 /audit", value="Inspect recent server audit log actions.", inline=False)
        embed.add_field(name="📊 /serverinfo | /info", value="Display server statistics or detailed user information.", inline=False)
        embed.add_field(name="🖼️ /avatar | /banner", value="Display user or server avatars and banners in 4K resolution.", inline=False)
        embed.add_field(name="📨 /spam | 🛑 /stop", value="Send repeated sequences or immediately stop active processes.", inline=False)

    elif category == "levels":
        embed = discord.Embed(
            title="🎂 Birthdays & Leveling System",
            description="Celebrate member birthdays and reward chat activity with cute level milestones:",
            color=discord.Color.from_rgb(255, 105, 180)
        )
        embed.add_field(name="🎂 /birthday set", value="Register your birthday (month, day, year).", inline=False)
        embed.add_field(name="🎈 /birthday view", value="Check your or another member's birthday and countdown.", inline=False)
        embed.add_field(name="📅 /birthday upcoming", value="List the next 10 upcoming birthdays in the server.", inline=False)
        embed.add_field(name="⚙️ /birthday setup", value="Set birthday announcement and input channels *(Owner/Extraowner)*.", inline=False)
        embed.add_field(name="🗑️ /birthday remove", value="Remove your registered birthday.", inline=False)
        embed.add_field(name="🛑 /birthday disable", value="Disable birthday celebrations *(Owner/Extraowner)*.", inline=False)
        embed.add_field(name="🌸 /rank", value="View your or a member's cute rank card, level, and XP bar.", inline=False)
        embed.add_field(name="🏆 /leaderboard", value="View the top 10 most active chatting members.", inline=False)
        embed.add_field(name="🎀 /leveling setup", value="Set the channel for cute level up cards *(Owner/Extraowner)*.", inline=False)
        embed.add_field(name="⚙️ /leveling toggle", value="Enable or disable leveling *(Owner/Extraowner)*.", inline=False)

    elif category == "roles":
        embed = discord.Embed(
            title="🎭 Reaction Roles & Welcoming",
            description="Self-assignable reaction roles and extraordinary member onboarding cards:",
            color=discord.Color.purple()
        )
        embed.add_field(name="🎭 /reactionrole create", value="Create a reaction role panel with up to 6 roles *(Owner/Extraowner)*.", inline=False)
        embed.add_field(name="➕ /reactionrole add", value="Attach an emoji & role to an existing message *(Owner/Extraowner)*.", inline=False)
        embed.add_field(name="📜 /reactionrole list", value="View active reaction role messages in the server.", inline=False)
        embed.add_field(name="🗑️ /reactionrole remove", value="Delete reaction role mappings *(Owner/Extraowner)*.", inline=False)
        embed.add_field(name="🎨 /greet setup", value="Configure welcome cards with 3 themes & up to 3 channels.", inline=False)
        embed.add_field(name="📢 /greet channel", value="Set or change the primary welcome channel.", inline=False)
        embed.add_field(name="🧪 /greet test", value="Send a live preview test of the welcome card.", inline=False)
        embed.add_field(name="🖼️ /greet templates", value="Preview all 3 available welcome card designs.", inline=False)
        embed.add_field(name="👋 /bye | /byestatus | /byedisable", value="Configure or disable automatic farewell cards when members leave.", inline=False)

    elif category == "voice":
        embed = discord.Embed(
            title="🔊 Voice, Actions & Community Fun",
            description="24/7 Voice persistence, voice moderation, interactive roleplay, and mini-games:",
            color=discord.Color.teal()
        )
        embed.add_field(name="🎧 /join | /leave", value="Connect bot to your voice channel (24/7 DAVE E2EE) or disconnect.", inline=False)
        embed.add_field(name="🔇 /muteall | 🔊 /deafenall", value="Server-mute/unmute or deafen/undeafen all voice members.", inline=False)
        embed.add_field(name="👢 /kickall", value="Disconnect all members from a voice channel.", inline=False)
        embed.add_field(name="🔒 /lock | 🔓 /unlock", value="Lock or unlock your current voice channel.", inline=False)
        embed.add_field(name="🫂 /hug | /kiss | /slap | /punch | /dance", value="Action roleplay with cute animated GIFs.", inline=False)
        embed.add_field(name="⚔️ /matchmaker", value="Split 8–12 players into two balanced squads.", inline=False)
        embed.add_field(name="🎡 /spin | 🪙 /coinflip | 🎲 /roll", value="Mini-games: wheel spin, coin flip, and dice roll.", inline=False)
        embed.add_field(name="🚀 /bump notify | /bump status", value="Configure automated bump reminders and ping roles.", inline=False)

    else:
        # Overview
        embed = discord.Embed(
            title="🔱 HELLFIRE CLUB — Bot Command Directory 🔱",
            description=(
                "Welcome to the official **HELLFIRE CLUB Command Center**!\n"
                "Use the interactive dropdown menu below to explore all commands by category.\n\n"
                "📌 **Quick Summary:**\n"
                "• 🛡️ **Security & Antinuke:** Real-time defense, Automod, Lockdowns, Whitelists\n"
                "• 👑 **Administration:** Extra Owners, Bot Admins, Autoroles, Nicknames\n"
                "• ⚖️ **Moderation & Logs:** Warns, Clears, Bans, Untimeout, Snipe, Audit\n"
                "• 🎂 **Birthdays & Leveling:** Automated birthday wishes, Cute level-up cards, Leaderboards\n"
                "• 🎭 **Reaction Roles & Welcomes:** Self-roles by reaction, 3 Welcome card themes\n"
                "• 🔊 **Voice & Fun:** 24/7 Voice Engine, Mass Mute/Deafen, Action GIFs, Mini-games\n\n"
                "💡 *Select a category from the dropdown below to view all options and guides!*"
            ),
            color=discord.Color.from_rgb(255, 30, 86)
        )
        if guild and guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        embed.set_footer(text="HELLFIRE CLUB Bot Engine • Pair with Extra Owner & Server Owner authority")

    return embed

class HelpSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Overview & Dashboard", emoji="🔱", description="General command directory overview", value="overview"),
            discord.SelectOption(label="Security & Antinuke", emoji="🛡️", description="Antinuke, Automod, Whitelist, Lockdowns", value="security"),
            discord.SelectOption(label="Administration & Extra Owners", emoji="👑", description="Admins, Extra Owners, Autorole, Nicknames", value="admin"),
            discord.SelectOption(label="Moderation & Utility", emoji="⚖️", description="Warn, Clear, Ban/Unban, Snipe, Audit", value="mod"),
            discord.SelectOption(label="Birthdays & Leveling", emoji="🎂", description="Birthday wishes, /rank, /leaderboard, Leveling", value="levels"),
            discord.SelectOption(label="Reaction Roles & Welcomes", emoji="🎭", description="Reaction roles, Greet cards, Farewell cards", value="roles"),
            discord.SelectOption(label="Voice, Action & Fun", emoji="🔊", description="24/7 VC, Muteall, Kickall, Hug, Games, Bump", value="voice"),
        ]
        super().__init__(placeholder="🔍 Select a category to explore commands...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        cat = self.values[0]
        embed = get_help_embed(cat, interaction.guild)
        await interaction.response.edit_message(embed=embed, view=self.view)

class HelpView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180.0)
        self.add_item(HelpSelect())

@bot.tree.command(name="help", description="Display the comprehensive command directory and guide")
@app_commands.describe(category="Optional category to jump straight to")
@app_commands.choices(category=[
    app_commands.Choice(name="🛡️ Security & Antinuke", value="security"),
    app_commands.Choice(name="👑 Administration & Extra Owners", value="admin"),
    app_commands.Choice(name="⚖️ Moderation & Server Utility", value="mod"),
    app_commands.Choice(name="🎂 Birthdays & Leveling", value="levels"),
    app_commands.Choice(name="🎭 Reaction Roles & Welcoming", value="roles"),
    app_commands.Choice(name="🔊 Voice, Action & Fun", value="voice"),
])
async def cmd_help(interaction: discord.Interaction, category: app_commands.Choice[str] = None):
    cat_val = category.value if category else "overview"
    embed = get_help_embed(cat_val, interaction.guild)
    view = HelpView()
    await interaction.response.send_message(embed=embed, view=view)


bot.tree.add_command(birthday_group)
bot.tree.add_command(reactionrole_group)
bot.tree.add_command(leveling_group)


_web_server_initialized = False

async def main():
    global _web_server_initialized
    if not _web_server_initialized:
        try:
            await start_web_server()
            _web_server_initialized = True
        except Exception as e:
            print(f"Web server warning: {e}", flush=True)
    async with bot:
        await bot.start(TOKEN, reconnect=True)

if __name__ == "__main__":
    print("=== Discord Bot Process Starting ===", flush=True)

    if not TOKEN:
        print("CRITICAL ERROR: DISCORD_TOKEN is missing or empty in .env!", flush=True)
        sys.exit(1)

    print(f"DISCORD_TOKEN loaded successfully ({len(TOKEN)} characters).", flush=True)

    retry_delay = 5
    while True:
        try:
            asyncio.run(main())
        except (KeyboardInterrupt, SystemExit):
            print("\nBot process terminated.", flush=True)
            break
        except discord.errors.PrivilegedIntentsRequired:
            print("\n" + "!" * 60, flush=True)
            print("CRITICAL ERROR: Privileged Gateway Intents are NOT enabled!", flush=True)
            print("Enable 'Server Members Intent' and 'Message Content Intent' in Discord Dev Portal.", flush=True)
            print("!" * 60 + "\n", flush=True)
            sys.exit(1)
        except discord.errors.LoginFailure:
            print("\n" + "!" * 60, flush=True)
            print("CRITICAL ERROR: Invalid Discord Bot Token!", flush=True)
            print("!" * 60 + "\n", flush=True)
            sys.exit(1)
        except Exception as e:
            print(f"[Auto-Reconnect] Connection lost/interrupted: {e}. Reconnecting in {int(retry_delay)}s...", flush=True)
            time.sleep(retry_delay)
            retry_delay = min(retry_delay * 1.5, 30)
