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
from discord.ext import commands
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

class ChallengeResponseView(discord.ui.View):
    def __init__(self, challenger_id: int, target_id: int, shout: str = "COME LETS FIGHT"):
        super().__init__(timeout=120)
        self.challenger_id = challenger_id
        self.target_id = target_id
        self.shout = shout

    @discord.ui.button(label="⚔️ Accept Battle", style=discord.ButtonStyle.danger, custom_id="challenge_accept")
    async def accept_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.target_id:
            await interaction.response.send_message("❌ Only the challenged opponent can accept this battle!", ephemeral=True)
            return

        for child in self.children:
            child.disabled = True

        embed = interaction.message.embeds[0] if interaction.message.embeds else discord.Embed()
        embed.color = discord.Color.dark_gold()
        embed.title = "⚔️ BATTLE ACCEPTED • FIGHT IN PROGRESS! 💥"
        embed.set_footer(text="Hellfire Combat Arena • Challenge Accepted")

        result_content = (
            f"### 🔥 CHALLENGE ACCEPTED! 🥊\n"
            f"{interaction.user.mention} stepped into the ring to fight <@{self.challenger_id}>!\n"
            f"> 💥 **LET THE BATTLE COMMENCE!** ⚔️"
        )
        await interaction.response.edit_message(content=result_content, embed=embed, view=self)

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
@bot.event
async def on_message(message: discord.Message):
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

    # Check if any mentioned user is in our target_ids for auto-reactions
    target_ids = get_target_user_ids()
    if target_ids and any(user.id in target_ids for user in message.mentions):
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

@bot.tree.command(name="challenge", description="Challenge another server member to an animated battle duel with a fighting shout card")
@app_commands.describe(
    target="The member you want to challenge to a fight",
    source="The challenger (defaults to you)",
    shout="Custom battle shout (defaults to: 'COME LETS FIGHT')"
)
async def cmd_challenge(interaction: discord.Interaction, target: discord.Member, source: discord.Member = None, shout: str = "COME LETS FIGHT"):
    actor = source if source is not None else interaction.user
    if is_duplicate_action(interaction.channel_id, "challenge", actor.id, target.id):
        if not interaction.response.is_done():
            await interaction.response.send_message("⚠️ A challenge was just issued! Please wait a moment.", ephemeral=True)
        return

    content, embed = make_challenge_payload(actor, target, shout)
    view = ChallengeResponseView(actor.id, target.id, shout)
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


# ==============================================================================
# 13. VOICE CHANNEL RECORDING SYSTEM
# ==============================================================================
RECORDINGS_DIR = os.path.join(os.path.dirname(__file__), "recordings")
os.makedirs(RECORDINGS_DIR, exist_ok=True)

# Per-guild recording state
guild_recording_states: dict = {}       # guild_id -> {"sink": sink, "wav_path": str, "channel": vc, "start_time": float, "mode": str, "notify_channel": TextChannel}
guild_instant_buffers: dict = {}        # guild_id -> {"buffer": deque of (timestamp, user_id, pcm_bytes), "vc": VoiceRecvClient}
guild_instant_tasks: dict = {}          # guild_id -> asyncio.Task (buffer watcher)

INSTANT_BUFFER_SECS  = 600   # Keep rolling 10 min of audio in memory buffer
INSTANT_EXPIRE_SECS  = 3600  # Auto-delete instant recordings after 1 hour
SAMPLE_RATE          = 48000
CHANNELS             = 2
SAMPLE_WIDTH         = 2     # bytes (16-bit PCM)
BYTES_PER_SECOND     = SAMPLE_RATE * CHANNELS * SAMPLE_WIDTH  # 192000

# ── Custom Sink: mixes all speakers into one PCM byte stream ──────────────────
class MixedPCMSink(voice_recv.AudioSink):
    """Writes all received PCM frames into a single wave file (mixed)."""
    def __init__(self, wav_file):
        super().__init__()
        self._wav = wav_file
        self._lock = threading.Lock()

    def wants_opus(self) -> bool:
        return False  # We want decoded PCM

    def write(self, user, data: voice_recv.VoiceData):
        pcm = data.pcm
        if pcm:
            with self._lock:
                self._wav.writeframes(pcm)

    def cleanup(self):
        try:
            self._wav.close()
        except Exception:
            pass


