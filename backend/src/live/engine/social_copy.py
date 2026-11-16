"""Per-platform post copy + a virality score for a clip's meta.json (pure Python, no API, $0).

  generate(meta)  -> {"youtube": {title, description, tags}, "instagram": {caption}, "tiktok": {caption}}
  virality(meta)  -> {"score": 0-100, "grade": "A".."D", "parts": [{label, points, max, note}]}

Copy follows each platform's norms:
- YouTube Shorts: a keyword title (max 100 chars), a description that credits the streamer and links the stream,
  3 hashtags, search tags.
- Instagram Reels: a hook line, the best quote, credit, a follow CTA and 8-12 hashtags at the end
  (max 2,200 chars).
- TikTok: a short, casual caption (under ~150 chars before hashtags), #fyp + 4-5 niche tags and credit.
The score is a transparent heuristic from the live-chat signal, not a prediction: how hard chat spiked, how
pure the reaction was, the hook, the length sweet spot, speech for captions, and a visible face/reaction.
"""
import hashlib
import re

HYPE = re.compile(r"lmao|lmfao|\blol+\b|haha|😂|🤣|💀|😭|🔥|🤯|😳|\bomg+\b|\bwtf\b|no ?way|insane|crazy|holy|\bw+\b|"
                  r"lets? ?go+|kekw|omegalul|\blulw?\b|icant|pog|monka|clip|sheesh|bro+|what+|\?\?\?|!!!", re.I)
EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿️]")
GENERIC = {"", "gaming", "live", "just chatting", "games + demos"}
KIND_EMOJI = {"laugh": "😭", "shock": "😳", "hype": "🔥"}
LINES = {
    "laugh": ["I can't breathe 😭", "chat was NOT ready for this 💀", "the laugh at the end 😭😭", "bro lost it 💀"],
    "shock": ["wait for it… 😳", "nobody saw this coming 😳", "chat went silent then EXPLODED 🤯", "no way this happened 😳"],
    "hype": ["chat went crazy for this 🔥", "this is why we watch live 🔥", "the clip of the stream 🔥", "W moment 🔥"],
}


def _pick(options, seed):
    return options[int(hashlib.md5(seed.encode()).hexdigest(), 16) % len(options)]


def _tag(s):
    return re.sub(r"[^A-Za-z0-9]", "", s or "")


def _clean(s):
    return re.sub(r"\s+", " ", EMOJI.sub("", s or "")).strip()


def best_quote(meta, max_len=90):
    """The punchiest short sentence from the transcript, else the loudest chat message."""
    text = (meta.get("transcript") or "").strip()
    sents = [s.strip(" -") for s in re.split(r"(?<=[.!?])\s+", text) if 3 <= len(s.split()) <= 16]
    if sents:
        score = lambda s: (s.count("!") * 2 + s.count("?") + len(re.findall(r"\b(no|what|bro|dude|oh|wait|how|why)\b", s, re.I))
                           + (2 if s.isupper() else 0) - len(s) / 120)
        q = max(sents, key=score)
        return q[:max_len].rstrip() + ("…" if len(q) > max_len else "")
    chat = [_clean(c) for c in meta.get("top_chat") or [] if 2 <= len(_clean(c)) <= 60]
    if not chat:
        return ""
    words, out = chat[0].split(), []
    for w in words:                                   # "LUL LUL LUL LUL LUL" -> "LUL LUL LUL"
        if len(out) >= 3 and all(x.lower() == w.lower() for x in out[-3:]):
            continue
        out.append(w)
    return "chat: " + " ".join(out)[:max_len]


def _context(meta):
    who = (meta.get("channel") or meta.get("login") or "the streamer").strip()
    label = meta.get("game_label") or meta.get("game") or ""
    game = "" if label.strip().lower() in GENERIC or label.strip("_ ").lower() == who.strip("_ ").lower() else label
    platform = "Twitch" if meta.get("platform") == "twitch" else "YouTube"
    link = meta.get("vod_url") or meta.get("source_url") or ""
    kind = meta.get("hype_kind") or "hype"
    return who, game, platform, link, kind


