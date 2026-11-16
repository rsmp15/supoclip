"""Keyword B-roll for clips: when the streamer SAYS something the stock library has (money, car, gym, rain...),
a matching clip flashes in for ~2 s over the gameplay panel. The face/reaction stays visible, and the hype
moment itself is never covered.

Library (all optional, all local):
  CLIPFORGE_BROLL_CATALOG  JSON list [{path, tags, exclude}] (default <WS>/assets/broll_catalog.json)
  CLIPFORGE_BROLL_DIRS     ;-separated folders of .mp4/.mov/.webm; tags come from the file name
                           (default <WS>/stock). e.g. stock/pmstock_money_6266433.mp4 -> "money"
"""
import hashlib
import json
import os
import re
from pathlib import Path

# spoken word -> library tag
SYNONYMS = {
    "money": "money cash rich dollar dollars bucks paid pay broke million millions bank",
    "car": "car cars drive driving drove race racing lambo ferrari speed",
    "run": "run running ran chase chasing escape sprint",
    "party": "party celebrate celebrating dance dancing club",
    "alarm": "alarm wake late siren emergency",
    "rain": "rain raining storm",
    "city": "city downtown street streets",
    "gym": "gym workout lift lifting strong muscle",
    "code": "code coding hacker hack computer",
    "study": "study studying school homework exam",
    "jet": "jet plane flight flying airport",
    "mansion": "mansion house home",
    "gold": "gold golden",
    "summit": "mountain summit climb climbing top",
    "box": "box package delivery",
    "lux": "luxury rich lifestyle",
}
WORD_TAG = {w: tag for tag, words in SYNONYMS.items() for w in words.split()}
VIDEO_EXT = {".mp4", ".mov", ".webm", ".mkv"}


def _ws():
    import cf
    return Path(cf.WS)


def library():
    """{tag: [paths]} from the catalog + folders."""
    lib = {}
    cat = Path(os.environ.get("CLIPFORGE_BROLL_CATALOG") or _ws() / "assets" / "broll_catalog.json")
    if cat.exists():
        try:
            for item in json.loads(cat.read_text("utf-8")):
                p = Path(item.get("path", ""))
                if item.get("exclude") or not p.exists():
                    continue
                for t in item.get("tags") or []:
                    lib.setdefault(str(t).lower(), []).append(str(p))
        except (ValueError, OSError):
            pass
    dirs = os.environ.get("CLIPFORGE_BROLL_DIRS") or str(_ws() / "stock")
    for d in [Path(x) for x in dirs.split(";") if x.strip()]:
        if not d.is_dir():
            continue
        for p in d.rglob("*"):
            if p.suffix.lower() in VIDEO_EXT and p.stat().st_size > 50_000:
                for tok in re.split(r"[^a-z]+", p.stem.lower()):
                    if tok in SYNONYMS:
                        lib.setdefault(tok, []).append(str(p))
    return {t: sorted(set(v)) for t, v in lib.items()}


def plan(words, dur, hype_t, max_cues=3, seconds=2.0, lib=None):
    """Pick up to max_cues cutaways: [{t, dur, path, tag, word}]. Skips the hook (first 3.5 s), the moment chat
    reacted to and the reaction (a cutaway may not overlap hype-6 s .. hype+4 s), the last second, and keeps cues
    4 s apart."""
    lib = library() if lib is None else lib
    if not lib or max_cues <= 0:
        return []
    cues, used = [], set()
    for w in words:
        word = re.sub(r"[^a-z]", "", w["w"].lower())
        tag = WORD_TAG.get(word)
        if not tag or tag not in lib:
            continue
        t = float(w["s"])
        if t < 3.5 or (t + seconds > hype_t - 6 and t < hype_t + 4) or t + seconds > dur - 1:
            continue
        if cues and t - cues[-1]["t"] < 4:
            continue
        options = [p for p in lib[tag] if p not in used] or lib[tag]
        path = options[int(hashlib.md5(f"{word}{t}".encode()).hexdigest(), 16) % len(options)]
        used.add(path)
        cues.append(dict(t=round(t, 2), dur=seconds, path=path, tag=tag, word=w["w"]))
        if len(cues) >= max_cues:
            break
    return cues


def panel(layout):
    """Where cutaways go (x, y, w, h) on the 1080x1920 canvas: over the gameplay, never over the face."""
    return {"facecam": (0, 640, 1080, 1280), "gameplay": (0, 460, 1080, 1000)}.get(layout, (90, 300, 900, 760))
