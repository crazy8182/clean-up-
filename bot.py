
import os
import re
import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timezone

from dotenv import load_dotenv
load_dotenv()

from aiohttp import web
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ASCENDING
from pyrogram import Client, filters, idle, enums
from pyrogram.errors import FloodWait, RPCError

# ============================================================
# LOGGING
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("movie-cleanup")

# ============================================================
# ENV
# ============================================================
def env_int(name, default=None):
    value = os.getenv(name)
    if value is None or value == "":
        if default is None:
            raise RuntimeError(f"Missing required environment variable: {name}")
        return default
    return int(value)

API_ID = env_int("API_ID")
API_HASH = os.getenv("API_HASH", "").strip()
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
MONGO_URI = os.getenv("MONGO_URI", os.getenv("DATABASE_URI", "")).strip()
DB_NAME = os.getenv("DB_NAME", os.getenv("DATABASE_NAME", "movie_cleanup")).strip()
COLLECTION_NAME = os.getenv("COLLECTION_NAME", "files").strip()
CHANNEL_ID = env_int("CHANNEL_ID")
PORT = env_int("PORT", 8080)

ADMIN_IDS = {
    int(x.strip())
    for x in os.getenv("ADMIN_IDS", os.getenv("ADMINS", "")).replace(",", " ").split()
    if x.strip()
}

if not API_HASH:
    raise RuntimeError("Missing API_HASH")
if not BOT_TOKEN:
    raise RuntimeError("Missing BOT_TOKEN")
if not MONGO_URI:
    raise RuntimeError("Missing MONGO_URI or DATABASE_URI")
if not ADMIN_IDS:
    raise RuntimeError("Missing ADMIN_IDS")

# ============================================================
# TELEGRAM + MONGODB
# ============================================================
app = Client(
    "movie_cleanup_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    workers=8,
    sleep_threshold=5,
)

mongo = AsyncIOMotorClient(
    MONGO_URI,
    serverSelectionTimeoutMS=15000,
    connectTimeoutMS=15000,
    socketTimeoutMS=30000,
)
db = mongo[DB_NAME]
files = db[COLLECTION_NAME]

# ============================================================
# QUALITY / SOURCE RULES
# ============================================================
QUALITIES = {"480p", "720p", "1080p"}
QUALITY_RANK = {"1080p": 3, "720p": 2, "480p": 1}

SOURCE_RANK = {
    "WEB-DL": 1,
    "WEBRip": 2,
    "HDRip": 3,
}

BAD_SOURCES = {
    "CAM", "CAMRIP", "HDCAM", "HDTC", "HDTS",
    "TS", "TC", "TELECINE", "TELESYNC",
}

VIDEO_EXTENSIONS = {
    ".mp4", ".mkv", ".avi", ".mov", ".m4v", ".webm",
    ".ts", ".m2ts", ".wmv", ".flv", ".mpeg", ".mpg",
}

COMBINED_PATTERNS = [
    r"\bcomplete\b",
    r"\bcompleted\b",
    r"\bcombined\b",
    r"\bfull[ ._-]*season\b",
    r"\bcomplete[ ._-]*season\b",
    r"\bseason[ ._-]*pack\b",
    r"\bseason[ ._-]*batch\b",
    r"\bbatch\b",
    r"\bmulti[ ._-]*episode\b",
    r"\ball[ ._-]*episodes\b",
    r"\bentire[ ._-]*season\b",
    r"\bcollection\b",
    r"\bcomplete[ ._-]*collection\b",
]

EPISODE_RE = re.compile(r"\bS(\d{1,2})\s*E(\d{1,3})\b", re.I)
SEASON_RE = re.compile(r"\bS(\d{1,2})\b|\bSEASON\s*(\d{1,2})\b", re.I)

EPISODE_RANGE_PATTERNS = [
    r"\bS\d{1,2}E\d{1,3}\s*[-_–]\s*(?:E)?\d{1,3}\b",
    r"\bE\d{1,3}\s*[-_–]\s*(?:E)?\d{1,3}\b",
]

