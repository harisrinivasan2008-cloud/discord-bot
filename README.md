# Discord Bot

A Discord bot built with `discord.py` that provides:
1. **Auto-reactions** when a specific user is tagged/mentioned.
2. **Nickname changes** via `/changenick` slash command.
3. **Role inspection** via `/roles` slash command.

## Quick Setup

1. Copy `.env.example` to `.env`:
   ```bash
   cp .env.example .env
   ```
2. Put your Discord bot token and target user ID in `.env`:
   ```env
   DISCORD_TOKEN=your_token_here
   TARGET_USER_ID=123456789012345678
   ```
3. Run the bot:
   ```bash
   python bot.py
   ```
