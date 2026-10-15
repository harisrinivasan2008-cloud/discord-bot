import os
import discord
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv

# Load credentials from .env file
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

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

from aiohttp import web

# Web server for Render.com health check
async def start_web_server():
    async def handle_ping(request):
        return web.Response(text="Discord Bot is running 24/7!")

    app = web.Application()
    app.router.add_get("/", handle_ping)
    app.router.add_get("/health", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"Health-check web server active on port {port}")

bot = commands.Bot(command_prefix="!", intents=intents)

_web_server_started = False

@bot.event
async def on_ready():
    global _web_server_started
    print("=" * 45)
    print(f"Logged in as: {bot.user.name} (ID: {bot.user.id})")
    print(f"Connected to {len(bot.guilds)} server(s)")
    target_ids = get_target_user_ids()
    if target_ids:
        print(f"Tracking {len(target_ids)} target user(s) for auto-reaction: {list(target_ids)}")
    else:
        print("[Notice] No TARGET_USER_IDS configured yet in .env")
    print("=" * 45)

    # Start health check web server once for Render
    if not _web_server_started:
        try:
            await start_web_server()
            _web_server_started = True
        except Exception as e:
            print(f"Web server notice: {e}")

    # Sync slash commands globally across Discord
    try:
        synced = await bot.tree.sync()
        print(f"Successfully synced {len(synced)} slash command(s).")
    except Exception as e:
        print(f"Failed to sync slash commands: {e}")

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
                print(f"Missing permission to add reaction in channel {message.channel.name}")
                break
            except discord.HTTPException as e:
                print(f"Failed to add reaction: {e}")

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

    # Use member's top role color if available, otherwise default blue
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
        # Avoid exceeding Discord embed limit (1024 characters per field)
        if len(roles_text) > 1024:
            roles_text = f"{roles_text[:1000]}... *(and more)*"
        embed.add_field(name=f"Assigned Roles ({role_count})", value=roles_text, inline=False)
    else:
        embed.add_field(name="Assigned Roles", value="*No roles assigned*", inline=False)

    embed.set_footer(text=f"Requested by {interaction.user.display_name}")

    await interaction.response.send_message(embed=embed)

# Error handler for slash command permissions
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

if __name__ == "__main__":
    if not TOKEN:
        print("ERROR: DISCORD_TOKEN is missing! Please set it in your .env file.")
    else:
        bot.run(TOKEN)
