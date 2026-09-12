from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from telecast.web import auth
from telecast.web.app import render

router = APIRouter(dependencies=[Depends(auth.require_login)])


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
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
    return render(request, "settings.html",
                  source_channels=s.source_channel_list,
                  dest_channel=s.dest_channel, prompt=prompt, health=health)
