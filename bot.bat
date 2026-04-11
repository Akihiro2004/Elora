@echo off
set BOT_DIR=%~dp0
start "Elora Telegram" cmd /k "cd /d "%BOT_DIR%" && python bot.py"
start "Elora WhatsApp" cmd /k "cd /d "%BOT_DIR%" && node whatsapp_bot.js"
