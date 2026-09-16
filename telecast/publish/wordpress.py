"""WordPress publisher: an article page built around the YouTube upload.

The video is not re-uploaded here — the post embeds the YouTube video the
`youtube` publisher already produced, which is why this publisher declares
`depends_on = "youtube"` and the worker only claims it once that sibling
target is PUBLISHED.
"""

import asyncio
import mimetypes
from html import escape
from pathlib import Path
from typing import Callable

from telecast.config import Settings
from telecast.models import Article, MediaFile
from telecast.publish.base import Adapted, Context
from telecast.publish.youtube import pick_video


def _upload_media(client, api: str, file_path: str) -> dict:
    path = Path(file_path)
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    resp = client.post(f"{api}/media", content=path.read_bytes(), headers={
        "Content-Disposition": f'attachment; filename="{path.name}"',
        "Content-Type": mime,
    }, timeout=600)
    resp.raise_for_status()
    return resp.json()


def _real_upload(thumb_path: str | None, title: str, html: str,
                 settings: Settings) -> str:
    import httpx

    api = settings.wordpress_url.rstrip("/") + "/wp-json/wp/v2"
    with httpx.Client(auth=(settings.wordpress_username,
                            settings.wordpress_app_password)) as client:
        body = {
            "title": title,
            "content": html,
            "status": settings.wordpress_status,
        }
        if thumb_path:
            # The poster frame doubles as the post's featured image, so
            # archive and card views still show something.
            body["featured_media"] = _upload_media(client, api, thumb_path)["id"]
        resp = client.post(f"{api}/posts", json=body)
        resp.raise_for_status()
        return resp.json()["link"]


def _embed_block(url: str) -> str:
    """WordPress' canonical oEmbed block — renders the YouTube player with
    no plugin, and stays editable in the block editor."""
    safe = escape(url, quote=True)
    return (
        '<!-- wp:embed {"url":"%s","type":"video","providerNameSlug":"youtube",'
        '"responsive":true,"className":"wp-embed-aspect-16-9 wp-has-aspect-ratio"} -->\n'
        '<figure class="wp-block-embed is-type-video is-provider-youtube '
        'wp-block-embed-youtube wp-embed-aspect-16-9 wp-has-aspect-ratio">'
        '<div class="wp-block-embed__wrapper">\n%s\n</div></figure>\n'
        "<!-- /wp:embed -->"
    ) % (safe, safe)


def _link(url: str, text: str) -> str:
    return f'<p><a href="{escape(url, quote=True)}" target="_blank" rel="noopener">{text}</a></p>'


class WordPressPublisher:
    name = "wordpress"
    depends_on = "youtube"

    def __init__(self, upload_fn: Callable | None = None, channel_url: str = ""):
        self._upload_fn = upload_fn or _real_upload
        self._channel_url = channel_url

    def configured(self, settings: Settings) -> bool:
        return bool(settings.wordpress_url and settings.wordpress_username
                    and settings.wordpress_app_password)

    def validate(self, article: Article, media: list[MediaFile], settings: Settings) -> list[str]:
        warnings = []
        if not self.configured(settings):
            warnings.append("wordpress not configured — set TELECAST_WORDPRESS_URL, "
                            "TELECAST_WORDPRESS_USERNAME, TELECAST_WORDPRESS_APP_PASSWORD")
        if not settings.youtube_token_path.exists():
            warnings.append("the post embeds the youtube video — without a configured "
                            "youtube publisher it waits and never publishes")
        return warnings

    def adapt(self, article: Article, context: Context | None = None) -> Adapted:
        youtube_url = (context.published if context else {}).get("youtube", "")

        blocks = []
        if youtube_url:
            blocks.append(_embed_block(youtube_url))
        paragraphs = [p.strip() for p in (article.final_text or "").split("\n") if p.strip()]
        if article.hashtags and article.hashtags.strip():
            paragraphs.append(article.hashtags.strip())
        if paragraphs:
            blocks.append("\n".join(f"<p>{escape(p)}</p>" for p in paragraphs))
        if youtube_url:
            blocks.append(_link(youtube_url, "Watch on YouTube"))
        if self._channel_url:
            blocks.append(_link(self._channel_url, "Join us on Telegram"))

        return Adapted(title=article.title or "", body="\n".join(blocks))

    async def publish(self, article: Article, media: list[MediaFile], adapted: Adapted,
                      settings: Settings) -> str:
        thumb_path = pick_video(media).thumb_path if media else None
        return await asyncio.to_thread(
            self._upload_fn,
            thumb_path,
            adapted.title,
            adapted.body,
            settings,
        )
