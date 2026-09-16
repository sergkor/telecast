# WordPress setup for Telecast

How to prepare a self-hosted WordPress site to receive Telecast articles
(video + enhanced text) via the REST API.

## 1. Requirements

- Self-hosted WordPress 5.6+ (Application Passwords are built in).
- The site MUST be served over **HTTPS** — Application Passwords send
  basic-auth credentials on every request.
- Permalinks: **Settings → Permalinks** → anything other than "Plain"
  (e.g. "Post name"). Plain permalinks break the REST API on some hosts.

## 2. Create a dedicated user + Application Password

1. **Users → Add New** — create a user for Telecast (e.g. `telecast-bot`)
   with the **Author** role. Author can create and publish its own posts
   but cannot touch other content, plugins, or settings.
2. Log in as (or edit) that user → **Users → Profile → Application
   Passwords** → name it `telecast` → **Add New Application Password**.
3. Copy the generated password (spaces included are fine) into `.env`:

   ```
   TELECAST_WORDPRESS_URL=https://blog.example.com
   TELECAST_WORDPRESS_USERNAME=telecast-bot
   TELECAST_WORDPRESS_APP_PASSWORD=abcd efgh ijkl mnop qrst uvwx
   ```

4. Optional: `TELECAST_WORDPRESS_STATUS=draft` to review posts in
   WordPress before they go live (default is `publish`).

If the Application Passwords section is missing, the site is not on
HTTPS or a security plugin disabled it — re-enable with
`add_filter('wp_is_application_passwords_available', '__return_true');`.

## 3. Raise upload limits for video

Telecast uploads the original Telegram video (up to hundreds of MB).
Defaults on most hosts (2–64 MB) are far too low.

**php.ini** (or hosting panel → PHP settings):

```ini
upload_max_filesize = 512M
post_max_size = 512M
max_execution_time = 600
memory_limit = 256M
```

**nginx** (if used) — in the server block:

```nginx
client_max_body_size 512m;
```

**Apache** — usually respects php.ini; some hosts also need in
`.htaccess`:

```apache
php_value upload_max_filesize 512M
php_value post_max_size 512M
```

Verify in **Media → Add New** — it shows "Maximum upload file size".
Match the value to `TELECAST_MEDIA_MAX_BYTES` (default 512 MB).

## 4. Serving the articles well

- **Featured image** — Telecast sets the video thumbnail as the post's
  featured image, so any theme with post cards/grids works out of the
  box. Pick a theme that shows featured images on archive pages.
- **Native video player** — posts embed the video as a standard
  `wp-block-video` block with a poster frame; no plugin needed.
- **Caching** — a page cache (WP Super Cache, W3 Total Cache, or host
  level) is recommended; posts are written once and rarely change.
  Exclude `/wp-json/` from caching so publishing keeps working.
- **Video delivery** — videos are served as static files from
  `wp-content/uploads`. If traffic grows, put the site behind a CDN
  (Cloudflare etc.) so video bytes don't hit PHP hosting.
- **Disk space** — every article stores its full video in the media
  library. Monitor hosting disk usage; prune old media if needed.
- **Security plugins** (Wordfence etc.) — allowlist the REST API for
  the `telecast-bot` user if the plugin rate-limits or blocks
  `/wp-json/wp/v2/*` requests.

## 5. Test the connection

```bash
curl -u 'telecast-bot:abcd efgh ijkl mnop qrst uvwx' \
  https://blog.example.com/wp-json/wp/v2/users/me
```

A JSON user object means auth works. Then approve an article for
`wordpress` in the Telecast review UI.
