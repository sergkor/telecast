"""Article state derived from the plugins that are actually usable.

A plugin whose credentials are missing must not hold an article in
review forever: its targets are ignored when deciding whether the
article is done. Because that decision changes whenever `.env` changes,
`recompute_all` re-derives it on demand for the whole table.
"""

from sqlmodel import Session, select

from telecast.config import Settings
from telecast.models import Article, ArticleState, PublishTarget, TargetStatus, utcnow
from telecast.publish import base as registry

DONE_STATUSES = {TargetStatus.PUBLISHED, TargetStatus.SKIPPED}
# Discarded and mid-pipeline articles have no publish decision to make.
RECOMPUTABLE_STATES = (ArticleState.PENDING_REVIEW,)


def effective_targets(targets, unconfigured: set[str]) -> list[PublishTarget]:
    """Targets that count towards an article's state. A target for an
    unregistered platform is kept — that is a data problem the UI warns
    about, not a missing config."""
    return [t for t in targets if t.platform not in unconfigured]


def recompute(session: Session, article: Article, unconfigured: set[str]) -> bool:
    """Promote `article` to PUBLISHED when every target that can actually
    publish is done. Never demotes an already-published article. Returns
    whether the state changed."""
    if article.state not in RECOMPUTABLE_STATES:
        return False
    targets = session.exec(
        select(PublishTarget).where(PublishTarget.article_id == article.id)
    ).all()
    effective = effective_targets(targets, unconfigured)
    if not effective or not all(t.status in DONE_STATUSES for t in effective):
        return False
    if not any(t.status == TargetStatus.PUBLISHED for t in effective):
        return False
    article.state = ArticleState.PUBLISHED
    article.updated_at = utcnow()
    session.commit()
    return True


def recompute_all(session: Session, settings: Settings) -> dict[str, int]:
    """Re-derive state for every reviewable article against the currently
    available plugins: give newly configured plugins a target to review,
    then promote whatever is now complete."""
    available = registry.available(settings)
    missing_config = registry.unconfigured(settings)
    articles = session.exec(
        select(Article).where(Article.state.in_(RECOMPUTABLE_STATES))
    ).all()

    added = 0
    for article in articles:
        have = {
            t.platform
            for t in session.exec(
                select(PublishTarget).where(PublishTarget.article_id == article.id)
            ).all()
        }
        for platform in available:
            if platform not in have:
                session.add(PublishTarget(article_id=article.id, platform=platform))
                article.updated_at = utcnow()
                added += 1
    session.commit()

    published = sum(recompute(session, a, missing_config) for a in articles)
    return {"targets_added": added, "articles_published": published}
