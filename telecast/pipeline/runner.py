import asyncio

from loguru import logger
from sqlmodel import select

from telecast.config import Settings
from telecast.models import Article, ArticleState, PublishTarget
from telecast.pipeline.enhance import enhance
from telecast.pipeline.llm import GeminiError, GeminiQuotaError
from telecast.pipeline.translate import translate
from telecast.publish import base as registry
from telecast.states import claim, complete, fail

_WORKABLE = [ArticleState.INGESTED, ArticleState.TRANSLATED, ArticleState.ENHANCED]


async def advance_one(session_factory, llm, settings: Settings) -> bool:
    with session_factory() as session:
        article = session.exec(
            select(Article).where(Article.state.in_(_WORKABLE)).order_by(Article.id)
        ).first()
        if article is None:
            return False

        if article.state == ArticleState.INGESTED:
            if not article.original_text.strip():
                complete(session, article, ArticleState.TRANSLATED, translated_text=None)
                return True
            claim(session, article, ArticleState.TRANSLATING)
            try:
                tr = await translate(article.original_text, llm)
            except GeminiQuotaError:
                article.state = ArticleState.INGESTED
                article.claimed_at = None
                session.commit()
                raise
            except GeminiError as e:
                fail(session, article, ArticleState.FAILED_TRANSLATE, str(e))
                return True
            complete(
                session, article, ArticleState.TRANSLATED,
                translated_text=tr.translated_text,
                detected_language=tr.detected_language,
            )
            return True

        if article.state == ArticleState.TRANSLATED:
            claim(session, article, ArticleState.ENHANCING)
            try:
                enh = await enhance(article.translated_text, settings.enhance_prompt_path, llm)
            except GeminiQuotaError:
                article.state = ArticleState.TRANSLATED
                article.claimed_at = None
                session.commit()
                raise
            except GeminiError as e:
                fail(session, article, ArticleState.FAILED_ENHANCE, str(e))
                return True
            fields = dict(
                enhanced_text=enh.article,
                title=enh.title,
                hashtags=" ".join(enh.hashtags),
            )
            if not article.final_text_edited:
                fields["final_text"] = enh.article
            complete(session, article, ArticleState.ENHANCED, **fields)
            return True

        # ENHANCED: create publish targets (idempotent) and open for review
        existing = {
            t.platform
            for t in session.exec(
                select(PublishTarget).where(PublishTarget.article_id == article.id)
            ).all()
        }
        for platform in registry.names():
            if platform not in existing:
                session.add(PublishTarget(article_id=article.id, platform=platform))
        complete(session, article, ArticleState.PENDING_REVIEW)
        return True


async def pipeline_loop(session_factory, llm, settings: Settings, stop_event: asyncio.Event,
                        poll_interval: float = 2.0) -> None:
    while not stop_event.is_set():
        try:
            worked = await advance_one(session_factory, llm, settings)
        except GeminiQuotaError:
            logger.warning("Gemini quota exhausted; pausing pipeline for 1h")
            for _ in range(720):  # 720 * 5s = 1h
                if stop_event.is_set():
                    return
                await asyncio.sleep(5)
            continue
        except Exception:
            logger.exception("pipeline error")
            worked = False
        if not worked:
            await asyncio.sleep(poll_interval)


async def reaper_loop(session_factory, settings: Settings, stop_event: asyncio.Event) -> None:
    from telecast.states import reap_stale

    while not stop_event.is_set():
        with session_factory() as session:
            n = reap_stale(session, settings.stale_claim_minutes)
            if n:
                logger.info(f"reaper reset {n} stale article(s)")
        for _ in range(60):  # 60 * 5s = 300s
            if stop_event.is_set():
                return
            await asyncio.sleep(5)
