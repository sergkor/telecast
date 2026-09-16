import asyncio

from loguru import logger
from sqlmodel import select

from telecast.config import Settings
from telecast.models import Article, ArticleState, MediaFile, PublishTarget, TargetStatus, utcnow
from telecast.publish import base as registry
from telecast.publish.base import Context
from telecast.publish.recalc import DONE_STATUSES, effective_targets


def _published_urls(session, article_id: int) -> dict[str, str]:
    return {
        t.platform: t.external_url
        for t in session.exec(
            select(PublishTarget).where(PublishTarget.article_id == article_id)
        ).all()
        if t.status == TargetStatus.PUBLISHED and t.external_url
    }


def _context(session, article_id: int) -> Context:
    return Context(published=_published_urls(session, article_id))


def _first_ready(session, targets: list[PublishTarget]) -> PublishTarget | None:
    """The first target whose dependency, if it declares one, has already
    published. A target still waiting is left APPROVED — it becomes ready on
    a later pass, which is the pass right after its dependency publishes."""
    for target in targets:
        needs = registry.dependency(target.platform)
        if needs is None or needs in _published_urls(session, target.article_id):
            return target
    return None


async def publish_one(session_factory, settings: Settings) -> bool:
    unconfigured = registry.unconfigured(settings)
    with session_factory() as session:
        # A target whose plugin lost its config stays APPROVED and waits
        # rather than failing against missing credentials.
        stmt = (
            select(PublishTarget)
            .join(Article, Article.id == PublishTarget.article_id)
            .where(PublishTarget.status == TargetStatus.APPROVED)
            .where((Article.scheduled_at == None) | (Article.scheduled_at <= utcnow()))  # noqa: E711
            .order_by(PublishTarget.id)
        )
        if unconfigured:
            stmt = stmt.where(PublishTarget.platform.not_in(unconfigured))
        target = _first_ready(session, session.exec(stmt).all())
        if target is None:
            return False

        target.status = TargetStatus.PUBLISHING
        session.commit()

        article = session.get(Article, target.article_id)
        media = session.exec(
            select(MediaFile).where(MediaFile.article_id == article.id)
        ).all()
        try:
            publisher = registry.get(target.platform)
            adapted = publisher.adapt(article, _context(session, article.id))
            url = await publisher.publish(article, list(media), adapted, settings)
        except Exception as e:
            logger.exception(f"publish failed: article={article.id} platform={target.platform}")
            target.status = TargetStatus.FAILED
            target.error = str(e)[:2000]
            session.commit()
            return True

        target.status = TargetStatus.PUBLISHED
        target.external_url = url
        target.adapted_text = f"{adapted.title}\n\n{adapted.body}".strip()
        target.error = None
        target.published_at = utcnow()
        session.commit()

        siblings = effective_targets(
            session.exec(
                select(PublishTarget).where(PublishTarget.article_id == article.id)
            ).all(),
            unconfigured,
        )
        if all(t.status in DONE_STATUSES for t in siblings) and any(
            t.status == TargetStatus.PUBLISHED for t in siblings
        ):
            article.state = ArticleState.PUBLISHED
            article.updated_at = utcnow()
            session.commit()
        return True


async def publish_loop(session_factory, settings: Settings, stop_event: asyncio.Event,
                       poll_interval: float = 2.0) -> None:
    while not stop_event.is_set():
        try:
            worked = await publish_one(session_factory, settings)
        except Exception:
            logger.exception("publish worker error")
            worked = False
        if not worked:
            await asyncio.sleep(poll_interval)
