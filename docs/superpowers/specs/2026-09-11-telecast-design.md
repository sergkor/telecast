# Telecast — Design Spec

**Date:** 2026-09-11
**Status:** Approved

## Purpose

A single-user Python service that watches source Telegram channels for
video posts, translates the post text to English, enhances it via the
Gemini API, and — after human review in a web app — republishes the
original video with the enhanced English text to a destination Telegram
channel and YouTube. TikTok, X, and Meta are out of scope for v1 but the
design keeps publishing pluggable so they can be added later.

## Scope decisions (settled during brainstorming)

- **Ingestion:** MTProto user account via Telethon (reads any public
  channel the account is subscribed to). Bot API is used only for
  publishing to the destination channel.
- **Video-only:** posts without video media are ignored. Videos are
  re-posted **as-is** — no re-encoding, subtitling, or dubbing.
- **AI pipeline:** two Gemini steps — (1) literal English translation,
  (2) enhancement with a configurable prompt. Both outputs stored and
  shown in the review UI.
- **Publishing v1:** Telegram (Bot API) + YouTube (Data API v3).
  Publisher-per-platform plugin architecture for future platforms.
- **Review model:** one editable final text per article; platform
  variants are auto-derived at publish time. Per-platform
  approve/skip/retry.
- **Scale:** single user, single box. Password login, SQLite, one
  docker-compose service.

## Architecture

Single async monolith: one FastAPI process whose lifespan starts the
background services on the shared asyncio event loop. No queue, no
broker. Crash-safety comes from the DB-backed state machine, not from
process separation.

```
telecast/
├── main.py            # FastAPI app + lifespan starts background services
├── config.py          # pydantic-settings: .env-driven (API keys, channels, prompt)
├── db.py              # SQLModel + SQLite (WAL mode), articles.db
├── models.py          # Article, MediaFile, PublishTarget tables
├── ingest/telegram.py # Telethon client: watches source channels, video posts only
├── pipeline/
│   ├── runner.py      # State-machine worker: picks up articles, advances stages
│   ├── translate.py   # Gemini call 1: literal EN translation
│   └── enhance.py     # Gemini call 2: enhancement with configurable prompt
├── publish/
│   ├── base.py        # Publisher protocol: validate(), adapt_text(), publish()
│   ├── telegram.py    # Bot API → destination channel
│   └── youtube.py     # YouTube Data API v3 resumable upload
└── web/               # Routes + Jinja2/htmx templates (list, detail/edit, approve)
```

### Runtime components

- **Ingestor** — Telethon `NewMessage` handler on configured source
  channels. Accepts only posts containing video media; groups albums
  (`grouped_id`) into one article. Downloads video(s) to `data/media/`,
  extracts a thumbnail (first frame via ffmpeg), inserts an Article in
  state `INGESTED`. On startup, backfills messages missed since the last
  seen message id per channel.
- **Pipeline runner** — single asyncio loop polling the DB for work and
  advancing `INGESTED → TRANSLATED → ENHANCED → PENDING_REVIEW`. Stages
  are idempotent; transient failures retry with backoff; permanent
  failures park the article in a `FAILED_<stage>` state with the error
  stored for the UI.
- **Publisher registry** — dict of platform name → `Publisher`
  implementation. v1 registers `telegram` and `youtube`. Adding a
  platform = new module implementing the protocol + registry entry +
  `.env` credentials; DB and UI already model arbitrary platforms.
- **Web app** — session-cookie login (single password from `.env`);
  review UI and actions (edit, approve per platform, retry, discard).

### Deployment

One `docker-compose.yml`: the app container plus a mounted volume for
SQLite, media files, the Telethon session, and the YouTube refresh
token. One-time interactive auth steps via CLI:

- `telecast auth telegram` — Telethon phone login, writes session file.
- `telecast auth youtube` — OAuth flow, stores refresh token.

## Data model

**Article** (one per source post/album)
- `id`, `source_channel`, `source_message_id`, `grouped_id` (nullable), `source_url`
- `original_text`, `translated_text`, `enhanced_text`, `final_text`
  (editable copy, seeded from `enhanced_text`)
- `title` (nullable; generated during enhancement, editable)
- `state`, `error` (last failure message), `created_at`, `updated_at`
- Unique index on `(source_channel, source_message_id)` — restarts and
  backfills never duplicate articles.

**MediaFile** (1..n per article, for albums)
- `id`, `article_id`, `file_path`, `mime_type`, `duration_s`, `width`,
  `height`, `size_bytes`, `tg_file_unique_id` (dedupe)

