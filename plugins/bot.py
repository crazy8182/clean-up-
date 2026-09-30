import os
import asyncio
import logging
import logging.config

from dotenv import load_dotenv
load_dotenv()

from aiohttp import web, ClientSession
from pyrogram import Client, idle, filters, __version__, enums
from pyrogram.handlers import MessageHandler
from pyrogram.errors import FloodWait

from cleanup_logic import (
    normalize_name, detect_quality, detect_source, is_video,
    is_bad_source, plan_deleteall
)
from motor.motor_asyncio import AsyncIOMotorClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("movie-cleanup")
logging.getLogger("pyrogram").setLevel(logging.INFO)

API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
BOT_TOKEN = os.environ["BOT_TOKEN"]
MONGO_URI = os.environ["MONGO_URI"]
DB_NAME = os.getenv("DB_NAME", "movie_cleanup")
CHANNEL_ID = int(os.environ["CHANNEL_ID"])
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").replace(",", " ").split() if x.strip()}
PORT = int(os.getenv("PORT", "8080"))
SESSION_NAME = os.getenv("SESSION_NAME", "imax_clean_bot")

mongo = AsyncIOMotorClient(MONGO_URI, serverSelectionTimeoutMS=10000)
db = mongo[DB_NAME]
files_col = db["files"]

app = Client(
    SESSION_NAME,
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    workers=int(os.getenv("WORKERS", "60")),
    sleep_threshold=5,
)

async def health(request):
    return web.Response(text="OK")

async def start_health_server():
    server = web.Application()
    server.router.add_get("/", health)
    server.router.add_get("/health", health)
    runner = web.AppRunner(server)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    log.info("Health server listening on 0.0.0.0:%s", PORT)

async def telegram_bot_api_diagnostic():
    """Clear any stale Bot API webhook and print its status.
    Pyrogram uses MTProto, but clearing a stale webhook is harmless and
    removes one common source of confusion when moving a bot between hosts.
    """
    url = f"https://api.telegram.org/bot{BOT_TOKEN}"
    try:
        async with ClientSession() as s:
            async with s.post(f"{url}/deleteWebhook", data={"drop_pending_updates": "false"}) as r:
                result = await r.json(content_type=None)
                log.info("Bot API deleteWebhook: %s", result.get("ok"))
            async with s.get(f"{url}/getWebhookInfo") as r:
                result = await r.json(content_type=None)
                info = result.get("result", {})
                log.info(
                    "Bot API webhook url=%r pending=%s",
                    info.get("url"), info.get("pending_update_count")
                )
    except Exception:
        log.exception("Bot API webhook diagnostic failed (Pyrogram can still run via MTProto)")

async def mongo_check():
    await mongo.admin.command("ping")
    await files_col.create_index([("channel_id", 1), ("message_id", 1)], unique=True)
    await files_col.create_index([("channel_id", 1), ("movie_name", 1)])
    log.info("MongoDB connection: OK")

async def is_admin(message):
    uid = message.from_user.id if message.from_user else 0
    return uid in ADMIN_IDS

async def reply_start(message):
    uid = message.from_user.id if message.from_user else 0
    if uid not in ADMIN_IDS:
        return await message.reply_text(f"⛔ Admin only.\nYour Telegram ID: `{uid}`")
    await message.reply_text(
        "🎬 Movie Cleanup Bot\n\n"
        "/ping - test bot\n"
        "/index - index channel\n"
        "/delete Movie Name - preview\n"
        "/confirm_delete Movie Name - execute\n"
        "/deleteall - complete database preview\n"
        "/confirm_deleteall - complete cleanup"
    )

