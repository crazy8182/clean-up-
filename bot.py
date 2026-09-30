
import asyncio
import logging
import time

from aiohttp import web
from motor.motor_asyncio import AsyncIOMotorClient
from pyrogram import Client, idle
from pyrogram.errors import FloodWait

from info import *

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

log = logging.getLogger("movie-cleanup")

# IMPORTANT:
# This follows the same client/startup pattern as the uploaded working
# Auto Filter bot: workers + sleep_threshold + plugins + idle().
app = Client(
    name=SESSION,
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    workers=60,
    sleep_threshold=5,
    plugins={"root": "plugins"},
)

mongo = AsyncIOMotorClient(DATABASE_URI)
db = mongo[DATABASE_NAME]
files_col = db["files"]


async def health(request):
    return web.Response(text="OK")


async def start_web():
    server = web.Application()
    server.router.add_get("/", health)
    server.router.add_get("/health", health)

    runner = web.AppRunner(server)
    await runner.setup()

    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

    log.info("Health server listening on 0.0.0.0:%s", PORT)


async def main():
    await start_web()

    log.info("Starting Telegram client...")
    await app.start()

    me = await app.get_me()

    log.info("========================================")
    log.info("BOT CONNECTED")
    log.info("Bot ID: %s", me.id)
    log.info("Bot Username: @%s", me.username)
    log.info("Bot Name: %s", me.first_name)
    log.info("Admin IDs: %s", sorted(ADMIN_IDS))
    log.info("Channel ID: %s", CHANNEL_ID)

    try:
        await mongo.admin.command("ping")
        log.info("MongoDB connection: OK")
    except Exception:
        log.exception("MongoDB connection failed")

    log.info("Plugins are loaded by Pyrogram.")
    log.info("Bot is now waiting for Telegram updates.")

    await idle()

    await app.stop()
    mongo.close()


if __name__ == "__main__":
    while True:
        try:
            asyncio.run(main())
            break
        except FloodWait as e:
            log.warning("FloodWait: sleeping %s seconds", e.value)
            time.sleep(e.value)
        except KeyboardInterrupt:
            log.info("Bot stopped.")
            break
        except Exception:
            log.exception("Fatal error; restarting in 5 seconds.")
            time.sleep(5)
