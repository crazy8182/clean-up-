
import logging
import re
import asyncio
from collections import defaultdict

from pyrogram import Client, filters
from pyrogram.errors import FloodWait

from info import ADMIN_IDS, CHANNEL_ID
from bot import files_col

log = logging.getLogger("movie-cleanup")

KEEP_QUALITIES = {"480p", "720p", "1080p"}
SOURCE_PRIORITY = {"WEB-DL": 1, "WEBRip": 2, "HDRip": 3}

BAD_SOURCES = {
    "CAM", "CAMRIP", "HDCAM", "HDTC", "HDTS",
    "TS", "TC", "TELECINE", "TELESYNC"
}

VIDEO_EXTENSIONS = {
    ".mp4", ".mkv", ".avi", ".mov", ".m4v", ".webm",
    ".ts", ".m2ts", ".wmv", ".flv", ".mpeg", ".mpg"
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
]

EPISODE = re.compile(r"\bS(\d{1,2})\s*E(\d{1,3})\b", re.I)
SEASON = re.compile(r"\bS(\d{1,2})\b|\bSEASON\s*(\d{1,2})\b", re.I)


def is_admin(message):
    return bool(message.from_user and message.from_user.id in ADMIN_IDS)


def ext(name):
    return re.search(r"(\.[A-Za-z0-9]{1,8})$", name or "").group(1).lower() if re.search(r"(\.[A-Za-z0-9]{1,8})$", name or "") else ""


def is_video(d):
    return d.get("media_type") == "video" or ext(d.get("file_name")) in VIDEO_EXTENSIONS