# ============================================================
# HELPERS
# ============================================================
def normalize_name(name: str) -> str:
    s = (name or "").lower()
    s = re.sub(r"\[[^\]]*\]|\([^)]*\)|\{[^}]*\}", " ", s)
    s = re.sub(r"[\._\-]+", " ", s)
    s = re.sub(
        r"\b(480p|720p|1080p|2160p|4k|8k)\b",
        " ",
        s,
        flags=re.I,
    )
    s = re.sub(
        r"\b(web[ -]?dl|web[ -]?rip|hdrip|camrip|cam|hdtc|hdts|hdcam|ts|tc)\b",
        " ",
        s,
        flags=re.I,
    )
    s = re.sub(r"\s+", " ", s).strip()
    return s


def detect_quality(name: str) -> str:
    m = re.search(r"\b(480p|720p|1080p|2160p|4k)\b", name or "", re.I)
    return m.group(1).lower() if m else "unknown"


def detect_source(name: str) -> str:
    n = (name or "").upper().replace("_", " ")
    checks = [
        ("WEB-DL", r"\bWEB[ ._-]?DL\b"),
        ("WEBRip", r"\bWEB[ ._-]?RIP\b"),
        ("HDRip", r"\bHD[ ._-]?RIP\b"),
        ("CAMRip", r"\bCAM[ ._-]?RIP\b"),
        ("CAM", r"\bCAM\b"),
        ("HDTC", r"\bHD[ ._-]?TC\b"),
        ("HDTS", r"\bHD[ ._-]?TS\b"),
        ("HDCAM", r"\bHD[ ._-]?CAM\b"),
        ("TS", r"\bTS\b"),
        ("TC", r"\bTC\b"),
    ]
    for label, pattern in checks:
        if re.search(pattern, n):
            return label
    return "unknown"


def combined_type(name: str) -> bool:
    n = (name or "").lower().replace("_", " ").replace("-", " ")
    for pattern in COMBINED_PATTERNS + EPISODE_RANGE_PATTERNS:
        if re.search(pattern, n, re.I):
            return True
    return False


def episode_info(name: str):
    m = EPISODE_RE.search(name or "")
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def season_info(name: str):
    m = SEASON_RE.search(name or "")
    if not m:
        return None
    return int(m.group(1) or m.group(2))


def series_key(name: str) -> str:
    n = normalize_name(name)
    n = re.sub(r"\bS\d{1,2}E\d{1,3}\b", " ", n, flags=re.I)
    n = re.sub(r"\bSEASON\s*\d{1,2}\b", " ", n, flags=re.I)
    n = re.sub(r"\bS\d{1,2}\b", " ", n, flags=re.I)
    n = re.sub(
        r"\b(complete|completed|combined|full season|complete season|"
        r"season pack|season batch|batch|multi episode|all episodes|"
        r"entire season|collection)\b",
        " ",
        n,
        flags=re.I,
    )
    return re.sub(r"\s+", " ", n).strip()


def is_bad_source(doc) -> bool:
    return str(doc.get("source", "unknown")).upper() in BAD_SOURCES


def is_video_record(doc) -> bool:
    # IMPORTANT: media_type is the Telegram media type at indexing time.
    # A .mkv uploaded as a Telegram DOCUMENT is still a document and will
    # be deleted by /deleteall.
    return doc.get("media_type") == "video"


def source_rank(doc) -> int:
    return SOURCE_RANK.get(doc.get("source"), 99)


def choose_best(items):
    return sorted(
        items,
        key=lambda d: (
            source_rank(d),
            -int(d.get("file_size") or 0),
            int(d.get("message_id") or 0),
        ),
    )[0]


