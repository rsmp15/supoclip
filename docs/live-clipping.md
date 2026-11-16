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

## Captions, descriptions and virality score (per platform, no API)

Every reviewed clip gets platform-specific post copy from `engine/social_copy.py`. It is offline and costs nothing.
It is built from the transcript, the loudest chat, the game and the streamer, and is shown on each clip card
under **Captions & descriptions**:

| Platform | What is generated |
|---|---|
| YouTube Shorts | a title (max 100 chars), a description with the hook, the best quote, streamer credit + stream/VOD link and 3 hashtags, and up to 15 search tags |
| Instagram Reels | a hook line, the quote, credit, a follow call-to-action and 8–12 hashtags at the end (max 2,200 chars) |
| TikTok | a short casual caption, best under about 150 characters, with the quote, `(cr: streamer)`, `#fyp` and niche tags |

- **Copy** puts the text on the clipboard. **Save** keeps your edit: auto-regeneration never overwrites a platform
  you edited. **Regenerate** goes back to the automatic text.
- Publishing uses each platform's own copy: the YouTube title + description, the Instagram caption, the TikTok
  caption.
- When there is no speech, the quote falls back to chat (e.g. `chat: LUL LUL LUL`).

**Virality score (0–100, grade A–D)** is shown as a badge on each card, with a breakdown:
- chat spike, up to 30 points;
- reaction purity, 20;
- hook, 15;
- length sweet spot (25–45 s), 15;
- speech for captions, 10;
- visible face/reaction, 10.

It is a transparent heuristic from the live-chat signal, not a view prediction. The same score goes into the
Library clip's `virality_score`. The **Transcript** panel shows the Whisper transcript of the clip.

## Edit style: effects, captions, title bar, voiceover, reframing

Each clip has style options in `meta.json` under `"style"`. In SupoClip, set them on the clip card under
**Live → Review clips → Edit style**, then press **Apply & re-render**.

| Option | Values | What it does |
|---|---|---|
| `color_fx` | `auto` (shock = red, hype = green, laugh = none), `red`, `green`, `none` | A colour grade + vignette on the hype moment, a colour hit at the spike, and a colour end flash |
| `captions` | `pop` (default), `classic`, `off` | `pop`: 1–2 words at a time, bouncing in; punchy words (NO, WHAT, BRO, WAIT, W…) take the effect colour. `classic`: 3-word karaoke. |
| `label` | `auto`, any text, or empty for off | A reaction label on the hype moment: *LOCKED IN* / *DEAD* / *NO WAY*, picked from the chat mood |
| `intro` | true / false | A zoom-blur punch-in for the first 0.35 s, with a whoosh |
| `title_bar` + `bar_text` | true / false, text | A title pinned on screen for the whole clip. Empty text uses a short title or "streamer • game". |
| `voiceover` + `vo_text` | true / false, text | A context line read by a voice in the first seconds ("Speed was playing GTA 6 when this happened"); stream audio is lowered under it. The voice is Kokoro if installed, else edge-tts if installed, else the offline Windows voice. |

**Reframing (horizontal → vertical)** is in `reframe.py`:
- **Faces:** the YuNet DNN detector (`models/face_detection_yunet_2023mar.onnx`, the same model SupoClip uses).
  Without the model, it falls back to Haar frontal + alt2 + profile cascades.
- **People without a visible face:** HOG body detection.

The layout is picked automatically:
- **facecam**: a face that stays in the same spot. The crop snaps to the webcam rectangle's borders when they are found.
- **face**: a person filling the frame. A smoothed crop follows them.
- **vertical**: portrait sources are fitted with a blurred fill.
- **gameplay**: nobody on screen.

On 23 real stream clips, YuNet found the facecam in 21 of 21 streamer clips, where the old Haar detector found 11.
It also recognised all 3 vertical sources.

## Caption presets, fonts, colours and B-roll

**Caption presets** (`style.caption_preset`; the card shows a live preview of each):

| Preset | Look |
|---|---|
| `pop` | Inter Display Black, white, punchy words in the effect colour, 1–2 words, bounce |
| `beast` | big Anton, white, keyword yellow, heavy outline |
| `hormozi` | Poppins ExtraBold, 3 words, keyword green |
| `comic` | yellow Bangers, keyword red, alternating wobble |
| `neon` | cyan Bebas Neue, magenta glow, 1 word |
| `clean` | TikTok Sans on a dark box, sentence case, 4 words |
| `boxed` | black text on a yellow box |
| `serif` | Instrument Serif italic, sentence case |

**Overrides:**
- `cap_font`: any of the 10 bundled static fonts.
- `cap_color`, `cap_key_color`, `cap_outline`: `#RRGGBB`.
- `cap_size`: `S` / `M` / `L`.

Fonts come from `fonts/`, the edit kit, `CLIPFORGE_EXTRA_FONTS` and SupoClip's `backend/fonts`. Variable fonts are
left out on purpose, because libass renders them in their Thin weight. THE BOLD FONT is free for personal use only.

**B-roll** (`style.broll`, `style.broll_max` 1–5) is in `broll.py`:
- When a spoken word matches the library ("money", "late", "car", "gym", "running", "golden"…), a matching stock
  clip covers the **gameplay panel** for 2 s with a whoosh. The face/reaction stays visible.
- It never appears during the hook, or from 6 s before the chat spike to 4 s after it, so the moment chat reacted to
  stays on screen.
- **Library:** `assets/broll_catalog.json` plus `stock/` (tags come from file names like `pmstock_money_*.mp4`).
  Change it with `CLIPFORGE_BROLL_CATALOG` / `CLIPFORGE_BROLL_DIRS`. The current library is lifestyle footage;
  gaming-specific clips (explosions, sirens, crowd cheers) would fit stream clips better.
- The clip card lists which words triggered which clip.

## Library dialog and the clip studio for live clips

- **Library dialog:** the clip popup shows **Captions & descriptions** (YouTube title + description + tags, Instagram and TikTok captions). For live clips the copy is editable (Save / Regenerate); other clips get generated copy to copy. Live clips fill SupoClip's four 0–25 scores from the live virality parts (hook, reaction → engagement, length + speech → value, chat spike → shareability).
- **Open in editor:** a live clip opens in the clip studio with its own **raw cut** (`raw.mp4` from the live clipper) as the source, so nothing is downloaded from the live stream. Its Whisper words become the editable caption track. The studio starts with a centred vertical crop; adjust it in **Framing**.
