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

    def configured(self, settings: Settings) -> bool: ...

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


def _is_configured(publisher: Publisher, settings: Settings) -> bool:
    # A publisher that does not declare `configured` has nothing to
    # configure — treat it as ready.
    check = getattr(publisher, "configured", None)
    return True if check is None else bool(check(settings))


def available(settings: Settings) -> list[str]:
    """Registered publishers whose credentials and config are present."""
    return [n for n, p in _registry.items() if _is_configured(p, settings)]


def unconfigured(settings: Settings) -> set[str]:
    """Registered publishers missing config — excluded from review and
    from an article's final state."""
    return {n for n, p in _registry.items() if not _is_configured(p, settings)}


def clear() -> None:
    _registry.clear()
