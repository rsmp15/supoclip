"""Text-to-speech for the optional context voiceover ("Speed was playing GTA 6 when this happened").

synth(text, out_wav) -> Path or None. Backends, best first, all free:
  1. Kokoro (pip install kokoro soundfile)          - natural local voice, offline
  2. edge-tts CLI (pip install edge-tts)            - natural Microsoft neural voices, needs internet
  3. Windows built-in speech (System.Speech / SAPI) - offline, always on Windows, robotic
The text is passed through a file, never on a command line.
"""
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

VOICE = os.environ.get("CLIPFORGE_VOICE", "")          # e.g. am_adam (Kokoro) / en-US-GuyNeural (edge-tts)


def _kokoro(text, out):
    from kokoro import KPipeline                     # noqa: imported lazily, optional
    import numpy as np
    import soundfile as sf
    pipe = KPipeline(lang_code="a")
    audio = [a for _, _, a in pipe(text, voice=VOICE or "am_adam")]
    sf.write(str(out), np.concatenate(audio), 24000)


def _edge(text, out):
    exe = shutil.which("edge-tts")
    if not exe:
        raise RuntimeError("edge-tts not installed")
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write(text)
    mp3 = Path(out).with_suffix(".mp3")
    try:
        subprocess.run([exe, "-f", f.name, "--voice", VOICE or "en-US-GuyNeural", "--write-media", str(mp3)],
                       check=True, capture_output=True, timeout=60)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(mp3), "-ar", "44100", str(out)], check=True, timeout=60)
    finally:
        os.unlink(f.name); mp3.unlink(missing_ok=True)


def _sapi(text, out):
    if os.name != "nt":
        raise RuntimeError("Windows speech only on Windows")
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write(text)
    ps = ("Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
          "$v = $s.GetInstalledVoices() | Where-Object { $_.VoiceInfo.Gender -eq 'Male' } | Select-Object -First 1; "
          "if ($v) { $s.SelectVoice($v.VoiceInfo.Name) }; $s.Rate = 1; "
          "$s.SetOutputToWaveFile($env:CF_OUT); $s.Speak([IO.File]::ReadAllText($env:CF_TXT)); $s.Dispose()")
    try:
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], check=True, capture_output=True,
                       timeout=60, env=dict(os.environ, CF_OUT=str(out), CF_TXT=f.name))
    finally:
        os.unlink(f.name)


def synth(text, out_wav):
    out = Path(out_wav)
    for fn in (_kokoro, _edge, _sapi):
        try:
            fn(text, out)
            if out.exists() and out.stat().st_size > 1000:
                return out
        except Exception:
            continue
    return None
