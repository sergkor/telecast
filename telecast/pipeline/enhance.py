from dataclasses import dataclass
from pathlib import Path

from telecast.pipeline.llm import GeminiError

ENHANCE_MODEL = "gemini-2.5-pro"

NO_TEXT_FALLBACK = (
    "(The source post contained no text — only a video. Write a brief, neutral, "
    "factual caption suitable for a news video, without inventing specifics.)"
)


@dataclass
class Enhancement:
    title: str
    article: str
    hashtags: list[str]


async def enhance(translated_text: str | None, prompt_path: Path, llm) -> Enhancement:
    template = prompt_path.read_text(encoding="utf-8")
    source = translated_text.strip() if translated_text else ""
    prompt = template.replace("{source_text}", source or NO_TEXT_FALLBACK)
    data = await llm.generate_json(ENHANCE_MODEL, prompt)
    try:
        return Enhancement(
            title=str(data["title"]),
            article=str(data["article"]),
            hashtags=[str(h) for h in data.get("hashtags", [])],
        )
    except (KeyError, TypeError) as e:
        raise GeminiError(f"bad enhance response: {data!r}") from e
