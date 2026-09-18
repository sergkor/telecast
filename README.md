# Telecast

Watches Telegram channels for video posts, translates and enhances the text
with Gemini, and republishes video + enhanced English text to configured
platforms after review in a web app.

Design: `docs/superpowers/specs/2026-09-11-telecast-design.md`

## Supported publishers

| Platform | Auth | Settings |
|----------|------|----------|
| Telegram | `TELECAST_BOT_TOKEN` (bot must be admin) | `TELECAST_DEST_CHANNEL` |
| YouTube | `client_secret.json` + `telecast auth youtube` | `TELECAST_YOUTUBE_PRIVACY`, `TELECAST_TELEGRAM_CHANNEL_URL` (appended to descriptions) |
| TikTok | `telecast auth tiktok` | `TELECAST_TIKTOK_CLIENT_KEY`, `TELECAST_TIKTOK_CLIENT_SECRET`, `TELECAST_TIKTOK_PRIVACY` |
| Pinterest | `telecast auth pinterest` | `TELECAST_PINTEREST_APP_ID`, `TELECAST_PINTEREST_APP_SECRET`, `TELECAST_PINTEREST_BOARD_ID` |
| WordPress | Application Password in `.env` (no auth command) | `TELECAST_WORDPRESS_URL`, `TELECAST_WORDPRESS_USERNAME`, `TELECAST_WORDPRESS_APP_PASSWORD`, `TELECAST_WORDPRESS_STATUS` — see `docs/wordpress-setup.md` |

Each article gets a per-platform approve/skip in the review UI. Adding a
platform = new module in `telecast/publish/` implementing the `Publisher`
protocol + a `registry.register(...)` call in `telecast/main.py`.

## Plugins without config

A publisher whose credentials are missing is inert: it gets no publish
target for new articles, its existing targets are hidden from the queue and
the article page, and it is ignored when deciding whether an article is
fully published. So an article never waits on a platform you have not set
up. Each publisher decides this itself via `configured(settings)`.

**Settings → Publishing plugins** lists every registered plugin as
*configured* / *not configured*, with a **Recalculate state** button. Press
it after changing `.env` (or running an `auth` command): it gives newly
configured plugins a `PENDING` target on every article still in review, then
promotes to `PUBLISHED` any article whose available targets are all done. It
never pulls a published article back into review.

## Republishing

Published articles can be sent to a platform again (e.g. after switching
YouTube channels):

- **Bulk:** Published tab → tick the articles (header checkbox = select all)
  → pick the platform → **Republish**. Articles without a target for that
  platform get one created.
- **Single:** article page → **Republish** button on a published target.

Republished targets go back through the normal publish schedule, so a large
batch won't blow the YouTube daily upload quota all at once — but note the
default quota only allows ~6 uploads/day.

## Stuck articles

Ingested articles are translated, enhanced and opened for review by a
background loop. That loop pauses for an hour after a Gemini quota error, so a
backlog can build up in `INGESTED` / `TRANSLATED` / `ENHANCED`.

**Settings → Pipeline** shows how many articles are waiting and offers
**Process stuck articles**, which drains that backlog in the background and
returns to the settings page immediately. It stops on the first quota error
rather than burning the rest of the backlog against an exhausted key.

## Publish schedule

Approving a target queues its article rather than publishing immediately:

- **Queue empty** → the article publishes `TELECAST_PUBLISH_DELAY_MINUTES`
  from now (default 5), leaving a window to undo a mistaken approval.
- **Queue not empty** → it publishes `TELECAST_PUBLISH_INTERVAL_HOURS`
  (default 6) after the last article already in the queue.

Slots are append-only: once assigned, an article's time never moves, so
approving something else — or approving a second platform on the same
article — cannot shuffle what you already saw. A queue left unpublished
through a downtime restarts its spacing from now instead of firing the whole
backlog at once. The queue shows each pending slot as a `⏱` chip, and the
article page has **Publish now** to jump the queue.

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
   - `telecast auth tiktok` (optional)
   - `telecast auth pinterest` (optional; set `TELECAST_PINTEREST_BOARD_ID` first)
4. Run: `telecast serve` (or `docker compose up -d` after doing step 3 on the host)
5. Open http://localhost:8000 and log in with `TELECAST_WEB_PASSWORD`.

## Editing the enhancement prompt

Edit `prompts/enhance.md` — it is re-read on every article, no restart needed.

## Tests

    pytest
