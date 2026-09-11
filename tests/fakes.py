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
