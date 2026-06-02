"""
Indeed PH internship monitor → Discord (bot or webhook).

Polls ph.indeed.com/jobs for the newest internship listings in the Philippines,
posts rich embeds to Discord, and pings on new postings.

Reads the same .env file as prosple_monitor.py.  State is tracked in a separate
indeed_state.json so both monitors can run side-by-side without interference.

Proxy rotation: if proxies.txt (Wolveproxy format: host:port:user:password)
exists in the script directory, requests automatically rotate through the list
to reduce the chance of Cloudflare challenges.  Override the file path with the
INDEED_PROXIES_FILE env var, or disable entirely with INDEED_USE_PROXIES=false.

Run:  python indeed_monitor.py            (continuous polling)
      python indeed_monitor.py --once     (single poll then exit)

Note: Indeed returns ~15 results per search page.  FETCH_LIMIT values above 15
are satisfied if possible, but a single page fetch is enough for a 5-minute
polling interval -- new listings rarely exceed 15 per poll.
"""

import gzip
import html
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone


# --------------------------------------------------------------------------- #
# Constants.
# --------------------------------------------------------------------------- #
SEARCH_URL   = "https://ph.indeed.com/jobs"
SEARCH_QUERY = {"q": "intern", "l": "Philippines", "sort": "date"}
SITE_BASE    = "https://ph.indeed.com"
EMBED_COLOR  = 0x2164F3   # Indeed blue
MAX_DESCRIPTION = 4096

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE  = os.path.join(SCRIPT_DIR, "indeed_state.json")

# Browser-like headers from the HAR capture (Chrome 149 / Windows 11).
# Accept-Encoding deliberately omits "br" (brotli): Python stdlib can only
# decompress gzip, so we let the server fall back to gzip automatically.
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8,"
        "application/signed-exchange;v=b3;q=0.7"
    ),
    "Accept-Language":           "en-US,en;q=0.9",
    "Accept-Encoding":           "gzip, deflate",
    "Cache-Control":             "max-age=0",
    "sec-ch-ua":                 '"Google Chrome";v="149", "Chromium";v="149", "Not)A;Brand";v="24"',
    "sec-ch-ua-mobile":          "?0",
    "sec-ch-ua-platform":        '"Windows"',
    "sec-ch-ua-platform-version": '"19.0.0"',
    "sec-fetch-dest":            "document",
    "sec-fetch-mode":            "navigate",
    "sec-fetch-site":            "none",
    "sec-fetch-user":            "?1",
    "Upgrade-Insecure-Requests": "1",
}

# Pattern that locates the job-card data blob embedded in Indeed's HTML.
# Indeed's Mosaic architecture injects all search results as:
#   window.mosaic.providerData["mosaic-provider-jobcards"] = {...};
_JOBCARDS_RE = re.compile(
    r'window\.mosaic\.providerData\["mosaic-provider-jobcards"\]\s*=\s*'
)

DISCORD_API = "https://discord.com/api/v10"


# --------------------------------------------------------------------------- #
# Tiny .env loader (no python-dotenv dependency).
# --------------------------------------------------------------------------- #
def load_dotenv(path):
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


# --------------------------------------------------------------------------- #
# Logging.
# --------------------------------------------------------------------------- #
def log(message):
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{stamp}] {message}", flush=True)


# --------------------------------------------------------------------------- #
# State persistence (which job keys we've already posted).
# --------------------------------------------------------------------------- #
def load_state():
    if not os.path.exists(STATE_FILE):
        return {"initialized": False, "seen_ids": []}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        data.setdefault("initialized", False)
        data.setdefault("seen_ids", [])
        return data
    except (json.JSONDecodeError, OSError) as exc:
        log(f"WARNING: could not read state file ({exc}); starting fresh.")
        return {"initialized": False, "seen_ids": []}


def save_state(state):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2)
    os.replace(tmp, STATE_FILE)


# --------------------------------------------------------------------------- #
# Proxy loading (Wolveproxy format: host:port:user:password).
# --------------------------------------------------------------------------- #
def load_proxies(path):
    """
    Parse a Wolveproxy-format proxy list and return ready-to-use proxy URLs.

    Each line: host:port:user:password
    Returns a list of "http://user:pass@host:port" strings.
    """
    if not os.path.exists(path):
        return []
    entries = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            # Split on ":" but only into 4 parts; passwords could theoretically
            # contain colons, though Wolveproxy passwords are hex-only in practice.
            parts = line.split(":", 3)
            if len(parts) != 4:
                continue
            host, port, user, password = parts
            # URL-encode credentials in case of special characters.
            u = urllib.parse.quote(user, safe="")
            p = urllib.parse.quote(password, safe="")
            entries.append(f"http://{u}:{p}@{host}:{port}")
    return entries


