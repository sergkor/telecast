from datetime import timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import select

from telecast.models import Article, ArticleState, MediaFile, PublishTarget, TargetStatus, utcnow
from telecast.publish import base as registry
from telecast.publish.schedule import reschedule
from telecast.web import auth
from telecast.web.app import render

router = APIRouter(dependencies=[Depends(auth.require_login)])
action = APIRouter(dependencies=[Depends(auth.require_csrf)])


# states in which review is done enough that platform targets may be added
ADDABLE_STATES = (ArticleState.PENDING_REVIEW, ArticleState.PUBLISHED)


def _load(request, article_id: int):
    session = request.app.state.session_factory()
    article = session.get(Article, article_id)
    if article is None:
        session.close()
        raise HTTPException(404)
    return session, article


def _detail_ctx(request, session, article):
    media = session.exec(select(MediaFile).where(MediaFile.article_id == article.id)).all()
    targets = session.exec(select(PublishTarget).where(PublishTarget.article_id == article.id)).all()
    warnings = {}
    for t in targets:
        try:
            pub = registry.get(t.platform)
            warnings[t.platform] = pub.validate(article, list(media), request.app.state.settings)
        except KeyError:
            warnings[t.platform] = [f"no publisher registered for {t.platform}"]
    missing = []
    if article.state in ADDABLE_STATES:
        have = {t.platform for t in targets}
        missing = [p for p in registry.names() if p not in have]
    return dict(article=article, media=media, targets=targets, warnings=warnings,
                missing_platforms=missing,
                publishing=any(t.status == TargetStatus.PUBLISHING for t in targets))


@router.get("/articles/{article_id}", response_class=HTMLResponse)
def detail(request: Request, article_id: int):
    session, article = _load(request, article_id)
    try:
        return render(request, "article.html", **_detail_ctx(request, session, article))
    finally:
        session.close()


@router.get("/articles/{article_id}/targets", response_class=HTMLResponse)
def targets_partial(request: Request, article_id: int):
    session, article = _load(request, article_id)
    try:
        return render(request, "_targets.html", **_detail_ctx(request, session, article))
    finally:
        session.close()


@action.post("/articles/{article_id}/save")
def save(request: Request, article_id: int, title: str = Form(""), final_text: str = Form("")):
    session, article = _load(request, article_id)
    try:
        article.title = title
        article.final_text = final_text
        if final_text != (article.enhanced_text or ""):
            article.final_text_edited = True
        article.updated_at = utcnow()
        session.commit()
    finally:
        session.close()
    return RedirectResponse(f"/articles/{article_id}", status_code=303)


@action.post("/articles/{article_id}/reenhance")
def reenhance(request: Request, article_id: int):
    session, article = _load(request, article_id)
    try:
        if article.state in (ArticleState.PENDING_REVIEW, ArticleState.FAILED_ENHANCE):
            article.state = ArticleState.TRANSLATED
            article.updated_at = utcnow()
            session.commit()
    finally:
        session.close()
    return RedirectResponse(f"/articles/{article_id}", status_code=303)


@action.post("/articles/{article_id}/retry")
def retry(request: Request, article_id: int):
    session, article = _load(request, article_id)
    try:
        if article.state == ArticleState.FAILED_TRANSLATE:
            article.state = ArticleState.INGESTED
        elif article.state == ArticleState.FAILED_ENHANCE:
            article.state = ArticleState.TRANSLATED
        article.error = None
        article.updated_at = utcnow()
        session.commit()
    finally:
        session.close()
    return RedirectResponse(f"/articles/{article_id}", status_code=303)


@action.post("/articles/{article_id}/discard")
def discard(request: Request, article_id: int):
    session, article = _load(request, article_id)
    try:
        article.state = ArticleState.DISCARDED
        article.updated_at = utcnow()
        session.commit()
    finally:
        session.close()
    return RedirectResponse("/", status_code=303)


@action.post("/articles/{article_id}/add_target")
def add_target(request: Request, article_id: int, platform: str = Form(...)):
    session, article = _load(request, article_id)
    try:
        if platform not in registry.names():
            raise HTTPException(400, f"unknown platform {platform}")
        if article.state not in ADDABLE_STATES:
            raise HTTPException(400, f"cannot add targets in state {article.state.value}")
        exists = session.exec(
            select(PublishTarget)
            .where(PublishTarget.article_id == article.id)
            .where(PublishTarget.platform == platform)
        ).first()
        if exists:
            raise HTTPException(400, f"{platform} target already exists")
        session.add(PublishTarget(article_id=article.id, platform=platform))
        article.updated_at = utcnow()
        session.commit()
    finally:
        session.close()
    return RedirectResponse(f"/articles/{article_id}", status_code=303)


def _target(request, target_id: int):
    session = request.app.state.session_factory()
    target = session.get(PublishTarget, target_id)
    if target is None:
        session.close()
        raise HTTPException(404)
    return session, target


@action.post("/targets/{target_id}/approve")
def approve(request: Request, target_id: int):
    session, target = _target(request, target_id)
    try:
        if target.status in (TargetStatus.PENDING, TargetStatus.FAILED):
            target.status = TargetStatus.APPROVED
            article = session.get(Article, target.article_id)
            if article.approved_at is None:
                article.approved_at = utcnow()
            session.commit()
            reschedule(session, delay=_delay(request))
        aid = target.article_id
    finally:
        session.close()
    return RedirectResponse(f"/articles/{aid}", status_code=303)


def _delay(request) -> timedelta:
    return timedelta(minutes=request.app.state.settings.publish_delay_minutes)


@action.post("/articles/{article_id}/publish_now")
def publish_now(request: Request, article_id: int):
    session, article = _load(request, article_id)
    try:
        article.scheduled_at = None
        article.updated_at = utcnow()
        session.commit()
    finally:
        session.close()
    return RedirectResponse(f"/articles/{article_id}", status_code=303)


@action.post("/targets/{target_id}/skip")
def skip(request: Request, target_id: int):
    session, target = _target(request, target_id)
    try:
        if target.status in (TargetStatus.PENDING, TargetStatus.FAILED):
            target.status = TargetStatus.SKIPPED
            session.commit()
        aid = target.article_id
    finally:
        session.close()
    return RedirectResponse(f"/articles/{aid}", status_code=303)


@action.post("/targets/{target_id}/republish")
def republish_target(request: Request, target_id: int):
    session, target = _target(request, target_id)
    try:
        if target.status == TargetStatus.PUBLISHED:
            target.status = TargetStatus.APPROVED
            target.error = None
            article = session.get(Article, target.article_id)
            if article.approved_at is None:
                article.approved_at = utcnow()
            session.commit()
            reschedule(session, delay=_delay(request))
        aid = target.article_id
    finally:
        session.close()
    return RedirectResponse(f"/articles/{aid}", status_code=303)


@action.post("/targets/{target_id}/retry")
def retry_target(request: Request, target_id: int):
    session, target = _target(request, target_id)
    try:
        if target.status == TargetStatus.FAILED:
            target.status = TargetStatus.APPROVED
            target.error = None
            article = session.get(Article, target.article_id)
            if article.approved_at is None:
                article.approved_at = utcnow()
            session.commit()
            reschedule(session, delay=_delay(request))
        aid = target.article_id
    finally:
        session.close()
    return RedirectResponse(f"/articles/{aid}", status_code=303)


router.include_router(action)
