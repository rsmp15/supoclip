"""Live-chat hype signals for clip selection (YouTube streams with chat replay).

After a YouTube live stream is published, its chat replay is still available and
every message carries its offset into the video. Chat explodes a few seconds
after something worth clipping happens, so the spikes are a strong, cheap hint
for the selection model: fetched in seconds, no video download needed.

``hype_block(url)`` returns a short text block (top spikes with timestamps and
sample messages) that is appended to the selection prompt's creator
instructions, or "" when the video has no chat replay. Enabled with
``CHAT_SIGNALS=true``. Results are cached per video id under TEMP_DIR.
"""

from __future__ import annotations

import collections
import json
import logging
import os
import re
import statistics
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_YT_ID = re.compile(r"(?:v=|youtu\.be/|/live/|/shorts/|/embed/)([\w-]{11})")
HYPE_RE = re.compile(
    r"lmao|lmfao|\blol+\b|haha|😂|🤣|💀|😭|🔥|🤯|😳|\bomg+\b|\bwtf\b|no ?way|\bnah+\b|insane|crazy|holy|"
    r"\bw+\b|lets? ?go+|\bgoat\b|kekw|pog|bro+|what+|\?\?\?|!!!|wow+|sheesh|"
    r"hahaha|हाहा|😆|👏|❤️|bhai|op\b|mast|kya",
    re.I,
)
CLIP_RE = re.compile(r"\bclip (it|that|this)\b|\bclipped\b|\bclip+\b", re.I)
EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF☀-➿️]")
PROMO_RE = re.compile(r"https?://|www\.|\.(com|store|gg|ly)\b", re.I)

WINDOW = 5.0          # seconds of chat scored together
MIN_GAP = 45.0        # spikes closer than this are the same moment
LOOKBACK = 20.0       # the event usually happens ~5-20 s before chat reacts


def enabled() -> bool:
    return (os.getenv("CHAT_SIGNALS") or "").strip().lower() in {"1", "true", "yes", "on"}


def video_id(url: str) -> Optional[str]:
    m = _YT_ID.search(url or "")
    return m.group(1) if m else None


def _cache_dir() -> Path:
    base = Path(os.getenv("TEMP_DIR") or tempfile.gettempdir()) / "chat-cache"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _text(renderer: Dict) -> str:
    out = ""
    for run in (renderer.get("message") or {}).get("runs", []):
        if "text" in run:
            out += run["text"]
        elif "emoji" in run:
            e = run["emoji"]
            out += e.get("emojiId", "") if not e.get("isCustomEmoji") else (e.get("shortcuts") or [""])[0]
    return out


def parse_replay(path: Path) -> List[Tuple[float, str]]:
    """yt-dlp live_chat JSON lines -> [(seconds into the video, text)]."""
    msgs: List[Tuple[float, str]] = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                j = json.loads(line)
            except ValueError:
                continue
            rep = j.get("replayChatItemAction") or {}
            off = rep.get("videoOffsetTimeMsec")
            if off is None:
                continue
            for act in rep.get("actions", []):
                item = (act.get("addChatItemAction") or {}).get("item") or {}
                r = item.get("liveChatTextMessageRenderer") or item.get("liveChatPaidMessageRenderer")
                if r:
                    msgs.append((int(off) / 1000.0, _text(r)))
    msgs.sort(key=lambda m: m[0])
    return msgs


def fetch_replay(url: str, timeout: int = 900) -> Optional[Path]:
    vid = video_id(url)
    if not vid:
        return None
    cache = _cache_dir() / f"{vid}.live_chat.json"
    if cache.is_file() and cache.stat().st_size > 0:
        return cache
    cmd = ["yt-dlp", "--skip-download", "--write-subs", "--sub-langs", "live_chat", "--no-progress",
           "-o", str(_cache_dir() / vid), f"https://www.youtube.com/watch?v={vid}"]
    runtime = (os.getenv("YTDLP_JS_RUNTIME") or "").strip()
    if runtime:
        cmd[1:1] = ["--js-runtimes", runtime]
    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("Chat replay download failed for %s: %s", vid, type(exc).__name__)
        return None
    return cache if cache.is_file() and cache.stat().st_size > 0 else None


