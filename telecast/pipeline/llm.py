import json

from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential


class GeminiError(Exception):
    pass


class GeminiQuotaError(GeminiError):
    pass


class GeminiTransientError(GeminiError):
    pass


class RealGeminiClient:
    def __init__(self, api_key: str):
        from google import genai

        self._client = genai.Client(api_key=api_key)

    @retry(
        retry=retry_if_exception_type(GeminiTransientError),
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
                raise GeminiQuotaError(str(e)) from e
            if e.code is not None and e.code >= 500:
                raise GeminiTransientError(str(e)) from e
            raise GeminiError(str(e)) from e
        try:
            return json.loads(resp.text)
        except (TypeError, ValueError) as e:
            raise GeminiError(f"non-JSON response: {resp.text!r}") from e
