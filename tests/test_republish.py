from sqlmodel import select

from telecast.models import Article, ArticleState, PublishTarget, TargetStatus, utcnow
from telecast.publish.republish import queue_republish


def _article(session, msg_id, state=ArticleState.PUBLISHED, targets=()):
    a = Article(source_channel="@n", source_message_id=msg_id, state=state,
                title="T", final_text="B", approved_at=utcnow())
    session.add(a)
    session.commit()
    for platform, status in targets:
        session.add(PublishTarget(article_id=a.id, platform=platform, status=status,
                                  external_url="https://old", error="old error",
                                  published_at=utcnow()))
    session.commit()
    return a.id


def _target(session, aid, platform):
    return session.exec(
        select(PublishTarget)
        .where(PublishTarget.article_id == aid)
        .where(PublishTarget.platform == platform)
    ).first()


def test_requeues_published_target(session):
    aid = _article(session, 1, targets=[("youtube", TargetStatus.PUBLISHED)])
    assert queue_republish(session, [aid], "youtube") == 1
    t = _target(session, aid, "youtube")
    assert t.status == TargetStatus.APPROVED
    assert t.error is None


def test_creates_missing_target(session):
    aid = _article(session, 2, targets=[("telegram", TargetStatus.PUBLISHED)])
    assert queue_republish(session, [aid], "youtube") == 1
    assert _target(session, aid, "youtube").status == TargetStatus.APPROVED


def test_skips_non_published_article(session):
    aid = _article(session, 3, state=ArticleState.PENDING_REVIEW,
                   targets=[("youtube", TargetStatus.PENDING)])
    assert queue_republish(session, [aid], "youtube") == 0
    assert _target(session, aid, "youtube").status == TargetStatus.PENDING


def test_skips_already_queued_target(session):
    aid = _article(session, 4, targets=[("youtube", TargetStatus.APPROVED)])
    assert queue_republish(session, [aid], "youtube") == 0


def test_skips_publishing_target(session):
    aid = _article(session, 5, targets=[("youtube", TargetStatus.PUBLISHING)])
    assert queue_republish(session, [aid], "youtube") == 0


def test_skips_unknown_article(session):
    assert queue_republish(session, [999], "youtube") == 0


def test_mixed_selection_counts_only_queued(session):
    a1 = _article(session, 6, targets=[("youtube", TargetStatus.PUBLISHED)])
    a2 = _article(session, 7, state=ArticleState.DISCARDED)
    a3 = _article(session, 8, targets=[("youtube", TargetStatus.FAILED)])
    assert queue_republish(session, [a1, a2, a3], "youtube") == 2
    assert _target(session, a1, "youtube").status == TargetStatus.APPROVED
    assert _target(session, a3, "youtube").status == TargetStatus.APPROVED
