@echo off
echo Packaging files for Discloud...
powershell -Command "Compress-Archive -Path bot.py, requirements.txt, .env, discloud.config -DestinationPath bot.zip -Force"
echo.
echo Done! 'bot.zip' has been created.
pause