def generate(meta):
    who, game, platform, link, kind = _context(meta)
    hook = _clean(meta.get("hook") or "").capitalize()
    quote = best_quote(meta)
    seed = (meta.get("source_url") or "") + str(meta.get("clip_start"))
    emo = KIND_EMOJI.get(kind, "🔥")
    who_t, game_t = _tag(who), _tag(game)
    niche = [t for t in [game_t, who_t] if t]
    credit = f"All credit to {who} - watch them live on {platform}" + (f": {link}" if link else ".")

    # YouTube Shorts
    title = (meta.get("yt_title") or hook or f"{who} moment").strip()
    title = title if len(title) <= 100 else title[:97].rsplit(" ", 1)[0] + "…"
    yt_tags = [t for t in dict.fromkeys([
        game, f"{game} funny moments" if game else "", f"{game} live" if game else "", who, f"{who} clips",
        f"{who} {platform.lower()}", "live stream moments", "gaming", "funny moments", "shorts"]) if t][:15]
    yt_hash = " ".join(f"#{t}" for t in (niche + ["shorts"])[:3])
    yt_desc = "\n".join(x for x in [
        f"{hook}{' ' + emo if hook else ''}",
        (quote if quote.startswith("chat:") else f"\"{quote}\"") if quote else "",
        "",
        f"🎮 Streamer: {who} ({platform})" + (f" · {game}" if game else ""),
        f"📺 Full stream: {link}" if link else "",
        "",
        f"Clipped live the second chat exploded. {credit} If you are the creator and want this removed, contact us.",
        "",
        yt_hash,
    ] if x is not None).strip()

    # Instagram Reels
    ig_tags = [f"#{t}" for t in dict.fromkeys(
        niche + [f"{game_t}clips" if game_t else "", "gaming", "gamingclips", "streamer", "livestream",
                 "funnymoments" if kind == "laugh" else "clutch" if kind == "hype" else "unexpected",
                 "reels", "reelsinstagram", "viralreels", "explorepage"]) if t][:12]
    ig_caption = "\n".join(x for x in [
        f"{_pick(LINES.get(kind, LINES['hype']), seed + 'ig')}",
        ("\n" + (quote if quote.startswith("chat:") else f"\"{quote}\"")) if quote else "",
        f"\n🎮 {who} on {platform}" + (f" | {game}" if game else ""),
        f"Follow for daily {game or 'stream'} clips ➕",
        f"\n🎥 credit: {who} ({platform})",
        "\n" + " ".join(ig_tags),
    ] if x)[:2200]

    # TikTok
    tt_line = _pick(LINES.get(kind, LINES["hype"]), seed + "tt")
    tt_hook = (hook.lower() if hook and len(hook) <= 60 else tt_line)
    tt_tags = " ".join(f"#{t}" for t in dict.fromkeys(["fyp", "foryou"] + [t.lower() for t in niche] + ["gaming", "streamer"]))
    tt_caption = f"{tt_hook} {emo if emo not in tt_hook else ''}".strip()
    if quote and len(tt_caption) + len(quote) < 120:
        tt_caption += f" {quote}" if quote.startswith("chat:") else f' "{quote}"'
    tt_caption = f"{tt_caption} (cr: {who}) {tt_tags}"[:2200]

    return {"youtube": {"title": title, "description": yt_desc[:5000], "tags": yt_tags},
            "instagram": {"caption": ig_caption},
            "tiktok": {"caption": tt_caption}}


def virality(meta):
    parts = []

    def add(label, pts, mx, note):
        parts.append({"label": label, "points": round(max(0, min(mx, pts))), "max": mx, "note": note})

    ratio = float(meta.get("ratio") or 0)
    add("Chat spike", (ratio - 2) * 14, 30, f"{ratio:g}x the stream's normal chat")
    chat = meta.get("chat") or meta.get("top_chat") or []
    share = sum(bool(HYPE.search(c)) for c in chat) / max(len(chat), 1)
    add("Reaction purity", share * 20, 20, f"{share:.0%} of messages were hype/laughs")
    hs = meta.get("hook_score")
    try:
        hs = float(hs)
    except (TypeError, ValueError):
        hs = None
    add("Hook", (hs / 100 * 15) if hs is not None else 8, 15, f"hook score {hs:g}/100" if hs is not None else "hook not scored")
    length = float(meta.get("clip_end") or 0) - float(meta.get("clip_start") or 0)
    pts = 15 if 25 <= length <= 45 else 11 if 20 <= length <= 55 else 6
    add("Length", pts, 15, f"{length:.0f} s (sweet spot 25-45 s)")
    words = len((meta.get("transcript") or "").split())
    add("Speech / captions", min(words / 40, 1) * 10, 10, f"{words} spoken words" if words else "no speech: chat-only reaction")
    layout = meta.get("layout")
    add("Visible reaction", {"facecam": 10, "face": 10, "gameplay": 5}.get(layout, 5), 10,
        {"facecam": "facecam + gameplay", "face": "full face", "gameplay": "gameplay only"}.get(layout, "unknown layout"))
    score = sum(p["points"] for p in parts)
    grade = "A" if score >= 80 else "B" if score >= 65 else "C" if score >= 50 else "D"
    return {"score": score, "grade": grade, "parts": parts}


def ensure(meta):
    """Fill meta['social_copy'] (keeping platforms the user edited) and meta['virality']. Returns True if changed."""
    fresh = generate(meta)
    old = meta.get("social_copy") or {}
    edited = set(meta.get("social_copy_edited") or [])
    copy = {p: (old[p] if p in edited and p in old else fresh[p]) for p in fresh}
    vir = virality(meta)
    changed = copy != old or vir != meta.get("virality")
    meta["social_copy"], meta["virality"] = copy, vir
    return changed
