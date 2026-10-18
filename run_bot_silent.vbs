Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "C:\Users\NishiHari\.gemini\antigravity\scratch\discord-bot"
WshShell.Run """C:\Users\NishiHari\AppData\Local\Programs\Python\Python311\pythonw.exe"" bot.py", 0, False