async def index_channel(client, message):
    if not await is_admin(message):
        return await message.reply_text("⛔ Admin only.")
    status = await message.reply_text("⏳ Indexing channel files...")
    count = 0
    async for msg in client.get_chat_history(CHANNEL_ID):
        media = msg.video or msg.document or msg.audio
        if not media:
            continue
        name = getattr(media, "file_name", None) or f"message_{msg.id}"
        media_type = "video" if msg.video else ("document" if msg.document else "audio")
        doc = {
            "channel_id": CHANNEL_ID,
            "message_id": msg.id,
            "file_id": media.file_id,
            "file_name": name,
            "movie_name": normalize_name(name),
            "quality": detect_quality(name),
            "source": detect_source(name),
            "media_type": media_type,
            "file_size": int(getattr(media, "file_size", 0) or 0),
        }
        await files_col.update_one(
            {"channel_id": CHANNEL_ID, "message_id": msg.id},
            {"$set": doc},
            upsert=True,
        )
        count += 1
        if count % 100 == 0:
            await status.edit_text(f"⏳ Indexed: {count}")
    await status.edit_text(f"✅ Index complete.\nFiles indexed: {count}")

async def delete_one(client, doc):
    try:
        await client.delete_messages(CHANNEL_ID, int(doc["message_id"]))
    except FloodWait as e:
        await asyncio.sleep(e.value)
        await client.delete_messages(CHANNEL_ID, int(doc["message_id"]))
    await files_col.delete_one({"channel_id": CHANNEL_ID, "message_id": int(doc["message_id"])})

async def run_delete(client, docs, status):
    deleted = failed = 0
    for doc in docs:
        try:
            await delete_one(client, doc)
            deleted += 1
        except Exception:
            failed += 1
            log.exception("Delete failed message_id=%s", doc.get("message_id"))
        n = deleted + failed
        if n % 20 == 0 or n == len(docs):
            await status.edit_text(
                f"🗑 Progress: {n}/{len(docs)}\n"
                f"Telegram deleted: {deleted}\n"
                f"MongoDB removed: {deleted}\n"
                f"Failed: {failed}"
            )
    return deleted, failed

async def get_matching(client, title):
    query = normalize_name(title)
    return await files_col.find({
        "channel_id": CHANNEL_ID,
        "movie_name": {"$regex": __import__('re').escape(query), "$options": "i"},
    }).to_list(length=5000)

async def preview_title(client, message, title):
    docs = await get_matching(client, title)
    if not docs:
        return await message.reply_text("ℹ️ No indexed files found.")
    keep, delete = plan_deleteall(docs)
    await message.reply_text(
        f"🎬 {title}\n\nFound: {len(docs)}\nDelete: {len(delete)}\nKeep: {len(keep)}\n\n"
        f"Run /confirm_delete {title} to execute."
    )

async def confirm_title(client, message, title):
    docs = await get_matching(client, title)
    if not docs:
        return await message.reply_text("ℹ️ No indexed files found.")
    keep, delete = plan_deleteall(docs)
    if not delete:
        return await message.reply_text("✅ Nothing to delete.")
    status = await message.reply_text(f"🗑 Starting cleanup: {len(delete)} files")
    deleted, failed = await run_delete(client, delete, status)
    await status.edit_text(
        f"✅ Cleanup completed\n\nTelegram deleted: {deleted}\n"
        f"MongoDB removed: {deleted}\nFailed: {failed}\nKept: {len(keep)}"
    )

async def deleteall_preview(client, message):
    if not await is_admin(message):
        return await message.reply_text("⛔ Admin only.")
    status = await message.reply_text("🔎 Scanning MongoDB...")
    docs = await files_col.find({"channel_id": CHANNEL_ID}).to_list(length=100000)
    if not docs:
        return await status.edit_text("ℹ️ MongoDB index is empty. Run /index first.")
    keep, delete = plan_deleteall(docs)
    docs_count = sum(1 for d in docs if not is_video(d))
    bad_count = sum(1 for d in docs if is_video(d) and is_bad_source(d))
    sample = "\n".join("❌ " + d.get("file_name", "unknown") for d in delete[:30]) or "Nothing"
    await status.edit_text(
        "🔎 DELETEALL PREVIEW\n\n"
        f"Total indexed: {len(docs)}\nDocuments: {docs_count}\nBad-source videos: {bad_count}\n"
        f"To delete: {len(delete)}\nTo keep: {len(keep)}\n\n"
        "Series: 480p + 720p + 1080p per episode\n"
        "Combined/Complete/Completed/Batch/Multi-Episode protected\n\n"
        f"Delete preview:\n{sample}\n\n"
        "⚠️ Nothing deleted.\nUse /confirm_deleteall to execute."
    )

