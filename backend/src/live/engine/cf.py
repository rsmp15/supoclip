"""clipforge shared helpers: download, probe, transcribe, ASS captions, face tracking, SFX."""
import json, os, subprocess, re, wave
from pathlib import Path
import numpy as np

# Paths can be overridden so the same engine runs inside SupoClip (backend/src/live/engine) with its data elsewhere.
ROOT = Path(os.environ.get("CLIPFORGE_DATA") or Path(__file__).resolve().parent)   # clips/, livebuf/, work/
WS = Path(os.environ.get("CLIPFORGE_WS") or ROOT.parent)                           # holds "Claude Edit Asset/"
FONT_DIR = Path(os.environ.get("CLIPFORGE_FONT_DIR") or Path(__file__).resolve().parent / "fonts")
FONT = FONT_DIR / "seguibl.ttf"          # PIL overlays (rank.py)
FONT_NAME = "Inter Display Black"          # libass captions (fonts/InterDisplay-Black.ttf, from the edit kit)
YTDLP = ["yt-dlp", "--js-runtimes", "node"]
WORK = ROOT / "work"
WORK.mkdir(parents=True, exist_ok=True)


def sh(cmd, cwd=None, check=True):
    r = subprocess.run([str(c) for c in cmd], cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if check and r.returncode:
        raise RuntimeError(f"{cmd[0]} failed:\n{r.stderr[-1800:]}")
    return r.stdout


def probe(path):
    j = json.loads(sh(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams",
                       "-show_format", path]))
    v = next(s for s in j["streams"] if s["codec_type"] == "video")
    num, den = v["r_frame_rate"].split("/")
    return dict(w=int(v["width"]), h=int(v["height"]), fps=float(num) / float(den or 1),
                dur=float(j["format"]["duration"]),
                audio=any(s["codec_type"] == "audio" for s in j["streams"]))


def download(url, outdir, max_h=1080):
    """Download a URL (YouTube/Instagram/TikTok/...) with yt-dlp. Local paths pass through."""
    outdir = Path(outdir); outdir.mkdir(parents=True, exist_ok=True)
    if Path(url).exists():
        p = Path(url).resolve()
        return dict(path=str(p), title=p.stem, views=0, url=str(p), id=p.stem[:24])
    out = sh([*YTDLP, "-f", f"bv*[height<={max_h}]+ba/b[height<={max_h}]/b",
              "--merge-output-format", "mp4", "--no-playlist", "--print-json", "--no-simulate",
              "-o", str(outdir / "%(id)s.%(ext)s"), url])
    info = json.loads(out.strip().splitlines()[-1])
    path = (info.get("requested_downloads") or [{}])[0].get("filepath") or str(outdir / f"{info['id']}.mp4")
    return dict(path=path, title=info.get("title", ""), views=info.get("view_count") or 0,
                url=url, id=info["id"], duration=info.get("duration"), uploader=info.get("uploader"))


def audio_rms(path, ss=0, dur=None, hop=0.25, sr=16000):
    """RMS loudness curve (one value per `hop` seconds)."""
    cmd = ["ffmpeg", "-v", "error", "-ss", str(ss)] + (["-t", str(dur)] if dur else []) + \
          ["-i", path, "-vn", "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"]
    raw = subprocess.run(cmd, capture_output=True).stdout
    a = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768
    n = int(sr * hop)
    if len(a) < n:
        return np.zeros(1)
    a = a[: len(a) // n * n].reshape(-1, n)
    return np.sqrt((a ** 2).mean(axis=1))


_model = None
_hf = None


def dehallucinate(ws, n=4, max_rep=2):
    """Drop Whisper repetition loops ('I'm not going to be able to do it' x20) - common on music/noise.
    Removes any n-word phrase after its `max_rep`-th occurrence; returns [] if the rest is still loop-like."""
    import zlib
    key = lambda i: " ".join(re.sub(r"\W", "", x["w"].lower()) for x in ws[i:i + n])
    seen, keep, i = {}, [], 0
    while i < len(ws):
        k = key(i)
        if len(k.split()) == n and seen.get(k, 0) >= max_rep:
            i += n; continue
        seen[k] = seen.get(k, 0) + 1
        keep.append(ws[i]); i += 1
    if len(ws) >= 12 and len(keep) < len(ws) * 0.7:              # >30 % was loop -> whole thing is hallucinated
        return []
    text = " ".join(x["w"] for x in keep).encode()
    if len(text) > 80 and len(text) / len(zlib.compress(text)) > 2.4:   # whisper's own loop heuristic
        return []
    return keep


def transcribe_hf(vp, model="base"):
    """Whisper through Hugging Face transformers (CPU). Word timestamps via cross-attention alignment."""
    global _hf
    import torch
    from transformers import pipeline
    if _hf is None:
        _hf = pipeline("automatic-speech-recognition", model=f"openai/whisper-{model}", device="cpu",
                       dtype=torch.float32)
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(vp), "-vn", "-ac", "1", "-ar", "16000", "-f", "s16le", "-"],
                         capture_output=True).stdout
    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768
    if audio.size < 1600:
        return dict(words=[], segments=[])
    r = _hf({"raw": audio, "sampling_rate": 16000}, return_timestamps="word", chunk_length_s=30, batch_size=4,
            generate_kwargs={"language": "en", "task": "transcribe"})
    ws = []
    for c in r.get("chunks", []):
        s, e = c["timestamp"]
        t = c["text"].strip()
        if t and s is not None:
            ws.append(dict(w=t, s=round(float(s), 2), e=round(float(e if e is not None else s + 0.3), 2)))
    ws = dehallucinate(ws)
    segs, cur = [], []
    for w in ws:
        if cur and (w["s"] - cur[-1]["e"] > 0.7 or len(cur) >= 12 or cur[-1]["w"][-1:] in ".?!"):
            segs.append(cur); cur = []
        cur.append(w)
    if cur:
        segs.append(cur)
    return dict(words=ws, segments=[dict(s=c[0]["s"], e=c[-1]["e"], text=" ".join(x["w"] for x in c)) for c in segs])


