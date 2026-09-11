import pytest
from sqlmodel import select

from telecast.config import Settings
from telecast.models import Article, ArticleState, PublishTarget, TargetStatus
from telecast.pipeline.llm import GeminiError, GeminiQuotaError
from telecast.pipeline.runner import advance_one
from telecast.publish import base as registry
from tests.fakes import FakeGemini


@pytest.fixture
def settings(tmp_path):
    prompt = tmp_path / "enhance.md"
    prompt.write_text("{source_text}")
    return Settings(_env_file=None, enhance_prompt_path=prompt, data_dir=tmp_path)


@pytest.fixture
def dummy_platforms():
    registry.clear()

    class P:
        def __init__(self, name):
            self.name = name

    registry.register(P("telegram"))
    registry.register(P("youtube"))
    yield
    registry.clear()


def _ingest(session, text="original", mid=1):
    a = Article(source_channel="@n", source_message_id=mid, original_text=text)
    session.add(a)
    session.commit()
    session.refresh(a)
    return a


async def test_full_advance_to_pending_review(session_factory, dummy_platforms, settings):
    with session_factory() as s:
        a = _ingest(s)
        aid = a.id
    llm = FakeGemini(responses=[
        {"detected_language": "uk", "translated_text": "translated"},
        {"title": "T", "article": "enhanced", "hashtags": ["#a"]},
    ])
    assert await advance_one(session_factory, llm, settings)  # translate
    assert await advance_one(session_factory, llm, settings)  # enhance
    assert await advance_one(session_factory, llm, settings)  # targets
    assert not await advance_one(session_factory, llm, settings)
    with session_factory() as s:
        a = s.get(Article, aid)
        assert a.state == ArticleState.PENDING_REVIEW
        assert a.translated_text == "translated"
        assert a.enhanced_text == "enhanced"
        assert a.final_text == "enhanced"
        assert a.title == "T" and a.hashtags == "#a"
        targets = s.exec(select(PublishTarget).where(PublishTarget.article_id == aid)).all()
        assert {t.platform for t in targets} == {"telegram", "youtube"}
        assert all(t.status == TargetStatus.PENDING for t in targets)


async def test_empty_caption_skips_translation(session_factory, dummy_platforms, settings):
    with session_factory() as s:
        _ingest(s, text="")
    llm = FakeGemini(responses=[{"title": "T", "article": "E", "hashtags": []}])
    await advance_one(session_factory, llm, settings)  # INGESTED -> TRANSLATED (no llm call)
    await advance_one(session_factory, llm, settings)  # enhance
    assert len(llm.calls) == 1


async def test_gemini_error_parks_article(session_factory, dummy_platforms, settings):
    with session_factory() as s:
        a = _ingest(s)
        aid = a.id
    llm = FakeGemini(error=GeminiError("bad"))
    await advance_one(session_factory, llm, settings)
    with session_factory() as s:
        a = s.get(Article, aid)
        assert a.state == ArticleState.FAILED_TRANSLATE
        assert a.error == "bad"


async def test_quota_error_resets_claim_and_raises(session_factory, dummy_platforms, settings):
    with session_factory() as s:
        a = _ingest(s)
        aid = a.id
    llm = FakeGemini(error=GeminiQuotaError("quota"))
    with pytest.raises(GeminiQuotaError):
        await advance_one(session_factory, llm, settings)
    with session_factory() as s:
        assert s.get(Article, aid).state == ArticleState.INGESTED


async def test_reenhance_preserves_edited_final_text(session_factory, dummy_platforms, settings):
    with session_factory() as s:
        a = _ingest(s)
        a.state = ArticleState.TRANSLATED
        a.translated_text = "tr"
        a.final_text = "my manual edit"
        a.final_text_edited = True
        s.commit()
        aid = a.id
    llm = FakeGemini(responses=[{"title": "T", "article": "regen", "hashtags": []}])
    await advance_one(session_factory, llm, settings)
    with session_factory() as s:
        a = s.get(Article, aid)
        assert a.enhanced_text == "regen"
        assert a.final_text == "my manual edit"
