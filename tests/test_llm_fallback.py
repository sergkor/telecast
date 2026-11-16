import pytest

from telecast.pipeline.llm import (
    FallbackLLM,
    GeminiError,
    GeminiQuotaError,
    GeminiTransientError,
    LLMError,
    LLMQuotaError,
    RealAnthropicClient,
    _extract_json,
)
from tests.fakes import FakeGemini


async def test_primary_success_never_touches_fallback():
    primary = FakeGemini(responses=[{"a": 1}])
    fallback = FakeGemini(responses=[{"b": 2}])
    llm = FallbackLLM(primary, fallback, fallback_model="claude-x")

    assert await llm.generate_json("gemini-m", "p") == {"a": 1}
    assert primary.calls == [("gemini-m", "p")]
    assert fallback.calls == []


@pytest.mark.parametrize("error", [
    GeminiError("bad"), GeminiQuotaError("quota"), GeminiTransientError("5xx"),
])
async def test_primary_failure_falls_back_with_fallback_model(error):
    primary = FakeGemini(error=error)
    fallback = FakeGemini(responses=[{"b": 2}])
    llm = FallbackLLM(primary, fallback, fallback_model="claude-x")

    assert await llm.generate_json("gemini-m", "prompt") == {"b": 2}
    assert fallback.calls == [("claude-x", "prompt")]


async def test_every_call_tries_primary_first_again():
    primary = FakeGemini(error=GeminiQuotaError("quota"))
    fallback = FakeGemini(responses=[{"b": 1}, {"b": 2}])
    llm = FallbackLLM(primary, fallback, fallback_model="claude-x")

    await llm.generate_json("gemini-m", "one")
    primary.error = None
    primary.responses = [{"a": 9}]
    assert await llm.generate_json("gemini-m", "two") == {"a": 9}
    assert len(primary.calls) == 2
    assert len(fallback.calls) == 1


async def test_both_failing_raises_fallback_error_chained_to_primary():
    primary = FakeGemini(error=GeminiError("gemini broke"))
    fallback = FakeGemini(error=LLMQuotaError("claude quota"))
    llm = FallbackLLM(primary, fallback, fallback_model="claude-x")

    with pytest.raises(LLMQuotaError) as info:
        await llm.generate_json("gemini-m", "p")
    assert isinstance(info.value.__cause__, GeminiError)
    assert "gemini broke" in str(info.value.__cause__)


def test_gemini_error_names_are_aliases_of_llm_errors():
    assert GeminiError is LLMError
    assert GeminiQuotaError is LLMQuotaError
    assert issubclass(GeminiTransientError, LLMError)


@pytest.mark.parametrize("text", [
    '{"a": 1}',
    '  {"a": 1}\n',
    '```json\n{"a": 1}\n```',
    '```\n{"a": 1}\n```',
    'Here you go:\n{"a": 1}',
])
def test_extract_json_tolerates_fences_and_prose(text):
    assert _extract_json(text) == {"a": 1}


def test_extract_json_rejects_garbage():
    with pytest.raises(LLMError):
        _extract_json("no json here")


class _Block:
    def __init__(self, type, text=""):
        self.type = type
        self.text = text


class _Response:
    def __init__(self, text, stop_reason="end_turn"):
        self.content = [_Block("thinking"), _Block("text", text)]
        self.stop_reason = stop_reason
        self.stop_details = None


class _FakeMessages:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.kwargs = None

    async def create(self, **kwargs):
        self.kwargs = kwargs
        if self.error is not None:
            raise self.error
        return self.response


def _client_with(messages):
    client = RealAnthropicClient.__new__(RealAnthropicClient)
    client._messages = messages
    return client


async def test_anthropic_client_parses_text_block_and_passes_model():
    messages = _FakeMessages(response=_Response('```json\n{"k": "v"}\n```'))
    client = _client_with(messages)

    assert await client.generate_json("claude-x", "hello") == {"k": "v"}
    assert messages.kwargs["model"] == "claude-x"
    assert messages.kwargs["messages"] == [{"role": "user", "content": "hello"}]


async def test_anthropic_client_refusal_is_llm_error():
    client = _client_with(_FakeMessages(response=_Response("", stop_reason="refusal")))
    with pytest.raises(LLMError):
        await client.generate_json("claude-x", "hello")


async def test_anthropic_client_maps_sdk_errors():
    import httpx2
    import anthropic

    req = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")

    def status(code):
        resp = httpx2.Response(code, request=req)
        return anthropic.APIStatusError("x", response=resp, body=None)

    cases = [
        (anthropic.RateLimitError("x", response=httpx2.Response(429, request=req), body=None),
         LLMQuotaError),
        (status(503), GeminiTransientError),
        (anthropic.APIConnectionError(request=req), GeminiTransientError),
        (status(400), LLMError),
    ]
    for error, expected in cases:
        client = _client_with(_FakeMessages(error=error))
        with pytest.raises(expected):
            await client.generate_json("claude-x", "hello")
