from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="TELECAST_", extra="ignore")

    telegram_api_id: int = 0
    telegram_api_hash: str = ""
    source_channels: str = ""
    bot_token: str = ""
    dest_channel: str = ""
    gemini_api_key: str = ""
    translate_model: str = "gemini-flash-latest"
    enhance_model: str = "gemini-flash-latest"
    web_password: str = "change-me"
    secret_key: str = "dev-secret-change-me"
    data_dir: Path = Path("data")
    media_max_bytes: int = 512 * 1024 * 1024
    media_max_seconds: int = 3600
    youtube_privacy: str = "public"
    tiktok_client_key: str = ""
    tiktok_client_secret: str = ""
    tiktok_privacy: str = "SELF_ONLY"
    pinterest_app_id: str = ""
    pinterest_app_secret: str = ""
    pinterest_board_id: str = ""
    enhance_prompt_path: Path = Path("prompts/enhance.md")
    stale_claim_minutes: int = 15
    recent_posts: int = 5
    publish_delay_minutes: int = 5

    @property
    def source_channel_list(self) -> list[str | int]:
        # Telethon resolves numeric channel ids only when passed as int;
        # a "#" prefix (as copied from some Telegram clients) is noise.
        items: list[str | int] = []
        for raw in self.source_channels.split(","):
            c = raw.strip().lstrip("#")
            if not c:
                continue
            items.append(int(c) if c.lstrip("-").isdigit() else c)
        return items

    @property
    def media_dir(self) -> Path:
        return self.data_dir / "media"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "articles.db"

    @property
    def youtube_token_path(self) -> Path:
        return self.data_dir / "youtube_token.json"

    @property
    def tiktok_token_path(self) -> Path:
        return self.data_dir / "tiktok_token.json"

    @property
    def pinterest_token_path(self) -> Path:
        return self.data_dir / "pinterest_token.json"

    @property
    def telethon_session_path(self) -> Path:
        return self.data_dir / "telethon.session"
