"""Twitch live hype clipper + YouTube Shorts poster (local, $0). Antigravity = the brain/reviewer.

  python twitchclip.py live <channel>             record a live Twitch channel, read its chat, auto-clip hype moments
  python twitchclip.py live --find "GTA 6"        pick the most-watched live channel in a category (needs Twitch app creds)
  python twitchclip.py find "GTA 6"               just list live channels in a category
  python twitchclip.py review                     list clips waiting for review (same queue as livehype.py)
  python twitchclip.py reedit <clip dir>          re-render after editing meta.json (hook / trim), keeps Twitch credit
  python twitchclip.py post <clip dir>            upload an APPROVED clip to YouTube Shorts (asks before uploading)

Pipeline: Streamlink pipes the live HLS into ffmpeg, which writes a rolling ~10 min buffer of 6 s .ts segments.
Chat is read anonymously over Twitch IRC (irc.chat.twitch.tv, justinfan nick, no OAuth). Each message goes into
livehype.HypeDetector (rolling median baseline, 5 s window, k=3, >=40 % hype messages, bot/link filter, cooldown),
with Twitch emotes counted as hype words. On a spike: cut 20 s before the first hype message to 10 s after, run
livehype.produce() (layout, captions, hook, chat pop-ups, kit SFX/flash/zoom), then write Twitch credit + VOD link.
Clips land in clips/<date>/<channel>_NN/ (final.mp4 + meta.json, status "needs_review"). Nothing is auto-posted.
"""
import argparse, datetime as dt, json, os, random, re, socket, subprocess, sys, time
from pathlib import Path
import requests
import cf
import livehype as lh
from livehype import log

SECRETS = cf.ROOT / "secrets"                     # git-ignored: Twitch app creds, YouTube OAuth client + token
LATENCY = 4.0                                     # s between the broadcaster and what viewers/chat see (VOD offset)
QUALITY = "1080p60,1080p,best"

# ------------------------------------------------------------------ 1. chat scoring: Twitch emotes are hype words
TWITCH_HYPE_RE = re.compile(
    r"\b(kekw|omegalul|lulw?|kek|icant|pepelaugh|xdd?|pogchamp|poggers|pogu|pog|pogg+ers|letsgo+|lets go+|clap|"
    r"monka[sw]|wutface|gigachad|hypers|ez+|w+|ayo+|sheesh|omg+|5head|catjam|pepejam|kreygasm|lmao+)\b", re.I)
BOTS = {"nightbot", "streamelements", "moobot", "fossabot", "streamlabs", "wizebot", "sery_bot", "soundalerts",
        "botrixoficial", "kofistreambot", "pokemoncommunitygame", "blerp", "own3d", "commanderroot", "streamstickers",
        "frostytoolsdotcom", "songlistbot", "tangiabot", "lumiastream"}


def twitch_weight(text):
    """livehype.msg_weight plus Twitch/7TV/BTTV emotes (they arrive as plain words in IRC text)."""
    w = lh.msg_weight(text)
    n = len(TWITCH_HYPE_RE.findall(text))
    if n and not lh.HYPE_RE.search(text):
        w += 1.5                                   # an emote-only reaction counts as a hype message (>= 2.5)
    if n > 1:
        w += min(n - 1, 4) * 0.3                   # KEKW KEKW KEKW spam
    return w


# ------------------------------------------------------------------ 2. anonymous IRC chat reader
def _parse_irc(line):
    """'@tags :nick!nick@nick.tmi.twitch.tv PRIVMSG #chan :text' -> (ts, user, text) or None."""
    if " PRIVMSG #" not in line:
        return None
    tags = {}
    if line.startswith("@"):
        raw, line = line[1:].split(" ", 1)
        tags = dict(kv.split("=", 1) for kv in raw.split(";") if "=" in kv)
    prefix, _, rest = line.partition(" PRIVMSG ")
    text = rest.split(" :", 1)[1] if " :" in rest else ""
    if text.startswith("\x01ACTION "):
        text = text[8:].rstrip("\x01")
    user = (tags.get("display-name") or prefix.lstrip(":").split("!")[0]).lower()
    ts = int(tags["tmi-sent-ts"]) / 1000 if tags.get("tmi-sent-ts", "").isdigit() else time.time()
    return ts, user, text


