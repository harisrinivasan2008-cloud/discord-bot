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
from collections import defaultdict, deque
import discord
from discord.ext import commands, tasks
from discord import app_commands
from discord.ext import voice_recv
from dotenv import load_dotenv
from aiohttp import web

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
        
    founder_role = discord.utils.find(lambda r: "founder" in r.name.lower(), guild.roles) if guild else None
    if founder_role:
        founder_tag = founder_role.mention
    elif guild and guild.owner:
        founder_tag = guild.owner.mention
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

def is_user_admin(user_id: int) -> bool:
    # 1. Supreme Bot Owner is ALWAYS permanently authorized across all servers
    if user_id == BOT_OWNER_ID:
        return True
    if bot.owner_id and user_id == bot.owner_id:
        return True
    # 2. Check if user is in authorized admins list
    admin_ids = load_admin_ids()
    return user_id in admin_ids

def is_authorized_admin(interaction: discord.Interaction) -> bool:
    return is_user_admin(interaction.user.id)

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

def is_spam_exempt(member: discord.Member) -> bool:
    if not isinstance(member, discord.Member):
        return False

    # 1. Supreme Bot Owner and Authorized Admins are always exempt
    if is_user_admin(member.id):
        return True

    # 2. Members with the "monarch" role (case-insensitive) are exempt
    for role in member.roles:
        if role.name.lower() == "monarch":
            return True

    # 3. Members with the Server Tag / Prefix in their name or nickname
    if member.guild:
        server_prefix = get_default_prefix(member.guild.id)
        if server_prefix:
            tag_raw = server_prefix.strip().lower()
            tag_clean = tag_raw.strip("[](){}<>|# ").lower()

            display_name = member.display_name.lower()
            user_name = member.name.lower()

            if tag_raw and (tag_raw in display_name or tag_raw in user_name):
                return True
            if tag_clean and (tag_clean in display_name or tag_clean in user_name):
                return True

    return False


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

bot = commands.Bot(command_prefix="!", intents=intents, voice_client_cls=voice_recv.VoiceRecvClient)

# Optional Web server for cloud health checks
async def start_web_server():
    async def handle_ping(request):
        return web.Response(text="Discord Bot is active and running!")

    app = web.Application()
    app.router.add_get("/", handle_ping)
    app.router.add_get("/health", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"Health-check web server active on port {port}", flush=True)

@bot.event
async def on_ready():
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

    # Sync slash commands globally across Discord
    try:
        synced = await bot.tree.sync()
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
# AUTO DEFAULT PREFIX ON MEMBER JOIN
# ==============================================================================
@bot.event
async def on_member_join(member: discord.Member):
    # Ignore bots
    if member.bot:
        return

    prefix = get_default_prefix(member.guild.id)
    if not prefix:
        return

    # Check bot role hierarchy before attempting edit
    bot_member = member.guild.get_member(bot.user.id)
    if bot_member and member.top_role >= bot_member.top_role:
        print(f"[Auto-Prefix] Cannot set prefix for {member.name} in {member.guild.name}: role higher than or equal to bot.", flush=True)
        return

    new_nick = f"{prefix} {member.display_name}".strip()
    if len(new_nick) > 32:
        new_nick = new_nick[:32]

    try:
        await member.edit(nick=new_nick)
        print(f"[Auto-Prefix] Applied prefix '{prefix}' to new member {member.name} ({member.id}) in {member.guild.name}.", flush=True)
    except discord.Forbidden:
        print(f"[Auto-Prefix] Forbidden: Bot lacks permission to edit {member.name}'s nickname in {member.guild.name}.", flush=True)
    except Exception as e:
        print(f"[Auto-Prefix] Error editing nickname for {member.name}: {e}", flush=True)

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

            # 1. Send the bump command string to the channel
            try:
                await channel.send(bump_text)
            except Exception as e:
                print(f"[Auto-Bump] Failed to send bump text in {guild.name}: {e}", flush=True)

            # 2. Post the rich bump reminder card with ping
            ping_str = f"<@&{ping_role_id}> " if ping_role_id else ""
            embed = discord.Embed(
                title="🚀 BUMP TIME! Automatic Server Bump Triggered",
                description=(
                    f"The **{interval_mins}-minute** cooldown has ended!\n\n"
                    f"• **Sent Bump**: `{bump_text}`\n"
                    f"• **Next Bump**: <t:{int(now + (interval_mins * 60))}:R> (<t:{int(now + (interval_mins * 60))}:t>)\n\n"
                    f"💡 *If your bump bot (like Disboard) requires a manual slash interaction, click or type `/bump` in this channel now!*"
                ),
                color=discord.Color.from_rgb(255, 115, 250)
            )
            embed.set_footer(text=f"Auto-Bump System • Interval: {interval_mins} mins")
            try:
                await channel.send(content=ping_str if ping_str else None, embed=embed)
                print(f"[Auto-Bump] Triggered auto-bump in #{channel.name} ({guild.name})", flush=True)
            except Exception as e:
                print(f"[Auto-Bump] Error sending reminder card: {e}", flush=True)


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

    # ================= AUTOMATED ANTI-SPAM SYSTEM =================
    # Members with the server tag, "monarch" role, or bot admins are 100% exempt from spam warnings
    if message.guild and isinstance(message.author, discord.Member) and not is_spam_exempt(message.author):
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

    await bot.process_commands(message)


# ==============================================================================
# 2. ADMIN ACCESS MANAGEMENT COMMANDS (/admin add, /admin remove, /admin list)
# ==============================================================================
# ==============================================================================
# BUMP — Automated Server Bumper & Cooldown Reminder (/bump)
# ==============================================================================
bump_group = app_commands.Group(name="bump", description="Automated server bump & reminder system")

@bump_group.command(name="setup", description="Configure automatic server bumping on a scheduled interval")
@app_commands.describe(
    channel="The text channel where bump commands and reminders will be sent",
    interval_minutes="Cooldown between bumps in minutes (default: 120 for Disboard)",
    bump_text="The bump text command to send (default: /bump)",
    ping_role="Optional role to ping when the server is bumped / ready"
)
@check_bot_admin()
async def bump_setup(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
    interval_minutes: int = 120,
    bump_text: str = "/bump",
    ping_role: discord.Role = None
):
    guild = interaction.guild
    if not guild:
        await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)
        return

    if interval_minutes < 5:
        await interaction.response.send_message("❌ Interval must be at least 5 minutes.", ephemeral=True)
        return

    now = time.time()
    next_bump = now + (interval_minutes * 60)

    bump_cfg = {
        "enabled": True,
        "channel_id": channel.id,
        "interval_minutes": interval_minutes,
        "bump_text": bump_text.strip(),
        "ping_role_id": ping_role.id if ping_role else None,
        "next_bump_timestamp": next_bump
    }
    save_bump_config(guild.id, bump_cfg)

    embed = discord.Embed(
        title="✅ Automatic Bumper Configured!",
        description=f"The bot will automatically bump **{guild.name}** and monitor cooldowns.",
        color=discord.Color.green()
    )
    embed.add_field(name="📌 Channel", value=channel.mention, inline=True)
    embed.add_field(name="⏱️ Interval", value=f"Every **{interval_minutes}** minutes ({(interval_minutes/60):.1f} hrs)", inline=True)
    embed.add_field(name="💬 Sent Text", value=f"`{bump_text}`", inline=True)
    embed.add_field(name="🔔 Ping Role", value=ping_role.mention if ping_role else "*None*", inline=True)
    embed.add_field(name="⏳ First Scheduled Bump", value=f"<t:{int(next_bump)}:R> (<t:{int(next_bump)}:t>)", inline=False)
    embed.set_footer(text="Use /bump now to trigger immediately, or /bump status to inspect.")

    await interaction.response.send_message(embed=embed)


