import pytest
from fastapi.testclient import TestClient

from telecast.config import Settings
from telecast.models import Article, ArticleState, PublishTarget, TargetStatus, utcnow
from telecast.publish import base as registry
from telecast.publish.base import Adapted
from telecast.web.app import create_app


class FakeYouTube:
    name = "youtube"

    def validate(self, article, media, settings):
        return []

    def adapt(self, article):
        return Adapted(title=article.title or "", body=article.final_text or "")

    async def publish(self, article, media, adapted, settings):
        return "https://youtube/new"


@pytest.fixture
def client(session_factory, tmp_path):
    registry.clear()
    registry.register(FakeYouTube())
    settings = Settings(_env_file=None, web_password="pw", secret_key="s", data_dir=tmp_path)
    app = create_app(settings, session_factory)
    c = TestClient(app)
    c.post("/login", data={"password": "pw"})
    yield c
    registry.clear()


def _csrf(client):
    return client.cookies["telecast_csrf"]


def _published(session_factory, msg_id, target_status=TargetStatus.PUBLISHED):
    with session_factory() as s:
        a = Article(source_channel="@n", source_message_id=msg_id,
                    state=ArticleState.PUBLISHED, title="T", final_text="B",
                    approved_at=utcnow())
        s.add(a)
        s.commit()
        s.add(PublishTarget(article_id=a.id, platform="youtube", status=target_status,
                            external_url="https://youtube/old", published_at=utcnow()))
        s.commit()
        return a.id


def test_bulk_republish_selected(client, session_factory):
    a1 = _published(session_factory, 1)
    _published(session_factory, 2)
    r = client.post("/republish", data={"article_ids": [a1], "platform": "youtube",
                                        "csrf": _csrf(client)}, follow_redirects=False)
    assert r.status_code == 303
    with session_factory() as s:
        assert s.get(PublishTarget, 1).status == TargetStatus.APPROVED
        assert s.get(PublishTarget, 2).status == TargetStatus.PUBLISHED  # not selected
        assert s.get(Article, a1).scheduled_at is not None


def test_bulk_republish_unknown_platform_rejected(client, session_factory):
    aid = _published(session_factory, 3)
    r = client.post("/republish", data={"article_ids": [aid], "platform": "myspace",
                                        "csrf": _csrf(client)}, follow_redirects=False)
    assert r.status_code == 400
    with session_factory() as s:
        assert s.get(PublishTarget, 1).status == TargetStatus.PUBLISHED


def test_bulk_republish_no_selection_redirects(client):
    r = client.post("/republish", data={"platform": "youtube", "csrf": _csrf(client)},
                    follow_redirects=False)
    assert r.status_code == 303


def test_target_republish_button(client, session_factory):
    aid = _published(session_factory, 4)
    r = client.post("/targets/1/republish", data={"csrf": _csrf(client)},
                    follow_redirects=False)
    assert r.status_code == 303
    with session_factory() as s:
        t = s.get(PublishTarget, 1)
        assert t.status == TargetStatus.APPROVED
        assert t.error is None
        assert s.get(Article, aid).scheduled_at is not None


def test_target_republish_only_from_published(client, session_factory):
    _published(session_factory, 5, target_status=TargetStatus.PENDING)
    client.post("/targets/1/republish", data={"csrf": _csrf(client)})
    with session_factory() as s:
        assert s.get(PublishTarget, 1).status == TargetStatus.PENDING


def test_published_tab_shows_republish_controls(client, session_factory):
    _published(session_factory, 6)
    r = client.get("/?tab=published")
    assert 'action="/republish"' in r.text
    assert 'name="article_ids"' in r.text
