from telecast.pipeline.enhance import ENHANCE_MODEL, NO_TEXT_FALLBACK, enhance
from tests.fakes import FakeGemini

RESPONSE = {"title": "Big News", "article": "Enhanced body.", "hashtags": ["#news", "#video"]}


async def test_enhance_injects_source_into_prompt_file(tmp_path):
    prompt = tmp_path / "enhance.md"
    prompt.write_text("Rewrite this:\n{source_text}\nReturn JSON.")
    llm = FakeGemini(responses=[dict(RESPONSE)])
    result = await enhance("Translated text here", prompt, llm)
    assert result.title == "Big News"
    assert result.hashtags == ["#news", "#video"]
    model, sent = llm.calls[0]
    assert model == ENHANCE_MODEL
    assert "Translated text here" in sent


async def test_enhance_uses_given_model(tmp_path):
    prompt = tmp_path / "enhance.md"
    prompt.write_text("{source_text}")
    llm = FakeGemini(responses=[dict(RESPONSE)])
    await enhance("x", prompt, llm, model="custom-model")
    assert llm.calls[0][0] == "custom-model"


async def test_enhance_empty_text_uses_fallback(tmp_path):
    prompt = tmp_path / "enhance.md"
    prompt.write_text("{source_text}")
    llm = FakeGemini(responses=[dict(RESPONSE)])
    await enhance(None, prompt, llm)
    assert NO_TEXT_FALLBACK in llm.calls[0][1]


async def test_prompt_is_hot_read_each_call(tmp_path):
    prompt = tmp_path / "enhance.md"
    prompt.write_text("v1 {source_text}")
    llm = FakeGemini(responses=[dict(RESPONSE), dict(RESPONSE)])
    await enhance("x", prompt, llm)
    prompt.write_text("v2 {source_text}")
    await enhance("x", prompt, llm)
    assert llm.calls[0][1].startswith("v1")
    assert llm.calls[1][1].startswith("v2")
