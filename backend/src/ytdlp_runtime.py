"""Give every yt-dlp client a JavaScript runtime.

YouTube now needs a JS runtime to solve its player challenge. The Docker image
ships deno; a native Windows/Mac install usually only has Node. Without a runtime
yt-dlp cannot list formats and every download fails. SupoClip builds yt-dlp
options in several places, so instead of touching each one this sets a default
``js_runtimes`` on all YoutubeDL instances (an explicit setting still wins).

YTDLP_JS_RUNTIME=node|deno|bun forces one; empty picks deno, then node.
"""

from __future__ import annotations

import os
import shutil


def install() -> None:
    try:
        import yt_dlp
    except ImportError:
        return
    if getattr(yt_dlp.YoutubeDL, "_supoclip_js_runtime", None) is not None:
        return
    runtime = (os.getenv("YTDLP_JS_RUNTIME") or "").strip().lower()
    if not runtime:
        runtime = next((r for r in ("deno", "node", "bun") if shutil.which(r)), "")
    yt_dlp.YoutubeDL._supoclip_js_runtime = runtime
    if not runtime:
        return
    original = yt_dlp.YoutubeDL.__init__

    def __init__(self, params=None, *args, **kwargs):
        params = dict(params or {})
        params.setdefault("js_runtimes", {runtime: {}})
        original(self, params, *args, **kwargs)

    yt_dlp.YoutubeDL.__init__ = __init__
