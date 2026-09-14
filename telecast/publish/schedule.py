from datetime import datetime, timedelta

from sqlmodel import select

from telecast.models import Article, PublishTarget, TargetStatus, utcnow

WINDOW = timedelta(hours=24)
DEFAULT_DELAY = timedelta(minutes=5)


def reschedule(session, now: datetime | None = None,
               delay: timedelta = DEFAULT_DELAY) -> int:
    """Recompute publish slots for every article with an approved, unpublished
    target: FIFO by approval time, first slot after `delay`, the rest spread
    evenly over the following 24h."""
    now = now or utcnow()
    queued_ids = session.exec(
        select(PublishTarget.article_id)
        .where(PublishTarget.status == TargetStatus.APPROVED)
        .distinct()
    ).all()
    if not queued_ids:
        return 0
    articles = session.exec(
        select(Article)
        .where(Article.id.in_(queued_ids))
        .order_by(Article.approved_at, Article.id)
    ).all()
    interval = WINDOW / len(articles)
    first = now + delay
    for slot, article in enumerate(articles):
        article.scheduled_at = first + slot * interval
        article.updated_at = now
    session.commit()
    return len(articles)
