from datetime import datetime, timedelta, timezone

from telecast.models import Article, ArticleState, PublishTarget, TargetStatus
from telecast.publish.schedule import schedule_pending

NOW = datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)
# SQLite stores datetimes without offset; read-back is naive UTC
DB_NOW = NOW.replace(tzinfo=None)
DELAY = timedelta(minutes=5)
INTERVAL = timedelta(hours=6)
# an empty queue publishes after the delay
DB_FIRST = DB_NOW + DELAY


def _article(session, mid, approved_at=None, target_status=TargetStatus.APPROVED,
             scheduled_at=None, platform="telegram"):
    a = Article(source_channel="@n", source_message_id=mid,
                state=ArticleState.PENDING_REVIEW,
                approved_at=approved_at, scheduled_at=scheduled_at)
    session.add(a)
    session.commit()
    session.add(PublishTarget(article_id=a.id, platform=platform, status=target_status))
    session.commit()
    return a.id


def test_empty_queue_schedules_after_the_delay(session):
    aid = _article(session, 1, approved_at=NOW)
    assert schedule_pending(session, now=NOW, delay=DELAY, interval=INTERVAL) == 1
    assert session.get(Article, aid).scheduled_at == DB_FIRST


def test_non_empty_queue_schedules_an_interval_after_the_tail(session):
    _article(session, 1, approved_at=NOW - timedelta(hours=1), scheduled_at=NOW + DELAY)
    aid = _article(session, 2, approved_at=NOW)
    assert schedule_pending(session, now=NOW, delay=DELAY, interval=INTERVAL) == 1
    assert session.get(Article, aid).scheduled_at == DB_FIRST + INTERVAL


def test_existing_slots_are_never_moved(session):
    tail = NOW + timedelta(hours=2)
    queued = _article(session, 1, approved_at=NOW - timedelta(hours=1), scheduled_at=tail)
    aid = _article(session, 2, approved_at=NOW)
    schedule_pending(session, now=NOW, delay=DELAY, interval=INTERVAL)
    assert session.get(Article, queued).scheduled_at == DB_NOW + timedelta(hours=2)
    assert session.get(Article, aid).scheduled_at == DB_NOW + timedelta(hours=8)


def test_batch_gets_successive_slots_fifo_by_approval(session):
    ids = [
        _article(session, i, approved_at=NOW - timedelta(minutes=30 - i))
        for i in range(3)
    ]
    assert schedule_pending(session, now=NOW, delay=DELAY, interval=INTERVAL) == 3
    slots = [session.get(Article, aid).scheduled_at for aid in ids]
    assert slots == [DB_FIRST, DB_FIRST + INTERVAL, DB_FIRST + 2 * INTERVAL]


def test_overdue_tail_anchors_to_now(session):
    """A queue that sat through a downtime restarts spacing from now instead
    of dumping the backlog at once."""
    _article(session, 1, approved_at=NOW - timedelta(hours=12),
             scheduled_at=NOW - timedelta(hours=10))
    aid = _article(session, 2, approved_at=NOW)
    schedule_pending(session, now=NOW, delay=DELAY, interval=INTERVAL)
    assert session.get(Article, aid).scheduled_at == DB_FIRST


def test_already_scheduled_article_is_left_alone(session):
    aid = _article(session, 1, approved_at=NOW, scheduled_at=NOW + timedelta(hours=3))
    assert schedule_pending(session, now=NOW, delay=DELAY, interval=INTERVAL) == 0
    assert session.get(Article, aid).scheduled_at == DB_NOW + timedelta(hours=3)


def test_delay_and_interval_are_configurable(session):
    a1 = _article(session, 1, approved_at=NOW - timedelta(minutes=1))
    a2 = _article(session, 2, approved_at=NOW)
    schedule_pending(session, now=NOW, delay=timedelta(minutes=30),
                     interval=timedelta(hours=2))
    assert session.get(Article, a1).scheduled_at == DB_NOW + timedelta(minutes=30)
    assert session.get(Article, a2).scheduled_at == DB_NOW + timedelta(hours=2, minutes=30)


def test_articles_without_approved_targets_not_scheduled(session):
    aid = _article(session, 1, target_status=TargetStatus.PENDING)
    assert schedule_pending(session, now=NOW, delay=DELAY, interval=INTERVAL) == 0
    assert session.get(Article, aid).scheduled_at is None


def test_published_targets_do_not_queue_article(session):
    aid = _article(session, 1, approved_at=NOW, target_status=TargetStatus.PUBLISHED)
    schedule_pending(session, now=NOW, delay=DELAY, interval=INTERVAL)
    assert session.get(Article, aid).scheduled_at is None


def test_empty_queue_is_noop(session):
    assert schedule_pending(session, now=NOW, delay=DELAY, interval=INTERVAL) == 0


def test_approved_target_of_unconfigured_plugin_takes_no_slot(session):
    """A plugin that loses its config must neither claim a slot nor anchor the
    tail for the articles that can actually publish."""
    stale = _article(session, 99, approved_at=NOW - timedelta(hours=1),
                     platform="wordpress", scheduled_at=NOW + timedelta(hours=20))
    pending = _article(session, 98, approved_at=NOW - timedelta(minutes=30),
                       platform="wordpress")
    aid = _article(session, 1, approved_at=NOW)

    assert schedule_pending(session, now=NOW, delay=DELAY, interval=INTERVAL,
                            unconfigured={"wordpress"}) == 1
    assert session.get(Article, pending).scheduled_at is None
    assert session.get(Article, stale).scheduled_at == DB_NOW + timedelta(hours=20)
    assert session.get(Article, aid).scheduled_at == DB_FIRST
