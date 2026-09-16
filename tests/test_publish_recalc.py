import pytest
from sqlmodel import select

from telecast.config import Settings
from telecast.models import Article, ArticleState, PublishTarget, TargetStatus
from telecast.publish import base as registry
from telecast.publish import recalc
from tests.fakes import StubPublisher


@pytest.fixture
def settings(tmp_path):
    return Settings(_env_file=None, data_dir=tmp_path)


@pytest.fixture
def platforms():
    """telegram configured, wordpress registered but missing its config."""
    registry.clear()
    registry.register(StubPublisher("telegram"))
    registry.register(StubPublisher("wordpress", configured=False))
    yield
    registry.clear()


def _article(session, mid=1, state=ArticleState.PENDING_REVIEW, targets=()):
    a = Article(source_channel="@n", source_message_id=mid, state=state,
                title="T", final_text="B")
    session.add(a)
    session.commit()
    session.refresh(a)
    for platform, status in targets:
        session.add(PublishTarget(article_id=a.id, platform=platform, status=status))
    session.commit()
    return a


def test_effective_targets_drops_unconfigured_platforms(session, settings, platforms):
    a = _article(session, targets=[("telegram", TargetStatus.PUBLISHED),
                                   ("wordpress", TargetStatus.PENDING)])
    targets = session.exec(
        select(PublishTarget).where(PublishTarget.article_id == a.id)
    ).all()
    effective = recalc.effective_targets(targets, registry.unconfigured(settings))
    assert [t.platform for t in effective] == ["telegram"]


def test_effective_targets_keeps_unregistered_platforms(session, settings, platforms):
    """An unknown platform is a data problem, not a missing config — keep it
    visible so the UI can warn about it."""
    a = _article(session, targets=[("myspace", TargetStatus.PENDING)])
    targets = session.exec(
        select(PublishTarget).where(PublishTarget.article_id == a.id)
    ).all()
    assert len(recalc.effective_targets(targets, registry.unconfigured(settings))) == 1


def test_recompute_promotes_when_configured_targets_are_done(session, settings, platforms):
    a = _article(session, targets=[("telegram", TargetStatus.PUBLISHED),
                                   ("wordpress", TargetStatus.PENDING)])
    assert recalc.recompute(session, a, registry.unconfigured(settings))
    assert a.state == ArticleState.PUBLISHED


def test_recompute_waits_for_configured_target(session, settings, platforms):
    a = _article(session, targets=[("telegram", TargetStatus.PENDING),
                                   ("wordpress", TargetStatus.PENDING)])
    assert not recalc.recompute(session, a, registry.unconfigured(settings))
    assert a.state == ArticleState.PENDING_REVIEW


def test_recompute_requires_at_least_one_published(session, settings, platforms):
    a = _article(session, targets=[("telegram", TargetStatus.SKIPPED),
                                   ("wordpress", TargetStatus.PENDING)])
    assert not recalc.recompute(session, a, registry.unconfigured(settings))
    assert a.state == ArticleState.PENDING_REVIEW


def test_recompute_ignores_article_with_no_effective_targets(session, settings, platforms):
    a = _article(session, targets=[("wordpress", TargetStatus.PENDING)])
    assert not recalc.recompute(session, a, registry.unconfigured(settings))
    assert a.state == ArticleState.PENDING_REVIEW


def test_recompute_never_demotes_a_published_article(session, settings, platforms):
    a = _article(session, state=ArticleState.PUBLISHED,
                 targets=[("telegram", TargetStatus.PENDING)])
    assert not recalc.recompute(session, a, registry.unconfigured(settings))
    assert a.state == ArticleState.PUBLISHED


def test_recompute_all_promotes_stalled_articles(session, settings, platforms):
    a = _article(session, mid=1, targets=[("telegram", TargetStatus.PUBLISHED),
                                          ("wordpress", TargetStatus.PENDING)])
    result = recalc.recompute_all(session, settings)
    assert result["articles_published"] == 1
    session.refresh(a)
    assert a.state == ArticleState.PUBLISHED


def test_recompute_all_backfills_targets_for_configured_plugins(session, settings, platforms):
    a = _article(session, targets=[("wordpress", TargetStatus.PENDING)])
    result = recalc.recompute_all(session, settings)
    rows = session.exec(select(PublishTarget).where(PublishTarget.article_id == a.id)).all()
    assert {t.platform for t in rows} == {"telegram", "wordpress"}
    assert result["targets_added"] == 1
    session.refresh(a)
    assert a.state == ArticleState.PENDING_REVIEW  # new target needs review


def test_recompute_all_does_not_backfill_unconfigured_plugins(session, settings, platforms):
    a = _article(session, targets=[("telegram", TargetStatus.PENDING)])
    recalc.recompute_all(session, settings)
    rows = session.exec(select(PublishTarget).where(PublishTarget.article_id == a.id)).all()
    assert {t.platform for t in rows} == {"telegram"}


def test_recompute_all_skips_discarded_and_failed_articles(session, settings, platforms):
    a = _article(session, mid=1, state=ArticleState.DISCARDED)
    b = _article(session, mid=2, state=ArticleState.FAILED_ENHANCE)
    result = recalc.recompute_all(session, settings)
    assert result == {"targets_added": 0, "articles_published": 0}
    for article in (a, b):
        rows = session.exec(
            select(PublishTarget).where(PublishTarget.article_id == article.id)
        ).all()
        assert rows == []
