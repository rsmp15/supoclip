"""Use a transcript made elsewhere (Gemini, a human, an SRT file) instead of transcribing.

Drop a file named after the YouTube video id (or the uploaded file's stem) into
``TRANSCRIPT_IMPORT_DIR``:

    <id>.txt   one line per utterance:  [MM:SS] text   or   [H:MM:SS] text
    <id>.srt   a standard SubRip file

The lines are converted to the same structure local Whisper returns, so the rest
of SupoClip (analysis formatting, word-timed captions, the transcript cache)
works unchanged. A .txt file has one timestamp per line, so word timings inside
a line are spread by word length: good enough for captions, roughly +-0.3 s.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_TXT_LINE = re.compile(r"^\s*\[?(?:(\d+):)?(\d{1,2}):(\d{2})(?:[.,](\d+))?\]?\s*[-–:]?\s*(.*)$")
_SRT_TIME = re.compile(
    r"(\d+):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*(\d+):(\d{2}):(\d{2})[,.](\d{1,3})"
)
_MARKER = re.compile(r"\[[^\]]*\]")  # [music], [laughter], [**], [संगीत] ...
_YT_ID = re.compile(r"(?:v=|youtu\.be/|/live/|/shorts/|/embed/)([\w-]{11})")
MAX_LINE_SECONDS = 9.0


def import_dir() -> Optional[Path]:
    raw = (os.getenv("TRANSCRIPT_IMPORT_DIR") or "").strip()
    return Path(raw) if raw else None


def source_key(video_path: Optional[Path], source_url: Optional[str]) -> List[str]:
    """Names an imported transcript may be filed under, most specific first."""
    keys: List[str] = []
    if source_url:
        m = _YT_ID.search(source_url)
        if m:
            keys.append(m.group(1))
    if video_path is not None:
        keys.append(video_path.stem)
        keys.append(video_path.stem.split(".")[0])
    return list(dict.fromkeys(k for k in keys if k))


def find_imported_transcript(video_path: Optional[Path], source_url: Optional[str]) -> Optional[Path]:
    directory = import_dir()
    if directory is None or not directory.is_dir():
        return None
    for key in source_key(video_path, source_url):
        for suffix in (".srt", ".txt"):
            candidate = directory / f"{key}{suffix}"
            if candidate.is_file() and candidate.stat().st_size > 0:
                return candidate
    return None


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", _MARKER.sub(" ", text)).strip()


def parse_timed_text(text: str) -> List[Tuple[float, Optional[float], str]]:
    """'[MM:SS] text' lines -> [(start, None, text)]. Untimed lines join the previous one."""
    out: List[List[Any]] = []
    for raw in text.splitlines():
        m = _TXT_LINE.match(raw)
        if m and (m.group(2) is not None):
            h, mi, s, frac, rest = m.groups()
            start = int(h or 0) * 3600 + int(mi) * 60 + int(s) + (float(f"0.{frac}") if frac else 0.0)
            out.append([start, None, rest.strip()])
        elif raw.strip() and out:
            out[-1][2] += " " + raw.strip()
    return [(a, b, c) for a, b, c in out]


def parse_srt(text: str) -> List[Tuple[float, Optional[float], str]]:
    out: List[Tuple[float, Optional[float], str]] = []
    for block in re.split(r"\r?\n\s*\r?\n", text.strip()):
        lines = [ln for ln in block.splitlines() if ln.strip()]
        for i, ln in enumerate(lines):
            m = _SRT_TIME.search(ln)
            if m:
                g = [int(x) for x in m.groups()]
                start = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000
                end = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000
                out.append((start, end, " ".join(lines[i + 1:])))
                break
    return out


def to_whisper_result(
    lines: List[Tuple[float, Optional[float], str]], total_seconds: Optional[float] = None, language: Optional[str] = None
) -> Dict[str, Any]:
    """Build the dict shape ``transcribe_with_whisper`` returns (seconds, float)."""
    lines = sorted(lines, key=lambda x: x[0])
    segments: List[Dict[str, Any]] = []
    for i, (start, end, raw_text) in enumerate(lines):
        text = _clean(raw_text)
        tokens = text.split()
        if not tokens:
            continue
        nxt = lines[i + 1][0] if i + 1 < len(lines) else (total_seconds or start + 5)
        stop = end if end is not None else min(nxt, start + MAX_LINE_SECONDS)
        stop = max(float(stop), start + 0.6)
        span = max(stop - start - 0.05, 0.4)
        weights = [len(t) + 1 for t in tokens]
        total_w = float(sum(weights))
        acc = 0.0
        words = []
        for tok, w in zip(tokens, weights):
            w_start = start + span * acc / total_w
            acc += w
            w_end = start + span * acc / total_w
            words.append({"word": " " + tok, "start": float(round(w_start, 3)), "end": float(round(w_end, 3)),
                          "probability": 1.0})
        segments.append({"start": float(start), "end": float(round(stop, 3)), "text": " " + text, "words": words})
    return {"segments": segments, "text": " ".join(s["text"].strip() for s in segments), "language": language}


def load_imported_transcript(path: Path, total_seconds: Optional[float] = None) -> Dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = parse_srt(text) if path.suffix.lower() == ".srt" else parse_timed_text(text)
    if not lines:
        raise ValueError(f"No timestamped lines found in imported transcript {path.name}")
    lang = (os.getenv("TRANSCRIPTION_LANGUAGE") or "").split("_", 1)[0] or None
    result = to_whisper_result(lines, total_seconds, lang)
    logger.info("Imported transcript %s: %d lines", path.name, len(result["segments"]))
    return result
