import sys

from sqlmodel import Session, select

from telecast import cli
from telecast.db import init_db, make_engine
from telecast.ingest.core import file_checksum
from telecast.models import Article, MediaFile


def test_backfill_checksums_command(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TELECAST_DATA_DIR", str(tmp_path))
    video = tmp_path / "v.mp4"
    video.write_bytes(b"clip")
    engine = make_engine(tmp_path / "articles.db")
    init_db(engine)
    with Session(engine) as s:
        for mid in (1, 2):
            a = Article(source_channel="@a", source_message_id=mid)
            s.add(a)
            s.flush()
            s.add(MediaFile(article_id=a.id, file_path=str(video)))
        s.add(MediaFile(article_id=1, file_path=str(tmp_path / "gone.mp4")))
        s.commit()

    monkeypatch.setattr(sys, "argv", ["telecast", "backfill-checksums"])
    cli.main()

    out = capsys.readouterr().out
    assert "Filled 2 checksums; 1 media rows have no file on disk." in out
    assert "Same media in articles: 1,2" in out
    with Session(engine) as s:
        checksums = s.exec(select(MediaFile.checksum)).all()
    assert checksums.count(file_checksum(video)) == 2