@bump_group.command(name="now", description="Trigger an immediate bump in the configured channel")
@check_bot_admin()
async def bump_now(interaction: discord.Interaction):
    guild = interaction.guild
    if not guild:
        await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)
        return

    bump_cfg = get_bump_config(guild.id)
    channel_id = bump_cfg.get("channel_id") if bump_cfg else None
    channel = guild.get_channel(channel_id) if channel_id else interaction.channel

    if not channel or not isinstance(channel, discord.TextChannel):
        await interaction.response.send_message("❌ No valid bump channel configured. Use `/bump setup` first.", ephemeral=True)
        return

    bump_text = bump_cfg.get("bump_text", "/bump") if bump_cfg else "/bump"
    interval_mins = bump_cfg.get("interval_minutes", 120) if bump_cfg else 120
    ping_role_id = bump_cfg.get("ping_role_id") if bump_cfg else None

    now = time.time()
    next_bump = now + (interval_mins * 60)

    if bump_cfg:
        bump_cfg["next_bump_timestamp"] = next_bump
        bump_cfg["enabled"] = True
        save_bump_config(guild.id, bump_cfg)

    # 1. Send the bump text in the target channel
    try:
        await channel.send(bump_text)
    except Exception as e:
        await interaction.response.send_message(f"❌ Failed to send bump text in {channel.mention}: {e}", ephemeral=True)
        return

    # 2. Send reminder embed
    ping_str = f"<@&{ping_role_id}> " if ping_role_id else ""
    embed = discord.Embed(
        title="🚀 Manual Bump Triggered!",
        description=(
            f"Successfully posted `{bump_text}` in {channel.mention}!\n\n"
            f"• **Next Scheduled Bump**: <t:{int(next_bump)}:R>\n"
            f"• **Interval**: {interval_mins} minutes\n\n"
            f"*If using Disboard slash commands, run `/bump` in {channel.mention} now!*"
        ),
        color=discord.Color.nitro_pink()
    )
    embed.set_footer(text=f"Triggered by {interaction.user.display_name}")
    await channel.send(content=ping_str if ping_str else None, embed=embed)
    await interaction.response.send_message(f"✅ Bump triggered in {channel.mention}!", ephemeral=True)


@bump_group.command(name="status", description="Check current bump timer, channel, and settings")
@check_bot_admin()
async def bump_status(interaction: discord.Interaction):
    guild = interaction.guild
    if not guild:
        await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)
        return

    bump_cfg = get_bump_config(guild.id)
    if not bump_cfg or not bump_cfg.get("channel_id"):
        await interaction.response.send_message(
            f"ℹ️ Automatic bump is currently **not configured** for **{guild.name}**.\n"
            f"Use `/bump setup channel: #channel` to activate it.",
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
        countdown_str = "🟢 **Ready to bump now!**"
    else:
        countdown_str = f"⏳ <t:{int(next_bump)}:R> (<t:{int(next_bump)}:t>)"

    embed = discord.Embed(
        title="📊 Server Auto-Bump Status",
        description=f"Auto-bump status and cooldown timer for **{guild.name}**.",
        color=discord.Color.green() if is_active else discord.Color.greyple()
    )
    embed.add_field(name="⚙️ Status", value="🟢 **Active**" if is_active else "🔴 **Disabled**", inline=True)
    embed.add_field(name="📌 Bump Channel", value=channel.mention if channel else "*Not found*", inline=True)
    embed.add_field(name="⏱️ Interval", value=f"Every **{interval_mins}** mins ({(interval_mins/60):.1f} hrs)", inline=True)
    embed.add_field(name="💬 Sent Text", value=f"`{bump_cfg.get('bump_text', '/bump')}`", inline=True)
    embed.add_field(name="🔔 Ping Role", value=ping_role.mention if ping_role else "*None*", inline=True)
    embed.add_field(name="⏰ Next Bump", value=countdown_str, inline=False)

    await interaction.response.send_message(embed=embed)


@bump_group.command(name="disable", description="Disable automatic server bumping")
@check_bot_admin()
async def bump_disable(interaction: discord.Interaction):
    guild = interaction.guild
    if not guild:
        await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)
        return

    if disable_bump_config(guild.id):
        await interaction.response.send_message(f"✅ Automatic server bumping has been **disabled** for **{guild.name}**.")
    else:
        await interaction.response.send_message("⚠️ Automatic bumping was not active.", ephemeral=True)

bot.tree.add_command(bump_group)

admin_group = app_commands.Group(name="admin", description="Manage authorized bot administrators")

@admin_group.command(name="add", description="Grant a user full access to use this bot")
@app_commands.describe(member="The member to authorize as a bot administrator")
@check_bot_admin()
async def admin_add(interaction: discord.Interaction, member: discord.Member):
    if member.id == BOT_OWNER_ID:
        await interaction.response.send_message(
            f"👑 {member.mention} is the supreme Bot Owner and already has permanent global access.",
            ephemeral=True
        )
        return

    admin_ids = load_admin_ids()
    if member.id in admin_ids:
        await interaction.response.send_message(
            f"⚠️ {member.mention} already has bot administrator access.",
            ephemeral=True
        )
        return

    admin_ids.add(member.id)
    save_admin_ids(admin_ids)
    await interaction.response.send_message(
        f"✅ Successfully granted bot admin access to {member.mention}."
    )

@admin_group.command(name="remove", description="Revoke a user's access to this bot")
@app_commands.describe(member="The member to remove from bot administrators")
@check_bot_admin()
async def admin_remove(interaction: discord.Interaction, member: discord.Member):
    if member.id == BOT_OWNER_ID:
        await interaction.response.send_message(
            "❌ Cannot revoke access from the Bot Owner (permanent supreme administrator).",
            ephemeral=True
        )
        return

    admin_ids = load_admin_ids()
    if member.id not in admin_ids:
        await interaction.response.send_message(
            f"⚠️ {member.mention} is not in the bot administrators list.",
            ephemeral=True
        )
        return

    admin_ids.remove(member.id)
    save_admin_ids(admin_ids)
    await interaction.response.send_message(
        f"✅ Successfully revoked bot admin access from {member.mention}."
    )