def yt_subs(vp, lang="en"):
    """Fallback transcript: YouTube auto-captions (json3 has per-word offsets). File stem must be the video id."""
    vid = Path(vp).stem
    if not re.fullmatch(r"[\w-]{11}", vid):
        return None
    d = WORK / "subs"; d.mkdir(exist_ok=True)
    sh([*YTDLP, "--skip-download", "--write-auto-subs", "--write-subs", "--sub-langs", f"{lang}.*,{lang}",
        "--sub-format", "json3", "-o", str(d / "%(id)s"), f"https://www.youtube.com/watch?v={vid}"], check=False)
    fs = sorted(d.glob(f"{vid}*.json3"))
    if not fs:
        return None
    ws = []
    for ev in json.loads(fs[0].read_text("utf-8")).get("events", []):
        segs = ev.get("segs") or []
        base = ev.get("tStartMs", 0)
        for i, sg in enumerate(segs):
            t = re.sub(r"\[[^\]]*\]|>>+", "", sg.get("utf8", "")).strip()
            if not t:
                continue
            s = (base + sg.get("tOffsetMs", 0)) / 1000
            nxt = (base + segs[i + 1].get("tOffsetMs", 0)) / 1000 if i + 1 < len(segs) else base / 1000 + ev.get("dDurationMs", 400) / 1000
            ws.append(dict(w=t, s=round(s, 2), e=round(max(nxt, s + 0.15), 2)))
    # sentence-ish segments: break on gaps > 0.8s or ~12 words
    segs_out, cur = [], []
    for w in ws:
        if cur and (w["s"] - cur[-1]["e"] > 0.8 or len(cur) >= 12):
            segs_out.append(cur); cur = []
        cur.append(w)
    if cur:
        segs_out.append(cur)
    return dict(words=ws, segments=[dict(s=c[0]["s"], e=c[-1]["e"], text=" ".join(x["w"] for x in c)) for c in segs_out])


def transcribe(video, model="base", cache=True):
    """Word-level transcript via faster-whisper (local, free). Cached next to the video."""
    vp = Path(video)
    cp = vp.with_suffix(".words.json")
    if cache and cp.exists():
        return json.loads(cp.read_text(encoding="utf-8"))
    global _model
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        out = None
        try:
            out = transcribe_hf(vp, model)          # Whisper via transformers (already installed)
        except Exception as e:
            print("  transformers whisper failed:", str(e)[:200])
            out = yt_subs(vp)
        if out is None:
            raise RuntimeError("no Whisper backend and no YouTube captions available (pip install faster-whisper)")
        cp.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
        return out
    if _model is None:
        _model = WhisperModel(model, device="cpu", compute_type="int8")
    segs, _ = _model.transcribe(str(vp), word_timestamps=True, vad_filter=True, beam_size=1)
    out = dict(segments=[], words=[])
    for s in segs:
        ws = [dict(w=w.word.strip(), s=round(w.start, 2), e=round(w.end, 2)) for w in (s.words or [])]
        out["segments"].append(dict(s=round(s.start, 2), e=round(s.end, 2), text=s.text.strip()))
        out["words"] += ws
    cp.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    return out


