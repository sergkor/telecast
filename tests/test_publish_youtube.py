from telecast.config import Settings
from telecast.models import Article, MediaFile
from telecast.publish.youtube import YouTubePublisher, pick_video


def make_article(**kw):
    defaults = dict(source_channel="@n", source_message_id=1,
                    title="A very long title " + "x" * 100,
                    final_text="Body", hashtags="#a #b")
    defaults.update(kw)
    return Article(**defaults)


def test_pick_video_prefers_longest():
    short = MediaFile(article_id=1, file_path="a.mp4", duration_s=5)
    long_v = MediaFile(article_id=1, file_path="b.mp4", duration_s=50)
    assert pick_video([short, long_v]).file_path == "b.mp4"


def test_adapt_truncates_title_to_100_and_appends_hashtags():
    pub = YouTubePublisher()
    adapted = pub.adapt(make_article())
    assert len(adapted.title) <= 100
    assert adapted.body == "Body\n\n#a #b"


async def test_publish_uses_injected_upload_fn(tmp_path):
    calls = {}

    def fake_upload(file_path, title, description, privacy, token_path):
        calls.update(dict(file_path=file_path, title=title,
                          description=description, privacy=privacy,
                          token_path=token_path))
        return "https://www.youtube.com/watch?v=xyz"

    pub = YouTubePublisher(upload_fn=fake_upload)
    settings = Settings(_env_file=None, data_dir=tmp_path, youtube_privacy="unlisted")
    art = make_article()
    media = [MediaFile(article_id=1, file_path="v.mp4", duration_s=10)]
    url = await pub.publish(art, media, pub.adapt(art), settings)
    assert url == "https://www.youtube.com/watch?v=xyz"
    assert calls["privacy"] == "unlisted"
    assert calls["file_path"] == "v.mp4"


def test_adapt_appends_telegram_channel_link_when_configured():
    pub = YouTubePublisher(channel_url="https://t.me/mychannel")
    adapted = pub.adapt(make_article())
    assert adapted.body == "Body\n\n#a #b\n\nTelegram: https://t.me/mychannel"


def test_adapt_omits_telegram_link_when_not_configured():
    pub = YouTubePublisher()
    adapted = pub.adapt(make_article())
    assert "Telegram" not in adapted.body


def test_settings_telegram_channel_url_default(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path)
    assert settings.telegram_channel_url == ""


def test_validate_blocks_without_token(tmp_path):
    pub = YouTubePublisher()
    settings = Settings(_env_file=None, data_dir=tmp_path)
    warnings = pub.validate(make_article(), [MediaFile(article_id=1, file_path="v.mp4")], settings)
    assert any("token" in w.lower() for w in warnings)
