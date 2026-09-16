from telecast.config import Settings
from telecast.models import Article, MediaFile
from telecast.publish.wordpress import WordPressPublisher


def make_article(**kw):
    defaults = dict(source_channel="@n", source_message_id=1,
                    title="Title", final_text="Para one\n\nPara two", hashtags="#a #b")
    defaults.update(kw)
    return Article(**defaults)


def make_settings(tmp_path, **kw):
    defaults = dict(_env_file=None, data_dir=tmp_path,
                    wordpress_url="https://blog.example.com",
                    wordpress_username="bot",
                    wordpress_app_password="xxxx xxxx")
    defaults.update(kw)
    return Settings(**defaults)


def test_adapt_wraps_paragraphs_and_appends_hashtags():
    pub = WordPressPublisher()
    adapted = pub.adapt(make_article())
    assert adapted.title == "Title"
    assert adapted.body == "<p>Para one</p>\n<p>Para two</p>\n<p>#a #b</p>"


def test_adapt_without_hashtags_has_no_trailing_paragraph():
    pub = WordPressPublisher()
    adapted = pub.adapt(make_article(hashtags=None))
    assert adapted.body == "<p>Para one</p>\n<p>Para two</p>"


async def test_publish_uploads_longest_video_with_injected_fn(tmp_path):
    calls = {}

    def fake_upload(file_path, thumb_path, title, html, settings):
        calls.update(dict(file_path=file_path, thumb_path=thumb_path,
                          title=title, html=html, url=settings.wordpress_url))
        return "https://blog.example.com/?p=7"

    pub = WordPressPublisher(upload_fn=fake_upload)
    settings = make_settings(tmp_path)
    art = make_article()
    media = [MediaFile(article_id=1, file_path="short.mp4", duration_s=5, thumb_path="s.jpg"),
             MediaFile(article_id=1, file_path="long.mp4", duration_s=50, thumb_path="l.jpg")]
    url = await pub.publish(art, media, pub.adapt(art), settings)
    assert url == "https://blog.example.com/?p=7"
    assert calls["file_path"] == "long.mp4"
    assert calls["thumb_path"] == "l.jpg"
    assert calls["title"] == "Title"
    assert calls["url"] == "https://blog.example.com"


def test_validate_warns_without_credentials(tmp_path):
    pub = WordPressPublisher()
    settings = Settings(_env_file=None, data_dir=tmp_path)
    warnings = pub.validate(make_article(),
                            [MediaFile(article_id=1, file_path="v.mp4")], settings)
    assert any("wordpress" in w.lower() and "not configured" in w.lower() for w in warnings)


def test_validate_warns_without_media(tmp_path):
    pub = WordPressPublisher()
    settings = make_settings(tmp_path)
    warnings = pub.validate(make_article(), [], settings)
    assert any("no video" in w.lower() for w in warnings)


def test_validate_warns_on_album_and_size(tmp_path):
    pub = WordPressPublisher()
    settings = make_settings(tmp_path, media_max_bytes=100)
    media = [MediaFile(article_id=1, file_path="a.mp4", duration_s=9, size_bytes=101),
             MediaFile(article_id=1, file_path="b.mp4", duration_s=5, size_bytes=10)]
    warnings = pub.validate(make_article(), media, settings)
    assert any("album" in w.lower() for w in warnings)
    assert any("size" in w.lower() for w in warnings)


def test_settings_wordpress_defaults(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path)
    assert settings.wordpress_url == ""
    assert settings.wordpress_username == ""
    assert settings.wordpress_app_password == ""
    assert settings.wordpress_status == "publish"


def test_configured_requires_url_username_and_app_password(tmp_path):
    pub = WordPressPublisher()
    assert pub.configured(make_settings(tmp_path))
    assert not pub.configured(make_settings(tmp_path, wordpress_app_password=""))
    assert not pub.configured(Settings(_env_file=None, data_dir=tmp_path))
