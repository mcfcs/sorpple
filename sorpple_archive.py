"""
Listing archive — the store behind the web dashboard's browser view.

The three state files (state.json, indeed_state.json, jobstreet_state.json) track
only *seen ids*: whether a listing has been posted to Discord.  Everything else --
title, employer, salary, closing date -- is fetched, rendered into an embed, and
thrown away.  That is all the Discord bot ever needed, but it leaves nothing for a
website to search, filter, or age out.

This module keeps a parallel record with the content itself, written to
listings.json as each listing is posted.  It never touches the state files, so a
corrupt or deleted archive cannot change what the bot posts.

Each source hands us a different native shape (Prosple a nested GraphQL object,
Indeed and JobStreet flat dicts), so one adapter per source maps them onto a
common record, reusing the same formatting helpers the embeds use.

    record("prosple", opp, years=["2027"])   # called from sorpple.py's poll cycle
    load()                                   # -> {"listings": [...]}, for the API
"""

import json
import os
import re
from datetime import datetime, timedelta, timezone

from prosple_monitor import (
    SCRIPT_DIR,
    SITE_BASE as PROSPLE_BASE,
    _safe,
    format_location,
    format_salary,
    format_work_mode,
    html_to_markdown as prosple_html_to_markdown,
    log,
    resolve_apply,
)
from indeed_monitor import html_to_markdown as indeed_html_to_markdown

ARCHIVE_FILE = os.path.join(SCRIPT_DIR, "listings.json")

# Enough to give the dashboard a real corpus without letting the file grow without
# bound the way seen_ids does.  Oldest first_seen is dropped first.
MAX_LISTINGS = 5000

# Teasers are for scanning in a card, not reading in full -- the listing links out.
TEASER_CHARS = 400


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _iso(value) -> str | None:
    """Normalize a source's date field to an ISO 8601 string, or None.

    Accepts what the three boards actually emit: an ISO string (Prosple,
    JobStreet) or epoch milliseconds (Indeed's `created`).
    """
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value / 1000, tz=timezone.utc).isoformat()
        except (ValueError, OSError, OverflowError):
            return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).isoformat()
    except ValueError:
        # Keep the raw string rather than dropping it; the UI treats an
        # unparseable date the same as a missing one.
        return text


_RELATIVE_RE = re.compile(r"(\d+)\s*\+?\s*(minute|hour|day|week|month)", re.I)

_RELATIVE_UNITS = {
    "minute": 1 / 1440,
    "hour":   1 / 24,
    "day":    1,
    "week":   7,
    "month":  30,
}


def _relative_to_iso(label: str | None) -> str | None:
    """Turn Indeed's relative date label into an absolute timestamp.

    Indeed's search blob almost never carries the numeric `created` field, but it
    always carries a phrase like "Just posted", "Today", or "3 days ago".  Anchor
    that to now so listings can still be sorted and aged by date.

    The result is approximate by design — a listing shown as "30+ days ago" is
    recorded as 30 days old.  That is accurate enough to sort and age by, which
    is all the dashboard asks of it.
    """
    if not label:
        return None
    text = str(label).strip().lower()
    now = datetime.now(timezone.utc)

    if "just posted" in text or "today" in text or "just now" in text:
        return now.isoformat()

    match = _RELATIVE_RE.search(text)
    if not match:
        return None
    amount = int(match.group(1))
    days = amount * _RELATIVE_UNITS[match.group(2).lower()]
    return (now - timedelta(days=days)).isoformat()


# Discord markdown emphasis, and the bullet glyphs html_to_markdown() emits for
# <li>.  The embeds want these; the web UI renders plain text, so they would show
# up literally as "**Intern**" on a card.
_EMPHASIS_RE = re.compile(r"\*{1,3}(?=\S)(.+?)(?<=\S)\*{1,3}", re.S)
_MD_LINK_RE  = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_BULLET_RE   = re.compile(r"[•‣▪]\s*")
# A leftover emphasis marker, matched only where markdown could have put one:
# hugging the start or end of a word.  That spares "snake_case" and "2*3", which
# are ordinary text a blanket strip would mangle.
_STRAY_MARK_RE = re.compile(r"(?<![\w*_])[*_]{1,3}(?=\w)|(?<=\w)[*_]{1,3}(?![\w*_])")


