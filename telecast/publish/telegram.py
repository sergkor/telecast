import json
from pathlib import Path

import httpx

from telecast.config import Settings
from telecast.models import Article, MediaFile
from telecast.publish.base import Adapted, truncate

CAPTION_LIMIT = 1024


class TelegramPublishError(Exception):
    pass


class TelegramPublisher:
    name = "telegram"

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None):
        self._transport = transport

    def validate(self, article: Article, media: list[MediaFile], settings: Settings) -> list[str]:
        warnings = []
        if not media:
            warnings.append("no video attached — cannot publish")
        for m in media:
            if m.size_bytes > settings.media_max_bytes:
                warnings.append(f"video size {m.size_bytes} exceeds cap — may fail on telegram")
            if m.duration_s > settings.media_max_seconds:
                warnings.append(f"video duration {m.duration_s:.0f}s exceeds cap — may fail on telegram")
        return warnings

    def adapt(self, article: Article) -> Adapted:
        title = article.title or ""
        body = article.final_text or ""
        caption = f"{title}\n\n{body}".strip()
        return Adapted(title=title, body=truncate(caption, CAPTION_LIMIT))

    async def publish(self, article: Article, media: list[MediaFile], adapted: Adapted,
                      settings: Settings) -> str:
        api = f"https://api.telegram.org/bot{settings.bot_token}"
        async with httpx.AsyncClient(transport=self._transport, timeout=600) as client:
            if len(media) == 1:
                path = Path(media[0].file_path)
                with path.open("rb") as fh:
                    resp = await client.post(
                        f"{api}/sendVideo",
                        data={"chat_id": settings.dest_channel, "caption": adapted.body},
                        files={"video": (path.name, fh, media[0].mime_type)},
                    )
            else:
                input_media = []
                files = {}
                try:
                    for i, m in enumerate(media):
                        key = f"video{i}"
                        item = {"type": "video", "media": f"attach://{key}"}
                        if i == 0:
                            item["caption"] = adapted.body
                        input_media.append(item)
                        files[key] = (Path(m.file_path).name, Path(m.file_path).open("rb"), m.mime_type)
                    resp = await client.post(
                        f"{api}/sendMediaGroup",
                        data={"chat_id": settings.dest_channel, "media": json.dumps(input_media)},
                        files=files,
                    )
                finally:
                    for _name, fh, _mime in files.values():
                        fh.close()
        payload = resp.json()
        if not payload.get("ok"):
            raise TelegramPublishError(f"telegram API error: {payload}")
        result = payload["result"]
        message_id = result[0]["message_id"] if isinstance(result, list) else result["message_id"]
        channel = settings.dest_channel.lstrip("@")
        return f"https://t.me/{channel}/{message_id}"
