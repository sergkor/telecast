from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from telecast.publish import base as registry
from telecast.publish.recalc import recompute_all
from telecast.web import auth
from telecast.web.app import render

router = APIRouter(dependencies=[Depends(auth.require_login)])
action = APIRouter(dependencies=[Depends(auth.require_csrf)])


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, recalculated: str | None = None):
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
    return render(request, "settings.html",
                  source_channels=s.source_channel_list,
                  dest_channel=s.dest_channel, prompt=prompt, health=health,
                  plugins=plugins, recalculated=recalculated)


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


router.include_router(action)
