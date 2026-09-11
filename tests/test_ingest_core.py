from sqlmodel import select

from telecast.ingest.core import IncomingPost, IncomingVideo, get_cursor, ingest_post, set_cursor
from telecast.models import Article, ArticleState, MediaFile


def _post(mid=1, videos=1, text="hello"):
    vids = [
        IncomingVideo(file_path=f"data/media/{mid}_{i}.mp4", mime_type="video/mp4",
                      duration_s=10.0, width=720, height=1280, size_bytes=1000,
                      tg_file_unique_id=f"uid{mid}_{i}")
        for i in range(videos)
    ]
    return IncomingPost(channel="@n", message_id=mid, grouped_id=None,
                        text=text, url=f"https://t.me/n/{mid}", videos=vids)


def test_ingest_creates_article_and_media(session):
    a = ingest_post(_post(videos=2), session)
    assert a is not None and a.state == ArticleState.INGESTED
    media = session.exec(select(MediaFile).where(MediaFile.article_id == a.id)).all()
    assert len(media) == 2
    assert get_cursor(session, "@n") == 1


def test_ingest_skips_duplicates_and_no_video(session):
    assert ingest_post(_post(mid=5), session) is not None
    assert ingest_post(_post(mid=5), session) is None
    assert ingest_post(_post(mid=6, videos=0), session) is None
    articles = session.exec(select(Article)).all()
    assert len(articles) == 1


def test_cursor_only_moves_forward(session):
    set_cursor(session, "@n", 10)
    set_cursor(session, "@n", 3)
    assert get_cursor(session, "@n") == 10
