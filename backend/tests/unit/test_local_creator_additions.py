"""Imported transcripts, chat-replay hype signals, local B-roll and the yt-dlp JS runtime default."""

import json
import random
from pathlib import Path

from src import chat_signals, local_broll
from src.media import transcript_import as ti


def test_gemini_lines_become_whisper_shape(tmp_path):
    text = "[00:00] यू, यू, वी आर बैक।\n[00:04] second line [संगीत] here\ncontinued\n[1:02:03] after an hour\n"
    result = ti.to_whisper_result(ti.parse_timed_text(text), total_seconds=3800)
    segs = result["segments"]
    assert [round(s["start"]) for s in segs] == [0, 4, 3723]
    assert "[" not in segs[1]["text"] and "continued" in segs[1]["text"]
    words = segs[0]["words"]
    assert words[0]["start"] == 0.0 and all(w["end"] > w["start"] for w in words)
    assert words[-1]["end"] <= 4.0  # spread inside the line, never past the next one


def test_srt_and_lookup_by_video_id(tmp_path, monkeypatch):
    (tmp_path / "dQw4w9WgXcQ.srt").write_text("1\n00:00:01,000 --> 00:00:03,500\nhello there\n", "utf-8")
    monkeypatch.setenv("TRANSCRIPT_IMPORT_DIR", str(tmp_path))
    found = ti.find_imported_transcript(Path("x/full-1080.mp4"), "https://youtu.be/dQw4w9WgXcQ?si=abc")
    assert found and found.suffix == ".srt"
    seg = ti.load_imported_transcript(found)["segments"][0]
    assert seg["start"] == 1.0 and seg["end"] == 3.5
    assert ti.find_imported_transcript(Path("other.mp4"), None) is None


def test_chat_spike_found_and_promos_ignored():
    rng = random.Random(1)
    msgs = []
    for s in range(900):
        n, pool = rng.randint(1, 3), ["nice", "hello", "gg", "where is he", "cool"]
        if 600 <= s < 606:
            n, pool = 14, ["LMAOOO", "NO WAY", "CLIP IT", "W", "bro 😂"]
        if s == 300:
            n, pool = 40, ["NEW MERCH at https://shop.example.com 🔥"]
        msgs += [(s + rng.random(), rng.choice(pool)) for _ in range(n)]
    spikes = chat_signals.find_spikes(sorted(msgs))
    assert len(spikes) == 1 and abs(spikes[0]["second"] - 600) <= 2  # 5 s window: +-2 s
    assert len(set(spikes[0]["chat"])) == len(spikes[0]["chat"])


def test_hype_block_off_unless_enabled(monkeypatch):
    monkeypatch.delenv("CHAT_SIGNALS", raising=False)
    assert chat_signals.hype_block("https://youtu.be/dQw4w9WgXcQ") == ""


def test_local_broll_matches_catalog_tags(tmp_path, monkeypatch):
    d = tmp_path / "stock"
    d.mkdir()
    (d / "pmstock_gym_1.mp4").write_bytes(b"x")
    (d / "random_9.mp4").write_bytes(b"x")
    catalog = tmp_path / "cat.json"
    catalog.write_text(json.dumps([{"path": str(d / "random_9.mp4"), "tags": ["money", "cash"], "description": ""}]), "utf-8")
    monkeypatch.setenv("LOCAL_BROLL_DIRS", str(d))
    monkeypatch.setenv("LOCAL_BROLL_CATALOG", str(catalog))
    monkeypatch.setattr(local_broll, "_probe", lambda p: {"width": 1080, "height": 1920, "duration": 10.0})
    local_broll._catalog_cached.cache_clear()
    gym = local_broll.find(["man lifting weights at the gym"], 2.0, set())
    cash = local_broll.find(["counting cash"], 2.0, set())
    assert Path(gym["local_path"]).name == "pmstock_gym_1.mp4" and gym["provider"] == "local"
    assert Path(cash["local_path"]).name == "random_9.mp4" and cash["pexels_id"] < 0
    assert local_broll.find(["unicorn"], 2.0, set()) is None
    assert local_broll.find(["counting cash"], 2.0, {cash["pexels_id"]}) is None


def test_ytdlp_gets_a_js_runtime(monkeypatch):
    import yt_dlp
    from src import ytdlp_runtime

    assert getattr(yt_dlp.YoutubeDL, "_supoclip_js_runtime", None) is not None  # installed by src.config
    runtime = yt_dlp.YoutubeDL._supoclip_js_runtime
    if runtime:
        assert yt_dlp.YoutubeDL({"quiet": True}).params["js_runtimes"] == {runtime: {}}
        explicit = yt_dlp.YoutubeDL({"quiet": True, "js_runtimes": {"deno": {}}})
        assert explicit.params["js_runtimes"] == {"deno": {}}
    ytdlp_runtime.install()  # idempotent
