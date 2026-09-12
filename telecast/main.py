import asyncio
from contextlib import asynccontextmanager

from loguru import logger

from telecast.config import Settings
from telecast.db import init_db, make_engine, make_session_factory
from telecast.pipeline.llm import RealGeminiClient
from telecast.pipeline.runner import pipeline_loop, reaper_loop
from telecast.publish import base as registry
from telecast.publish.telegram import TelegramPublisher
from telecast.publish.worker import publish_loop
from telecast.publish.youtube import YouTubePublisher
from telecast.web.app import create_app


def _log_task_exception(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.opt(exception=exc).error(f"background task failed: {task.get_name()}")


def build():
    settings = Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.media_dir.mkdir(parents=True, exist_ok=True)
    engine = make_engine(settings.db_path)
    init_db(engine)
    session_factory = make_session_factory(engine)
    llm = RealGeminiClient(settings.gemini_api_key)
    registry.register(TelegramPublisher())
    registry.register(YouTubePublisher())

    stop_event = asyncio.Event()

    @asynccontextmanager
    async def lifespan(app):
        tasks = [
            asyncio.create_task(pipeline_loop(session_factory, llm, settings, stop_event)),
            asyncio.create_task(publish_loop(session_factory, settings, stop_event)),
            asyncio.create_task(reaper_loop(session_factory, settings, stop_event)),
        ]
        if settings.telegram_api_id:
            from telecast.ingest.telegram import Ingestor
            tasks.append(asyncio.create_task(Ingestor(settings, session_factory).start(stop_event)))
        else:
            logger.warning("TELECAST_TELEGRAM_API_ID not set — ingestor disabled")
        for t in tasks:
            t.add_done_callback(_log_task_exception)
        yield
        stop_event.set()
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    app = create_app(settings, session_factory, llm=llm)
    app.router.lifespan_context = lifespan
    return app, stop_event


def get_app():
    app, _ = build()
    return app