def irc_chat(login):
    """Yield (timestamp, text) forever from the channel's chat; reconnects on drops. Bots and !commands dropped."""
    while True:
        try:
            s = socket.create_connection(("irc.chat.twitch.tv", 6667), timeout=360)
            for cmd in ("CAP REQ :twitch.tv/tags twitch.tv/commands", "PASS SCHMOOPIIE",
                        f"NICK justinfan{random.randint(10000, 99999)}", f"JOIN #{login}"):
                s.sendall((cmd + "\r\n").encode())
            log(f"chat: joined #{login} (anonymous IRC)")
            buf = b""
            while True:
                data = s.recv(65536)
                if not data:
                    raise ConnectionError("chat socket closed")
                buf += data
                *lines, buf = buf.split(b"\r\n")
                for raw in lines:
                    line = raw.decode("utf-8", "replace")
                    if line.startswith("PING"):
                        s.sendall(b"PONG :tmi.twitch.tv\r\n"); continue
                    if " RECONNECT" in line:
                        raise ConnectionError("server asked to reconnect")
                    m = _parse_irc(line)
                    if not m:
                        continue
                    ts, user, text = m
                    if user in BOTS or user.endswith("bot") or text.startswith("!"):
                        continue
                    yield ts, text
        except (OSError, ConnectionError) as e:
            log("chat: reconnecting -", str(e)[:100]); time.sleep(3)


# ------------------------------------------------------------------ 3. channel info, category search (Helix), VOD lookup
def stream_info(login):
    """Title / category / display name of a live channel via Streamlink's own metadata (no API key)."""
    try:
        r = subprocess.run(["streamlink", "--json", f"https://www.twitch.tv/{login}"], capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=60)
        j = json.loads(r.stdout or "{}")
    except Exception:
        j = {}
    if j.get("error"):
        return None                                # offline / no such channel
    md = j.get("metadata") or {}
    return dict(channel=md.get("author") or login, title=md.get("title") or "", game=md.get("category") or "")


def _twitch_creds():
    cid, sec = os.environ.get("TWITCH_CLIENT_ID"), os.environ.get("TWITCH_CLIENT_SECRET")
    f = SECRETS / "twitch.json"
    if not (cid and sec) and f.exists():
        j = json.loads(f.read_text("utf-8")); cid, sec = j.get("client_id"), j.get("client_secret")
    return (cid, sec) if cid and sec else None


def helix(path, **params):
    """Official Twitch Helix API with the user's own free app (client-credentials token, cached in secrets/)."""
    creds = _twitch_creds()
    if not creds:
        raise RuntimeError("no Twitch app credentials: create a free app at https://dev.twitch.tv/console/apps and put "
                           '{"client_id": "...", "client_secret": "..."} in secrets/twitch.json (see ANTIGRAVITY.md)')
    cid, sec = creds
    tok_f = SECRETS / "twitch_token.json"
    tok = json.loads(tok_f.read_text("utf-8")) if tok_f.exists() else {}
    if tok.get("client_id") != cid or tok.get("expires", 0) < time.time() + 300:
        r = requests.post("https://id.twitch.tv/oauth2/token", timeout=20,
                          data=dict(client_id=cid, client_secret=sec, grant_type="client_credentials"))
        r.raise_for_status(); j = r.json()
        tok = dict(client_id=cid, access_token=j["access_token"], expires=time.time() + j.get("expires_in", 3600))
        SECRETS.mkdir(exist_ok=True); tok_f.write_text(json.dumps(tok), "utf-8")
    r = requests.get(f"https://api.twitch.tv/helix/{path}", params=params, timeout=20,
                     headers={"Client-Id": cid, "Authorization": f"Bearer {tok['access_token']}"})
    r.raise_for_status()
    return r.json().get("data", [])


