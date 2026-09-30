
import os
import re
import asyncio
import logging
from collections import defaultdict

from aiohttp import web
from motor.motor_asyncio import AsyncIOMotorClient
from pyrogram import Client, filters
from pyrogram.errors import FloodWait

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("movie-cleanup")

API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
BOT_TOKEN = os.environ["BOT_TOKEN"]
MONGO_URI = os.environ["MONGO_URI"]
DB_NAME = os.getenv("DB_NAME", "movie_cleanup")
CHANNEL_ID = int(os.environ["CHANNEL_ID"])
ADMIN_IDS = {int(x.strip()) for x in os.environ["ADMIN_IDS"].split(",") if x.strip()}

app = Client("movie_cleanup_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
mongo = AsyncIOMotorClient(MONGO_URI)
db = mongo[DB_NAME]
files_col = db["files"]

# Only these three quality labels are eligible for normal retention.
KEEP_QUALITIES = {"480p", "720p", "1080p"}

# Same-quality source priority: lower number wins.
SOURCE_PRIORITY = {"WEB-DL": 1, "WEBRip": 2, "HDRip": 3}

BAD_SOURCES = {
    "CAM", "CAMRIP", "HDCAM", "HDTC", "HDTS", "TS", "TC",
    "TELECINE", "TELESYNC"
}

VIDEO_EXTENSIONS = {
    ".mp4", ".mkv", ".avi", ".mov", ".m4v", ".webm",
    ".ts", ".m2ts", ".wmv", ".flv", ".mpeg", ".mpg"
}

# These indicate a season/collection/multi-episode file.
COMBINED_PATTERNS = [
    r"\bcomplete\b",
    r"\bcompleted\b",
    r"\bcombined\b",
    r"\bcomplete[ ._-]*season\b",
    r"\bfull[ ._-]*season\b",
    r"\bseason[ ._-]*pack\b",
    r"\bseason[ ._-]*batch\b",
    r"\bbatch\b",
    r"\bmulti[ ._-]*episode\b",
    r"\ball[ ._-]*episodes\b",
    r"\bentire[ ._-]*season\b",
    r"\bcollection\b",
    r"\bcomplete[ ._-]*collection\b",
]

# A range such as S01E01-E05 is also a combined/multi-episode file.
EPISODE_RANGE_PATTERNS = [
    r"\bS\d{1,2}E\d{1,3}\s*[-_–]\s*(?:E)?\d{1,3}\b",
    r"\bE\d{1,3}\s*[-_–]\s*(?:E)?\d{1,3}\b",
    r"\b\d{1,3}\s*[-_–]\s*\d{1,3}\b",
]

EPISODE_PATTERN = re.compile(r"\bS(\d{1,2})\s*E(\d{1,3})\b", re.I)
SEASON_PATTERN = re.compile(r"\bS(\d{1,2})\b|\bSEASON\s*(\d{1,2})\b", re.I)

def extension(name):
    return os.path.splitext((name or "").lower())[1]

def is_video(doc):
    if doc.get("media_type") == "video":
        return True
    return extension(doc.get("file_name", "")) in VIDEO_EXTENSIONS

def is_document(doc):
    return not is_video(doc)

def normalize_name(name):
    s = (name or "").lower()
    s = re.sub(r"\[[^\]]*\]|\([^)]*\)|\{[^}]*\}", " ", s)
    s = re.sub(r"[\._\-]+", " ", s)
    s = re.sub(r"\b(480p|720p|1080p|2160p|4k|8k)\b", " ", s, flags=re.I)
    s = re.sub(
        r"\b(web[ -]?dl|web[ -]?rip|hdrip|camrip|cam|hdtc|hdts|hdcam|ts|tc)\b",
        " ", s, flags=re.I
    )
    s = re.sub(r"\s+", " ", s).strip()
    return s

def detect_quality(name):
    m = re.search(r"\b(480p|720p|1080p|2160p|4k)\b", name or "", re.I)
    return m.group(1).lower() if m else "unknown"

def detect_source(name):
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

def is_bad_source(doc):
    return str(doc.get("source", "unknown")).upper() in BAD_SOURCES

def combined_type(name):
    n = (name or "").lower().replace("_", " ").replace("-", " ")
    for p in COMBINED_PATTERNS:
        if re.search(p, n, re.I):
            return True
    for p in EPISODE_RANGE_PATTERNS:
        if re.search(p, n, re.I):
            return True
    return False

def episode_info(name):
    m = EPISODE_PATTERN.search(name or "")
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))

