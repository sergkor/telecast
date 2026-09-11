from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from telecast.web import auth

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def render(request: Request, name: str, **ctx) -> HTMLResponse:
    ctx.setdefault("csrf", request.cookies.get(auth.CSRF_COOKIE, ""))
    return templates.TemplateResponse(request, name, ctx)


def create_app(settings, session_factory, llm=None) -> FastAPI:
    app = FastAPI(title="Telecast")
    app.state.settings = settings
    app.state.session_factory = session_factory
    app.state.llm = llm

    settings.media_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/media", StaticFiles(directory=str(settings.media_dir)), name="media")

    @app.exception_handler(HTTPException)
    async def _http_exc(request: Request, exc: HTTPException):
        if exc.status_code == 303 and exc.headers and "Location" in exc.headers:
            return RedirectResponse(exc.headers["Location"], status_code=303)
        return await http_exception_handler(request, exc)

    @app.get("/login", response_class=HTMLResponse)
    def login_page(request: Request):
        return render(request, "login.html", error=None)

    @app.post("/login")
    def login(request: Request, password: str = Form(...)):
        if password != settings.web_password:
            return render(request, "login.html", error="Invalid password")
        response = RedirectResponse("/", status_code=303)
        auth.login_user(response, settings)
        return response

    @app.post("/logout")
    async def logout(request: Request):
        await auth.require_csrf(request)
        response = RedirectResponse("/login", status_code=303)
        auth.logout_user(response)
        return response

    from telecast.web.routes_queue import router as queue_router
    app.include_router(queue_router)

    return app
