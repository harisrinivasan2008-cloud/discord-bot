# Discord Bot

A powerful Discord bot built with `discord.py` that provides:
1. **Interactive Birthday Registry & Celebrations**
   - Clickable button panel (`🎂 Set Birthday`, `🔍 My Birthday`, `🎈 Upcoming`, `🗑️ Remove`) posted in any designated text channel.
   - Interactive date selection modal with intelligent month (name or number) and day validation.
   - Automated daily wishes with custom **Hellfire Birthday Decrees**.
2. **Self-Assignable Reaction Roles**
   - Full support for server custom emojis, animated emojis, and standard Unicode emoji aliases.
   - Real visual emoji rendering in embed panels (no raw text names like `:emoji:`).
   - Real-time slash command autocomplete suggestions with custom and popular emojis.
3. **Antinuke & Server Security**
4. **Voice & Leveling Systems**
5. **Auto-reactions, Welcomes & Nickname Management**

## Commands Summary

### 🎂 Birthday System
- `/birthday setup [announcement_channel] [panel_channel] [input_channel]`: Configure the birthday announcement channel and send the clickable Birthday Button panel to a particular text channel.
- `/birthday panel [channel]`: Post or repost the clickable Birthday Registration Button panel to a channel.
- `/birthday set [month] [day] [year]`: Manually register a birthday via slash command.
- `/birthday view [member]`: Check registered birthdays and countdown.
- `/birthday upcoming`: View the next 10 upcoming server birthdays.
- `/birthday remove`: Remove registered birthday.

### 🎭 Reaction Roles
- `/reactionrole create [channel] [title] [description] [emoji1] [role1] ...`: Create an interactive reaction role panel (renders real visual emojis for custom & standard emojis).
- `/reactionrole add [message_id] [emoji] [role]`: Attach an emoji and role to any existing message.
- `/reactionrole list`: View active reaction role messages.
- `/reactionrole remove [message_id] [emoji]`: Delete reaction role mappings.

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