# ============================================================
# CLEANUP PLANNER
# ============================================================
def build_cleanup_plan(all_docs):
    """
    Returns keep_docs, delete_docs.

    Documents:
      ALWAYS DELETE.

    Bad source videos:
      ALWAYS DELETE.

    TV individual episodes:
      Keep max one 480p, one 720p, one 1080p.
      Same quality source priority: WEB-DL > WEBRip > HDRip.

    Combined / complete / batch / multi-episode:
      Keep max one per quality.

    Movies:
      Keep max one 480p, one 720p, one 1080p.

    Unknown/non-standard video:
      KEEP conservatively.
    """
    delete_ids = set()
    keep_ids = set()
    groups = defaultdict(list)

    for doc in all_docs:
        if not is_video_record(doc):
            delete_ids.add(doc["_id"])
        elif is_bad_source(doc):
            delete_ids.add(doc["_id"])

    eligible = [
        d for d in all_docs
        if d["_id"] not in delete_ids
        and is_video_record(d)
        and d.get("quality") in QUALITIES
        and d.get("source") in SOURCE_RANK
    ]

    for doc in eligible:
        name = doc.get("file_name", "")
        ep = episode_info(name)
        season = season_info(name)

        if ep and season:
            # Individual episode: series + season + episode + quality.
            key = (
                "episode",
                series_key(name),
                season,
                ep[1],
                doc["quality"],
            )
        elif season and combined_type(name):
            # Combined/complete season: series + season + quality.
            key = (
                "combined",
                series_key(name),
                season,
                doc["quality"],
            )
        else:
            # Movie or non-season file: normalized title + quality.
            key = (
                "movie",
                normalize_name(name),
                doc["quality"],
            )

        groups[key].append(doc)

    for items in groups.values():
        keep_ids.add(choose_best(items)["_id"])

    # Safety: unknown quality/source videos are NOT automatically deleted.
    for doc in all_docs:
        if (
            doc["_id"] not in delete_ids
            and doc["_id"] not in keep_ids
            and is_video_record(doc)
        ):
            keep_ids.add(doc["_id"])

    keep_docs = [d for d in all_docs if d["_id"] in keep_ids]
    delete_docs = [d for d in all_docs if d["_id"] not in keep_ids]

    return keep_docs, delete_docs


# ============================================================
# HEALTH SERVER
# ============================================================
async def health_handler(request):
    return web.Response(text="OK")


async def start_health_server():
    server = web.Application()
    server.router.add_get("/", health_handler)
    server.router.add_get("/health", health_handler)

    runner = web.AppRunner(server)
    await runner.setup()

    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

    log.info("Health server listening on 0.0.0.0:%s", PORT)


# ============================================================
# INDEX
# ============================================================
async def index_channel(message):
    status = await message.reply_text("⏳ Starting channel indexing...")

    scanned = 0
    saved = 0
    updated = 0
    skipped = 0
    last_report = 0

    try:
        async for msg in app.get_chat_history(CHANNEL_ID):
            scanned += 1

            media = None
            media_type = None

            if msg.video:
                media = msg.video
                media_type = "video"
            elif msg.document:
                media = msg.document
                media_type = "document"
            elif msg.audio:
                media = msg.audio
                media_type = "audio"

            if media is None:
                continue

            file_name = getattr(media, "file_name", None) or f"message_{msg.id}"
            quality = detect_quality(file_name)
            source = detect_source(file_name)

            record = {
                "channel_id": CHANNEL_ID,
                "message_id": msg.id,
                "file_id": media.file_id,
                "file_name": file_name,
                "movie_name": normalize_name(file_name),
                "quality": quality,
                "source": source,
                "media_type": media_type,
                "mime_type": getattr(media, "mime_type", None),
                "file_size": int(getattr(media, "file_size", 0) or 0),
                "indexed_at": datetime.now(timezone.utc),
            }

            result = await files.update_one(
                {
                    "channel_id": CHANNEL_ID,
                    "message_id": msg.id,
                },
                {"$set": record},
                upsert=True,
            )

            if result.upserted_id is not None:
                saved += 1
            else:
                updated += 1

            if scanned - last_report >= 100:
                last_report = scanned
                await status.edit_text(
                    f"⏳ Indexing...\n\n"
                    f"Messages scanned: {scanned}\n"
                    f"New files: {saved}\n"
                    f"Updated: {updated}"
                )

        await status.edit_text(
            f"✅ Indexing completed.\n\n"
            f"Messages scanned: {scanned}\n"
            f"New files: {saved}\n"
            f"Updated: {updated}"
        )

    except Exception as e:
        log.exception("Index error")
        await status.edit_text(
            f"❌ Indexing error:\n<code>{str(e)[:1500]}</code>"
        )


# ============================================================
# DELETE EXECUTION
# ============================================================
async def delete_one(doc):
    """
    Telegram deletion MUST succeed before MongoDB record deletion.
    """
    message_id = int(doc["message_id"])

    try:
        await app.delete_messages(CHANNEL_ID, message_id)
    except FloodWait as e:
        await asyncio.sleep(e.value)
        await app.delete_messages(CHANNEL_ID, message_id)

    # Only after successful Telegram deletion:
    result = await files.delete_one({
        "channel_id": CHANNEL_ID,
        "message_id": message_id,
    })

    return result.deleted_count


