import os
import sys
import json
import time
import asyncio
import traceback
import random
import re
from collections import defaultdict
import discord
from discord.ext import commands
from discord import app_commands
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

bot = commands.Bot(command_prefix="!", intents=intents)

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
# ACTION & ROLEPLAY SYSTEM (kiss, hug, slap, chase, kill)
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
    "kill": [
        "https://media.giphy.com/media/4eMkhdpa912i4/giphy.gif",
        "https://media.giphy.com/media/arbHBoiCW55JA8DSLm/giphy.gif",
        "https://media.giphy.com/media/11HeubLHnHgaVG/giphy.gif",
        "https://media.giphy.com/media/ebITvSXYKNvEc/giphy.gif",
        "https://media.giphy.com/media/xT39D4GSIcBwuelKTe/giphy.gif",
        "https://media.giphy.com/media/9r4GF7750wF04iZ8qB/giphy.gif",
        "https://media.giphy.com/media/okECPQqhxukVC/giphy.gif",
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
    "kill": {
        "verb": "obliterated",
        "emoji": "⚔️",
        "text": "{source} ruthlessly obliterated {target}! 💥"
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
    }
}

ACTION_WORDS = {
    "kiss": "kiss",
    "hug": "hug",
    "slap": "slap",
    "kill": "kill",
    "chasing": "chase",
    "chase": "chase"
}

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

    # Roleplay / Action Detection in Chat (e.g. "@someone kill @someone2", "hug @someone", etc.)
    if message.guild and message.mentions and not message.mention_everyone:
        content_lower = message.content.lower()
        matched_act = None
        for word, key in ACTION_WORDS.items():
            if re.search(rf"\b{word}\b", content_lower):
                matched_act = key
                break

        if matched_act:
            if len(message.mentions) >= 2:
                act_user = message.mentions[0]
                tgt_user = message.mentions[1]
                content, embed = make_action_payload(matched_act, act_user, tgt_user)
                try:
                    await message.channel.send(content=content, embed=embed)
                except Exception as e:
                    print(f"[Action Error] Could not send action message: {e}", flush=True)
            elif len(message.mentions) == 1:
                act_user = message.author
                tgt_user = message.mentions[0]
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

    await interaction.response.defer(ephemeral=True)

    def check_target(msg: discord.Message):
        if target:
            return msg.author.id == target.id
        return True

    try:
        deleted = await channel.purge(limit=amount, check=check_target, bulk=True)
        actual = len(deleted)

        if actual == 0:
            await interaction.followup.send(
                f"⚠️ No messages found{f' from {target.mention}' if target else ''} to delete.",
                ephemeral=True
            )
            return

        await interaction.followup.send(
            f"🧹 Successfully deleted **{actual}** message(s)"
            f"{f' from {target.mention}' if target else ''} in {channel.mention}.",
            ephemeral=True
        )

    except discord.Forbidden:
        await interaction.followup.send(
            "❌ Missing Permissions: Ensure the bot has **Manage Messages** permission in this channel.",
            ephemeral=True
        )
    except discord.HTTPException as e:
        await interaction.followup.send(f"❌ Failed to delete messages: {e}", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Unexpected error while deleting messages: {e}", ephemeral=True)

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
# ACTION / ROLEPLAY SLASH COMMANDS (/kiss, /hug, /slap, /chase, /chasing, /kill)
# ==============================================================================
@bot.tree.command(name="kiss", description="Kiss another server member with a looping animated GIF")
@app_commands.describe(
    target="The member you want to kiss",
    source="The member who is kissing (defaults to you)"
)
async def cmd_kiss(interaction: discord.Interaction, target: discord.Member, source: discord.Member = None):
    actor = source if source is not None else interaction.user
    content, embed = make_action_payload("kiss", actor, target)
    try:
        await interaction.response.send_message(content=content, embed=embed)
    except (discord.NotFound, discord.HTTPException):
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
    content, embed = make_action_payload("hug", actor, target)
    try:
        await interaction.response.send_message(content=content, embed=embed)
    except (discord.NotFound, discord.HTTPException):
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
    content, embed = make_action_payload("slap", actor, target)
    try:
        await interaction.response.send_message(content=content, embed=embed)
    except (discord.NotFound, discord.HTTPException):
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
    content, embed = make_action_payload("chase", actor, target)
    try:
        await interaction.response.send_message(content=content, embed=embed)
    except (discord.NotFound, discord.HTTPException):
        try:
            await interaction.channel.send(content=content, embed=embed)
        except Exception:
            pass

@bot.tree.command(name="chasing", description="Chase down another server member with a looping animated GIF")
@app_commands.describe(
    target="The member you want to chase",
    source="The member who is chasing (defaults to you)"
)
async def cmd_chasing(interaction: discord.Interaction, target: discord.Member, source: discord.Member = None):
    actor = source if source is not None else interaction.user
    content, embed = make_action_payload("chase", actor, target)
    try:
        await interaction.response.send_message(content=content, embed=embed)
    except (discord.NotFound, discord.HTTPException):
        try:
            await interaction.channel.send(content=content, embed=embed)
        except Exception:
            pass

@bot.tree.command(name="kill", description="Obliterate another server member with an anime attack GIF")
@app_commands.describe(
    target="The member you want to kill",
    source="The member who is attacking (defaults to you)"
)
async def cmd_kill(interaction: discord.Interaction, target: discord.Member, source: discord.Member = None):
    actor = source if source is not None else interaction.user
    content, embed = make_action_payload("kill", actor, target)
    try:
        await interaction.response.send_message(content=content, embed=embed)
    except (discord.NotFound, discord.HTTPException):
        try:
            await interaction.channel.send(content=content, embed=embed)
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


def find_local_audio(input_str: str) -> tuple:
    clean = input_str.strip()
    if not clean:
        return None, None

    # Do not treat URLs or YouTube links as local audio files
    if clean.startswith(('http://', 'https://', 'www.')) or 'youtube.com' in clean or 'youtu.be' in clean:
        return None, None

    valid_exts = ('.mp3', '.wav', '.m4a', '.ogg', '.flac')

    # 1. Exact file match in AUDIO_DIR
    exact = os.path.join(AUDIO_DIR, clean)
    if os.path.isfile(exact) and clean.lower().endswith(valid_exts):
        return exact, clean

    # 2. Check standard audio extensions in AUDIO_DIR
    for ext in valid_exts:
        test_p = os.path.join(AUDIO_DIR, f"{clean}{ext}")
        if os.path.isfile(test_p):
            return test_p, f"{clean}{ext}"

    # 3. Case-insensitive or stem match in AUDIO_DIR
    clean_lower = clean.lower()
    if os.path.exists(AUDIO_DIR):
        for f in os.listdir(AUDIO_DIR):
            f_path = os.path.join(AUDIO_DIR, f)
            if os.path.isfile(f_path) and f.lower().endswith(valid_exts):
                f_low = f.lower()
                stem, _ = os.path.splitext(f_low)
                if f_low == clean_lower or stem == clean_lower:
                    return f_path, f

    # 4. Check yt_cache directory (only complete valid audio files)
    yt_cache = os.path.join(AUDIO_DIR, "yt_cache")
    if os.path.exists(yt_cache):
        for f in os.listdir(yt_cache):
            f_path = os.path.join(yt_cache, f)
            if os.path.isfile(f_path) and f.lower().endswith(valid_exts):
                f_low = f.lower()
                stem, _ = os.path.splitext(f_low)
                if f_low == clean_lower or stem == clean_lower:
                    return f_path, f

    return None, None



def get_youtube_video_id(url_or_id: str) -> str:
    clean = url_or_id.strip()
    m = re.search(r'(?:v=|\/|youtu\.be\/|^)([a-zA-Z0-9_-]{11})(?:[&?]|$)', clean)
    return m.group(1) if m else None


def fetch_youtube_audio(query_or_url: str) -> dict:
    import yt_dlp

    yt_cache_dir = os.path.join(AUDIO_DIR, "yt_cache")
    os.makedirs(yt_cache_dir, exist_ok=True)

    clean_target = query_or_url.strip()
    video_id = get_youtube_video_id(clean_target)
    resolved_title = None

    # 1. Instant Cache Check: If direct video ID or URL with video ID is already downloaded
    if video_id:
        cache_file = os.path.join(yt_cache_dir, f"{video_id}.mp3")
        if os.path.exists(cache_file) and os.path.getsize(cache_file) > 1000:
            return {
                'path': cache_file,
                'title': f"YouTube Track ({video_id})",
                'url': f"https://www.youtube.com/watch?v={video_id}"
            }

    # 2. Determine target query or direct video URL
    is_url = (
        clean_target.startswith(("http://", "https://", "www."))
        or "youtube.com" in clean_target
        or "youtu.be" in clean_target
    )

    # 3. For search queries (e.g. "aaluma doluma"), resolve quickly via flat search to get ID & check cache
    if not is_url and not video_id:
        flat_opts = {
            'quiet': True,
            'extract_flat': True,
            'default_search': 'ytsearch',
            'noplaylist': True,
            'extractor_args': {
                'youtube': {
                    'player_client': ['android'],
                }
            }
        }
        try:
            with yt_dlp.YoutubeDL(flat_opts) as ydl:
                search_res = ydl.extract_info(f"ytsearch1:{clean_target}", download=False)
                if 'entries' in search_res and search_res['entries']:
                    first_entry = search_res['entries'][0]
                    video_id = first_entry.get('id')
                    resolved_title = first_entry.get('title')
        except Exception as e:
            print(f"[Search Resolution Warning] {e}", flush=True)

    # 4. If video_id is now known, check instant cache again before downloading
    if video_id:
        cache_file = os.path.join(yt_cache_dir, f"{video_id}.mp3")
        if os.path.exists(cache_file) and os.path.getsize(cache_file) > 1000:
            return {
                'path': cache_file,
                'title': resolved_title or f"YouTube Track ({video_id})",
                'url': f"https://www.youtube.com/watch?v={video_id}"
            }
        target = f"https://www.youtube.com/watch?v={video_id}"
    elif is_url:
        target = clean_target
    else:
        target = f"ytsearch1:{clean_target}"

    ffmpeg_bin = get_ffmpeg_path()

    # Prioritize mobile/embedded clients that do not trigger bot verification
    client_combos = [
        ['android'],
        ['tv_embedded'],
        ['android_creator'],
    ]

    last_error = None
    for clients in client_combos:
        ydl_opts = {
            'format': 'bestaudio/best/18/22',
            'outtmpl': os.path.join(yt_cache_dir, '%(id)s.%(ext)s'),
            'ffmpeg_location': ffmpeg_bin,
            'noplaylist': True,
            'quiet': True,
            'no_warnings': True,
            'default_search': 'ytsearch',
            'extractor_args': {
                'youtube': {
                    'player_client': clients,
                }
            },
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(target, download=True)
                if 'entries' in info and info['entries']:
                    info = info['entries'][0]

                v_id = info.get('id', video_id or 'audio')
                title = info.get('title', resolved_title or 'YouTube Track')
                webpage_url = info.get('webpage_url', f"https://www.youtube.com/watch?v={v_id}")

                final_path = os.path.join(yt_cache_dir, f"{v_id}.mp3")
                if not os.path.exists(final_path):
                    for fname in os.listdir(yt_cache_dir):
                        if fname.startswith(v_id) and fname.endswith('.mp3'):
                            final_path = os.path.join(yt_cache_dir, fname)
                            break

                return {
                    'path': final_path,
                    'title': title,
                    'url': webpage_url
                }
        except Exception as e:
            last_error = e
            continue

    if last_error:
        raise last_error
    raise RuntimeError("Failed to extract YouTube audio.")

# ==============================================================================
# MUSIC QUEUE, SMART AUTOPLAY, AND INTERACTIVE CONTROLLER CARD SYSTEM
# ==============================================================================
class Track:
    def __init__(self, title: str, audio_path: str, url: str = None, requester: discord.Member = None, is_autoplay: bool = False):
        self.title = title
        self.audio_path = audio_path
        self.url = url
        self.requester = requester
        self.is_autoplay = is_autoplay

class GuildMusicState:
    def __init__(self, guild_id: int):
        self.guild_id = guild_id
        self.queue: list[Track] = []
        self.current_track: Track = None
        self.volume: int = 100
        self.autoplay: bool = True  # Autoplay enabled by default
        self.played_history: list[str] = []
        self.text_channel: discord.TextChannel = None
        self.card_message: discord.Message = None
        self.is_advancing: bool = False

guild_music_states: dict[int, GuildMusicState] = {}

def get_music_state(guild_id: int) -> GuildMusicState:
    if guild_id not in guild_music_states:
        guild_music_states[guild_id] = GuildMusicState(guild_id)
    return guild_music_states[guild_id]

def make_volume_bar(volume: int) -> str:
    blocks = 10
    clamped = max(0, min(volume, 200))
    filled = int(round((clamped / 200.0) * blocks))
    bar = "█" * filled + "░" * (blocks - filled)
    return f"`[{bar}]` **{volume}%**"

def clean_song_title(title: str) -> str:
    cleaned = re.sub(r'(?i)\(?(official\s*(music\s*)?video|audio|lyrics|hd|4k|visualizer|remastered|explicit)\)?', '', title)
    cleaned = re.sub(r'\[.*?\]|\(.*?\)', '', cleaned)
    return ' '.join(cleaned.split())

def fetch_similar_youtube_song(last_title: str, history: list[str]) -> dict:
    import yt_dlp
    cleaned = clean_song_title(last_title)
    if not cleaned:
        cleaned = last_title
    search_query = f"{cleaned} similar music"

    ydl_opts = {
        'quiet': True,
        'extract_flat': True,
        'default_search': 'ytsearch',
        'noplaylist': True,
        'extractor_args': {
            'youtube': {
                'player_client': ['android'],
            }
        },
    }

    history_lower = [h.lower() for h in history]
    last_title_lower = last_title.lower()

    found_url = None
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            res = ydl.extract_info(f"ytsearch8:{search_query}", download=False)
            for e in res.get('entries', []):
                t = e.get('title', '')
                u = e.get('url') or e.get('webpage_url')
                if not u:
                    continue
                t_low = t.lower()
                if t_low != last_title_lower and t_low not in history_lower:
                    found_url = u if u.startswith(('http://', 'https://')) else f"https://www.youtube.com/watch?v={u}"
                    break
        except Exception as e:
            print(f"[Autoplay Search Warning] {e}", flush=True)

    if not found_url:
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                res = ydl.extract_info(f"ytsearch6:{cleaned} music", download=False)
                for e in res.get('entries', []):
                    t = e.get('title', '')
                    u = e.get('url') or e.get('webpage_url')
                    if u and t.lower() not in history_lower:
                        found_url = u if u.startswith(('http://', 'https://')) else f"https://www.youtube.com/watch?v={u}"
                        break
        except Exception:
            pass

    if not found_url:
        return None

    return fetch_youtube_audio(found_url)

def build_player_embed(guild_id: int) -> discord.Embed:
    state = get_music_state(guild_id)
    track = state.current_track
    if not track:
        embed = discord.Embed(
            title="🎧 𝐇𝐄𝐋𝐋𝐅𝐈𝐑𝐄 𝐀𝐔𝐃𝐈𝐎 𝐒𝐓𝐀𝐓𝐈𝐎𝐍",
            description="```yaml\nStatus: STANDBY (Ready for playback)\nQueue:  Empty\n```\nClick **🔍 Search** below or use `/play` to drop a track!",
            color=0x2B2D31
        )
        embed.set_footer(text="Hellfire Club • Music Engine v2.0")
        return embed

    is_auto = track.is_autoplay
    theme_color = 0x9B59B6 if is_auto else 0xED4245
    status_badge = "🟣 SMART AUTOPLAY" if is_auto else "🔴 LIVE PLAYBACK"

    embed = discord.Embed(
        color=theme_color,
        timestamp=discord.utils.utcnow()
    )

    embed.set_author(
        name=f"HELLFIRE JUKEBOX • {status_badge}",
        icon_url="https://cdn.discordapp.com/emojis/853304564887355412.gif"
    )

    t_url = track.url if track.url else "https://youtube.com"
    clean_t_title = track.title[:80] + ("..." if len(track.title) > 80 else "")

    embed.description = (
        f"### 🎵 [{clean_t_title}]({t_url})\n"
        f"`ılıılı|̲̅l̲̅|̲̅l̲̅|̲̅l̲̅|̲̅|̲̅l̲̅|̲̅l̲̅|̲̅l̲̅|ılıılı`\n"
    )

    req_text = track.requester.mention if track.requester else "🤖 *Autoplay AI*"
    source_label = "🌐 YouTube Stream" if track.url else "📁 Local Audio File"

    embed.add_field(name="👤 Requested By", value=req_text, inline=True)
    embed.add_field(name="📡 Audio Source", value=f"`{source_label}`", inline=True)
    embed.add_field(name="🔁 Autoplay Mode", value="`🟢 ENABLED`" if state.autoplay else "`⚪ DISABLED`", inline=True)

    vol_pct = state.volume
    blocks = 10
    clamped = max(0, min(vol_pct, 200))
    filled = int(round((clamped / 200.0) * blocks))
    vol_bar = "█" * filled + "░" * (blocks - filled)
    embed.add_field(name="🔊 Master Volume", value=f"`[{vol_bar}]` **{vol_pct}%**", inline=False)

    if state.queue:
        queue_text_lines = []
        for i, q_track in enumerate(state.queue[:4], 1):
            q_req = q_track.requester.display_name if q_track.requester else "Autoplay"
            q_title_short = q_track.title[:32] + "..." if len(q_track.title) > 35 else q_track.title
            q_link = f"[{q_title_short}]({q_track.url})" if q_track.url else q_title_short
            queue_text_lines.append(f"`{i}.` {q_link} `({q_req})`")
        if len(state.queue) > 4:
            queue_text_lines.append(f"*...and +{len(state.queue) - 4} more track(s)*")
        embed.add_field(
            name=f"📑 Next in Queue ({len(state.queue)})",
            value="\n".join(queue_text_lines),
            inline=False
        )
    else:
        autoplay_status = "Autoplay active: familiar songs will follow automatically." if state.autoplay else "Queue is empty. Click 🔍 Search to add tracks."
        embed.add_field(
            name="📑 Next in Queue (0)",
            value=f"*{autoplay_status}*",
            inline=False
        )

    embed.set_footer(text="Hellfire Sound System • Click 🔍 Search or buttons below")
    return embed

class MusicSearchModal(discord.ui.Modal, title="🔍 Search & Play Music"):
    song_input = discord.ui.TextInput(
        label="Song Name or YouTube Link",
        placeholder="e.g. Aaluma Doluma, Believer, or https://...",
        required=True,
        max_length=200
    )

    def __init__(self, guild_id: int):
        super().__init__()
        self.guild_id = guild_id

    async def on_submit(self, interaction: discord.Interaction):
        try:
            await interaction.response.defer(ephemeral=True)
        except Exception:
            pass

        target_vc = interaction.user.voice.channel if (interaction.user.voice and interaction.user.voice.channel) else None
        if not target_vc:
            try:
                await interaction.followup.send("❌ You must be in a voice channel to search and play music.", ephemeral=True)
            except Exception:
                pass
            return

        vc = interaction.guild.voice_client
        if not vc:
            try:
                vc = await target_vc.connect(reconnect=True, timeout=10.0)
            except Exception as e:
                try:
                    await interaction.followup.send(f"❌ Could not connect to {target_vc.mention}: {e}", ephemeral=True)
                except Exception:
                    pass
                return
        elif vc.channel != target_vc:
            try:
                await vc.move_to(target_vc)
            except Exception:
                pass

        clean_query = self.song_input.value.strip()
        local_path, local_name = find_local_audio(clean_query)
        if local_path:
            audio_path = local_path
            display_title = local_name
            source_url = None
        else:
            try:
                yt_info = await asyncio.to_thread(fetch_youtube_audio, clean_query)
                audio_path = yt_info['path']
                display_title = yt_info['title']
                source_url = yt_info.get('url')
            except Exception as e:
                try:
                    await interaction.followup.send(f"❌ Failed to fetch audio: {e}", ephemeral=True)
                except Exception:
                    pass
                return

        state = get_music_state(self.guild_id)
        state.text_channel = interaction.channel

        new_track = Track(
            title=display_title,
            audio_path=audio_path,
            url=source_url,
            requester=interaction.user,
            is_autoplay=False
        )

        if vc.is_playing():
            state.queue.append(new_track)
            try:
                await interaction.followup.send(f"📥 Added to queue: **[{display_title}]({source_url or ''})** (`#{len(state.queue)}`)", ephemeral=True)
            except Exception:
                pass
            await update_player_card(self.guild_id)
        else:
            state.current_track = new_track
            state.played_history.append(new_track.title)
            if len(state.played_history) > 25:
                state.played_history.pop(0)

            ffmpeg_bin = get_ffmpeg_path()
            raw_source = discord.FFmpegPCMAudio(audio_path, executable=ffmpeg_bin)
            pcm_source = discord.PCMVolumeTransformer(raw_source, volume=state.volume / 100.0)

            def _after(e):
                if e:
                    print(f"[Playback Callback Error] {e}", flush=True)
                bot.loop.call_soon_threadsafe(lambda: asyncio.create_task(play_next_track(self.guild_id)))

            vc.play(pcm_source, after=_after)
            try:
                await interaction.followup.send(f"▶️ Now playing: **[{display_title}]({source_url or ''})**", ephemeral=True)
            except Exception:
                pass
            await update_player_card(self.guild_id)

class MusicControllerView(discord.ui.View):
    def __init__(self, guild_id: int):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        state = get_music_state(guild_id)
        for child in self.children:
            if getattr(child, "custom_id", "") == "music_autoplay":
                child.style = discord.ButtonStyle.success if state.autoplay else discord.ButtonStyle.secondary
                child.label = f"🔁 Autoplay: {'ON' if state.autoplay else 'OFF'}"

    @discord.ui.button(label="🔉 Vol -", style=discord.ButtonStyle.secondary, custom_id="music_vol_down", row=0)
    async def vol_down(self, interaction: discord.Interaction, button: discord.ui.Button):
        state = get_music_state(self.guild_id)
        state.volume = max(10, state.volume - 20)
        vc = interaction.guild.voice_client if interaction.guild else None
        if vc and vc.source and hasattr(vc.source, 'volume'):
            vc.source.volume = state.volume / 100.0
        embed = build_player_embed(self.guild_id)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="⏭️ Skip", style=discord.ButtonStyle.primary, custom_id="music_skip", row=0)
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        state = get_music_state(self.guild_id)
        vc = interaction.guild.voice_client if interaction.guild else None
        if vc and vc.is_playing():
            await interaction.response.send_message("⏭️ Skipped current song!", ephemeral=True)
            vc.stop()
        else:
            await interaction.response.send_message("⚠️ No track is currently playing to skip.", ephemeral=True)

    @discord.ui.button(label="🔊 Vol +", style=discord.ButtonStyle.secondary, custom_id="music_vol_up", row=0)
    async def vol_up(self, interaction: discord.Interaction, button: discord.ui.Button):
        state = get_music_state(self.guild_id)
        state.volume = min(200, state.volume + 20)
        vc = interaction.guild.voice_client if interaction.guild else None
        if vc and vc.source and hasattr(vc.source, 'volume'):
            vc.source.volume = state.volume / 100.0
        embed = build_player_embed(self.guild_id)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="🔍 Search", style=discord.ButtonStyle.success, custom_id="music_search", row=0)
    async def search_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(MusicSearchModal(self.guild_id))

    @discord.ui.button(label="🔁 Autoplay: ON", style=discord.ButtonStyle.success, custom_id="music_autoplay", row=1)
    async def toggle_autoplay(self, interaction: discord.Interaction, button: discord.ui.Button):
        state = get_music_state(self.guild_id)
        state.autoplay = not state.autoplay
        button.style = discord.ButtonStyle.success if state.autoplay else discord.ButtonStyle.secondary
        button.label = f"🔁 Autoplay: {'ON' if state.autoplay else 'OFF'}"
        embed = build_player_embed(self.guild_id)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="📜 Queue", style=discord.ButtonStyle.secondary, custom_id="music_queue", row=1)
    async def queue_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        state = get_music_state(self.guild_id)
        if not state.queue:
            await interaction.response.send_message("📜 Queue is currently empty.", ephemeral=True)
            return
        lines = [f"`{idx}.` {t.title} (by {t.requester.display_name if t.requester else 'Autoplay'})" for idx, t in enumerate(state.queue[:15], 1)]
        await interaction.response.send_message("### 📜 Upcoming Queue:\n" + "\n".join(lines), ephemeral=True)

    @discord.ui.button(label="⏹️ Stop", style=discord.ButtonStyle.danger, custom_id="music_stop", row=1)
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        state = get_music_state(self.guild_id)
        state.queue.clear()
        state.current_track = None
        vc = interaction.guild.voice_client if interaction.guild else None
        if vc and vc.is_playing():
            vc.stop()
        stop_embed = discord.Embed(
            title="⏹️ HELLFIRE AUDIO • PLAYBACK STOPPED",
            description=f"Playback stopped and queue cleared by {interaction.user.mention}.",
            color=discord.Color.red()
        )
        stop_embed.set_footer(text="Hellfire Sound System • Ready for next session")
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(embed=stop_embed, view=self)

