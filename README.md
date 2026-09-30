# Movie Cleanup Bot - DreamX Backtested Structure

Built using the same startup/client architecture as the uploaded working Auto Filter bot:
- Pyrofork 2.3.69
- workers=60
- sleep_threshold=5
- Pyrogram plugins
- `idle()` main loop
- Koyeb HTTP health server

Commands:
- /start
- /ping
- /index
- /delete Movie Name
- /confirm_delete Movie Name
- /deleteall
- /confirm_deleteall

IMPORTANT:
The first test after deployment must be `/ping`.

Koyeb:
- Web Service
- HTTP health path: /health
- Port: 8080 (or Koyeb PORT)
- Start command: python bot.py

Telegram:
The bot must be administrator in CHANNEL_ID with permission to delete messages.

Deletion safety:
Telegram message is deleted first. MongoDB record is deleted only after successful Telegram deletion.

Series:
Individual S01E01/S01E02 files keep one best 480p, 720p and 1080p.
Combined/Complete/Completed/Full Season/Season Pack/Batch/Multi-Episode/range files are protected and one best copy per quality is retained.


## Restart notification

After every successful process start/restart, the bot sends all configured `ADMIN_IDS`:

♻️ Bot Restarted Successfully

This is sent only after Telegram and MongoDB startup checks have completed.
