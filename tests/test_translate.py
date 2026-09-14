import pytest

from telecast.pipeline.llm import GeminiError
from telecast.pipeline.translate import TRANSLATE_MODEL, translate
from tests.fakes import FakeGemini


async def test_translate_returns_translation():
    llm = FakeGemini(responses=[
        {"detected_language": "uk", "translated_text": "Hello world"}
    ])
    result = await translate("Привіт світ", llm)
    assert result.detected_language == "uk"
    assert result.translated_text == "Hello world"
    model, prompt = llm.calls[0]
    assert model == TRANSLATE_MODEL
    assert "Привіт світ" in prompt


async def test_translate_uses_given_model():
    llm = FakeGemini(responses=[{"detected_language": "en", "translated_text": "x"}])
    await translate("t", llm, model="custom-model")
    assert llm.calls[0][0] == "custom-model"


async def test_translate_missing_key_raises():
    llm = FakeGemini(responses=[{"nope": 1}])
    with pytest.raises(GeminiError):
        await translate("text", llm)
