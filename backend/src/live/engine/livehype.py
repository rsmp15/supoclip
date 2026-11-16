"""GTA 6 live-stream hype clipper (local, $0). Antigravity = the brain/reviewer.

  python livehype.py live                       find the biggest live GTA 6 stream, watch chat, auto-clip hype
  python livehype.py live --url <youtube live>  watch a specific stream
  python livehype.py vod  <youtube VOD url>     same algorithm on a finished stream (chat replay) - for testing
  python livehype.py find                       just list live GTA 6 streams
  python livehype.py review                     list clips waiting for review
  python livehype.py reedit <clip dir>          re-render after editing meta.json (title / trim / captions)

Algorithm: chat messages are scored (hype words, emotes, CAPS, "clip it"); when the 5-second chat score
spikes far above the stream's own rolling baseline, the moment = spike onset, clip = [onset-20s, onset+10s]
(the reaction lags the event; 20 s back catches the setup). Clip gets the production edit and lands in
clips/<date>/<stream>_<n>/ with final.mp4 + meta.json (status "needs_review"). Nothing is auto-published.
"""
import argparse, collections, datetime as dt, json, os, queue, re, shutil, statistics, subprocess, sys, threading, time
from pathlib import Path
import numpy as np
import cf

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
CLIPS = cf.ROOT / "clips"
LIVEBUF = cf.ROOT / "livebuf"
GAME_RE = re.compile(r"\bgta\s*(6|vi)\b|grand theft auto\s*(6|vi)\b", re.I)
SKIP_RE = re.compile(r"countdown|24/7|release (date|time)|pre ?-?load|\buntil\b|\bsoon\b|before gta|waiting for|"
                     r"\bgta ?(4|iv|5|v)\b|grand theft auto (iv|v)\b|trailer|rp\b|roleplay|fivem", re.I)
LIVE_SEARCH ="https://www.youtube.com/results?search_query={q}&sp=EgJAAQ%253D%253D"   # filter: Live
QUERIES = ["gta 6", "gta 6 live", "gta vi gameplay", "gta 6 gameplay live"]
LOOKBACK, LOOKAHEAD = 20.0, 10.0
MIN_VIEWERS = 50          # dead streams have no chat to read
KIT = cf.WS / "Claude Edit Asset" / "Claude Edit Asset"          # creator edit kit (SFX, flashes, fonts)
_SFX = KIT / "01 SFX" / "Original Synth SFX"
KIT_SFX = dict(riser=_SFX / "03 Risers & Impacts" / "Riser 1s.wav",
               impact=_SFX / "03 Risers & Impacts" / "Cinematic Impact.wav",
               whoosh=_SFX / "01 Whooshes & Transitions" / "Whoosh Fast L-R.wav",
               flash=_SFX / "01 Whooshes & Transitions" / "White Flash Hit.wav",
               swell=_SFX / "02 Caption Air Swells" / "Air Swell Soft.wav",
               pop=_SFX / "04 UI Clicks & Pops" / "Blip A.wav")

HYPE_RE = re.compile(r"lmao|lmfao|\blol+\b|haha|hahah|😂|🤣|💀|😭|🔥|🤯|😳|\bomg+\b|\bwtf\b|no ?way|\bnah+\b|insane|"
                     r"crazy|holy|\bw+\b|\bl+\b|lets? ?go+|\bgoat\b|kekw|pog|bro+|what+|\?\?\?|!!!|wow+|sheesh", re.I)
CLIP_RE = re.compile(r"\bclip (it|that|this)\b|\bclipped\b|\bclip+\b", re.I)
EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF☀-➿️]")


def log(*a):
    print(time.strftime("[%H:%M:%S]"), *a, flush=True)


# ------------------------------------------------------------------ 1. find live GTA 6 streams
def find_live(limit=10):
    seen, out = set(), []
    for q in QUERIES:
        try:
            raw = cf.sh([*cf.YTDLP, "--flat-playlist", "-j", "--playlist-end", "25",
                         LIVE_SEARCH.format(q=q.replace(" ", "+"))], check=False)
        except Exception:
            continue
        for line in raw.splitlines():
            try:
                j = json.loads(line)
            except ValueError:
                continue
            t = j.get("title") or ""
            if j.get("id") in seen or j.get("live_status") != "is_live" or not GAME_RE.search(t):
                continue
            if SKIP_RE.search(t):
                continue                                 # countdowns, 24/7 loops, GTA 4/5 streams name-dropping GTA 6
            seen.add(j["id"])
            out.append(dict(id=j["id"], url=f"https://www.youtube.com/watch?v={j['id']}", title=t,
                            viewers=j.get("concurrent_view_count") or 0, channel=j.get("channel") or j.get("uploader") or ""))
    out.sort(key=lambda s: -s["viewers"])
    return out[:limit]


# ------------------------------------------------------------------ 2. chat hype detector
def msg_weight(text):
    w = 1.0
    if HYPE_RE.search(text):
        w += 1.5
    if CLIP_RE.search(text):
        w += 3.0
    letters = [c for c in text if c.isalpha()]
    if len(letters) >= 3 and sum(c.isupper() for c in letters) / len(letters) > 0.7:
        w += 0.7
    w += min(len(EMOJI_RE.findall(text)), 4) * 0.3
    if re.search(r"(.)\1{3,}", text):                   # LMAOOOO / !!!!!
        w += 0.5
    return w


class HypeDetector:
    """Rolling-baseline spike detector. Times are seconds on any consistent clock (wall or stream)."""

    def __init__(self, win=5.0, warmup=60.0, cooldown=75.0, k=3.0, min_score=12.0, min_msgs=6, weight=None):
        self.win, self.warmup, self.cooldown, self.k = win, warmup, cooldown, k
        self.weight = weight or msg_weight                 # twitchclip passes an emote-aware weight
        self.min_score, self.min_msgs = min_score, min_msgs
        self.msgs = collections.deque()                  # (t, weight, text)
        self.hist = collections.deque(maxlen=240)        # window score sampled each second
        self.t0 = None
        self.last_fire = -1e9
        self.last_tick = None

    def add(self, t, text):
        if len(text) > 120 or re.search(r"https?://|www\.|\.(com|store|gg|ly)\b", text, re.I):
            return                                       # promos / bots / link spam
        if self.t0 is None:
            self.t0 = t
        self.msgs.append((t, self.weight(text), text))

    def tick(self, now):
        """Call ~once per second. Returns a hype event dict or None."""
        if self.t0 is None:
            return None
        while self.msgs and self.msgs[0][0] < now - 90:
            self.msgs.popleft()
        win = [m for m in self.msgs if now - self.win < m[0] <= now]
        score = sum(m[1] for m in win)
        base = list(self.hist)
        self.hist.append(score)
        if now - self.t0 < self.warmup or len(base) < 20 or now - self.last_fire < self.cooldown:
            return None
        med = statistics.median(base)
        mad = statistics.median([abs(b - med) for b in base]) or 1.0
        thresh = max(self.min_score, med * self.k, med + 6 * mad)
        hype_share = sum(m[1] >= 2.5 for m in win) / max(len(win), 1)
        if score >= thresh and len(win) >= self.min_msgs and hype_share >= 0.4:
            self.last_fire = now
            onset = next((m[0] for m in win if m[1] >= 2.5), win[0][0])   # first hype message
            top = sorted(win, key=lambda m: -m[1])
            return dict(onset=onset, at=now, score=round(score, 1), baseline=round(med, 1),
                        ratio=round(score / max(med, 1), 1), chat=[m[2] for m in win],
                        top_chat=[m[2] for m in top[:6]])
        return None