async def play_next_track(guild_id: int):
    state = get_music_state(guild_id)
    if state.is_advancing:
        return
    state.is_advancing = True

    guild = bot.get_guild(guild_id)
    if not guild:
        state.is_advancing = False
        return

    vc = guild.voice_client
    if not vc or not vc.is_connected():
        state.current_track = None
        state.is_advancing = False
        return

    next_track = None
    if state.queue:
        next_track = state.queue.pop(0)
    elif state.autoplay and state.current_track:
        last_title = state.current_track.title
        try:
            yt_data = await asyncio.to_thread(fetch_similar_youtube_song, last_title, state.played_history)
            if yt_data:
                next_track = Track(
                    title=yt_data['title'],
                    audio_path=yt_data['path'],
                    url=yt_data.get('url'),
                    requester=None,
                    is_autoplay=True
                )
        except Exception as e:
            print(f"[Autoplay Error] {e}", flush=True)

    if not next_track:
        state.current_track = None
        state.is_advancing = False
        if state.card_message:
            try:
                idle_embed = discord.Embed(
                    title="🎵 Music Queue Finished",
                    description="Queue is empty and no more tracks to autoplay. Use `/play` to start playing music!",
                    color=discord.Color.blue()
                )
                await state.card_message.edit(embed=idle_embed, view=None)
            except Exception:
                pass
        return

    state.current_track = next_track
    state.played_history.append(next_track.title)
    if len(state.played_history) > 25:
        state.played_history.pop(0)

    try:
        ffmpeg_bin = get_ffmpeg_path()
        raw_source = discord.FFmpegPCMAudio(next_track.audio_path, executable=ffmpeg_bin)
        pcm_source = discord.PCMVolumeTransformer(raw_source, volume=state.volume / 100.0)

        def _after(e):
            if e:
                print(f"[Playback Callback Error] {e}", flush=True)
            bot.loop.call_soon_threadsafe(lambda: asyncio.create_task(play_next_track(guild_id)))

        state.is_advancing = False
        vc.play(pcm_source, after=_after)

        await update_player_card(guild_id)
    except Exception as e:
        print(f"[Playback Start Error] {e}", flush=True)
        state.is_advancing = False
        await play_next_track(guild_id)

