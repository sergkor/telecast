import asyncio

from loguru import logger
from sqlmodel import select

from telecast.config import Settings
from telecast.models import Article, ArticleState, MediaFile, PublishTarget, TargetStatus, utcnow
from telecast.publish import base as registry


async def publish_one(session_factory, settings: Settings) -> bool:
    with session_factory() as session:
        target = session.exec(
            select(PublishTarget)
            .join(Article, Article.id == PublishTarget.article_id)
            .where(PublishTarget.status == TargetStatus.APPROVED)
            .where((Article.scheduled_at == None) | (Article.scheduled_at <= utcnow()))  # noqa: E711
            .order_by(PublishTarget.id)
        ).first()
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
            adapted = publisher.adapt(article)
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

        siblings = session.exec(
            select(PublishTarget).where(PublishTarget.article_id == article.id)
        ).all()
        done = {TargetStatus.PUBLISHED, TargetStatus.SKIPPED}
        if all(t.status in done for t in siblings) and any(
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
