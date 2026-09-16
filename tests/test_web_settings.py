import pytest
from fastapi.testclient import TestClient

from telecast import models  # noqa: F401
from telecast.config import Settings
from telecast.models import (
    Article,
    ArticleState,
    PublishTarget,
    TargetStatus,
)
from telecast.publish import base as registry
from telecast.web.app import create_app
from tests.fakes import StubPublisher


@pytest.fixture
def client(session_factory, tmp_path):
    prompt = tmp_path / "enhance.md"
    prompt.write_text("PROMPT BODY {source_text}")
    settings = Settings(_env_file=None, web_password="pw", secret_key="s",
                        data_dir=tmp_path, enhance_prompt_path=prompt,
                        source_channels="@a,@b", dest_channel="@dest")
    app = create_app(settings, session_factory)
    c = TestClient(app)
    c.post("/login", data={"password": "pw"})
    return c


def test_settings_page_shows_config_and_health(client):
    r = client.get("/settings")
    assert r.status_code == 200
    assert "@a" in r.text and "@dest" in r.text
    assert "PROMPT BODY" in r.text
    assert "missing" in r.text.lower()  # youtube token not present


def test_settings_page_lists_plugin_config_status(client):
    registry.clear()
    registry.register(StubPublisher("telegram"))
    registry.register(StubPublisher("wordpress", configured=False))
    try:
        r = client.get("/settings")
        assert "telegram" in r.text and "wordpress" in r.text
        assert "not configured" in r.text
    finally:
        registry.clear()


def test_recalculate_promotes_stalled_article(client, session_factory):
    registry.clear()
    registry.register(StubPublisher("telegram"))
    registry.register(StubPublisher("wordpress", configured=False))
    try:
        with session_factory() as s:
            a = Article(source_channel="@n", source_message_id=1,
                        state=ArticleState.PENDING_REVIEW, title="T")
            s.add(a)
            s.commit()
            s.refresh(a)
            s.add(PublishTarget(article_id=a.id, platform="telegram",
                                status=TargetStatus.PUBLISHED))
            s.add(PublishTarget(article_id=a.id, platform="wordpress",
                                status=TargetStatus.PENDING))
            s.commit()
            aid = a.id
        r = client.post("/settings/recalculate",
                        data={"csrf": client.cookies["telecast_csrf"]},
                        follow_redirects=False)
        assert r.status_code == 303
        with session_factory() as s:
            assert s.get(Article, aid).state == ArticleState.PUBLISHED
    finally:
        registry.clear()


def test_recalculate_requires_csrf(client):
    r = client.post("/settings/recalculate", data={})
    assert r.status_code == 403
