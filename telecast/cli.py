import argparse


def cmd_serve(_args):
    import uvicorn

    from telecast.main import get_app

    uvicorn.run(get_app(), host="0.0.0.0", port=8000)


def cmd_backfill_checksums(_args):
    from sqlalchemy import func
    from sqlmodel import select

    from telecast.config import Settings
    from telecast.db import init_db, make_engine, make_session_factory
    from telecast.ingest.core import backfill_checksums
    from telecast.models import MediaFile

    settings = Settings()
    engine = make_engine(settings.db_path)
    init_db(engine)
    with make_session_factory(engine)() as session:
        filled = backfill_checksums(session)
        missing = session.exec(
            select(func.count()).select_from(MediaFile).where(MediaFile.checksum.is_(None))
        ).one()
        groups = session.exec(
            select(func.group_concat(MediaFile.article_id))
            .where(MediaFile.checksum.is_not(None))
            .group_by(MediaFile.checksum)
            .having(func.count(func.distinct(MediaFile.article_id)) > 1)
        ).all()
    print(f"Filled {filled} checksums; {missing} media rows have no file on disk.")
    for ids in groups:
        print(f"Same media in articles: {ids}")


def cmd_auth_telegram(_args):
    import asyncio

    from telethon import TelegramClient

    from telecast.config import Settings

    settings = Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)

    async def login():
        client = TelegramClient(str(settings.telethon_session_path),
                                settings.telegram_api_id, settings.telegram_api_hash)
        await client.start()  # interactive: asks for phone + code
        me = await client.get_me()
        print(f"Logged in as {me.username or me.id}; session saved to {settings.telethon_session_path}")
        await client.disconnect()

    asyncio.run(login())


def cmd_auth_youtube(_args):
    from google_auth_oauthlib.flow import InstalledAppFlow

    from telecast.config import Settings
    from telecast.publish.youtube import SCOPES

    settings = Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    flow = InstalledAppFlow.from_client_secrets_file("client_secret.json", SCOPES)
    creds = flow.run_local_server(port=0)
    settings.youtube_token_path.write_text(creds.to_json())
    print(f"YouTube token saved to {settings.youtube_token_path}")


def cmd_auth_tiktok(_args):
    import hashlib
    import http.server
    import json
    import secrets
    import urllib.parse
    import webbrowser

    import httpx

    from telecast.config import Settings
    from telecast.publish.tiktok import API, AUTH_URL, SCOPES

    settings = Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    port = 8321
    redirect_uri = f"http://127.0.0.1:{port}/callback"
    state = secrets.token_urlsafe(16)
    verifier = secrets.token_urlsafe(43)
    # TikTok PKCE uses hex-encoded SHA256, not base64url
    challenge = hashlib.sha256(verifier.encode()).hexdigest()
    url = AUTH_URL + "?" + urllib.parse.urlencode({
        "client_key": settings.tiktok_client_key,
        "scope": SCOPES,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })
    result = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            result.update({k: v[0] for k, v in params.items()})
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Authorized. You can close this tab.")

        def log_message(self, *a):
            pass

    print(f"Register {redirect_uri} as a redirect URI in your TikTok app, then authorize:")
    print(url)
    webbrowser.open(url)
    with http.server.HTTPServer(("127.0.0.1", port), Handler) as server:
        while "code" not in result and "error" not in result:
            server.handle_request()
    if result.get("error"):
        raise SystemExit(f"tiktok auth failed: {result['error']}")
    if result.get("state") != state:
        raise SystemExit("tiktok auth failed: state mismatch")
    resp = httpx.post(f"{API}/oauth/token/", data={
        "client_key": settings.tiktok_client_key,
        "client_secret": settings.tiktok_client_secret,
        "code": result["code"],
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
        "code_verifier": verifier,
    })
    resp.raise_for_status()
    token = resp.json()
    if "refresh_token" not in token:
        raise SystemExit(f"tiktok auth failed: {token}")
    settings.tiktok_token_path.write_text(json.dumps(token))
    print(f"TikTok token saved to {settings.tiktok_token_path}")


def cmd_auth_pinterest(_args):
    import http.server
    import json
    import secrets
    import urllib.parse
    import webbrowser

    import httpx

    from telecast.config import Settings
    from telecast.publish.pinterest import API, AUTH_URL, SCOPES

    settings = Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    if not settings.pinterest_board_id:
        raise SystemExit("set TELECAST_PINTEREST_BOARD_ID first")
    port = 8321
    redirect_uri = f"http://localhost:{port}/callback"
    state = secrets.token_urlsafe(16)
    url = AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": settings.pinterest_app_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPES,
        "state": state,
    })
    result = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            result.update({k: v[0] for k, v in params.items()})
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Authorized. You can close this tab.")

        def log_message(self, *a):
            pass

    print(f"Register {redirect_uri} as a redirect URI in your Pinterest app, then authorize:")
    print(url)
    webbrowser.open(url)
    with http.server.HTTPServer(("127.0.0.1", port), Handler) as server:
        while "code" not in result and "error" not in result:
            server.handle_request()
    if result.get("error"):
        raise SystemExit(f"pinterest auth failed: {result['error']}")
    if result.get("state") != state:
        raise SystemExit("pinterest auth failed: state mismatch")
    resp = httpx.post(f"{API}/oauth/token", data={
        "grant_type": "authorization_code",
        "code": result["code"],
        "redirect_uri": redirect_uri,
        "continuous_refresh": "true",
    }, auth=(settings.pinterest_app_id, settings.pinterest_app_secret))
    resp.raise_for_status()
    token = resp.json()
    if "refresh_token" not in token:
        raise SystemExit(f"pinterest auth failed: {token}")
    board = httpx.get(f"{API}/boards/{settings.pinterest_board_id}",
                      headers={"Authorization": f"Bearer {token['access_token']}"})
    if board.status_code != 200:
        raise SystemExit(
            f"pinterest board {settings.pinterest_board_id} not accessible: {board.text}")
    settings.pinterest_token_path.write_text(json.dumps(token))
    print(f"Pinterest token saved to {settings.pinterest_token_path} "
          f"(board: {board.json().get('name')})")


def main():
    parser = argparse.ArgumentParser(prog="telecast")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("serve").set_defaults(fn=cmd_serve)
    sub.add_parser(
        "backfill-checksums",
        help="compute missing media checksums used for duplicate detection",
    ).set_defaults(fn=cmd_backfill_checksums)
    auth = sub.add_parser("auth")
    auth_sub = auth.add_subparsers(dest="service", required=True)
    auth_sub.add_parser("telegram").set_defaults(fn=cmd_auth_telegram)
    auth_sub.add_parser("youtube").set_defaults(fn=cmd_auth_youtube)
    auth_sub.add_parser("tiktok").set_defaults(fn=cmd_auth_tiktok)
    auth_sub.add_parser("pinterest").set_defaults(fn=cmd_auth_pinterest)
    args = parser.parse_args()
    args.fn(args)
