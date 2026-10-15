Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "C:\Users\NishiHari\.gemini\antigravity\scratch\discord-bot"
WshShell.Run "pythonw bot.py", 0, False
