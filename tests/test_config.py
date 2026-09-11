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
