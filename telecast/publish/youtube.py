import asyncio
from pathlib import Path
from typing import Callable

from telecast.config import Settings
from telecast.models import Article, MediaFile
from telecast.publish.base import Adapted, truncate

TITLE_LIMIT = 100
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def pick_video(media: list[MediaFile]) -> MediaFile:
    return max(media, key=lambda m: m.duration_s)


def _real_upload(file_path: str, title: str, description: str, privacy: str,
                 token_path: Path) -> str:
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload

    creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    yt = build("youtube", "v3", credentials=creds)
    request = yt.videos().insert(
        part="snippet,status",
        body={
            "snippet": {"title": title, "description": description},
            "status": {"privacyStatus": privacy},
        },
        media_body=MediaFileUpload(file_path, resumable=True),
    )
    response = None
    while response is None:
        _status, response = request.next_chunk()
    return f"https://www.youtube.com/watch?v={response['id']}"


class YouTubePublisher:
    name = "youtube"

    def __init__(self, upload_fn: Callable | None = None, channel_url: str = ""):
        self._upload_fn = upload_fn or _real_upload
        self._channel_url = channel_url

    def validate(self, article: Article, media: list[MediaFile], settings: Settings) -> list[str]:
        warnings = []
        if not media:
            warnings.append("no video attached — cannot publish")
        if not settings.youtube_token_path.exists():
            warnings.append("youtube token missing — run `telecast auth youtube`")
        if len(media) > 1:
            warnings.append("album: only the longest video will be uploaded to youtube")
        for m in media:
            if m.size_bytes > settings.media_max_bytes:
                warnings.append(f"video size {m.size_bytes} exceeds cap — may fail on youtube")
            if m.duration_s > settings.media_max_seconds:
                warnings.append(f"video duration {m.duration_s:.0f}s exceeds cap — may fail on youtube")
        return warnings

    def adapt(self, article: Article) -> Adapted:
        title = truncate(article.title or article.final_text or "Video", TITLE_LIMIT)
        body = (article.final_text or "").strip()
        if article.hashtags:
            body = f"{body}\n\n{article.hashtags}".strip()
        if self._channel_url:
            body = f"{body}\n\nTelegram: {self._channel_url}".strip()
        return Adapted(title=title, body=body)

    async def publish(self, article: Article, media: list[MediaFile], adapted: Adapted,
                      settings: Settings) -> str:
        video = pick_video(media)
        return await asyncio.to_thread(
            self._upload_fn,
            video.file_path,
            adapted.title,
            adapted.body,
            settings.youtube_privacy,
            settings.youtube_token_path,
        )
