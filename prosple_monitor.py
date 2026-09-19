"""
Prosple PH internship monitor -> Discord (bot or webhook).

Polls the ph.prosple.com opportunities API for the newest internships
("Internship, Clerkship or Placement"), posts rich embeds to Discord, and
pings whenever a brand-new listing appears.

Two delivery modes, chosen automatically:
  * BOT     -- if DISCORD_BOT_TOKEN + DISCORD_CHANNEL_ID are set. Posts via the
               bot API and includes real "Apply" buttons.
  * WEBHOOK -- otherwise, posts via DISCORD_WEBHOOK_URL. Webhooks can't carry
               buttons, so the apply action is a clickable link in the embed.

On the very first run it seeds the channel with the top 10 latest
internships (no ping), records them as "seen", and then watches for new
ones from there on.

Configuration (read from a .env file in the same folder, or env vars):
    DISCORD_BOT_TOKEN     (bot mode)  bot token from the Developer Portal
    DISCORD_CHANNEL_ID    (bot mode)  target channel id (enable Developer Mode,
                                      right-click channel -> Copy Channel ID)
    DISCORD_WEBHOOK_URL   (webhook)   used only if no bot token/channel is set
    DISCORD_PING          (optional)  text put in the message content for a
                                      NEW listing, e.g. "@here", "@everyone",
                                      or "<@&ROLE_ID>". Empty = no mention.
    POLL_INTERVAL_SECONDS (optional)  seconds between polls (default 300)
    INIT_COUNT            (optional)  how many to seed on first run (default 10)
    FETCH_LIMIT           (optional)  how many to pull each poll (default 30)
    INCLUDE_DESCRIPTION   (optional)  embed the full job description (default 1)

Zero third-party dependencies -- standard library only.
Run with:  python prosple_monitor.py   (add --once for a single cycle)
"""

import gzip
import json
import os
import html
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

# --------------------------------------------------------------------------- #
# Constants discovered from the site's network traffic (HAR capture).
# --------------------------------------------------------------------------- #
API_URL = "https://prosple-gw.global.ssl.fastly.net/internal"

# --------------------------------------------------------------------------- #
# GraphQL documents.
#
# We used to call these operations by their Apollo *persisted-query hash* -- the
# sha256 of a query document registered on Prosple's gateway. That coupled us to
# Prosple's front-end build twice over: the hash rotated on every redeploy, and
# the document behind it was whatever their UI needed, including fields we never
# read. On 2026-09-02 they dropped `remoteAvailable` from the schema, so the
# document behind our pinned hash stopped validating and every poll came back
# `400 Bad Request` -- a failure we could not fix from our side, because the
# server, not us, owned the query text.
#
# The gateway also accepts ordinary ad-hoc queries, so we now send the document
# itself. It asks for exactly the fields this monitor renders, it cannot be
# invalidated by a redeploy, and when Prosple does change the schema the error
# names the offending field instead of a bare 400.
# --------------------------------------------------------------------------- #
SEARCH_OPERATION = "SorppleOpportunitiesSearch"
SEARCH_QUERY = """
query SorppleOpportunitiesSearch($parameters: OpportunitiesSearchInput!) {
  opportunitiesSearch(parameters: $parameters) {
    opportunities {
      id
      title
      detailPageURL
      url
      applyByUrl
      workMode
      expired
      applicationsOpen
      applicationsOpenDate
      applicationsCloseDate
      hideSalary
      minSalary
      maxSalary
      salaryDescription
      minNumberVacancies
      maxNumberVacancies
      salary { currency { label } }
      salaryCurrency { label }
      geoAddresses { label locality }
      studyFields { label }
      opportunityTypes { label }
      overview { summary }
      parentEmployer {
        title
        logo { thumbnail { url } }
        reviewStats { totalNumReviews }
      }
    }
  }
}
"""

DETAIL_OPERATION = "SorppleOpportunityDetail"
# `id`/`gid` are ID! on this schema -- passing them as String! is rejected.
DETAIL_QUERY = """
query SorppleOpportunityDetail($id: ID!, $gid: ID!) {
  opportunity(id: $id, gid: $gid) {
    overview { fullText }
  }
}
"""

# Opportunity-type facet id for "Internship, Clerkship or Placement".
INTERNSHIP_TYPE_ID = "2"
SITE_BASE = "https://ph.prosple.com"
# Discord embed description hard limit.
MAX_DESCRIPTION = 4096

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(SCRIPT_DIR, "state.json")