@admin_group.command(name="list", description="Show all users authorized to access this bot")
@check_bot_admin()
async def admin_list(interaction: discord.Interaction):
    admin_ids = load_admin_ids()
    owner_str = f"👑 <@{BOT_OWNER_ID}> (`{BOT_OWNER_ID}`) *(Supreme Bot Owner - Global)*"

    admin_list_text = []
    for uid in sorted(admin_ids):
        if uid == BOT_OWNER_ID:
            continue
        admin_list_text.append(f"🛡️ <@{uid}> (`{uid}`)")

    embed = discord.Embed(
        title="🔐 Authorized Bot Administrators",
        description="Only users on this list are permitted to execute bot commands across servers.",
        color=discord.Color.gold()
    )
    embed.add_field(name="Supreme Bot Owner", value=owner_str, inline=False)
    if admin_list_text:
        embed.add_field(
            name=f"Configured Admins ({len(admin_list_text)})",
            value="\n".join(admin_list_text),
            inline=False
        )
    else:
        embed.add_field(
            name="Configured Admins",
            value="*No additional admins added yet. Use `/admin add` to authorize members.*",
            inline=False
        )
    embed.set_footer(text=f"Total: {len(admin_list_text) + 1} Authorized User(s)")

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
# 5. AVATAR COMMAND
# ==============================================================================
@bot.tree.command(name="avatar", description="Display a member's avatar in full size")
@app_commands.describe(member="The member whose avatar you want to view (defaults to yourself)")
@check_bot_admin()
async def avatar(interaction: discord.Interaction, member: discord.Member = None):
    target = member or interaction.user
    avatar_url = target.display_avatar.url

    embed = discord.Embed(
        title=f"🖼️ Avatar of {target.display_name}",
        color=target.color if target.color.value != 0 else discord.Color.blue()
    )
    embed.set_image(url=avatar_url)
    embed.add_field(name="Direct Link", value=f"[Open in Browser]({avatar_url})", inline=False)
    embed.set_footer(text=f"Requested by {interaction.user.display_name}")

    await interaction.response.send_message(embed=embed)

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
        act_key = entry.action.name.lower()
        icon = ACTION_ICONS.get(act_key, "📝")
        act_name = str(entry.action.name).replace("_", " ").title()
        
        user_str = entry.user.mention if entry.user else "Unknown Moderator"
        target_str = ""
        if entry.target:
            if hasattr(entry.target, "mention"):
                target_str = f" ➔ {entry.target.mention}"
            elif hasattr(entry.target, "name"):
                target_str = f" ➔ **{entry.target.name}**"
            else:
                target_str = f" ➔ `{entry.target}`"

        time_str = f"<t:{int(entry.created_at.timestamp())}:R>"
        reason_str = f"\n> *Reason: {entry.reason}*" if entry.reason else ""

        val = f"**Mod:** {user_str}{target_str}\n**When:** {time_str}{reason_str}"
        embed.add_field(name=f"{icon} #{i} • {act_name}", value=val, inline=False)

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

@bot.tree.command(name="join", description="Connect the bot to your current voice channel")
@app_commands.describe(channel="Voice channel to join (optional, defaults to your current VC)")
@check_bot_admin()
async def join_voice(interaction: discord.Interaction, channel: discord.VoiceChannel = None):
    target = channel
    if not target:
        if interaction.user.voice and interaction.user.voice.channel:
            target = interaction.user.voice.channel
        else:
            await interaction.response.send_message("❌ Please join a voice channel first or select one.", ephemeral=True)
            return

    vc = interaction.guild.voice_client
    if vc and vc.is_connected():
        if vc.channel.id == target.id:
            await interaction.response.send_message(f"⚠️ Already connected to {target.mention}.", ephemeral=True)
            return
        await vc.move_to(target)
        await interaction.response.send_message(f"🔊 Moved to {target.mention}.")
    else:
        try:
            await target.connect()
            await interaction.response.send_message(f"🔊 Successfully connected to {target.mention}!")
        except Exception as e:
            await interaction.response.send_message(f"❌ Failed to connect: {e}", ephemeral=True)

