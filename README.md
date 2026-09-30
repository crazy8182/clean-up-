# Movie Cleanup Bot - DreamX Reference Build

Standalone cleanup/indexing bot built with the same Pyrofork stack used by the supplied Auto Filter reference.

## Required environment variables

API_ID
API_HASH
BOT_TOKEN
MONGO_URI
CHANNEL_ID
ADMIN_IDS

Optional:
DB_NAME=movie_cleanup
COLLECTION_NAME=files
PORT=8080

`ADMIN_IDS` accepts space or comma separated Telegram user IDs.

## Commands

/start
/ping
/index
/delete Movie Name
/confirm_delete Movie Name
/deleteall
/confirm_deleteall

## Indexing

`/index` scans the configured channel history and stores:
- Telegram message_id
- file_id
- file_name
- movie_name
- quality
- source
- Telegram media_type
- mime_type
- file_size

Telegram `document` and `video` are intentionally stored with different media_type values.

## Cleanup rules

### Documents
Anything indexed as Telegram `document` is deleted by cleanup, even if the filename ends in `.mkv` or `.mp4`.

### Movies
Maximum one video for each:
- 480p
- 720p
- 1080p

At the same quality:
WEB-DL > WEBRip > HDRip

CAM/CAMRip/HDTC/HDTS/HDCAM/TS/TC are deleted.

### TV episodes
For every S01E01-style episode:
- one 480p
- one 720p
- one 1080p

are retained.

### Combined / Complete files
These are protected:
- Combined
- Complete
- Completed
- Full Season
- Complete Season
- Season Pack
- Season Batch
- Batch
- Multi-Episode
- All Episodes
- Entire Season
- Collection
- S01E01-E05 style ranges

For protected combined/season files, one best file per quality is retained.

### Safety
Unknown/non-standard video files are kept conservatively.

For every deletion:
1. Telegram message is deleted first.
2. MongoDB record is deleted only after Telegram deletion succeeds.
3. If Telegram deletion fails, its MongoDB record remains.

## Koyeb

Use Web Service.
HTTP health check:
- Path: `/health`
- Port: 8080 (or Koyeb `$PORT`)

The process binds to `0.0.0.0:$PORT`.

The bot uses Pyrofork 2.3.69, matching the supplied reference project stack.