EMBED_COLOR = 0x6C5CE7  # Prosple-ish purple
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
    ),
    "Origin": SITE_BASE,
    "Referer": SITE_BASE + "/",
    "content-type": "application/json",
    "accept": "*/*",
    "Accept-Language": "en-PH,en-US;q=0.9,en;q=0.8",
}


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
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            # Don't clobber a value already set in the real environment.
            os.environ.setdefault(key, value)


# --------------------------------------------------------------------------- #
# State persistence (which opportunity ids we've already posted).
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
# Logging helper.
# --------------------------------------------------------------------------- #
# Log lines carry em dashes and arrows, and listing titles carry anything at all.
# A legacy Windows console defaults to cp1252/cp437, which cannot encode them: it
# either mangles the output or raises UnicodeEncodeError mid-poll.  Switching the
# stream to UTF-8 with a replacing error handler fixes both, once, at import.
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        pass


# Every log line is also appended here so the web dashboard's console can show
# what the bot is doing without the terminal being visible.  Trimmed in place
# once it grows past LOG_MAX_BYTES, so it never needs external rotation.
LOG_FILE = os.path.join(SCRIPT_DIR, "sorpple.log")
LOG_MAX_BYTES = 512 * 1024
LOG_KEEP_BYTES = 256 * 1024


def _append_log(line):
    """Mirror one line into sorpple.log.  Never raises — logging is not critical."""
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        if os.path.getsize(LOG_FILE) > LOG_MAX_BYTES:
            with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as fh:
                fh.seek(os.path.getsize(LOG_FILE) - LOG_KEEP_BYTES)
                fh.readline()          # drop the half line the seek landed in
                tail = fh.read()
            with open(LOG_FILE, "w", encoding="utf-8") as fh:
                fh.write(tail)
    except OSError:
        pass


def log(message):
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{stamp}] {message}"
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        # Belt and braces: a stream that refused to reconfigure still must not
        # take a poll cycle down over one unprintable glyph.
        encoding = getattr(sys.stdout, "encoding", None) or "ascii"
        print(line.encode(encoding, "replace").decode(encoding), flush=True)
    _append_log(line)


