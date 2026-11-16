"""SupoClip.exe - one-click launcher for the local install (Windows).

Starts what is not already running, in order, and waits for each to be healthy:
  Redis -> (PostgreSQL check) -> API (uvicorn) -> worker -> website (production build, else dev server)
then opens SupoClip in its own window and sits in the tray (Open / Restart / Update yt-dlp / Logs / Quit).
No console windows; every service logs to logs/launcher/. Services it did not start are left alone on Quit.

Paths come from launcher.json next to the exe (created with defaults on first run).
Build: launcher/build_exe.bat  ->  SupoClip.exe in the repo root.
"""
import ctypes
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

NO_WINDOW = 0x08000000                     # CREATE_NO_WINDOW
NEW_GROUP = 0x00000200                     # CREATE_NEW_PROCESS_GROUP
APP = "SupoClip"


def repo_root() -> Path:
    if getattr(sys, "frozen", False):        # the exe sits in the repo root
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


ROOT = repo_root()
CONFIG_FILE = ROOT / "launcher.json"
DEFAULTS = {
    "redis_exe": str(ROOT.parent / "tools" / "redis" / "redis-server.exe"),
    "redis_conf": str(ROOT.parent / "tools" / "redis" / "redis.windows.conf"),
    "backend_dir": str(ROOT / "backend"),
    "frontend_dir": str(ROOT / "frontend"),
    "python": str(ROOT / "backend" / ".venv" / "Scripts" / "python.exe"),
    "node": "node",
    "pnpm": "pnpm",
    "web_build_dir": ".next-prod",
    "ports": {"redis": 6379, "postgres": 5432, "api": 8000, "web": 3107},
    "open_path": "/live",
    "start_worker": True,
}


def load_config() -> dict:
    cfg = json.loads(json.dumps(DEFAULTS))
    if CONFIG_FILE.exists():
        try:
            user = json.loads(CONFIG_FILE.read_text("utf-8"))
            cfg.update({k: v for k, v in user.items() if k != "ports"})
            cfg["ports"].update(user.get("ports") or {})
        except ValueError:
            pass
    else:
        CONFIG_FILE.write_text(json.dumps(cfg, indent=2), "utf-8")
    return cfg


CFG = load_config()
LOGS = ROOT / "logs" / "launcher"
LOGS.mkdir(parents=True, exist_ok=True)


def log(msg: str) -> None:
    with open(LOGS / "launcher.log", "a", encoding="utf-8") as f:
        f.write(time.strftime("[%Y-%m-%d %H:%M:%S] ") + msg + "\n")


def port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def http_ok(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=3) as r:
            return r.status < 500
    except urllib.error.HTTPError as e:
        return e.code < 500
    except Exception:
        return False


def read_env_file(path: Path) -> dict:
    out = {}
    if path.exists():
        for line in path.read_text("utf-8", errors="replace").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    return out


class Service:
    def __init__(self, name, start, healthy, timeout=120):
        self.name, self._start, self.healthy, self.timeout = name, start, healthy, timeout
        self.proc = None            # set only when this launcher started it
        self.state = "stopped"      # stopped / starting / running / external / failed

    def check(self) -> bool:
        try:
            return bool(self.healthy())
        except Exception as e:              # a broken check must never stall the start-up
            log(f"{self.name} health check error: {e}")
            return False

    def ensure(self) -> bool:
        if self.check():
            self.state = "running" if self.proc else "external"
            return True
        self.state = "starting"
        log(f"starting {self.name}")
        try:
            self.proc = self._start()
        except Exception as e:
            log(f"{self.name} failed to start: {e}")
            self.state = "failed"
            return False
        t_end = time.time() + self.timeout
        while time.time() < t_end:
            if self.check():
                self.state = "running"
                log(f"{self.name} is up")
                return True
            if self.proc is not None and self.proc.poll() is not None:
                break
            time.sleep(1)
        self.state = "failed"
        log(f"{self.name} did not become healthy (see logs/launcher/{self.name}.log)")
        return False

    def stop(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(self.proc.pid)], capture_output=True, creationflags=NO_WINDOW)
        self.proc = None
        self.state = "stopped"


def spawn(name, args, cwd, env=None):
    logf = open(LOGS / f"{name}.log", "a", encoding="utf-8")
    logf.write(time.strftime("\n===== %Y-%m-%d %H:%M:%S start\n")); logf.flush()
    return subprocess.Popen([str(a) for a in args], cwd=str(cwd), stdout=logf, stderr=subprocess.STDOUT,
                            env=env or os.environ.copy(), creationflags=NO_WINDOW | NEW_GROUP)


def worker_running() -> bool:
    """Is a SupoClip worker already running? (wmic is gone on new Windows 11 builds: PowerShell only.)"""
    ps = subprocess.run(["powershell", "-NoProfile", "-Command",
                         "(Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'run_workers' }).Count"],
                        capture_output=True, text=True, creationflags=NO_WINDOW, timeout=30).stdout.strip()
    return ps not in ("", "0")


