import enum
from datetime import datetime, timezone

from sqlmodel import Field, SQLModel, UniqueConstraint


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ArticleState(str, enum.Enum):
    INGESTED = "INGESTED"
    TRANSLATING = "TRANSLATING"
    TRANSLATED = "TRANSLATED"
    ENHANCING = "ENHANCING"
    ENHANCED = "ENHANCED"
    PENDING_REVIEW = "PENDING_REVIEW"
    PUBLISHED = "PUBLISHED"
    FAILED_TRANSLATE = "FAILED_TRANSLATE"
    FAILED_ENHANCE = "FAILED_ENHANCE"
    DISCARDED = "DISCARDED"


WORKING_STATES = {
    ArticleState.TRANSLATING: ArticleState.INGESTED,
    ArticleState.ENHANCING: ArticleState.TRANSLATED,
}
FAILED_STATES = {ArticleState.FAILED_TRANSLATE, ArticleState.FAILED_ENHANCE}


class TargetStatus(str, enum.Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    PUBLISHING = "PUBLISHING"
    PUBLISHED = "PUBLISHED"
    SKIPPED = "SKIPPED"
    FAILED = "FAILED"


class Article(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("source_channel", "source_message_id"),)

    id: int | None = Field(default=None, primary_key=True)
    source_channel: str
    source_message_id: int
    grouped_id: int | None = None
    source_url: str = ""
    original_text: str = ""
    detected_language: str | None = None
    translated_text: str | None = None
    enhanced_text: str | None = None
    final_text: str | None = None
    final_text_edited: bool = False
    title: str | None = None
    hashtags: str = ""
    state: ArticleState = Field(default=ArticleState.INGESTED, index=True)
    error: str | None = None
    claimed_at: datetime | None = None
    approved_at: datetime | None = None
    scheduled_at: datetime | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class MediaFile(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    article_id: int = Field(foreign_key="article.id", index=True)
    file_path: str
    thumb_path: str | None = None
    mime_type: str = "video/mp4"
    duration_s: float = 0.0
    width: int = 0
    height: int = 0
    size_bytes: int = 0
    tg_file_unique_id: str = ""
    # sha256 of the file contents; identical videos reposted anywhere dedupe on it
    checksum: str | None = Field(default=None, index=True)


class PublishTarget(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    article_id: int = Field(foreign_key="article.id", index=True)
    platform: str
    status: TargetStatus = Field(default=TargetStatus.PENDING, index=True)
    adapted_text: str | None = None
    external_url: str | None = None
    error: str | None = None
    published_at: datetime | None = None


class ChannelCursor(SQLModel, table=True):
    channel: str = Field(primary_key=True)
    last_message_id: int = 0
