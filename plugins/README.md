# Movie Cleanup Bot - Back Tested

Built using the same Pyrofork stack as the uploaded DreamX Auto Filter reference.

## Critical Telegram receive fix

The bot uses an explicit Pyrogram `MessageHandler(filters.incoming)` instead of relying on command decorators or `filters.private`. Every incoming update is logged as `RECEIVED UPDATE` before command parsing.

At startup it also calls Telegram Bot API `deleteWebhook` and logs webhook status, then starts Pyrofork 2.3.69.

## Test

Send `/ping` to the bot. Koyeb logs must show:

`RECEIVED UPDATE | ... text='/ping'`

and Telegram should reply:

`🏓 Bot is working!`

## Commands

/start
/ping
/index
/delete Movie Name
/confirm_delete Movie Name
/deleteall
/confirm_deleteall

## Cleanup rules

- Telegram Document/non-video media: delete during cleanup.
- CAM/CAMRip/HDTC/HDTS/HDCAM/TS/TC: delete.
- Movies: keep one best 480p, one best 720p, one best 1080p.
- Same quality source priority: WEB-DL > WEBRip > HDRip.
- TV individual episodes: keep one best 480p/720p/1080p for every SxxExx.
- Combined/Complete/Completed/Full Season/Season Pack/Season Batch/Batch/Multi-Episode/All Episodes/Entire Season/Collection/episode ranges are treated separately and one best file per quality is retained.
- Unknown video names/qualities are kept conservatively.
- Telegram message is deleted first; MongoDB record is deleted only after successful Telegram deletion.

## Koyeb

Deploy as Web Service. The app binds to `0.0.0.0:$PORT` and exposes `/health`.
Recommended HTTP health check: `/health` on the exposed port (normally 8080).

## Environment variables

Set these in Koyeb (or `.env` for local use):
API_ID, API_HASH, BOT_TOKEN, MONGO_URI, DB_NAME, CHANNEL_ID, ADMIN_IDS.
