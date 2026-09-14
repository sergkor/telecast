from dataclasses import dataclass

from telecast.pipeline.llm import GeminiError

TRANSLATE_MODEL = "gemini-flash-latest"

TRANSLATE_PROMPT = """Translate the following social media post to English.
Be literal and faithful; do not add, remove, or embellish anything.
Return ONLY JSON: {{"detected_language": "<iso 639-1 code>", "translated_text": "<english translation>"}}

POST:
{text}"""


@dataclass
class Translation:
    detected_language: str
    translated_text: str


async def translate(text: str, llm, model: str = TRANSLATE_MODEL) -> Translation:
    data = await llm.generate_json(model, TRANSLATE_PROMPT.format(text=text))
    try:
        return Translation(
            detected_language=data["detected_language"],
            translated_text=data["translated_text"],
        )
    except (KeyError, TypeError) as e:
        raise GeminiError(f"bad translate response: {data!r}") from e
