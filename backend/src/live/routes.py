"""/live/* - live stream clipping (search, sessions, review queue, publish). LIVE_CLIPPING=true to enable."""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from ..auth_headers import get_authenticated_user_id
from ..config import get_config
from ..utils.async_helpers import run_in_thread
from . import manager

router = APIRouter(prefix="/live", tags=["live"])


def _user(request: Request) -> str:
    if not manager.enabled():
        raise HTTPException(status_code=404, detail="Live clipping is not enabled (set LIVE_CLIPPING=true)")
    return get_authenticated_user_id(request, get_config())


async def _body(request: Request) -> Dict[str, Any]:
    try:
        data = await request.json()
    except Exception:
        data = {}
    return data if isinstance(data, dict) else {}


@router.get("/status")
async def status(request: Request):
    _user(request)
    return {"enabled": True, "settings": manager.settings(), "system": manager.system()}


@router.get("/settings")
async def get_settings(request: Request):
    _user(request)
    return manager.settings()


@router.post("/settings")
async def set_settings(request: Request):
    _user(request)
    return manager.save_settings(await _body(request))


@router.get("/search")
async def search(request: Request, q: str = ""):
    _user(request)
    try:
        return {"items": await run_in_thread(manager.search, q)}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Live search failed: {str(exc)[:300]}")


@router.get("/sessions")
async def list_sessions(request: Request):
    _user(request)
    return {"sessions": manager.sessions()}


@router.post("/sessions")
async def start_sessions(request: Request):
    user_id = _user(request)
    items = (await _body(request)).get("items") or []
    if not isinstance(items, list) or not items:
        raise HTTPException(status_code=422, detail="items is required")
    started = []
    for item in items[:30]:
        try:
            started.append((await run_in_thread(manager.start_session, item, user_id)).id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
    return {"started": started}


@router.post("/sessions/stop-all")
async def stop_all(request: Request):
    _user(request)
    await run_in_thread(manager.stop_all)
    return {"ok": True}


@router.post("/sessions/clear")
async def clear(request: Request):
    _user(request)
    manager.clear_finished()
    return {"ok": True}


@router.post("/sessions/{sid}/stop")
async def stop_session(sid: str, request: Request):
    _user(request)
    s = manager.get_session(sid)
    if not s:
        raise HTTPException(status_code=404, detail="Session not found")
    await run_in_thread(s.stop)
    return {"ok": True}


@router.post("/sessions/{sid}/restart")
async def restart_session(sid: str, request: Request):
    _user(request)
    s = manager.get_session(sid)
    if not s:
        raise HTTPException(status_code=404, detail="Session not found")
    await run_in_thread(s.stop)
    s.stopped, s.restarts = False, 0
    await run_in_thread(s.start)
    return {"ok": True}


@router.get("/clips")
async def list_clips(request: Request):
    _user(request)
    return {"clips": [manager.clip_view(d) for d in reversed(manager.clip_dirs())]}


@router.get("/clips/{rel:path}/video")
async def clip_video(rel: str, request: Request):
    _user(request)
    try:
        d = manager.clip_path(rel)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Clip not found")
    final = d / "final.mp4"
    if not final.exists():
        raise HTTPException(status_code=404, detail="Clip not rendered yet")
    return FileResponse(final, media_type="video/mp4", headers={"Cache-Control": "private, no-store"})


@router.post("/clips/{rel:path}/update")
async def update_clip(rel: str, request: Request):
    _user(request)
    try:
        return manager.update_clip(rel, await _body(request))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Clip not found")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.post("/clips/{rel:path}/publish")
async def publish_clip(rel: str, request: Request):
    """Postiz (YouTube + Instagram) or the free YouTube API. SupoClip's own social publishing uses /social/posts
    with the clip's supoclip.task_id / clip_id instead."""
    _user(request)
    body = await _body(request)
    platforms = [p for p in (body.get("platforms") or ["youtube"]) if p in ("youtube", "instagram")]
    via = body.get("via") if body.get("via") in ("postiz", "youtube-api") else "postiz"
    privacy = body.get("privacy") if body.get("privacy") in ("public", "unlisted", "private") else "public"
    if not platforms:
        raise HTTPException(status_code=422, detail="platforms is required")
    try:
        return await run_in_thread(manager.publish_external, rel, platforms, privacy, via)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Clip not found")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)[:800])
