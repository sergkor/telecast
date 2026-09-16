from sqlmodel import select

from telecast.models import Article, ArticleState, PublishTarget, TargetStatus, utcnow

# APPROVED / PUBLISHING targets are already in flight — never re-queue those.
REQUEUEABLE = {TargetStatus.PUBLISHED, TargetStatus.FAILED,
               TargetStatus.SKIPPED, TargetStatus.PENDING}


def queue_republish(session, article_ids: list[int], platform: str) -> int:
    """Re-queue `platform` for the given PUBLISHED articles; the publish
    worker picks them up like any approved target. Returns how many were
    queued. Missing targets are created; non-PUBLISHED articles skipped."""
    queued = 0
    for aid in article_ids:
        article = session.get(Article, aid)
        if article is None or article.state != ArticleState.PUBLISHED:
            continue
        target = session.exec(
            select(PublishTarget)
            .where(PublishTarget.article_id == aid)
            .where(PublishTarget.platform == platform)
        ).first()
        if target is None:
            target = PublishTarget(article_id=aid, platform=platform)
            session.add(target)
        elif target.status not in REQUEUEABLE:
            continue
        target.status = TargetStatus.APPROVED
        target.error = None
        if article.approved_at is None:
            article.approved_at = utcnow()
        article.updated_at = utcnow()
        queued += 1
    session.commit()
    return queued