def weight(text: str) -> float:
    if len(text) > 120 or PROMO_RE.search(text):
        return 0.0
    w = 1.0
    if HYPE_RE.search(text):
        w += 1.5
    if CLIP_RE.search(text):
        w += 3.0
    letters = [c for c in text if c.isalpha()]
    if len(letters) >= 3 and sum(c.isupper() for c in letters) / len(letters) > 0.7:
        w += 0.7
    w += min(len(EMOJI_RE.findall(text)), 4) * 0.3
    if re.search(r"(.)\1{3,}", text):
        w += 0.5
    return w


def find_spikes(msgs: List[Tuple[float, str]], top: int = 12, k: float = 2.5) -> List[Dict]:
    """Seconds where the 5 s chat score jumps above the stream's own rolling baseline."""
    if len(msgs) < 30:
        return []
    end = int(msgs[-1][0]) + 1
    score = [0.0] * (end + 1)
    texts: Dict[int, List[Tuple[float, str]]] = collections.defaultdict(list)
    for t, txt in msgs:
        w = weight(txt)
        if w <= 0:
            continue
        sec = int(t)
        score[sec] += w
        texts[sec].append((w, txt))
    win = int(WINDOW)
    rolling = [sum(score[max(0, s - win + 1): s + 1]) for s in range(end + 1)]
    hist: collections.deque = collections.deque(maxlen=240)
    cands = []
    for s in range(end + 1):
        if len(hist) >= 30:
            med = statistics.median(hist) or 1.0
            ratio = rolling[s] / max(med, 1.0)
            if ratio >= k and rolling[s] >= 8:
                cands.append((ratio, s))
        hist.append(rolling[s])
    cands.sort(reverse=True)
    picked: List[Dict] = []
    for ratio, s in cands:
        if any(abs(s - p["second"]) < MIN_GAP for p in picked):
            continue
        sample = sorted((m for sec in range(s - win + 1, s + 1) for m in texts.get(sec, [])), key=lambda m: -m[0])
        first = next((sec for sec in range(s - win + 1, s + 1) if any(w >= 2.5 for w, _ in texts.get(sec, []))), s - win + 1)
        seen_txt = list(dict.fromkeys(EMOJI_RE.sub("", t).strip()[:40] for _, t in sample))
        picked.append({"second": first, "ratio": round(ratio, 1), "chat": [t for t in seen_txt if t][:4]})
        if len(picked) >= top:
            break
    picked.sort(key=lambda p: p["second"])
    return picked


def _mmss(sec: float) -> str:
    sec = max(0, int(sec))
    return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}" if sec >= 3600 else f"{sec // 60:02d}:{sec % 60:02d}"


def spikes_for(url: str) -> List[Dict]:
    vid = video_id(url)
    if not vid:
        return []
    cache = _cache_dir() / f"{vid}.spikes.json"
    if cache.is_file():
        try:
            return json.loads(cache.read_text("utf-8"))
        except ValueError:
            pass
    path = fetch_replay(url)
    if path is None:
        logger.info("No chat replay for %s", vid)
        cache.write_text("[]", "utf-8")
        return []
    spikes = find_spikes(parse_replay(path))
    cache.write_text(json.dumps(spikes, ensure_ascii=False), "utf-8")
    logger.info("Chat replay for %s: %d hype spikes", vid, len(spikes))
    return spikes


def hype_block(url: str) -> str:
    """Text for the selection prompt, or '' when there is nothing to say."""
    if not enabled():
        return ""
    try:
        spikes = spikes_for(url)
    except Exception as exc:  # signals are a hint; never fail the task over them
        logger.warning("Chat signals unavailable: %s", exc)
        return ""
    if not spikes:
        return ""
    lines = [
        "Live chat replay hype (chat reacts AFTER the moment, so the event usually starts 5-20 s "
        "before each time). Strongly prefer clips that contain one of these moments, start a little "
        "before it, and end on the payoff. Ignore a spike if the transcript shows nothing clip-worthy there:"
    ]
    for s in spikes:
        start = max(0, s["second"] - LOOKBACK)
        lines.append(f"- around {_mmss(s['second'])} (event ~{_mmss(start)}), chat x{s['ratio']}: "
                     + " | ".join(s["chat"][:3]))
    return "\n".join(lines)
