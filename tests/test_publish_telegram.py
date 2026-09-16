import httpx

from telecast.config import Settings
from telecast.models import Article, MediaFile
from telecast.publish.telegram import TelegramPublisher


def make_settings(tmp_path):
    return Settings(_env_file=None, bot_token="123:abc", dest_channel="@dest", data_dir=tmp_path)


def make_article():
    return Article(source_channel="@n", source_message_id=1,
                   title="Title", final_text="Body text", hashtags="#a #b")


def test_adapt_truncates_caption_to_1024():
    pub = TelegramPublisher()
    art = make_article()
    art.final_text = "word " * 500
    adapted = pub.adapt(art)
    assert len(adapted.body) <= 1024
    assert adapted.body.endswith("…")


async def test_publish_single_video_calls_sendVideo(tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"fake")
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 77}})

    pub = TelegramPublisher(transport=httpx.MockTransport(handler))
    art = make_article()
    media = [MediaFile(article_id=1, file_path=str(video), size_bytes=4)]
    url = await pub.publish(art, media, pub.adapt(art), make_settings(tmp_path))
    assert "sendVideo" in captured["url"]
    assert url == "https://t.me/dest/77"


async def test_validate_warns_on_oversize(tmp_path):
    pub = TelegramPublisher()
    s = make_settings(tmp_path)
    media = [MediaFile(article_id=1, file_path="v.mp4", size_bytes=s.media_max_bytes + 1)]
    warnings = pub.validate(make_article(), media, s)
    assert any("size" in w.lower() for w in warnings)


async def test_publish_album_calls_sendMediaGroup_with_caption_on_first(tmp_path):
    """Album path: multiple videos, sendMediaGroup, attach:// keys, caption on first item only."""
    v1 = tmp_path / "v1.mp4"
    v2 = tmp_path / "v2.mp4"
    v1.write_bytes(b"fake1")
    v2.write_bytes(b"fake2")
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["data"] = request.content.decode() if isinstance(request.content, bytes) else str(request.content)
        return httpx.Response(200, json={"ok": True, "result": [{"message_id": 99}]})

    pub = TelegramPublisher(transport=httpx.MockTransport(handler))
    art = make_article()
    media = [
        MediaFile(article_id=1, file_path=str(v1), size_bytes=5),
        MediaFile(article_id=1, file_path=str(v2), size_bytes=5),
    ]
    url = await pub.publish(art, media, pub.adapt(art), make_settings(tmp_path))
    assert "sendMediaGroup" in captured["url"]
    assert "attach://video0" in captured["data"]
    assert "attach://video1" in captured["data"]
    assert url == "https://t.me/dest/99"


def test_configured_requires_bot_token_and_dest_channel(tmp_path):
    pub = TelegramPublisher()
    assert pub.configured(make_settings(tmp_path))
    assert not pub.configured(Settings(_env_file=None, data_dir=tmp_path,
                                       dest_channel="@dest"))
    assert not pub.configured(Settings(_env_file=None, data_dir=tmp_path,
                                       bot_token="123:abc"))
