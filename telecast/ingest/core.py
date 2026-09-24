import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger
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
    checksum: str | None = None


@dataclass
class IncomingPost:
    channel: str
    message_id: int
    grouped_id: int | None
    text: str
    url: str
    videos: list[IncomingVideo] = field(default_factory=list)


def file_checksum(path: Path | str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def find_duplicate(session: Session, videos: list[IncomingVideo]) -> Article | None:
    """The article already holding every one of these videos, if the post adds
    no new media. An album with at least one unseen video is new content."""
    checksums = {v.checksum for v in videos}
    if not checksums or None in checksums:
        return None
    rows = session.exec(
        select(MediaFile.checksum, MediaFile.article_id)
        .where(MediaFile.checksum.in_(checksums))
    ).all()
    if {c for c, _ in rows} != checksums:
        return None
    return session.get(Article, min(a for _, a in rows))


def _discard_files(session: Session, videos: list[IncomingVideo]) -> None:
    # never delete a path an existing MediaFile still points at (a re-download
    # of an already-ingested message lands on the same file name)
    for v in videos:
        for path in (v.file_path, v.thumb_path):
            if not path:
                continue
            referenced = session.exec(
                select(MediaFile.id).where(
                    (MediaFile.file_path == path) | (MediaFile.thumb_path == path))
            ).first()
            if referenced is None:
                Path(path).unlink(missing_ok=True)


def backfill_checksums(session: Session) -> int:
    """Fill `checksum` for media ingested before dedupe existed. Rows whose
    file is gone are left NULL. Returns the number of rows filled."""
    rows = session.exec(select(MediaFile).where(MediaFile.checksum.is_(None))).all()
    count = 0
    for media in rows:
        if not Path(media.file_path).is_file():
            continue
        try:
            media.checksum = file_checksum(media.file_path)
        except OSError as e:
            logger.warning(f"checksum failed for {media.file_path}: {e}")
            continue
        session.commit()
        count += 1
    return count


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
    duplicate = find_duplicate(session, post.videos)
    if duplicate is not None:
        logger.info(f"skipping {post.channel}/{post.message_id}: "
                    f"same media as article {duplicate.id}")
        _discard_files(session, post.videos)
        set_cursor(session, post.channel, post.message_id)
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
                              size_bytes=v.size_bytes, tg_file_unique_id=v.tg_file_unique_id,
                              checksum=v.checksum))
    _upsert_cursor(session, post.channel, post.message_id)
    session.commit()
    session.refresh(article)
    return article
