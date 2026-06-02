"""
JobStreet PH internship monitor → Discord (bot or webhook).

Polls ph.jobstreet.com for the newest "intern" listings in the Philippines,
posts rich embeds to Discord, and pings on new postings.

Reads the same .env file as the other monitors.  State is tracked in a separate
jobstreet_state.json so all three monitors can run side-by-side.

JobStreet embeds the full job list in window.SEEK_REDUX_DATA in the page HTML,
so no separate API key or authentication is required.

Proxy support: same proxies.txt format as indeed_monitor.py.
Override path with JOBSTREET_PROXIES_FILE; disable with JOBSTREET_USE_PROXIES=false.

Run:  python jobstreet_monitor.py            (continuous polling)
      python jobstreet_monitor.py --once     (single poll then exit)
"""

import gzip
import html
import http.cookiejar
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
# URL from HAR capture — percent-encoded quotes are intentional and required.
SEARCH_URL    = "https://ph.jobstreet.com/%22intern%22-jobs/in-Philippines"
SEARCH_PARAMS = {"sortmode": "ListedDate"}
SITE_BASE     = "https://ph.jobstreet.com"
EMBED_COLOR   = 0xF04A00   # JobStreet orange
MAX_DESCRIPTION = 4096

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE  = os.path.join(SCRIPT_DIR, "jobstreet_state.json")

# Browser headers from the HAR capture (Chrome 149 / Windows 11).
# Accept-Encoding omits "br" (brotli) — Python stdlib decompresses gzip only.
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

# When navigating to a job detail page from the search results, the browser
# sends same-origin context headers.
_DETAIL_HEADERS = {
    **REQUEST_HEADERS,
    "sec-fetch-site": "same-origin",
    "Referer":        SEARCH_URL + "?" + urllib.parse.urlencode(SEARCH_PARAMS),
}

# Pattern to locate the embedded Redux state blob.
# JobStreet (SEEK) SSR injects all search results as:
#   window.SEEK_REDUX_DATA = {...};
_REDUX_RE = re.compile(r'window\.SEEK_REDUX_DATA\s*=\s*')

DISCORD_API = "https://discord.com/api/v10"

# Shared cookie jar: session tokens set on the search page are forwarded to
# subsequent job-detail fetches automatically.
_COOKIE_JAR = http.cookiejar.CookieJar()


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
# State persistence.
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
    if not os.path.exists(path):
        return []
    entries = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(":", 3)
            if len(parts) != 4:
                continue
            host, port, user, password = parts
            u = urllib.parse.quote(user, safe="")
            p = urllib.parse.quote(password, safe="")
            entries.append(f"http://{u}:{p}@{host}:{port}")
    return entries


# --------------------------------------------------------------------------- #
# HTTP fetch (proxy-aware, gzip-transparent, cookie-persistent).
# --------------------------------------------------------------------------- #
def _http_get(url, proxy_url=None, headers=None):
    """GET a URL, returning decoded HTML.  Shares _COOKIE_JAR across calls."""
    handlers = [urllib.request.HTTPCookieProcessor(_COOKIE_JAR)]
    if proxy_url:
        handlers.append(urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url}))
    else:
        handlers.append(urllib.request.ProxyHandler({}))
    opener = urllib.request.build_opener(*handlers)
    req = urllib.request.Request(url, headers=headers or REQUEST_HEADERS)
    with opener.open(req, timeout=30) as resp:
        raw = resp.read()
        if resp.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
    return raw.decode("utf-8", errors="replace")


# --------------------------------------------------------------------------- #
# JobStreet HTML → job list extraction.
# --------------------------------------------------------------------------- #
def _extract_redux(page_html):
    """
    Parse window.SEEK_REDUX_DATA from the page HTML using raw_decode,
    which handles the arbitrarily-nested JSON without a fragile end-delimiter.
    Returns the parsed dict, or None on failure.
    """
    m = _REDUX_RE.search(page_html)
    if not m:
        return None
    try:
        data, _ = json.JSONDecoder().raw_decode(page_html, m.end())
        return data
    except json.JSONDecodeError:
        return None