def season_info(name):
    m = SEASON_PATTERN.search(name or "")
    if not m:
        return None
    return int(m.group(1) or m.group(2))

def source_rank(doc):
    return SOURCE_PRIORITY.get(doc.get("source"), 99)

def choose_best(docs):
    """Choose one best file from a group, based on source priority."""
    return sorted(
        docs,
        key=lambda d: (
            source_rank(d),
            -int(d.get("file_size") or 0),
            int(d.get("message_id") or 0)
        )
    )[0]

def series_key(doc):
    # Prefer normalized name; remove episode and combined labels to group a series.
    n = normalize_name(doc.get("file_name", ""))
    n = re.sub(r"\bS\d{1,2}E\d{1,3}\b", " ", n, flags=re.I)
    n = re.sub(r"\bSEASON\s*\d{1,2}\b", " ", n, flags=re.I)
    n = re.sub(r"\bS\d{1,2}\b", " ", n, flags=re.I)
    n = re.sub(r"\b(complete|completed|combined|full season|season pack|season batch|batch|multi episode|all episodes|entire season|collection)\b", " ", n, flags=re.I)
    return re.sub(r"\s+", " ", n).strip()

def plan_deleteall(docs):
    """
    Global cleanup rules:

    1. Documents: DELETE.
    2. Bad-source videos: DELETE.
    3. TV individual episodes:
       - keep one best 480p, one best 720p, one best 1080p per episode.
    4. TV combined/complete/multi-episode files:
       - preserve one best file per quality.
    5. Movies:
       - keep one best 480p, one best 720p, one best 1080p.
    6. Unknown/unclassifiable videos are conservatively kept.
    """
    keep_ids = set()
    groups = defaultdict(list)

    # Documents and clearly bad sources can be decided immediately.
    delete_ids = set()
    for d in docs:
        if is_document(d):
            delete_ids.add(d["_id"])
        elif is_bad_source(d):
            delete_ids.add(d["_id"])

    candidates = [
        d for d in docs
        if d["_id"] not in delete_ids
        and is_video(d)
        and d.get("quality") in KEEP_QUALITIES
        and d.get("source") in SOURCE_PRIORITY
    ]

    # Group likely TV episode files.
    for d in candidates:
        ep = episode_info(d.get("file_name", ""))
        season = season_info(d.get("file_name", ""))
        if ep and season:
            key = ("episode", series_key(d), ep[0], ep[1], d["quality"])
            groups[key].append(d)
        elif season and combined_type(d.get("file_name", "")):
            key = ("combined", series_key(d), season, d["quality"])
            groups[key].append(d)
        else:
            key = ("movie", normalize_name(d.get("file_name", "")), d["quality"])
            groups[key].append(d)

    for key, items in groups.items():
        best = choose_best(items)
        keep_ids.add(best["_id"])

    # Unknown video files are kept for safety, rather than silently deleting them.
    for d in docs:
        if d["_id"] not in delete_ids and d["_id"] not in keep_ids:
            if is_video(d):
                # It may be a non-standard filename or unsupported quality.
                # Keep it conservatively.
                if d.get("quality") not in KEEP_QUALITIES or d.get("source") not in SOURCE_PRIORITY:
                    keep_ids.add(d["_id"])

    delete_docs = [d for d in docs if d["_id"] not in keep_ids]
    keep_docs = [d for d in docs if d["_id"] in keep_ids]
    return keep_docs, delete_docs

async def health(request):
    return web.Response(text="OK")