def _plain(text: str | None) -> str:
    """Strip the markdown the embed layer adds, leaving readable prose."""
    if not text:
        return ""
    text = _MD_LINK_RE.sub(r"\1", str(text))
    text = _EMPHASIS_RE.sub(r"\1", text)
    # A bullet list collapsed onto one line reads better separated than run
    # together, so bullets become separators rather than vanishing.
    text = _BULLET_RE.sub(" · ", text)
    # Markers left unpaired by the source ("**Brand Intern" with no closer, or a
    # pair the clip below would cut in half) are dropped outright — a stray "**"
    # on a card is worse than losing the emphasis.
    text = _STRAY_MARK_RE.sub("", text)
    text = text.replace("`", "")
    return " ".join(text.split()).lstrip("· ").strip()


def _clip(text: str | None, limit: int = TEASER_CHARS) -> str:
    text = _plain(text)
    if not text:
        return ""
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


# ── Per-source adapters ───────────────────────────────────────────────────────
# Each returns the same record shape, so the dashboard never branches on source.
#
# Closing status differs by board and the shape reflects that honestly:
#   `closed`    — the board's own verdict.  Only Prosple has one (it keeps expired
#                 listings in the feed and flags them); SEEK and Indeed simply drop
#                 expired ads, so anything still visible there is live.
#   `closes_at` — an actual deadline.  Prosple only, and often absent even there.
# A listing with neither shows as "unknown" in the UI and is aged by `posted_at`.


def _from_prosple(opp: dict) -> dict:
    detail_path = opp.get("detailPageURL") or ""
    url = PROSPLE_BASE + detail_path if detail_path.startswith("/") else detail_path
    _, apply_url = resolve_apply(opp, url)
    employer = opp.get("parentEmployer") or {}

    # Prosple publishes its own verdict on whether applications are still open.
    # That beats inferring from applicationsCloseDate, which is often absent even
    # on a listing Prosple already considers expired.
    if opp.get("expired"):
        closed = True
    elif opp.get("applicationsOpen") is False:
        closed = True
    else:
        closed = False

    return {
        "closed":         closed,
        "start_date":     _safe(opp, "startDate", "category", "label"),
        "id":             str(opp.get("id") or ""),
        "title":          opp.get("title") or "Untitled internship",
        "company":        employer.get("title") or employer.get("advertiserName") or "Unknown employer",
        "location":       format_location(opp),
        "url":            url,
        "apply_url":      apply_url if apply_url != url else None,
        "logo_url":       _safe(employer, "logo", "thumbnail", "url"),
        "salary":         format_salary(opp),
        "work_type":      format_work_mode(opp),
        "classification": ", ".join(
            t.get("label", "") for t in opp.get("opportunityTypes") or []
        ) or "Internship",
        "posted_at":      _iso(opp.get("applicationsOpenDate")),
        "posted_label":   None,
        "opens_at":       _iso(opp.get("applicationsOpenDate")),
        "closes_at":      _iso(opp.get("applicationsCloseDate")),
        "teaser":         _clip(_safe(opp, "overview", "summary")),
    }


def _from_jobstreet(job: dict) -> dict:
    return {
        # SEEK delists expired ads rather than flagging them, so anything we can
        # still see is live; the UI ages these by posted_at instead.
        "closed":         False,
        "start_date":     None,
        "id":             str(job.get("id") or ""),
        "title":          job.get("title") or "Untitled",
        "company":        job.get("company") or "Unknown company",
        "location":       job.get("location") or "Philippines",
        "url":            job.get("job_url"),
        "apply_url":      None,
        "logo_url":       job.get("logo_url"),
        "salary":         job.get("salary_label"),
        "work_type":      job.get("work_type"),
        "classification": job.get("classification"),
        "posted_at":      _iso(job.get("listing_date")),
        "posted_label":   job.get("listing_date_display"),
        "opens_at":       None,
        "closes_at":      None,
        "teaser":         _clip(job.get("teaser") or " · ".join(job.get("bullet_points") or [])),
    }


