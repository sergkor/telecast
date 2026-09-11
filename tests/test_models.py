import pytest
from sqlalchemy.exc import IntegrityError

from telecast.models import Article, ArticleState, MediaFile, PublishTarget, TargetStatus


def test_article_defaults(session):
    a = Article(source_channel="@news", source_message_id=10)
    session.add(a)
    session.commit()
    session.refresh(a)
    assert a.state == ArticleState.INGESTED
    assert a.final_text_edited is False
    assert a.created_at is not None


def test_article_dedupe_unique_constraint(session):
    session.add(Article(source_channel="@news", source_message_id=10))
    session.commit()
    session.add(Article(source_channel="@news", source_message_id=10))
    with pytest.raises(IntegrityError):
        session.commit()


def test_target_and_media_rows(session):
    a = Article(source_channel="@news", source_message_id=11)
    session.add(a)
    session.commit()
    session.add(MediaFile(article_id=a.id, file_path="data/media/v.mp4",
                          mime_type="video/mp4", size_bytes=100,
                          tg_file_unique_id="abc"))
    session.add(PublishTarget(article_id=a.id, platform="youtube"))
    session.commit()
    t = session.get(PublishTarget, 1)
    assert t.status == TargetStatus.PENDING
