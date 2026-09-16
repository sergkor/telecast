import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from telecast.config import Settings
from telecast.models import Article, ArticleState, PublishTarget, TargetStatus
from telecast.publish import base as registry
from telecast.web.app import create_app
from tests.fakes import StubPublisher


@pytest.fixture
def client(session_factory, tmp_path):
    registry.clear()
    settings = Settings(_env_file=None, web_password="pw", secret_key="s", data_dir=tmp_path)
    app = create_app(settings, session_factory)
    c = TestClient(app)
    c.post("/login", data={"password": "pw"})
    yield c
    registry.clear()


@pytest.fixture
def article(session_factory):
    with session_factory() as s:
        a = Article(source_channel="@n", source_message_id=1,
                    state=ArticleState.PENDING_REVIEW, title="T",
                    original_text="orig", translated_text="tr",
                    enhanced_text="enh", final_text="enh")
        s.add(a)
        s.commit()
        s.refresh(a)
        s.add(PublishTarget(article_id=a.id, platform="telegram"))
        s.commit()
        return a.id


def _csrf(client):
    return client.cookies["telecast_csrf"]


def test_queue_lists_pending(client, article):
    r = client.get("/")
    assert r.status_code == 200
    assert "T" in r.text


def test_detail_shows_texts(client, article):
    r = client.get(f"/articles/{article}")
    assert "orig" in r.text and "tr" in r.text and "enh" in r.text


def test_save_marks_edited(client, article, session_factory):
    client.post(f"/articles/{article}/save",
                data={"title": "T2", "final_text": "edited", "csrf": _csrf(client)})
    with session_factory() as s:
        a = s.get(Article, article)
        assert a.final_text == "edited" and a.final_text_edited and a.title == "T2"


def test_approve_then_skip_noop(client, article, session_factory):
    client.post("/targets/1/approve", data={"csrf": _csrf(client)})
    with session_factory() as s:
        assert s.get(PublishTarget, 1).status == TargetStatus.APPROVED
    client.post("/targets/1/skip", data={"csrf": _csrf(client)})
    with session_factory() as s:  # skip only from PENDING/FAILED — stays APPROVED
        assert s.get(PublishTarget, 1).status == TargetStatus.APPROVED


def test_discard(client, article, session_factory):
    client.post(f"/articles/{article}/discard", data={"csrf": _csrf(client)})
    with session_factory() as s:
        assert s.get(Article, article).state == ArticleState.DISCARDED


def test_post_without_csrf_rejected(client, article):
    r = client.post(f"/articles/{article}/discard", data={})
    assert r.status_code == 403


def test_approve_sets_approved_at_and_schedules(client, article, session_factory):
    client.post("/targets/1/approve", data={"csrf": _csrf(client)})
    with session_factory() as s:
        a = s.get(Article, article)
        assert a.approved_at is not None
        assert a.scheduled_at is not None


def test_new_approval_resets_schedule_oldest_goes_now(client, article, session_factory):
    client.post("/targets/1/approve", data={"csrf": _csrf(client)})
    with session_factory() as s:
        a2 = Article(source_channel="@n", source_message_id=2,
                     state=ArticleState.PENDING_REVIEW, title="T2")
        s.add(a2)
        s.commit()
        s.refresh(a2)
        s.add(PublishTarget(article_id=a2.id, platform="telegram"))
        s.commit()
        a2_id = a2.id
    client.post("/targets/2/approve", data={"csrf": _csrf(client)})
    with session_factory() as s:
        a1 = s.get(Article, article)
        a2 = s.get(Article, a2_id)
        assert a1.scheduled_at is not None and a2.scheduled_at is not None
        # oldest approval gets the immediate slot; newer one is 12h later
        assert a1.scheduled_at < a2.scheduled_at


