from datetime import datetime, timedelta, timezone

from telecast.models import Article, ArticleState, PublishTarget, TargetStatus
from telecast.publish.schedule import reset_schedule, schedule_pending

NOW = datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)
# SQLite stores datetimes without offset; read-back is naive UTC
DB_NOW = NOW.replace(tzinfo=None)
DELAY = timedelta(minutes=5)
INTERVAL = timedelta(hours=6)
# an empty queue publishes after the delay
DB_FIRST = DB_NOW + DELAY


def _article(session, mid, approved_at=None, target_status=TargetStatus.APPROVED,
             scheduled_at=None, platform="telegram", created_at=None):
    a = Article(source_channel="@n", source_message_id=mid,
                state=ArticleState.PENDING_REVIEW,
                approved_at=approved_at, scheduled_at=scheduled_at,
                **({"created_at": created_at} if created_at else {}))
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


# --- reset_schedule ---------------------------------------------------
# Rebuilding the queue is the one operation allowed to move a slot that the
# review UI already showed, so each rule it breaks is pinned here.

OLD = NOW - timedelta(days=3)


def test_reset_orders_by_creation_date_not_approval(session):
    """The whole point: an old article approved last still publishes first."""
    late = _article(session, 1, created_at=OLD, approved_at=NOW)
    early = _article(session, 2, created_at=NOW - timedelta(hours=1),
                     approved_at=NOW - timedelta(hours=2))
    assert reset_schedule(session, now=NOW, delay=DELAY, interval=INTERVAL) == 2
    assert session.get(Article, late).scheduled_at == DB_FIRST
    assert session.get(Article, early).scheduled_at == DB_FIRST + INTERVAL


def test_reset_overwrites_existing_slots(session):
    aid = _article(session, 1, created_at=OLD, approved_at=NOW,
                   scheduled_at=NOW + timedelta(days=9))
    assert reset_schedule(session, now=NOW, delay=DELAY, interval=INTERVAL) == 1
    assert session.get(Article, aid).scheduled_at == DB_FIRST


def test_reset_spaces_the_whole_queue_from_the_delay(session):
    ids = [_article(session, i, created_at=OLD + timedelta(hours=i),
                    approved_at=NOW, scheduled_at=NOW + timedelta(days=30 - i))
           for i in range(3)]
    assert reset_schedule(session, now=NOW, delay=DELAY, interval=INTERVAL) == 3
    slots = [session.get(Article, aid).scheduled_at for aid in ids]
    assert slots == [DB_FIRST, DB_FIRST + INTERVAL, DB_FIRST + 2 * INTERVAL]


def test_reset_leaves_an_in_flight_article_alone(session):
    """The worker already claimed it — its slot means nothing now, and moving
    it would only misreport what is happening."""
    flying = _article(session, 1, created_at=OLD, approved_at=NOW,
                      target_status=TargetStatus.PUBLISHING,
                      scheduled_at=NOW - timedelta(minutes=1))
    session.add(PublishTarget(article_id=flying, platform="youtube",
                              status=TargetStatus.APPROVED))
    session.commit()
    aid = _article(session, 2, created_at=OLD + timedelta(hours=1), approved_at=NOW)

    assert reset_schedule(session, now=NOW, delay=DELAY, interval=INTERVAL) == 1
    assert session.get(Article, flying).scheduled_at == DB_NOW - timedelta(minutes=1)
    # …and it does not anchor the tail: the rest still starts at the delay.
    assert session.get(Article, aid).scheduled_at == DB_FIRST


def test_reset_ignores_unconfigured_plugins(session):
    orphan = _article(session, 1, created_at=OLD, approved_at=NOW,
                      platform="wordpress", scheduled_at=NOW + timedelta(hours=20))
    aid = _article(session, 2, created_at=OLD + timedelta(hours=1), approved_at=NOW)
    assert reset_schedule(session, now=NOW, delay=DELAY, interval=INTERVAL,
                          unconfigured={"wordpress"}) == 1
    assert session.get(Article, orphan).scheduled_at == DB_NOW + timedelta(hours=20)
    assert session.get(Article, aid).scheduled_at == DB_FIRST


def test_reset_skips_articles_with_no_approved_target(session):
    pending = _article(session, 1, created_at=OLD, target_status=TargetStatus.PENDING)
    done = _article(session, 2, created_at=OLD, target_status=TargetStatus.PUBLISHED,
                    scheduled_at=NOW + timedelta(hours=4))
    assert reset_schedule(session, now=NOW, delay=DELAY, interval=INTERVAL) == 0
    assert session.get(Article, pending).scheduled_at is None
    assert session.get(Article, done).scheduled_at == DB_NOW + timedelta(hours=4)


def test_reset_on_an_empty_queue_is_a_noop(session):
    assert reset_schedule(session, now=NOW, delay=DELAY, interval=INTERVAL) == 0