def _from_indeed(job: dict) -> dict:
    return {
        # Same as JobStreet: Indeed drops expired postings from the search feed.
        "closed":         False,
        "start_date":     None,
        "id":             str(job.get("id") or ""),
        "title":          job.get("title") or "Untitled",
        "company":        job.get("company") or "Unknown company",
        "location":       job.get("location") or "Philippines",
        "url":            job.get("job_url"),
        "apply_url":      job.get("apply_url"),
        "logo_url":       job.get("logo_url"),
        "salary":         job.get("salary"),
        "work_type":      None,
        "classification": None,
        # Indeed's `created` is usually absent from the search blob; its relative
        # `date` string ("Just posted", "3 days ago") is what actually ships, so
        # resolve that to a timestamp rather than losing the posting date.
        "posted_at":      _iso(job.get("created_ms")) or _relative_to_iso(job.get("date_str")),
        "posted_label":   job.get("date_str"),
        "opens_at":       None,
        "closes_at":      None,
        "teaser":         _clip(indeed_html_to_markdown(job.get("snippet_html") or "")),
    }


ADAPTERS = {
    "prosple":   _from_prosple,
    "jobstreet": _from_jobstreet,
    "indeed":    _from_indeed,
}


# ── Storage ───────────────────────────────────────────────────────────────────


def description(source_key: str, listing_id: str) -> tuple[str | None, str]:
    """Full job description for one listing, as markdown.

    Returns (text, origin) where origin is "cache" or "fetched".  Descriptions
    are fetched from the board on first request and then stored on the listing,
    so opening the same one twice costs nothing — this matters for Indeed, where
    every fetch spends a paid proxy request.

    Returns (None, ...) when the board has no description for it.
    """
    data  = load()
    index = {f"{e['source']}:{e['id']}": e for e in data["listings"]}
    entry = index.get(f"{source_key}:{listing_id}")
    if entry is None:
        return None, "unknown"

    if entry.get("description"):
        return entry["description"], "cache"

    text = _fetch_description(source_key, listing_id)
    if text:
        entry["description"] = text
        try:
            save(data)
        except OSError as exc:
            log(f"[archive] Could not cache description: {exc!r}")
    return text, "fetched"


def _fetch_description(source_key: str, listing_id: str) -> str | None:
    """Pull one description from its board and render it as markdown.

    Imported lazily: the scrapers pull in proxy lists and are only needed when a
    description is actually requested, not to serve the listings table.
    """
    try:
        if source_key == "prosple":
            from prosple_monitor import fetch_description, html_to_markdown
            raw = fetch_description(listing_id)
            return html_to_markdown(raw) if raw else None

        if source_key == "jobstreet":
            from jobstreet_monitor import fetch_description, html_to_markdown
            raw = fetch_description(listing_id, _proxies("JOBSTREET"))
            return html_to_markdown(raw) if raw else None

        if source_key == "indeed":
            from indeed_monitor import fetch_description, html_to_markdown
            raw = fetch_description(listing_id, _proxies("INDEED"))
            return html_to_markdown(raw) if raw else None
    except Exception as exc:  # noqa: BLE001 — a failed fetch is not an error page
        log(f"[archive] Description fetch failed for {source_key}:{listing_id}: {exc!r}")
    return None


