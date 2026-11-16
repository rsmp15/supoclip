from src.live.engine import social_copy

META = dict(channel="caseoh_", login="caseoh_", platform="twitch", game_label="Fall Guys", hype_kind="laugh",
            hook="CHAT COULD NOT HANDLE THIS", yt_title="Chat Lost It Over This Fall Guys Moment",
            source_url="https://www.twitch.tv/caseoh_", clip_start=100, clip_end=138, ratio=3.4, hook_score=60,
            layout="facecam", transcript="Wait what. No no no! Bro how did that happen? I just died!",
            top_chat=["LUL LUL LUL LUL LUL", "LMAOOO"], chat=["LUL LUL", "LMAOOO", "W", "hello"])


def test_platform_copy_respects_limits_and_credit():
    c = social_copy.generate(META)
    assert len(c["youtube"]["title"]) <= 100 and "caseoh_" in c["youtube"]["description"]
    assert "#shorts" in c["youtube"]["description"] and "https://www.twitch.tv/caseoh_" in c["youtube"]["description"]
    assert len(c["instagram"]["caption"]) <= 2200 and "#reels" in c["instagram"]["caption"]
    assert c["tiktok"]["caption"].count("#fyp") == 1 and "(cr: caseoh_)" in c["tiktok"]["caption"]
    assert "#FallGuys" in c["instagram"]["caption"] and "Fall Guys" in c["youtube"]["tags"]


def test_quote_prefers_speech_then_collapses_chat_spam():
    assert social_copy.best_quote(META) in META["transcript"]
    assert social_copy.best_quote({**META, "transcript": ""}) == "chat: LUL LUL LUL"


def test_virality_breakdown_sums_and_grades():
    v = social_copy.virality(META)
    assert v["score"] == sum(p["points"] for p in v["parts"]) and 0 <= v["score"] <= 100
    assert v["grade"] in "ABCD" and {p["label"] for p in v["parts"]} >= {"Chat spike", "Length", "Hook"}
    weak = social_copy.virality({**META, "ratio": 2.0, "transcript": "", "layout": "gameplay", "clip_end": 170})
    assert weak["score"] < v["score"]


def test_ensure_keeps_user_edits():
    meta = dict(META)
    social_copy.ensure(meta)
    meta["social_copy"]["tiktok"]["caption"] = "mine"
    meta["social_copy_edited"] = ["tiktok"]
    social_copy.ensure(meta)
    assert meta["social_copy"]["tiktok"]["caption"] == "mine"
    meta["social_copy_edited"] = []
    social_copy.ensure(meta)
    assert meta["social_copy"]["tiktok"]["caption"] != "mine"