# ---------- captions (ASS, word-by-word karaoke pop) ----------
def _ts(t):
    t = max(t, 0)
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}"


def ass_header(style="Cap", size=84, margin_v=420, primary="&H00FFFFFF"):
    return ("[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\nWrapStyle: 2\n\n"
            "[V4+ Styles]\nFormat: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,"
            "BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,"
            "Shadow,Alignment,MarginL,MarginR,MarginV,Encoding\n"
            f"Style: {style},{FONT_NAME},{size},{primary},&H000000FF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,7,2,2,136,160,{margin_v},1\n\n"
            "[Events]\nFormat: Layer,Start,End,Name,Style,MarginL,MarginR,MarginV,Effect,Text\n")


HIGHLIGHTS = ["&H0000FFFF", "&H0000FF00", "&H00FFFF00", "&H003CA0FF"]  # yellow, green, cyan, orange (BGR)


def ass_words(words, t0=0.0, t1=None, per_line=3, max_gap=0.7, offset=0.0, style="Cap"):
    """Dialogue lines: chunks of `per_line` words; the spoken word pops in a highlight colour."""
    ws = [w for w in words if w["e"] > t0 and (t1 is None or w["s"] < t1)]
    lines, chunk = [], []
    for w in ws:
        if chunk and (len(chunk) >= per_line or w["s"] - chunk[-1]["e"] > max_gap):
            lines.append(chunk); chunk = []
        chunk.append(w)
    if chunk:
        lines.append(chunk)
    ev = []
    for li, ch in enumerate(lines):
        col = HIGHLIGHTS[li % len(HIGHLIGHTS)]
        for i, w in enumerate(ch):
            s = w["s"]
            nxt = lines[li + 1][0]["s"] if li + 1 < len(lines) else 1e9      # never overlap the next line
            e = ch[i + 1]["s"] if i + 1 < len(ch) else min(w["e"] + 0.25, (t1 or 1e9), nxt)
            e = max(e, s + 0.05)
            txt = " ".join(
                (f"{{\\c{col}\\fscx118\\fscy118}}{x['w'].upper()}{{\\c&HFFFFFF&\\fscx100\\fscy100}}" if j == i
                 else x["w"].upper()) for j, x in enumerate(ch))
            ev.append(f"Dialogue: 0,{_ts(s - t0 + offset)},{_ts(e - t0 + offset)},{style},{style},0,0,0,,{txt}")
    return ev


# ---------- face tracking (OpenCV Haar, free) ----------
def face_track(video, t0, t1, step=0.4):
    """Smoothed face-centre x (0..1 of frame width) per sample; None if no face found."""
    import cv2
    cap = cv2.VideoCapture(str(video))
    det = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    pts, t = [], t0
    while t < t1:
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, fr = cap.read()
        if not ok:
            break
        h, w = fr.shape[:2]
        sc = 480 / w
        g = cv2.cvtColor(cv2.resize(fr, None, fx=sc, fy=sc), cv2.COLOR_BGR2GRAY)
        fs = det.detectMultiScale(g, 1.2, 5, minSize=(30, 30))
        if len(fs):
            x, y, fw, fh = max(fs, key=lambda f: f[2] * f[3])
            pts.append((t, (x + fw / 2) / g.shape[1]))
        t += step
    cap.release()
    if len(pts) < max(2, int((t1 - t0) / step * 0.25)):
        return None
    return pts


def render_vertical(video, t0, t1, out_noaudio, face_pts, W=1080, H=1920):
    """Write 9:16 video-only mp4: face-follow crop (landscape) or pass-through scale (vertical)."""
    import cv2
    cap = cv2.VideoCapture(str(video))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    sw, sh_ = int(cap.get(3)), int(cap.get(4))
    cap.set(cv2.CAP_PROP_POS_MSEC, t0 * 1000)
    cw = min(sw, int(sh_ * W / H))
    if face_pts:
        ts = np.array([p[0] for p in face_pts]); xs = np.array([p[1] for p in face_pts]) * sw
        sm = np.copy(xs)
        for i in range(1, len(sm)):
            sm[i] = sm[i - 1] * 0.6 + xs[i] * 0.4
    p = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24",
                          "-s", f"{W}x{H}", "-r", str(fps), "-i", "-", "-an", "-c:v", "libx264",
                          "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", str(out_noaudio)],
                         stdin=subprocess.PIPE)
    n = int((t1 - t0) * fps)
    for i in range(n):
        ok, fr = cap.read()
        if not ok:
            break
        t = t0 + i / fps
        if face_pts:
            cx = float(np.interp(t, ts, sm))
        else:
            cx = sw / 2
        x0 = int(min(max(cx - cw / 2, 0), sw - cw))
        crop = fr[:, x0:x0 + cw]
        p.stdin.write(cv2.resize(crop, (W, H), interpolation=cv2.INTER_AREA).tobytes())
    p.stdin.close(); p.wait(); cap.release()


