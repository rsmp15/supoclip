# Live clipping (YouTube + Twitch live streams)

SupoClip normally clips finished videos. **Live clipping** watches live streams while they happen:

1. it records a rolling 10-minute buffer;
2. it reads the live chat;
3. it detects the moments when chat explodes;
4. it cuts each moment and edits it into a 9:16 Short;
5. it puts every clip in a review queue on **/live**.

Approved clips are published from there, through SupoClip's own social accounts, Postiz, or the YouTube API.

It is a **single-machine** feature, for self-hosted and local installs. Engine processes, the clip files and the API
share one disk. Turn it on with `LIVE_CLIPPING=true`.

## Setup

In `backend/.env` (the API reads this file):

```env
LIVE_CLIPPING=true
LIVE_PYTHON=C:/Users/you/AppData/Local/Programs/Python/Python313/python.exe
LIVE_KIT_PARENT=D:/path/to/folder-containing/Claude Edit Asset   # optional SFX / white-flash kit
LIVE_FONT_DIR=D:/path/to/fonts                                   # InterDisplay-Black.ttf for captions
# LIVE_DATA_DIR=                                                 # default TEMP_DIR/live
```

`LIVE_PYTHON` must have these installed:
- `numpy`, `opencv-python`, `pillow`, `requests`, `streamlink`;
- `faster-whisper`, or `transformers` + `torch`.

These tools must be on `PATH`:
- `ffmpeg`;
- `yt-dlp` (keep it updated with `yt-dlp -U`);
- `node` (yt-dlp needs it for YouTube).

The engine runs as separate processes, so the API's own virtualenv needs nothing extra. Restart the API after
changing `.env`.

## Using it (/live)

- **Live streams**: when the page opens, it searches YouTube for live gaming streams (BGMI, GTA, Free Fire,
  Minecraft, Fortnite, Valorant, Warzone and more). 24/7 loops, music, news and casino streams are dropped. The list
  is sorted by viewers. Type another context and press **Search live**. Start clipping with **Clip this** (one
  stream), **Clip selected**, or **Clip all shown**. Paste a channel name into **Clip Twitch** for Twitch.
  A finished stream with chat replay → **Clip all hype moments**.
- Each **active clipper** card shows clips, chat/min, buffer, restarts, the last hype message and its log.
- **Review clips**: watch each clip. Change the on-screen hook or the YouTube title, then **Save & re-render**.
  Then **Approve** or **Reject**. **Publish** works only on approved clips.
- **Settings**: parallel renders, 720p/1080p, hours per stream, Twitch sensitivity, caption model.

Every rendered clip is also added to the **Library**, as a task called `LIVE · <channel> · <date>` (one per stream
per day). There you can use SupoClip's player, downloads and social publishing.

## How it works

`backend/src/live/`:

| File | Role |
|---|---|
| `engine/` | The clipping engine (`livehype.py`, `twitchclip.py`, `publish.py`, `cf.py`). It is the same code as the `live-hype-clipper` repo and runs as subprocesses with `LIVE_PYTHON`. |
| `manager.py` | One process per stream records, reads chat, detects and cuts `raw.mp4`. A shared render queue (1 at a time by default) makes `final.mp4`. The supervisor restarts crashed sessions. |
| `importer.py` | Every 8 s, rendered clips become `sources` / `tasks` / `generated_clips` rows (the file is copied into media storage). A re-render updates the same clip. |
| `routes.py` | The `/live/*` API (search, sessions, clips, settings, publish). |

Frontend: `src/app/live/page.tsx`, and the `src/app/api/live/[...path]` signed proxy.

**Detection**:
- Chat messages are scored: hype words, emotes, CAPS, "clip it", emoji.
- A moment is hype when the 5-second score is at least 3× the stream's rolling median, with at least 40% hype
  messages.
- Bots, `!commands` and links are ignored.
- Each stream has a 75 s cooldown between moments.