# ------------------------------------------------------------------ 3. rolling recorder (live)
class Recorder(threading.Thread):
    """ffmpeg pulls the live HLS into 6 s .ts segments; keeps the last `keep` seconds; maps wall->stream time."""

    def __init__(self, url, bufdir, keep=600):
        super().__init__(daemon=True)
        self.url, self.dir, self.keep = url, Path(bufdir), keep
        self.segs = []                                   # (name, s_start, s_end, wall_end)
        self.proc, self.stop_flag, self.lock = None, False, threading.Lock()
        if self.dir.exists():
            shutil.rmtree(self.dir, ignore_errors=True)
        self.dir.mkdir(parents=True)

    def manifest(self):
        """Live streams now serve separate video/audio HLS -> returns [video_url, audio_url] (or [muxed_url])."""
        h = int(os.environ.get("CLIPFORGE_MAX_HEIGHT", "1080"))          # the app sets 720 for many parallel streams
        return cf.sh([*cf.YTDLP, "-g", "-f", f"bv*[height<={h}]+ba/b[height<={h}]/b", self.url]).strip().splitlines()[:2]

    def run(self):
        restarts = 0
        while not self.stop_flag and restarts < 20:
            part = len(self.segs)
            lst = self.dir / f"list_{restarts}.csv"
            try:
                self.proc = self._launch(restarts, part, lst)
            except Exception as e:
                log("recorder: cannot get stream URL:", str(e)[:150]); restarts += 1; time.sleep(10); continue
            offset = self.segs[-1][2] if self.segs else 0.0
            first = None
            seen = 0
            while self.proc.poll() is None and not self.stop_flag:
                time.sleep(1)
                if not lst.exists():
                    continue
                rows = [r.split(",") for r in lst.read_text().splitlines() if r.count(",") >= 2]
                for name, a, b in rows[seen:]:
                    a, b = float(a), float(b)
                    first = a if first is None else first
                    with self.lock:
                        self.segs.append((name, offset + a - first, offset + b - first, time.time()))
                seen = len(rows)
                self.prune()
            if not self.stop_flag:
                log("recorder: ffmpeg exited, restarting (manifest URLs expire)")
                restarts += 1
                time.sleep(2)

    def _launch(self, restarts, part, lst):
        """Start the process that writes 6 s .ts segments + a csv segment list into self.dir."""
        m3u8 = self.manifest()
        return subprocess.Popen(
            ["ffmpeg", "-loglevel", "error", "-y", "-i", m3u8[0]] + (["-i", m3u8[1]] if len(m3u8) > 1 else []) +
            ["-map", "0:v:0", "-map", f"{len(m3u8) - 1}:a:0?", "-c", "copy",
             "-f", "segment", "-segment_time", "6", "-segment_list", lst.name, "-segment_list_type", "csv",
             "-segment_start_number", str(part + 1), f"seg{restarts}_%06d.ts"], cwd=self.dir)

    def prune(self):
        with self.lock:
            if not self.segs:
                return
            latest = self.segs[-1][2]
            keep = [s for s in self.segs if s[2] >= latest - self.keep]
            for s in self.segs[: len(self.segs) - len(keep)]:
                (self.dir / s[0]).unlink(missing_ok=True)
            self.segs = keep

    def wall_to_stream(self, wall):
        with self.lock:
            if not self.segs:
                return None
            _, _, s_end, w_end = self.segs[-1]
        return s_end - (w_end - wall)

    def available_until(self):
        with self.lock:
            return self.segs[-1][2] if self.segs else 0.0

    def extract(self, a, b, out):
        with self.lock:
            use = [s for s in self.segs if s[2] > a and s[1] < b]
        if not use:
            return False
        lst = self.dir / f"cat_{int(time.time()*1000)}.txt"
        lst.write_text("".join(f"file '{s[0]}'\n" for s in use))
        cf.sh(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst.name, "-ss", max(0, a - use[0][1]),
               "-t", b - a, "-c:v", "libx264", "-preset", "veryfast", "-crf", "17", "-c:a", "aac", "-b:a", "192k",
               Path(out).resolve()], cwd=self.dir)
        lst.unlink(missing_ok=True)
        return True

    def stop(self):
        self.stop_flag = True
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()


def _parse_chat_line(line):
    """yt-dlp live_chat JSON line -> list of (timestamp_sec, video_offset_sec|None, text)."""
    try:
        j = json.loads(line)
    except ValueError:
        return []
    rep = j.get("replayChatItemAction", {})
    off = rep.get("videoOffsetTimeMsec")
    off = int(off) / 1000 if off is not None else None
    out = []
    for act in rep.get("actions", []) or [j]:
        item = act.get("addChatItemAction", {}).get("item", {})
        r = item.get("liveChatTextMessageRenderer") or item.get("liveChatPaidMessageRenderer")
        if not r or "message" not in r:
            continue
        txt = ""
        for run in r["message"].get("runs", []):
            if "text" in run:
                txt += run["text"]
            elif "emoji" in run:
                e = run["emoji"]
                txt += e.get("emojiId") if not e.get("isCustomEmoji") else (e.get("shortcuts") or [""])[0]
        out.append((int(r.get("timestampUsec", 0)) / 1e6, off, txt))
    return out


