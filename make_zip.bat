@echo off
echo Packaging files for Discloud...
powershell -Command "Compress-Archive -Path bot.py, requirements.txt, .env, .env.example, discloud.config, admins.json, follow_config.json, server_config.json -DestinationPath bot.zip -Force"
echo.
echo Done! 'bot.zip' has been created.
pause
