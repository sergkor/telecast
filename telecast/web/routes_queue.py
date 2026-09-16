from datetime import timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import select

from telecast.models import Article, FAILED_STATES, ArticleState, MediaFile, PublishTarget
from telecast.publish import base as registry
from telecast.publish.recalc import effective_targets
from telecast.publish.republish import queue_republish
from telecast.publish.schedule import schedule_pending
from telecast.web import auth
from telecast.web.app import render

router = APIRouter(dependencies=[Depends(auth.require_login)])
action = APIRouter(dependencies=[Depends(auth.require_csrf)])

TABS = {
    "pending": [ArticleState.PENDING_REVIEW],
    "failed": list(FAILED_STATES),
    "published": [ArticleState.PUBLISHED],
    "discarded": [ArticleState.DISCARDED],
}


@router.get("/", response_class=HTMLResponse)
def queue(request: Request, tab: str = "pending"):
    settings = request.app.state.settings
    unconfigured = registry.unconfigured(settings)
    with request.app.state.session_factory() as session:
        stmt = select(Article).order_by(Article.id.desc())
        if tab in TABS:
            stmt = stmt.where(Article.state.in_(TABS[tab]))
        articles = session.exec(stmt).all()
        rows = []
        for a in articles:
            media = session.exec(
                select(MediaFile).where(MediaFile.article_id == a.id)
            ).all()
            targets = effective_targets(
                session.exec(
                    select(PublishTarget).where(PublishTarget.article_id == a.id)
                ).all(),
                unconfigured,
            )
            rows.append({"article": a, "media": media, "targets": targets})
    return render(request, "queue.html", rows=rows, tab=tab,
                  tabs=["pending", "all", "failed", "published", "discarded"],
                  platforms=registry.available(settings))


@action.post("/republish")
def republish(request: Request, article_ids: list[int] = Form([]),
              platform: str = Form("youtube")):
    settings = request.app.state.settings
    if platform not in registry.available(settings):
        raise HTTPException(400, f"unknown or unconfigured platform {platform}")
    if article_ids:
        with request.app.state.session_factory() as session:
            queued = queue_republish(session, article_ids, platform)
            if queued:
                schedule_pending(
                    session,
                    delay=timedelta(minutes=settings.publish_delay_minutes),
                    interval=timedelta(hours=settings.publish_interval_hours),
                    unconfigured=registry.unconfigured(settings),
                )
    return RedirectResponse("/?tab=published", status_code=303)


router.include_router(action)