def _normalize(raw):
    """
    Map a raw SEEK job dict to a consistent, flat shape.

    Key fields in a SEEK job result:
      id              — numeric job ID (string after normalization)
      title           — job title
      companyName     — company name
      locations[]     — [{label, countryCode, ...}]
      classifications[] — [{classification: {description}, subclassification: {description}}]
      workTypes[]     — ["Full time", "Part time", ...]
      salaryLabel     — salary string if disclosed (often empty)
      listingDate     — ISO 8601 timestamp
      listingDateDisplay — relative string ("34m ago", "2d ago")
      teaser          — short description paragraph
      bulletPoints[]  — 3 key highlights for premium listings
      branding.serpLogoUrl — company logo thumbnail
    """
    jid = str(raw.get("id") or "")
    if not jid:
        return None

    locations = raw.get("locations") or []
    location = locations[0]["label"] if locations else "Philippines"

    classifications = raw.get("classifications") or []
    classification = None
    if classifications:
        classification = (classifications[0].get("classification") or {}).get("description")

    work_types = raw.get("workTypes") or []
    work_type = work_types[0] if work_types else None

    branding = raw.get("branding") or {}
    logo = branding.get("serpLogoUrl")

    return {
        "id":                   jid,
        "title":                raw.get("title") or "Untitled",
        "company":              raw.get("companyName") or "Unknown company",
        "location":             location,
        "classification":       classification,
        "work_type":            work_type,
        "salary_label":         raw.get("salaryLabel") or None,
        "listing_date":         raw.get("listingDate"),
        "listing_date_display": raw.get("listingDateDisplay"),
        "teaser":               raw.get("teaser") or "",
        "bullet_points":        raw.get("bulletPoints") or [],
        "logo_url":             logo,
        "job_url":              f"{SITE_BASE}/job/{jid}",
    }


def fetch_jobs(limit, proxies=None):
    """
    Scrape the JobStreet PH intern search page and return normalized job dicts.

    JobStreet SSR-renders all 30 results into window.SEEK_REDUX_DATA in the
    initial HTML, so a single GET is sufficient per poll.
    Returns an empty list on failure.
    """
    proxy_url = random.choice(proxies) if proxies else None
    url = SEARCH_URL + "?" + urllib.parse.urlencode(SEARCH_PARAMS)

    try:
        page = _http_get(url, proxy_url=proxy_url)
    except urllib.error.HTTPError as exc:
        log(f"HTTP {exc.code} fetching JobStreet search page.")
        return []
    except urllib.error.URLError as exc:
        log(f"Network error fetching JobStreet: {exc}")
        return []

    redux = _extract_redux(page)
    if not redux:
        log("Could not find SEEK_REDUX_DATA in JobStreet HTML (structure may have changed).")
        log(f"  Page preview: {page[:400]!r}")
        return []

    raw_jobs = (
        redux.get("results", {})
             .get("results", {})
             .get("jobs") or []
    )
    jobs = [_normalize(r) for r in raw_jobs]
    jobs = [j for j in jobs if j]
    return jobs[:limit]


def fetch_description(jid, proxies=None):
    """
    Fetch the full job description HTML for one JobStreet listing.

    Fetches the job detail page (/job/{id}) and extracts the description from
    window.SEEK_REDUX_DATA.jobdetails.result — the same Redux pattern used on
    the search page, populated with full job data on the detail page.

    Returns the raw HTML string, or None if unavailable.  Never raises.
    """
    try:
        proxy_url = random.choice(proxies) if proxies else None
        url = f"{SITE_BASE}/job/{jid}"
        page = _http_get(url, proxy_url=proxy_url, headers=_DETAIL_HEADERS)

        redux = _extract_redux(page)
        if redux:
            result = (redux.get("jobdetails") or {}).get("result") or {}
            desc = (
                result.get("jobDescription")
                or result.get("description")
                or (result.get("details") or {}).get("jobDescription")
            )
            if desc:
                return desc

        return None
    except Exception as exc:  # noqa: BLE001 — missing JD should not block posting
        log(f"Could not fetch description for {jid}: {exc}")
        return None


