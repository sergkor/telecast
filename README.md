# Telecast

Watches Telegram channels for video posts, translates and enhances the text
with Gemini, and republishes video + enhanced English text to a destination
Telegram channel and YouTube after review in a web app.

Design: `docs/superpowers/specs/2026-09-11-telecast-design.md`

## Setup

1. `cp .env.example .env` and fill in credentials:
   - Telegram API id/hash: https://my.telegram.org
   - Bot token: @BotFather (bot must be admin of the destination channel)
   - Gemini API key: https://aistudio.google.com
   - YouTube: create an OAuth desktop client, save `client_secret.json` in the repo root
2. `pip install -e ".[dev]"`
3. One-time auth (writes into `data/`):
   - `telecast auth telegram`
   - `telecast auth youtube`
4. Run: `telecast serve` (or `docker compose up -d` after doing step 3 on the host)
5. Open http://localhost:8000 and log in with `TELECAST_WEB_PASSWORD`.

## Editing the enhancement prompt

Edit `prompts/enhance.md` — it is re-read on every article, no restart needed.

## Tests

    pytest
