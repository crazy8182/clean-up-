# Movie Cleanup Bot - Final

## Commands

/index
/delete Movie or Series Name
/confirm_delete Movie or Series Name
/deleteall
/confirm_deleteall

## DELETEALL rules

### Documents
All non-video/document media indexed in MongoDB is deleted.

### Movies
For recognized movie video files:
- keep one 480p
- keep one 720p
- keep one 1080p
- source priority: WEB-DL > WEBRip > HDRip
- CAM/CAMRip/HDTC/HDTS/HDCAM/TS/TC are deleted

### TV series individual episodes
For each `SxxExx`:
- keep one best 480p
- keep one best 720p
- keep one best 1080p

Example:
Money Heist S01E01 480p WEB-DL -> KEEP
Money Heist S01E01 720p WEB-DL -> KEEP
Money Heist S01E01 1080p WEB-DL -> KEEP

Duplicate source at same quality is removed according to source priority.

### Combined / complete / batch files
The following are protected from episode-level duplicate logic:
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
- episode ranges such as S01E01-E05

For these, one best file per quality is kept.

## SAFETY

/deleteall is a two-step process.

1. /deleteall only scans and creates a preview.
2. /confirm_deleteall performs deletion.

For every file:
1. Telegram message is deleted first.
2. MongoDB record is deleted only after Telegram deletion succeeds.
3. If Telegram deletion fails, MongoDB record remains.

Unknown/non-standard video names are conservatively kept instead of being automatically deleted.

## Koyeb

Use Web Service.

Health endpoint:
`/health`

The application listens on:
`0.0.0.0:$PORT`

Recommended HTTP health check:
- Port: 8080 (or the Koyeb exposed PORT)
- Path: /health

The bot must be admin in the channel with permission to delete messages.
