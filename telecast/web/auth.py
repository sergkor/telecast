import secrets

from fastapi import HTTPException, Request
from fastapi.responses import Response
from itsdangerous import BadSignature, TimestampSigner

SESSION_COOKIE = "telecast_session"
CSRF_COOKIE = "telecast_csrf"
MAX_AGE = 30 * 24 * 3600


def _signer(secret: str) -> TimestampSigner:
    return TimestampSigner(secret)


def login_user(response: Response, settings) -> None:
    signer = _signer(settings.secret_key)
    response.set_cookie(SESSION_COOKIE, signer.sign(b"ok").decode(),
                        httponly=True, max_age=MAX_AGE, samesite="lax")
    csrf = secrets.token_urlsafe(24)
    response.set_cookie(CSRF_COOKIE, csrf, httponly=False, max_age=MAX_AGE, samesite="lax")


def logout_user(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE)
    response.delete_cookie(CSRF_COOKIE)


def is_logged_in(request: Request) -> bool:
    raw = request.cookies.get(SESSION_COOKIE)
    if not raw:
        return False
    try:
        _signer(request.app.state.settings.secret_key).unsign(raw, max_age=MAX_AGE)
        return True
    except BadSignature:
        return False


def require_login(request: Request) -> None:
    if not is_logged_in(request):
        raise HTTPException(status_code=303, headers={"Location": "/login"})


async def require_csrf(request: Request) -> None:
    require_login(request)
    form = await request.form()
    if form.get("csrf") != request.cookies.get(CSRF_COOKIE):
        raise HTTPException(status_code=403, detail="bad csrf token")