# --------------------------------------------------------------------------- #
# Prosple API.
# --------------------------------------------------------------------------- #
def _graphql(operation, query, variables, timeout=30):
    """POST a GraphQL document and return its `data` payload.

    Raises RuntimeError carrying the server's own message when the query is
    rejected, so a schema change reads as the field that broke rather than as
    an opaque `400 Bad Request`.
    """
    body = json.dumps(
        {"operationName": operation, "query": query, "variables": variables}
    ).encode("utf-8")
    request = urllib.request.Request(
        API_URL, data=body, headers=REQUEST_HEADERS, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            if response.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            payload = json.loads(raw)
    except urllib.error.HTTPError as exc:
        # A GraphQL validation failure is a 400 whose *body* holds the reason.
        detail = ""
        try:
            raw = exc.read()
            if exc.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            errors = json.loads(raw).get("errors") or []
            detail = "; ".join(e.get("message", "") for e in errors)[:400]
        except Exception:  # noqa: BLE001 - never mask the original error
            pass
        if detail:
            raise RuntimeError(f"{operation} rejected ({exc.code}): {detail}") from exc
        raise

    errors = payload.get("errors")
    if errors:
        detail = "; ".join(e.get("message", "") for e in errors)[:400]
        raise RuntimeError(f"{operation} returned errors: {detail}")
    return payload.get("data") or {}


def fetch_internships(limit):
    """Return a list of newest-first internship opportunity dicts."""
    parameters = {
        "gid": "1",
        "range": {"offset": 0, "limit": limit},
        "sortBy": {"criteria": "NEWEST_OPPORTUNITIES", "direction": "DESC"},
        "workRightLocation": "29226",
        "selectedStartDateRangeFacet": None,
        "defaultBid": 0,
        "experiments": {
            "sortByBidPopularityKeyword": "FIFTH_VARIATION",
            "prod2432": False,
        },
        "locationFilter": {"location": "Philippines"},
        "unitOfDistance": "KM",
        # The key filter: only "Internship, Clerkship or Placement".
        "selectedOpportunityTypeFacets": [{"id": INTERNSHIP_TYPE_ID}],
    }
    data = _graphql(SEARCH_OPERATION, SEARCH_QUERY, {"parameters": parameters})
    return _safe(data, "opportunitiesSearch", "opportunities", default=[]) or []


def fetch_description(opp_id, gid="1"):
    """Fetch the full job-description HTML for one opportunity.

    Returns the HTML string, or None if unavailable. Never raises -- a missing
    description should not stop a listing from being posted.
    """
    try:
        data = _graphql(
            DETAIL_OPERATION, DETAIL_QUERY, {"id": str(opp_id), "gid": str(gid)}
        )
        return _safe(data, "opportunity", "overview", "fullText")
    except (urllib.error.URLError, RuntimeError, ValueError, KeyError) as exc:
        log(f"Could not fetch description for {opp_id}: {exc}")
        return None


# --------------------------------------------------------------------------- #
# HTML -> Discord markdown (no third-party dependency).
# --------------------------------------------------------------------------- #
def html_to_markdown(raw_html):
    """Convert the description HTML into Discord-flavoured markdown text."""
    if not raw_html:
        return ""
    text = raw_html

    # Links: <a href="X">label</a> -> [label](X)
    text = re.sub(
        r'<a\b[^>]*?href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
        lambda m: f"[{re.sub(r'<[^>]+>', '', m.group(2)).strip()}]({m.group(1)})",
        text,
        flags=re.I | re.S,
    )
    # Bold / italic.
    text = re.sub(r"</?(strong|b)\b[^>]*>", "**", text, flags=re.I)
    text = re.sub(r"</?(em|i)\b[^>]*>", "*", text, flags=re.I)
    # Headings -> bold line.
    text = re.sub(r"<h[1-6]\b[^>]*>", "\n**", text, flags=re.I)
    text = re.sub(r"</h[1-6]>", "**\n", text, flags=re.I)
    # List items -> bullets.
    text = re.sub(r"<li\b[^>]*>", "\n• ", text, flags=re.I)
    # Line/paragraph breaks.
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</(p|div|ul|ol|tr|h[1-6])>", "\n", text, flags=re.I)
    # Drop every remaining tag, then decode entities.
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    # Tidy whitespace.
    text = "\n".join(line.strip() for line in text.splitlines())
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


# --------------------------------------------------------------------------- #
# Formatting helpers for embeds.
# --------------------------------------------------------------------------- #
def _safe(node, *path, default=None):
    """Safely walk a nested dict/list structure."""
    for key in path:
        if isinstance(node, dict):
            node = node.get(key)
        elif isinstance(node, list) and isinstance(key, int) and -len(node) <= key < len(node):
            node = node[key]
        else:
            return default
        if node is None:
            return default
    return node


def format_date(iso_string, with_relative=False):
    """Turn an ISO timestamp into '12 Jun 2026' (+ optional 'in 5 days')."""
    if not iso_string:
        return None
    try:
        dt = datetime.fromisoformat(iso_string.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return iso_string
    text = dt.strftime("%d %b %Y")
    if with_relative:
        delta_days = (dt - datetime.now(timezone.utc)).days
        if delta_days < 0:
            text += " (closed)"
        elif delta_days == 0:
            text += " (today)"
        elif delta_days == 1:
            text += " (tomorrow)"
        else:
            text += f" (in {delta_days} days)"
    return text


def format_location(opp):
    addresses = opp.get("geoAddresses") or []
    labels = []
    for addr in addresses:
        label = addr.get("label") or addr.get("locality")
        if label and label not in labels:
            labels.append(label)
    location = ", ".join(labels[:3]) if labels else None
    if not location and _is_remote(opp):
        location = "Remote"
    return location or "Philippines"


def _is_remote(opp):
    """Prosple dropped the `remoteAvailable` boolean; workMode now carries it."""
    return "REMOTE" in (opp.get("workMode") or "").upper()


def format_work_mode(opp):
    # e.g. "ON_SITE" -> "On Site", "HYBRID_REMOTE" -> "Hybrid Remote".
    return (opp.get("workMode") or "").replace("_", " ").title() or None


def format_salary(opp):
    if opp.get("hideSalary"):
        return None
    salary = opp.get("salary") or {}
    currency = _safe(salary, "currency", "label") or _safe(opp, "salaryCurrency", "label") or ""
    rate = salary.get("rate")
    rate_text = f" / {rate}" if rate else ""

    stype = salary.get("type")
    amount = None
    if stype == "exact" and salary.get("value") is not None:
        amount = f"{salary['value']:,}"
    elif stype == "range" and isinstance(salary.get("range"), dict):
        low, high = salary["range"].get("min"), salary["range"].get("max")
        if low is not None and high is not None and low != high:
            amount = f"{low:,} - {high:,}"
        elif low is not None or high is not None:
            amount = f"{(low if low is not None else high):,}"

    if amount is None:
        # Fall back to the flat min/max fields.
        low, high = opp.get("minSalary"), opp.get("maxSalary")
        if low and high and low != high:
            amount = f"{low:,} - {high:,}"
        elif low or high:
            amount = f"{(low or high):,}"
        else:
            return None
    return f"{currency} {amount}{rate_text}".strip()


def format_study_fields(opp):
    fields = opp.get("studyFields") or []
    labels = [f.get("label") for f in fields if f.get("label")]
    if not labels:
        return None
    text = ", ".join(labels[:4])
    if len(labels) > 4:
        text += f" +{len(labels) - 4} more"
    return text


def format_vacancies(opp):
    low, high = opp.get("minNumberVacancies"), opp.get("maxNumberVacancies")
    if not low and not high:
        return None
    if low and high and low != high:
        return f"{low}-{high}"
    return str(high or low)


def safe_url(url):
    """Percent-encode a URL so Discord will accept it as a link-button target.

    Employers hand Prosple/SEEK/Indeed apply links with raw spaces and other
    unescaped characters in the query string (e.g. `...&title=Campaigns
    Marketing Intern`). Discord validates button URLs strictly and answers
    `50035 Invalid Form Body ... Not a well formed URL`, which aborted the whole
    poll -- 179 times across the three sources -- and, because the post happens
    before the archive write, dropped those listings from the dashboard too.

    Returns None for anything that is not an http(s) URL, so callers can fall
    back rather than hand Discord something it will reject.
    """
    if not url:
        return None
    url = url.strip()
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    return urllib.parse.urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            urllib.parse.quote(parts.path, safe="/%:@&=+$,~"),
            urllib.parse.quote(parts.query, safe="/?:@&=+$,%~"),
            urllib.parse.quote(parts.fragment, safe="/?:@&=+$,%~"),
        )
    )