# ---------- synthetic SFX (no downloads) ----------
def make_sfx(dirpath):
    """whoosh.wav (rising noise sweep) and hit.wav (sub boom) at 44.1k stereo."""
    sr = 44100
    d = Path(dirpath); d.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(1)

    def save(name, x):
        x = np.clip(x / (np.abs(x).max() + 1e-9) * 0.9, -1, 1)
        with wave.open(str(d / name), "wb") as f:
            f.setnchannels(2); f.setsampwidth(2); f.setframerate(sr)
            f.writeframes((np.repeat(x[:, None], 2, 1) * 32767).astype(np.int16).tobytes())
    n = int(sr * 0.55); t = np.linspace(0, 1, n)
    noise = rng.standard_normal(n)
    env = np.sin(np.pi * t) ** 2 * (0.3 + t)
    # sweep: low-passed noise whose cutoff rises (moving average length shrinks over time)
    lp = np.convolve(noise, np.ones(40) / 40, "same") * 0.7 + noise * 0.3 * t
    save("whoosh.wav", lp * env)
    n = int(sr * 0.5); t = np.arange(n) / sr
    save("hit.wav", np.sin(2 * np.pi * (90 * np.exp(-t * 6) + 35) * t) * np.exp(-t * 7))
    return d / "whoosh.wav", d / "hit.wav"


# ---------- import a transcript made elsewhere (e.g. Gemini "[MM:SS] text" lines) ----------
_TS_RE = re.compile(r"^\s*\[?(?:(\d+):)?(\d{1,2}):(\d{2})(?:[.,]\d+)?\]?\s*[-–:]?\s*(.*)$")


def parse_timed_lines(text):
    """Lines like '[01:23] text' or '1:02:03 text' -> [(seconds, text)]. Lines without a timestamp are appended to the previous one."""
    out = []
    for raw in text.splitlines():
        m = _TS_RE.match(raw)
        if m:
            h, mi, s, rest = m.groups()
            out.append([int(h or 0) * 3600 + int(mi) * 60 + int(s), rest.strip()])
        elif raw.strip() and out:
            out[-1][1] += " " + raw.strip()
    return out


def import_transcript(video, text, max_line=9.0):
    """Write <video>.words.json (the cache transcribe() reads) from a timestamped transcript.
    Gemini gives one timestamp per line, so word times are spread across the line by word length (approximate, +-0.3 s)."""
    lines = parse_timed_lines(text)
    try:
        total = probe(str(video))["dur"]
    except Exception:
        total = lines[-1][0] + 5 if lines else 0
    words, segs = [], []
    for i, (t, txt) in enumerate(lines):
        txt = re.sub(r"\[[^\]]*\]", " ", txt)             # [laughter] / [**] / [संगीत] markers
        toks = txt.split()
        if not toks:
            continue
        nxt = lines[i + 1][0] if i + 1 < len(lines) else total
        end = min(nxt, t + max_line)
        end = max(end, t + 0.8)
        span = max(end - t - 0.1, 0.5)
        weights = [len(w) + 1 for w in toks]
        acc = 0.0
        for w, wt in zip(toks, weights):
            s0 = t + span * acc / sum(weights); acc += wt
            e0 = t + span * acc / sum(weights)
            words.append(dict(w=w, s=round(s0, 2), e=round(e0, 2)))
        segs.append(dict(s=float(t), e=round(end, 2), text=" ".join(toks)))
    out = dict(words=words, segments=segs, source="imported")
    Path(video).with_suffix(".words.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    return out


if __name__ == "__main__":
    import sys
    if len(sys.argv) == 4 and sys.argv[1] == "import-transcript":
        r = import_transcript(sys.argv[2], Path(sys.argv[3]).read_text("utf-8"))
        print(f"imported {len(r['segments'])} lines, {len(r['words'])} words -> {Path(sys.argv[2]).with_suffix('.words.json')}")
    else:
        print("usage: python cf.py import-transcript <video.mp4> <transcript.txt>")