@bot.tree.command(name="leave", description="Disconnect the bot from the voice channel")
@check_bot_admin()
async def leave_voice(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if not vc or not vc.is_connected():
        await interaction.response.send_message("❌ The bot is not currently in any voice channel.", ephemeral=True)
        return
    await vc.disconnect()
    await interaction.response.send_message("👋 Disconnected from voice channel.")

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





import davey
import discord.ext.voice_recv.reader as vr_reader
import discord.ext.voice_recv.opus as vr_opus

# 1. Fix UDP keepalive: discord-ext-voice-recv erroneously set delay to 5000s instead of 5s
vr_reader.UDPKeepAlive.delay = 5

import audioop

# 2. Robust DAVE-aware packet decoding with proper PLC concealment (no screeching ciphertext)
def safe_decode_packet(self, packet):
    assert self._decoder is not None
    vc = getattr(self.sink, "voice_client", None) or getattr(self.sink, "_voice_client", None)
    
    # Resolve cached user id from SSRC
    if not self._cached_id and vc and hasattr(vc, "_get_id_from_ssrc"):
        uid = vc._get_id_from_ssrc(self.ssrc)
        if uid:
            self._cached_id = uid

    if not packet:
        try:
            pcm = self._decoder.decode(None, fec=False)
            return packet, pcm
        except Exception:
            return packet, b'\x00' * 3840

    raw = packet.decrypted_data
    if not raw:
        return packet, b'\x00' * 3840

    # Discord silence frame (standard Opus, unencrypted)
    if raw == b'\xf8\xff\xfe':
        try:
            pcm = self._decoder.decode(raw, fec=False)
            return packet, pcm
        except Exception:
            return packet, b'\x00' * 3840

    state = getattr(vc, "_connection", None) if vc else None
    dave = getattr(state, "dave_session", None) if state else None
    dave_ver = getattr(state, "dave_protocol_version", 0) if state else 0

    if dave_ver > 0 and dave and getattr(dave, "ready", False) and self._cached_id:
        try:
            opus_payload = dave.decrypt(int(self._cached_id), davey.MediaType.audio, bytes(raw))
            pcm = self._decoder.decode(opus_payload, fec=False)
            return packet, pcm
        except Exception:
            # Undecrypted ciphertext - NEVER feed ciphertext into opus! Conceal with PLC
            try:
                pcm = self._decoder.decode(None, fec=False)
                return packet, pcm
            except Exception:
                return packet, b'\x00' * 3840

    try:
        pcm = self._decoder.decode(raw, fec=False)
        return packet, pcm
    except Exception:
        try:
            pcm = self._decoder.decode(None, fec=False)
            return packet, pcm
        except Exception:
            return packet, b'\x00' * 3840

vr_opus.PacketDecoder._decode_packet = safe_decode_packet

# ==============================================================================
# 13. VOICE CHANNEL RECORDING SYSTEM (DM Delivery + Instant Recall)
# ==============================================================================
RECORDINGS_DIR = os.path.join(os.path.dirname(__file__), "recordings")
os.makedirs(RECORDINGS_DIR, exist_ok=True)

guild_recording_states: dict = {}
guild_master_sinks: dict = {}

INSTANT_BUFFER_SECS  = 600   # 10 minutes rolling buffer
INSTANT_EXPIRE_SECS  = 3600  # Auto-delete after 1 hour
SAMPLE_RATE          = 48000
CHANNELS             = 2
SAMPLE_WIDTH         = 2
BYTES_PER_SECOND     = SAMPLE_RATE * CHANNELS * SAMPLE_WIDTH
FRAME_BYTES          = 3840  # 20ms of 48000Hz 16-bit stereo PCM


# ── Guild Master Sink (Continuous 10-Min Rolling Recall + Multiplexer) ───────
class GuildMasterSink(voice_recv.AudioSink):
    """Unified master audio sink for a guild voice channel.
    Permanently keeps a rolling 10-minute ring buffer of mixed audio,
    AND separate rolling buffers for every individual speaker.
    Dynamically multiplexes audio to manual recording sinks (mixed or individual).
    """
    def __init__(self, max_seconds: int = INSTANT_BUFFER_SECS):
        super().__init__()
        self._max_bytes = max_seconds * BYTES_PER_SECOND
        self._mixed_buf = bytearray()
        self._user_bufs: dict[int | str, bytearray] = {}
        self._user_names: dict[int | str, str] = {}
        self._last_user_time: dict[int | str, float] = {}
        self._lock = threading.Lock()
        self.manual_sink: voice_recv.AudioSink | None = None
        self._last_activity = time.time()
        self.total_frames_received = 0

        # Mixing tracking
        self._last_frame_time = 0.0
        self._last_speaker = None

    @property
    def voice_client(self):
        return getattr(self, "_voice_client", None)

    @voice_client.setter
    def voice_client(self, vc):
        self._voice_client = vc
        ms = self.manual_sink
        if ms:
            try:
                ms._voice_client = vc
            except Exception:
                pass

    def wants_opus(self) -> bool:
        return False

    def write(self, user, data: voice_recv.VoiceData):
        pcm = data.pcm
        if not pcm or len(pcm) != FRAME_BYTES:
            return

        now = time.time()
        vc = self.voice_client

        # Resolve speaker
        speaker_id = getattr(user, "id", None)
        speaker_name = getattr(user, "display_name", None)
        if not speaker_id:
            ssrc = getattr(data.packet, "ssrc", None) if hasattr(data, "packet") else None
            if vc and ssrc and hasattr(vc, "_get_id_from_ssrc"):
                mapped_uid = vc._get_id_from_ssrc(ssrc)
                if mapped_uid:
                    member = vc.guild.get_member(mapped_uid) if hasattr(vc, "guild") else None
                    if member:
                        speaker_id = member.id
                        speaker_name = member.display_name
            if not speaker_id:
                speaker_id = ssrc if ssrc else "Speaker"
                speaker_name = f"Speaker_{speaker_id}"
        elif not speaker_name:
            speaker_name = str(user)

        with self._lock:
            # 1. Store in speaker's individual rolling buffer
            if speaker_id not in self._user_bufs:
                self._user_bufs[speaker_id] = bytearray()
            self._user_names[speaker_id] = speaker_name
            self._last_user_time[speaker_id] = now
            ubuf = self._user_bufs[speaker_id]
            ubuf.extend(pcm)
            if len(ubuf) > self._max_bytes:
                self._user_bufs[speaker_id] = ubuf[-self._max_bytes:]

            # 2. Store in master mixed rolling buffer
            # If a DIFFERENT speaker speaks within 8ms (same 20ms audio tick):
            if (now - self._last_frame_time) < 0.008 and self._last_speaker != speaker_id and len(self._mixed_buf) >= FRAME_BYTES:
                last_frame = bytes(self._mixed_buf[-FRAME_BYTES:])
                mixed = audioop.add(last_frame, pcm, 2)
                self._mixed_buf[-FRAME_BYTES:] = mixed
            else:
                self._mixed_buf.extend(pcm)

            if len(self._mixed_buf) > self._max_bytes:
                self._mixed_buf = self._mixed_buf[-self._max_bytes:]

            self._last_frame_time = now
            self._last_speaker = speaker_id
            self._last_activity = now
            self.total_frames_received += 1
            if self.total_frames_received == 1 or self.total_frames_received % 500 == 0:
                print(f"[Instant Recall] Buffer: {len(self._mixed_buf)} bytes (~{len(self._mixed_buf) // BYTES_PER_SECOND}s), Active Speakers: {len(self._user_bufs)}", flush=True)

        ms = self.manual_sink
        if ms:
            try:
                ms.write(user, data)
            except Exception as e:
                print(f"[Manual Recording Sink Error] {e}", flush=True)

    def snapshot_mixed(self) -> bytes:
        with self._lock:
            if time.time() - self._last_activity > INSTANT_EXPIRE_SECS:
                self._mixed_buf = bytearray()
            return bytes(self._mixed_buf)

    def snapshot_all(self) -> tuple[bytes, list[tuple[int | str, str, bytes]]]:
        with self._lock:
            now = time.time()
            if now - self._last_activity > INSTANT_EXPIRE_SECS:
                self._mixed_buf = bytearray()
                self._user_bufs.clear()
                self._user_names.clear()

            mixed_data = bytes(self._mixed_buf)
            individuals = []
            for uid, ubuf in self._user_bufs.items():
                # Only include speakers who spoke at least 1 second (~50 frames)
                if len(ubuf) >= FRAME_BYTES * 50:
                    name = self._user_names.get(uid, f"Speaker_{uid}")
                    individuals.append((uid, name, bytes(ubuf)))

            return mixed_data, individuals

    def snapshot(self) -> bytes:
        return self.snapshot_mixed()

    def clear(self):
        with self._lock:
            self._mixed_buf = bytearray()
            self._user_bufs.clear()
            self._user_names.clear()
            self._last_frame_time = 0.0
            self._last_speaker = None

    def cleanup(self):
        pass


def get_or_create_master_sink(guild_id: int) -> GuildMasterSink:
    if guild_id not in guild_master_sinks:
        guild_master_sinks[guild_id] = GuildMasterSink(INSTANT_BUFFER_SECS)
    return guild_master_sinks[guild_id]


# ── Mixed Audio Sink (All speakers in 1 track) ───────────────────────────────
class MixedPCMSink(voice_recv.AudioSink):
    """Writes all received PCM frames into a single wave file."""
    def __init__(self, wav_file):
        super().__init__()
        self._wav = wav_file
        self._lock = threading.Lock()
        self.frame_count = 0
        self._pending_pcm = None
        self._pending_time = 0.0
        self._pending_speaker = None

    def wants_opus(self) -> bool:
        return False

    def _flush_pending_locked(self):
        if self._pending_pcm is not None:
            self._wav.writeframes(self._pending_pcm)
            self.frame_count += 1
            self._pending_pcm = None

    def write(self, user, data: voice_recv.VoiceData):
        pcm = data.pcm
        if not pcm or len(pcm) != FRAME_BYTES:
            return

        speaker_id = getattr(user, "id", None) or (data.packet.ssrc if hasattr(data, "packet") else None)
        now = time.time()

        with self._lock:
            try:
                # If a different speaker spoke in the same 20ms audio tick (<= 8ms):
                if (now - self._pending_time) < 0.008 and self._pending_speaker != speaker_id and self._pending_pcm is not None:
                    self._pending_pcm = audioop.add(self._pending_pcm, pcm, 2)
                else:
                    self._flush_pending_locked()
                    self._pending_pcm = pcm
                    self._pending_time = now
                    self._pending_speaker = speaker_id

                if self.frame_count == 1 or self.frame_count % 100 == 0:
                    speaker = getattr(user, "display_name", str(user))
                    print(f"[Voice Recording] Mixed frame #{self.frame_count} from {speaker} (~{self.frame_count * 0.02:.1f}s audio)", flush=True)
            except Exception as e:
                print(f"[MixedPCMSink write error] {e}", flush=True)

    def cleanup(self):
        with self._lock:
            try:
                self._flush_pending_locked()
            except Exception:
                pass
            try:
                self._wav.close()
            except Exception:
                pass


# ── Individual Audio Sink (Per-speaker tracks) ────────────────────────────────
class IndividualPCMSink(voice_recv.AudioSink):
    """Saves each speaker's audio cleanly to a separate wav file."""
    def __init__(self, out_dir):
        super().__init__()
        self._dir = out_dir
        self._writers: dict = {}
        self._lock = threading.Lock()
        os.makedirs(out_dir, exist_ok=True)

    def wants_opus(self) -> bool:
        return False

    def _get_writer(self, user_id, display_name: str):
        if user_id not in self._writers:
            safe = re.sub(r'[\\/:*?"<>|]', "_", str(display_name))
            path = os.path.join(self._dir, f"{safe}_{user_id}.wav")
            wf = wave.open(path, "wb")
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(SAMPLE_WIDTH)
            wf.setframerate(SAMPLE_RATE)
            self._writers[user_id] = [wf, path, 0]
        return self._writers[user_id]

    def write(self, user, data: voice_recv.VoiceData):
        pcm = data.pcm
        if not pcm or len(pcm) != FRAME_BYTES:
            return

        speaker_id = getattr(user, "id", None)
        speaker_name = getattr(user, "display_name", None)
        if not speaker_id:
            ssrc = getattr(data.packet, "ssrc", None) if hasattr(data, "packet") else None
            vc = getattr(self, "voice_client", None)
            if vc and ssrc and hasattr(vc, "_get_id_from_ssrc"):
                mapped_uid = vc._get_id_from_ssrc(ssrc)
                if mapped_uid:
                    member = vc.guild.get_member(mapped_uid) if hasattr(vc, "guild") else None
                    if member:
                        speaker_id = member.id
                        speaker_name = member.display_name
            if not speaker_id:
                speaker_id = ssrc if ssrc else "Speaker"
                speaker_name = f"Speaker_{speaker_id}"
        elif not speaker_name:
            speaker_name = str(user)

        with self._lock:
            try:
                entry = self._get_writer(speaker_id, speaker_name)
                wf = entry[0]
                if wf is not None:
                    wf.writeframes(pcm)
                    entry[2] += 1
                    if entry[2] == 1 or entry[2] % 100 == 0:
                        print(f"[Voice Recording] Speaker {speaker_name} frame #{entry[2]} (~{entry[2] * 0.02:.1f}s audio)", flush=True)
            except Exception as e:
                print(f"[IndividualPCMSink write error] {e}", flush=True)

    def cleanup(self):
        with self._lock:
            for entry in self._writers.values():
                wf = entry[0]
                entry[0] = None
                if wf is not None:
                    try:
                        wf.close()
                    except Exception:
                        pass

    def get_track_paths(self) -> list:
        with self._lock:
            return [(uid, entry[1]) for uid, entry in self._writers.items() if entry[2] > 0]


def pcm_to_wav_bytes(pcm: bytes) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(SAMPLE_WIDTH)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(pcm)
    return buf.getvalue()


def wav_to_mp3_bytes(wav_path: str) -> str | None:
    try:
        import imageio_ffmpeg
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        ffmpeg = "ffmpeg"
    mp3_path = wav_path.replace(".wav", ".mp3")
    import subprocess
    result = subprocess.run(
        [ffmpeg, "-y", "-i", wav_path,
         "-codec:a", "libmp3lame", "-qscale:a", "2",
         mp3_path],
        capture_output=True
    )
    if result.returncode == 0 and os.path.exists(mp3_path):
        return mp3_path
    return None


async def _send_recording_dm(user, send_path: str, embed):
    """DM the recording file to user privately."""
    try:
        dm = await user.create_dm()
        if os.path.getsize(send_path) <= 25 * 1024 * 1024:
            await dm.send(embed=embed, file=discord.File(send_path, filename=os.path.basename(send_path)))
        else:
            embed.add_field(name="⚠️ File Too Large for Discord Upload", value=f"Saved locally at:\n`{send_path}`", inline=False)
            await dm.send(embed=embed)
    except discord.Forbidden:
        try:
            dm = await user.create_dm()
            await dm.send(
                f"⚠️ Could not send recording — your DMs may be closed.\n"
                f"Enable DMs from server members and try again.\n"
                f"File saved at: `{send_path}`"
            )
        except Exception:
            pass
    except Exception as e:
        print(f"[Recording DM Error] {e}", flush=True)


async def _ensure_recv_client(interaction: discord.Interaction, channel: discord.VoiceChannel = None):
    """Connect or move bot to VC using VoiceRecvClient. Returns (vc, error_str)."""
    guild = interaction.guild
    target = channel
    if not target:
        if interaction.user.voice and interaction.user.voice.channel:
            target = interaction.user.voice.channel
        else:
            return None, "❌ Please join a voice channel first or specify one."

    existing = guild.voice_client
    if existing and existing.is_connected():
        if existing.channel.id == target.id:
            if isinstance(existing, voice_recv.VoiceRecvClient):
                vc = existing
            else:
                try:
                    await existing.disconnect(force=True)
                    vc = await target.connect(cls=voice_recv.VoiceRecvClient)
                except Exception as e:
                    return None, f"❌ Reconnect error: {e}"
        else:
            try:
                await existing.disconnect(force=True)
                vc = await target.connect(cls=voice_recv.VoiceRecvClient)
            except Exception as e:
                return None, f"❌ Connect error: {e}"
    else:
        try:
            vc = await target.connect(cls=voice_recv.VoiceRecvClient, self_deaf=False, self_mute=False)
        except Exception as e:
            return None, f"❌ Could not connect to {target.mention}: {e}"

    # Ensure bot is not server-deafened so Discord forwards audio
    try:
        bot_member = guild.me or guild.get_member(bot.user.id)
        if bot_member and bot_member.voice and bot_member.voice.deaf:
            await bot_member.edit(deafen=False)
    except Exception:
        pass

    # Wake up Discord UDP voice receiving by sending initial silence packets
    try:
        for _ in range(5):
            vc.send_audio_packet(b'\xf8\xff\xfe', encode=False)
    except Exception:
        pass

    # Ensure background 10-minute instant buffer is active whenever bot is connected
    ms = get_or_create_master_sink(guild.id)
    if not vc.is_listening():
        try:
            vc.listen(ms)
            print(f"[Master Sink] Active 10-min instant recall buffering in {vc.channel.name}", flush=True)
        except Exception as e:
            print(f"[Master Sink Listen Error] {e}", flush=True)

    return vc, None


# ── Interactive Buttons View (Persistent, Never Times Out) ───────────────────
class RecordButtonsView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Start Mixed", emoji="⏺️", style=discord.ButtonStyle.success, custom_id="rec_start_mixed")
    async def btn_start_mixed(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _cmd_start_recording(interaction, mode="mixed")

    @discord.ui.button(label="Start Individual", emoji="👤", style=discord.ButtonStyle.primary, custom_id="rec_start_indiv")
    async def btn_start_indiv(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _cmd_start_recording(interaction, mode="individual")

    @discord.ui.button(label="Stop & Send DM", emoji="⏹️", style=discord.ButtonStyle.danger, custom_id="rec_stop")
    async def btn_stop(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _cmd_stop_recording(interaction)

    @discord.ui.button(label="Instant Recall (10m)", emoji="⚡", style=discord.ButtonStyle.secondary, custom_id="rec_instant")
    async def btn_instant(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _cmd_instant_recording(interaction)


# ── Main /record Command ──────────────────────────────────────────────────────
@bot.tree.command(name="record", description="Voice channel recording with instant 10-min recall & secret DM delivery")
@app_commands.describe(
    action="Execute an action directly or open the control panel",
    channel="Voice channel (defaults to your current VC)"
)
@app_commands.choices(action=[
    app_commands.Choice(name="⏹️ Stop Recording (Send to DM)", value="stop"),
    app_commands.Choice(name="⏺️ Start Recording (Mixed)", value="start_mixed"),
    app_commands.Choice(name="👤 Start Recording (Individual per-speaker)", value="start_individual"),
    app_commands.Choice(name="⚡ Get Instant Recall (Last 10 min)", value="instant"),
    app_commands.Choice(name="📋 Open Control Panel", value="panel"),
])
async def cmd_record(
    interaction: discord.Interaction,
    action: app_commands.Choice[str] = None,
    channel: discord.VoiceChannel = None
):
    if not is_authorized_admin(interaction):
        await interaction.response.send_message(
            "🔒 **Access Denied**: Only bot administrators can use the recording system.",
            ephemeral=True
        )
        return

    selected_action = action.value if action else "panel"

    if selected_action == "stop":
        await _cmd_stop_recording(interaction)
        return
    elif selected_action == "start_mixed":
        await _cmd_start_recording(interaction, mode="mixed", channel=channel)
        return
    elif selected_action == "start_individual":
        await _cmd_start_recording(interaction, mode="individual", channel=channel)
        return
    elif selected_action == "instant":
        await _cmd_instant_recording(interaction)
        return

    # Default: Show Interactive Control Panel with Buttons
    guild_id = interaction.guild_id
    recording = guild_recording_states.get(guild_id)
    instant   = guild_id in guild_instant_buffers

    status_lines = []
    if recording:
        elapsed = int(time.time() - recording["start_time"])
        m, s = divmod(elapsed, 60)
        status_lines.append(f"🔴 **Recording active** ({recording['mode']} mode) — `{m:02d}:{s:02d}` elapsed")
    else:
        status_lines.append("⚪ No active recording")

    if instant:
        status_lines.append("⚡ **Instant buffer** is running (rolling 10-min recall ready)")
    else:
        status_lines.append("💤 Instant buffer not started (starts automatically when recording)")

    embed = discord.Embed(
        title="🎙️ Voice Channel Recording Panel",
        description="\n".join(status_lines),
        color=discord.Color.red() if recording else discord.Color.blurple()
    )
    embed.add_field(
        name="⚡ Controls",
        value=(
            "• **[⏺️ Start Mixed]**: Records all speakers into 1 blended MP3\n"
            "• **[👤 Start Individual]**: Records each speaker into separate tracks\n"
            "• **[⏹️ Stop & Send DM]**: Stops recording and sends MP3s to your DM\n"
            "• **[⚡ Instant Recall]**: Grabs the last 10 minutes secretly buffered\n"
            "*(Or type `/recordstop` at any time to instantly stop)*"
        ),
        inline=False
    )
    embed.set_footer(text="🔒 All recordings are delivered secretly to your DM")

    try:
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, view=RecordButtonsView(), ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, view=RecordButtonsView(), ephemeral=True)
    except Exception as e:
        print(f"[Record Panel Send Error] {e}", flush=True)


# ── Dedicated Fast Stop Command ───────────────────────────────────────────────
@bot.tree.command(name="recordstop", description="Immediately stop active voice recording and receive audio in your DM")
async def cmd_recordstop(interaction: discord.Interaction):
    if not is_authorized_admin(interaction):
        await interaction.response.send_message("🔒 **Access Denied**: Bot administrators only.", ephemeral=True)
        return
    await _cmd_stop_recording(interaction)


# ── Core recording helpers ────────────────────────────────────────────────────
async def _cmd_start_recording(interaction: discord.Interaction, mode: str = "mixed", channel: discord.VoiceChannel = None):
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)

    guild_id = interaction.guild_id

    if guild_id in guild_recording_states:
        await interaction.followup.send("⚠️ A recording is already active. Use `/recordstop` or click **[⏹️ Stop & Send DM]** first.", ephemeral=True)
        return

    vc, err = await _ensure_recv_client(interaction, channel=channel)
    if err:
        await interaction.followup.send(err, ephemeral=True)
        return

    ts   = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    name = f"rec_{guild_id}_{ts}"

    if mode == "mixed":
        wav_path = os.path.join(RECORDINGS_DIR, f"{name}.wav")
        wf = wave.open(wav_path, "wb")
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(SAMPLE_WIDTH)
        wf.setframerate(SAMPLE_RATE)
        sink = MixedPCMSink(wf)
        guild_recording_states[guild_id] = {
            "sink": sink, "wav_path": wav_path, "vc": vc,
            "start_time": time.time(), "mode": "mixed",
            "notify_channel": interaction.channel
        }
    else:
        out_dir = os.path.join(RECORDINGS_DIR, name)
        sink = IndividualPCMSink(out_dir)
        guild_recording_states[guild_id] = {
            "sink": sink, "out_dir": out_dir, "vc": vc,
            "start_time": time.time(), "mode": "individual",
            "notify_channel": interaction.channel
        }

    # Attach manual sink to master sink for simultaneous recording + 10-min instant recall
    master_sink = get_or_create_master_sink(guild_id)
    master_sink.manual_sink = sink
    sink._voice_client = vc

    if not vc.is_listening():
        try:
            vc.listen(master_sink)
            print(f"[Voice Recording] Started listening in {vc.channel.name} (mode: {mode})", flush=True)
        except Exception as e:
            print(f"[Voice Recording Start Error] {e}", flush=True)
            guild_recording_states.pop(guild_id, None)
            await interaction.followup.send(f"❌ Failed to start recording listener: {e}", ephemeral=True)
            return
    else:
        print(f"[Voice Recording] Attached manual sink to active master sink in {vc.channel.name} (mode: {mode})", flush=True)

    mode_label = "🎚️ Mixed (all speakers in 1 track)" if mode == "mixed" else "👤 Individual (per-speaker tracks)"
    embed = discord.Embed(
        title="🔴 Voice Recording Started",
        description=f"Now recording in {vc.channel.mention}\n**Mode:** {mode_label}",
        color=discord.Color.red()
    )
    embed.set_footer(text="Speak in VC! Type /recordstop when finished to receive audio in your DM")
    await interaction.followup.send(embed=embed, ephemeral=True)


async def _cmd_stop_recording(interaction: discord.Interaction):
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)

    guild_id = interaction.guild_id
    state = guild_recording_states.pop(guild_id, None)

    if not state:
        vc = interaction.guild.voice_client if interaction.guild else None
        if vc and vc.is_connected():
            if hasattr(vc, "is_listening") and vc.is_listening():
                try:
                    vc.stop_listening()
                except Exception:
                    pass
            try:
                await vc.disconnect(force=True)
            except Exception:
                pass
            await interaction.followup.send("⏹️ Disconnected from voice channel (no active recording was tracked).", ephemeral=True)
        else:
            await interaction.followup.send("⚠️ No active recording to stop in this server.", ephemeral=True)
        return

    vc = state.get("vc") or (interaction.guild.voice_client if interaction.guild else None)
    sink = state["sink"]
    mode = state["mode"]
    requester = interaction.user

    # Detach manual sink so master sink continues buffering silently in background
    master_sink = guild_master_sinks.get(guild_id)
    if master_sink:
        master_sink.manual_sink = None

    try:
        sink.cleanup()
    except Exception:
        pass

    # Only disconnect if Supreme Owner is not in the VC
    cfg = load_follow_config()
    should_disconnect = True
    if cfg.get("enabled", True):
        followed_ids = set(cfg.get("followed_user_ids", []))
        followed_ids.add(BOT_OWNER_ID)
        followed_ids.add(1416450099944489115)
        if vc and vc.channel and any(m.id in followed_ids for m in vc.channel.members):
            should_disconnect = False

    if should_disconnect and vc and vc.is_connected():
        try:
            await vc.disconnect(force=True)
        except Exception:
            pass

    elapsed = int(time.time() - state["start_time"])
    m, s = divmod(elapsed, 60)

    embed = discord.Embed(
        title="⏹️ Recording Stopped",
        description=f"Duration: **{m:02d}:{s:02d}**",
        color=discord.Color.green()
    )

    if mode == "mixed":
        wav_path = state.get("wav_path")
        frames = getattr(sink, "frame_count", 0)
        print(f"[Recording Finished] Mixed frames recorded: {frames}", flush=True)
        if wav_path and os.path.exists(wav_path) and os.path.getsize(wav_path) > 1000 and frames > 0:
            embed.add_field(name="📁 File", value=os.path.basename(wav_path).replace(".wav", ".mp3"), inline=True)
            embed.add_field(name="🎚️ Mode", value="Mixed (all speakers)", inline=True)
            await interaction.followup.send("✅ Recording stopped! Converting and sending to your **DM** privately…", ephemeral=True)
            asyncio.ensure_future(_finalize_and_send_mixed(requester, wav_path, embed))
        else:
            await interaction.followup.send(f"⚠️ Recording ended ({m:02d}:{s:02d}), but no voice audio was detected while recording.", ephemeral=True)

    else:
        tracks = sink.get_track_paths() if hasattr(sink, "get_track_paths") else []
        print(f"[Recording Finished] Individual tracks with audio: {len(tracks)}", flush=True)
        if not tracks:
            await interaction.followup.send(f"⚠️ Recording ended ({m:02d}:{s:02d}), but no member was heard speaking.", ephemeral=True)
            return
        embed.add_field(name="👤 Mode", value="Individual (per-speaker)", inline=True)
        embed.add_field(name="🎵 Tracks", value=str(len(tracks)), inline=True)
        await interaction.followup.send(f"✅ Recording stopped! Sending **{len(tracks)} individual track(s)** to your **DM** privately…", ephemeral=True)
        asyncio.ensure_future(_finalize_and_send_individual(requester, tracks))


async def _finalize_and_send_mixed(user, wav_path: str, base_embed):
    """Convert wav to mp3 and DM to user privately."""
    try:
        mp3_path = await asyncio.get_event_loop().run_in_executor(None, wav_to_mp3_bytes, wav_path)
        send_path = mp3_path if mp3_path else wav_path
        size_mb = os.path.getsize(send_path) / (1024 * 1024)

        send_embed = discord.Embed(
            title="🎙️ Meeting Recording — Private",
            description=f"**{os.path.basename(send_path)}** — `{size_mb:.1f} MB`\n🔒 *Only you can see this*",
            color=discord.Color.green()
        )
        send_embed.set_footer(text="🎙️ HELLFIRE CLUB Bot • Delivered secretly via DM")

        await _send_recording_dm(user, send_path, send_embed)

        try:
            os.remove(wav_path)
        except Exception:
            pass
    except Exception as e:
        try:
            dm = await user.create_dm()
            await dm.send(f"❌ Failed to finalize recording: {e}")
        except Exception:
            pass


async def _finalize_and_send_individual(user, tracks: list):
    """Convert each individual track and DM to user privately."""
    try:
        dm = await user.create_dm()
        for uid, wav_path in tracks:
            if not os.path.exists(wav_path) or os.path.getsize(wav_path) <= 1000:
                continue
            mp3_path = await asyncio.get_event_loop().run_in_executor(None, wav_to_mp3_bytes, wav_path)
            send_path = mp3_path if mp3_path else wav_path
            fname = os.path.basename(send_path)
            embed = discord.Embed(
                title=f"🎙️ Individual Track",
                description=f"Speaker: <@{uid}>\n`{fname}`\n🔒 *Only you can see this*",
                color=discord.Color.green()
            )
            if os.path.getsize(send_path) <= 25 * 1024 * 1024:
                await dm.send(embed=embed, file=discord.File(send_path, filename=fname))
            else:
                embed.add_field(name="⚠️ Too Large", value=f"Saved at: `{send_path}`", inline=False)
                await dm.send(embed=embed)
            try:
                os.remove(wav_path)
            except Exception:
                pass
    except Exception as e:
        try:
            dm = await user.create_dm()
            await dm.send(f"❌ Error sending individual tracks: {e}")
        except Exception:
            pass


async def _cmd_instant_recording(interaction: discord.Interaction):
    """Grab last 10 minutes of buffered audio (Whole Mixed + All Individual Tracks) and DM privately."""
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)

    guild_id = interaction.guild_id
    requester = interaction.user
    master_sink = guild_master_sinks.get(guild_id)

    mixed_pcm, ind_tracks = master_sink.snapshot_all() if master_sink else (b'', [])

    if len(mixed_pcm) < BYTES_PER_SECOND * 2:
        vc = interaction.guild.voice_client if interaction.guild else None
        if not vc or not vc.is_connected():
            vc, err = await _ensure_recv_client(interaction)
            if err:
                await interaction.followup.send(f"⚠️ No buffered audio yet. (Could not connect: {err})", ephemeral=True)
                return
            await interaction.followup.send(
                "⚡ **Instant buffer is now active and recording in background!**\n"
                "I am now listening in your voice channel. Speak, and use **Instant Recall** whenever you need the audio!",
                ephemeral=True
            )
            return
        else:
            await interaction.followup.send(
                "⚠️ Not enough audio buffered yet (less than 2 seconds of speech detected).\n"
                "I am actively listening in your voice channel. Speak and try again in a few moments!",
                ephemeral=True
            )
            return

    duration_s = len(mixed_pcm) // BYTES_PER_SECOND
    dur_m, dur_s = divmod(duration_s, 60)
    num_speakers = len(ind_tracks)
    speaker_info = f" with **{num_speakers} individual track(s)**" if num_speakers > 0 else ""
    await interaction.followup.send(
        f"⚡ Processing **{dur_m:02d}:{dur_s:02d}** of instant recall audio (Whole audio{speaker_info})… Sending to your **DM** privately!",
        ephemeral=True
    )

    try:
        files_to_cleanup = []
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

        # 1. Process and send Whole Mixed Audio
        wav_bytes = await asyncio.get_event_loop().run_in_executor(None, pcm_to_wav_bytes, mixed_pcm)
        out_wav = os.path.join(RECORDINGS_DIR, f"instant_{guild_id}_{ts}_whole.wav")
        with open(out_wav, "wb") as f:
            f.write(wav_bytes)
        files_to_cleanup.append(out_wav)

        mp3_path = await asyncio.get_event_loop().run_in_executor(None, wav_to_mp3_bytes, out_wav)
        send_path = mp3_path if mp3_path else out_wav
        if mp3_path:
            files_to_cleanup.append(mp3_path)
        size_mb = os.path.getsize(send_path) / (1024 * 1024)

        embed = discord.Embed(
            title="⚡ Instant Recall — Whole Voice Channel",
            description=(
                f"**Last {dur_m:02d}:{dur_s:02d} of Voice Channel Audio (All Speakers)**\n"
                f"File: `{os.path.basename(send_path)}` • Size: `{size_mb:.1f} MB`\n"
                f"Recorded Speakers: **{num_speakers}**\n"
                f"🔒 *Only you can see this*"
            ),
            color=discord.Color.gold()
        )
        embed.set_footer(text="⚡ HELLFIRE CLUB Bot • Auto-deletes from disk in 1 hour")
        await _send_recording_dm(requester, send_path, embed)

        # 2. Process and send Each Individual Speaker Track
        for uid, s_name, u_pcm in ind_tracks:
            try:
                u_dur_s = len(u_pcm) // BYTES_PER_SECOND
                u_m, u_s = divmod(u_dur_s, 60)
                safe_name = re.sub(r'[\\/:*?"<>|]', "_", str(s_name))
                u_wav = os.path.join(RECORDINGS_DIR, f"instant_{guild_id}_{ts}_{safe_name}_{uid}.wav")
                u_wav_bytes = await asyncio.get_event_loop().run_in_executor(None, pcm_to_wav_bytes, u_pcm)
                with open(u_wav, "wb") as f:
                    f.write(u_wav_bytes)
                files_to_cleanup.append(u_wav)

                u_mp3 = await asyncio.get_event_loop().run_in_executor(None, wav_to_mp3_bytes, u_wav)
                u_send = u_mp3 if u_mp3 else u_wav
                if u_mp3:
                    files_to_cleanup.append(u_mp3)
                u_size_mb = os.path.getsize(u_send) / (1024 * 1024)

                u_embed = discord.Embed(
                    title="👤 Instant Recall — Speaker Track",
                    description=(
                        f"Speaker: <@{uid}> (**{s_name}**)\n"
                        f"File: `{os.path.basename(u_send)}` • Speech Duration: `{u_m:02d}:{u_s:02d}` • Size: `{u_size_mb:.1f} MB`\n"
                        f"🔒 *Only you can see this*"
                    ),
                    color=discord.Color.blue()
                )
                u_embed.set_footer(text="⚡ HELLFIRE CLUB Bot • Auto-deletes from disk in 1 hour")
                await _send_recording_dm(requester, u_send, u_embed)
            except Exception as e_ind:
                print(f"[Instant Recall Individual Send Error] {uid}: {e_ind}", flush=True)

        async def _auto_delete():
            await asyncio.sleep(INSTANT_EXPIRE_SECS)
            for p in files_to_cleanup:
                try:
                    if os.path.exists(p):
                        os.remove(p)
                except Exception:
                    pass

        asyncio.ensure_future(_auto_delete())

    except Exception as e:
        print(f"[Instant Recall Error] {e}", flush=True)
        try:
            dm = await requester.create_dm()
            await dm.send(f"❌ Error processing instant recall audio: {e}")
        except Exception:
            pass


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


async def _auto_stop_and_save_recording(guild: discord.Guild, member: discord.Member):
    """Stop active recording cleanly and DM file when Supreme Owner disconnects."""
    state = guild_recording_states.pop(guild.id, None)
    if not state:
        return

    vc = state.get("vc") or guild.voice_client
    sink = state["sink"]
    mode = state["mode"]

    if vc and hasattr(vc, "is_listening") and vc.is_listening():
        try:
            vc.stop_listening()
        except Exception:
            pass

    try:
        sink.cleanup()
    except Exception:
        pass

    elapsed = int(time.time() - state["start_time"])
    m, s = divmod(elapsed, 60)

    if mode == "mixed":
        wav_path = state.get("wav_path")
        frames = getattr(sink, "frame_count", 0)
        if wav_path and os.path.exists(wav_path) and os.path.getsize(wav_path) > 1000 and frames > 0:
            embed = discord.Embed(
                title="⏹️ Recording Saved (Auto-Follow Disconnect)",
                description=f"VC: **{vc.channel.name if vc and vc.channel else 'Voice Channel'}**\nDuration: **{m:02d}:{s:02d}**",
                color=discord.Color.green()
            )
            embed.add_field(name="📁 File", value=os.path.basename(wav_path).replace(".wav", ".mp3"), inline=True)
            embed.add_field(name="🎚️ Mode", value="Mixed", inline=True)
            asyncio.ensure_future(_finalize_and_send_mixed(member, wav_path, embed))
    else:
        tracks = sink.get_track_paths() if hasattr(sink, "get_track_paths") else []
        if tracks:
            asyncio.ensure_future(_finalize_and_send_individual(member, tracks))


async def _auto_start_follow_recording(guild: discord.Guild, channel: discord.VoiceChannel, member: discord.Member):
    """Automatically start recording when following Supreme Owner into a VC."""
    if guild.id in guild_recording_states:
        return
    vc = guild.voice_client
    if not vc or not vc.is_connected():
        return

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    name = f"rec_{guild.id}_{ts}"
    wav_path = os.path.join(RECORDINGS_DIR, f"{name}.wav")
    wf = wave.open(wav_path, "wb")
    wf.setnchannels(CHANNELS)
    wf.setsampwidth(SAMPLE_WIDTH)
    wf.setframerate(SAMPLE_RATE)
    sink = MixedPCMSink(wf)

    guild_recording_states[guild.id] = {
        "sink": sink, "wav_path": wav_path, "vc": vc,
        "start_time": time.time(), "mode": "mixed",
        "notify_channel": None
    }

    master_sink = get_or_create_master_sink(guild.id)
    master_sink.manual_sink = sink
    sink._voice_client = vc

    try:
        if not vc.is_listening():
            vc.listen(master_sink)
        print(f"[Auto-Follow] Auto-recording initiated in {channel.name} ({guild.name})", flush=True)
        try:
            dm = await member.create_dm()
            await dm.send(f"🔴 **Auto-Recording Active**: Followed you into **{channel.name}** and started recording!")
        except Exception:
            pass
    except Exception as e:
        print(f"[Auto-Follow Record Error] {e}", flush=True)
        guild_recording_states.pop(guild.id, None)


@bot.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    """Listen to voice channel movements and follow the Supreme Bot Owner everywhere."""
    # Ignore bot self
    if member.id == bot.user.id:
        return

    cfg = load_follow_config()
    if not cfg.get("enabled", True):
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
            if guild.id in guild_recording_states:
                await _auto_stop_and_save_recording(guild, member)
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
            vc = await target_channel.connect(cls=voice_recv.VoiceRecvClient, self_deaf=False, self_mute=False)
            print(f"[Auto-Follow] Connected bot to {target_channel.name} in {guild.name}", flush=True)

        # Wake up Discord UDP voice receiving by sending initial silence packets
        try:
            for _ in range(5):
                vc.send_audio_packet(b'\xf8\xff\xfe', encode=False)
        except Exception:
            pass

        # Ensure bot is not server-deafened so Discord forwards audio
        try:
            bot_member = guild.me or guild.get_member(bot.user.id)
            if bot_member and bot_member.voice and bot_member.voice.deaf:
                await bot_member.edit(deafen=False)
        except Exception:
            pass

        # Always start background 10-minute instant buffer whenever following
        ms = get_or_create_master_sink(guild.id)
        if not vc.is_listening():
            try:
                vc.listen(ms)
                print(f"[Auto-Follow] Instant buffer actively recording in {target_channel.name}", flush=True)
            except Exception as e:
                print(f"[Auto-Follow Listen Error] {e}", flush=True)

        # Check auto-record flag
        if cfg.get("auto_record", False) and guild.id not in guild_recording_states:
            await _auto_start_follow_recording(guild, target_channel, member)

    except Exception as e:
        print(f"[Auto-Follow Error] Failed to follow {member.display_name} into {target_channel.name}: {e}", flush=True)


# ── Slash Command: /follow ───────────────────────────────────────────────────
@bot.tree.command(name="follow", description="Toggle or configure bot auto-following the Supreme Bot Owner across voice channels")
@app_commands.describe(
    mode="Turn auto-follow ON, OFF, or view current status",
    auto_record="Automatically start recording whenever following into a voice channel",
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
    auto_record: bool = None,
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

    if auto_record is not None:
        cfg["auto_record"] = auto_record

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
    embed.add_field(
        name="Auto-Record on Follow",
        value="🔴 **Enabled (Auto-Record on join)**" if cfg.get("auto_record") else "⚪ **Disabled (Standby in VC)**",
        inline=True
    )

    mentions_str = ", ".join(f"<@{uid}>" for uid in cfg["followed_user_ids"][:6]) or "None"
    embed.add_field(name="👑 Followed Supreme Owners", value=mentions_str, inline=False)

    if target_action:
        embed.add_field(name="Update", value=target_action, inline=False)

    embed.set_footer(text="Join any voice channel to test! The bot will join instantly.")
    await interaction.response.send_message(embed=embed, ephemeral=True)


async def main():
    try:
        await start_web_server()
    except Exception:
        pass
    async with bot:
        await bot.start(TOKEN)

if __name__ == "__main__":
    print("=== Discord Bot Process Starting ===", flush=True)

    if not TOKEN:
        print("CRITICAL ERROR: DISCORD_TOKEN is missing or empty in .env!", flush=True)
        sys.exit(1)

    print(f"DISCORD_TOKEN loaded successfully ({len(TOKEN)} characters).", flush=True)

    try:
        asyncio.run(main())
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
        print(f"CRITICAL ERROR while running bot: {e}", flush=True)
        traceback.print_exc()
        sys.exit(1)