def resolve_apply(opp, detail_url):
    """Return (label, url) for the apply action.

    Prefer the employer's own application URL (`applyByUrl`) when Prosple has
    one -- that link goes straight to the company's careers site / ATS and
    skips Prosple's sign-up wall. Otherwise the employer only accepts
    applications through Prosple, so fall back to the Prosple listing page.
    """
    external = safe_url(opp.get("applyByUrl") or opp.get("url"))
    if external:
        return "Apply on company site ↗", external
    return "Apply on Prosple ↗", safe_url(detail_url) or detail_url


def build_components(opp, detail_url):
    """Build a Discord action row of link buttons (bot mode only).

    Webhooks cannot send these -- only a bot/application can.
    """
    apply_label, apply_url = resolve_apply(opp, detail_url)
    label = apply_label.replace(chr(0x2197), "").strip()[:80]
    buttons = [{"type": 2, "style": 5, "label": label, "url": apply_url}]
    if apply_url != detail_url:
        buttons.append({"type": 2, "style": 5, "label": "View on Prosple", "url": detail_url})
    return [{"type": 1, "components": buttons}]


def build_description_block(opp, detail_url, include_full_description, include_apply_links=True):
    """Build the embed description: optional apply link(s) plus a collapsible JD."""
    header = ""
    apply_label, apply_url = resolve_apply(opp, detail_url)
    if include_apply_links and apply_url == detail_url:
        # Application goes through Prosple itself -- one link is enough.
        header = f"**[{apply_label}]({apply_url})**"
    elif include_apply_links:
        header = f"**[{apply_label}]({apply_url})** • [View on Prosple ↗]({detail_url})"

    if not include_full_description:
        return header

    raw = fetch_description(opp.get("id"))
    body = html_to_markdown(raw)
    if not body:
        # Fall back to the short summary if there's no full text.
        body = (_safe(opp, "overview", "summary") or "").strip()
    if not body:
        return header

    # Discord has no real "collapsible", but spoiler tags (||...||) blur the
    # text until the reader clicks it -- the closest native equivalent.
    body = body.replace("||", "| |")  # don't let the JD break out of the spoiler
    label = "\n\n\U0001f4cb **Job description** *(click to reveal)*\n"
    budget = MAX_DESCRIPTION - len(header) - len(label) - len("||||") - 40
    truncated = False
    if len(body) > budget:
        body = body[:budget].rsplit(" ", 1)[0]
        truncated = True
    spoiler = f"||{body}||"
    if truncated:
        spoiler += f"\n*Truncated — [read the full description ↗]({detail_url})*"
    return header + label + spoiler