async def deleteall_confirm(client, message):
    if not await is_admin(message):
        return await message.reply_text("⛔ Admin only.")
    status = await message.reply_text("🔎 Re-scanning MongoDB...")
    docs = await files_col.find({"channel_id": CHANNEL_ID}).to_list(length=100000)
    if not docs:
        return await status.edit_text("ℹ️ Nothing indexed.")
    keep, delete = plan_deleteall(docs)
    if not delete:
        return await status.edit_text("✅ Database is already clean.")
    await status.edit_text(f"🗑 Starting DELETEALL\nFiles to delete: {len(delete)}\nFiles to keep: {len(keep)}")
    deleted, failed = await run_delete(client, delete, status)
    await status.edit_text(
        f"✅ DELETEALL completed\n\nTelegram deleted: {deleted}\nMongoDB removed: {deleted}\n"
        f"Failed: {failed}\nKept: {len(keep)}"
    )

async def incoming_message(client, message):
    # Deliberately catch ALL incoming messages, not filters.private, so logs prove
    # whether Telegram updates are reaching this process.
    uid = message.from_user.id if message.from_user else 0
    text = (message.text or message.caption or "").strip()
    log.info(
        "RECEIVED UPDATE | chat=%s type=%s user=%s text=%r",
        getattr(message.chat, "id", None), getattr(message.chat, "type", None), uid, text[:200]
    )
    if not message.text or not message.chat:
        return
    if message.chat.type != enums.ChatType.PRIVATE:
        return
    parts = message.text.split(maxsplit=1)
    command = parts[0].split("@", 1)[0].lower()
    args = parts[1].strip() if len(parts) == 2 else ""
    try:
        if command == "/ping":
            await message.reply_text("🏓 Bot is working!\n\nTelegram connection: ✅\nKoyeb service: ✅")
        elif command == "/start":
            await reply_start(message)
        elif command == "/index":
            await index_channel(client, message)
        elif command == "/delete":
            if not args: return await message.reply_text("Usage: /delete Movie Name")
            await preview_title(client, message, args)
        elif command == "/confirm_delete":
            if not args: return await message.reply_text("Usage: /confirm_delete Movie Name")
            await confirm_title(client, message, args)
        elif command == "/deleteall":
            await deleteall_preview(client, message)
        elif command == "/confirm_deleteall":
            await deleteall_confirm(client, message)
        else:
            await message.reply_text("❓ Unknown command. Use /start or /ping.")
    except Exception:
        log.exception("Command handler failed: %s", command)
        await message.reply_text("❌ Error occurred. Check Koyeb logs.")

# Use Pyrogram's explicit handler registration, matching the working reference bot.
app.add_handler(MessageHandler(incoming_message, filters.incoming), group=0)

async def main():
    await start_health_server()
    await telegram_bot_api_diagnostic()
    log.info("Starting Pyrofork %s...", __version__)
    await app.start()
    me = await app.get_me()
    log.info("====================================")
    log.info("BOT CONNECTED")
    log.info("Bot ID: %s", me.id)
    log.info("Bot Username: @%s", me.username)
    log.info("Admin IDs: %s", sorted(ADMIN_IDS))
    log.info("Channel ID: %s", CHANNEL_ID)
    log.info("MongoDB: testing...")
    await mongo_check()
    log.info("Telegram update handler: READY")
    log.info("Bot started and waiting for updates")
    await idle()

if __name__ == "__main__":
    asyncio.run(main())
