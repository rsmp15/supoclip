"""Live clipping sessions: one light engine process per stream + a shared render queue + an error loop.

A session process records a rolling 10-min buffer, reads chat, detects hype and cuts raw.mp4 (status
"needs_edit"). The render queue (1 at a time by default; Whisper + encode is RAM-heavy) turns raw.mp4 into
final.mp4 (status "needs_review"). Crashed sessions restart with backoff while the stream is live; failed renders
retry 3x. Single-machine feature: engine processes, clip files and this manager share one disk (TEMP_DIR/live).
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

ENGINE = Path(__file__).resolve().parent / "engine"
MAX_RESTARTS = 5
DEFAULTS: Dict[str, Any] = dict(renderers=1, max_height=720, hours=6, k=3.0, model="base", query="Gaming",
                                auto_search=True, privacy="public")
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def enabled() -> bool:
    return os.getenv("LIVE_CLIPPING", "false").strip().lower() in ("1", "true", "yes")


def data_dir() -> Path:
    base = os.getenv("LIVE_DATA_DIR") or str(Path(os.getenv("TEMP_DIR", "temp")) / "live")
    d = Path(base).resolve(); d.mkdir(parents=True, exist_ok=True)
    return d


def clips_dir() -> Path:
    return data_dir() / "clips"


def python_exe() -> str:
    return os.getenv("LIVE_PYTHON") or shutil.which("python") or sys.executable


def engine_env(max_height: Optional[int] = None) -> Dict[str, str]:
    env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8", CLIPFORGE_DATA=str(data_dir()))
    for src, dst in (("LIVE_KIT_PARENT", "CLIPFORGE_WS"), ("LIVE_FONT_DIR", "CLIPFORGE_FONT_DIR")):
        if os.getenv(src):
            env[dst] = os.environ[src]
    if max_height:
        env["CLIPFORGE_MAX_HEIGHT"] = str(max_height)
    for k in ("DATABASE_URL", "BACKEND_AUTH_SECRET", "BETTER_AUTH_SECRET", "APP_SETTINGS_ENCRYPTION_KEY"):
        env.pop(k, None)                      # the engine needs none of the API's secrets
    return env


def run_engine(args: List[str], timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run([python_exe(), "-u", *args], cwd=ENGINE, env=engine_env(), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout, creationflags=_NO_WINDOW)


# ------------------------------------------------------------------ settings
def settings() -> Dict[str, Any]:
    s = dict(DEFAULTS)
    f = data_dir() / "live_settings.json"
    if f.exists():
        try:
            s.update(json.loads(f.read_text("utf-8")))
        except ValueError:
            pass
    return s


def save_settings(patch: Dict[str, Any]) -> Dict[str, Any]:
    s = settings()
    s.update({k: v for k, v in patch.items() if k in DEFAULTS})
    s["renderers"] = max(1, min(4, int(s["renderers"])))
    (data_dir() / "live_settings.json").write_text(json.dumps(s, indent=1), "utf-8")
    return s


# ------------------------------------------------------------------ sessions
def _kill_tree(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True)
    else:
        try:
            os.killpg(os.getpgid(pid), 15)
        except OSError:
            pass


class Session:
    def __init__(self, sid: str, kind: str, label: str, args: List[str], info: Dict[str, Any], user_id: str):
        self.id, self.kind, self.label, self.args, self.info, self.user_id = sid, kind, label, args, info, user_id
        self.dir = data_dir() / "runs" / sid; self.dir.mkdir(parents=True, exist_ok=True)
        self.status_f, self.log_f = self.dir / "status.json", self.dir / "log.txt"
        self.proc: Optional[subprocess.Popen] = None
        self.restarts, self.stopped, self.state, self.note = 0, False, "starting", ""
        self.started, self.next_restart, self.done = time.time(), None, False

    def start(self) -> None:
        self.status_f.unlink(missing_ok=True)
        logf = open(self.log_f, "a", encoding="utf-8")
        logf.write(f"\n===== {dt.datetime.now():%H:%M:%S} start #{self.restarts + 1}\n"); logf.flush()
        self.proc = subprocess.Popen(
            [python_exe(), "-u", *self.args, "--status-file", str(self.status_f)], cwd=ENGINE,
            env=engine_env(settings()["max_height"]), stdout=logf, stderr=subprocess.STDOUT, creationflags=_NO_WINDOW,
            start_new_session=os.name != "nt")
        self.state, self.started, self.next_restart, self.done = "starting", time.time(), None, False

    def stop(self) -> None:
        self.stopped = True
        if self.proc and self.proc.poll() is None:
            _kill_tree(self.proc.pid)
        self.state = "stopped"

    def status(self) -> Dict[str, Any]:
        try:
            return json.loads(self.status_f.read_text("utf-8"))
        except (OSError, ValueError):
            return {}

    def alive(self) -> bool:
        return bool(self.proc and self.proc.poll() is None)

    def view(self) -> Dict[str, Any]:
        try:
            log = self.log_f.read_text("utf-8", errors="replace").splitlines()[-12:]
        except OSError:
            log = []
        return dict(id=self.id, kind=self.kind, label=self.label, info=self.info, state=self.state, note=self.note,
                    restarts=self.restarts, uptime=int(time.time() - self.started), status=self.status(), log=log,
                    alive=self.alive())


_lock = threading.RLock()
_sessions: Dict[str, Session] = {}
_started = False


def _still_live(s: Session) -> bool:
    try:
        if s.kind == "twitch":
            r = subprocess.run(["streamlink", "--json", s.info["url"]], capture_output=True, text=True, timeout=60,
                               encoding="utf-8", errors="replace", creationflags=_NO_WINDOW)
            return '"error"' not in (r.stdout or "")
        if s.kind == "youtube":
            r = subprocess.run(["yt-dlp", "--js-runtimes", "node", "-j", "--skip-download", s.info["url"]],
                               capture_output=True, text=True, timeout=90, encoding="utf-8", errors="replace",
                               creationflags=_NO_WINDOW)
            return '"live_status": "is_live"' in (r.stdout or "")
    except Exception:
        return True
    return False


def _supervise() -> None:
    while True:
        time.sleep(5)
        with _lock:
            items = list(_sessions.values())
        for s in items:
            try:
                if s.stopped or s.proc is None:
                    continue
                if s.alive():
                    s.state = s.status().get("state", "starting"); continue
                if s.next_restart:
                    if time.time() >= s.next_restart:
                        s.restarts += 1; s.start()
                    continue
                if s.done:
                    continue
                s.done = True
                rc, reason = s.proc.returncode, s.status().get("reason")
                if rc == 0 and reason == "no_chat_replay":
                    s.state, s.note = "finished", "no chat replay: this video was never live. Use Create for normal videos."
                    continue
                if rc == 0 and reason in ("max_clips", "time_up", "vod_done", "stopped"):
                    s.state, s.note = "finished", reason; continue
                if reason == "offline" or (s.kind in ("youtube", "twitch") and not _still_live(s)):
                    s.state, s.note = "ended", "stream is offline"; continue
                if s.restarts >= (MAX_RESTARTS if s.kind in ("youtube", "twitch") else 2):
                    s.state, s.note = "error", f"gave up after {s.restarts} restarts (exit {rc}, {reason or 'no status'})"
                    continue
                delay = 10 * (s.restarts + 1)
                s.state, s.note = "restarting", f"exit {rc} ({reason or 'crash'}), retry in {delay}s"
                s.next_restart, s.done = time.time() + delay, False
            except Exception:
                logger.exception("live supervisor error for %s", s.id)


def start_session(item: Dict[str, Any], user_id: str) -> Session:
    st = settings()
    kind = item.get("platform", "youtube")
    if kind == "youtube":
        m = re.search(r"(?:v=|youtu\.be/|live/)([\w-]{11})", item.get("url", "")) if not item.get("id") else None
        vid = item.get("id") or (m.group(1) if m else None)
        if not vid:
            raise ValueError("not a YouTube live URL")
        url = f"https://www.youtube.com/watch?v={vid}"
        sid, label = f"yt_{vid}", item.get("channel") or vid
        args = ["livehype.py", "live", "--url", url, "--no-edit", "--hours", str(st["hours"]),
                "--title", item.get("title", ""), "--channel", item.get("channel", "")]
        info = dict(url=url, title=item.get("title", ""), channel=item.get("channel", ""), game=item.get("game", ""),
                    thumb=item.get("thumb") or f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg")
    elif kind == "twitch":
        login = re.sub(r"[^a-z0-9_]", "", (item.get("login") or "").lower().strip().rsplit("/", 1)[-1])
        if not login:
            raise ValueError("Twitch channel name required")
        q = "720p60,720p,best" if int(st["max_height"]) <= 720 else "1080p60,1080p,best"
        sid, label = f"tw_{login}", login
        args = ["twitchclip.py", "live", login, "--no-edit", "--hours", str(st["hours"]), "--quality", q, "--k", str(st["k"])]
        info = dict(url=f"https://www.twitch.tv/{login}", login=login, title=item.get("title", ""), channel=login)
    elif kind == "vod":
        m = re.search(r"(?:v=|youtu\.be/|live/)([\w-]{11})", item.get("url", ""))
        if not m:
            raise ValueError("not a YouTube video URL")
        sid, label = f"vod_{m.group(1)}", f"VOD {m.group(1)}"
        args = ["livehype.py", "vod", item["url"], "--no-edit"]
        info = dict(url=item["url"], title="past stream (chat replay)")
    else:
        raise ValueError(f"unknown platform {kind}")
    with _lock:
        s = _sessions.get(sid)
        if s and s.alive():
            return s
        s = Session(sid, kind, label, args, info, user_id); _sessions[sid] = s
    (data_dir() / "owner.json").write_text(json.dumps({"user_id": user_id}), "utf-8")
    s.start()
    return s


def sessions() -> List[Dict[str, Any]]:
    with _lock:
        return [s.view() for s in _sessions.values()]


def get_session(sid: str) -> Optional[Session]:
    with _lock:
        return _sessions.get(sid)


def stop_all() -> None:
    with _lock:
        items = list(_sessions.values())
    for s in items:
        s.stop()


def clear_finished() -> None:
    with _lock:
        for k in [k for k, s in _sessions.items() if not s.alive() and not s.next_restart]:
            _sessions.pop(k)


def owner_for(source_url: str) -> Optional[str]:
    with _lock:
        for s in _sessions.values():
            if s.info.get("url") == source_url:
                return s.user_id
    try:
        return json.loads((data_dir() / "owner.json").read_text("utf-8")).get("user_id")
    except (OSError, ValueError):
        return None


# ------------------------------------------------------------------ clips + render queue
def clip_dirs() -> List[Path]:
    return sorted((p.parent for p in clips_dir().glob("*/*/meta.json")), key=lambda d: d.stat().st_mtime)


def read_meta(d: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads((d / "meta.json").read_text("utf-8"))
    except (OSError, ValueError):
        return None


def write_meta(d: Path, m: Dict[str, Any]) -> None:
    tmp = d / "meta.json.tmp"
    tmp.write_text(json.dumps(m, indent=1, ensure_ascii=False), "utf-8")
    os.replace(tmp, d / "meta.json")


def clip_path(rel: str) -> Path:
    d = (clips_dir() / rel).resolve()
    if clips_dir().resolve() not in d.parents or not (d / "meta.json").exists():
        raise FileNotFoundError(rel)
    return d


def _claim_next():
    with _lock:
        for d in clip_dirs():
            m = read_meta(d)
            if m and m.get("status") == "needs_edit" and (d / "raw.mp4").exists():
                m["status"] = "rendering"; write_meta(d, m)
                return d, m
    return None, None


def _renderer(n: int) -> None:
    while True:
        try:
            if n >= int(settings()["renderers"]):
                time.sleep(5); continue
            d, m = _claim_next()
            if not d:
                time.sleep(3); continue
            script = "twitchclip.py" if m.get("platform") == "twitch" else "livehype.py"
            try:
                r = run_engine([script, "reedit", str(d), "--model", settings()["model"]], timeout=1200)
                ok, err = r.returncode == 0 and (d / "final.mp4").exists(), (r.stdout + r.stderr)[-800:]
            except subprocess.TimeoutExpired:
                ok, err = False, "render timed out after 20 min"
            m = read_meta(d) or m
            if ok and m.get("status") == "needs_review":
                m.pop("render_error", None)
            else:
                m["edit_attempts"] = m.get("edit_attempts", 0) + 1
                m["render_error"] = err
                m["status"] = "needs_edit" if m["edit_attempts"] < 3 else "edit_failed"
            write_meta(d, m)
        except Exception:
            logger.exception("live renderer error"); time.sleep(5)


def clip_view(d: Path) -> Dict[str, Any]:
    from .engine import social_copy

    m = read_meta(d) or {}
    if m.get("status") in ("needs_review", "approved", "posted", "rejected"):
        with _lock:
            m = read_meta(d) or m
            if social_copy.ensure(m):          # per-platform captions + virality, kept in meta.json
                write_meta(d, m)
    rel = d.relative_to(clips_dir()).as_posix()
    final = d / "final.mp4"
    return dict(rel=rel, status=m.get("status"), hook=m.get("hook", ""), yt_title=m.get("yt_title", ""),
                channel=m.get("channel", ""), platform=m.get("platform", "youtube"), game=m.get("game_label", ""),
                ratio=m.get("ratio"), top_chat=m.get("top_chat", [])[:4], created=m.get("created", ""),
                length=round((m.get("clip_end") or 0) - (m.get("clip_start") or 0), 1), layout=m.get("layout"),
                has_final=final.exists(), version=int(final.stat().st_mtime) if final.exists() else 0,
                description=m.get("description", ""), published=m.get("published", {}), source_url=m.get("source_url"),
                render_error=m.get("render_error"), supoclip=m.get("supoclip"),
                transcript=m.get("transcript", ""), virality=m.get("virality"), social_copy=m.get("social_copy"),
                style=m.get("style") or {}, style_used=m.get("style_used"), detector=m.get("detector"),
                cam_box_found=m.get("cam_box_found"),
                social_copy_edited=m.get("social_copy_edited", []))


STYLE_CHOICES: Dict[str, Any] = {
    "intro": bool, "title_bar": bool, "voiceover": bool,
    "captions": ("pop", "classic", "off"), "color_fx": ("auto", "red", "green", "none"),
    "label": str, "bar_text": str, "vo_text": str,
}


def clean_style(style: Dict[str, Any]) -> Dict[str, Any]:
    """Keep only known edit-style keys with valid values (the engine reads meta["style"])."""
    out: Dict[str, Any] = {}
    for key, rule in STYLE_CHOICES.items():
        if key not in style:
            continue
        value = style[key]
        if rule is bool:
            out[key] = bool(value)
        elif rule is str:
            out[key] = str(value or "").strip()[:60 if key != "vo_text" else 200]
        elif value in rule:
            out[key] = value
    return out


def update_clip(rel: str, body: Dict[str, Any]) -> Dict[str, Any]:
    d = clip_path(rel)
    with _lock:
        m = read_meta(d) or {}
        rerender = False
        hook = (body.get("hook") or "").strip()
        if hook and hook.upper() != m.get("hook"):
            m["hook"] = hook.upper(); rerender = True
        title = body.get("yt_title")
        if isinstance(title, str) and title.strip() and title.strip() != m.get("yt_title"):
            m["yt_title"] = title.strip()[:100]; m["title_locked"] = True
        status = body.get("status")
        if status in ("approved", "rejected", "needs_review", "posted"):
            if status == "approved" and m.get("status") not in ("needs_review", "approved"):
                raise ValueError(f"cannot approve a clip that is '{m.get('status')}'")
            if status == "posted" and m.get("status") not in ("approved", "posted"):
                raise ValueError("only approved clips can be marked posted")
            m["status"] = status
        copy = body.get("social_copy")
        if isinstance(copy, dict):                       # user edits to a platform's caption/description
            cur, edited = m.get("social_copy") or {}, set(m.get("social_copy_edited") or [])
            for platform, fields in copy.items():
                if platform in ("youtube", "instagram", "tiktok") and isinstance(fields, dict):
                    clean = {k: v for k, v in fields.items() if k in ("title", "description", "caption", "tags")}
                    cur[platform] = {**cur.get(platform, {}), **clean}; edited.add(platform)
            m["social_copy"], m["social_copy_edited"] = cur, sorted(edited)
        style = body.get("style")
        if isinstance(style, dict):                      # editor: effects / captions / title bar / voiceover
            m["style"] = {**(m.get("style") or {}), **clean_style(style)}
            rerender = True
        regen = body.get("regenerate_copy")
        if regen:                                        # drop edits -> the generator rewrites that platform
            m["social_copy_edited"] = [] if regen == "all" else [p for p in m.get("social_copy_edited", []) if p != regen]
        if body.get("rerender") or rerender:
            m["status"], m["edit_attempts"] = "needs_edit", 0
        write_meta(d, m)
    return clip_view(d)


def system() -> Dict[str, Any]:
    out: Dict[str, Any] = dict(sessions=sum(1 for s in _sessions.values() if s.alive()))
    metas = [read_meta(d) or {} for d in clip_dirs()]
    out.update(queue=sum(m.get("status") == "needs_edit" for m in metas),
               rendering=sum(m.get("status") == "rendering" for m in metas),
               review=sum(m.get("status") == "needs_review" for m in metas),
               disk_free_gb=round(shutil.disk_usage(data_dir()).free / 2**30, 1))
    try:
        import psutil
        vm = psutil.virtual_memory()
        out.update(cpu=psutil.cpu_percent(), ram=vm.percent, ram_free_gb=round(vm.available / 2**30, 1))
    except ImportError:
        pass
    return out


def search(query: str) -> List[Dict[str, Any]]:
    r = run_engine(["livehype.py", "gaming", query or settings()["query"], "--json"], timeout=180)
    try:
        items = json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        raise RuntimeError((r.stderr or r.stdout or "search failed")[-400:])
    with _lock:
        active = {s.info.get("url") for s in _sessions.values() if s.alive()}
    return [dict(i, active=i["url"] in active) for i in items]


def publish_external(rel: str, platforms: List[str], privacy: str, via: str) -> Dict[str, Any]:
    """Postiz (YouTube + Instagram) or the free YouTube API, via the engine's publish.py. Approved clips only."""
    d = clip_path(rel)
    m = read_meta(d) or {}
    if m.get("status") != "approved":
        raise ValueError("only approved clips can be published")
    r = run_engine(["publish.py", str(d), "--to", ",".join(platforms), "--privacy", privacy, "--via", via, "--yes"],
                   timeout=3600)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip()[-800:])
    return clip_view(d)


def recover() -> None:
    for d in clip_dirs():
        m = read_meta(d)
        if m and m.get("status") == "rendering" and (d / "raw.mp4").exists():
            m["status"] = "needs_edit"; write_meta(d, m)


def start() -> None:
    """Start the supervisor + render threads once per API process."""
    global _started
    if _started or not enabled():
        return
    _started = True
    recover()
    threading.Thread(target=_supervise, name="live-supervisor", daemon=True).start()
    for i in range(4):
        threading.Thread(target=_renderer, args=(i,), name=f"live-render-{i}", daemon=True).start()
    logger.info("Live clipping enabled (data: %s, engine python: %s)", data_dir(), python_exe())


def shutdown() -> None:
    stop_all()