# --------------------------------------------------------------------------- #
# HTTP fetch (proxy-aware, gzip-transparent).
# --------------------------------------------------------------------------- #
def _http_get(url, proxy_url=None):
    """GET a URL and return decoded HTML.  Handles gzip decompression."""
    if proxy_url:
        handler = urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})
    else:
        handler = urllib.request.ProxyHandler({})   # explicitly no proxy
    opener = urllib.request.build_opener(handler)
    req = urllib.request.Request(url, headers=REQUEST_HEADERS)
    with opener.open(req, timeout=30) as resp:
        raw = resp.read()
        if resp.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
    return raw.decode("utf-8", errors="replace")


# --------------------------------------------------------------------------- #
# Indeed HTML → job list extraction.
# --------------------------------------------------------------------------- #
def _extract_results(page_html):
    """
    Pull the raw job-card list out of Indeed's embedded JavaScript.

    Uses json.JSONDecoder.raw_decode() to parse the JSON value cleanly from
    the middle of an inline script block, avoiding fragile end-delimiter regexes.

    Returns the list of raw result dicts, or None on failure.
    """
    m = _JOBCARDS_RE.search(page_html)
    if not m:
        return None
    try:
        data, _ = json.JSONDecoder().raw_decode(page_html, m.end())
    except json.JSONDecodeError:
        return None

    # Indeed uses two possible nesting depths; try both.
    results = (
        data.get("metaData", {})
            .get("mosaicProviderJobCardsModel", {})
            .get("results")
        or data.get("results")
    )
    return results or None


def _normalize(raw):
    """
    Map a raw Indeed result dict to a consistent, flat shape.

    Key fields in a typical result:
      jobkey / jk          — unique job identifier (used for dedup)
      displayTitle         — job title (preferred over "title")
      company              — company name
      formattedLocation    — location string
      salarySnippet.text   — salary if disclosed
      created              — post timestamp in Unix milliseconds
      snippet              — short HTML job summary (3-4 lines)
      thirdPartyApplyUrl   — external ATS link when available
      companyBrandingAttributes.logoUrl — company logo thumbnail
    """
    jk = raw.get("jobkey") or raw.get("jk") or raw.get("jobKey") or ""
    if not jk:
        return None

    salary_text = (raw.get("salarySnippet") or {}).get("text")

    # Canonical job URL (no tracking tokens) and external apply link.
    job_url   = f"{SITE_BASE}/viewjob?jk={jk}"
    apply_url = raw.get("thirdPartyApplyUrl") or job_url

    logo = (
        (raw.get("companyBrandingAttributes") or {}).get("logoUrl")
        or raw.get("companyLogo")
    )

    return {
        "id":           jk,
        "title":        raw.get("displayTitle") or raw.get("title") or "Untitled",
        "company":      raw.get("company") or raw.get("companyName") or "Unknown company",
        "location":     raw.get("formattedLocation") or raw.get("jobLocationCity") or "Philippines",
        "salary":       salary_text,
        "created_ms":   raw.get("created"),
        "date_str":     raw.get("date") or raw.get("formattedRelativeTime"),
        "snippet_html": raw.get("snippet") or "",
        "job_url":      job_url,
        "apply_url":    apply_url,
        "logo_url":     logo,
    }


def fetch_jobs(limit, proxies=None):
    """
    Scrape the Indeed PH intern search page and return normalized job dicts.

    Picks a random proxy from `proxies` (if provided) for each call.
    Returns an empty list on Cloudflare block (403) or parse failure.
    """
    proxy_url = random.choice(proxies) if proxies else None
    url = SEARCH_URL + "?" + urllib.parse.urlencode(SEARCH_QUERY)

    try:
        page = _http_get(url, proxy_url=proxy_url)
    except urllib.error.HTTPError as exc:
        if exc.code == 403:
            log("Indeed returned 403 (Cloudflare block). Try enabling/rotating proxies.")
        else:
            log(f"HTTP {exc.code} fetching Indeed search page.")
        return []
    except urllib.error.URLError as exc:
        log(f"Network error fetching Indeed: {exc}")
        return []

    results = _extract_results(page)
    if results is None:
        log("Could not find job-card data in Indeed HTML (page structure may have changed).")
        log(f"  Page preview: {page[:400]!r}")
        return []

    jobs = [_normalize(r) for r in results]
    jobs = [j for j in jobs if j]
    return jobs[:limit]