def build_services():
    P, be, fe = CFG["ports"], Path(CFG["backend_dir"]), Path(CFG["frontend_dir"])
    py = CFG["python"]
    services = [
        Service("redis", lambda: spawn("redis", [CFG["redis_exe"], CFG["redis_conf"], "--bind", "127.0.0.1",
                                                 "--port", P["redis"]], Path(CFG["redis_exe"]).parent),
                lambda: port_open(P["redis"]), 30),
        Service("postgres", lambda: None, lambda: port_open(P["postgres"]), 5),
        Service("api", lambda: spawn("api", [py, "-m", "uvicorn", "src.main:app", "--host", "127.0.0.1", "--port", P["api"]], be),
                lambda: http_ok(f"http://127.0.0.1:{P['api']}/health"), 120),
    ]
    if CFG.get("start_worker", True):
        holder = {}

        def start_worker():
            holder["p"] = spawn("worker", [py, "-m", "src.workers.run_workers"], be)
            return holder["p"]

        def worker_ok():
            p = holder.get("p")
            return (p is not None and p.poll() is None) or (p is None and worker_running())
        services.append(Service("worker", start_worker, worker_ok, 20))

    def start_web():
        env = os.environ.copy()
        for f in (".env", ".env.local", ".env.production", ".env.production.local"):
            env.update(read_env_file(fe / f))
        env.update(PORT=str(P["web"]), HOSTNAME="127.0.0.1", NODE_ENV="production", NEXT_TELEMETRY_DISABLED="1")
        found = sorted((fe / CFG["web_build_dir"] / "standalone").glob("**/server.js"), key=lambda p: len(p.parts))
        server = next((p for p in found if "node_modules" not in p.parts), None)
        if server is not None and (server.parent / CFG["web_build_dir"] / "static").exists():
            return spawn("web", [CFG["node"], server], server.parent, env)
        log("no production build found: starting the dev server (slower). Build it with launcher/build_web.bat")
        env.pop("NODE_ENV", None)
        return spawn("web", ["cmd", "/c", CFG["pnpm"], "dev"], fe, env)
    services.append(Service("web", start_web, lambda: http_ok(f"http://127.0.0.1:{P['web']}/"), 240))
    return services


SERVICES = build_services()
ICON = None
_lock = threading.Lock()


def notify(msg: str) -> None:
    log(msg)
    try:
        if ICON is not None:
            ICON.notify(msg, APP)
    except Exception:
        pass


def status_text() -> str:
    ok = sum(s.state in ("running", "external") for s in SERVICES)
    bad = [s.name for s in SERVICES if s.state == "failed"]
    return f"{APP}: {ok}/{len(SERVICES)} running" + (f" - problem: {', '.join(bad)}" if bad else "")


def open_window(_=None) -> None:
    url = f"http://localhost:{CFG['ports']['web']}{CFG.get('open_path', '/')}"
    for edge in (Path(os.environ.get("ProgramFiles(x86)", "")) / "Microsoft/Edge/Application/msedge.exe",
                 Path(os.environ.get("ProgramFiles", "")) / "Microsoft/Edge/Application/msedge.exe"):
        if edge.exists():
            subprocess.Popen([str(edge), f"--app={url}", "--window-size=1440,920"], creationflags=NO_WINDOW)
            return
    webbrowser.open(url)


def start_all(open_after=True) -> None:
    try:
        _start_all(open_after)
    except Exception as e:
        notify(f"Start-up error: {e}")


def _start_all(open_after=True) -> None:
    with _lock:
        for s in SERVICES:
            if not s.ensure():
                if s.name == "postgres":
                    notify("PostgreSQL is not running. Start the PostgreSQL service, then choose Restart.")
                elif s.name == "redis":
                    notify(f"Redis did not start. Check {CFG['redis_exe']} (logs/launcher/redis.log).")
                else:
                    notify(f"{s.name} did not start - see logs/launcher/{s.name}.log")
                if s.name in ("redis", "postgres", "api"):
                    break                       # everything after needs these
            refresh_title()
        refresh_title()
    if open_after and all(s.state in ("running", "external") for s in SERVICES if s.name in ("api", "web")):
        notify("SupoClip is ready")
        open_window()


def stop_all() -> None:
    with _lock:
        for s in reversed(SERVICES):
            s.stop()
    refresh_title()


def refresh_title() -> None:
    if ICON is not None:
        ICON.title = status_text()


def restart(_=None) -> None:
    def run():
        notify("Restarting SupoClip...")
        stop_all()
        time.sleep(2)
        start_all()
    threading.Thread(target=run, daemon=True).start()


def update_ytdlp(_=None) -> None:
    def run():
        r = subprocess.run(["yt-dlp", "-U"], capture_output=True, text=True, creationflags=NO_WINDOW)
        notify((r.stdout or r.stderr or "yt-dlp: done").strip().splitlines()[-1][:200])
    threading.Thread(target=run, daemon=True).start()


def open_logs(_=None) -> None:
    os.startfile(LOGS)


def quit_app(icon, _=None) -> None:
    notify("Stopping SupoClip...")
    stop_all()
    icon.stop()


def tray_image():
    from PIL import Image, ImageDraw
    ico = ROOT / "launcher" / "supoclip.ico"
    if ico.exists():
        try:
            return Image.open(ico)
        except Exception:
            pass
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((4, 4, 60, 60), 14, fill=(20, 20, 20, 255))
    d.text((22, 18), "S", fill=(255, 255, 255, 255))
    return img


def single_instance() -> bool:
    ctypes.windll.kernel32.CreateMutexW(None, False, "SupoClipLauncherMutex")
    return ctypes.windll.kernel32.GetLastError() != 183      # ERROR_ALREADY_EXISTS


def main() -> None:
    global ICON
    if not single_instance():
        open_window()                                        # already running: just bring up the window
        return
    import pystray
    menu = pystray.Menu(
        pystray.MenuItem("Open SupoClip", open_window, default=True),
        pystray.MenuItem(lambda _i: status_text(), None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Restart services", restart),
        pystray.MenuItem("Update yt-dlp", update_ytdlp),
        pystray.MenuItem("Open logs folder", open_logs),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit (stop SupoClip)", quit_app),
    )
    ICON = pystray.Icon(APP, tray_image(), f"{APP}: starting...", menu)

    def setup(icon):
        icon.visible = True
        threading.Thread(target=start_all, daemon=True).start()
    log("launcher started")
    ICON.run(setup=setup)
    log("launcher exited")


if __name__ == "__main__":
    main()
