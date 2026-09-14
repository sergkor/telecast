from telecast.config import Settings
from telecast.models import Article, MediaFile
from telecast.publish.tiktok import CAPTION_LIMIT, TIKTOK_MAX_SECONDS, TikTokPublisher


def make_article(**kw):
    defaults = dict(source_channel="@n", source_message_id=1,
                    title="Title", final_text="Body", hashtags="#a #b")
    defaults.update(kw)
    return Article(**defaults)


def test_adapt_combines_title_body_hashtags_into_caption():
    pub = TikTokPublisher()
    adapted = pub.adapt(make_article())
    assert adapted.title == "Title\n\nBody\n\n#a #b"
    assert adapted.body == ""


def test_adapt_truncates_caption_to_limit():
    pub = TikTokPublisher()
    adapted = pub.adapt(make_article(final_text="x" * 3000))
    assert len(adapted.title) <= CAPTION_LIMIT
    assert adapted.title.endswith("…")


async def test_publish_uploads_longest_video_with_injected_fn(tmp_path):
    calls = {}

    def fake_upload(file_path, caption, privacy, settings):
        calls.update(dict(file_path=file_path, caption=caption, privacy=privacy))
        return "https://www.tiktok.com/video/123"

    pub = TikTokPublisher(upload_fn=fake_upload)
    settings = Settings(_env_file=None, data_dir=tmp_path, tiktok_privacy="PUBLIC_TO_EVERYONE")
    art = make_article()
    media = [MediaFile(article_id=1, file_path="short.mp4", duration_s=5),
             MediaFile(article_id=1, file_path="long.mp4", duration_s=50)]
    url = await pub.publish(art, media, pub.adapt(art), settings)
    assert url == "https://www.tiktok.com/video/123"
    assert calls["file_path"] == "long.mp4"
    assert calls["privacy"] == "PUBLIC_TO_EVERYONE"
    assert calls["caption"] == "Title\n\nBody\n\n#a #b"


def test_validate_warns_without_token(tmp_path):
    pub = TikTokPublisher()
    settings = Settings(_env_file=None, data_dir=tmp_path)
    warnings = pub.validate(make_article(), [MediaFile(article_id=1, file_path="v.mp4")], settings)
    assert any("token" in w.lower() for w in warnings)


def test_validate_warns_without_media(tmp_path):
    pub = TikTokPublisher()
    settings = Settings(_env_file=None, data_dir=tmp_path)
    warnings = pub.validate(make_article(), [], settings)
    assert any("no video" in w.lower() for w in warnings)


def test_validate_warns_on_album_and_tiktok_duration_cap(tmp_path):
    pub = TikTokPublisher()
    settings = Settings(_env_file=None, data_dir=tmp_path)
    media = [MediaFile(article_id=1, file_path="a.mp4", duration_s=TIKTOK_MAX_SECONDS + 1),
             MediaFile(article_id=1, file_path="b.mp4", duration_s=5)]
    warnings = pub.validate(make_article(), media, settings)
    assert any("album" in w.lower() for w in warnings)
    assert any("duration" in w.lower() and "tiktok" in w.lower() for w in warnings)


def test_settings_tiktok_defaults(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path)
    assert settings.tiktok_privacy == "SELF_ONLY"
    assert settings.tiktok_token_path == tmp_path / "tiktok_token.json"