# --------------------------------------------------------------------------- #
# HTML snippet → Discord markdown (same approach as prosple_monitor.py).
# --------------------------------------------------------------------------- #
def html_to_markdown(raw_html):
    if not raw_html:
        return ""
    text = raw_html
    text = re.sub(
        r'<a\b[^>]*?href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
        lambda m: f"[{re.sub(r'<[^>]+>', '', m.group(2)).strip()}]({m.group(1)})",
        text, flags=re.I | re.S,
    )
    text = re.sub(r"</?(strong|b)\b[^>]*>", "**", text, flags=re.I)
    text = re.sub(r"</?(em|i)\b[^>]*>", "*", text, flags=re.I)
    text = re.sub(r"<h[1-6]\b[^>]*>", "\n**", text, flags=re.I)
    text = re.sub(r"</h[1-6]>", "**\n", text, flags=re.I)
    text = re.sub(r"<li\b[^>]*>", "\n• ", text, flags=re.I)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</(p|div|ul|ol|tr|h[1-6])>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = "\n".join(line.strip() for line in text.splitlines())
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


# --------------------------------------------------------------------------- #
# Embed builder.
# --------------------------------------------------------------------------- #
def _format_posted(job):
    """Return a human-readable posting date from the Unix-ms timestamp or relative string."""
    ms = job.get("created_ms")
    if ms:
        try:
            dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
            delta = (datetime.now(timezone.utc) - dt).days
            label = dt.strftime("%d %b %Y")
            if delta == 0:
                label += " (today)"
            elif delta == 1:
                label += " (yesterday)"
            else:
                label += f" ({delta} days ago)"
            return label
        except (ValueError, OSError):
            pass
    return job.get("date_str") or None


def build_embed(job):
    """Return a Discord embed dict for one Indeed listing."""
    job_url   = job["job_url"]
    apply_url = job["apply_url"]

    fields = []

    def add(name, value, inline=True):
        if value:
            fields.append({"name": name, "value": str(value)[:1024], "inline": inline})

    add("🏢 Company",  job["company"])
    add("📍 Location", job["location"])
    if job.get("salary"):
        add("💰 Salary", job["salary"])
    posted = _format_posted(job)
    if posted:
        add("📅 Posted", posted)

    snippet = html_to_markdown(job.get("snippet_html", ""))

    # Apply / view links always live in the description (Indeed has no standalone
    # external-apply modal like Prosple, so both links are always useful).
    if apply_url != job_url:
        link_line = f"**[Apply ↗]({apply_url})** • [View on Indeed ↗]({job_url})"
    else:
        link_line = f"**[View / Apply ↗]({job_url})**"

    description = link_line
    if snippet:
        description += f"\n\n{snippet}"
    description = description[:MAX_DESCRIPTION]

    embed = {
        "title":       job["title"][:256],
        "url":         job_url,
        "color":       EMBED_COLOR,
        "description": description,
        "fields":      fields,
        "footer":      {"text": f"Indeed PH • {job['id']}"},
    }
    if job.get("logo_url"):
        embed["thumbnail"] = {"url": job["logo_url"]}

    return embed


# --------------------------------------------------------------------------- #
# Discord delivery (bot API or webhook).
# --------------------------------------------------------------------------- #
def _post_json(url, headers, body):
    data = json.dumps(body).encode("utf-8")
    base = {"Content-Type": "application/json", "User-Agent": "indeed-monitor"}
    base.update(headers)
    req = urllib.request.Request(url, data=data, headers=base, method="POST")
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                resp.read()
                return True
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                retry_after = 1.0
                try:
                    info = json.loads(exc.read().decode("utf-8"))
                    retry_after = float(info.get("retry_after", 1.0))
                except (ValueError, OSError):
                    pass
                log(f"Rate limited by Discord; waiting {retry_after:.1f}s.")
                time.sleep(retry_after + 0.5)
                continue
            log(f"Discord error {exc.code}: {exc.read().decode('utf-8', 'replace')[:300]}")
            return False
        except urllib.error.URLError as exc:
            log(f"Network error posting to Discord: {exc}; retrying.")
            time.sleep(2 * (attempt + 1))
    return False


def send_message(config, embeds, content=None):
    body = {
        "embeds": embeds[:10],
        "allowed_mentions": {"parse": ["everyone", "roles"]},
    }
    if content:
        body["content"] = content[:2000]

    if config["mode"] == "bot":
        url = f"{DISCORD_API}/channels/{config['channel_id']}/messages"
        return _post_json(url, {"Authorization": f"Bot {config['bot_token']}"}, body)

    return _post_json(config["webhook_url"], {}, body)


