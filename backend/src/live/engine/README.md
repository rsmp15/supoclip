# Live clipping engine (vendored)

Same code as `rsmp15/live-hype-clipper` (`clipforge/`): `livehype.py` (YouTube live + shared detector, cut and edit),
`twitchclip.py` (Twitch), `publish.py` (Postiz / YouTube API), `cf.py` (helpers). Fix bugs there and copy here
(or the other way round) so both stay identical.

These scripts are run as **subprocesses** by `src/live/manager.py` with `LIVE_PYTHON` (a Python that has
numpy, opencv-python, pillow, faster-whisper or transformers+torch, streamlink, requests), not imported by the
API. Paths come from the environment the manager sets:

| Variable | Meaning |
|---|---|
| `CLIPFORGE_DATA` | where `clips/`, `livebuf/`, `work/` go (default `TEMP_DIR/live`) |
| `CLIPFORGE_WS` | folder that contains `Claude Edit Asset/` (SFX, white flashes); without it SFX are synthesized |
| `CLIPFORGE_FONT_DIR` | folder with `InterDisplay-Black.ttf` (caption font) |
