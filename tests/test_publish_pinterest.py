from telecast.config import Settings
from telecast.models import Article, MediaFile
from telecast.publish.pinterest import (
    DESCRIPTION_LIMIT,
    PINTEREST_MAX_SECONDS,
    TITLE_LIMIT,
    PinterestPublisher,
)


def make_article(**kw):
    defaults = dict(source_channel="@n", source_message_id=1,
                    title="Title", final_text="Body", hashtags="#a #b")
    defaults.update(kw)
    return Article(**defaults)


def test_adapt_splits_title_and_description():
    pub = PinterestPublisher()
    adapted = pub.adapt(make_article())
    assert adapted.title == "Title"
    assert adapted.body == "Body\n\n#a #b"


def test_adapt_truncates_title_and_description_to_limits():
    pub = PinterestPublisher()
    adapted = pub.adapt(make_article(title="t" * 200, final_text="x" * 1000))
    assert len(adapted.title) <= TITLE_LIMIT
    assert adapted.title.endswith("…")
    assert len(adapted.body) <= DESCRIPTION_LIMIT
    assert adapted.body.endswith("…")


async def test_publish_uploads_longest_video_with_injected_fn(tmp_path):
    calls = {}

    def fake_upload(file_path, thumb_path, title, description, settings):
        calls.update(dict(file_path=file_path, thumb_path=thumb_path,
                          title=title, description=description,
                          board_id=settings.pinterest_board_id))
        return "https://www.pinterest.com/pin/123/"

    pub = PinterestPublisher(upload_fn=fake_upload)
    settings = Settings(_env_file=None, data_dir=tmp_path, pinterest_board_id="b1")
    art = make_article()
    media = [MediaFile(article_id=1, file_path="short.mp4", duration_s=5, thumb_path="short.jpg"),
             MediaFile(article_id=1, file_path="long.mp4", duration_s=50, thumb_path="long.jpg")]
    url = await pub.publish(art, media, pub.adapt(art), settings)
    assert url == "https://www.pinterest.com/pin/123/"
    assert calls["file_path"] == "long.mp4"
    assert calls["thumb_path"] == "long.jpg"
    assert calls["title"] == "Title"
    assert calls["description"] == "Body\n\n#a #b"
    assert calls["board_id"] == "b1"


def test_validate_warns_without_token(tmp_path):
    pub = PinterestPublisher()
    settings = Settings(_env_file=None, data_dir=tmp_path, pinterest_board_id="b1")
    warnings = pub.validate(make_article(),
                            [MediaFile(article_id=1, file_path="v.mp4", thumb_path="v.jpg")],
                            settings)
    assert any("token" in w.lower() for w in warnings)


def test_validate_warns_without_media(tmp_path):
    pub = PinterestPublisher()
    settings = Settings(_env_file=None, data_dir=tmp_path)
    warnings = pub.validate(make_article(), [], settings)
    assert any("no video" in w.lower() for w in warnings)


def test_validate_warns_without_board_id(tmp_path):
    pub = PinterestPublisher()
    settings = Settings(_env_file=None, data_dir=tmp_path)
    warnings = pub.validate(make_article(),
                            [MediaFile(article_id=1, file_path="v.mp4", thumb_path="v.jpg")],
                            settings)
    assert any("board" in w.lower() for w in warnings)


def test_validate_warns_without_thumbnail(tmp_path):
    pub = PinterestPublisher()
    settings = Settings(_env_file=None, data_dir=tmp_path, pinterest_board_id="b1")
    warnings = pub.validate(make_article(),
                            [MediaFile(article_id=1, file_path="v.mp4", thumb_path=None)],
                            settings)
    assert any("cover" in w.lower() or "thumbnail" in w.lower() for w in warnings)


def test_validate_warns_on_album_and_pinterest_duration_cap(tmp_path):
    pub = PinterestPublisher()
    settings = Settings(_env_file=None, data_dir=tmp_path, pinterest_board_id="b1")
    media = [MediaFile(article_id=1, file_path="a.mp4", thumb_path="a.jpg",
                       duration_s=PINTEREST_MAX_SECONDS + 1),
             MediaFile(article_id=1, file_path="b.mp4", thumb_path="b.jpg", duration_s=5)]
    warnings = pub.validate(make_article(), media, settings)
    assert any("album" in w.lower() for w in warnings)
    assert any("duration" in w.lower() and "pinterest" in w.lower() for w in warnings)


def test_settings_pinterest_defaults(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path)
    assert settings.pinterest_board_id == ""
    assert settings.pinterest_token_path == tmp_path / "pinterest_token.json"
