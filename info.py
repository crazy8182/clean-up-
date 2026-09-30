
import os
from os import environ

SESSION = environ.get("SESSION", "imax_clean_bot")
API_ID = int(environ["API_ID"])
API_HASH = environ["API_HASH"]
BOT_TOKEN = environ["BOT_TOKEN"]

ADMIN_IDS = {
    int(x.strip())
    for x in environ.get("ADMIN_IDS", "").replace(",", " ").split()
    if x.strip()
}

CHANNEL_ID = int(environ["CHANNEL_ID"])

DATABASE_URI = environ["MONGO_URI"]
DATABASE_NAME = environ.get("DB_NAME", "movie_cleanup")

PORT = int(environ.get("PORT", "8080"))
