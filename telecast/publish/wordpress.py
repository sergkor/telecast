"""WordPress publisher: an article page built around the YouTube upload.

The video is not re-uploaded here — the post embeds the YouTube video the
`youtube` publisher already produced, which is why this publisher declares
`depends_on = "youtube"` and the worker only claims it once that sibling
target is PUBLISHED.
"""

import asyncio
import mimetypes
from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from telecast.config import Settings
from telecast.models import Article, MediaFile
from telecast.publish.base import Adapted, Context
from telecast.publish.youtube import pick_video


@dataclass
class CheckResult:
    """Outcome of a connection check — `message` is shown as-is in the UI."""

    ok: bool
    message: str


NOT_CONFIGURED = ("not configured — set TELECAST_WORDPRESS_URL, "
                  "TELECAST_WORDPRESS_USERNAME, TELECAST_WORDPRESS_APP_PASSWORD")


def _api_root(settings: Settings) -> str:
    return settings.wordpress_url.rstrip("/") + "/wp-json/wp/v2"


def check_connection(settings: Settings, client=None) -> CheckResult:
    """Ask WordPress who we are — the cheapest read-only proof that the URL,
    the REST API and the application password all work, and that the user is
    allowed to publish. `client` is injected by tests."""
    import httpx

    if not (settings.wordpress_url and settings.wordpress_username
            and settings.wordpress_app_password):
        return CheckResult(False, NOT_CONFIGURED)

    host = urlsplit(settings.wordpress_url).netloc or settings.wordpress_url
    owned = client is None
    client = client or httpx.Client(
        auth=(settings.wordpress_username, settings.wordpress_app_password),
        timeout=15, follow_redirects=True)
    try:
        resp = client.get(f"{_api_root(settings)}/users/me", params={"context": "edit"})
    except httpx.RequestError as exc:
        return CheckResult(False, f"cannot reach {host}: {exc}")
    finally:
        if owned:
            client.close()

    if resp.status_code in (401, 403):
        return CheckResult(False, "credentials rejected — check the username and "
                                  "the application password")
    if resp.status_code == 404:
        return CheckResult(False, "REST API not found — check the URL and that "
                                  "permalinks are not set to 'Plain'")
    if resp.status_code >= 400:
        return CheckResult(False, f"WordPress returned HTTP {resp.status_code}")

    try:
        data = resp.json()
    except ValueError:
        return CheckResult(False, f"{host} did not answer with JSON — is that a "
                                  "WordPress REST API?")
    name = data.get("name") or settings.wordpress_username
    if not (data.get("capabilities") or {}).get("publish_posts"):
        return CheckResult(False, f"connected as {name}, but this user cannot "
                                  "publish posts — give it the Author role")
    return CheckResult(True, _with_https_warning(
        f"connected as {name} — can publish posts", settings))


def _with_https_warning(message: str, settings: Settings) -> str:
    if urlsplit(settings.wordpress_url).scheme == "https":
        return message
    return (message + " (warning: the site is not served over HTTPS — the "
            "application password travels in the clear)")


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

    api = _api_root(settings)
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

    def check_connection(self, settings: Settings, client=None) -> CheckResult:
        return check_connection(settings, client=client)

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
