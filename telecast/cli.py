import argparse


def cmd_serve(_args):
    import uvicorn

    from telecast.main import get_app

    uvicorn.run(get_app(), host="0.0.0.0", port=8000)


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


def main():
    parser = argparse.ArgumentParser(prog="telecast")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("serve").set_defaults(fn=cmd_serve)
    auth = sub.add_parser("auth")
    auth_sub = auth.add_subparsers(dest="service", required=True)
    auth_sub.add_parser("telegram").set_defaults(fn=cmd_auth_telegram)
    auth_sub.add_parser("youtube").set_defaults(fn=cmd_auth_youtube)
    args = parser.parse_args()
    args.fn(args)
