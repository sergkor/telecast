from types import SimpleNamespace

from telecast.ingest.core import get_cursor
from telecast.ingest.telegram import Ingestor, is_video_message
from telecast.ingest.thumbs import make_thumbnail
from telecast.models import Article


def make_msg(mid, video=False, grouped_id=None):
    return SimpleNamespace(id=mid, video=object() if video else None,
                           grouped_id=grouped_id, message="")


class FakeClient:
    def __init__(self, messages):
        self.messages = messages  # newest first

    async def get_messages(self, entity, limit=None):
        return self.messages[:limit]


def make_ingestor(session_factory, messages, recent_posts=5):
    ing = Ingestor.__new__(Ingestor)
    ing.settings = SimpleNamespace(recent_posts=recent_posts)
    ing.session_factory = session_factory
    ing.client = FakeClient(messages)
    ing.handled = []

    async def fake_handle(chat, msgs):
        ing.handled.append([m.id for m in msgs])
        return True

    ing._handle = fake_handle
    return ing


ENTITY = SimpleNamespace(username="src")


async def test_ingest_recent_picks_last_5_video_posts(session_factory):
    # newest first: ids 20..1; even ids are videos
    messages = [make_msg(i, video=(i % 2 == 0)) for i in range(20, 0, -1)]
    ing = make_ingestor(session_factory, messages)
    await ing._ingest_recent(ENTITY, "@src")
    assert ing.handled == [[12], [14], [16], [18], [20]]  # oldest first
    with session_factory() as s:
        assert get_cursor(s, "@src") == 20


async def test_ingest_recent_count_is_configurable(session_factory):
    messages = [make_msg(i, video=True) for i in range(20, 0, -1)]
    ing = make_ingestor(session_factory, messages, recent_posts=2)
    await ing._ingest_recent(ENTITY, "@src")
    assert ing.handled == [[19], [20]]


async def test_ingest_recent_skips_already_ingested(session_factory):
    with session_factory() as s:
        s.add(Article(source_channel="@src", source_message_id=18))
        s.commit()
    messages = [make_msg(i, video=True) for i in range(20, 15, -1)]
    ing = make_ingestor(session_factory, messages)
    await ing._ingest_recent(ENTITY, "@src")
    assert [g[0] for g in ing.handled] == [16, 17, 19, 20]


async def test_ingest_recent_counts_album_as_one_post(session_factory):
    messages = [
        make_msg(10, video=True),
        make_msg(9, video=True, grouped_id=7),
        make_msg(8, video=True, grouped_id=7),
        make_msg(5, video=True),
    ]
    ing = make_ingestor(session_factory, messages)
    await ing._ingest_recent(ENTITY, "@src")
    assert ing.handled == [[5], [8, 9], [10]]


async def test_ingest_recent_skips_ingested_album_by_grouped_id(session_factory):
    with session_factory() as s:
        s.add(Article(source_channel="@src", source_message_id=8, grouped_id=7))
        s.commit()
    messages = [
        make_msg(9, video=True, grouped_id=7),
        make_msg(8, video=True, grouped_id=7),
    ]
    ing = make_ingestor(session_factory, messages)
    await ing._ingest_recent(ENTITY, "@src")
    assert ing.handled == []


def test_is_video_message():
    assert is_video_message(SimpleNamespace(video=object()))
    assert not is_video_message(SimpleNamespace(video=None))


def test_make_thumbnail_returns_none_on_bad_input(tmp_path):
    bad = tmp_path / "not_a_video.mp4"
    bad.write_bytes(b"junk")
    assert make_thumbnail(bad, tmp_path) is None