def chat_stream(url, workdir):
    """Yield (timestamp, video_offset, text) while yt-dlp streams the live chat (or a replay) into a file."""
    workdir = Path(workdir); workdir.mkdir(parents=True, exist_ok=True)
    for f in workdir.glob("chat*"):
        f.unlink(missing_ok=True)
    proc = subprocess.Popen([*cf.YTDLP, "--skip-download", "--write-subs", "--sub-langs", "live_chat",
                             "-o", str(workdir / "chat"), url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    pos, buf, path = 0, "", None
    while True:
        cand = [p for p in workdir.glob("chat.live_chat.json*") if "Frag" not in p.name]
        if cand:
            path = max(cand, key=lambda p: p.stat().st_size)
        if path and path.exists():
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                f.seek(pos); chunk = f.read(); pos = f.tell()
            buf += chunk
            *lines, buf = buf.split("\n")
            for line in lines:
                yield from _parse_chat_line(line)
        if proc.poll() is not None and not chunk_pending(path, pos):
            break
        time.sleep(0.5)


def chunk_pending(path, pos):
    try:
        return path is not None and path.exists() and path.stat().st_size > pos
    except OSError:
        return False


# ------------------------------------------------------------------ 4. production edit
LAUGH_RE = re.compile(r"lmao|lol|haha|😂|🤣|💀|😭|kekw|omegalul|\blulw?\b|icant|pepelaugh|\bkek\b|\bxdd?\b", re.I)
SHOCK_RE = re.compile(r"no ?way|wtf|what|😳|🤯|\?\?|monka[sw]|wutface|\bayo+\b|\bd:", re.I)


def hype_kind(top_chat):
    blob = " ".join(top_chat).lower()
    return "laugh" if LAUGH_RE.search(blob) else "shock" if SHOCK_RE.search(blob) else "hype"


def wrap_hook(title, max_line=26):
    """Captions use WrapStyle 2 (no auto-wrap), so split long hooks into two balanced lines."""
    words = title.split()
    if len(title) <= max_line or len(words) < 2:
        return title
    i = min(range(1, len(words)), key=lambda k: max(len(" ".join(words[:k])), len(" ".join(words[k:]))))
    return " ".join(words[:i]) + r"\N" + " ".join(words[i:])


def strip_emoji(s):
    return re.sub(r"\s+", " ", EMOJI_RE.sub("", s)).strip()


def auto_title(top_chat, transcript_text=""):
    blob = " ".join(top_chat).lower()
    if re.search(r"lmao|lol|haha|😂|🤣|💀|😭|kekw", blob):
        base = "CHAT COULDN'T STOP LAUGHING"
    elif re.search(r"no ?way|wtf|what|😳|🤯|\?\?", blob):
        base = "NO WAY THIS HAPPENED IN GTA 6"
    elif re.search(r"insane|crazy|holy|🔥|goat|w\b", blob):
        base = "THIS GTA 6 MOMENT IS INSANE"
    else:
        base = "CHAT WENT CRAZY FOR THIS"
    return base


HOOKS = {"laugh": ["Watch his face when this happens", "You will not stop laughing at this GTA 6 moment",
                    "Chat could not handle what he did next"],
         "shock": ["You will not believe what he does next in GTA 6", "Watch what happens in two seconds",
                   "Nobody saw this GTA 6 moment coming"],
         "hype": ["Watch what chat does the second this happens", "You will not believe this GTA 6 moment",
                  "This GTA 6 moment made chat go crazy"]}


def best_hook(kind):
    """yt-script skill: score candidate openings with hookscore.py and keep the best (rule-based lint, not a forecast)."""
    cands = HOOKS.get(kind, HOOKS["hype"])
    if YT_SKILLS.exists():
        tmp = Path(os.environ.get("TEMP", ".")) / "hooks.txt"
        tmp.write_text(chr(10).join(cands), "utf-8")
        res = _tool_json("yt-script/hookscore.py", str(tmp))
        try:
            top = max(res, key=lambda r: r.get("verdict", 0))
            return (top.get("hook") or top.get("line") or cands[0]).upper()
        except Exception:
            pass
    return cands[0].upper()


def detect_layout(video):
    """(layout, face) - see reframe.py. detect_info() returns the full result (webcam box, follow track)."""
    info = detect_info(video)
    return info["layout"], info.get("face")


def detect_info(video):
    try:
        import reframe
        return reframe.detect(video)
    except Exception as e:                           # never lose a clip to the detector: old Haar path
        log("  reframe failed, using basic face detection:", str(e)[:120])
        layout, face = _detect_layout_basic(video)
        return dict(layout=layout, face=face, cam_box=None, track=None, detector="haar-basic")


def _detect_layout_basic(video):
    """'facecam' (small webcam in gameplay), 'face' (full-frame person/IRL) or 'gameplay'."""
    import cv2
    cap = cv2.VideoCapture(str(video))
    det = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1
    W, H = cap.get(3), cap.get(4)
    faces = []
    N = 16
    for i in range(N):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(n * (i + 0.5) / N))
        ok, fr = cap.read()
        if not ok:
            continue
        g = cv2.equalizeHist(cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY))
        for x, y, w, h in det.detectMultiScale(g, 1.08, 5, minSize=(int(W * 0.015), int(W * 0.015))):
            faces.append((i, (x + w / 2) / W, (y + h / 2) / H, w / W))
    cap.release()
    if not faces:
        return "gameplay", None
    # a webcam = a face that keeps showing up at the SAME spot; game characters wander around
    best, best_hits = None, 0
    for f in faces:
        near = [g for g in faces if abs(g[1] - f[1]) < 0.04 and abs(g[2] - f[2]) < 0.05]
        hits = len({g[0] for g in near})
        if hits > best_hits:
            best, best_hits = near, hits
    if best_hits < N * 0.4:
        return "gameplay", None
    cx = float(np.median([f[1] for f in best])); cy = float(np.median([f[2] for f in best]))
    fw = float(np.median([f[3] for f in best]))
    return ("facecam" if fw < 0.16 else "face"), (cx, cy, fw)


def layout_filter(layout, face, dur, hype_t, info=None, job=None):
    """Filter graph from [0:v] (normalised 1920x1080) to [lay] 1080x1920 + caption/chat placement."""
    info = info or {}
    pre = "[0:v]scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30"
    if layout == "vertical":                          # already portrait: fit + blurred fill
        g = ("[0:v]setsar=1,fps=30,split[s1][s2];[s1]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
             "boxblur=28,eq=brightness=-0.2[bg];[s2]scale=1080:1920:force_original_aspect_ratio=decrease[fg];"
             "[bg][fg]overlay=(W-w)/2:(H-h)/2[lay]")
        return g, dict(cap_v=420, hook_v=230, chat_y=1050, bar_v=140)
    if layout == "facecam" and info.get("cam_box"):   # the webcam's own borders were found: crop exactly that
        bx, by, bw, bh = info["cam_box"]
        gw = 1080 * 1080 / 1280
        g = (f"{pre},split[s1][s2];[s1]crop={bw * 1920:.0f}:{bh * 1080:.0f}:{bx * 1920:.0f}:{by * 1080:.0f},"
             f"scale=1080:640:force_original_aspect_ratio=increase,crop=1080:640[cam];"
             f"[s2]crop={gw:.0f}:1080:(1920-{gw:.0f})/2:0,scale=1080:1280[game];[cam][game]vstack[lay0];"
             f"[lay0]drawbox=x=0:y=636:w=1080:h=8:color=black:t=fill[lay]")
        return g, dict(cap_v=1920 - 640 - 230, hook_v=70, chat_y=1380, focus_y=320, bar_v=660)
    if layout == "facecam":
        cx, cy, fw = face
        cw = min(1920, max(360, fw * 1920 * 3.4)); ch = cw * 640 / 1080
        x = min(max(cx * 1920 - cw / 2, 0), 1920 - cw); y = min(max(cy * 1080 - ch / 2, 0), 1080 - ch)
        gw = 1080 * 1080 / 1280
        g = (f"{pre},split[s1][s2];[s1]crop={cw:.0f}:{ch:.0f}:{x:.0f}:{y:.0f},scale=1080:640[cam];"
             f"[s2]crop={gw:.0f}:1080:(1920-{gw:.0f})/2:0,scale=1080:1280[game];[cam][game]vstack[lay0];"
             f"[lay0]drawbox=x=0:y=636:w=1080:h=8:color=black:t=fill[lay]")
        return g, dict(cap_v=1920 - 640 - 230, hook_v=70, chat_y=1380, focus_y=320, bar_v=660)
    if layout == "face":
        cw = 1080 * 1080 / 1920
        if info.get("track") and job is not None:     # follow the person: crop x moves along the smoothed track
            import reframe
            x0 = reframe.sendcmd_crop(info["track"], cw, 1920, Path(job) / "follow.cmd")
            return (f"{pre},sendcmd=f=follow.cmd,crop@follow=w={cw:.0f}:h=1080:x={x0}:y=0,scale=1080:1920[lay]",
                    dict(cap_v=520, hook_v=230, chat_y=1050, bar_v=140))
        cx = face[0]
        x = min(max(cx * 1920 - cw / 2, 0), 1920 - cw)
        return f"{pre},crop={cw:.0f}:1080:{x:.0f}:0,scale=1080:1920[lay]", dict(cap_v=520, hook_v=230, chat_y=1050, bar_v=140)
    # gameplay: blurred fill + zoomed 16:9 band
    g = (f"{pre},split[s1][s2];[s1]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=28,"
         f"eq=brightness=-0.22[bg];[s2]scale=1780:-2,crop=1080:1000[fg];[bg][fg]overlay=0:460[lay]")
    return g, dict(cap_v=360, hook_v=250, chat_y=1100, bar_v=250)


def chat_events(msgs, t_start, y0):
    """Real chat messages popping in (left side) around the hype - the 'chat went crazy' proof."""
    ev, shown = [], [m for m in msgs if 3 <= len(strip_emoji(m)) <= 38][:5]
    for i, m in enumerate(shown):
        s = t_start + i * 0.55
        ev.append(f"Dialogue: 2,{cf._ts(s)},{cf._ts(t_start + 5.5)},Chat,Chat,0,0,0,,"
                  f"{{\\pos(60,{y0 + i * 86})\\fad(120,200)\\fscx80\\fscy80\\t(0,120,\\fscx100\\fscy100)}}{strip_emoji(m)}")
    return ev


