import asyncio

from loguru import logger
from telethon import TelegramClient, events

from telecast.config import Settings
from telecast.ingest.core import IncomingPost, IncomingVideo, get_cursor, ingest_post
from telecast.ingest.thumbs import make_thumbnail


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

    async def _backfill(self, channel: str) -> None:
        with self.session_factory() as session:
            min_id = get_cursor(session, channel)
        entity = await self.client.get_entity(channel)
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
            await self._handle(entity, msgs)

    async def _handle(self, chat, msgs) -> None:
        channel = f"@{chat.username}" if getattr(chat, "username", None) else str(chat.id)
        try:
            post = await self._messages_to_post(channel, msgs)
            with self.session_factory() as session:
                article = ingest_post(post, session)
        except Exception:
            logger.exception(f"ingest failed for {channel}/{msgs[0].id}")
            return
        if article:
            logger.info(f"ingested article {article.id} from {channel}/{post.message_id}")

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
        username = channel.lstrip("@")
        return IncomingPost(
            channel=channel, message_id=first.id,
            grouped_id=first.grouped_id, text=text,
            url=f"https://t.me/{username}/{first.id}", videos=videos,
        )
