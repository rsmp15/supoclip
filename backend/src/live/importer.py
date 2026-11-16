"""Register rendered live clips as ordinary SupoClip tasks/clips (one task per stream per day).

That puts them in the Library with SupoClip's player, downloads/exports and social publishing (YouTube, TikTok,
Instagram). The live clip folder keeps its own final.mp4; SupoClip gets a copy (persist_clip moves the file it is
given into media storage). A re-rendered clip (new hook) is upserted into the same clip slot.
"""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
import uuid
from pathlib import Path
from typing import Any, Dict

from . import manager
from .engine import social_copy

logger = logging.getLogger(__name__)
IMPORTABLE = {"needs_review", "approved", "posted"}


def _int(v) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def _mmss(sec: float) -> str:
    sec = max(0, int(sec))
    return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}" if sec >= 3600 else f"{sec // 60:02d}:{sec % 60:02d}"


def _task_map_path() -> Path:
    return manager.data_dir() / "supoclip_tasks.json"


def _load_map() -> Dict[str, Any]:
    try:
        return json.loads(_task_map_path().read_text("utf-8"))
    except (OSError, ValueError):
        return {}


async def _import_one(d: Path, meta: Dict[str, Any], tasks: Dict[str, Any]) -> None:
    from ..database import AsyncSessionLocal
    from ..repositories.clip_repository import ClipRepository
    from ..repositories.source_repository import SourceRepository
    from ..repositories.task_repository import TaskRepository

    user_id = manager.owner_for(meta.get("source_url", ""))
    if not user_id:
        return                                              # nobody started a session in this API process yet
    final = d / "final.mp4"
    day = d.parent.name
    start, end = float(meta.get("clip_start") or 0), float(meta.get("clip_end") or 0)
    key = f"{user_id}|{meta.get('source_url')}|{day}"
    copy_dir = manager.data_dir() / "import"; copy_dir.mkdir(exist_ok=True)
    copy = copy_dir / f"{uuid.uuid4().hex}.mp4"
    shutil.copy2(final, copy)
    async with AsyncSessionLocal() as db:
        entry = tasks.get(key)
        if entry:
            task = await TaskRepository.get_task_by_id(db, entry["task_id"])
            if not task:
                entry = None                                # task was deleted in the Library: start a new one
        if not entry:
            platform = meta.get("platform") or "youtube"
            title = f"LIVE · {meta.get('channel') or 'stream'} · {day}"
            source_id = await SourceRepository.create_source(
                db, "twitch" if platform == "twitch" else "youtube", title[:500], meta.get("source_url"))
            task_id = await TaskRepository.create_task(db, user_id, source_id, status="completed",
                                                       processing_mode="fast", charge_minutes=False,
                                                       source_duration_seconds=max(1, round(end - start)))
            await db.commit()
            entry = tasks[key] = {"task_id": task_id, "next_order": 0}
        sc = meta.get("supoclip") or {}
        order = sc.get("clip_order") if sc.get("task_id") == entry["task_id"] else None
        if order is None:
            order = entry["next_order"]; entry["next_order"] += 1
        chat = " | ".join(meta.get("top_chat", [])[:4])
        clip_id = await ClipRepository.create_clip(
            db, entry["task_id"], filename=f"{d.name}.mp4", file_path=str(copy), start_time=_mmss(start),
            end_time=_mmss(end), duration=round(end - start, 2), text=meta.get("transcript") or chat,
            relevance_score=min(1.0, float(meta.get("ratio") or 0) / 5), clip_order=order,
            reasoning=f"Live chat spike x{meta.get('ratio')} vs the stream's baseline. Chat: {chat}"[:1000],
            virality_score=int(social_copy.virality(meta)["score"]), hook_score=_int(meta.get("hook_score")),
            hook_type="reaction",
            hook_title=meta.get("hook") or None, workflow_meta={"live_clip": d.relative_to(manager.clips_dir()).as_posix()})
        await db.commit()
    meta = manager.read_meta(d) or meta
    meta["supoclip"] = {"task_id": entry["task_id"], "clip_id": clip_id, "clip_order": order,
                        "version": int(final.stat().st_mtime)}
    manager.write_meta(d, meta)
    _task_map_path().write_text(json.dumps(tasks, indent=1), "utf-8")
    logger.info("live clip %s imported into task %s", d.name, entry["task_id"])


async def import_pending() -> int:
    tasks, n = _load_map(), 0
    for d in manager.clip_dirs():
        meta = manager.read_meta(d)
        final = d / "final.mp4"
        if not meta or meta.get("status") not in IMPORTABLE or not final.exists():
            continue
        sc = meta.get("supoclip") or {}
        version = int(final.stat().st_mtime)
        if sc.get("version") == version or (sc.get("failed_version") == version and sc.get("attempts", 0) >= 3):
            continue
        try:
            await _import_one(d, meta, tasks); n += 1
        except Exception as exc:
            logger.exception("live clip import failed for %s", d)
            meta = manager.read_meta(d) or meta
            sc = meta.get("supoclip") or {}
            attempts = sc.get("attempts", 0) + 1 if sc.get("failed_version") == version else 1
            meta["supoclip"] = {**sc, "failed_version": version, "attempts": attempts, "error": str(exc)[:300]}
            manager.write_meta(d, meta)
    return n


async def import_loop() -> None:
    while True:
        try:
            await import_pending()
        except Exception:
            logger.exception("live import loop error")
        await asyncio.sleep(8)
