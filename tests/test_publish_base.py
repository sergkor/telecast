from telecast.config import Settings
from telecast.publish import base
from tests.fakes import StubPublisher


def test_truncate_short_text_unchanged():
    assert base.truncate("hello", 10) == "hello"


def test_truncate_cuts_at_word_boundary_with_ellipsis():
    out = base.truncate("one two three four", 12)
    assert len(out) <= 12
    assert out.endswith("…")
    assert out == "one two…"


def test_registry_register_get_names():
    base.clear()

    class Dummy:
        name = "dummy"

    base.register(Dummy())
    assert base.names() == ["dummy"]
    assert base.get("dummy").name == "dummy"
    base.clear()


def test_available_excludes_publishers_missing_config():
    base.clear()
    base.register(StubPublisher("telegram"))
    base.register(StubPublisher("wordpress", configured=False))
    settings = Settings(_env_file=None)
    assert base.available(settings) == ["telegram"]
    assert base.unconfigured(settings) == {"wordpress"}
    base.clear()


def test_available_treats_publisher_without_configured_as_available():
    base.clear()

    class Legacy:
        name = "legacy"

    base.register(Legacy())
    settings = Settings(_env_file=None)
    assert base.available(settings) == ["legacy"]
    assert base.unconfigured(settings) == set()
    base.clear()
