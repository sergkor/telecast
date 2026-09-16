from datetime import datetime, timedelta, timezone

from telecast.models import Article, ArticleState, PublishTarget, TargetStatus
from telecast.publish.schedule import reschedule

NOW = datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)
# SQLite stores datetimes without offset; read-back is naive UTC
DB_NOW = NOW.replace(tzinfo=None)
# first slot waits the default publish delay
DB_FIRST = DB_NOW + timedelta(minutes=5)


def _article(session, mid, approved_at=None, target_status=TargetStatus.APPROVED,
             scheduled_at=None):
    a = Article(source_channel="@n", source_message_id=mid,
                state=ArticleState.PENDING_REVIEW,
                approved_at=approved_at, scheduled_at=scheduled_at)
    session.add(a)
    session.commit()
    session.add(PublishTarget(article_id=a.id, platform="telegram", status=target_status))
    session.commit()
    return a.id


def test_single_article_scheduled_after_delay(session):
    aid = _article(session, 1, approved_at=NOW)
    reschedule(session, now=NOW)
    assert session.get(Article, aid).scheduled_at == DB_FIRST


def test_delay_is_configurable(session):
    aid = _article(session, 1, approved_at=NOW)
    reschedule(session, now=NOW, delay=timedelta(minutes=30))
    assert session.get(Article, aid).scheduled_at == DB_NOW + timedelta(minutes=30)


def test_three_articles_spread_over_24h_fifo(session):
    ids = [
        _article(session, i, approved_at=NOW - timedelta(minutes=30 - i))
        for i in range(3)
    ]
    reschedule(session, now=NOW)
    arts = [session.get(Article, aid) for aid in ids]
    assert arts[0].scheduled_at == DB_FIRST
    assert arts[1].scheduled_at == DB_FIRST + timedelta(hours=8)
    assert arts[2].scheduled_at == DB_FIRST + timedelta(hours=16)


def test_reschedule_resets_existing_slots_from_now(session):
    old_slot = NOW - timedelta(hours=2)
    a1 = _article(session, 1, approved_at=NOW - timedelta(hours=3), scheduled_at=old_slot)
    a2 = _article(session, 2, approved_at=NOW)
    reschedule(session, now=NOW)
    assert session.get(Article, a1).scheduled_at == DB_FIRST
    assert session.get(Article, a2).scheduled_at == DB_FIRST + timedelta(hours=12)


def test_articles_without_approved_targets_not_scheduled(session):
    aid = _article(session, 1, target_status=TargetStatus.PENDING)
    reschedule(session, now=NOW)
    assert session.get(Article, aid).scheduled_at is None


def test_published_targets_do_not_queue_article(session):
    aid = _article(session, 1, approved_at=NOW, target_status=TargetStatus.PUBLISHED)
    reschedule(session, now=NOW)
    assert session.get(Article, aid).scheduled_at is None


def test_empty_queue_is_noop(session):
    assert reschedule(session, now=NOW) == 0


def test_approved_target_of_unconfigured_plugin_takes_no_slot(session):
    """A plugin that loses its config must not hold a publish slot and skew
    the spread for the articles that can actually publish."""
    stale = Article(source_channel="@n", source_message_id=99,
                    state=ArticleState.PENDING_REVIEW, approved_at=NOW - timedelta(hours=1))
    session.add(stale)
    session.commit()
    session.add(PublishTarget(article_id=stale.id, platform="wordpress",
                              status=TargetStatus.APPROVED))
    session.commit()
    aid = _article(session, 1, approved_at=NOW)

    assert reschedule(session, now=NOW, unconfigured={"wordpress"}) == 1
    assert session.get(Article, stale.id).scheduled_at is None
    assert session.get(Article, aid).scheduled_at == DB_FIRST
