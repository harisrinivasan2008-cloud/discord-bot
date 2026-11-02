# Discord Bot (HELLFIRE CLUB)

A feature-rich, high-performance Discord bot built with `discord.py` that provides:

1. **Interactive Birthday Registry & Celebrations**
   - Clickable button panel (`🎂 Set Birthday`, `🔍 My Birthday`, `🎈 Upcoming`, `🗑️ Remove`) posted in any designated text channel.
   - Interactive date selection modal with intelligent month (name or number) and day validation.
   - Automated daily wishes with custom **Hellfire Birthday Decrees** and blessings.
2. **Self-Assignable Reaction Roles**
   - Full support for server custom emojis, animated emojis, and standard Unicode emoji aliases.
   - Real visual emoji rendering in embed panels.
   - Real-time slash command autocomplete suggestions with custom and popular emojis.
3. **Cute Leveling System & Voice XP**
   - Dynamic XP tracking for text chat and voice channel (VC) participation.
   - Member-only progression (bot owner, extra owners, and admins are exempt).
   - Cute compact level-up cards.
4. **Interactive Moderation & Antinuke Security**
   - Interactive `/unban` with autocomplete and dropdown select menu.
   - Full rights parity for Server Owner & Extra Owners.
   - Fast audit log resolution and antinuke guards.
5. **Zero-Config Cloud Hosting Support**
   - Built-in safe token fallback allows the bot to start out of the box even when `.env` is absent on cloud hosts.

---

## 🚀 Quick Setup & Hosting

### Option A: Direct Local Run / Cloud Host
1. Clone or download repository:
   ```bash
   git clone https://github.com/harisrinivasan2008-cloud/discord-bot.git
   cd discord-bot
   ```
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Run the bot:
   ```bash
   python bot.py
   ```
   *(The bot starts with its default configuration automatically. You can optionally copy `.env.example` to `.env` to customize settings).*

### Option B: Discloud / ZIP Upload
- Use `bot.zip` (already packaged with all configurations and `.env`) for 1-click deployment on Discloud or other container hosts.

---

## 📜 Key Commands

### 🎂 Birthday System
- `/birthday setup [announcement_channel] [panel_channel] [input_channel]`: Configure announcement channel and deploy the interactive Birthday button panel.
- `/birthday panel [channel]`: Post or repost the clickable Birthday Registration Button panel.
- `/birthday set [month] [day] [year]`: Register birthday via slash command.
- `/setbirthday [month] [day] [year]`: Fast shortcut for members.
- `/birthday view [member]`: Check registered birthdays and countdown.
- `/birthday upcoming`: View the next 10 upcoming server birthdays.
- `/birthday remove`: Remove registered birthday.

### 🎭 Reaction Roles
- `/reactionrole create [channel] [title] [description] [emoji1] [role1] ...`: Create an interactive reaction role panel.
- `/reactionrole add [message_id] [emoji] [role]`: Attach an emoji and role to any existing message.
- `/reactionrole list`: View active reaction role messages.
- `/reactionrole remove [message_id] [emoji]`: Delete reaction role mappings.

### 📈 Leveling & Voice XP
- `/rank [member]`: View your cute compact rank card.
- `/leaderboard`: View server top XP rankings.
- `/leveling setup [channel]`: Set level-up notification channel.
- `/leveling toggle`: Enable or disable the leveling engine.

### 🛡️ Moderation & Management
- `/help`: Interactive dropdown menu with 6 modules.
- `/unban`: Interactive selection menu listing all banned members.
- `/join [channel]`: Connect bot to voice channel for 24/7 stay-in-VC mode.
- `/leave`: Disconnect bot from voice.
