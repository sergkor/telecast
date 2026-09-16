import asyncio
import json
import time
from typing import Callable

from telecast.config import Settings
from telecast.models import Article, MediaFile
from telecast.publish.base import Adapted, truncate
from telecast.publish.youtube import pick_video

CAPTION_LIMIT = 2200
TIKTOK_MAX_BYTES = 4 * 1024 * 1024 * 1024
TIKTOK_MAX_SECONDS = 600

API = "https://open.tiktokapis.com/v2"
AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
SCOPES = "video.publish"
# TikTok requires chunks of 5-64 MB; the whole file may go as one chunk
# only when it is under 64 MB.
CHUNK_SIZE = 64 * 1024 * 1024


def _refresh_access_token(settings: Settings) -> str:
    import httpx

    token = json.loads(settings.tiktok_token_path.read_text())
    resp = httpx.post(f"{API}/oauth/token/", data={
        "client_key": settings.tiktok_client_key,
        "client_secret": settings.tiktok_client_secret,
        "grant_type": "refresh_token",
        "refresh_token": token["refresh_token"],
    })
    resp.raise_for_status()
    data = resp.json()
    if "access_token" not in data:
        raise RuntimeError(f"tiktok token refresh failed: {data}")
    # TikTok rotates refresh tokens — persist the new one.
    token["refresh_token"] = data.get("refresh_token", token["refresh_token"])
    settings.tiktok_token_path.write_text(json.dumps(token))
    return data["access_token"]


def _real_upload(file_path: str, caption: str, privacy: str, settings: Settings) -> str:
    import httpx

    access_token = _refresh_access_token(settings)
    headers = {"Authorization": f"Bearer {access_token}"}
    with open(file_path, "rb") as f:
        video = f.read()
    size = len(video)
    chunk_size = size if size < CHUNK_SIZE else CHUNK_SIZE
    total_chunks = max(1, size // chunk_size)

    resp = httpx.post(f"{API}/post/publish/video/init/", headers=headers, json={
        "post_info": {"title": caption, "privacy_level": privacy},
        "source_info": {
            "source": "FILE_UPLOAD",
            "video_size": size,
            "chunk_size": chunk_size,
            "total_chunk_count": total_chunks,
        },
    })
    resp.raise_for_status()
    data = resp.json()["data"]
    publish_id, upload_url = data["publish_id"], data["upload_url"]

    for i in range(total_chunks):
        start = i * chunk_size
        # the final chunk absorbs the remainder
        end = size if i == total_chunks - 1 else start + chunk_size
        httpx.put(upload_url, content=video[start:end], headers={
            "Content-Type": "video/mp4",
            "Content-Range": f"bytes {start}-{end - 1}/{size}",
        }, timeout=300).raise_for_status()

    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        resp = httpx.post(f"{API}/post/publish/status/fetch/", headers=headers,
                          json={"publish_id": publish_id})
        resp.raise_for_status()
        data = resp.json()["data"]
        status = data.get("status")
        if status == "PUBLISH_COMPLETE":
            ids = data.get("publicaly_available_post_id") or []
            return (f"https://www.tiktok.com/video/{ids[0]}" if ids
                    else f"tiktok:publish:{publish_id}")
        if status == "FAILED":
            raise RuntimeError(f"tiktok publish failed: {data.get('fail_reason')}")
        time.sleep(5)
    raise RuntimeError("tiktok publish timed out waiting for PUBLISH_COMPLETE")


class TikTokPublisher:
    name = "tiktok"

    def __init__(self, upload_fn: Callable | None = None):
        self._upload_fn = upload_fn or _real_upload

    def configured(self, settings: Settings) -> bool:
        return bool(settings.tiktok_client_key and settings.tiktok_client_secret
                    and settings.tiktok_token_path.exists())

    def validate(self, article: Article, media: list[MediaFile], settings: Settings) -> list[str]:
        warnings = []
        if not media:
            warnings.append("no video attached — cannot publish")
        if not settings.tiktok_token_path.exists():
            warnings.append("tiktok token missing — run `telecast auth tiktok`")
        if len(media) > 1:
            warnings.append("album: only the longest video will be uploaded to tiktok")
        max_bytes = min(settings.media_max_bytes, TIKTOK_MAX_BYTES)
        max_seconds = min(settings.media_max_seconds, TIKTOK_MAX_SECONDS)
        for m in media:
            if m.size_bytes > max_bytes:
                warnings.append(f"video size {m.size_bytes} exceeds cap — may fail on tiktok")
            if m.duration_s > max_seconds:
                warnings.append(f"video duration {m.duration_s:.0f}s exceeds cap — may fail on tiktok")
        return warnings

    def adapt(self, article: Article) -> Adapted:
        # TikTok has a single caption field: title, body, and hashtags
        # collapse into it.
        parts = [article.title, article.final_text, article.hashtags]
        caption = "\n\n".join(p.strip() for p in parts if p and p.strip())
        return Adapted(title=truncate(caption, CAPTION_LIMIT), body="")

    async def publish(self, article: Article, media: list[MediaFile], adapted: Adapted,
                      settings: Settings) -> str:
        video = pick_video(media)
        return await asyncio.to_thread(
            self._upload_fn,
            video.file_path,
            adapted.title,
            settings.tiktok_privacy,
            settings,
        )
