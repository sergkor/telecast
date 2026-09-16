from datetime import timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import select

from telecast.models import Article, FAILED_STATES, ArticleState, MediaFile, PublishTarget
from telecast.publish import base as registry
from telecast.publish.republish import queue_republish
from telecast.publish.schedule import reschedule
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
            targets = session.exec(
                select(PublishTarget).where(PublishTarget.article_id == a.id)
            ).all()
            rows.append({"article": a, "media": media, "targets": targets})
    return render(request, "queue.html", rows=rows, tab=tab,
                  tabs=["pending", "all", "failed", "published", "discarded"],
                  platforms=registry.names())


@action.post("/republish")
def republish(request: Request, article_ids: list[int] = Form([]),
              platform: str = Form("youtube")):
    if platform not in registry.names():
        raise HTTPException(400, f"unknown platform {platform}")
    if article_ids:
        with request.app.state.session_factory() as session:
            queued = queue_republish(session, article_ids, platform)
            if queued:
                delay = timedelta(minutes=request.app.state.settings.publish_delay_minutes)
                reschedule(session, delay=delay)
    return RedirectResponse("/?tab=published", status_code=303)


router.include_router(action)