YT_SKILLS = Path.home() / ".yt-agent" / "youtube-agent-skill" / "skills"

# ---------------------------------------------------------------- edit style (per clip: meta["style"])
STYLE_DEFAULTS = dict(intro=True,            # zoom-blur intro + whoosh (first 0.35 s)
                      captions="pop",        # pop = 1-2 words with colour keywords, classic = 3-word karaoke, off
                      label="auto",          # reaction label on the hype moment: auto / custom text / "" (off)
                      title_bar=True,        # title pinned on screen the whole clip
                      bar_text="",           # "" = from the hook
                      color_fx="auto",       # auto (shock=red, hype=green, laugh=none) / red / green / none
                      voiceover=False,       # context line read by a TTS voice in the first seconds
                      vo_text="")            # "" = "<streamer> was playing <game> when this happened"
FX_COLORS = {"red": ("&H003030FF&", "red"), "green": ("&H0040FF40&", "0x22DD44"), "none": ("&H0000E5FF&", "white")}
LABELS = {"laugh": ["*DEAD*", "*CRYING*", "*CAN'T BREATHE*"], "shock": ["*NO WAY*", "*JAW DROP*", "*SHOCKED*"],
          "hype": ["*LOCKED IN*", "*GOATED*", "*W*"]}
KEY_RE = re.compile(r"^(no+|nah+|what+|wait|bro+|oh+|yo+|w|win|dead|crazy|insane|play|run|go+|stop|help|why|how|omg|"
                    r"wow|yes+|goat|clutch|kill(ed)?|died|die|bruh|lets|let's|ayo+|huh|never|sick|holy)[.!?,]*$", re.I)


def clip_style(meta):
    st = dict(STYLE_DEFAULTS)
    st.update({k: v for k, v in (meta.get("style") or {}).items() if k in STYLE_DEFAULTS})
    if st["color_fx"] == "auto":
        st["color_fx"] = {"shock": "red", "hype": "green"}.get(meta.get("hype_kind"), "none")
    return st


def ass_pop(words, t1, accent):
    """1-2 word pop captions; punchy keywords take the accent colour."""
    chunks, cur = [], []
    for w in words:
        long_word = len(w["w"]) >= 8
        if cur and (len(cur) >= 2 or w["s"] - cur[-1]["e"] > 0.35 or long_word or len(cur[0]["w"]) >= 8):
            chunks.append(cur); cur = []
        cur.append(w)
    if cur:
        chunks.append(cur)
    ev = []
    for i, ch in enumerate(chunks):
        s0 = ch[0]["s"]
        e0 = min(chunks[i + 1][0]["s"] if i + 1 < len(chunks) else 1e9, ch[-1]["e"] + 0.3, t1)
        if e0 - s0 < 0.08:
            continue
        txt = " ".join((f"{{\\c{accent}}}{strip_emoji(x['w']).upper()}{{\\c&HFFFFFF&}}" if KEY_RE.match(x["w"].strip())
                        else strip_emoji(x["w"]).upper()) for x in ch)
        ev.append(f"Dialogue: 0,{cf._ts(s0)},{cf._ts(e0)},Pop,Pop,0,0,0,,"
                  f"{{\\fscx70\\fscy70\\t(0,90,\\fscx114\\fscy114)\\t(90,170,\\fscx100\\fscy100)}}{txt}")
    return ev


def short_title(text, n=34):
    t = strip_emoji(text).strip()
    if len(t) <= n:
        return t
    return t[:n].rsplit(" ", 1)[0] + "…"


def _tool_json(script, *args, stdin_lines=None):
    try:
        r = subprocess.run([sys.executable, str(YT_SKILLS / script), "--json", *args], capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=60)
        return json.loads(r.stdout)
    except Exception:
        return None


def package(meta):
    """yt-package + yt-script skills: lint candidate titles (length / repetition / concreteness) and score the
    on-screen hook. Returns (best_title, hook_score). Falls back to plain titles if the pack isn't installed."""
    hook = meta.get("hook", "")
    who = (meta.get("channel") or "").strip()
    kind = meta.get("hype_kind", "")
    base = {"laugh": ["Streamer Can't Stop Laughing at This GTA 6 Moment", "Chat Lost It Over This GTA 6 Moment"],
            "shock": ["Nobody Saw This Coming in GTA 6", "Chat Said NO WAY After This GTA 6 Moment"],
            "hype": ["This GTA 6 Moment Broke Chat", "GTA 6 Gameplay Moment Chat Went Crazy For"]}.get(
        kind, ["Chat Went Crazy Over This GTA 6 Moment", "GTA 6 Live: The Moment Chat Exploded"])
    label = meta.get("game_label") or "GTA 6"
    reacts = [f"{who} Reacts to This GTA 6 Moment"] if who and label.strip("_ ").lower() != who.strip("_ ").lower() else []
    cands = base + reacts + [" ".join(w.capitalize() for w in hook.lower().split())]
    cands = [c.replace("GTA 6", label) for c in dict.fromkeys(cands) if c][:6]
    thumb = " ".join(hook.split()[:3])
    best, scored = cands[0], None
    if YT_SKILLS.exists():
        tmp = Path(os.environ.get("TEMP", ".")) / "titles.txt"
        tmp.write_text(chr(10).join(cands), "utf-8")
        scored = _tool_json("yt-package/title.py", str(tmp), "--thumb", thumb)
        if scored:
            scored.sort(key=lambda r: (-r["score"], len(r["issues"]), r["chars"]))
            best = scored[0]["title"]
    hs = _tool_json("yt-script/hookscore.py", "--hook", hook) if YT_SKILLS.exists() else None
    hook_score = None
    if isinstance(hs, list) and hs:
        hook_score = hs[0].get("score") or hs[0].get("verdict")
    elif isinstance(hs, dict):
        hook_score = hs.get("score") or hs.get("verdict")
    return best, hook_score, scored


