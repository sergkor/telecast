from dataclasses import dataclass
from typing import Protocol

from telecast.config import Settings
from telecast.models import Article, MediaFile


def truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    space = cut.rfind(" ")
    if space > limit // 2:
        cut = cut[:space]
    return cut.rstrip() + "…"


@dataclass
class Adapted:
    title: str
    body: str


class Publisher(Protocol):
    name: str

    def validate(self, article: Article, media: list[MediaFile], settings: Settings) -> list[str]: ...

    def adapt(self, article: Article) -> Adapted: ...

    async def publish(
        self, article: Article, media: list[MediaFile], adapted: Adapted, settings: Settings
    ) -> str: ...


_registry: dict[str, Publisher] = {}


def register(publisher: Publisher) -> None:
    _registry[publisher.name] = publisher


def get(name: str) -> Publisher:
    return _registry[name]


def names() -> list[str]:
    return list(_registry)


def clear() -> None:
    _registry.clear()
