from pathlib import Path

from telecast.config import Settings


def test_settings_defaults_and_channel_list(monkeypatch):
    monkeypatch.setenv("TELECAST_SOURCE_CHANNELS", "@newsA, @newsB ,")
    s = Settings(_env_file=None)
    assert s.source_channel_list == ["@newsA", "@newsB"]
    assert s.media_max_bytes == 512 * 1024 * 1024
    assert s.media_max_seconds == 3600
    assert s.stale_claim_minutes == 15
    assert s.db_path == Path("data/articles.db")
    assert s.media_dir == Path("data/media")
    assert s.youtube_token_path == Path("data/youtube_token.json")


def test_channel_list_converts_numeric_ids_to_int(monkeypatch):
    monkeypatch.setenv(
        "TELECAST_SOURCE_CHANNELS", "@newsA, -1001687211358, #-1002222222222"
    )
    s = Settings(_env_file=None)
    assert s.source_channel_list == ["@newsA", -1001687211358, -1002222222222]


def test_model_defaults():
    s = Settings(_env_file=None)
    assert s.translate_model == "gemini-flash-latest"
    assert s.enhance_model == "gemini-flash-latest"


def test_recent_posts_default_and_override(monkeypatch):
    assert Settings(_env_file=None).recent_posts == 5
    monkeypatch.setenv("TELECAST_RECENT_POSTS", "7")
    assert Settings(_env_file=None).recent_posts == 7


def test_publish_delay_default_and_override(monkeypatch):
    assert Settings(_env_file=None).publish_delay_minutes == 5
    monkeypatch.setenv("TELECAST_PUBLISH_DELAY_MINUTES", "10")
    assert Settings(_env_file=None).publish_delay_minutes == 10
