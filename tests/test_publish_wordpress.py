from telecast.config import Settings
from telecast.models import Article, MediaFile
from telecast.publish.base import Context
from telecast.publish.wordpress import WordPressPublisher

YT_URL = "https://www.youtube.com/watch?v=abc123"
TG_URL = "https://t.me/mychannel"


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


def make_pub(**kw):
    defaults = dict(channel_url=TG_URL)
    defaults.update(kw)
    return WordPressPublisher(**defaults)


def yt_context(url=YT_URL):
    return Context(published={"youtube": url})


def test_wordpress_depends_on_youtube():
    assert WordPressPublisher.depends_on == "youtube"


def test_adapt_leads_with_a_youtube_embed_block():
    body = make_pub().adapt(make_article(), yt_context()).body
    assert body.startswith("<!-- wp:embed")
    assert body.count(YT_URL) >= 2  # block attrs + the bare URL WordPress embeds
    assert "wp-block-embed-youtube" in body
    assert "<!-- /wp:embed -->" in body


def test_adapt_keeps_paragraphs_and_hashtags_after_the_embed():
    body = make_pub().adapt(make_article(), yt_context()).body
    assert "<p>Para one</p>\n<p>Para two</p>\n<p>#a #b</p>" in body
    assert body.index("<p>Para one</p>") > body.index("<!-- /wp:embed -->")


def test_adapt_ends_with_youtube_and_telegram_links():
    body = make_pub().adapt(make_article(), yt_context()).body
    tail = body[body.index("<p>#a #b</p>"):]
    assert f'<a href="{YT_URL}"' in tail
    assert "Watch on YouTube" in tail
    assert f'<a href="{TG_URL}"' in tail
    assert "Telegram" in tail


def test_adapt_omits_telegram_link_when_channel_url_unset():
    body = make_pub(channel_url="").adapt(make_article(), yt_context()).body
    assert "t.me" not in body
    assert YT_URL in body


def test_adapt_without_context_has_no_embed_and_no_youtube_link():
    body = make_pub().adapt(make_article()).body
    assert "wp:embed" not in body
    assert "youtube" not in body.lower()
    assert body.startswith("<p>Para one</p>")


def test_adapt_without_hashtags_has_no_trailing_hashtag_paragraph():
    body = make_pub().adapt(make_article(hashtags=None), yt_context()).body
    assert "<p>Para one</p>\n<p>Para two</p>" in body
    assert "#a #b" not in body


def test_adapt_title_is_the_article_title():
    assert make_pub().adapt(make_article(), yt_context()).title == "Title"


async def test_publish_uploads_only_the_thumbnail(tmp_path):
    calls = {}

    def fake_upload(thumb_path, title, html, settings):
        calls.update(dict(thumb_path=thumb_path, title=title, html=html,
                          url=settings.wordpress_url))
        return "https://blog.example.com/?p=7"

    pub = make_pub(upload_fn=fake_upload)
    settings = make_settings(tmp_path)
    art = make_article()
    media = [MediaFile(article_id=1, file_path="short.mp4", duration_s=5, thumb_path="s.jpg"),
             MediaFile(article_id=1, file_path="long.mp4", duration_s=50, thumb_path="l.jpg")]
    url = await pub.publish(art, media, pub.adapt(art, yt_context()), settings)
    assert url == "https://blog.example.com/?p=7"
    assert calls["thumb_path"] == "l.jpg"
    assert calls["title"] == "Title"
    assert YT_URL in calls["html"]
    assert calls["url"] == "https://blog.example.com"


async def test_publish_without_media_sends_no_thumbnail(tmp_path):
    calls = {}

    def fake_upload(thumb_path, title, html, settings):
        calls["thumb_path"] = thumb_path
        return "https://blog.example.com/?p=8"

    pub = make_pub(upload_fn=fake_upload)
    art = make_article()
    await pub.publish(art, [], pub.adapt(art, yt_context()), make_settings(tmp_path))
    assert calls["thumb_path"] is None


def test_validate_warns_without_credentials(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path)
    warnings = make_pub().validate(make_article(),
                                   [MediaFile(article_id=1, file_path="v.mp4")], settings)
    assert any("wordpress" in w.lower() and "not configured" in w.lower() for w in warnings)


def test_validate_warns_when_youtube_is_unconfigured(tmp_path):
    # The post embeds the YouTube video, so without YouTube it never publishes.
    warnings = make_pub().validate(make_article(),
                                   [MediaFile(article_id=1, file_path="v.mp4")],
                                   make_settings(tmp_path))
    assert any("youtube" in w.lower() for w in warnings)


