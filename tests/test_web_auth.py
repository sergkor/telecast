import pytest
from fastapi.testclient import TestClient

from telecast.config import Settings
from telecast.web.app import create_app


@pytest.fixture
def client(session_factory, tmp_path):
    settings = Settings(_env_file=None, web_password="pw", secret_key="s3cret",
                        data_dir=tmp_path)
    app = create_app(settings, session_factory)
    return TestClient(app)


def test_unauthenticated_redirects_to_login(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_wrong_password_rejected(client):
    r = client.post("/login", data={"password": "nope"})
    assert "Invalid password" in r.text


def test_login_sets_cookies_and_grants_access(client):
    r = client.post("/login", data={"password": "pw"}, follow_redirects=False)
    assert r.status_code == 303
    assert "telecast_session" in r.cookies
    assert "telecast_csrf" in r.cookies
    r2 = client.get("/")
    assert r2.status_code == 200
