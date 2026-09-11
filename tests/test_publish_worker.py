from telecast.config import Settings
from telecast.models import Article, ArticleState, MediaFile, PublishTarget, TargetStatus
from telecast.publish import base as registry
from telecast.publish.base import Adapted
from telecast.publish.worker import publish_one


class GoodPublisher:
    name = "good"

    def validate(self, article, media, settings):
        return []

    def adapt(self, article):
        return Adapted(title=article.title or "", body=article.final_text or "")

    async def publish(self, article, media, adapted, settings):
        return "https://example.com/1"


class BadPublisher(GoodPublisher):
    name = "bad"

    async def publish(self, article, media, adapted, settings):
        raise RuntimeError("upload exploded")


def _setup(session, platforms):
    registry.clear()
    a = Article(source_channel="@n", source_message_id=1,
                state=ArticleState.PENDING_REVIEW, title="T", final_text="B")
    session.add(a)
    session.commit()
    session.add(MediaFile(article_id=a.id, file_path="v.mp4"))
    for p in platforms:
        registry.register(p)
        session.add(PublishTarget(article_id=a.id, platform=p.name,
                                  status=TargetStatus.APPROVED))
    session.commit()
    return a.id


async def test_publish_success_marks_published(session_factory, tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path)
    with session_factory() as s:
        aid = _setup(s, [GoodPublisher()])
    assert await publish_one(session_factory, settings)
    with session_factory() as s:
        t = s.get(PublishTarget, 1)
        assert t.status == TargetStatus.PUBLISHED
        assert t.external_url == "https://example.com/1"
        assert s.get(Article, aid).state == ArticleState.PUBLISHED
    registry.clear()


async def test_publish_failure_marks_failed_keeps_article(session_factory, tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path)
    with session_factory() as s:
        aid = _setup(s, [BadPublisher()])
    assert await publish_one(session_factory, settings)
    with session_factory() as s:
        t = s.get(PublishTarget, 1)
        assert t.status == TargetStatus.FAILED
        assert "upload exploded" in t.error
        assert s.get(Article, aid).state == ArticleState.PENDING_REVIEW
    registry.clear()


async def test_no_approved_targets_returns_false(session_factory, tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path)
    assert not await publish_one(session_factory, settings)
