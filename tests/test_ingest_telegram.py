from types import SimpleNamespace

from telecast.ingest.telegram import is_video_message
from telecast.ingest.thumbs import make_thumbnail


def test_is_video_message():
    assert is_video_message(SimpleNamespace(video=object()))
    assert not is_video_message(SimpleNamespace(video=None))


def test_make_thumbnail_returns_none_on_bad_input(tmp_path):
    bad = tmp_path / "not_a_video.mp4"
    bad.write_bytes(b"junk")
    assert make_thumbnail(bad, tmp_path) is None
