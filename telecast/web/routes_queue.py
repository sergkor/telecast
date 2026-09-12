from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlmodel import select

from telecast.models import Article, FAILED_STATES, ArticleState, MediaFile, PublishTarget
from telecast.web import auth
from telecast.web.app import render

router = APIRouter(dependencies=[Depends(auth.require_login)])

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
                  tabs=["pending", "all", "failed", "published", "discarded"])