**PublishTarget** (one per article × platform, created when the article
reaches `PENDING_REVIEW`)
- `id`, `article_id`, `platform` (`telegram` | `youtube` | future)
- `status`: `PENDING → APPROVED → PUBLISHING → PUBLISHED`, or `SKIPPED` / `FAILED`
- `adapted_text` (filled at publish time), `external_url` (result link),
  `error`, `published_at`

### Article state machine

```
INGESTED → TRANSLATING → TRANSLATED → ENHANCING → ENHANCED → PENDING_REVIEW
                                                                 ↓ (user)
                                                          APPROVED (≥1 target approved)
                                                                 ↓
                                              PUBLISHED (all non-skipped targets done)
Any stage → FAILED_TRANSLATE / FAILED_ENHANCE (retryable from UI)
User anytime → DISCARDED
```

Rules:
- `*ING` states are claim markers: a stale `*ING` row older than 15
  minutes (crashed run) is reset to its previous state for retry — on
  startup and every 5 minutes.
- Editing `final_text`/`title` is allowed in `PENDING_REVIEW`. Approving
  a target freezes that target, not the article — YouTube can be
  approved now, Telegram later.
- Re-running enhancement regenerates `enhanced_text` and re-seeds
  `final_text` only if it was never manually edited.

## AI pipeline

- **Translate** — `gemini-2.5-flash`, structured JSON output:
  `{detected_language, translated_text}`. Literal translation, no
  styling. Empty caption (video-only post) skips translation; the
  enhance stage then uses a describe/contextualize fallback prompt.
- **Enhance** — `gemini-2.5-pro`, prompt template loaded from
  `prompts/enhance.md` (hot-read on every run so the prompt can be
  iterated without restart), translated text injected. Output JSON:
  `{title, article, hashtags[]}`. "Better facts" behavior lives in the
  prompt file, not in code.

## Publishing

Triggered immediately when the user approves a target (no scheduling in
v1). Sequential per article. Each publisher implements:

- `validate(article) -> list[str]` — warnings/blockers (size, duration)
  surfaced in the UI before approval.
- `adapt_text(final_text, title, hashtags) -> str/parts` — platform
  variant derivation.
- `publish(article, media) -> external_url`.

**Telegram:** caption = title + article, ellipsis-safe truncation to the
1024-char caption limit; albums via `send_media_group` with the caption
on the first item; sent from local file via Bot API.

**YouTube:** `title` ≤ 100 chars, `article` as description + hashtags;
resumable upload; privacy status configurable (default `public`); no
Shorts-specific handling (YouTube auto-detects). Album articles upload
the first/longest video only — noted in the UI.

Media caps (configurable; defaults 512 MB, 60 min) do not block
ingestion; oversized media is flagged in the UI as "may fail on
<platform>" and re-checked by `validate()` before upload.

Target status transitions `APPROVED → PUBLISHING → PUBLISHED` with
`external_url` stored; failure → `FAILED` + error + retry button.

## Web app

Server-rendered Jinja2 + htmx; no build step. All state-changing routes
are POST with CSRF token.

- **Login** — single password from `.env`, signed session cookie.
- **Queue** (`/`) — `PENDING_REVIEW` by default; tabs for All / Failed /
  Published / Discarded. Rows show thumbnail, title, source channel,
  age, per-platform status chips.
- **Article detail** (`/articles/{id}`) — video player, original /
  translated / editable final text + title side by side; Save,
  Re-enhance, per-platform Approve / Skip / Retry, Discard; failed
  stages show the stored error with Retry; htmx polls every 3 s while
  any target is `PUBLISHING`.
- **Settings** (`/settings`) — read-only: configured channels, prompt
  file contents, platform credential health.

## Error handling

- Every external call (Telethon, Gemini, publishers) wrapped with
  tenacity: 3 attempts, exponential backoff, transient errors only.
- Permanent errors (4xx, quota, validation) park the row as `FAILED_*`
  or target-`FAILED` with the message stored — nothing silently dropped.
- Gemini quota exhaustion pauses the pipeline; resume check hourly.
- Structured logging (loguru) to stdout + rotating file.

## Testing

pytest + pytest-asyncio.

- Unit: state-machine transitions, text adaptation (caption truncation,
  title limits), dedupe.
- Pipeline stages against faked Gemini/Telethon/YouTube clients (each
  external client behind a thin interface).
- One integration test: fake-ingest → published through the real FastAPI
  app with in-memory SQLite.
- No live-API tests in CI.

## Out of scope (v1)

- TikTok, X, Meta publishers (architecture accommodates them).
- Video processing (re-encode, subtitles, dubbing).
- Scheduling/queued publish times.
- Multi-user auth, roles, audit trail.
- Non-video posts.