async def update_player_card(guild_id: int):
    state = get_music_state(guild_id)
    embed = build_player_embed(guild_id)
    view = MusicControllerView(guild_id)
    if state.card_message:
        try:
            await state.card_message.edit(embed=embed, view=view)
            return
        except (discord.NotFound, discord.HTTPException):
            state.card_message = None

    if state.text_channel:
        try:
            msg = await state.text_channel.send(embed=embed, view=view)
            state.card_message = msg
        except Exception as e:
            print(f"[Player Card Send Error] {e}", flush=True)

@bot.tree.command(name="play", description="Play audio from YouTube, video search, uploaded file, or local audio folder")
@app_commands.describe(
    link_or_name="YouTube link (https://...), search terms, or local audio file name (e.g. 1.mp3)",
    file="Upload an audio file (.mp3, .wav, .m4a, .ogg) to play directly"
)
async def play_audio(
    interaction: discord.Interaction,
    link_or_name: str = None,
    file: discord.Attachment = None
):
    async def send_play_response(content=None, embed=None, view=None):
        try:
            if interaction.response.is_done():
                return await interaction.followup.send(content=content, embed=embed, view=view)
            else:
                return await interaction.response.send_message(content=content, embed=embed, view=view)
        except Exception:
            if interaction.channel:
                try:
                    return await interaction.channel.send(content=content, embed=embed, view=view)
                except Exception:
                    pass

    try:
        await interaction.response.defer()
    except Exception:
        pass

    # 1. Connect or move to voice channel
    target_vc = interaction.user.voice.channel if (interaction.user.voice and interaction.user.voice.channel) else None
    if not target_vc:
        await send_play_response("❌ You must be in a voice channel (or use `/join`) to play audio.")
        return

    vc = interaction.guild.voice_client
    if vc is not None:
        try:
            if not vc.is_connected():
                await vc.disconnect(force=True)
                vc = await target_vc.connect(reconnect=True, timeout=10.0)
            elif vc.channel.id != target_vc.id:
                await vc.move_to(target_vc)
        except Exception:
            try:
                await vc.disconnect(force=True)
            except Exception:
                pass
            try:
                vc = await target_vc.connect(reconnect=True, timeout=10.0)
            except Exception as e:
                await send_play_response(f"❌ Could not connect to {target_vc.mention}: {e}")
                return
    else:
        try:
            vc = await target_vc.connect(reconnect=True, timeout=10.0)
        except discord.ClientException:
            vc = interaction.guild.voice_client
            if vc and vc.channel.id != target_vc.id:
                try:
                    await vc.move_to(target_vc)
                except Exception as e:
                    await send_play_response(f"❌ Could not move to {target_vc.mention}: {e}")
                    return
        except Exception as e:
            await send_play_response(f"❌ Could not connect to {target_vc.mention}: {e}")
            return

    # 2. Determine audio source
    audio_path = None
    display_title = ""
    source_url = None

    if file:
        filename = file.filename
        safe_filename = "".join(c for c in filename if c.isalnum() or c in "._- ")
        audio_path = os.path.join(AUDIO_DIR, safe_filename)
        await file.save(audio_path)
        display_title = safe_filename

    elif link_or_name:
        clean_input = link_or_name.strip()
        local_path, local_name = find_local_audio(clean_input)
        if local_path:
            audio_path = local_path
            display_title = local_name
        else:
            try:
                yt_info = await asyncio.to_thread(fetch_youtube_audio, clean_input)
                audio_path = yt_info['path']
                display_title = yt_info['title']
                source_url = yt_info.get('url')
            except Exception as e:
                await send_play_response(f"❌ Failed to fetch audio: {e}")
                return
    else:
        files = [f for f in os.listdir(AUDIO_DIR) if f.lower().endswith(('.mp3', '.wav', '.m4a', '.ogg', '.flac')) and os.path.isfile(os.path.join(AUDIO_DIR, f))]
        if files:
            chosen = "sample.wav" if "sample.wav" in files else files[0]
            audio_path = os.path.join(AUDIO_DIR, chosen)
            display_title = chosen
        else:
            await send_play_response("❌ Please provide a YouTube link, video name, or local file name to play.")
            return

    if not audio_path or not os.path.exists(audio_path):
        await send_play_response("❌ Audio file could not be prepared for playback.")
        return

    state = get_music_state(interaction.guild_id)
    state.text_channel = interaction.channel

    new_track = Track(
        title=display_title,
        audio_path=audio_path,
        url=source_url,
        requester=interaction.user,
        is_autoplay=False
    )

    # If audio is already playing, add to queue!
    if vc.is_playing():
        state.queue.append(new_track)
        queue_pos = len(state.queue)
        queue_embed = discord.Embed(
            title="📥 Added to Queue",
            description=f"**[{new_track.title}]({new_track.url})**" if new_track.url else f"**{new_track.title}**",
            color=discord.Color.blue()
        )
        queue_embed.add_field(name="Position in Queue", value=f"#{queue_pos}", inline=True)
        queue_embed.add_field(name="Requested By", value=interaction.user.mention, inline=True)
        queue_embed.set_footer(text="Use /skip to advance to the next song or use the player card buttons")
        await send_play_response(embed=queue_embed)

        # Refresh existing player card to show the updated queue count
        if state.card_message:
            try:
                await state.card_message.edit(embed=build_player_embed(interaction.guild_id), view=MusicControllerView(interaction.guild_id))
            except Exception:
                pass
        return

    # Not currently playing: start playback immediately
    state.current_track = new_track
    state.played_history.append(new_track.title)
    if len(state.played_history) > 25:
        state.played_history.pop(0)

    try:
        ffmpeg_bin = get_ffmpeg_path()
        raw_source = discord.FFmpegPCMAudio(audio_path, executable=ffmpeg_bin)
        pcm_source = discord.PCMVolumeTransformer(raw_source, volume=state.volume / 100.0)

        def _after(e):
            if e:
                print(f"[Playback Callback Error] {e}", flush=True)
            bot.loop.call_soon_threadsafe(lambda: asyncio.create_task(play_next_track(interaction.guild_id)))

        vc.play(pcm_source, after=_after)

        card_embed = build_player_embed(interaction.guild_id)
        card_view = MusicControllerView(interaction.guild_id)
        sent_msg = await send_play_response(embed=card_embed, view=card_view)
        if sent_msg:
            state.card_message = sent_msg
    except Exception as e:
        await send_play_response(f"❌ Failed to play audio: {e}")