# --------------------------------------------------------------------------- #
# Core monitor logic.
# --------------------------------------------------------------------------- #
def run_once(config, state, proxies):
    """One poll cycle.  Returns the (possibly updated) state dict."""
    jobs = fetch_jobs(config["fetch_limit"], proxies=proxies)
    if not jobs:
        log("No jobs returned from Indeed.")
        return state

    seen = set(state["seen_ids"])

    if not state["initialized"]:
        latest = jobs[: config["init_count"]]
        log(f"Initialization: posting top {len(latest)} latest Indeed listings.")
        for job in reversed(latest):   # oldest-first so newest lands last in channel
            send_message(config, [build_embed(job)])
            time.sleep(1)
        for job in jobs:               # mark the whole current board as seen
            seen.add(job["id"])
        state["initialized"] = True
        state["seen_ids"] = sorted(seen)
        save_state(state)
        log("Initialization complete.")
        return state

    new_jobs = [j for j in jobs if j["id"] not in seen]
    if not new_jobs:
        log("No new Indeed listings.")
        return state

    log(f"Found {len(new_jobs)} new Indeed listing(s); posting.")
    ping = config.get("ping") or None
    for job in reversed(new_jobs):
        content = f"🆕 **New internship** at {job['company']}: {job['title']}"
        if ping:
            content = f"{ping} {content}"
        ok = send_message(config, [build_embed(job)], content=content)
        if ok:
            seen.add(job["id"])
        time.sleep(1)

    state["seen_ids"] = sorted(seen)
    save_state(state)
    return state


# --------------------------------------------------------------------------- #
# Config loading.
# --------------------------------------------------------------------------- #
def load_config():
    load_dotenv(os.path.join(SCRIPT_DIR, ".env"))

    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    bot_token   = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    channel_id  = os.environ.get("DISCORD_CHANNEL_ID", "").strip()

    if bot_token and channel_id:
        mode = "bot"
    elif webhook_url and "XXXXXXXX" not in webhook_url:
        mode = "webhook"
    else:
        log(
            "ERROR: configure either DISCORD_BOT_TOKEN + DISCORD_CHANNEL_ID "
            "(bot mode) or DISCORD_WEBHOOK_URL (webhook mode) in .env."
        )
        sys.exit(1)

    def _int(name, default):
        try:
            return max(1, int(os.environ.get(name, default)))
        except ValueError:
            return default

    # Proxy setup: enabled by default if proxies.txt exists.
    proxies_file = os.environ.get(
        "INDEED_PROXIES_FILE",
        os.path.join(SCRIPT_DIR, "proxies.txt"),
    )
    use_proxies = os.environ.get("INDEED_USE_PROXIES", "").strip().lower()
    if use_proxies in ("0", "false", "no", "off"):
        proxies = []
        log("Proxies disabled via INDEED_USE_PROXIES.")
    else:
        proxies = load_proxies(proxies_file)
        if proxies:
            log(f"Loaded {len(proxies)} proxies from {os.path.basename(proxies_file)}.")
        else:
            log("No proxy file found; making direct requests (Cloudflare may block).")

    return {
        "mode":          mode,
        "webhook_url":   webhook_url,
        "bot_token":     bot_token,
        "channel_id":    channel_id,
        "ping":          os.environ.get("DISCORD_PING", "").strip(),
        "poll_interval": _int("POLL_INTERVAL_SECONDS", 300),
        "init_count":    _int("INIT_COUNT", 10),
        "fetch_limit":   _int("FETCH_LIMIT", 30),
        "proxies":       proxies,
    }


# --------------------------------------------------------------------------- #
# Entry point.
# --------------------------------------------------------------------------- #
def main():
    config = load_config()
    proxies = config.pop("proxies")
    state   = load_state()

    if "--once" in sys.argv:
        log("Running a single poll cycle (--once).")
        run_once(config, state, proxies)
        return

    proxy_note = f"{len(proxies)} proxies loaded" if proxies else "no proxies — direct requests"
    log(
        f"Starting Indeed PH internship monitor in {config['mode'].upper()} mode. "
        f"Poll every {config['poll_interval']}s, {proxy_note}."
    )

    while True:
        try:
            state = run_once(config, state, proxies)
        except urllib.error.HTTPError as exc:
            log(f"HTTP error {exc.code}; will retry next cycle.")
        except urllib.error.URLError as exc:
            log(f"Network error: {exc}; will retry next cycle.")
        except Exception as exc:  # noqa: BLE001 — keep the loop alive
            log(f"Unexpected error: {exc!r}; will retry next cycle.")

        try:
            time.sleep(config["poll_interval"])
        except KeyboardInterrupt:
            log("Stopped by user. Bye!")
            break


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("Stopped by user. Bye!")
