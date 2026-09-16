import asyncio
import mimetypes
from pathlib import Path
from typing import Callable

from telecast.config import Settings
from telecast.models import Article, MediaFile
from telecast.publish.base import Adapted
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


def _real_upload(file_path: str, thumb_path: str | None, title: str, html: str,
                 settings: Settings) -> str:
    import httpx

    api = settings.wordpress_url.rstrip("/") + "/wp-json/wp/v2"
    with httpx.Client(auth=(settings.wordpress_username,
                            settings.wordpress_app_password)) as client:
        video = _upload_media(client, api, file_path)
        featured_media = 0
        poster = ""
        if thumb_path:
            thumb = _upload_media(client, api, thumb_path)
            featured_media = thumb["id"]
            poster = f' poster="{thumb["source_url"]}"'
        video_block = (
            "<!-- wp:video --><figure class=\"wp-block-video\">"
            f"<video controls src=\"{video['source_url']}\"{poster}></video>"
            "</figure><!-- /wp:video -->"
        )
        body = {
            "title": title,
            "content": f"{video_block}\n{html}",
            "status": settings.wordpress_status,
        }
        if featured_media:
            body["featured_media"] = featured_media
        resp = client.post(f"{api}/posts", json=body)
        resp.raise_for_status()
        return resp.json()["link"]


class WordPressPublisher:
    name = "wordpress"

    def __init__(self, upload_fn: Callable | None = None):
        self._upload_fn = upload_fn or _real_upload

    def configured(self, settings: Settings) -> bool:
        return bool(settings.wordpress_url and settings.wordpress_username
                    and settings.wordpress_app_password)

    def validate(self, article: Article, media: list[MediaFile], settings: Settings) -> list[str]:
        warnings = []
        if not media:
            warnings.append("no video attached — cannot publish")
        if not (settings.wordpress_url and settings.wordpress_username
                and settings.wordpress_app_password):
            warnings.append("wordpress not configured — set TELECAST_WORDPRESS_URL, "
                            "TELECAST_WORDPRESS_USERNAME, TELECAST_WORDPRESS_APP_PASSWORD")
        if len(media) > 1:
            warnings.append("album: only the longest video will be uploaded to wordpress")
        for m in media:
            if m.size_bytes > settings.media_max_bytes:
                warnings.append(f"video size {m.size_bytes} exceeds cap — "
                                "may exceed the wordpress upload limit")
        return warnings

    def adapt(self, article: Article) -> Adapted:
        paragraphs = [p.strip() for p in (article.final_text or "").split("\n") if p.strip()]
        if article.hashtags and article.hashtags.strip():
            paragraphs.append(article.hashtags.strip())
        html = "\n".join(f"<p>{p}</p>" for p in paragraphs)
        return Adapted(title=article.title or "", body=html)

    async def publish(self, article: Article, media: list[MediaFile], adapted: Adapted,
                      settings: Settings) -> str:
        video = pick_video(media)
        return await asyncio.to_thread(
            self._upload_fn,
            video.file_path,
            video.thumb_path,
            adapted.title,
            adapted.body,
            settings,
        )