@play_audio.autocomplete("link_or_name")
async def play_audio_autocomplete(interaction: discord.Interaction, current: str):
    choices = []
    current_lower = current.lower()
    if os.path.exists(AUDIO_DIR):
        for f in os.listdir(AUDIO_DIR):
            if f.lower().endswith(('.mp3', '.wav', '.m4a', '.ogg', '.flac')) and os.path.isfile(os.path.join(AUDIO_DIR, f)):
                if not current or current_lower in f.lower():
                    choices.append(app_commands.Choice(name=f"📁 Local Audio: {f}", value=f))
            if len(choices) >= 20:
                break
    return choices[:25]

@bot.tree.command(name="skip", description="Skip the currently playing song in voice chat")
async def skip_track(interaction: discord.Interaction):
    guild = interaction.guild
    if not guild or not guild.voice_client:
        await interaction.response.send_message("❌ I am not connected to any voice channel in this server.", ephemeral=True)
        return
    vc = guild.voice_client
    if not vc.is_playing():
        await interaction.response.send_message("⚠️ There is no song currently playing to skip.", ephemeral=True)
        return

    state = get_music_state(guild.id)
    current_title = state.current_track.title if state.current_track else "Current track"
    vc.stop()
    await interaction.response.send_message(f"⏭️ Skipped **`{current_title}`**!")

