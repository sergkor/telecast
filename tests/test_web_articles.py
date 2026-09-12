import pytest
from fastapi.testclient import TestClient

from telecast.config import Settings
from telecast.models import Article, ArticleState, PublishTarget, TargetStatus
from telecast.publish import base as registry
from telecast.web.app import create_app


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
