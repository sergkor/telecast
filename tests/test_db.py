from sqlalchemy import text

from telecast.db import init_db, make_engine


def test_init_db_adds_schedule_columns_to_old_article_table(tmp_path):
    db_path = tmp_path / "articles.db"
    engine = make_engine(db_path)
    # simulate a pre-queue database: article table without the new columns
    with engine.connect() as conn:
        conn.execute(text(
            "CREATE TABLE article ("
            "id INTEGER PRIMARY KEY, source_channel VARCHAR, source_message_id INTEGER)"
        ))
        conn.commit()
    init_db(engine)
    with engine.connect() as conn:
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(article)"))}
    assert "approved_at" in cols
    assert "scheduled_at" in cols


def test_init_db_adds_checksum_column_to_old_mediafile_table(tmp_path):
    engine = make_engine(tmp_path / "articles.db")
    with engine.connect() as conn:
        conn.execute(text(
            "CREATE TABLE mediafile (id INTEGER PRIMARY KEY, article_id INTEGER, file_path VARCHAR)"
        ))
        conn.commit()
    init_db(engine)
    init_db(engine)  # idempotent
    with engine.connect() as conn:
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(mediafile)"))}
        indexes = {row[1] for row in conn.execute(text("PRAGMA index_list(mediafile)"))}
    assert "checksum" in cols
    assert "ix_mediafile_checksum" in indexes