@bot.tree.command(name="queue", description="Display the current music queue and upcoming songs")
async def show_queue(interaction: discord.Interaction):
    guild = interaction.guild
    if not guild:
        await interaction.response.send_message("❌ This command must be used in a server.", ephemeral=True)
        return
    embed = build_player_embed(guild.id)
    view = MusicControllerView(guild.id)
    await interaction.response.send_message(embed=embed, view=view)

@bot.tree.command(name="autoplay", description="Toggle smart autoplay on or off")
async def toggle_autoplay_cmd(interaction: discord.Interaction):
    guild = interaction.guild
    if not guild:
        await interaction.response.send_message("❌ This command must be used in a server.", ephemeral=True)
        return
    state = get_music_state(guild.id)
    state.autoplay = not state.autoplay
    status_str = "Enabled ✅ (Will auto-play familiar tracks when queue ends)" if state.autoplay else "Disabled ❌"
    await interaction.response.send_message(f"🔁 Smart Autoplay is now **{status_str}**.")
    if state.card_message:
        try:
            await state.card_message.edit(embed=build_player_embed(guild.id), view=MusicControllerView(guild.id))
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

    # 1. Stop voice audio playback and clear queue
    state = get_music_state(guild_id)
    state.queue.clear()
    state.current_track = None
    if state.card_message:
        try:
            stop_embed = discord.Embed(
                title="⏹️ Music Playback Stopped",
                description=f"Playback stopped and queue cleared by {interaction.user.mention}.",
                color=discord.Color.red()
            )
            await state.card_message.edit(embed=stop_embed, view=None)
        except Exception:
            pass

    vc = interaction.guild.voice_client if interaction.guild else None
    if vc and vc.is_connected() and vc.is_playing():
        vc.stop()
        stopped_actions.append(f"⏹️ **Audio Playback**: Stopped playing in {vc.channel.mention} and cleared queue")

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
