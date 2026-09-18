import pytest
from sqlmodel import select

from telecast.config import Settings
from telecast.models import Article, ArticleState, PublishTarget, TargetStatus
from telecast.pipeline.llm import GeminiError, GeminiQuotaError
from telecast.pipeline.runner import advance_one, count_workable, drain
from telecast.publish import base as registry
from tests.fakes import FakeGemini, StubPublisher


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


async def test_targets_created_only_for_configured_platforms(session_factory, settings):
    registry.clear()
    registry.register(StubPublisher("telegram"))
    registry.register(StubPublisher("wordpress", configured=False))
    try:
        with session_factory() as s:
            a = _ingest(s)
            a.state = ArticleState.ENHANCED
            s.commit()
            aid = a.id
        assert await advance_one(session_factory, FakeGemini(), settings)
        with session_factory() as s:
            targets = s.exec(select(PublishTarget).where(PublishTarget.article_id == aid)).all()
            assert {t.platform for t in targets} == {"telegram"}
            assert s.get(Article, aid).state == ArticleState.PENDING_REVIEW
    finally:
        registry.clear()


# --- manual drain -----------------------------------------------------

async def test_count_workable_counts_only_unfinished_articles(session_factory, settings):
    states = [ArticleState.INGESTED, ArticleState.TRANSLATED, ArticleState.ENHANCED,
              ArticleState.PENDING_REVIEW, ArticleState.FAILED_TRANSLATE]
    with session_factory() as s:
        for mid, state in enumerate(states, start=1):
            _ingest(s, mid=mid).state = state
        s.commit()
    with session_factory() as s:
        assert count_workable(s) == 3


async def test_count_workable_is_zero_on_an_idle_queue(session_factory, settings):
    with session_factory() as s:
        assert count_workable(s) == 0


async def test_drain_advances_every_stuck_article_to_review(session_factory,
                                                            dummy_platforms, settings):
    with session_factory() as s:
        first = _ingest(s, mid=1).id
        stalled = _ingest(s, mid=2)
        stalled.state = ArticleState.TRANSLATED
        stalled.translated_text = "already translated"
        s.commit()
        second = stalled.id
    llm = FakeGemini(responses=[
        {"detected_language": "uk", "translated_text": "tr"},
        {"title": "T1", "article": "e1", "hashtags": []},
        {"title": "T2", "article": "e2", "hashtags": []},
    ])
    # 3 steps for the INGESTED article, 2 for the one stuck after translation
    assert await drain(session_factory, llm, settings) == 5
    with session_factory() as s:
        assert s.get(Article, first).state == ArticleState.PENDING_REVIEW
        assert s.get(Article, second).state == ArticleState.PENDING_REVIEW


async def test_drain_on_an_idle_queue_does_nothing(session_factory, dummy_platforms, settings):
    llm = FakeGemini()
    assert await drain(session_factory, llm, settings) == 0
    assert llm.calls == []


async def test_drain_stops_on_quota_and_leaves_the_article_workable(
        session_factory, dummy_platforms, settings):
    with session_factory() as s:
        aid = _ingest(s).id
    llm = FakeGemini(error=GeminiQuotaError("quota"))
    assert await drain(session_factory, llm, settings) == 0
    with session_factory() as s:
        assert s.get(Article, aid).state == ArticleState.INGESTED
    assert len(llm.calls) == 1  # gave up instead of hammering the quota


async def test_drain_honours_its_step_limit(session_factory, dummy_platforms, settings):
    with session_factory() as s:
        aid = _ingest(s).id
    llm = FakeGemini(responses=[{"detected_language": "uk", "translated_text": "tr"}])
    assert await drain(session_factory, llm, settings, limit=1) == 1
    with session_factory() as s:
        assert s.get(Article, aid).state == ArticleState.TRANSLATED