def normalize(name):
    s = (name or "").lower()
    s = re.sub(r"[\[\]\(\)\{\}]", " ", s)
    s = re.sub(r"[\._-]+", " ", s)
    s = re.sub(r"\b(480p|720p|1080p|2160p|4k)\b", " ", s)
    s = re.sub(r"\b(web[ -]?dl|web[ -]?rip|hdrip|camrip|cam|hdtc|hdts|hdcam|ts|tc)\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def quality(name):
    m = re.search(r"\b(480p|720p|1080p|2160p|4k)\b", name or "", re.I)
    return m.group(1).lower() if m else "unknown"


def source(name):
    n = (name or "").upper().replace("_", " ")
    for label, pattern in [
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
    ]:
        if re.search(pattern, n):
            return label
    return "unknown"


def bad_source(d):
    return str(d.get("source", "unknown")).upper() in BAD_SOURCES


def combined(name):
    n = (name or "").lower().replace("_", " ").replace("-", " ")
    if any(re.search(p, n, re.I) for p in COMBINED_PATTERNS):
        return True
    return bool(re.search(
        r"\bS\d{1,2}E\d{1,3}\s*[-–]\s*(?:E)?\d{1,3}\b", n, re.I
    ))


def episode(name):
    m = EPISODE.search(name or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


def season(name):
    m = SEASON.search(name or "")
    return int(m.group(1) or m.group(2)) if m else None


def source_rank(d):
    return SOURCE_PRIORITY.get(d.get("source"), 99)


def best(items):
    return sorted(
        items,
        key=lambda d: (
            source_rank(d),
            -int(d.get("file_size") or 0),
            int(d.get("message_id") or 0),
        )
    )[0]


def series_key(d):
    s = normalize(d.get("file_name", ""))
    s = re.sub(r"\bS\d{1,2}E\d{1,3}\b", " ", s, flags=re.I)
    s = re.sub(r"\bS\d{1,2}\b", " ", s, flags=re.I)
    s = re.sub(r"\bSEASON\s*\d{1,2}\b", " ", s, flags=re.I)
    return re.sub(
        r"\b(complete|completed|combined|full season|season pack|season batch|batch|multi episode|all episodes|entire season|collection)\b",
        " ", s, flags=re.I
    ).strip()


def make_plan(docs):
    groups = defaultdict(list)
    keep = set()

    # Always delete documents and unwanted sources.
    forced_delete = {
        d["_id"] for d in docs
        if not is_video(d) or bad_source(d)
    }

    candidates = [
        d for d in docs
        if d["_id"] not in forced_delete
        and d.get("quality") in KEEP_QUALITIES
        and d.get("source") in SOURCE_PRIORITY
    ]

    for d in candidates:
        ep = episode(d.get("file_name", ""))
        se = season(d.get("file_name", ""))

        if ep and se:
            key = ("episode", series_key(d), ep[0], ep[1], d["quality"])
        elif se and combined(d.get("file_name", "")):
            key = ("combined", series_key(d), se, d["quality"])
        else:
            key = ("movie", normalize(d.get("file_name", "")), d["quality"])

        groups[key].append(d)

    for items in groups.values():
        keep.add(best(items)["_id"])

    # Conservative: unknown video files stay.
    for d in docs:
        if d["_id"] not in forced_delete and d["_id"] not in keep and is_video(d):
            keep.add(d["_id"])

    return [d for d in docs if d["_id"] in keep], [d for d in docs if d["_id"] not in keep]


@Client.on_message(filters.private & filters.command("index"))
async def index_handler(client, message):
    if not is_admin(message):
        return await message.reply_text("⛔ Admin only.")

    status = await message.reply_text("⏳ Indexing...")
    count = 0

    async for msg in client.get_chat_history(CHANNEL_ID):
        media = msg.video or msg.document or msg.audio
        if not media:
            continue

        name = getattr(media, "file_name", None) or f"message_{msg.id}"

        await files_col.update_one(
            {"channel_id": CHANNEL_ID, "message_id": msg.id},
            {"$set": {
                "channel_id": CHANNEL_ID,
                "message_id": msg.id,
                "file_id": media.file_id,
                "file_name": name,
                "movie_name": normalize(name),
                "quality": quality(name),
                "source": source(name),
                "media_type": "video" if msg.video else ("document" if msg.document else "audio"),
                "file_size": getattr(media, "file_size", 0) or 0,
            }},
            upsert=True,
        )

        count += 1
        if count % 100 == 0:
            await status.edit_text(f"⏳ Indexed: {count}")

    await status.edit_text(f"✅ Index complete\nFiles: {count}")


async def find_title(title):
    q = normalize(title)
    return await files_col.find({
        "channel_id": CHANNEL_ID,
        "movie_name": {"$regex": re.escape(q), "$options": "i"},
    }).to_list(length=5000)


async def delete_one(client, d):
    try:
        await client.delete_messages(CHANNEL_ID, int(d["message_id"]))
    except FloodWait as e:
        await asyncio.sleep(e.value)
        await client.delete_messages(CHANNEL_ID, int(d["message_id"]))

    # MongoDB only after Telegram deletion succeeds.
    await files_col.delete_one({
        "channel_id": CHANNEL_ID,
        "message_id": int(d["message_id"]),
    })


@Client.on_message(filters.private & filters.command("delete"))
async def delete_handler(client, message):
    if not is_admin(message):
        return await message.reply_text("⛔ Admin only.")

    title = message.text.split(maxsplit=1)[1] if len(message.text.split(maxsplit=1)) > 1 else ""
    if not title:
        return await message.reply_text("Usage: /delete Movie Name")

    docs = await find_title(title)
    if not docs:
        return await message.reply_text("ℹ️ No indexed files found.")

    keep, remove = make_plan(docs)
    await message.reply_text(
        f"🎬 {title}\n\n"
        f"Found: {len(docs)}\n"
        f"Delete: {len(remove)}\n"
        f"Keep: {len(keep)}\n\n"
        f"Run /confirm_delete {title}"
    )


@Client.on_message(filters.private & filters.command("confirm_delete"))
async def confirm_delete_handler(client, message):
    if not is_admin(message):
        return await message.reply_text("⛔ Admin only.")

    title = message.text.split(maxsplit=1)[1] if len(message.text.split(maxsplit=1)) > 1 else ""
    if not title:
        return await message.reply_text("Usage: /confirm_delete Movie Name")

    docs = await find_title(title)
    keep, remove = make_plan(docs)

    if not remove:
        return await message.reply_text("✅ Nothing to delete.")

    status = await message.reply_text(f"🗑 Deleting 0/{len(remove)}")
    deleted = failed = 0

    for d in remove:
        try:
            await delete_one(client, d)
            deleted += 1
        except Exception:
            failed += 1
            log.exception("Delete failed: %s", d.get("message_id"))

        if (deleted + failed) % 20 == 0:
            await status.edit_text(
                f"🗑 Progress: {deleted + failed}/{len(remove)}\n"
                f"Deleted: {deleted}\nFailed: {failed}"
            )

    await status.edit_text(
        f"✅ Completed\nTelegram deleted: {deleted}\n"
        f"MongoDB removed: {deleted}\nFailed: {failed}\nKept: {len(keep)}"
    )


@Client.on_message(filters.private & filters.command("deleteall"))
async def deleteall_handler(client, message):
    if not is_admin(message):
        return await message.reply_text("⛔ Admin only.")

    status = await message.reply_text("🔎 Scanning MongoDB...")
    docs = await files_col.find({"channel_id": CHANNEL_ID}).to_list(length=100000)

    if not docs:
        return await status.edit_text("ℹ️ Database index is empty. Run /index.")

    keep, remove = make_plan(docs)

    await status.edit_text(
        f"🔎 DELETEALL PREVIEW\n\n"
        f"Total: {len(docs)}\n"
        f"To delete: {len(remove)}\n"
        f"To keep: {len(keep)}\n\n"
        f"⚠️ Nothing deleted.\n"
        f"Run /confirm_deleteall to execute."
    )


@Client.on_message(filters.private & filters.command("confirm_deleteall"))
async def confirm_deleteall_handler(client, message):
    if not is_admin(message):
        return await message.reply_text("⛔ Admin only.")

    status = await message.reply_text("🔎 Re-scanning...")
    docs = await files_col.find({"channel_id": CHANNEL_ID}).to_list(length=100000)

    keep, remove = make_plan(docs)

    if not remove:
        return await status.edit_text("✅ Nothing to delete.")

    deleted = failed = 0

    for d in remove:
        try:
            await delete_one(client, d)
            deleted += 1
        except Exception:
            failed += 1
            log.exception("Delete failed: %s", d.get("message_id"))

        if (deleted + failed) % 20 == 0:
            await status.edit_text(
                f"🗑 Progress: {deleted + failed}/{len(remove)}\n"
                f"Deleted: {deleted}\nFailed: {failed}"
            )

    await status.edit_text(
        f"✅ DELETEALL completed\n\n"
        f"Telegram deleted: {deleted}\n"
        f"MongoDB removed: {deleted}\n"
        f"Failed: {failed}\n"
        f"Kept: {len(keep)}"
    )
