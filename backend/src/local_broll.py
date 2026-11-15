"""B-roll from local folders (your own stock library) before, or instead of, Pexels.

    LOCAL_BROLL_DIRS=D:/03_STARTUP/Make Money/stock;D:/more/footage
    LOCAL_BROLL_CATALOG=D:/03_STARTUP/Make Money/assets/broll_catalog.json   (optional)
    LOCAL_BROLL_ONLY=false     # true: never call Pexels

The catalog is a JSON list of {"path", "tags", "description"} entries. Build a
skeleton with ``tools/build_asset_catalog.py`` and let an agent (Antigravity)
enrich ``tags``/``description`` by looking at each clip: the model's B-roll
cue queries are matched against those words. Without a catalog, tags are
derived from file and folder names (``pmstock_gym_29546708.mp4`` -> gym).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}
STOP = {"the", "a", "an", "of", "and", "or", "with", "in", "on", "at", "to", "for", "shot", "footage",
        "video", "clip", "stock", "pmstock", "luxury", "close", "up", "slow", "motion", "mp4"}


def dirs() -> List[Path]:
    raw = os.getenv("LOCAL_BROLL_DIRS") or ""
    return [Path(p.strip()) for p in re.split(r"[;\n]", raw) if p.strip() and Path(p.strip()).is_dir()]


def configured() -> bool:
    return bool(dirs())


def local_only() -> bool:
    return (os.getenv("LOCAL_BROLL_ONLY") or "").strip().lower() in {"1", "true", "yes", "on"}


def _words(text: str) -> List[str]:
    toks = re.findall(r"[a-z]+", (text or "").lower())
    out = []
    for t in toks:
        if t in STOP or len(t) < 3:
            continue
        out.append(t[:-1] if t.endswith("s") and len(t) > 4 else t)
    return out


def _probe(path: Path) -> Dict[str, float]:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
             "stream=width,height:format=duration", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=20).stdout
        j = json.loads(out)
        st = (j.get("streams") or [{}])[0]
        return {"width": int(st.get("width") or 0), "height": int(st.get("height") or 0),
                "duration": float((j.get("format") or {}).get("duration") or 0)}
    except Exception:
        return {"width": 0, "height": 0, "duration": 0.0}


def _stable_id(path: str) -> int:
    # Negative so it can never collide with a real Pexels id in shared code paths.
    return -int(hashlib.sha1(path.lower().encode()).hexdigest()[:12], 16)


@lru_cache(maxsize=1)
def _catalog_cached(signature: str) -> List[Dict[str, Any]]:
    entries: Dict[str, Dict[str, Any]] = {}
    cat_path = (os.getenv("LOCAL_BROLL_CATALOG") or "").strip()
    if cat_path and Path(cat_path).is_file():
        try:
            for e in json.loads(Path(cat_path).read_text("utf-8")):
                p = Path(e.get("path", ""))
                if p.is_file() and p.suffix.lower() in VIDEO_EXT:
                    entries[str(p)] = {**e, "path": str(p)}
        except (ValueError, OSError) as exc:
            logger.warning("Local B-roll catalog unreadable (%s); using file names", exc)
    for d in dirs():
        for p in d.rglob("*"):
            if p.suffix.lower() in VIDEO_EXT and p.is_file() and str(p) not in entries:
                rel = p.relative_to(d)
                entries[str(p)] = {"path": str(p), "tags": _words(" ".join(rel.parts)), "description": ""}
    out = []
    for e in entries.values():
        if e.get("exclude"):
            continue
        e["_words"] = set(_words(" ".join(e.get("tags") or []) + " " + (e.get("description") or "")))
        out.append(e)
    logger.info("Local B-roll library: %d clips", len(out))
    return out


def catalog() -> List[Dict[str, Any]]:
    sig = "|".join(str(d) for d in dirs()) + "|" + (os.getenv("LOCAL_BROLL_CATALOG") or "")
    cat = os.getenv("LOCAL_BROLL_CATALOG") or ""
    if cat and Path(cat).is_file():
        sig += f"|{Path(cat).stat().st_mtime}"
    return _catalog_cached(sig)


def find(queries: Iterable[str], duration: float, used: set, orientation: str = "portrait") -> Optional[Dict[str, Any]]:
    """Best local clip for the cue, or None. ``used`` holds ids already placed in this task."""
    want = set()
    for q in queries:
        want |= set(_words(q))
    if not want:
        return None
    best, best_score = None, 0.0
    for e in catalog():
        sid = _stable_id(e["path"])
        if sid in used:
            continue
        overlap = len(want & e["_words"])
        if overlap == 0:
            continue
        score = overlap + 0.2 * len(e["_words"] & set(_words(e.get("description") or "")) & want)
        if score > best_score:
            best, best_score = e, score
    if best is None:
        return None
    meta = _probe(Path(best["path"]))
    if meta["duration"] and meta["duration"] < duration:
        return None
    return {
        "provider": "local",
        "pexels_id": _stable_id(best["path"]),   # shared code keys placements on this field
        "local_path": best["path"],
        "file_url": None,
        "width": meta["width"] or 1080,
        "height": meta["height"] or 1920,
        "footage_duration": meta["duration"],
        "credit": "local library",
        "thumbnail": None,
    }
