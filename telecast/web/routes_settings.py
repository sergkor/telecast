from datetime import timedelta
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from telecast.publish import base as registry
from telecast.publish.recalc import recompute_all
from telecast.publish.schedule import reset_schedule
from telecast.web import auth
from telecast.web.app import render

router = APIRouter(dependencies=[Depends(auth.require_login)])
action = APIRouter(dependencies=[Depends(auth.require_csrf)])


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, recalculated: str | None = None,
                  checked: str | None = None, rescheduled: str | None = None):
    s = request.app.state.settings
    try:
        prompt = s.enhance_prompt_path.read_text(encoding="utf-8")
    except OSError:
        prompt = "(prompt file not found)"
    health = {
        "Telethon session": "OK" if s.telethon_session_path.exists() else "missing — run `telecast auth telegram`",
        "YouTube token": "OK" if s.youtube_token_path.exists() else "missing — run `telecast auth youtube`",
        "Bot token": "set" if s.bot_token else "missing",
        "Gemini API key": "set" if s.gemini_api_key else "missing",
    }
    available = set(registry.available(s))
    plugins = {
        name: "configured" if name in available else "not configured"
        for name in registry.names()
    }
    checkable = {n for n in registry.names()
                 if hasattr(registry.get(n), "check_connection")}
    return render(request, "settings.html",
                  source_channels=s.source_channel_list,
                  dest_channel=s.dest_channel, prompt=prompt, health=health,
                  plugins=plugins, checkable=checkable,
                  recalculated=recalculated, checked=checked,
                  publish_delay_minutes=s.publish_delay_minutes,
                  publish_interval_hours=s.publish_interval_hours,
                  rescheduled=rescheduled)


@action.post("/settings/recalculate")
def recalculate(request: Request):
    """Re-derive article state against the plugins configured right now —
    the way to unblock articles waiting on a plugin that never had, or no
    longer has, credentials."""
    settings = request.app.state.settings
    with request.app.state.session_factory() as session:
        result = recompute_all(session, settings)
    summary = (f"{result['articles_published']} article(s) published, "
               f"{result['targets_added']} target(s) added")
    return RedirectResponse(f"/settings?recalculated={summary}", status_code=303)


@action.post("/settings/validate/{plugin}")
def validate_plugin(request: Request, plugin: str):
    """Ask a plugin to talk to its platform — credentials that merely exist
    are not credentials that work, and the answer belongs here rather than in
    a failed publish hours later. Sync on purpose: FastAPI runs it in a
    worker thread, so the network call does not block the event loop."""
    try:
        publisher = registry.get(plugin)
    except KeyError:
        message = "unknown plugin"
    else:
        check = getattr(publisher, "check_connection", None)
        message = (check(request.app.state.settings).message if check
                   else "no connection check available")
    return RedirectResponse(f"/settings?checked={quote(f'{plugin}: {message}')}",
                            status_code=303)


@action.post("/settings/reset-schedule")
def reset_publish_schedule(request: Request):
    """Lay the publish queue out again from scratch, oldest article first —
    for when approvals came in an order that no longer reflects how the
    articles should go out."""
    settings = request.app.state.settings
    with request.app.state.session_factory() as session:
        moved = reset_schedule(
            session,
            delay=timedelta(minutes=settings.publish_delay_minutes),
            interval=timedelta(hours=settings.publish_interval_hours),
            unconfigured=registry.unconfigured(settings),
        )
    return RedirectResponse(
        f"/settings?rescheduled={quote(f'{moved} article(s) rescheduled')}",
        status_code=303)


router.include_router(action)
