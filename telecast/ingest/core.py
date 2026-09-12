from dataclasses import dataclass, field

from sqlmodel import Session, select

from telecast.models import Article, ChannelCursor, MediaFile


@dataclass
class IncomingVideo:
    file_path: str
    mime_type: str
    duration_s: float
    width: int
    height: int
    size_bytes: int
    tg_file_unique_id: str
    thumb_path: str | None = None


@dataclass
class IncomingPost:
    channel: str
    message_id: int
    grouped_id: int | None
    text: str
    url: str
    videos: list[IncomingVideo] = field(default_factory=list)


def get_cursor(session: Session, channel: str) -> int:
    row = session.get(ChannelCursor, channel.casefold())
    return row.last_message_id if row else 0


def _upsert_cursor(session: Session, channel: str, message_id: int) -> None:
    channel = channel.casefold()
    row = session.get(ChannelCursor, channel)
    if row is None:
        session.add(ChannelCursor(channel=channel, last_message_id=message_id))
    elif message_id > row.last_message_id:
        row.last_message_id = message_id


def set_cursor(session: Session, channel: str, message_id: int) -> None:
    _upsert_cursor(session, channel, message_id)
    session.commit()


def ingest_post(post: IncomingPost, session: Session) -> Article | None:
    if not post.videos:
        set_cursor(session, post.channel, post.message_id)
        return None
    exists = session.exec(
        select(Article).where(
            Article.source_channel == post.channel,
            Article.source_message_id == post.message_id,
        )
    ).first()
    if exists is not None:
        return None
    if post.grouped_id is not None:
        exists_group = session.exec(
            select(Article).where(
                Article.source_channel == post.channel,
                Article.grouped_id == post.grouped_id,
            )
        ).first()
        if exists_group is not None:
            return None

    article = Article(
        source_channel=post.channel,
        source_message_id=post.message_id,
        grouped_id=post.grouped_id,
        source_url=post.url,
        original_text=post.text or "",
    )
    session.add(article)
    session.flush()
    for v in post.videos:
        session.add(MediaFile(article_id=article.id, file_path=v.file_path,
                              thumb_path=v.thumb_path, mime_type=v.mime_type,
                              duration_s=v.duration_s, width=v.width, height=v.height,
                              size_bytes=v.size_bytes, tg_file_unique_id=v.tg_file_unique_id))
    _upsert_cursor(session, post.channel, post.message_id)
    session.commit()
    session.refresh(article)
    return article