def test_publish_now_clears_schedule(client, article, session_factory):
    client.post("/targets/1/approve", data={"csrf": _csrf(client)})
    client.post(f"/articles/{article}/publish_now", data={"csrf": _csrf(client)})
    with session_factory() as s:
        assert s.get(Article, article).scheduled_at is None


def _set_state(session_factory, article_id, state):
    with session_factory() as s:
        s.get(Article, article_id).state = state
        s.commit()


def test_add_target_creates_pending_on_published_article(client, article, session_factory):
    registry.register(StubPublisher("telegram"))
    registry.register(StubPublisher("tiktok"))
    _set_state(session_factory, article, ArticleState.PUBLISHED)
    client.post(f"/articles/{article}/add_target",
                data={"platform": "tiktok", "csrf": _csrf(client)})
    with session_factory() as s:
        t = s.exec(select(PublishTarget).where(PublishTarget.article_id == article,
                                               PublishTarget.platform == "tiktok")).one()
        assert t.status == TargetStatus.PENDING


def test_add_target_rejects_unregistered_platform(client, article, session_factory):
    _set_state(session_factory, article, ArticleState.PUBLISHED)
    r = client.post(f"/articles/{article}/add_target",
                    data={"platform": "myspace", "csrf": _csrf(client)})
    assert r.status_code == 400


def test_add_target_rejects_duplicate(client, article, session_factory):
    registry.register(StubPublisher("telegram"))
    _set_state(session_factory, article, ArticleState.PUBLISHED)
    r = client.post(f"/articles/{article}/add_target",
                    data={"platform": "telegram", "csrf": _csrf(client)})
    assert r.status_code == 400
    with session_factory() as s:
        rows = s.exec(select(PublishTarget).where(PublishTarget.article_id == article,
                                                  PublishTarget.platform == "telegram")).all()
        assert len(rows) == 1


def test_add_target_rejects_unreviewed_article(client, article, session_factory):
    registry.register(StubPublisher("tiktok"))
    _set_state(session_factory, article, ArticleState.INGESTED)
    r = client.post(f"/articles/{article}/add_target",
                    data={"platform": "tiktok", "csrf": _csrf(client)})
    assert r.status_code == 400


def test_detail_offers_missing_platforms(client, article, session_factory):
    registry.register(StubPublisher("telegram"))
    registry.register(StubPublisher("tiktok"))
    _set_state(session_factory, article, ArticleState.PUBLISHED)
    r = client.get(f"/articles/{article}")
    assert "add_target" in r.text and "tiktok" in r.text


def test_detail_hides_add_when_no_missing_platforms(client, article, session_factory):
    registry.register(StubPublisher("telegram"))
    _set_state(session_factory, article, ArticleState.PUBLISHED)
    r = client.get(f"/articles/{article}")
    assert "add_target" not in r.text


def test_detail_hides_target_for_unconfigured_plugin(client, article, session_factory):
    registry.register(StubPublisher("telegram"))
    registry.register(StubPublisher("wordpress", configured=False))
    with session_factory() as s:
        s.add(PublishTarget(article_id=article, platform="wordpress"))
        s.commit()
    r = client.get(f"/articles/{article}")
    assert "wordpress" not in r.text
    assert "telegram" in r.text


def test_detail_does_not_offer_unconfigured_plugin(client, article, session_factory):
    registry.register(StubPublisher("telegram"))
    registry.register(StubPublisher("wordpress", configured=False))
    _set_state(session_factory, article, ArticleState.PUBLISHED)
    r = client.get(f"/articles/{article}")
    assert "add_target" not in r.text


def test_add_target_rejects_unconfigured_platform(client, article, session_factory):
    registry.register(StubPublisher("telegram"))
    registry.register(StubPublisher("wordpress", configured=False))
    _set_state(session_factory, article, ArticleState.PUBLISHED)
    r = client.post(f"/articles/{article}/add_target",
                    data={"platform": "wordpress", "csrf": _csrf(client)})
    assert r.status_code == 400
