# WordPress setup for Telecast

How to prepare a self-hosted WordPress site to receive Telecast articles
via the REST API.

Telecast does **not** upload the video to WordPress. A post is published
right after the article's YouTube upload finishes, and embeds that
YouTube video; only the poster frame is uploaded, as the featured image.
A WordPress target therefore waits for its `youtube` sibling — approve
both, or the post never goes up.

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

## 3. Uploads

Only the video thumbnail (a JPEG of a few hundred KB) is uploaded, so the
default host limits are fine. No php.ini or nginx tuning is needed.

## 4. Serving the articles well

- **Featured image** — Telecast sets the video thumbnail as the post's
  featured image, so any theme with post cards/grids works out of the
  box. Pick a theme that shows featured images on archive pages.
- **YouTube player** — posts open with a standard `wp:embed` block
  pointing at the YouTube URL; WordPress' built-in oEmbed renders the
  responsive player, no plugin needed. The post ends with a link to the
  video on YouTube and one to the Telegram channel
  (`TELECAST_TELEGRAM_CHANNEL_URL`; omitted when unset).
- **Caching** — a page cache (WP Super Cache, W3 Total Cache, or host
  level) is recommended; posts are written once and rarely change.
  Exclude `/wp-json/` from caching so publishing keeps working.
- **Video delivery** — video bytes are served by YouTube, never by the
  WordPress host.
- **Security plugins** (Wordfence etc.) — allowlist the REST API for
  the `telecast-bot` user if the plugin rate-limits or blocks
  `/wp-json/wp/v2/*` requests.

## 5. Test the connection

```bash
curl -u 'telecast-bot:abcd efgh ijkl mnop qrst uvwx' \
  https://blog.example.com/wp-json/wp/v2/users/me
```

A JSON user object means auth works. The Telecast settings page does the
same check behind **Validate connection** on the `wordpress` row: it reports
the user it connected as and whether that user may publish posts, without
writing anything to the site.

Then approve an article for both
`youtube` and `wordpress` in the Telecast review UI — the post appears
seconds after the YouTube upload completes.
