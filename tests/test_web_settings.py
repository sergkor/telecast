from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from telecast import models  # noqa: F401
from telecast.config import Settings
from telecast.models import (
    Article,
    ArticleState,
    PublishTarget,
    TargetStatus,
    utcnow,
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


# --- plugin connection check ------------------------------------------

class CheckablePublisher(StubPublisher):
    """Stub exposing the optional `check_connection` hook."""

    def __init__(self, name, result, **kw):
        super().__init__(name, **kw)
        self._result = result
        self.calls = 0

    def check_connection(self, settings):
        self.calls += 1
        return self._result


def post_validate(client, plugin):
    return client.post(f"/settings/validate/{plugin}",
                       data={"csrf": client.cookies["telecast_csrf"]},
                       follow_redirects=True)


def test_validate_shows_the_check_result(client):
    from telecast.publish.wordpress import CheckResult

    pub = CheckablePublisher("wordpress", CheckResult(True, "connected as telecast-bot"))
    registry.clear()
    registry.register(pub)
    try:
        r = post_validate(client, "wordpress")
        assert r.status_code == 200
        assert pub.calls == 1
        assert "connected as telecast-bot" in r.text
    finally:
        registry.clear()


def test_validate_shows_a_failure_message(client):
    from telecast.publish.wordpress import CheckResult

    registry.clear()
    registry.register(CheckablePublisher("wordpress",
                                         CheckResult(False, "credentials rejected")))
    try:
        assert "credentials rejected" in post_validate(client, "wordpress").text
    finally:
        registry.clear()


def test_validate_reports_a_plugin_without_a_check(client):
    registry.clear()
    registry.register(StubPublisher("telegram"))
    try:
        assert "no connection check" in post_validate(client, "telegram").text
    finally:
        registry.clear()


def test_validate_reports_an_unknown_plugin(client):
    registry.clear()
    try:
        assert "unknown plugin" in post_validate(client, "nope").text
    finally:
        registry.clear()


def test_validate_requires_csrf(client):
    r = client.post("/settings/validate/wordpress", data={})
    assert r.status_code == 403


def test_settings_page_offers_validate_only_for_checkable_plugins(client):
    from telecast.publish.wordpress import CheckResult

    registry.clear()
    registry.register(CheckablePublisher("wordpress", CheckResult(True, "ok")))
    registry.register(StubPublisher("telegram"))
    try:
        text = client.get("/settings").text
        assert "/settings/validate/wordpress" in text
        assert "/settings/validate/telegram" not in text
    finally:
        registry.clear()


# --- reset schedule ---------------------------------------------------

def _queued_article(session_factory, mid, created_at, scheduled_at=None):
    with session_factory() as s:
        a = Article(source_channel="@n", source_message_id=mid,
                    state=ArticleState.PENDING_REVIEW, created_at=created_at,
                    approved_at=created_at, scheduled_at=scheduled_at)
        s.add(a)
        s.commit()
        s.refresh(a)
        s.add(PublishTarget(article_id=a.id, platform="telegram",
                            status=TargetStatus.APPROVED))
        s.commit()
        return a.id


def test_reset_schedule_reorders_the_queue_by_creation_date(client, session_factory):
    registry.clear()
    registry.register(StubPublisher("telegram"))
    try:
        now = utcnow()
        old = _queued_article(session_factory, 1, now - timedelta(days=3),
                              scheduled_at=now + timedelta(days=9))
        new = _queued_article(session_factory, 2, now - timedelta(hours=1),
                              scheduled_at=now + timedelta(minutes=5))
        r = client.post("/settings/reset-schedule",
                        data={"csrf": client.cookies["telecast_csrf"]},
                        follow_redirects=True)
        assert r.status_code == 200
        assert "2 article(s)" in r.text
        with session_factory() as s:
            first = s.get(Article, old).scheduled_at
            second = s.get(Article, new).scheduled_at
        # oldest article first, then one interval (default 6h) behind it
        assert first < second
        assert second - first == timedelta(hours=6)
    finally:
        registry.clear()


def test_reset_schedule_requires_csrf(client):
    r = client.post("/settings/reset-schedule", data={})
    assert r.status_code == 403


def test_settings_page_offers_the_reset_button(client):
    assert "/settings/reset-schedule" in client.get("/settings").text


# --- process stuck articles -------------------------------------------

def _stuck(session_factory, mid, state=ArticleState.INGESTED, text="original"):
    with session_factory() as s:
        a = Article(source_channel="@n", source_message_id=mid, state=state,
                    original_text=text, translated_text="tr")
        s.add(a)
        s.commit()
        s.refresh(a)
        return a.id


def test_settings_page_shows_how_many_articles_are_stuck(client, session_factory):
    _stuck(session_factory, 1)
    _stuck(session_factory, 2, state=ArticleState.TRANSLATED)
    _stuck(session_factory, 3, state=ArticleState.PENDING_REVIEW)
    text = client.get("/settings").text
    assert "/settings/process-stuck" in text
    assert "2 article(s)" in text


def test_process_stuck_advances_the_whole_backlog(client, session_factory):
    from tests.fakes import FakeGemini

    registry.clear()
    registry.register(StubPublisher("telegram"))
    try:
        aid = _stuck(session_factory, 1)
        client.app.state.llm = FakeGemini(responses=[
            {"detected_language": "uk", "translated_text": "tr"},
            {"title": "T", "article": "enhanced", "hashtags": []},
        ])
        r = client.post("/settings/process-stuck",
                        data={"csrf": client.cookies["telecast_csrf"]},
                        follow_redirects=True)
        assert r.status_code == 200
        assert "1 article(s)" in r.text
        with session_factory() as s:
            assert s.get(Article, aid).state == ArticleState.PENDING_REVIEW
    finally:
        registry.clear()


def test_process_stuck_on_an_idle_queue_reports_nothing_to_do(client):
    r = client.post("/settings/process-stuck",
                    data={"csrf": client.cookies["telecast_csrf"]},
                    follow_redirects=True)
    assert "0 article(s)" in r.text


def test_process_stuck_without_a_configured_llm_says_so(client, session_factory):
    _stuck(session_factory, 1)
    r = client.post("/settings/process-stuck",
                    data={"csrf": client.cookies["telecast_csrf"]},
                    follow_redirects=True)
    assert "no Gemini client" in r.text
    with session_factory() as s:
        assert s.get(Article, 1).state == ArticleState.INGESTED


def test_process_stuck_requires_csrf(client):
    r = client.post("/settings/process-stuck", data={})
    assert r.status_code == 403
