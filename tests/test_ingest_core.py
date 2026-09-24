from sqlmodel import select

from telecast.ingest.core import (IncomingPost, IncomingVideo, backfill_checksums, file_checksum,
                                  get_cursor, ingest_post, set_cursor)
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


def test_ingest_dedupes_grouped_post_by_grouped_id(session):
    first = IncomingPost(channel="@n", message_id=100, grouped_id=555,
                         text="album", url="https://t.me/n/100",
                         videos=[IncomingVideo(file_path="data/media/100_0.mp4",
                                               mime_type="video/mp4", duration_s=10.0,
                                               width=720, height=1280, size_bytes=1000,
                                               tg_file_unique_id="uid100_0")])
    second = IncomingPost(channel="@n", message_id=101, grouped_id=555,
                          text="album", url="https://t.me/n/101",
                          videos=[IncomingVideo(file_path="data/media/101_0.mp4",
                                                mime_type="video/mp4", duration_s=10.0,
                                                width=720, height=1280, size_bytes=1000,
                                                tg_file_unique_id="uid101_0")])
    a = ingest_post(first, session)
    assert a is not None
    assert ingest_post(second, session) is None
    articles = session.exec(select(Article).where(Article.grouped_id == 555)).all()
    assert len(articles) == 1


def _video(path, checksum, uid="uid"):
    return IncomingVideo(file_path=str(path), mime_type="video/mp4", duration_s=10.0,
                         width=720, height=1280, size_bytes=1000,
                         tg_file_unique_id=uid, checksum=checksum)


def _post_with(channel, mid, videos, grouped_id=None):
    return IncomingPost(channel=channel, message_id=mid, grouped_id=grouped_id,
                        text="t", url="u", videos=videos)


def test_file_checksum_is_sha256_of_contents(tmp_path):
    import hashlib
    f = tmp_path / "v.mp4"
    f.write_bytes(b"video-bytes")
    assert file_checksum(f) == hashlib.sha256(b"video-bytes").hexdigest()


def test_ingest_skips_same_media_from_other_channel_and_deletes_download(session, tmp_path):
    orig = tmp_path / "a_1.mp4"
    orig.write_bytes(b"same")
    dup, dup_thumb = tmp_path / "b_7.mp4", tmp_path / "b_7.jpg"
    dup.write_bytes(b"same")
    dup_thumb.write_bytes(b"jpg")
    assert ingest_post(_post_with("@a", 1, [_video(orig, "abc")]), session) is not None

    v = _video(dup, "abc")
    v.thumb_path = str(dup_thumb)
    assert ingest_post(_post_with("@b", 7, [v]), session) is None

    assert len(session.exec(select(Article)).all()) == 1
    assert orig.exists()
    assert not dup.exists() and not dup_thumb.exists()
    assert get_cursor(session, "@b") == 7


def test_duplicate_of_discarded_article_is_still_skipped(session, tmp_path):
    a = ingest_post(_post_with("@a", 1, [_video(tmp_path / "x.mp4", "abc")]), session)
    a.state = ArticleState.DISCARDED
    session.commit()
    assert ingest_post(_post_with("@a", 2, [_video(tmp_path / "y.mp4", "abc")]), session) is None


def test_album_with_new_video_is_not_a_duplicate(session, tmp_path):
    ingest_post(_post_with("@a", 1, [_video(tmp_path / "x.mp4", "abc")]), session)
    album = [_video(tmp_path / "y.mp4", "abc"), _video(tmp_path / "z.mp4", "new")]
    assert ingest_post(_post_with("@a", 2, album, grouped_id=9), session) is not None


def test_album_whose_videos_are_all_known_is_a_duplicate(session, tmp_path):
    ingest_post(_post_with("@a", 1, [_video(tmp_path / "x.mp4", "abc")]), session)
    ingest_post(_post_with("@a", 2, [_video(tmp_path / "y.mp4", "def")]), session)
    album = [_video(tmp_path / "p.mp4", "abc"), _video(tmp_path / "q.mp4", "def")]
    assert ingest_post(_post_with("@b", 5, album, grouped_id=3), session) is None


def test_media_without_checksum_is_never_a_duplicate(session, tmp_path):
    ingest_post(_post_with("@a", 1, [_video(tmp_path / "x.mp4", None)]), session)
    assert ingest_post(_post_with("@a", 2, [_video(tmp_path / "y.mp4", None)]), session) is not None


def test_redownload_of_ingested_message_keeps_its_file(session, tmp_path):
    f = tmp_path / "a_1.mp4"
    f.write_bytes(b"same")
    ingest_post(_post_with("@a", 1, [_video(f, "abc")]), session)
    # same message seen again (e.g. live event after backfill): same path
    assert ingest_post(_post_with("@a", 1, [_video(f, "abc")]), session) is None
    assert f.exists()


def test_backfill_checksums_fills_existing_files_only(session, tmp_path):
    present = tmp_path / "p.mp4"
    present.write_bytes(b"data")
    a = Article(source_channel="@a", source_message_id=1)
    session.add(a)
    session.flush()
    session.add(MediaFile(article_id=a.id, file_path=str(present)))
    session.add(MediaFile(article_id=a.id, file_path=str(tmp_path / "gone.mp4")))
    session.add(MediaFile(article_id=a.id, file_path=str(present), checksum="kept"))
    session.commit()

    assert backfill_checksums(session) == 1
    by_path = {(m.file_path, m.checksum) for m in session.exec(select(MediaFile)).all()}
    assert (str(present), file_checksum(present)) in by_path
    assert (str(tmp_path / "gone.mp4"), None) in by_path
    assert (str(present), "kept") in by_path
    assert backfill_checksums(session) == 0
