import json
import re

from loguru import logger
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential


class LLMError(Exception):
    pass


class LLMQuotaError(LLMError):
    pass


class LLMTransientError(LLMError):
    pass


# Historical names; the pipeline and tests still refer to them.
GeminiError = LLMError
GeminiQuotaError = LLMQuotaError
GeminiTransientError = LLMTransientError


class RealGeminiClient:
    def __init__(self, api_key: str):
        from google import genai

        self._client = genai.Client(api_key=api_key)

    @retry(
        retry=retry_if_exception_type(LLMTransientError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(min=1, max=30),
        reraise=True,
    )
    async def generate_json(self, model: str, prompt: str) -> dict:
        from google.genai import errors as genai_errors

        try:
            resp = await self._client.aio.models.generate_content(
                model=model,
                contents=prompt,
                config={"response_mime_type": "application/json"},
            )
        except genai_errors.APIError as e:
            if e.code == 429:
                raise LLMQuotaError(str(e)) from e
            if e.code is not None and e.code >= 500:
                raise LLMTransientError(str(e)) from e
            raise LLMError(str(e)) from e
        try:
            return json.loads(resp.text)
        except (TypeError, ValueError) as e:
            raise LLMError(f"non-JSON response: {resp.text!r}") from e


_JSON_SYSTEM = (
    "Respond with a single JSON object and nothing else: no prose, no markdown, "
    "no code fences."
)
_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


def _extract_json(text: str) -> dict:
    """Parse a JSON object out of model text, tolerating fences or a short preamble."""
    m = _FENCE.match(text)
    body = m.group(1) if m else text.strip()
    try:
        return json.loads(body)
    except ValueError:
        pass
    start, end = body.find("{"), body.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(body[start:end + 1])
        except ValueError:
            pass
    raise LLMError(f"non-JSON response: {text!r}")


class RealAnthropicClient:
    """Same generate_json contract as RealGeminiClient, backed by the Claude API.

    The SDK already retries 429/5xx/connection errors with backoff, so no tenacity here.
    """

    def __init__(self, api_key: str):
        import anthropic

        self._messages = anthropic.AsyncAnthropic(api_key=api_key).messages

    async def generate_json(self, model: str, prompt: str) -> dict:
        import anthropic

        try:
            resp = await self._messages.create(
                model=model,
                max_tokens=16000,
                system=_JSON_SYSTEM,
                messages=[{"role": "user", "content": prompt}],
            )
        except anthropic.RateLimitError as e:
            raise LLMQuotaError(str(e)) from e
        except anthropic.APIStatusError as e:
            if e.status_code >= 500:
                raise LLMTransientError(str(e)) from e
            raise LLMError(str(e)) from e
        except anthropic.APIConnectionError as e:  # includes timeouts
            raise LLMTransientError(str(e)) from e
        if resp.stop_reason == "refusal":
            detail = getattr(resp.stop_details, "explanation", None) or "refusal"
            raise LLMError(f"Claude declined the request: {detail}")
        text = "".join(b.text for b in resp.content if b.type == "text")
        return _extract_json(text)


class FallbackLLM:
    """Try the primary client first on every call; on any failure retry once on the fallback.

    No state is kept between calls, so the next call tries the primary again.
    """

    def __init__(self, primary, fallback, fallback_model: str):
        self._primary = primary
        self._fallback = fallback
        self._fallback_model = fallback_model

    async def generate_json(self, model: str, prompt: str) -> dict:
        try:
            return await self._primary.generate_json(model, prompt)
        except LLMError as primary_error:
            logger.warning("{} failed ({}); falling back to {}",
                           model, primary_error, self._fallback_model)
            try:
                return await self._fallback.generate_json(self._fallback_model, prompt)
            except LLMError as fallback_error:
                raise fallback_error from primary_error
