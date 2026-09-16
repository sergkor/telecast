class FakeGemini:
    def __init__(self, responses=None, error=None):
        self.responses = list(responses or [])
        self.error = error
        self.calls: list[tuple[str, str]] = []

    async def generate_json(self, model: str, prompt: str) -> dict:
        self.calls.append((model, prompt))
        if self.error is not None:
            raise self.error
        return self.responses.pop(0)


class StubPublisher:
    """Registry stand-in: name, optional config state, no-op publish."""

    def __init__(self, name, configured=True, url="https://example.test/1"):
        self.name = name
        self._configured = configured
        self._url = url

    def configured(self, settings) -> bool:
        return self._configured

    def validate(self, article, media, settings):
        return []

    def adapt(self, article, context=None):
        from telecast.publish.base import Adapted

        return Adapted(title=article.title or "", body=article.final_text or "")

    async def publish(self, article, media, adapted, settings):
        return self._url
