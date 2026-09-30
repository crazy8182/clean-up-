
import logging
from pyrogram import Client, filters

from info import ADMIN_IDS

log = logging.getLogger("movie-cleanup")


def admin_only(user_id):
    return user_id in ADMIN_IDS


@Client.on_message(filters.private & filters.command("ping"))
async def ping_handler(client, message):
    user_id = message.from_user.id if message.from_user else 0

    log.info(
        "PING RECEIVED | user_id=%s | username=%s | chat_id=%s",
        user_id,
        message.from_user.username if message.from_user else None,
        message.chat.id if message.chat else None,
    )

    if not admin_only(user_id):
        return await message.reply_text(
            f"⛔ Admin only.\nYour Telegram ID: `{user_id}`"
        )

    await message.reply_text(
        "🏓 **Bot is working!**\n\n"
        "Telegram: ✅\n"
        "Koyeb: ✅\n"
        "Pyrofork: ✅"
    )


@Client.on_message(filters.private & filters.command("start"))
async def start_handler(client, message):
    user_id = message.from_user.id if message.from_user else 0

    log.info(
        "START RECEIVED | user_id=%s | chat_id=%s",
        user_id,
        message.chat.id if message.chat else None,
    )

    if not admin_only(user_id):
        return await message.reply_text(
            f"⛔ Admin only.\nYour Telegram ID: `{user_id}`"
        )

    await message.reply_text(
        "🎬 **Movie Cleanup Bot**\n\n"
        "/ping - test bot\n"
        "/index - index channel\n"
        "/delete Movie Name - preview cleanup\n"
        "/confirm_delete Movie Name - execute cleanup\n"
        "/deleteall - scan complete database\n"
        "/confirm_deleteall - execute full cleanup"
    )