def produce(raw, job, model="base"):
    """Full edit: layout, word captions, hook title, chat pop-ups, punch zoom + flash + whoosh at the hype, progress bar."""
    job = Path(job).resolve(); meta = json.loads((job / "meta.json").read_text("utf-8"))
    pr = cf.probe(raw); dur = pr["dur"]
    hype_t = min(meta.get("hype_offset", LOOKBACK), dur - 1)
    info = detect_info(raw)
    layout, face = info["layout"], info.get("face")
    meta["layout"], meta["detector"] = layout, info.get("detector")
    meta["cam_box_found"] = bool(info.get("cam_box"))
    g, pos = layout_filter(layout, face, dur, hype_t, info, job)
    words = []
    try:
        words = cf.transcribe(raw, model)["words"]
    except Exception as e:
        log("  captions skipped:", str(e)[:120])
    meta["transcript"] = " ".join(w["w"] for w in words)
    cf._hf = None; cf._model = None                   # free Whisper (~1 GB) before the encode: avoids out-of-memory
    import gc; gc.collect()
    meta["hype_kind"] = meta.get("hype_kind") or hype_kind(meta.get("top_chat", []))
    if not meta.get("hook"):
        meta["hook"] = best_hook(meta["hype_kind"]).replace("GTA 6", (meta.get("game_label") or "GTA 6").upper())
    title = strip_emoji(meta["hook"]).upper()
    st = clip_style(meta)
    meta["style_used"] = st
    accent, fx_rgb = FX_COLORS.get(st["color_fx"], FX_COLORS["none"])
    F = cf.FONT_NAME
    ass = cf.ass_header("Cap", 82, pos["cap_v"]).replace(
        "\n\n[Events]",
        f"\nStyle: Hook,{F},74,&H00000000,&H000000FF,&H00FFFFFF,&H00FFFFFF,-1,0,0,0,100,100,0,0,3,16,0,8,136,160,"
        f"{pos['hook_v']},1\nStyle: Chat,{F},48,&H00FFFFFF,&H000000FF,&HC8000000,&HC8000000,-1,0,0,0,100,100,0,0,3,10,0,7,0,0,0,1"
        f"\nStyle: Pop,{F},104,&H00FFFFFF,&H000000FF,&H00000000,&H96000000,-1,0,0,0,100,100,0,0,1,9,3,2,90,90,{pos['cap_v']},1"
        f"\nStyle: Label,{F},92,{accent},&H000000FF,&H00000000,&H96000000,-1,0,0,0,100,100,2,0,1,10,4,5,0,0,0,1"
        f"\nStyle: Bar,{F},54,&H00FFFFFF,&H000000FF,&HB4000000,&HB4000000,-1,0,0,0,100,100,0,0,3,14,0,8,90,90,{pos.get('bar_v', 140)},1"
        "\n\n[Events]", 1)
    ev = (ass_pop(words, dur, accent) if st["captions"] == "pop" else
          cf.ass_words(words, 0, dur) if st["captions"] == "classic" else [])
    ev.append(f"Dialogue: 3,{cf._ts(0)},{cf._ts(3.2)},Hook,Hook,0,0,0,,{{\\fad(0,250)\\fscx90\\fscy90\\t(0,150,\\fscx100\\fscy100)}}{wrap_hook(title)}")
    if st["title_bar"]:                               # pinned title: from the start where it can't clash with the hook
        bar = st["bar_text"] or next((t for t in (meta.get("yt_title"), meta["hook"].title()) if t and len(t) <= 36), None)
        if not bar:
            who_b = (meta.get("channel") or "LIVE").strip("_ ")
            game_b = meta.get("game_label") or ""
            generic_b = {"", "just chatting", "games + demos", "irl", "variety", "live", "gaming"}
            bar = (f"{who_b} • {game_b}" if game_b.lower() not in generic_b and game_b.lower() != who_b.lower()
                   else f"{who_b} LIVE")
        bar = short_title(bar, 38)
        bar_t0 = 0.0 if pos.get("bar_v", 140) > pos["hook_v"] + 260 else 3.2
        ev.append(f"Dialogue: 2,{cf._ts(bar_t0)},{cf._ts(dur)},Bar,Bar,0,0,0,,{{\\fad(200,0)}}{bar}")
    label = st["label"]
    if label == "auto":
        opts = LABELS.get(meta.get("hype_kind"), LABELS["hype"])
        label = opts[int(float(meta.get("ratio") or 0) * 10) % len(opts)]
    if label:
        ly = 1150 if layout == "facecam" else 760     # facecam: in the gameplay panel, between captions and chat
        ev.append(f"Dialogue: 4,{cf._ts(hype_t)},{cf._ts(min(dur, hype_t + 1.8))},Label,Label,0,0,0,,"
                  f"{{\\pos(540,{ly})\\frz-4\\fscx40\\fscy40\\t(0,120,\\fscx118\\fscy118)\\t(120,220,\\fscx100\\fscy100)\\fad(0,200)}}"
                  f"{strip_emoji(label).upper()}")
    chat_t0 = max(0, hype_t - 0.3)
    chat_ev = chat_events(meta.get("chat", []), chat_t0, pos["chat_y"])
    ev += chat_ev
    (job / "edit.ass").write_text(ass + "\n".join(ev) + "\n", "utf-8")
    h0 = max(hype_t - 0.15, 0)

    # --- zoom: the kit's smootherstep punch (in 0.9 s, hold 1 s, out 2 s, 8 %), centred on the face panel
    S = lambda x: f"(({x})*({x})*({x})*(({x})*(({x})*6-15)+10))"
    T0, T1, T2, T3, A = h0 - 0.45, h0 + 0.45, h0 + 1.45, h0 + 3.45, 0.08
    z = (f"if(lt(t,{T0:.2f}),0,if(lt(t,{T1:.2f}),{A}*{S(f'(t-{T0:.2f})/0.9')},if(lt(t,{T2:.2f}),{A},"
         f"if(lt(t,{T3:.2f}),{A}*(1-{S(f'(t-{T2:.2f})/2')}),0))))")
    if st["intro"]:
        z = f"({z})+if(lt(t,0.35),0.22*(1-t/0.35)*(1-t/0.35),0)"
    cy = pos.get("focus_y", 960)
    zoom = (f"scale=w='2*trunc(540*(1+{z}))':h=-2:eval=frame,"
            f"crop=1080:1920:'(iw-1080)/2':'(ih-1920)*{cy / 1920:.3f}'")

    # --- kit assets (fall back to synthesized SFX / drawbox flash if the kit is missing)
    sfx, inputs = [], []
    def add(path, at, vol):
        if path and Path(path).exists() and at >= 0:
            inputs.append(Path(path)); sfx.append((len(inputs), int(at * 1000), vol))
    if KIT.exists():
        add(KIT_SFX["riser"], h0 - 1.0, 0.35)
        add(KIT_SFX["whoosh"], h0 - 0.25, 0.45)
        add(KIT_SFX["flash"], h0, 0.6)
        add(KIT_SFX["impact"], h0, 0.45)
        add(KIT_SFX["swell"], 0.0, 0.25)
        for i in range(len(chat_ev)):
            add(KIT_SFX["pop"], chat_t0 + i * 0.55, 0.22)
        if st["intro"]:
            add(KIT_SFX["whoosh"], 0.0, 0.4)
    else:
        whoosh, hit = cf.make_sfx(job / "sfx")
        add(whoosh, h0 - 0.25, 0.5); add(hit, h0, 0.6)
    vo_dur = 0.0
    if st["voiceover"]:
        import voice
        who_vo = (meta.get("channel") or "the streamer").strip("_ ")
        game_vo = meta.get("game_label") or ""
        generic = {"", "just chatting", "games + demos", "irl", "variety", "live", "gaming"}
        real_game = game_vo and game_vo.lower() not in generic and game_vo.lower() != who_vo.lower()
        text = st["vo_text"] or (f"{who_vo} was playing {game_vo} when this happened." if real_game
                                 else f"Watch what happened on {who_vo}'s stream.")
        wav = voice.synth(text, job / "vo.wav")
        if wav:
            import wave
            with wave.open(str(wav), "rb") as wf:
                vo_dur = wf.getnframes() / float(wf.getframerate())
            add(wav, 0.15, 1.15)
            meta["vo_text_used"] = text
        else:
            log("  voiceover skipped: no TTS voice available")
    flash = KIT / "05 Transitions (white overlays)" / "White Flash - Quick (0.5s).mp4"
    grad = job / "grad.png"
    if not grad.exists():
        from PIL import Image
        gim = Image.new("RGBA", (1080, 1920), (0, 0, 0, 0)); px = gim.load()
        top = 1920 - pos["cap_v"] - 330                      # soft black gradient under the caption zone
        for y in range(max(top, 0), 1920):
            a = int(150 * min(1, (y - top) / 300))
            for x in range(1080):
                px[x, y] = (0, 0, 0, a)
        gim.save(grad)
    n_in = 1 + len(inputs)
    try:
        fonts = os.path.relpath(cf.FONT_DIR, job).replace("\\", "/")
    except ValueError:                                # clip folder on another drive: bring the fonts along
        (job / "fonts").mkdir(exist_ok=True)
        for f in Path(cf.FONT_DIR).glob("*.[ot]tf"):
            shutil.copy2(f, job / "fonts" / f.name)
        fonts = "fonts"
    vf = f"{g};[lay]{zoom}[zm];[zm][{n_in}:v]overlay=0:0[gr];"
    if flash.exists():
        vf += (f"[{n_in + 1}:v]fps=30,scale=1080:1920,format=gbrp,tpad=start_duration={h0:.2f}:stop_duration={dur:.2f}:color=black,"
               f"trim=0:{dur:.2f},setpts=PTS-STARTPTS[fl];[gr]format=gbrp[gb];[gb][fl]blend=all_mode=screen:shortest=1,format=yuv420p[fx];")
    else:
        vf += (f"[gr]drawbox=x=0:y=0:w=iw:h=ih:color=white@0.8:t=fill:enable='between(t,{h0:.2f},{h0 + 0.1:.2f})'[fx];")
    look = ""
    w0, w1 = h0, h0 + 1.4
    if st["color_fx"] == "red":                       # danger grade + red hit on the hype moment
        look = (f"colorchannelmixer=rr=1.3:rg=0.1:gg=0.68:bb=0.68:enable='between(t,{w0:.2f},{w1:.2f})',"
                f"vignette=PI/4:enable='between(t,{w0:.2f},{w1:.2f})',"
                f"drawbox=x=0:y=0:w=iw:h=ih:color=red@0.35:t=fill:enable='between(t,{h0:.2f},{h0 + 0.12:.2f})',")
    elif st["color_fx"] == "green":                   # win grade + green hit
        look = (f"colorchannelmixer=rr=0.75:gg=1.3:gb=0.08:bb=0.75:enable='between(t,{w0:.2f},{w1:.2f})',"
                f"vignette=PI/4:enable='between(t,{w0:.2f},{w1:.2f})',"
                f"drawbox=x=0:y=0:w=iw:h=ih:color=0x22DD44@0.30:t=fill:enable='between(t,{h0:.2f},{h0 + 0.12:.2f})',")
    if st["intro"]:
        look += "gblur=sigma=16:steps=2:enable='lt(t,0.28)',"
    look += f"drawbox=x=0:y=0:w=iw:h=ih:color={fx_rgb}@0.85:t=fill:enable='gt(t,{max(dur - 0.22, 0):.2f})',"
    vf += (f"[fx]{look}drawbox=x=0:y=ih-12:w='iw*t/{dur:.2f}':h=12:color=0x00E5FF@0.95:t=fill,"
           f"ass=edit.ass:fontsdir={fonts},fade=t=in:st=0:d=0.25[v]")
    duck = f",volume=0.3:enable='between(t,0.1,{vo_dur + 0.25:.2f})'" if vo_dur else ""
    a0 = (f"[0:a]loudnorm=I=-14:TP=-1.5:LRA=11,aresample=44100,aformat=channel_layouts=stereo{duck}[a0]" if pr["audio"]
          else f"anullsrc=r=44100:cl=stereo,atrim=0:{dur:.2f}[a0]")
    af = a0 + "".join(f";[{i}:a]aresample=44100,aformat=channel_layouts=stereo,adelay={d}|{d},volume={v}[s{i}]"
                      for i, d, v in sfx)
    af += ";[a0]" + "".join(f"[s{i}]" for i, _, _ in sfx) + f"amix=inputs={1 + len(sfx)}:duration=first:normalize=0[a]"
    out = job / "final.mp4"
    cmd = ["ffmpeg", "-v", "error", "-y", "-i", Path(raw).resolve()]
    for p in inputs:
        cmd += ["-i", p]
    cmd += ["-loop", "1", "-t", dur, "-i", grad] + (["-i", flash] if flash.exists() else [])
    cf.sh(cmd + ["-filter_complex", f"{vf};{af}", "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "medium",
                 "-crf", "18", "-pix_fmt", "yuv420p", "-r", "30", "-c:a", "aac", "-b:a", "192k", "-ar", "44100",
                 "-movflags", "+faststart", "-t", dur, "final.mp4"], cwd=job)
    ts = int(meta.get("stream_t", 0))
    best_title, hook_score, scored = package(meta)
    src = meta.get("source_url", "")
    link = f"{src}&t={ts}s" if ts and "watch?v=" in src else src
    who = meta.get("channel") or "the streamer"
    label = meta.get("game_label") or "GTA 6"
    if label == "GTA 6":
        hashtags = "#GTA6 #GTAVI #GrandTheftAuto6 #GTA6Gameplay #shorts"
        tags = ["GTA 6", "GTA VI", "Grand Theft Auto 6", "GTA 6 gameplay", "GTA 6 live", "GTA 6 funny moments", "shorts"]
    else:
        tag = re.sub(r"[^A-Za-z0-9]", "", label)
        hashtags = f"#{tag} #gaming #livestream #shorts"
        tags = [t for t in dict.fromkeys([label, f"{label} live", f"{label} funny moments", f"{label} gameplay",
                                          meta.get("channel", ""), "gaming", "live stream moments", "shorts"]) if t]
    meta.update(status="needs_review", final=str(out), hook_score=hook_score,
                yt_title=(meta.get("yt_title") if meta.get("title_locked") else best_title)[:100],
                title_lint=(scored or [])[:3],
                description=(f"{best_title}. {label} moment from {who}'s live stream, the second chat exploded."
                             f"\nFull stream: {link}\n\n"
                             f"Clipped automatically from the live chat's biggest hype spike. All credit to {who}; "
                             "if you are the creator and want this removed, contact the uploader.\n\n" + hashtags),
                tags=tags,
                shorts_checklist=dict(hook_on_screen_first_3s=True, captions_burned_in=bool(words), vertical_1080x1920=True,
                                      length_s=round(dur, 1), keyword_title=True,
                                      ai_disclosure_needed=False, note="real stream footage, no AI-generated visuals"))
    (job / "meta.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False), "utf-8")
    log("  READY FOR REVIEW ->", out)
    return out


def new_job(src, ev, a, b, stream_t):
    day = CLIPS / dt.date.today().isoformat(); day.mkdir(parents=True, exist_ok=True)
    n = len(list(day.glob(f"{src['id']}_*"))) + 1
    job = day / f"{src['id']}_{n:02d}"; job.mkdir()
    meta = dict(source_url=src["url"], source_title=src.get("title", ""), channel=src.get("channel", ""),
                stream_t=round(a, 1), clip_start=round(a, 1), clip_end=round(b, 1), hype_offset=round(stream_t - a, 1),
                hype_score=ev["score"], baseline=ev["baseline"], ratio=ev["ratio"], chat=ev["chat"][:40],
                top_chat=ev["top_chat"], onset_clock=round(ev["onset"], 1),
                created=dt.datetime.now().isoformat(timespec="seconds"), status="rendering")
    meta.update(src.get("meta") or {})                  # e.g. twitchclip: platform, login, game, game_label
    (job / "meta.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False), "utf-8")
    return job


# ------------------------------------------------------------------ 5. gaming live search (YouTube)
GAMING_QUERIES = ["gaming live", "live gameplay", "gta 5 live", "gta 6 live", "minecraft live", "fortnite live",
                  "valorant live", "bgmi live", "free fire live", "warzone live", "roblox live", "cs2 live",
                  "apex legends live", "call of duty live", "pubg live"]
NOT_GAMING = re.compile(r"24/7|24x7|lofi|lo-fi|\bradio\b|music|\bnews\b|podcast|asmr|countdown|\bsleep|relax|study|"
                        r"\bmass\b|church|cctv|webcam|camera|traffic|weather|\bbhajan|aarti|kirtan|poker|casino|slots?\b|gambl|"
                        r"\bbet(ting)?\b|roulette|blackjack", re.I)
GAMES = [(r"\bgta\s*(6|vi)\b|grand theft auto\s*(6|vi)", "GTA 6"), (r"\bgta\s*(5|v)\b|\bgta ?rp\b|grand theft auto\s*(5|v)\b|\bgta\b", "GTA 5"),
         (r"minecraft", "Minecraft"), (r"fortnite", "Fortnite"), (r"valorant", "Valorant"), (r"\bbgmi\b", "BGMI"),
         (r"free ?fire", "Free Fire"), (r"warzone", "Warzone"), (r"call of duty|\bcod\b", "Call of Duty"),
         (r"roblox", "Roblox"), (r"\bcs ?2\b|counter[- ]strike", "CS2"), (r"apex", "Apex Legends"), (r"\bpubg\b", "PUBG"),
         (r"league of legends|\blol\b", "League of Legends"), (r"\bdota\b", "Dota 2"), (r"rocket league", "Rocket League"),
         (r"\bfc ?2[5-7]\b|\bfifa\b", "EA FC"), (r"overwatch", "Overwatch"), (r"elden ring", "Elden Ring"),
         (r"marvel rivals", "Marvel Rivals"), (r"rainbow six|\br6\b", "Rainbow Six"), (r"clash", "Clash"),
         (r"among us", "Among Us"), (r"\bark\b", "ARK"), (r"rust\b", "Rust"), (r"tarkov", "Tarkov")]


def guess_game(title, channel=""):
    """Short game name for hooks/titles from a stream title; falls back to the channel name."""
    for pat, name in GAMES:
        if re.search(pat, title or "", re.I):
            return name
    return (channel or "").strip("_ ")[:16] or "LIVE"


def find_gaming_live(query=None, limit=60):
    """Live YouTube streams for a gaming context ("Gaming" = a spread of popular game searches), most-watched first."""
    from concurrent.futures import ThreadPoolExecutor
    queries = GAMING_QUERIES if not query or query.strip().lower() in ("gaming", "games") else [query, f"{query} live"]

    def one(q):
        try:
            return cf.sh([*cf.YTDLP, "--flat-playlist", "-j", "--playlist-end", "30",
                          LIVE_SEARCH.format(q=q.replace(" ", "+"))], check=False)
        except Exception:
            return ""
    seen, out = set(), []
    with ThreadPoolExecutor(8) as ex:
        for raw in ex.map(one, queries):
            for line in raw.splitlines():
                try:
                    j = json.loads(line)
                except ValueError:
                    continue
                t = j.get("title") or ""
                if j.get("id") in seen or j.get("live_status") != "is_live" or NOT_GAMING.search(t):
                    continue
                seen.add(j["id"])
                ch = j.get("channel") or j.get("uploader") or ""
                out.append(dict(platform="youtube", id=j["id"], url=f"https://www.youtube.com/watch?v={j['id']}", title=t,
                                channel=ch, viewers=j.get("concurrent_view_count") or 0, game=guess_game(t, ch),
                                thumb=f"https://i.ytimg.com/vi/{j['id']}/mqdefault.jpg"))
    out.sort(key=lambda s: -s["viewers"])
    return out[:limit]


# ------------------------------------------------------------------ 6. modes
MIN_AFTER, MAX_AFTER, CALM_TAIL, HOT_K = 10.0, 28.0, 6.0, 1.6   # clip = 20 s before + 10..28 s after (30-48 s total)


def clip_end(onset, hot):
    """Seconds after the hype onset to end the clip: until chat calms down (+ a tail), clamped to MIN/MAX_AFTER."""
    return min(max(hot + CALM_TAIL - onset, MIN_AFTER), MAX_AFTER)


def write_status(path, **kw):
    if path:
        try:
            Path(path).write_text(json.dumps(dict(kw, updated=time.time()), ensure_ascii=False), "utf-8")
        except OSError:
            pass


def run_live(url, max_clips, hours, model, edit=True, status_file=None, title="", channel=""):
    if url:
        vid = re.search(r"(?:v=|youtu\.be/|live/)([\w-]{11})", url).group(1)
        src = dict(id=vid, url=f"https://www.youtube.com/watch?v={vid}", title=title, channel=channel)
        if channel or title:                             # launched from the app's gaming search: label by game
            src["meta"] = dict(platform="youtube", game_label=guess_game(title, channel))
    else:
        lives = [s for s in find_live() if s["viewers"] >= MIN_VIEWERS]
        if not lives:
            log("no live GTA 6 streams found right now - retry later (or pass --url)")
            write_status(status_file, state="no_stream"); return
        for s in lives[:5]:
            log(f"  {s['viewers']:>7} watching | {s['channel'][:20]:20} | {s['title'][:70]}")
        src = lives[0]
    log("WATCHING:", src["url"], src.get("title", ""))
    chat = lambda: ((ts or time.time(), text) for ts, _off, text in chat_stream(src["url"], LIVEBUF / f"{src['id']}_chat"))
    made, why = watch(src, Recorder(src["url"], LIVEBUF / src["id"]), chat, HypeDetector(), max_clips, hours, model,
                      edit=edit, status_file=status_file)
    log(f"done ({why}): {made} clips. Review with: python livehype.py review")


def watch(src, rec, chat, det, max_clips, hours, model, after_edit=None, edit=True, status_file=None):
    """Shared live loop: rolling recorder + chat (callable -> iterable of (timestamp, text)) -> detector -> cut.
    edit=True renders each clip here (produce + after_edit); edit=False leaves raw.mp4 with status "needs_edit"
    for a shared render queue (the ClipForge app). Clip length follows the chat: it ends CALM_TAIL s after chat
    drops back below HOT_K x baseline (30-48 s total). Returns (clips_made, reason)."""
    rec.start()
    msgs = queue.Queue()

    def chat_worker():
        try:
            for item in chat():
                msgs.put(item)
        except Exception as e:
            log("chat ended/failed:", str(e)[:150])
        msgs.put(None)

    threading.Thread(target=chat_worker, daemon=True).start()
    jobs = queue.Queue()

    def edit_worker():
        while True:
            item = jobs.get()
            if item is None:
                return
            raw, job = item
            try:
                produce(raw, job, model)
                if after_edit:
                    after_edit(job)
            except Exception as e:
                log("  edit failed:", str(e)[:300])

    ew = threading.Thread(target=edit_worker, daemon=True); ew.start()
    pending, made, t_end, alive, rate_n, chat_clock = [], 0, time.time() + hours * 3600, True, 0, 0
    last_status, last_write, rate, last_hype, why = time.time(), 0, 0, None, "time_up"
    try:
        while True:
            if time.time() >= t_end:
                why = "time_up"; break
            if made >= max_clips and not pending:
                why = "max_clips"; break
            if not alive and not pending:
                why = "chat_ended"; break
            if not rec.is_alive() and not pending:
                why = "recorder_stopped"; log("recorder gave up (stream offline?)"); break
            while True:
                try:
                    it = msgs.get_nowait()
                except queue.Empty:
                    break
                if it is None:
                    alive = False; break
                det.add(*it); rate_n += 1
            now = time.time()
            ev = None
            if det.msgs:                                 # tick on the CHAT clock: chat can arrive in late batches
                chat_now = int(det.msgs[-1][0]) - 2
                chat_clock = max(chat_clock or chat_now - 1, chat_now - 30)
                while chat_clock < chat_now and not ev:
                    chat_clock += 1
                    ev = det.tick(float(chat_clock))
            if ev:
                st = rec.wall_to_stream(ev["onset"])
                if st is None:
                    log("hype but recorder has no video yet")
                elif made + len(pending) < max_clips:
                    log(f"HYPE x{ev['ratio']} (score {ev['score']} vs {ev['baseline']}): {' | '.join(ev['top_chat'][:3])[:120]}")
                    pending.append(dict(ev=ev, st=st, hot=ev["onset"], after=None))
                    last_hype = " | ".join(ev["top_chat"][:3])[:80]
            cur = det.hist[-1] if det.hist else 0
            for p in list(pending):
                ev, st = p["ev"], p["st"]
                if p["after"] is None:
                    if cur >= max(det.min_score, ev["baseline"] * HOT_K):
                        p["hot"] = max(p["hot"], chat_clock)       # chat still hyped -> keep the clip running
                    since = chat_clock - ev["onset"]
                    if since >= MIN_AFTER and (chat_clock >= p["hot"] + CALM_TAIL or since >= MAX_AFTER):
                        p["after"] = clip_end(ev["onset"], p["hot"])
                    continue
                a, b = max(0, st - LOOKBACK), st + p["after"]
                if rec.available_until() >= b + 1:
                    pending.remove(p)
                    job = new_job(src, ev, a, b, st)
                    if rec.extract(a, b, job / "raw.mp4"):
                        made += 1
                        if edit:
                            jobs.put((job / "raw.mp4", job))
                        else:
                            m = json.loads((job / "meta.json").read_text("utf-8")); m["status"] = "needs_edit"
                            (job / "meta.json").write_text(json.dumps(m, indent=1, ensure_ascii=False), "utf-8")
                        log(f"clip {made} cut {a:.0f}-{b:.0f}s ({b - a:.0f} s) -> {'editing' if edit else 'render queue'}")
            if now - last_status > 60:
                log(f"chat {rate_n}/min, buffer {rec.available_until():.0f}s, clips {made}/{max_clips}")
                rate, rate_n, last_status = rate_n, 0, now
            if now - last_write > 5:
                write_status(status_file, state="recording" if rec.available_until() else "starting",
                             buffer=round(rec.available_until()), chat_per_min=rate or rate_n, clips=made,
                             pending=len(pending), last_hype=last_hype, alive=alive)
                last_write = now
            time.sleep(1)
    except KeyboardInterrupt:
        why = "stopped"; log("stopping...")
    rec.stop(); jobs.put(None); ew.join()
    write_status(status_file, state="finished", reason=why, clips=made, buffer=0, chat_per_min=0)
    return made, why


def run_vod(url, max_clips, model, edit=True, status_file=None):
    """Replay a finished stream's chat through the same detector, then cut every hype section (chat-driven length)."""
    vid = re.search(r"(?:v=|youtu\.be/|live/)([\w-]{11})", url).group(1)
    info = json.loads(cf.sh([*cf.YTDLP, "-j", "--skip-download", url]).strip().splitlines()[-1])
    src = dict(id=vid, url=f"https://www.youtube.com/watch?v={vid}", title=info.get("title", ""),
               channel=info.get("channel", ""))
    src["meta"] = dict(platform="youtube", game_label=guess_game(src["title"], src["channel"]))
    if not (info.get("was_live") or "live_chat" in (info.get("subtitles") or {})):
        log("this video has no live-chat replay (it was never a live stream), so there is no chat to detect hype in. "
            "Use SupoClip's Create page (or clip.py) for normal videos.")
        write_status(status_file, state="finished", reason="no_chat_replay", clips=0); return
    log("replaying chat:", src["title"])
    write_status(status_file, state="reading_chat", clips=0)
    det, events, last, scores = HypeDetector(), [], None, {}
    for _ts, t, text in chat_stream(src["url"], cf.WORK / f"{vid}_chat"):
        if t is None or t < 0:
            continue
        det.add(t, text)
        sec = int(t)
        if sec != last:
            for s in range(last + 1 if last is not None else sec, sec + 1):
                ev = det.tick(float(s))
                scores[s] = det.hist[-1] if det.hist else 0
                if ev:
                    events.append(ev); log(f"HYPE at {ev['onset']:.0f}s x{ev['ratio']}: {' | '.join(ev['top_chat'][:3])[:100]}")
            last = sec
    events.sort(key=lambda e: -e["ratio"])
    done = 0
    for ev in events[:max_clips]:
        st, hot = ev["onset"], ev["onset"]
        for s in range(int(st), int(st + MAX_AFTER) + 1):
            if scores.get(s, 0) >= max(det.min_score, ev["baseline"] * HOT_K):
                hot = s
            elif s > hot + CALM_TAIL:
                break
        a, b = max(0, st - LOOKBACK), st + clip_end(st, hot)
        job = new_job(src, ev, a, b, st)
        cf.sh([*cf.YTDLP, "-f", "bv*[height<=1080]+ba/b[height<=1080]", "--merge-output-format", "mp4",
               "--download-sections", f"*{a:.1f}-{b:.1f}", "--force-keyframes-at-cuts", "-o", str(job / "raw.%(ext)s"), src["url"]])
        if edit:
            produce(job / "raw.mp4", job, model)
        else:
            m = json.loads((job / "meta.json").read_text("utf-8")); m["status"] = "needs_edit"
            (job / "meta.json").write_text(json.dumps(m, indent=1, ensure_ascii=False), "utf-8")
        done += 1
        write_status(status_file, state="cutting", clips=done, total=min(len(events), max_clips))
    write_status(status_file, state="finished", reason="vod_done", clips=done)
    log(f"done: {done} clips from {len(events)} hype moments")


def review():
    rows = sorted(CLIPS.glob("*/*/meta.json"))
    for p in rows:
        m = json.loads(p.read_text("utf-8"))
        if m.get("status") in ("needs_review", "rendering", "needs_edit"):
            print(f"{m['status']:13} x{m.get('ratio')}  {p.parent}\n   hook: {m.get('hook')}\n   chat: {' | '.join(m.get('top_chat', [])[:3])[:110]}")
    print(f"{len(rows)} clips total")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["live", "vod", "find", "gaming", "review", "reedit"])
    ap.add_argument("target", nargs="?")
    ap.add_argument("--url")
    ap.add_argument("--max-clips", type=int, default=999, help="default: every hype moment")
    ap.add_argument("--hours", type=float, default=6)
    ap.add_argument("--model", default="base")
    ap.add_argument("--no-edit", action="store_true", help="only cut raw clips (status needs_edit) for the app's render queue")
    ap.add_argument("--status-file", help="JSON status for the ClipForge app")
    ap.add_argument("--title", default="")
    ap.add_argument("--channel", default="")
    ap.add_argument("--json", action="store_true", help="gaming: print JSON")
    a = ap.parse_args()
    if a.cmd == "find":
        for s in find_live():
            print(f"{s['viewers']:>7} | {s['channel'][:22]:22} | {s['url']} | {s['title'][:70]}")
    elif a.cmd == "gaming" and a.json:
        print(json.dumps(find_gaming_live(a.target), ensure_ascii=False))
    elif a.cmd == "gaming":
        for s in find_gaming_live(a.target):
            print(f"{s['viewers']:>7} | {s['game'][:12]:12} | {s['channel'][:22]:22} | {s['url']} | {s['title'][:60]}")
    elif a.cmd == "live":
        run_live(a.url or a.target, a.max_clips, a.hours, a.model, not a.no_edit, a.status_file, a.title, a.channel)
    elif a.cmd == "vod":
        run_vod(a.target, a.max_clips, a.model, not a.no_edit, a.status_file)
    elif a.cmd == "review":
        review()
    else:
        produce(Path(a.target) / "raw.mp4", a.target, a.model)