# --------------------------------------------------------------------------- #
# HTML → Discord markdown.
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
def _format_listed(job):
    """Return a human-readable listing date."""
    disp = job.get("listing_date_display")
    iso  = job.get("listing_date")
    if iso:
        try:
            dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
            delta = (datetime.now(timezone.utc) - dt).days
            label = dt.strftime("%d %b %Y")
            if delta == 0 and disp:
                label += f" ({disp})"
            elif delta == 0:
                label += " (today)"
            elif delta == 1:
                label += " (yesterday)"
            else:
                label += f" ({delta} days ago)"
            return label
        except (ValueError, AttributeError):
            pass
    return disp or None


def build_embed(job, include_teaser=True, include_apply_links=True):
    """Return a Discord embed dict for one JobStreet listing.

    include_teaser       — embed the short teaser/bullet highlights.
                           Set False in bot mode (📋 button shows full JD).
    include_apply_links  — put the view link in the description text.
                           Set False in bot mode (link button carries it).
    """
    job_url = job["job_url"]

    fields = []

    def add(name, value, inline=True):
        if value:
            fields.append({"name": name, "value": str(value)[:1024], "inline": inline})

    add("🏢 Company",      job["company"])
    add("📍 Location",     job["location"])
    if job.get("work_type"):
        add("💼 Work type", job["work_type"])
    if job.get("classification"):
        add("🗂 Field",     job["classification"])
    if job.get("salary_label"):
        add("💰 Salary",   job["salary_label"])
    listed = _format_listed(job)
    if listed:
        add("📅 Listed",   listed)

    parts = []

    if include_apply_links:
        parts.append(f"**[View / Apply ↗]({job_url})**")

    if include_teaser:
        bullets = job.get("bullet_points") or []
        teaser  = job.get("teaser") or ""
        if bullets:
            parts.append("• " + "\n• ".join(bullets))
        elif teaser:
            parts.append(teaser)

    embed = {
        "title":  job["title"][:256],
        "url":    job_url,
        "color":  EMBED_COLOR,
        "fields": fields,
        "footer": {"text": f"JobStreet PH • {job['id']}"},
    }
    if parts:
        embed["description"] = "\n\n".join(parts)[:MAX_DESCRIPTION]
    if job.get("logo_url"):
        embed["thumbnail"] = {"url": job["logo_url"]}

    return embed


# --------------------------------------------------------------------------- #
# Discord delivery (bot API or webhook).
# --------------------------------------------------------------------------- #
def _post_json(url, headers, body):
    data = json.dumps(body).encode("utf-8")
    base = {"Content-Type": "application/json", "User-Agent": "jobstreet-monitor"}
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
        log("No jobs returned from JobStreet.")
        return state

    seen = set(state["seen_ids"])

    if not state["initialized"]:
        latest = jobs[: config["init_count"]]
        log(f"Initialization: posting top {len(latest)} latest JobStreet listings.")
        for job in reversed(latest):
            send_message(config, [build_embed(job)])
            time.sleep(1)
        for job in jobs:
            seen.add(job["id"])
        state["initialized"] = True
        state["seen_ids"] = sorted(seen)
        save_state(state)
        log("Initialization complete.")
        return state

    new_jobs = [j for j in jobs if j["id"] not in seen]
    if not new_jobs:
        log("No new JobStreet listings.")
        return state

    log(f"Found {len(new_jobs)} new JobStreet listing(s); posting.")
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

    proxies_file = os.environ.get(
        "JOBSTREET_PROXIES_FILE",
        os.path.join(SCRIPT_DIR, "proxies.txt"),
    )
    use_proxies = os.environ.get("JOBSTREET_USE_PROXIES", "").strip().lower()
    if use_proxies in ("1", "true", "yes", "on"):
        proxies = load_proxies(proxies_file)
        log(f"Loaded {len(proxies)} proxies from {os.path.basename(proxies_file)}.")
    else:
        proxies = []

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
        f"Starting JobStreet PH internship monitor in {config['mode'].upper()} mode. "
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