async def start_health_server():
    port = int(os.getenv("PORT", "8080"))
    server = web.Application()
    server.router.add_get("/", health)
    server.router.add_get("/health", health)
    runner = web.AppRunner(server)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    log.info("Health server listening on 0.0.0.0:%s", port)

@app.on_message(filters.command("start") & filters.private)
async def start_cmd(client, message):
    if message.from_user.id not in ADMIN_IDS:
        return await message.reply_text("⛔ Admin only.")
    await message.reply_text(
        "🎬 Movie Cleanup Bot\n\n"
        "/index - index channel\n"
        "/delete Movie/Series - preview one title\n"
        "/confirm_delete Movie/Series - execute one-title cleanup\n"
        "/deleteall - scan complete database and preview cleanup\n"
        "/confirm_deleteall - execute complete cleanup\n\n"
        "Series keep one best 480p + 720p + 1080p per episode.\n"
        "Combined/Complete/Batch/Multi-Episode files are protected."
    )

@app.on_message(filters.command("index") & filters.private)
async def index_cmd(client, message):
    if message.from_user.id not in ADMIN_IDS:
        return await message.reply_text("⛔ Admin only.")
    status = await message.reply_text("⏳ Indexing channel files...")
    count = 0

    async for msg in client.get_chat_history(CHANNEL_ID):
        media = msg.document or msg.video or msg.audio
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
            "file_size": getattr(media, "file_size", 0) or 0,
        }

        await files_col.update_one(
            {"channel_id": CHANNEL_ID, "message_id": msg.id},
            {"$set": doc},
            upsert=True
        )
        count += 1

        if count % 100 == 0:
            await status.edit_text(f"⏳ Indexed: {count}")

    await status.edit_text(f"✅ Index complete.\nFiles indexed: {count}")

async def delete_telegram_then_mongo(client, doc):
    try:
        await client.delete_messages(CHANNEL_ID, int(doc["message_id"]))
    except FloodWait as e:
        await asyncio.sleep(e.value)
        await client.delete_messages(CHANNEL_ID, int(doc["message_id"]))

    # Only after successful Telegram deletion:
    await files_col.delete_one({
        "channel_id": CHANNEL_ID,
        "message_id": int(doc["message_id"])
    })

async def run_deletion(client, docs, progress):
    deleted = 0
    failed = 0

    for d in docs:
        try:
            await delete_telegram_then_mongo(client, d)
            deleted += 1
        except Exception as e:
            failed += 1
            log.exception("Deletion failed message=%s: %s", d.get("message_id"), e)

        processed = deleted + failed
        if processed % 20 == 0 or processed == len(docs):
            await progress.edit_text(
                f"🗑 Progress: {processed}/{len(docs)}\n"
                f"Telegram deleted: {deleted}\n"
                f"MongoDB removed: {deleted}\n"
                f"Failed: {failed}"
            )

    return deleted, failed

@app.on_message(filters.command("deleteall") & filters.private)
async def deleteall_cmd(client, message):
    if message.from_user.id not in ADMIN_IDS:
        return await message.reply_text("⛔ Admin only.")

    status = await message.reply_text("🔎 Scanning complete MongoDB index...")
    docs = await files_col.find({"channel_id": CHANNEL_ID}).to_list(length=100000)

    if not docs:
        return await status.edit_text("ℹ️ MongoDB index is empty. Run /index first.")

    keep, delete_docs = plan_deleteall(docs)

    document_count = sum(1 for d in docs if is_document(d))
    bad_count = sum(1 for d in docs if is_video(d) and is_bad_source(d))

    # Keep only a compact preview in Telegram.
    sample = []
    for d in delete_docs[:30]:
        sample.append("❌ " + d.get("file_name", "unknown"))

    text = (
        "🔎 DELETEALL PREVIEW\n\n"
        f"Total indexed: {len(docs)}\n"
        f"Documents: {document_count}\n"
        f"Bad-source videos: {bad_count}\n"
        f"To delete: {len(delete_docs)}\n"
        f"To keep: {len(keep)}\n\n"
        "Series rule:\n"
        "• 480p + 720p + 1080p per individual episode\n"
        "• Combined/Complete/Completed/Full Season/Batch/Multi-Episode protected\n"
        "• Movies: one best file per quality\n\n"
        "Delete preview:\n"
        + ("\n".join(sample) if sample else "Nothing")
        + (
            f"\n... +{len(delete_docs)-30} more" if len(delete_docs) > 30 else ""
        )
        + "\n\n⚠️ No files have been deleted yet.\n"
          "Run /confirm_deleteall to execute."
    )
    await status.edit_text(text)

