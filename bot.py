import os
import sys
import json
import time
import asyncio
import traceback
from collections import defaultdict
import discord
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv
from aiohttp import web

# Force unbuffered stdout/stderr so logs appear immediately
try:
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
except Exception:
    pass

# Load credentials from .env file
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN", "").strip()

# ==============================================================================
# BOT ADMIN MANAGEMENT & PERSISTENCE
# ==============================================================================
ADMINS_FILE = os.path.join(os.path.dirname(__file__), "admins.json")

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

def is_user_admin(user_id: int, guild: discord.Guild = None) -> bool:
    # 1. Server Owner is ALWAYS permanently authorized
    if guild and guild.owner_id == user_id:
        return True
    # 2. Check if user is in authorized admins list
    admin_ids = load_admin_ids()
    return user_id in admin_ids

def is_authorized_admin(interaction: discord.Interaction) -> bool:
    if not interaction.guild:
        return False
    return is_user_admin(interaction.user.id, interaction.guild)

def check_bot_admin():
    async def predicate(interaction: discord.Interaction) -> bool:
        if is_authorized_admin(interaction):
            return True
        await interaction.response.send_message(
            "🔒 **Access Denied**: You are not an authorized bot administrator.\n"
            "Only the server owner and designated bot admins can access these commands.",
            ephemeral=True
        )
        return False
    return app_commands.check(predicate)

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

# Anti-spam tracking dictionary: maps user_id -> list of message timestamps
user_message_times = defaultdict(list)

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

# ==============================================================================
# 1. AUTO-REACT ON MENTION & AUTOMATED ANTI-SPAM DETECTION
# ==============================================================================
@bot.event
async def on_message(message: discord.Message):
    # Ignore messages sent by bots (including our own bot)
    if message.author.bot:
        return

    # Anti-Spam Detection (Exempts Server Owner and Bot Admins)
    if message.guild and not is_user_admin(message.author.id, message.guild):
        now = time.time()
        uid = message.author.id
        # Keep timestamps from the last 4 seconds
        user_message_times[uid] = [t for t in user_message_times[uid] if now - t < 4.0]
        user_message_times[uid].append(now)

        # Trigger warning if 5 or more messages sent in 4 seconds
        if len(user_message_times[uid]) >= 5:
            user_message_times[uid].clear()  # Reset so it doesn't double-warn in the same second

            warning_embed = discord.Embed(
                title="⚠️ SPAM WARNING CARD",
                description=f"**{message.author.mention}, slow down!**\n"
                            f"Excessive messaging and spamming are not allowed in this server.",
                color=discord.Color.red()
            )
            warning_embed.set_thumbnail(url=message.author.display_avatar.url)
            warning_embed.add_field(name="Member", value=f"{message.author.name} (`{message.author.id}`)", inline=True)
            warning_embed.add_field(name="Channel", value=message.channel.mention, inline=True)
            warning_embed.add_field(name="Reason", value="Sending messages too rapidly (Anti-Spam Triggered)", inline=False)
            warning_embed.set_footer(text="Automated Server Anti-Spam Protection")

            try:
                await message.channel.send(content=message.author.mention, embed=warning_embed)
            except discord.HTTPException as e:
                print(f"Failed to send spam warning card: {e}", flush=True)

    # Check if any mentioned user is in our target_ids
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
    admin_ids = load_admin_ids()
    if member.id in admin_ids or member.id == interaction.guild.owner_id:
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
    if member.id == interaction.guild.owner_id:
        await interaction.response.send_message(
            "❌ Cannot revoke access from the Server Owner (permanent administrator).",
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
    owner = interaction.guild.owner
    owner_str = f"👑 {owner.mention} *(Server Owner - Permanent)*" if owner else "Unknown"

    admin_list_text = []
    for uid in admin_ids:
        if interaction.guild and uid == interaction.guild.owner_id:
            continue
        admin_list_text.append(f"🛡️ <@{uid}> (`{uid}`)")

    embed = discord.Embed(
        title="🔐 Authorized Bot Administrators",
        description="Only users on this list are permitted to execute bot commands.",
        color=discord.Color.gold()
    )
    embed.add_field(name="Server Owner", value=owner_str, inline=False)
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
@bot.tree.command(name="spam", description="Send a message multiple times (Admin only)")
@app_commands.describe(
    amount="How many times to send the message (1 to 25)",
    message="The text message to spam"
)
@check_bot_admin()
async def spam_messages(interaction: discord.Interaction, amount: int, message: str):
    if amount < 1:
        await interaction.response.send_message("❌ Amount must be at least 1.", ephemeral=True)
        return
    if amount > 25:
        await interaction.response.send_message("❌ Amount cannot exceed 25 to protect the bot from Discord rate limits.", ephemeral=True)
        return

    await interaction.response.send_message(f"🚀 Spaming {amount} message(s)...", ephemeral=True)
    for _ in range(amount):
        try:
            await interaction.channel.send(message)
            await asyncio.sleep(0.5)  # Safe delay to prevent Discord API 429 rate limit
        except Exception as e:
            print(f"Error during spam command: {e}", flush=True)
            break

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
# 8. VOICE CHAT CONTROLS: MUTE / UNMUTE (INDIVIDUAL)
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
