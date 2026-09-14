import asyncio

from loguru import logger
from sqlmodel import select
from telethon import TelegramClient, events, utils

from telecast.config import Settings
from telecast.ingest.core import IncomingPost, IncomingVideo, get_cursor, ingest_post, set_cursor
from telecast.ingest.thumbs import make_thumbnail
from telecast.models import Article

RECENT_SCAN_LIMIT = 100


def is_video_message(msg) -> bool:
    return getattr(msg, "video", None) is not None


class Ingestor:
    def __init__(self, settings: Settings, session_factory):
        self.settings = settings
        self.session_factory = session_factory
        self.client = TelegramClient(
            str(settings.telethon_session_path),
            settings.telegram_api_id,
            settings.telegram_api_hash,
        )

    async def start(self, stop_event: asyncio.Event) -> None:
        try:
            self.settings.media_dir.mkdir(parents=True, exist_ok=True)
            channels = self.settings.source_channel_list

            @self.client.on(events.Album(chats=channels))
            async def on_album(event):
                await self._handle(event.chat, event.messages)

            @self.client.on(events.NewMessage(chats=channels))
            async def on_message(event):
                if event.message.grouped_id is not None:
                    return  # handled by the Album event
                await self._handle(event.chat, [event.message])

            await self.client.start()
            for channel in channels:
                await self._backfill(channel)
            logger.info(f"ingestor listening on {channels}")
            stopper = asyncio.create_task(stop_event.wait())
            runner = asyncio.create_task(self.client.run_until_disconnected())
            await asyncio.wait({stopper, runner}, return_when=asyncio.FIRST_COMPLETED)
            await self.client.disconnect()
        except Exception:
            logger.exception("ingestor failed to start")
            return

    async def _backfill(self, channel: str | int) -> None:
        entity = await self.client.get_entity(channel)
        key = self._channel_key(entity)
        with self.session_factory() as session:
            min_id = get_cursor(session, key)
        if min_id > 0:
            groups: dict[int, list] = {}
            singles = []
            async for msg in self.client.iter_messages(entity, min_id=min_id, reverse=True):
                if msg.grouped_id:
                    groups.setdefault(msg.grouped_id, []).append(msg)
                else:
                    singles.append(msg)
            message_groups = list(groups.values()) + [[m] for m in singles]
            message_groups.sort(key=lambda msgs: msgs[0].id)
            for msgs in message_groups:
                ok = await self._handle(entity, msgs)
                if not ok:
                    break
        await self._ingest_recent(entity, key)

    async def _ingest_recent(self, entity, key: str, count: int | None = None) -> None:
        """Ingest the last `count` video posts (album = one post) that are
        not in the DB yet, then advance the cursor to the newest message."""
        if count is None:
            count = self.settings.recent_posts
        msgs = await self.client.get_messages(entity, limit=RECENT_SCAN_LIMIT)
        if not msgs:
            return
        groups: dict[int, list] = {}
        singles = []
        for m in msgs:  # newest first
            if m.grouped_id:
                groups.setdefault(m.grouped_id, []).append(m)
            else:
                singles.append(m)
        posts = [sorted(g, key=lambda m: m.id) for g in groups.values()]
        posts += [[m] for m in singles]
        posts = [p for p in posts if any(is_video_message(m) for m in p)]
        posts.sort(key=lambda p: p[0].id)
        for post in posts[-count:]:
            if self._already_ingested(key, post):
                continue
            ok = await self._handle(entity, post)
            if not ok:
                return  # keep cursor so the failed post is retried next start
        with self.session_factory() as session:
            set_cursor(session, key, msgs[0].id)

    def _already_ingested(self, key: str, msgs) -> bool:
        first = msgs[0]
        query = select(Article).where(Article.source_channel == key)
        if first.grouped_id is not None:
            query = query.where(Article.grouped_id == first.grouped_id)
        else:
            query = query.where(Article.source_message_id == first.id)
        with self.session_factory() as session:
            return session.exec(query).first() is not None

    @staticmethod
    def _channel_key(chat) -> str:
        # "@username" for public channels; "-100<id>" peer-id form otherwise,
        # so cursor keys match however the channel was configured.
        if getattr(chat, "username", None):
            return f"@{chat.username}"
        return str(utils.get_peer_id(chat))

    async def _handle(self, chat, msgs) -> bool:
        channel = self._channel_key(chat)
        try:
            post = await self._messages_to_post(channel, msgs)
            with self.session_factory() as session:
                article = ingest_post(post, session)
                article_id = article.id if article else None
                set_cursor(session, channel, max(m.id for m in msgs))
        except Exception:
            logger.exception(f"ingest failed for {channel}/{msgs[0].id}")
            return False
        if article_id is not None:
            logger.info(f"ingested article {article_id} from {channel}/{post.message_id}")
        return True

    async def _messages_to_post(self, channel: str, msgs) -> IncomingPost:
        first = msgs[0]
        text = next((m.message for m in msgs if m.message), "") or ""
        videos = []
        for m in msgs:
            if not is_video_message(m):
                continue
            dest = self.settings.media_dir / f"{channel.lstrip('@')}_{m.id}.mp4"
            await self.client.download_media(m, file=str(dest))
            thumb = await asyncio.to_thread(make_thumbnail, dest, self.settings.media_dir)
            videos.append(IncomingVideo(
                file_path=str(dest),
                mime_type=(m.file.mime_type if m.file else None) or "video/mp4",
                duration_s=float(getattr(m.file, "duration", 0) or 0),
                width=getattr(m.file, "width", 0) or 0,
                height=getattr(m.file, "height", 0) or 0,
                size_bytes=(m.file.size if m.file else 0) or dest.stat().st_size,
                tg_file_unique_id=str(m.file.id) if m.file else "",
                thumb_path=str(thumb) if thumb else None,
            ))
        if channel.startswith("@"):
            url = f"https://t.me/{channel[1:]}/{first.id}"
        else:
            url = f"https://t.me/c/{channel.removeprefix('-100')}/{first.id}"
        return IncomingPost(
            channel=channel, message_id=first.id,
            grouped_id=first.grouped_id, text=text,
            url=url, videos=videos,
        )
