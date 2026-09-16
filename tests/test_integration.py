from fastapi.testclient import TestClient
from sqlmodel import select

from telecast.config import Settings
from telecast.ingest.core import IncomingPost, IncomingVideo, ingest_post
from telecast.models import Article, ArticleState, PublishTarget, TargetStatus
from telecast.pipeline.runner import advance_one
from telecast.publish import base as registry
from telecast.publish.base import Adapted
from telecast.publish.worker import publish_one
from telecast.web.app import create_app
from tests.fakes import FakeGemini


class FakePublisher:
    name = "faketube"

    def validate(self, article, media, settings):
        return []

    def adapt(self, article, context=None):
        return Adapted(title=article.title or "", body=article.final_text or "")

    async def publish(self, article, media, adapted, settings):
        return "https://fake.example/v/1"


async def test_ingest_to_published(session_factory, tmp_path):
    registry.clear()
    registry.register(FakePublisher())
    prompt = tmp_path / "enhance.md"
    prompt.write_text("{source_text}")
    settings = Settings(_env_file=None, web_password="pw", secret_key="s",
                        data_dir=tmp_path, enhance_prompt_path=prompt)
    llm = FakeGemini(responses=[
        {"detected_language": "uk", "translated_text": "translated"},
        {"title": "Headline", "article": "Enhanced body", "hashtags": ["#x"]},
    ])

    # 1. ingest
    video = IncomingVideo(file_path=str(tmp_path / "v.mp4"), mime_type="video/mp4",
                          duration_s=12, width=720, height=1280, size_bytes=10,
                          tg_file_unique_id="u1")
    with session_factory() as s:
        a = ingest_post(IncomingPost(channel="@src", message_id=1, grouped_id=None,
                                     text="оригінал", url="https://t.me/src/1",
                                     videos=[video]), s)
        aid = a.id

    # 2. pipeline to review
    while await advance_one(session_factory, llm, settings):
        pass
    with session_factory() as s:
        assert s.get(Article, aid).state == ArticleState.PENDING_REVIEW

    # 3. approve via the web app
    app = create_app(settings, session_factory)
    client = TestClient(app)
    client.post("/login", data={"password": "pw"})
    with session_factory() as s:
        tid = s.exec(select(PublishTarget)).first().id
    client.post(f"/targets/{tid}/approve", data={"csrf": client.cookies["telecast_csrf"]})

    # 4. publish — approval queued it 5 min out; "publish now" makes it due
    with session_factory() as s:
        assert s.get(Article, aid).scheduled_at is not None
    client.post(f"/articles/{aid}/publish_now", data={"csrf": client.cookies["telecast_csrf"]})
    assert await publish_one(session_factory, settings)
    with session_factory() as s:
        t = s.get(PublishTarget, tid)
        assert t.status == TargetStatus.PUBLISHED
        assert t.external_url == "https://fake.example/v/1"
        assert s.get(Article, aid).state == ArticleState.PUBLISHED
    registry.clear()
