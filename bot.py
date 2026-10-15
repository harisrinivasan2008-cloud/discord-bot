import os
import sys
import asyncio
import traceback
import discord
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv
from aiohttp import web

# Force unbuffered stdout/stderr so logs appear immediately in Render
try:
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
except Exception:
    pass

# Load credentials from .env file (if running locally) or system environment (if running on Render)
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
intents.members = True          # Required for roles inspection and nickname management

bot = commands.Bot(command_prefix="!", intents=intents)

# Web server for Render.com health check
async def start_web_server():
    async def handle_ping(request):
        return web.Response(text="Discord Bot is active and running 24/7!")

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
        print("[Notice] No TARGET_USER_IDS configured yet in environment", flush=True)
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
    # Check if target is server owner (Discord prevents bots from altering owner's nickname)
    if interaction.guild.owner_id == member.id:
        await interaction.response.send_message(
            "❌ Discord does not permit bots to change the server owner's nickname.",
            ephemeral=True
        )
        return

    # Check Discord Role Hierarchy
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
# 3. DISPLAY ROLES OF A SPECIFIC PERSON
# ==============================================================================
@bot.tree.command(name="roles", description="Show all roles and details for a specific member")
@app_commands.describe(member="The member whose roles you want to view")
async def show_roles(interaction: discord.Interaction, member: discord.Member):
    # Get all roles excluding @everyone, ordered from highest to lowest hierarchy
    member_roles = [role for role in member.roles if role.name != "@everyone"]
    member_roles.reverse()

    role_mentions = [role.mention for role in member_roles]
    role_count = len(member_roles)

    embed_color = member.color if member.color.value != 0 else discord.Color.blue()

    embed = discord.Embed(
        title=f"👤 Roles & Info for {member.display_name}",
        color=embed_color
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="Username", value=f"{member.name} (`{member.id}`)", inline=True)
    embed.add_field(name="Highest Role", value=member.top_role.mention, inline=True)
    embed.add_field(name="Total Roles", value=str(role_count), inline=True)

    if role_mentions:
        roles_text = " ".join(role_mentions)
        if len(roles_text) > 1024:
            roles_text = f"{roles_text[:1000]}... *(and more)*"
        embed.add_field(name=f"Assigned Roles ({role_count})", value=roles_text, inline=False)
    else:
        embed.add_field(name="Assigned Roles", value="*No roles assigned*", inline=False)

    embed.set_footer(text=f"Requested by {interaction.user.display_name}")

    await interaction.response.send_message(embed=embed)

@changenick.error
async def changenick_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message(
            "❌ You need the **Manage Nicknames** permission to use this command.",
            ephemeral=True
        )
    else:
        await interaction.response.send_message(
            f"❌ Error executing command: {error}",
            ephemeral=True
        )

async def main():
    # 1. Start the web server first so Render detects port 10000 immediately
    await start_web_server()
    # 2. Connect to Discord Gateway
    async with bot:
        await bot.start(TOKEN)

if __name__ == "__main__":
    print("=== Discord Bot Process Starting ===", flush=True)

    if not TOKEN:
        print("CRITICAL ERROR: DISCORD_TOKEN is missing or empty!", flush=True)
        print("Please go to Render Dashboard -> Your Service -> Environment -> Add DISCORD_TOKEN", flush=True)
        sys.exit(1)

    print(f"DISCORD_TOKEN loaded successfully ({len(TOKEN)} characters).", flush=True)

    try:
        asyncio.run(main())
    except discord.errors.PrivilegedIntentsRequired:
        print("\n" + "!" * 60, flush=True)
        print("CRITICAL ERROR: Privileged Gateway Intents are NOT enabled!", flush=True)
        print("Fix this in 30 seconds:", flush=True)
        print("1. Go to https://discord.com/developers/applications", flush=True)
        print("2. Click your bot -> Click 'Bot' tab on the left", flush=True)
        print("3. Scroll to 'Privileged Gateway Intents'", flush=True)
        print("4. Turn ON 'Server Members Intent' and 'Message Content Intent'", flush=True)
        print("5. Click Save Changes", flush=True)
        print("!" * 60 + "\n", flush=True)
        sys.exit(1)
    except discord.errors.LoginFailure:
        print("\n" + "!" * 60, flush=True)
        print("CRITICAL ERROR: Invalid Discord Bot Token!", flush=True)
        print("Please check your DISCORD_TOKEN in Render Environment variables.", flush=True)
        print("!" * 60 + "\n", flush=True)
        sys.exit(1)
    except Exception as e:
        print(f"CRITICAL ERROR while running bot: {e}", flush=True)
        traceback.print_exc()
        sys.exit(1)