def build_embed(opp, include_full_description=True, include_apply_links=True):
    """Build a Discord embed dict with all relevant internship details."""
    title = opp.get("title") or "Untitled internship"
    detail_path = opp.get("detailPageURL") or ""
    url = SITE_BASE + detail_path if detail_path.startswith("/") else detail_path

    employer = opp.get("parentEmployer") or {}
    employer_name = employer.get("title") or employer.get("advertiserName") or "Unknown employer"
    logo = _safe(employer, "logo", "thumbnail", "url")
    rating = _safe(
        employer, "reviewStats", "statsPerCategory", "overallSatisfaction", "averageRating"
    )
    num_reviews = _safe(employer, "reviewStats", "totalNumReviews")

    opp_types = ", ".join(t.get("label", "") for t in opp.get("opportunityTypes") or []) or "Internship"

    fields = []

    def add_field(name, value, inline=True):
        if value:
            fields.append({"name": name, "value": str(value)[:1024], "inline": inline})

    add_field("🏢 Employer", employer_name)
    add_field("💼 Type", opp_types)
    add_field("📍 Location", format_location(opp))
    add_field("🏠 Work mode", format_work_mode(opp))
    add_field("💰 Salary", format_salary(opp) or "Not disclosed")
    add_field("👥 Vacancies", format_vacancies(opp))
    add_field("📅 Opens", format_date(opp.get("applicationsOpenDate")))
    add_field("⏳ Closes", format_date(opp.get("applicationsCloseDate"), with_relative=True))
    if rating:
        stars = f"{rating:.1f}/10"
        if num_reviews:
            stars += f" ({num_reviews} reviews)"
        add_field("⭐ Employer rating", stars)
    add_field("🎓 Study fields", format_study_fields(opp), inline=False)

    embed = {
        "title": title[:256],
        "url": url,
        "color": EMBED_COLOR,
        "fields": fields,
        "footer": {"text": f"Prosple PH • ID {opp.get('id')}"},
    }

    description = build_description_block(opp, url, include_full_description, include_apply_links).strip()
    if description:
        embed["description"] = description[:MAX_DESCRIPTION]
    if logo:
        embed["thumbnail"] = {"url": logo}
    open_date = opp.get("applicationsOpenDate")
    if open_date:
        embed["timestamp"] = open_date.replace("Z", "+00:00")

    return embed


# --------------------------------------------------------------------------- #
# Discord delivery (bot API or webhook -- chosen automatically).
# --------------------------------------------------------------------------- #
DISCORD_API = "https://discord.com/api/v10"


def _post_json(url, headers, body):
    """POST one JSON message, retrying on 429 / transient network errors."""
    data = json.dumps(body).encode("utf-8")
    base_headers = {"Content-Type": "application/json", "User-Agent": "prosple-monitor"}
    base_headers.update(headers)
    request = urllib.request.Request(url, data=data, headers=base_headers, method="POST")
    for attempt in range(5):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                response.read()
                return True
        except urllib.error.HTTPError as exc:
            if exc.code == 429:  # rate limited
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


def send_message(config, embeds, content=None, components=None):
    """Send one message via the bot API (if configured) or the webhook."""
    body = {
        "embeds": embeds[:10],
        # Only allow the explicit mentions we put in `content`.
        "allowed_mentions": {"parse": ["everyone", "roles"]},
    }
    if content:
        body["content"] = content[:2000]

    if config["mode"] == "bot":
        # Buttons are only deliverable through the bot API.
        if components:
            body["components"] = components
        url = f"{DISCORD_API}/channels/{config['channel_id']}/messages"
        headers = {"Authorization": f"Bot {config['bot_token']}"}
        return _post_json(url, headers, body)

    return _post_json(config["webhook_url"], {}, body)


# --------------------------------------------------------------------------- #
# Core monitor logic.
# --------------------------------------------------------------------------- #
def render(opp, config):
    """Return (embed, components) for one opportunity, tailored to the mode.

    In bot mode the apply action is a real button, so it's left out of the
    embed text. In webhook mode buttons aren't possible, so the apply links
    live inside the embed description instead.
    """
    bot = config["mode"] == "bot"
    detail_path = opp.get("detailPageURL") or ""
    detail_url = SITE_BASE + detail_path if detail_path.startswith("/") else detail_path
    embed = build_embed(
        opp,
        include_full_description=config["include_description"],
        include_apply_links=not bot,
    )
    components = build_components(opp, detail_url) if bot else None
    return embed, components


