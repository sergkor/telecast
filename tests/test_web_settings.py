import pytest
from fastapi.testclient import TestClient

from telecast.config import Settings
from telecast.web.app import create_app


@pytest.fixture
def client(session_factory, tmp_path):
    prompt = tmp_path / "enhance.md"
    prompt.write_text("PROMPT BODY {source_text}")
    settings = Settings(_env_file=None, web_password="pw", secret_key="s",
                        data_dir=tmp_path, enhance_prompt_path=prompt,
                        source_channels="@a,@b", dest_channel="@dest")
    app = create_app(settings, session_factory)
    c = TestClient(app)
    c.post("/login", data={"password": "pw"})
    return c


def test_settings_page_shows_config_and_health(client):
    r = client.get("/settings")
    assert r.status_code == 200
    assert "@a" in r.text and "@dest" in r.text
    assert "PROMPT BODY" in r.text
    assert "missing" in r.text.lower()  # youtube token not present