# ── Custom Sink: saves individual speaker tracks ──────────────────────────────
class IndividualPCMSink(voice_recv.AudioSink):
    """Saves each speaker's audio to a separate wav file."""
    def __init__(self, out_dir):
        super().__init__()
        self._dir = out_dir
        self._writers: dict = {}  # user_id -> wave.Wave_write
        self._lock = threading.Lock()
        os.makedirs(out_dir, exist_ok=True)

    def wants_opus(self) -> bool:
        return False

    def _get_writer(self, user_id: int, display_name: str):
        if user_id not in self._writers:
            safe = re.sub(r'[\\/:*?"<>|]', "_", display_name)
            path = os.path.join(self._dir, f"{safe}_{user_id}.wav")
            wf = wave.open(path, "wb")
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(SAMPLE_WIDTH)
            wf.setframerate(SAMPLE_RATE)
            self._writers[user_id] = (wf, path)
        return self._writers[user_id]

    def write(self, user, data: voice_recv.VoiceData):
        pcm = data.pcm
        if not pcm or user is None:
            return
        with self._lock:
            wf, _ = self._get_writer(user.id, getattr(user, "display_name", str(user.id)))
            wf.writeframes(pcm)

    def cleanup(self):
        with self._lock:
            for wf, _ in self._writers.values():
                try:
                    wf.close()
                except Exception:
                    pass

    def get_track_paths(self) -> list:
        with self._lock:
            return [(uid, path) for uid, (_, path) in self._writers.items()]


# ── Custom Sink: Rolling buffer (for instant recall) ─────────────────────────
class RollingBufferSink(voice_recv.AudioSink):
    """Keeps a rolling 10-minute ring buffer of mixed audio."""
    def __init__(self, max_seconds: int = INSTANT_BUFFER_SECS):
        super().__init__()
        self._max_bytes = max_seconds * BYTES_PER_SECOND
        self._buf = bytearray()
        self._lock = threading.Lock()

    def wants_opus(self) -> bool:
        return False

    def write(self, user, data: voice_recv.VoiceData):
        pcm = data.pcm
        if not pcm:
            return
        with self._lock:
            self._buf.extend(pcm)
            if len(self._buf) > self._max_bytes:
                # Keep only last max_bytes
                self._buf = self._buf[-self._max_bytes:]

    def snapshot(self) -> bytes:
        """Return a copy of the current buffer."""
        with self._lock:
            return bytes(self._buf)

    def cleanup(self):
        with self._lock:
            self._buf = bytearray()