def _proxies(prefix: str) -> list | None:
    """Proxy list for a source, honouring the same env vars sorpple.py reads."""
    import os as _os

    from indeed_monitor import load_proxies

    flag = (_os.environ.get(f"{prefix}_USE_PROXIES", "") or "").strip().lower()
    default_on = prefix == "INDEED"
    enabled = default_on if flag == "" else flag in ("1", "true", "yes", "on")
    if not enabled:
        return None
    path = _os.environ.get(f"{prefix}_PROXIES_FILE", _os.path.join(SCRIPT_DIR, "proxies.txt"))
    return load_proxies(path) or None


def load() -> dict:
    """Read the archive.  Never raises — a broken file starts a fresh one."""
    if not os.path.exists(ARCHIVE_FILE):
        return {"listings": []}
    try:
        with open(ARCHIVE_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict) or not isinstance(data.get("listings"), list):
            raise ValueError("unexpected archive shape")
        return data
    except (json.JSONDecodeError, OSError, ValueError) as exc:
        log(f"WARNING: could not read {os.path.basename(ARCHIVE_FILE)} ({exc}); starting empty.")
        return {"listings": []}


def save(data: dict) -> None:
    """Write the archive atomically, the same way sorpple.py persists settings."""
    tmp = ARCHIVE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, ARCHIVE_FILE)


def record(source_key: str, item: dict, years: list[str] | None = None) -> None:
    """Add or refresh one listing in the archive.

    Called for every listing the bot posts.  Deliberately swallows its own errors:
    a failed archive write must never stop a listing reaching Discord, which is
    still the product's actual job.
    """
    try:
        _record(source_key, item, years or [])
    except Exception as exc:  # noqa: BLE001 — archiving is best-effort by design
        log(f"[archive] Could not record {source_key} listing: {exc!r}")


def record_many(source_key: str, items: list[dict]) -> int:
    """Archive a batch in one read/write.  Returns how many were stored.

    Used by --backfill, where writing the file once per listing would mean 30
    rewrites of a growing file per source.
    """
    adapter = ADAPTERS.get(source_key)
    if adapter is None:
        log(f"[archive] Unknown source {source_key!r}; skipping.")
        return 0

    data = load()
    index = {f"{e['source']}:{e['id']}": e for e in data["listings"]}
    stored = 0

    for item in items:
        try:
            entry = adapter(item)
        except Exception as exc:  # noqa: BLE001
            log(f"[archive] Could not map {source_key} listing: {exc!r}")
            continue
        if not entry["id"]:
            continue
        _merge(index, source_key, entry, [])
        stored += 1

    data["listings"] = _trim(list(index.values()))
    save(data)
    return stored


def _record(source_key: str, item: dict, years: list[str]) -> None:
    adapter = ADAPTERS.get(source_key)
    if adapter is None:
        log(f"[archive] Unknown source {source_key!r}; skipping.")
        return

    entry = adapter(item)
    if not entry["id"]:
        return

    data  = load()
    index = {f"{e['source']}:{e['id']}": e for e in data["listings"]}
    _merge(index, source_key, entry, years)
    data["listings"] = _trim(list(index.values()))
    save(data)


def _merge(index: dict, source_key: str, entry: dict, years: list[str]) -> None:
    """Insert `entry`, preserving first_seen and year hits from any earlier record.

    Re-archiving (a backfill over listings already posted) must not reset when we
    first saw something, or the dashboard's "new" ordering would jump around.
    """
    key = f"{source_key}:{entry['id']}"
    existing = index.get(key)

    entry["source"] = source_key
    entry["first_seen"] = existing.get("first_seen") if existing else _now_iso()
    entry["first_seen"] = entry["first_seen"] or _now_iso()

    # A backfill scans no descriptions, so it reports no years.  Keep whatever the
    # live poll already found rather than clearing a real hit.
    prior_years = (existing or {}).get("years") or []
    entry["years"] = sorted(set(years) | set(prior_years))

    index[key] = entry


def _trim(listings: list[dict]) -> list[dict]:
    """Newest first_seen first, capped at MAX_LISTINGS."""
    listings.sort(key=lambda e: e.get("first_seen") or "", reverse=True)
    return listings[:MAX_LISTINGS]
