from datetime import timedelta

from telecast.models import Article, ArticleState, utcnow
from telecast.states import claim, complete, fail, reap_stale


def _mk(session, **kw):
    a = Article(source_channel="@n", source_message_id=kw.pop("mid", 1), **kw)
    session.add(a)
    session.commit()
    session.refresh(a)
    return a


def test_claim_complete_fail_cycle(session):
    a = _mk(session)
    claim(session, a, ArticleState.TRANSLATING)
    assert a.state == ArticleState.TRANSLATING and a.claimed_at is not None
    complete(session, a, ArticleState.TRANSLATED, translated_text="hello")
    assert a.state == ArticleState.TRANSLATED
    assert a.translated_text == "hello"
    assert a.claimed_at is None
    fail(session, a, ArticleState.FAILED_TRANSLATE, "boom")
    assert a.state == ArticleState.FAILED_TRANSLATE and a.error == "boom"


def test_reap_stale_resets_only_old_claims(session):
    fresh = _mk(session, mid=1)
    stale = _mk(session, mid=2)
    claim(session, fresh, ArticleState.TRANSLATING)
    claim(session, stale, ArticleState.ENHANCING)
    stale.claimed_at = utcnow() - timedelta(minutes=30)
    session.commit()
    n = reap_stale(session, older_than_minutes=15)
    assert n == 1
    session.refresh(stale)
    session.refresh(fresh)
    assert stale.state == ArticleState.TRANSLATED
    assert fresh.state == ArticleState.TRANSLATING