**Clip length follows the chat**: 20 s before the first hype message, until 6 s after chat drops back below 1.6× its
baseline. Minimum 30 s, maximum 48 s.

**Error loop**:
- A session that crashes or loses chat restarts automatically, at most 5 times, with a growing pause, while the
  stream is still live.
- An offline stream ends as `ended`.
- A failed render is retried 3 times, then marked `edit_failed`, with the error shown on the card.
- Clips that were half-rendered when the API stopped go back into the queue on the next start.

## Capacity (one PC)

Measured on a Ryzen 7 7730U laptop with 16 GB RAM and no NVIDIA GPU:
- each YouTube session uses about 210 MB RAM and each Twitch session about 140 MB, at around 0.1% CPU;
- each stream needs about 5–6 Mb/s of download at 720p;
- one render (Whisper + encode) needs about 1–1.5 GB RAM. Two renders at once ran out of memory on that laptop, which
  is why the default is 1.

Plan on 6–8 streams at 720p. 10–12 is possible with at least 5 GB of RAM free and about 70 Mb/s of internet; the
render queue may fall behind on busy nights, but clips still get cut and render later.

## Publishing

| Option | Platforms | Needs |
|---|---|---|
| **SupoClip accounts** (`/social/posts`) | YouTube, TikTok, Instagram | `SOCIAL_PUBLISHING_ACCESS=all` and each provider's OAuth app (see `docs/configuration.md`). Instagram needs a public URL for `SOCIAL_PUBLIC_MEDIA_BASE_URL`, for example a tunnel. |
| **Postiz** | YouTube + Instagram in one click | The Postiz CLI (`npm i -g postiz`, `postiz auth:login`) and an active Postiz plan (or a self-hosted Postiz) with both channels connected |
| **YouTube API** | YouTube | An OAuth client in `TEMP_DIR/live/secrets/client_secret.json` (Desktop app). Uploads from unverified projects stay private until Google's audit. |

Nothing is ever published automatically: only approved clips, and only when you press Publish.

## Agent loop (Antigravity / Claude Code)

In local single-user mode, the frontend signs requests for you, so an agent can call the same endpoints the page
uses at `http://localhost:3107/api/live/...`. Repeat every few minutes while sessions run:

1. **Health**: `GET /api/live/sessions`.
   - `state: error`: read `log` (the last lines). Known fixes:
     - `Sign in to confirm` or `HTTP Error 403` → `yt-dlp -U`, then `POST /api/live/sessions/<id>/restart`;
     - `cannot get stream URL` → the stream ended → leave it;
     - a traceback in `backend/src/live/engine/*.py` → fix the code (also in the `live-hype-clipper` repo), then
       restart the session.
   - `ended` / `finished`: nothing to do (`POST /api/live/sessions/clear` tidies the list).
2. **Renders**: `GET /api/live/clips`.
   - `edit_failed`: read `render_error`. Typical causes: out of RAM (set Parallel renders to 1), a missing font or kit
     file, an ffmpeg error. Fix it, then `POST /api/live/clips/<date>/<clip>/update {"rerender": true}`.
3. **Review** each `needs_review` clip (`meta.json` in `TEMP_DIR/live/clips/<date>/<clip>/` has `transcript` and
   `top_chat`):
   - reject if the moment doesn't stand alone, has no payoff, or chat reacts to something off-screen:
     `{"status": "rejected"}`;
   - otherwise write a better `hook` (≤ 6 words, curiosity gap, no emoji) and `yt_title` (≤ 60 chars), which
     re-renders. When it is good: `{"status": "approved"}`.
4. **Never publish** without the user's explicit go-ahead for that clip. Publishing is the user's Publish button.
5. API down (no answer on :8000) → run `start-local.bat`. Half-rendered clips recover by themselves.
   Give up on a session after 3 code fixes that didn't help, and tell the user what is failing.