def find_live(category, limit=10):
    """Most-watched live channels in the category that best matches `category` (e.g. "GTA 6" -> Grand Theft Auto VI)."""
    cats = helix("search/categories", query=category, first=10)
    if not cats:
        return None, []
    want = category.lower().replace("6", "vi")
    cat = next((c for c in cats if c["name"].lower() in (category.lower(), want)), None)
    cat = cat or next((c for c in cats if "grand theft auto vi" == c["name"].lower() and "gta" in want), cats[0])
    streams = helix("streams", game_id=cat["id"], first=min(limit, 100), type="live")
    return cat["name"], [dict(login=s["user_login"], channel=s["user_name"], viewers=s["viewer_count"], title=s["title"],
                              game=s["game_name"], started_at=s["started_at"]) for s in streams]


def vod_link(login, onset_clock):
    """URL of the live stream's archive VOD at the clip start (needs Helix creds and VODs enabled on the channel)."""
    if not _twitch_creds():
        return None, None                          # description then asks the reviewer to add the VOD link
    try:
        users = helix("users", login=login)
        vids = helix("videos", user_id=users[0]["id"], type="archive", first=1) if users else []
    except Exception as e:
        log("  VOD lookup skipped:", str(e)[:120]); return None, None
    if not vids:
        return None, None
    v = vids[0]
    start = dt.datetime.fromisoformat(v["created_at"].replace("Z", "+00:00")).timestamp()
    off = int(onset_clock - lh.LOOKBACK - LATENCY - start)
    if off < 0:
        return None, None                          # newest archive is not this broadcast
    h, m, s = off // 3600, off % 3600 // 60, off % 60
    return f"https://www.twitch.tv/videos/{v['id']}?t={h}h{m:02d}m{s:02d}s", off


def game_label(game, channel):
    if not game or game.lower() in ("just chatting", "irl", "always on", "special events", "talk shows & podcasts",
                                       "games + demos", "variety", "software and game development"):
        return channel.strip("_")                  # "THIS CASEOH MOMENT..." reads better than "THIS JUST CHATTING..."
    if re.fullmatch(r"grand theft auto vi|gta ?(6|vi)", game, re.I):
        return "GTA 6"
    game = re.sub(r"^the\s+", "", game, flags=re.I)
    return game if len(game) <= 16 else channel.strip("_")   # long names overflow the hook line ("ZELDA: OCARINA...")


# ------------------------------------------------------------------ 4. recorder: streamlink | ffmpeg segmenter
class TwitchRecorder(lh.Recorder):
    """Same rolling-buffer recorder as livehype (6 s .ts, keep ~10 min, prune older), fed by Streamlink."""

    def __init__(self, login, bufdir, keep=600, quality=QUALITY):
        super().__init__(f"https://www.twitch.tv/{login}", bufdir, keep)
        self.quality, self.sl = quality, None

    def _launch(self, restarts, part, lst):
        if self.sl and self.sl.poll() is None:
            self.sl.kill()
        self.sl = subprocess.Popen(["streamlink", "--stdout", "--loglevel", "warning", "--twitch-low-latency",
                                    self.url, self.quality], stdout=subprocess.PIPE,
                                   stderr=open(self.dir / "streamlink.log", "a"))
        ff = subprocess.Popen(["ffmpeg", "-loglevel", "error", "-y", "-i", "pipe:0", "-map", "0:v:0", "-map", "0:a:0?",
                               "-c", "copy", "-f", "segment", "-segment_time", "6", "-segment_list", lst.name,
                               "-segment_list_type", "csv", "-segment_start_number", str(part + 1),
                               f"seg{restarts}_%06d.ts"], stdin=self.sl.stdout, cwd=self.dir)
        self.sl.stdout.close()                     # ffmpeg owns the pipe now; it sees EOF when streamlink exits
        return ff

    def stop(self):
        super().stop()
        if self.sl and self.sl.poll() is None:
            self.sl.terminate()


