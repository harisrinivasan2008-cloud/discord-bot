import os
import sys
import asyncio
import traceback
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
    print("=" * 45, flush=True)

    # Sync slash commands globally across Discord
    try:
        synced = await bot.tree.sync()
        print(f"Successfully synced {len(synced)} slash command(s).", flush=True)
    except Exception as e:
        print(f"Failed to sync slash commands: {e}", flush=True)

# ==============================================================================
# 1. AUTO-REACT WHEN SPECIFIC USER(S) ARE TAGGED / MENTIONED
# ==============================================================================
@bot.event
async def on_message(message: discord.Message):
    # Ignore messages sent by the bot itself
    if message.author.bot:
        return

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
# 2. CHANGE NICKNAME IN THE SERVER
# ==============================================================================
@bot.tree.command(name="changenick", description="Change a member's nickname in this server")
@app_commands.describe(
    member="The member whose nickname should be changed",
    new_nickname="The new nickname to apply (leave empty to reset to original name)"
)
@app_commands.checks.has_permissions(manage_nicknames=True)
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
# 3. DISPLAY ROLES & DATES OF A SPECIFIC PERSON
# ==============================================================================
@bot.tree.command(name="roles", description="Show all roles and join dates for a member")
@app_commands.describe(member="The member whose roles you want to view")
async def show_roles(interaction: discord.Interaction, member: discord.Member):
    member_roles = [role for role in member.roles if role.name != "@everyone"]
    member_roles.reverse()

    role_mentions = [role.mention for role in member_roles]
    role_count = len(member_roles)

    created_ts = int(member.created_at.timestamp())
    joined_ts = int(member.joined_at.timestamp()) if member.joined_at else None

    embed_color = member.color if member.color.value != 0 else discord.Color.blue()
    embed = discord.Embed(
        title=f"👤 Member Info & Roles: {member.display_name}",
        color=embed_color
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="Username", value=f"{member.name} (`{member.id}`)", inline=True)
    embed.add_field(name="Highest Role", value=member.top_role.mention, inline=True)
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
# 4. AVATAR COMMAND
# ==============================================================================
@bot.tree.command(name="avatar", description="Display a member's avatar in full size")
@app_commands.describe(member="The member whose avatar you want to view (defaults to yourself)")
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
# 5. VOICE CHAT CONTROLS: MUTE / UNMUTE
# ==============================================================================
@bot.tree.command(name="vmute", description="Server mute or unmute a member in voice chat")
@app_commands.describe(
    member="The member currently in a voice channel",
    mute="True to mute, False to unmute (default: True)"
)
@app_commands.checks.has_permissions(mute_members=True)
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
# 6. VOICE CHAT CONTROLS: DEAFEN / UNDEAFEN
# ==============================================================================
@bot.tree.command(name="vdeafen", description="Server deafen or undeafen a member in voice chat")
@app_commands.describe(
    member="The member currently in a voice channel",
    deafen="True to deafen, False to undeafen (default: True)"
)
@app_commands.checks.has_permissions(deafen_members=True)
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
# 7. VOICE CHAT CONTROLS: MOVE MEMBER TO ANOTHER VC
# ==============================================================================
@bot.tree.command(name="movevc", description="Move a member from one voice channel to another")
@app_commands.describe(
    member="The member to move",
    channel="The destination voice channel"
)
@app_commands.checks.has_permissions(move_members=True)
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
# GENERAL PERMISSION ERROR HANDLERS
# ==============================================================================
@changenick.error
@vmute.error
@vdeafen.error
@movevc.error
async def on_command_permission_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message(
            "❌ You do not have permission to execute this command.",
            ephemeral=True
        )
    else:
        await interaction.response.send_message(
            f"❌ An error occurred: {error}",
            ephemeral=True
        )

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
