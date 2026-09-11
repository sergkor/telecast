from telecast.publish import base


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