# ------------------------------------------------------------------ 5. Twitch credit / description after the edit
def finish_meta(job):
    """Overwrite produce()'s YouTube-flavoured description/tags with Twitch credit + the VOD link."""
    job = Path(job); meta = json.loads((job / "meta.json").read_text("utf-8"))
    login, who = meta.get("login") or meta.get("channel", ""), meta.get("channel") or meta.get("login", "")
    game = meta.get("game") or ""
    if not meta.get("vod_url") and meta.get("onset_clock"):
        meta["vod_url"], meta["vod_offset_s"] = vod_link(login, meta["onset_clock"])
    chan_url = f"https://www.twitch.tv/{login}"
    title = meta.get("yt_title") or meta.get("hook", "").title()
    src_line = (f"Full stream (VOD, moment at the timestamp): {meta['vod_url']}" if meta.get("vod_url") else
                f"Full stream: {chan_url}/videos (VOD link not found automatically - add it before posting)")
    tag_name = re.sub(r"\W", "", who)
    game_tags = "#GTA6 #GTAVI " if meta.get("game_label") == "GTA 6" else (f"#{re.sub(r'[^A-Za-z0-9]', '', game)} " if game else "")
    meta.update(
        platform="twitch", source_url=chan_url,
        description=(f"{title}\n\nStreamer: {who} - {chan_url}\n{src_line}\n\n"
                     f"Clipped from {who}'s Twitch live stream{' (' + game + ')' if game else ''} the moment chat exploded. "
                     f"All credit to {who} - go follow them on Twitch. If you are the creator and want this removed, "
                     "contact the uploader.\n\n"
                     f"#{tag_name} {game_tags}#twitch #twitchclips #shorts"),
        tags=[t for t in dict.fromkeys([who, f"{who} clips", f"{who} twitch", game, f"{game} funny moments" if game else "",
                                        meta.get("game_label", ""), "twitch clips", "twitch", "streamer moments", "shorts"]) if t])
    (job / "meta.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False), "utf-8")


# ------------------------------------------------------------------ 6. live mode
def run_live(login, find, max_clips, hours, model, k, quality, edit=True, status_file=None):
    if find:
        cat, lives = find_live(find)
        lives = [s for s in lives if s["viewers"] >= lh.MIN_VIEWERS]
        if not lives:
            log(f"no live channels in '{find}' right now"); lh.write_status(status_file, state="no_stream"); return
        for s in lives[:5]:
            log(f"  {s['viewers']:>7} watching | {s['channel'][:20]:20} | {s['title'][:70]}")
        login = lives[0]["login"]
    login = login.lower().rsplit("/", 1)[-1]
    info = stream_info(login)
    if not info:
        log(f"twitch.tv/{login} is not live (or does not exist)")
        lh.write_status(status_file, state="finished", reason="offline", clips=0); return
    label = game_label(info["game"], info["channel"])
    src = dict(id=login, url=f"https://www.twitch.tv/{login}", title=info["title"], channel=info["channel"],
               meta=dict(platform="twitch", login=login, game=info["game"], game_label=label))
    log(f"WATCHING: {src['url']} | {info['game']} | {info['title'][:80]}")
    det = lh.HypeDetector(k=k, weight=twitch_weight)
    made, why = lh.watch(src, TwitchRecorder(login, lh.LIVEBUF / f"twitch_{login}", quality=quality),
                         lambda: irc_chat(login), det, max_clips, hours, model, after_edit=finish_meta,
                         edit=edit, status_file=status_file)
    log(f"done ({why}): {made} clips. Review with: python twitchclip.py review")


# ------------------------------------------------------------------ 7. YouTube Shorts poster (official Data API)
YT_SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def youtube_client():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    secret, token = SECRETS / "client_secret.json", SECRETS / "youtube_token.json"
    creds = Credentials.from_authorized_user_file(str(token), YT_SCOPES) if token.exists() else None
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    if not creds or not creds.valid:
        if not secret.exists():
            sys.exit(f"missing {secret}: create an OAuth client (Desktop app) in Google Cloud and download its JSON "
                     "there - steps in ANTIGRAVITY.md, 'YouTube poster setup'")
        creds = InstalledAppFlow.from_client_secrets_file(str(secret), YT_SCOPES).run_local_server(port=0)
    SECRETS.mkdir(exist_ok=True); token.write_text(creds.to_json(), "utf-8")
    return build("youtube", "v3", credentials=creds)


def post(clip, privacy, yes, dry_run):
    clip = Path(clip); mf = clip / "meta.json"
    meta = json.loads(mf.read_text("utf-8"))
    if meta.get("youtube_id"):
        sys.exit(f"already posted: {meta.get('youtube_url')}")
    if meta.get("status") != "approved":
        sys.exit(f"refusing to post: status is '{meta.get('status')}', not 'approved'. Review the clip and set "
                 '"status": "approved" in meta.json first.')
    final = clip / "final.mp4"
    if not final.exists():
        sys.exit(f"missing {final}")
    title = (meta.get("yt_title") or meta.get("hook", "")).strip()
    if "#shorts" not in title.lower() and len(title) + 8 <= 100:
        title += " #shorts"
    body = dict(snippet=dict(title=title[:100], description=meta.get("description", "")[:5000],
                             tags=meta.get("tags", [])[:30], categoryId="20"),            # 20 = Gaming
                status=dict(privacyStatus=privacy, selfDeclaredMadeForKids=False, containsSyntheticMedia=False))
    print(json.dumps(body, indent=1, ensure_ascii=False))
    if dry_run:
        print("dry run: nothing uploaded"); return
    if not yes and input(f"\nUpload {final} to YouTube as {privacy}? type 'yes': ").strip().lower() != "yes":
        print("cancelled"); return
    from googleapiclient.http import MediaFileUpload
    req = youtube_client().videos().insert(part="snippet,status", body=body, media_body=MediaFileUpload(
        str(final), mimetype="video/mp4", chunksize=8 * 1024 * 1024, resumable=True))
    resp = None
    while resp is None:
        st, resp = req.next_chunk()
        if st:
            log(f"  uploaded {int(st.progress() * 100)}%")
    vid = resp["id"]
    meta.update(status="posted", youtube_id=vid, youtube_url=f"https://www.youtube.com/shorts/{vid}",
                posted_at=dt.datetime.now().isoformat(timespec="seconds"), posted_privacy=privacy)
    mf.write_text(json.dumps(meta, indent=1, ensure_ascii=False), "utf-8")
    log("POSTED ->", meta["youtube_url"])


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["live", "find", "review", "reedit", "post"])
    ap.add_argument("target", nargs="?", help="channel login (live), category (find) or clip dir (reedit/post)")
    ap.add_argument("--find", help='live: pick the most-watched live channel in this category, e.g. "GTA 6"')
    ap.add_argument("--max-clips", type=int, default=999, help="default: every hype moment")
    ap.add_argument("--no-edit", action="store_true", help="only cut raw clips for the ClipForge render queue")
    ap.add_argument("--status-file", help="JSON status for the ClipForge app")
    ap.add_argument("--hours", type=float, default=6)
    ap.add_argument("--model", default="base", help="Whisper model for captions")
    ap.add_argument("--k", type=float, default=3.0, help="spike ratio vs baseline (raise for huge, spammy chats)")
    ap.add_argument("--quality", default=QUALITY)
    ap.add_argument("--privacy", choices=["private", "unlisted", "public"], default="private")
    ap.add_argument("--yes", action="store_true", help="post: skip the interactive confirmation")
    ap.add_argument("--dry-run", action="store_true", help="post: show the upload request without uploading")
    a = ap.parse_args()
    if a.cmd in ("live", "find") and (a.find or a.cmd == "find") and not _twitch_creds():
        sys.exit("--find / find need free Twitch app creds in secrets/twitch.json (see ANTIGRAVITY.md, 'Twitch app creds'). "
                 "Or watch a channel directly: python twitchclip.py live <channel>")
    if a.cmd == "live":
        if not (a.target or a.find):
            ap.error("live needs a channel or --find <category>")
        run_live(a.target, a.find, a.max_clips, a.hours, a.model, a.k, a.quality, not a.no_edit, a.status_file)
    elif a.cmd == "find":
        cat, lives = find_live(a.target or "GTA 6")
        print(f"category: {cat}")
        for s in lives:
            print(f"{s['viewers']:>7} | {s['channel'][:22]:22} | https://www.twitch.tv/{s['login']} | {s['title'][:70]}")
    elif a.cmd == "review":
        lh.review()
    elif a.cmd == "reedit":
        lh.produce(Path(a.target) / "raw.mp4", a.target, a.model); finish_meta(a.target)
    else:
        post(a.target, a.privacy, a.yes, a.dry_run)