def run_once(config, state):
    """One poll cycle. Returns the (possibly updated) state."""
    opportunities = fetch_internships(config["fetch_limit"])
    if not opportunities:
        log("No internships returned by the API.")
        return state

    seen = set(state["seen_ids"])

    if not state["initialized"]:
        # First run: post the latest INIT_COUNT (no ping)...
        latest = opportunities[: config["init_count"]]
        log(f"Initialization: posting top {len(latest)} latest internships.")
        # Post oldest-first so the newest ends up at the bottom of the channel.
        for opp in reversed(latest):
            embed, components = render(opp, config)
            send_message(config, [embed], components=components)
            time.sleep(1)  # be gentle with the service
        # ...but mark EVERY currently-listed internship as seen, so the
        # already-existing ones below the top 10 are never mistaken for "new"
        # arrivals and pinged on the next poll.
        for opp in opportunities:
            seen.add(opp["id"])
        state["initialized"] = True
        state["seen_ids"] = sorted(seen)
        save_state(state)
        log("Initialization complete.")
        return state

    # Subsequent runs: anything we haven't posted before is new.
    new_opps = [opp for opp in opportunities if opp["id"] not in seen]
    if not new_opps:
        log("No new internships.")
        return state

    log(f"Found {len(new_opps)} new internship(s); posting with ping.")
    # Post oldest-first so the newest reads last/most-recent in the channel.
    for opp in reversed(new_opps):
        ping = config["ping"] or None
        title = opp.get("title", "internship")
        employer = _safe(opp, "parentEmployer", "title") or "an employer"
        content = f"🆕 **New internship** at {employer}: {title}"
        if ping:
            content = f"{ping} {content}"
        embed, components = render(opp, config)
        ok = send_message(config, [embed], content=content, components=components)
        if ok:
            seen.add(opp["id"])
        time.sleep(1)

    state["seen_ids"] = sorted(seen)
    save_state(state)
    return state


def load_config():
    load_dotenv(os.path.join(SCRIPT_DIR, ".env"))
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    bot_token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    channel_id = os.environ.get("DISCORD_CHANNEL_ID", "").strip()

    # Prefer bot mode (real Apply buttons) when both token and channel are set;
    # otherwise fall back to the webhook (clickable links instead of buttons).
    if bot_token and channel_id:
        mode = "bot"
    elif webhook_url and "XXXXXXXX" not in webhook_url:
        mode = "webhook"
    else:
        log(
            "ERROR: configure either DISCORD_BOT_TOKEN + DISCORD_CHANNEL_ID "
            "(bot mode, with buttons) or DISCORD_WEBHOOK_URL (webhook mode) in .env."
        )
        sys.exit(1)

    def _int(name, default):
        try:
            return max(1, int(os.environ.get(name, default)))
        except ValueError:
            return default

    def _bool(name, default):
        return os.environ.get(name, str(default)).strip().lower() not in ("0", "false", "no", "off")

    return {
        "mode": mode,
        "webhook_url": webhook_url,
        "bot_token": bot_token,
        "channel_id": channel_id,
        "ping": os.environ.get("DISCORD_PING", "").strip(),
        "poll_interval": _int("POLL_INTERVAL_SECONDS", 300),
        "init_count": _int("INIT_COUNT", 10),
        "fetch_limit": _int("FETCH_LIMIT", 30),
        "include_description": _bool("INCLUDE_DESCRIPTION", True),
    }


def main():
    config = load_config()
    run_forever = "--once" not in sys.argv
    state = load_state()

    if not run_forever:
        log("Running a single poll cycle (--once).")
        state = run_once(config, state)
        return

    log(
        f"Starting Prosple PH internship monitor in {config['mode'].upper()} mode. "
        f"Poll every {config['poll_interval']}s, "
        f"ping={'(none)' if not config['ping'] else config['ping']}."
    )

    while True:
        try:
            state = run_once(config, state)
        except urllib.error.HTTPError as exc:
            log(f"API HTTP error {exc.code}: {exc.reason}; will retry next cycle.")
        except urllib.error.URLError as exc:
            log(f"Network error reaching API: {exc}; will retry next cycle.")
        except Exception as exc:  # noqa: BLE001 - keep the loop alive
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
