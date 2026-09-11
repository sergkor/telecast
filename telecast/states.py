from datetime import timedelta, timezone

from sqlmodel import Session, select

from telecast.models import Article, ArticleState, WORKING_STATES, utcnow


def claim(session: Session, article: Article, working_state: ArticleState) -> None:
    article.state = working_state
    article.claimed_at = utcnow()
    article.updated_at = utcnow()
    session.commit()


def complete(session: Session, article: Article, new_state: ArticleState, **fields) -> None:
    for key, value in fields.items():
        setattr(article, key, value)
    article.state = new_state
    article.claimed_at = None
    article.error = None
    article.updated_at = utcnow()
    session.commit()


def fail(session: Session, article: Article, failed_state: ArticleState, error: str) -> None:
    article.state = failed_state
    article.error = error[:2000]
    article.claimed_at = None
    article.updated_at = utcnow()
    session.commit()


def reap_stale(session: Session, older_than_minutes: int) -> int:
    cutoff = utcnow() - timedelta(minutes=older_than_minutes)
    rows = session.exec(
        select(Article).where(Article.state.in_(list(WORKING_STATES)))
    ).all()
    count = 0
    for article in rows:
        claimed = article.claimed_at
        if claimed is not None and claimed.tzinfo is None:
            claimed = claimed.replace(tzinfo=timezone.utc)
        if claimed is None or claimed < cutoff:
            article.state = WORKING_STATES[article.state]
            article.claimed_at = None
            count += 1
    session.commit()
    return count