def test_validate_is_quiet_when_youtube_is_configured(tmp_path):
    settings = make_settings(tmp_path)
    settings.youtube_token_path.write_text("{}")
    warnings = make_pub().validate(make_article(),
                                   [MediaFile(article_id=1, file_path="v.mp4")], settings)
    assert warnings == []


def test_validate_does_not_warn_about_album_or_size(tmp_path):
    # Nothing but the thumbnail is uploaded now, so upload limits are moot.
    settings = make_settings(tmp_path, media_max_bytes=100)
    settings.youtube_token_path.write_text("{}")
    media = [MediaFile(article_id=1, file_path="a.mp4", duration_s=9, size_bytes=101),
             MediaFile(article_id=1, file_path="b.mp4", duration_s=5, size_bytes=10)]
    warnings = make_pub().validate(make_article(), media, settings)
    assert warnings == []


def test_settings_wordpress_defaults(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path)
    assert settings.wordpress_url == ""
    assert settings.wordpress_username == ""
    assert settings.wordpress_app_password == ""
    assert settings.wordpress_status == "publish"


def test_configured_requires_url_username_and_app_password(tmp_path):
    pub = make_pub()
    assert pub.configured(make_settings(tmp_path))
    assert not pub.configured(make_settings(tmp_path, wordpress_app_password=""))
    assert not pub.configured(Settings(_env_file=None, data_dir=tmp_path))


# --- connection check -------------------------------------------------

def mock_client(handler, **kw):
    import httpx
    return httpx.Client(transport=httpx.MockTransport(handler), **kw)


def users_me_handler(payload, status=200):
    import httpx

    def handler(request):
        assert request.url.path == "/wp-json/wp/v2/users/me"
        assert request.url.params.get("context") == "edit"
        return httpx.Response(status, json=payload)

    return handler


def test_check_connection_reports_the_user_and_publish_capability(tmp_path):
    from telecast.publish.wordpress import check_connection

    client = mock_client(users_me_handler(
        {"name": "telecast-bot", "capabilities": {"publish_posts": True}}))
    result = check_connection(make_settings(tmp_path), client=client)
    assert result.ok
    assert "telecast-bot" in result.message
    assert "publish" in result.message.lower()


def test_check_connection_fails_when_the_user_cannot_publish(tmp_path):
    from telecast.publish.wordpress import check_connection

    client = mock_client(users_me_handler(
        {"name": "reader", "capabilities": {"read": True}}))
    result = check_connection(make_settings(tmp_path), client=client)
    assert not result.ok
    assert "reader" in result.message
    assert "cannot publish" in result.message.lower()


def test_check_connection_reports_rejected_credentials(tmp_path):
    from telecast.publish.wordpress import check_connection

    client = mock_client(users_me_handler({"code": "incorrect_password"}, status=401))
    result = check_connection(make_settings(tmp_path), client=client)
    assert not result.ok
    assert "credentials" in result.message.lower()


def test_check_connection_reports_a_missing_rest_api(tmp_path):
    from telecast.publish.wordpress import check_connection

    client = mock_client(users_me_handler({}, status=404))
    result = check_connection(make_settings(tmp_path), client=client)
    assert not result.ok
    assert "rest api" in result.message.lower()
    assert "permalink" in result.message.lower()


def test_check_connection_reports_other_http_errors(tmp_path):
    from telecast.publish.wordpress import check_connection

    client = mock_client(users_me_handler({}, status=503))
    result = check_connection(make_settings(tmp_path), client=client)
    assert not result.ok
    assert "503" in result.message


def test_check_connection_reports_an_unreachable_site(tmp_path):
    import httpx

    from telecast.publish.wordpress import check_connection

    def handler(request):
        raise httpx.ConnectError("name resolution failed", request=request)

    result = check_connection(make_settings(tmp_path), client=mock_client(handler))
    assert not result.ok
    assert "blog.example.com" in result.message
    assert "reach" in result.message.lower()


def test_check_connection_says_so_when_unconfigured(tmp_path):
    from telecast.publish.wordpress import check_connection

    result = check_connection(Settings(_env_file=None, data_dir=tmp_path))
    assert not result.ok
    assert "not configured" in result.message.lower()


def test_check_connection_warns_about_a_plain_http_url(tmp_path):
    from telecast.publish.wordpress import check_connection

    client = mock_client(users_me_handler(
        {"name": "bot", "capabilities": {"publish_posts": True}}))
    settings = make_settings(tmp_path, wordpress_url="http://blog.example.com")
    result = check_connection(settings, client=client)
    assert "https" in result.message.lower()


def test_publisher_exposes_the_connection_check(tmp_path):
    client = mock_client(users_me_handler(
        {"name": "bot", "capabilities": {"publish_posts": True}}))
    result = make_pub().check_connection(make_settings(tmp_path), client=client)
    assert result.ok