@app.on_message(filters.command("confirm_deleteall") & filters.private)
async def confirm_deleteall_cmd(client, message):
    if message.from_user.id not in ADMIN_IDS:
        return await message.reply_text("⛔ Admin only.")

    status = await message.reply_text("🔎 Re-scanning MongoDB before deletion...")
    docs = await files_col.find({"channel_id": CHANNEL_ID}).to_list(length=100000)

    if not docs:
        return await status.edit_text("ℹ️ Nothing indexed.")

    keep, delete_docs = plan_deleteall(docs)

    if not delete_docs:
        return await status.edit_text("✅ Database is already clean.")

    await status.edit_text(
        f"🗑 Starting DELETEALL\n"
        f"Files to delete: {len(delete_docs)}\n"
        f"Files to keep: {len(keep)}"
    )

    deleted, failed = await run_deletion(client, delete_docs, status)

    await status.edit_text(
        f"✅ DELETEALL completed\n\n"
        f"Telegram deleted: {deleted}\n"
        f"MongoDB records removed: {deleted}\n"
        f"Failed: {failed}\n\n"
        f"Remaining indexed files: {len(keep) + failed}"
    )

@app.on_message(filters.command("delete") & filters.private)
async def delete_cmd(client, message):
    if message.from_user.id not in ADMIN_IDS:
        return await message.reply_text("⛔ Admin only.")

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        return await message.reply_text("Usage: /delete Movie Name")

    query = normalize_name(parts[1])
    docs = await files_col.find({
        "channel_id": CHANNEL_ID,
        "movie_name": {"$regex": re.escape(query), "$options": "i"}
    }).to_list(length=5000)

    if not docs:
        return await message.reply_text("ℹ️ No indexed files found.")

    keep, delete_docs = plan_deleteall(docs)

    text = (
        f"🎬 {parts[1]}\n\n"
        f"Found: {len(docs)}\n"
        f"Delete: {len(delete_docs)}\n"
        f"Keep: {len(keep)}\n\n"
        "⚠️ Preview only.\n"
        f"Run /confirm_delete {parts[1]} to delete."
    )
    await message.reply_text(text)

@app.on_message(filters.command("confirm_delete") & filters.private)
async def confirm_delete_cmd(client, message):
    if message.from_user.id not in ADMIN_IDS:
        return await message.reply_text("⛔ Admin only.")

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        return await message.reply_text("Usage: /confirm_delete Movie Name")

    query = normalize_name(parts[1])
    docs = await files_col.find({
        "channel_id": CHANNEL_ID,
        "movie_name": {"$regex": re.escape(query), "$options": "i"}
    }).to_list(length=5000)

    if not docs:
        return await message.reply_text("ℹ️ No indexed files found.")

    keep, delete_docs = plan_deleteall(docs)

    if not delete_docs:
        return await message.reply_text("✅ Nothing to delete.")

    progress = await message.reply_text(
        f"🗑 Starting cleanup: {len(delete_docs)} files"
    )
    deleted, failed = await run_deletion(client, delete_docs, progress)

    await progress.edit_text(
        f"✅ Cleanup completed\n\n"
        f"Telegram deleted: {deleted}\n"
        f"MongoDB removed: {deleted}\n"
        f"Failed: {failed}\n"
        f"Kept: {len(keep)}"
    )

async def main():
    await start_health_server()
    await app.start()
    log.info("Bot started")
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