async def execute_deletion(docs, status):
    deleted = 0
    failed = 0

    total = len(docs)

    for index, doc in enumerate(docs, 1):
        try:
            removed = await delete_one(doc)
            if removed:
                deleted += 1
        except RPCError as e:
            failed += 1
            log.error(
                "Telegram delete failed message_id=%s: %s",
                doc.get("message_id"),
                e,
            )
        except Exception:
            failed += 1
            log.exception(
                "Delete failed message_id=%s",
                doc.get("message_id"),
            )

        if index % 20 == 0 or index == total:
            try:
                await status.edit_text(
                    f"🗑 Deletion progress\n\n"
                    f"Processed: {index}/{total}\n"
                    f"Telegram deleted: {deleted}\n"
                    f"MongoDB removed: {deleted}\n"
                    f"Failed: {failed}"
                )
            except Exception:
                pass

    return deleted, failed


# ============================================================
# COMMAND HANDLERS
# ============================================================
@app.on_message(filters.private & filters.command("start"))
async def start_handler(client, message):
    user_id = message.from_user.id

    if user_id not in ADMIN_IDS:
        return await message.reply_text(
            f"⛔ Admin only.\n\nYour Telegram ID: <code>{user_id}</code>"
        )

    await message.reply_text(
        "🎬 <b>Movie Cleanup Bot</b>\n\n"
        "/ping - Bot test\n"
        "/index - Index channel\n"
        "/delete Movie Name - Preview one title\n"
        "/confirm_delete Movie Name - Delete one title\n"
        "/deleteall - Scan complete database\n"
        "/confirm_deleteall - Execute complete cleanup",
        parse_mode=enums.ParseMode.HTML,
    )


@app.on_message(filters.private & filters.command("ping"))
async def ping_handler(client, message):
    await message.reply_text(
        "🏓 <b>Bot is working!</b>\n\n"
        "Telegram: ✅\n"
        "Koyeb: ✅\n"
        f"Admin: {'✅' if message.from_user.id in ADMIN_IDS else '❌'}",
        parse_mode=enums.ParseMode.HTML,
    )


@app.on_message(filters.private & filters.command("index"))
async def index_handler(client, message):
    if message.from_user.id not in ADMIN_IDS:
        return await message.reply_text("⛔ Admin only.")

    await index_channel(message)


@app.on_message(filters.private & filters.command("delete"))
async def delete_handler(client, message):
    if message.from_user.id not in ADMIN_IDS:
        return await message.reply_text("⛔ Admin only.")

    if len(message.command) < 2:
        return await message.reply_text("Usage:\n/delete Movie Name")

    query = normalize_name(" ".join(message.command[1:]))

    docs = await files.find({
        "channel_id": CHANNEL_ID,
        "movie_name": {"$regex": re.escape(query), "$options": "i"},
    }).to_list(length=10000)

    if not docs:
        return await message.reply_text("ℹ️ No indexed files found.")

    keep, delete_docs = build_cleanup_plan(docs)

    preview = "\n".join(
        f"❌ {d.get('file_name', 'unknown')}"
        for d in delete_docs[:30]
    ) or "Nothing"

    await message.reply_text(
        f"🎬 <b>{' '.join(message.command[1:])}</b>\n\n"
        f"Indexed: {len(docs)}\n"
        f"Keep: {len(keep)}\n"
        f"Delete: {len(delete_docs)}\n\n"
        f"<b>Delete preview:</b>\n{preview}\n\n"
        f"Run:\n<code>/confirm_delete {' '.join(message.command[1:])}</code>",
        parse_mode=enums.ParseMode.HTML,
    )


@app.on_message(filters.private & filters.command("confirm_delete"))
async def confirm_delete_handler(client, message):
    if message.from_user.id not in ADMIN_IDS:
        return await message.reply_text("⛔ Admin only.")

    if len(message.command) < 2:
        return await message.reply_text("Usage:\n/confirm_delete Movie Name")

    query = normalize_name(" ".join(message.command[1:]))

    docs = await files.find({
        "channel_id": CHANNEL_ID,
        "movie_name": {"$regex": re.escape(query), "$options": "i"},
    }).to_list(length=10000)

    if not docs:
        return await message.reply_text("ℹ️ No indexed files found.")

    keep, delete_docs = build_cleanup_plan(docs)

    if not delete_docs:
        return await message.reply_text("✅ Nothing to delete.")

    status = await message.reply_text(
        f"🗑 Starting deletion of {len(delete_docs)} files..."
    )

    deleted, failed = await execute_deletion(delete_docs, status)

    await status.edit_text(
        f"✅ <b>Cleanup completed</b>\n\n"
        f"Telegram deleted: {deleted}\n"
        f"MongoDB removed: {deleted}\n"
        f"Failed: {failed}\n"
        f"Kept: {len(keep)}",
        parse_mode=enums.ParseMode.HTML,
    )


