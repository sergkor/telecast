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


# columns added after the initial release; create_all won't alter existing tables
_MIGRATIONS = {
    "article": {
        "approved_at": "TIMESTAMP",
        "scheduled_at": "TIMESTAMP",
    },
    "mediafile": {
        "checksum": "VARCHAR",
    },
}
_INDEXES = {
    "ix_mediafile_checksum": "mediafile (checksum)",
}


def init_db(engine) -> None:
    SQLModel.metadata.create_all(engine)
    with engine.connect() as conn:
        for table, columns in _MIGRATIONS.items():
            cols = {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")}
            for name, sql_type in columns.items():
                if name not in cols:
                    conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}")
        for index, target in _INDEXES.items():
            conn.exec_driver_sql(f"CREATE INDEX IF NOT EXISTS {index} ON {target}")
        conn.commit()


def make_session_factory(engine):
    return lambda: Session(engine)
