import json

import pytest

from src.live import importer, manager


@pytest.fixture()
def live_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LIVE_CLIPPING", "true")
    return tmp_path


def _clip(live_dir, rel="2026-11-16/chan_01", **meta):
    d = live_dir / "clips" / rel
    d.mkdir(parents=True)
    base = dict(status="needs_review", hook="OLD HOOK", yt_title="Old", clip_start=10, clip_end=48, top_chat=["W", "LOL"])
    base.update(meta)
    (d / "meta.json").write_text(json.dumps(base), "utf-8")
    return d


def test_enabled_flag(monkeypatch):
    monkeypatch.setenv("LIVE_CLIPPING", "false")
    assert not manager.enabled()
    monkeypatch.setenv("LIVE_CLIPPING", "true")
    assert manager.enabled()


def test_settings_roundtrip_and_clamp(live_dir):
    s = manager.save_settings({"renderers": 9, "max_height": 1080, "unknown": 1})
    assert s["renderers"] == 4 and s["max_height"] == 1080 and "unknown" not in s
    assert manager.settings()["renderers"] == 4


def test_clip_path_rejects_traversal(live_dir):
    _clip(live_dir)
    assert manager.clip_path("2026-11-16/chan_01").name == "chan_01"
    with pytest.raises(FileNotFoundError):
        manager.clip_path("../../etc")


def test_new_hook_requeues_render(live_dir):
    d = _clip(live_dir)
    view = manager.update_clip("2026-11-16/chan_01", {"hook": "new hook"})
    meta = json.loads((d / "meta.json").read_text("utf-8"))
    assert meta["hook"] == "NEW HOOK" and meta["status"] == "needs_edit" and view["status"] == "needs_edit"


def test_title_edit_is_locked_without_rerender(live_dir):
    d = _clip(live_dir)
    manager.update_clip("2026-11-16/chan_01", {"yt_title": "Better title"})
    meta = json.loads((d / "meta.json").read_text("utf-8"))
    assert meta["yt_title"] == "Better title" and meta["title_locked"] and meta["status"] == "needs_review"


def test_approve_only_from_review_and_posted_only_after_approval(live_dir):
    _clip(live_dir, status="rendering")
    with pytest.raises(ValueError):
        manager.update_clip("2026-11-16/chan_01", {"status": "approved"})
    _clip(live_dir, rel="2026-11-16/chan_02")
    with pytest.raises(ValueError):
        manager.update_clip("2026-11-16/chan_02", {"status": "posted"})
    manager.update_clip("2026-11-16/chan_02", {"status": "approved"})
    assert manager.update_clip("2026-11-16/chan_02", {"status": "posted"})["status"] == "posted"


def test_publish_external_requires_approval(live_dir):
    _clip(live_dir)
    with pytest.raises(ValueError):
        manager.publish_external("2026-11-16/chan_01", ["youtube"], "private", "youtube-api")


def test_recover_requeues_half_rendered(live_dir):
    d = _clip(live_dir, status="rendering")
    (d / "raw.mp4").write_bytes(b"x")
    manager.recover()
    assert json.loads((d / "meta.json").read_text("utf-8"))["status"] == "needs_edit"


def test_start_session_validates_input(live_dir):
    with pytest.raises(ValueError):
        manager.start_session({"platform": "youtube", "url": "https://example.com"}, "u1")
    with pytest.raises(ValueError):
        manager.start_session({"platform": "twitch", "login": "///"}, "u1")
    with pytest.raises(ValueError):
        manager.start_session({"platform": "nope"}, "u1")


def test_mmss():
    assert importer._mmss(75) == "01:15"
    assert importer._mmss(3725) == "01:02:05"


def test_clean_style_whitelists_values():
    out = manager.clean_style({"color_fx": "red", "captions": "pop", "intro": 0, "label": " *LOCKED IN* ",
                               "voiceover": "yes", "vo_text": "x" * 500, "evil": "rm -rf", "color_fx_bad": 1})
    assert out == {"color_fx": "red", "captions": "pop", "intro": False, "label": "*LOCKED IN*",
                   "voiceover": True, "vo_text": "x" * 200}
    assert manager.clean_style({"color_fx": "purple", "captions": "huge"}) == {}


def test_style_edit_requeues_render(live_dir):
    d = _clip(live_dir)
    view = manager.update_clip("2026-11-16/chan_01", {"style": {"color_fx": "green", "title_bar": False}})
    meta = json.loads((d / "meta.json").read_text("utf-8"))
    assert meta["style"] == {"color_fx": "green", "title_bar": False} and meta["status"] == "needs_edit"
    assert view["style"] == {"color_fx": "green", "title_bar": False}
