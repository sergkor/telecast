from pathlib import Path

from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine


def make_engine(db_path: Path | str):
    engine = create_engine(
        f"sqlite:///{db_path}", connect_args={"check_same_thread": False}
    )

    @event.listens_for(engine, "connect")
    def _set_wal(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    return engine


def init_db(engine) -> None:
    SQLModel.metadata.create_all(engine)


def make_session_factory(engine):
    return lambda: Session(engine)
