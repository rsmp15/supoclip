"""Publish an APPROVED clip to YouTube Shorts and/or Instagram Reels.

  python publish.py <clip dir> --to youtube,instagram [--privacy public] [--via postiz|youtube-api] [--dry-run]

Backends
  postiz       Postiz CLI (`npm i -g postiz`, `postiz auth:login`). Needs an active Postiz plan (cloud) or a self-hosted
               Postiz, with your YouTube and Instagram (Business/Creator) accounts connected in Postiz.
               Integration ids are auto-discovered, or set them in secrets/postiz.json {"youtube": "..", "instagram": ".."}.
  youtube-api  Free direct upload with the official YouTube Data API (OAuth client in secrets/client_secret.json,
               see ANTIGRAVITY.md). YouTube only.

Rules: refuses unless meta.json status == "approved"; never posts the same clip to the same platform twice;
results are written to meta.json ("published": {platform: {...}}); status becomes "posted" when every requested
platform succeeded.
"""
import argparse, datetime as dt, json, shutil, subprocess, sys
from pathlib import Path
import cf

SECRETS = cf.ROOT / "secrets"
for _s in (sys.stdout, sys.stderr):
    _s.reconfigure(encoding="utf-8", errors="replace")


class PublishError(Exception):
    pass


def _postiz(*args, timeout=600):
    exe = shutil.which("postiz") or shutil.which("postiz.cmd")
    if not exe:
        raise PublishError("Postiz CLI not installed: npm install -g postiz, then postiz auth:login")
    r = subprocess.run([exe, *args], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    out = (r.stdout or "") + (r.stderr or "")
    if r.returncode != 0 or "❌" in out:
        raise PublishError(out.strip()[-600:] or f"postiz {args[0]} failed")
    return out


def _json_tail(text):
    """Postiz prints a header line before the JSON."""
    for start in ("[", "{"):
        i = text.find(start)
        if i >= 0:
            try:
                return json.loads(text[i:])
            except ValueError:
                continue
    raise PublishError(f"unexpected Postiz output: {text[:300]}")


def postiz_integrations():
    cfg = SECRETS / "postiz.json"
    if cfg.exists():
        return json.loads(cfg.read_text("utf-8"))
    found = {}
    for it in _json_tail(_postiz("integrations:list")):
        if it.get("disabled"):
            continue
        ident = (it.get("identifier") or "").lower()
        if ident == "youtube":
            found.setdefault("youtube", it["id"])
        elif ident.startswith("instagram"):
            found.setdefault("instagram", it["id"])
    return found


def _check_publishable(meta, platforms):
    if meta.get("status") not in ("approved", "posted"):
        raise PublishError(f"status is '{meta.get('status')}', not 'approved' - review and approve the clip first")
    done = [p for p in platforms if (meta.get("published") or {}).get(p, {}).get("ok")]
    if done:
        raise PublishError(f"already published to {', '.join(done)}")


def _texts(meta):
    title = (meta.get("yt_title") or meta.get("hook") or "").strip()
    if "#shorts" not in title.lower() and len(title) + 8 <= 100:
        title += " #shorts"
    desc = meta.get("description", "")
    caption = desc[:2100]                                  # Instagram caption limit is 2,200 chars
    return title[:100], desc[:5000], caption


def publish_postiz(clip, meta, platforms, privacy, dry_run=False, log=print):
    ids = postiz_integrations()
    missing = [p for p in platforms if p not in ids]
    if missing:
        raise PublishError(f"no Postiz channel connected for: {', '.join(missing)} (connect it in Postiz, or set "
                           "secrets/postiz.json)")
    title, desc, caption = _texts(meta)
    for p in platforms:                                    # Rule: read each channel's settings rules before posting
        rules = _json_tail(_postiz("integrations:settings", ids[p])).get("output", {}).get("rules")
        if rules:
            log(f"postiz {p} rules: {str(rules)[:300]}")
    plan = {"youtube": dict(title=title, type=privacy,
                            tags=[dict(value=t, label=t) for t in meta.get("tags", [])[:15]]),
            "instagram": dict(post_type="post")}           # a video "post" publishes as a Reel
    if dry_run:
        return {p: dict(ok=False, dry_run=True, integration=ids[p], settings=plan[p]) for p in platforms}
    log("uploading video to Postiz...")
    media = _json_tail(_postiz("upload", str(clip / "final.mp4"), timeout=1800))["path"]
    when = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    res = {}
    for p in platforms:
        out = _postiz("posts:create", "-c", desc if p == "youtube" else caption, "-m", media, "-s", when,
                      "-i", ids[p], "--settings", json.dumps(plan[p], ensure_ascii=False))
        res[p] = dict(ok=True, via="postiz", integration=ids[p], scheduled_for=when, response=out.strip()[-400:])
        log(f"{p}: queued in Postiz for {when}")
    return res


def publish_youtube_api(clip, meta, privacy, dry_run=False, log=print):
    title, desc, _ = _texts(meta)
    body = dict(snippet=dict(title=title, description=desc, tags=meta.get("tags", [])[:30], categoryId="20"),
                status=dict(privacyStatus=privacy, selfDeclaredMadeForKids=False, containsSyntheticMedia=False))
    if dry_run:
        return {"youtube": dict(ok=False, dry_run=True, body=body)}
    from googleapiclient.http import MediaFileUpload
    import twitchclip
    req = twitchclip.youtube_client().videos().insert(part="snippet,status", body=body, media_body=MediaFileUpload(
        str(clip / "final.mp4"), mimetype="video/mp4", chunksize=8 * 1024 * 1024, resumable=True))
    resp = None
    while resp is None:
        st, resp = req.next_chunk()
        if st:
            log(f"youtube upload {int(st.progress() * 100)}%")
    vid = resp["id"]
    return {"youtube": dict(ok=True, via="youtube-api", id=vid, url=f"https://www.youtube.com/shorts/{vid}",
                            privacy=privacy)}


def publish(clip, platforms, privacy="public", via="postiz", dry_run=False, log=print):
    clip = Path(clip); mf = clip / "meta.json"
    meta = json.loads(mf.read_text("utf-8"))
    platforms = [p.strip().lower() for p in platforms if p.strip()]
    _check_publishable(meta, platforms)
    if not (clip / "final.mp4").exists():
        raise PublishError("final.mp4 missing - render the clip first")
    if via == "youtube-api":
        if platforms != ["youtube"]:
            raise PublishError("the free YouTube API backend posts to YouTube only; use Postiz for Instagram")
        res = publish_youtube_api(clip, meta, privacy, dry_run, log)
    else:
        res = publish_postiz(clip, meta, platforms, privacy, dry_run, log)
    if not dry_run:
        meta = json.loads(mf.read_text("utf-8"))
        meta.setdefault("published", {}).update(res)
        meta["published_at"] = dt.datetime.now().isoformat(timespec="seconds")
        if all(meta["published"].get(p, {}).get("ok") for p in platforms):
            meta["status"] = "posted"
        mf.write_text(json.dumps(meta, indent=1, ensure_ascii=False), "utf-8")
    return res


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("clip")
    ap.add_argument("--to", default="youtube,instagram")
    ap.add_argument("--privacy", choices=["private", "unlisted", "public"], default="public")
    ap.add_argument("--via", choices=["postiz", "youtube-api"], default="postiz")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--yes", action="store_true", help="skip the typed confirmation")
    a = ap.parse_args()
    if not a.dry_run and not a.yes and input(f"Publish {a.clip} to {a.to} ({a.privacy}) via {a.via}? type 'yes': ").strip().lower() != "yes":
        sys.exit("cancelled")
    try:
        print(json.dumps(publish(a.clip, a.to.split(","), a.privacy, a.via, a.dry_run), indent=1, ensure_ascii=False))
    except PublishError as e:
        sys.exit(f"not published: {e}")