@app.on_message(filters.private & filters.command("deleteall"))
async def deleteall_handler(client, message):
    if message.from_user.id not in ADMIN_IDS:
        return await message.reply_text("⛔ Admin only.")

    status = await message.reply_text("🔎 Scanning complete MongoDB index...")

    docs = await files.find({"channel_id": CHANNEL_ID}).to_list(length=200000)

    if not docs:
        return await status.edit_text(
            "ℹ️ Database index is empty.\nRun /index first."
        )

    keep, delete_docs = build_cleanup_plan(docs)

    documents = sum(1 for d in docs if not is_video_record(d))
    bad = sum(1 for d in docs if is_video_record(d) and is_bad_source(d))

    preview = "\n".join(
        f"❌ {d.get('file_name', 'unknown')}"
        for d in delete_docs[:40]
    ) or "Nothing"

    await status.edit_text(
        "🔎 <b>DELETEALL PREVIEW</b>\n\n"
        f"Total indexed: {len(docs)}\n"
        f"Documents: {documents}\n"
        f"Bad-source videos: {bad}\n"
        f"Delete: {len(delete_docs)}\n"
        f"Keep: {len(keep)}\n\n"
        "<b>Series rule</b>\n"
        "• Each episode keeps 480p + 720p + 1080p\n"
        "• Same quality: WEB-DL > WEBRip > HDRip\n"
        "• Combined/Complete/Completed/Full Season/Batch/Multi-Episode protected\n"
        "• Movies keep one best file per quality\n\n"
        "<b>Delete preview</b>\n"
        f"{preview}\n\n"
        "⚠️ Nothing has been deleted yet.\n\n"
        "Run <code>/confirm_deleteall</code> to execute.",
        parse_mode=enums.ParseMode.HTML,
    )


@app.on_message(filters.private & filters.command("confirm_deleteall"))
async def confirm_deleteall_handler(client, message):
    if message.from_user.id not in ADMIN_IDS:
        return await message.reply_text("⛔ Admin only.")

    status = await message.reply_text(
        "🔎 Re-scanning database before deletion..."
    )

    docs = await files.find({"channel_id": CHANNEL_ID}).to_list(length=200000)

    if not docs:
        return await status.edit_text("ℹ️ Database is empty.")

    keep, delete_docs = build_cleanup_plan(docs)

    if not delete_docs:
        return await status.edit_text("✅ Database is already clean.")

    await status.edit_text(
        f"🗑 Starting cleanup...\n\n"
        f"Delete: {len(delete_docs)}\n"
        f"Keep: {len(keep)}"
    )

    deleted, failed = await execute_deletion(delete_docs, status)

    await status.edit_text(
        f"✅ <b>DELETEALL completed</b>\n\n"
        f"Telegram deleted: {deleted}\n"
        f"MongoDB removed: {deleted}\n"
        f"Failed: {failed}\n"
        f"Remaining planned keep: {len(keep)}",
        parse_mode=enums.ParseMode.HTML,
    )


# ============================================================
# STARTUP
# ============================================================
async def startup_checks():
    await mongo.admin.command("ping")
    await files.create_index(
        [("channel_id", ASCENDING), ("message_id", ASCENDING)],
        unique=True,
    )
    await files.create_index([("channel_id", ASCENDING), ("movie_name", ASCENDING)])
    await app.get_chat(CHANNEL_ID)


async def main():
    await start_health_server()

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
        await startup_checks()
        log.info("MongoDB: OK")
        log.info("Channel access: OK")
        log.info("Indexes: OK")
    except Exception:
        log.exception("STARTUP CHECK FAILED")
        await app.stop()
        raise

    log.info("Command handlers: READY")
    log.info("Bot is waiting for Telegram messages")

    await idle()

    await app.stop()
    mongo.close()


if __name__ == "__main__":
    asyncio.run(main())
