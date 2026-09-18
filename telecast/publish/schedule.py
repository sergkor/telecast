"""Publish slots: one article at a time, a fixed interval apart.

The queue only ever grows at the tail — a slot, once assigned, is never
moved, so what the review UI shows for an article stays true until it
publishes.
"""

from datetime import datetime, timedelta, timezone

from sqlmodel import select

from telecast.models import Article, PublishTarget, TargetStatus, utcnow

DEFAULT_DELAY = timedelta(minutes=5)
DEFAULT_INTERVAL = timedelta(hours=6)


def _as_utc(dt: datetime) -> datetime:
    # SQLite gives datetimes back without an offset; they are always UTC.
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _queued(session, unconfigured: set[str], order=None) -> list[Article]:
    """Articles with an approved target that some configured plugin can
    actually publish, oldest approval first unless `order` says otherwise."""
    stmt = (
        select(PublishTarget.article_id)
        .where(PublishTarget.status == TargetStatus.APPROVED)
        .distinct()
    )
    if unconfigured:
        stmt = stmt.where(PublishTarget.platform.not_in(unconfigured))
    ids = session.exec(stmt).all()
    if not ids:
        return []
    return session.exec(
        select(Article)
        .where(Article.id.in_(ids))
        .order_by(*(order or (Article.approved_at,)), Article.id)
    ).all()


def _in_flight(session) -> set[int]:
    """Articles the publish worker has already claimed. Their slot has served
    its purpose; rewriting it would only misreport what is happening."""
    return set(session.exec(
        select(PublishTarget.article_id)
        .where(PublishTarget.status == TargetStatus.PUBLISHING)
        .distinct()
    ).all())


def schedule_pending(session, now: datetime | None = None,
                     delay: timedelta = DEFAULT_DELAY,
                     interval: timedelta = DEFAULT_INTERVAL,
                     unconfigured: set[str] = frozenset()) -> int:
    """Give every queued article that has no slot yet one at the tail of the
    queue: the first publish waits `delay`, each one after it follows
    `interval` behind its predecessor. Articles that already hold a slot keep
    it and define where the tail is. Returns how many were scheduled."""
    now = now or utcnow()
    queued = _queued(session, unconfigured)
    slots = [_as_utc(a.scheduled_at) for a in queued if a.scheduled_at is not None]
    tail = max(slots) if slots else None

    scheduled = 0
    for article in queued:
        if article.scheduled_at is not None:
            continue
        earliest = now + delay
        # An overdue tail restarts spacing from now rather than firing the
        # whole backlog at once.
        article.scheduled_at = earliest if tail is None else max(earliest, tail + interval)
        article.updated_at = now
        tail = article.scheduled_at
        scheduled += 1
    if scheduled:
        session.commit()
    return scheduled


def reset_schedule(session, now: datetime | None = None,
                   delay: timedelta = DEFAULT_DELAY,
                   interval: timedelta = DEFAULT_INTERVAL,
                   unconfigured: set[str] = frozenset()) -> int:
    """Throw the queue away and lay it out again from scratch: every queued
    article re-slotted in creation order, the first at `now + delay` and each
    one after it `interval` behind its predecessor. Returns how many moved.

    This is the one operation allowed to rewrite a slot the review UI has
    already shown — the escape hatch for a queue whose order no longer makes
    sense. Articles already being published keep their slot and do not anchor
    the tail.
    """
    now = now or utcnow()
    flying = _in_flight(session)
    queued = [a for a in _queued(session, unconfigured, order=(Article.created_at,))
              if a.id not in flying]

    for position, article in enumerate(queued):
        article.scheduled_at = now + delay + position * interval
        article.updated_at = now
    if queued:
        session.commit()
    return len(queued)