def pcm_to_wav_bytes(pcm: bytes) -> bytes:
    """Wrap raw PCM bytes in a WAV container and return as bytes."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(SAMPLE_WIDTH)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(pcm)
    return buf.getvalue()


def wav_to_mp3_bytes(wav_path: str) -> str | None:
    """Convert wav file to mp3 using ffmpeg. Returns mp3 path or None."""
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


async def _send_audio_file(channel, file_path: str, embed: discord.Embed, label: str = "recording"):
    """Send audio file to channel. Converts to mp3 if wav is too large."""
    size = os.path.getsize(file_path)
    max_size = 25 * 1024 * 1024  # 25 MB Discord limit

    send_path = file_path
    if size > max_size and file_path.endswith(".wav"):
        mp3 = await asyncio.get_event_loop().run_in_executor(None, wav_to_mp3_bytes, file_path)
        if mp3:
            send_path = mp3
            size = os.path.getsize(mp3)

    if os.path.getsize(send_path) > max_size:
        embed.add_field(
            name="⚠️ File Too Large",
            value=f"Recording is **{size / (1024*1024):.1f} MB** — too large for Discord upload (25 MB limit).\n"
                  f"File saved locally at:\n`{send_path}`",
            inline=False
        )
        await channel.send(embed=embed)
        return

    await channel.send(
        embed=embed,
        file=discord.File(send_path, filename=os.path.basename(send_path))
    )


# ── Ensure VoiceRecvClient is used when connecting ───────────────────────────
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
                return existing, None
            # Wrong type — disconnect and reconnect with recv client
            await existing.disconnect(force=True)
        else:
            await existing.disconnect(force=True)

    try:
        vc = await target.connect(cls=voice_recv.VoiceRecvClient)
        return vc, None
    except Exception as e:
        return None, f"❌ Could not connect to {target.mention}: {e}"


# ── /record slash command ─────────────────────────────────────────────────────
class RecordActionSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="⏺️  Start Recording (Mixed)",      value="start_mixed",   description="Record all speakers into one audio file"),
            discord.SelectOption(label="👤  Start Recording (Individual)", value="start_individual", description="Record each speaker in a separate audio track"),
            discord.SelectOption(label="⏹️  Stop Recording",               value="stop",          description="Stop and get the recording file"),
            discord.SelectOption(label="⚡  Get Instant (last 10 min)",    value="instant",       description="Get the last 10 minutes of VC audio"),
        ]
        super().__init__(placeholder="Choose a recording action…", options=options, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        choice = self.values[0]
        guild_id = interaction.guild_id
        await interaction.response.defer(ephemeral=True)

        if choice in ("start_mixed", "start_individual"):
            await _cmd_start_recording(interaction, mode=choice.replace("start_", ""))

        elif choice == "stop":
            await _cmd_stop_recording(interaction)

        elif choice == "instant":
            await _cmd_instant_recording(interaction)


class RecordView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)
        self.add_item(RecordActionSelect())


@bot.tree.command(name="record", description="Record the voice channel — mixed or per-speaker. Includes instant 10-min recall.")
@app_commands.describe(channel="Voice channel to record (defaults to your current VC)")
async def cmd_record(interaction: discord.Interaction, channel: discord.VoiceChannel = None):
    if not is_authorized_admin(interaction):
        await interaction.response.send_message(
            "🔒 **Access Denied**: Only bot administrators can use the recording system.",
            ephemeral=True
        )
        return

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
        status_lines.append("💤 Instant buffer not started — start a recording to enable it")

    embed = discord.Embed(
        title="🎙️ Recording Control Panel",
        description="\n".join(status_lines),
        color=discord.Color.red() if recording else discord.Color.blurple()
    )
    embed.add_field(
        name="📖 How it works",
        value=(
            "**Mixed** — all voices blended into one `.mp3` file\n"
            "**Individual** — each speaker gets their own track\n"
            "**Instant** — bot always keeps the last 10 min buffered; ask anytime\n"
            "**Auto-delete** — instant files deleted after 1 hour"
        ),
        inline=False
    )
    embed.set_footer(text="Recordings are only accessible by bot admins • Use /record to manage")
    await interaction.response.send_message(embed=embed, view=RecordView(), ephemeral=True)


# ── Core recording helpers ────────────────────────────────────────────────────
async def _cmd_start_recording(interaction: discord.Interaction, mode: str = "mixed"):
    guild_id = interaction.guild_id

    if guild_id in guild_recording_states:
        await interaction.followup.send("⚠️ A recording is already active. Use **Stop Recording** first.", ephemeral=True)
        return

    vc, err = await _ensure_recv_client(interaction)
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

    # Also hook rolling buffer for instant recall (shared connection)
    if guild_id not in guild_instant_buffers:
        buf_sink = RollingBufferSink(INSTANT_BUFFER_SECS)
        guild_instant_buffers[guild_id] = {"sink": buf_sink, "vc": vc}
        vc.listen(voice_recv.MultiAudioSink(sink, buf_sink))
    else:
        vc.listen(sink)

    mode_label = "🎚️ Mixed (all speakers)" if mode == "mixed" else "👤 Individual (per-speaker)"
    embed = discord.Embed(
        title="🔴 Recording Started",
        description=f"Now recording **{vc.channel.mention}**\n**Mode:** {mode_label}",
        color=discord.Color.red()
    )
    embed.set_footer(text="Use /record → Stop Recording to end and receive the file")
    await interaction.followup.send(embed=embed, ephemeral=True)


async def _send_recording_dm(user, send_path: str, embed):
    """DM the recording file to user privately."""
    try:
        dm = await user.create_dm()
        if os.path.getsize(send_path) <= 25 * 1024 * 1024:
            await dm.send(embed=embed, file=discord.File(send_path, filename=os.path.basename(send_path)))
        else:
            embed.add_field(name="⚠️ File Too Large", value=f"Saved locally at:\n`{send_path}`", inline=False)
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


async def _cmd_stop_recording(interaction: discord.Interaction):
    guild_id = interaction.guild_id
    state = guild_recording_states.pop(guild_id, None)

    if not state:
        await interaction.followup.send("⚠️ No active recording to stop.", ephemeral=True)
        return

    vc = state["vc"]
    sink = state["sink"]
    mode = state["mode"]
    requester = interaction.user

    if vc.is_listening():
        vc.stop_listening()

    try:
        sink.cleanup()
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
        if wav_path and os.path.exists(wav_path) and os.path.getsize(wav_path) > 44:
            embed.add_field(name="📁 File", value=os.path.basename(wav_path).replace(".wav", ".mp3"), inline=True)
            embed.add_field(name="🎚️ Mode", value="Mixed (all speakers)", inline=True)
            await interaction.followup.send("✅ Recording stopped! Converting and sending to your **DMs** privately…", ephemeral=True)
            asyncio.ensure_future(_finalize_and_send_mixed(requester, wav_path, embed))
        else:
            await interaction.followup.send("⚠️ Recording file is empty — no audio was captured.", ephemeral=True)

    else:
        tracks = sink.get_track_paths() if hasattr(sink, "get_track_paths") else []
        if not tracks:
            await interaction.followup.send("⚠️ No individual tracks recorded.", ephemeral=True)
            return
        embed.add_field(name="👤 Mode", value="Individual (per-speaker)", inline=True)
        embed.add_field(name="🎵 Tracks", value=str(len(tracks)), inline=True)
        await interaction.followup.send(f"✅ Recording stopped! Sending **{len(tracks)} individual track(s)** to your **DMs** privately…", ephemeral=True)
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
        send_embed.set_footer(text="🎙️ HELLFIRE CLUB Bot • Delivered privately via DM")

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
            if not os.path.exists(wav_path) or os.path.getsize(wav_path) <= 44:
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
    """Grab last 10 minutes of buffered audio and DM it privately."""
    guild_id = interaction.guild_id
    buf_state = guild_instant_buffers.get(guild_id)
    requester = interaction.user

    if not buf_state:
        vc, err = await _ensure_recv_client(interaction)
        if err:
            await interaction.followup.send(err, ephemeral=True)
            return
        buf_sink = RollingBufferSink(INSTANT_BUFFER_SECS)
        guild_instant_buffers[guild_id] = {"sink": buf_sink, "vc": vc}
        vc.listen(buf_sink)
        await interaction.followup.send(
            "⚡ **Instant buffer started** — I\'ll now keep the last 10 minutes of audio ready.\n"
            "Use `/record → Get Instant` again to retrieve it.",
            ephemeral=True
        )
        return

    sink = buf_state["sink"]
    pcm = sink.snapshot()

    if len(pcm) < BYTES_PER_SECOND * 2:
        await interaction.followup.send("⚠️ Not enough audio buffered yet (less than 2 seconds). Try again after some activity.", ephemeral=True)
        return

    await interaction.followup.send("⚡ Processing… Recording will be sent to your **DMs** privately.", ephemeral=True)

    try:
        wav_bytes = await asyncio.get_event_loop().run_in_executor(None, pcm_to_wav_bytes, pcm)
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        out_wav = os.path.join(RECORDINGS_DIR, f"instant_{guild_id}_{ts}.wav")
        with open(out_wav, "wb") as f:
            f.write(wav_bytes)

        mp3_path = await asyncio.get_event_loop().run_in_executor(None, wav_to_mp3_bytes, out_wav)
        send_path = mp3_path if mp3_path else out_wav
        duration_s = len(pcm) // BYTES_PER_SECOND
        dur_m, dur_s = divmod(duration_s, 60)
        size_mb = os.path.getsize(send_path) / (1024 * 1024)

        embed = discord.Embed(
            title="⚡ Instant Recording — Private",
            description=(
                f"Last 10 min of VC audio\n"
                f"Duration: **{dur_m:02d}:{dur_s:02d}** • Size: `{size_mb:.1f} MB`\n"
                f"🔒 *Only you can see this*"
            ),
            color=discord.Color.orange()
        )
        embed.set_footer(text="⚠️ This file will be auto-deleted in 1 hour")

        await _send_recording_dm(requester, send_path, embed)

        async def _auto_delete():
            await asyncio.sleep(INSTANT_EXPIRE_SECS)
            for p in [send_path, out_wav]:
                try:
                    if os.path.exists(p):
                        os.remove(p)
                except Exception:
                    pass

        asyncio.ensure_future(_auto_delete())

    except Exception as e:
        try:
            dm = await requester.create_dm()
            await dm.send(f"❌ Failed to process instant recording: {e}")
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
