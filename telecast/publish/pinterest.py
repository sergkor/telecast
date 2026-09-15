import asyncio
import base64
import json
import time
from typing import Callable

from telecast.config import Settings
from telecast.models import Article, MediaFile
from telecast.publish.base import Adapted, truncate
from telecast.publish.youtube import pick_video

TITLE_LIMIT = 100
DESCRIPTION_LIMIT = 800
PINTEREST_MAX_BYTES = 2 * 1024 * 1024 * 1024
PINTEREST_MAX_SECONDS = 15 * 60

API = "https://api.pinterest.com/v5"
AUTH_URL = "https://www.pinterest.com/oauth/"
SCOPES = "boards:read,pins:read,pins:write"


def _refresh_access_token(settings: Settings) -> str:
    import httpx

    token = json.loads(settings.pinterest_token_path.read_text())
    resp = httpx.post(f"{API}/oauth/token", data={
        "grant_type": "refresh_token",
        "refresh_token": token["refresh_token"],
    }, auth=(settings.pinterest_app_id, settings.pinterest_app_secret))
    resp.raise_for_status()
    data = resp.json()
    if "access_token" not in data:
        raise RuntimeError(f"pinterest token refresh failed: {data}")
    # continuous refresh may rotate the refresh token — persist it
    token["refresh_token"] = data.get("refresh_token", token["refresh_token"])
    settings.pinterest_token_path.write_text(json.dumps(token))
    return data["access_token"]


def _real_upload(file_path: str, thumb_path: str, title: str, description: str,
                 settings: Settings) -> str:
    import httpx

    access_token = _refresh_access_token(settings)
    headers = {"Authorization": f"Bearer {access_token}"}

    resp = httpx.post(f"{API}/media", headers=headers, json={"media_type": "video"})
    resp.raise_for_status()
    reg = resp.json()
    media_id, upload_url = reg["media_id"], reg["upload_url"]

    with open(file_path, "rb") as f:
        httpx.post(upload_url, data=reg["upload_parameters"],
                   files={"file": f}, timeout=600).raise_for_status()

    deadline = time.monotonic() + 600
    while True:
        resp = httpx.get(f"{API}/media/{media_id}", headers=headers)
        resp.raise_for_status()
        status = resp.json().get("status")
        if status == "succeeded":
            break
        if status == "failed":
            raise RuntimeError(f"pinterest media processing failed: {media_id}")
        if time.monotonic() > deadline:
            raise RuntimeError("pinterest media processing timed out")
        time.sleep(5)

    cover = base64.b64encode(open(thumb_path, "rb").read()).decode()
    resp = httpx.post(f"{API}/pins", headers=headers, json={
        "board_id": settings.pinterest_board_id,
        "title": title,
        "description": description,
        "media_source": {
            "source_type": "video_id",
            "media_id": media_id,
            "cover_image_content_type": "image/jpeg",
            "cover_image_data": cover,
        },
    })
    resp.raise_for_status()
    return f"https://www.pinterest.com/pin/{resp.json()['id']}/"


class PinterestPublisher:
    name = "pinterest"

    def __init__(self, upload_fn: Callable | None = None):
        self._upload_fn = upload_fn or _real_upload

    def validate(self, article: Article, media: list[MediaFile], settings: Settings) -> list[str]:
        warnings = []
        if not media:
            warnings.append("no video attached — cannot publish")
        if not settings.pinterest_board_id:
            warnings.append("pinterest board not set — set TELECAST_PINTEREST_BOARD_ID")
        if not settings.pinterest_token_path.exists():
            warnings.append("pinterest token missing — run `telecast auth pinterest`")
        if len(media) > 1:
            warnings.append("album: only the longest video will be uploaded to pinterest")
        max_bytes = min(settings.media_max_bytes, PINTEREST_MAX_BYTES)
        max_seconds = min(settings.media_max_seconds, PINTEREST_MAX_SECONDS)
        for m in media:
            if not m.thumb_path:
                warnings.append("no thumbnail — pinterest requires a cover image, publish will fail")
            if m.size_bytes > max_bytes:
                warnings.append(f"video size {m.size_bytes} exceeds cap — may fail on pinterest")
            if m.duration_s > max_seconds:
                warnings.append(f"video duration {m.duration_s:.0f}s exceeds cap — may fail on pinterest")
        return warnings

    def adapt(self, article: Article) -> Adapted:
        parts = [article.final_text, article.hashtags]
        description = "\n\n".join(p.strip() for p in parts if p and p.strip())
        return Adapted(title=truncate(article.title or "", TITLE_LIMIT),
                       body=truncate(description, DESCRIPTION_LIMIT))

    async def publish(self, article: Article, media: list[MediaFile], adapted: Adapted,
                      settings: Settings) -> str:
        video = pick_video(media)
        if not video.thumb_path:
            raise RuntimeError("pinterest requires a cover image but the video has no thumbnail")
        return await asyncio.to_thread(
            self._upload_fn,
            video.file_path,
            video.thumb_path,
            adapted.title,
            adapted.body,
            settings,
        )
