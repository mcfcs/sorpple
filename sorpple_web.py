"""
Sorpple web dashboard — HTTP API and static file server.

Serves the React dashboard from web/dist and a small JSON API from the same
origin, so there is one port, one process, and no CORS.

Runs as its own process, separate from the bot.  Read-only data (settings, seen
counts, the listing archive) is read straight off disk; anything that has to
touch a live poller is queued through sorpple_control.py, which the bot drains
every couple of seconds.  A web-server crash therefore cannot take Discord
posting down with it.

    python sorpple_web.py                 # 0.0.0.0:7331 — reachable over Tailscale
    python sorpple_web.py --port 8080
    python sorpple_web.py --host 127.0.0.1  # this machine only

Access control is the tailnet: bind to a Tailscale-reachable address and only
your own devices can reach it.  There is no login, and the API can pause the
monitor -- do not expose this port to the public internet.
"""

import argparse
import json
import mimetypes
import os
import subprocess
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

import sorpple_archive
import sorpple_control
from prosple_monitor import SCRIPT_DIR, log

DEFAULT_PORT = 7331
DEFAULT_HOST = "0.0.0.0"

WEB_DIR      = os.path.join(SCRIPT_DIR, "web", "dist")
SETTINGS_FILE = os.path.join(SCRIPT_DIR, "sorpple_settings.json")

# Mirrors DEFAULT_MINUTES / MIN_MINUTES / MAX_MINUTES in sorpple.py.  Duplicated
# rather than imported because importing sorpple.py would pull in discord.py and
# every scraper just to serve a web page.
DEFAULT_MINUTES = {"prosple": 10, "jobstreet": 10, "indeed": 30}
MIN_MINUTES, MAX_MINUTES = 1, 1440

SOURCE_LABELS = {"prosple": "Prosple", "jobstreet": "JobStreet", "indeed": "Indeed"}
STATE_FILES   = {
    "prosple":   "state.json",
    "jobstreet": "jobstreet_state.json",
    "indeed":    "indeed_state.json",
}

# Requests bigger than this are refused outright — every real request here is a
# few dozen bytes of JSON.
MAX_BODY_BYTES = 64 * 1024


def _read_json(path, fallback):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        return fallback


def _seen_count(source: str) -> int:
    state = _read_json(os.path.join(SCRIPT_DIR, STATE_FILES[source]), {})
    return len(state.get("seen_ids") or [])


def build_status() -> dict:
    """Current state of all three sources.

    Prefers the bot's live heartbeat, which is the only place the real next-run
    time and in-flight errors exist.  With the bot down, falls back to the
    settings file so the dashboard still shows the configured intervals — marked
    offline, so nothing reads as live that isn't.
    """
    live = sorpple_control.read_status()
    settings = _read_json(SETTINGS_FILE, {}) or {}
    intervals = settings.get("intervals") or {}
    paused    = settings.get("paused") or {}

    sources = []
    for key, label in SOURCE_LABELS.items():
        if live and key in (live.get("sources") or {}):
            entry = dict(live["sources"][key])
        else:
            entry = {
                "key":        key,
                "label":      label,
                "minutes":    intervals.get(key, DEFAULT_MINUTES[key]),
                "running":    False,
                "paused":     bool(paused.get(key, False)),
                "next_run":   None,
                "last_poll":  None,
                "last_new":   0,
                "last_error": None,
                "seen_count": _seen_count(key),
            }
        entry.setdefault("label", label)
        sources.append(entry)

    return {
        "online":     bool(live),
        "bot_user":   (live or {}).get("bot_user"),
        "year_roles": (live or {}).get("year_roles") or [],
        "sources":    sources,
        "server_time": datetime.now(timezone.utc).isoformat(),
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "sorpple-web"

    # ── Plumbing ──────────────────────────────────────────────────────────────

    def log_message(self, fmt, *args):
        # BaseHTTPRequestHandler logs every asset request to stderr; the dashboard
        # polls a few times a minute, which would bury anything worth reading.
        pass

    def _send_json(self, payload, status: int = 200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        # Status is polled continuously; a cached response would freeze the UI.
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: int, message: str):
        self._send_json({"error": message}, status=status)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        if length > MAX_BODY_BYTES:
            raise ValueError("request body too large")
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError(f"invalid JSON body: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError("body must be a JSON object")
        return data

    # ── Routes ────────────────────────────────────────────────────────────────

    def do_GET(self):
        path = urlparse(self.path).path

        if path == "/api/status":
            return self._send_json(build_status())

        if path == "/api/listings":
            archive = sorpple_archive.load()
            return self._send_json({
                "listings":    archive.get("listings", []),
                "server_time": datetime.now(timezone.utc).isoformat(),
            })

        if path.startswith("/api/"):
            return self._error(404, f"no such endpoint: {path}")

        return self._serve_static(path)

    def do_POST(self):
        path = urlparse(self.path).path
        if not path.startswith("/api/"):
            return self._error(404, f"no such endpoint: {path}")

        action = path[len("/api/"):].strip("/")
        if action not in sorpple_control.VALID_ACTIONS:
            return self._error(404, f"no such endpoint: {path}")

        try:
            body = self._body()
        except ValueError as exc:
            return self._error(400, str(exc))

        source = body.get("source", "all")
        value  = body.get("value")

        if action == "interval":
            try:
                value = int(value)
            except (TypeError, ValueError):
                return self._error(400, "interval needs a numeric 'value' in minutes")
            if not MIN_MINUTES <= value <= MAX_MINUTES:
                return self._error(
                    400, f"interval must be between {MIN_MINUTES} and {MAX_MINUTES} minutes"
                )

        if action == "monitor":
            if not isinstance(value, bool):
                return self._error(400, "monitor needs a boolean 'value' (true = on)")

        try:
            entry = sorpple_control.enqueue(action, source, value)
        except ValueError as exc:
            return self._error(400, str(exc))
        except OSError as exc:
            return self._error(500, f"could not queue the action: {exc}")

        online = sorpple_control.read_status() is not None
        log(f"[web] Queued {action} for {source}" + ("" if online else " (bot offline)"))
        return self._send_json({
            "queued": entry,
            "online": online,
            # The bot applies queued actions on its next tick, so the caller knows
            # to re-read status shortly rather than expecting an immediate change.
            "note":   None if online else "Sorpple is not running; this will apply when it starts.",
        })

    # ── Static files ──────────────────────────────────────────────────────────

    def _serve_static(self, path: str):
        if not os.path.isdir(WEB_DIR):
            return self._send_placeholder()

        # Decode before the containment check, or "%2e%2e%2f" would slip past a
        # check that only ever sees the literal escape sequence.
        relative = unquote(path).lstrip("/") or "index.html"

        # Reject a decoded path that is absolute or has a drive letter; joining
        # one onto WEB_DIR would silently discard WEB_DIR entirely.
        if os.path.isabs(relative) or os.path.splitdrive(relative)[0]:
            return self._error(403, "forbidden")

        target = os.path.normpath(os.path.join(WEB_DIR, relative))

        # Refuse anything that escapes web/dist — "../.env" must not be servable.
        if os.path.commonpath([os.path.abspath(target), os.path.abspath(WEB_DIR)]) != os.path.abspath(WEB_DIR):
            return self._error(403, "forbidden")

        # Unknown paths fall through to index.html so client-side routes work on
        # a hard refresh.
        if not os.path.isfile(target):
            target = os.path.join(WEB_DIR, "index.html")
            if not os.path.isfile(target):
                return self._send_placeholder()

        ctype = mimetypes.guess_type(target)[0] or "application/octet-stream"
        try:
            with open(target, "rb") as fh:
                body = fh.read()
        except OSError as exc:
            return self._error(500, f"could not read {relative}: {exc}")

        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # Vite fingerprints asset filenames, so they can cache hard; index.html
        # must not, or a rebuild would never reach the browser.
        if "/assets/" in target.replace("\\", "/"):
            self.send_header("Cache-Control", "public, max-age=31536000, immutable")
        else:
            self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_placeholder(self):
        """Explain the missing build rather than returning a bare 404."""
        body = (
            "<!doctype html><meta charset='utf-8'>"
            "<title>Sorpple — dashboard not built</title>"
            "<style>body{font:15px/1.6 system-ui;margin:15vh auto;max-width:34rem;"
            "padding:0 1.5rem;background:#0f1620;color:#e8edf4}"
            "code{background:#1b2634;padding:.15rem .4rem;border-radius:4px}</style>"
            "<h1>The dashboard isn't built yet</h1>"
            "<p>The API is running, but <code>web/dist</code> is missing. Build it:</p>"
            "<p><code>cd web &amp;&amp; npm install &amp;&amp; npm run build</code></p>"
            "<p>Or run <code>start-sorpple-web.bat</code>, which builds it for you.</p>"
        ).encode("utf-8")
        self.send_response(503)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def tailscale_url(port: int) -> str | None:
    """This machine's tailnet URL, or None if Tailscale isn't set up.

    Used by start-sorpple-web.bat to print an address that works from your other
    devices, rather than only localhost.
    """
    candidates = [
        os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"),
                     "Tailscale", "tailscale.exe"),
        "tailscale",
    ]
    for exe in candidates:
        try:
            out = subprocess.run(
                [exe, "status", "--json"],
                capture_output=True, text=True, timeout=5, check=True,
            ).stdout
        except (OSError, subprocess.SubprocessError):
            continue
        try:
            name = (json.loads(out).get("Self") or {}).get("DNSName", "").rstrip(".")
        except (json.JSONDecodeError, AttributeError):
            continue
        if name:
            return f"http://{name}:{port}"
    return None


def main():
    parser = argparse.ArgumentParser(description="Sorpple web dashboard server.")
    parser.add_argument("--port", type=int, default=int(os.environ.get("SORPPLE_WEB_PORT", DEFAULT_PORT)))
    parser.add_argument("--host", default=os.environ.get("SORPPLE_WEB_HOST", DEFAULT_HOST))
    parser.add_argument(
        "--print-url", action="store_true",
        help="print this machine's tailnet URL and exit (used by the launcher)",
    )
    args = parser.parse_args()

    if args.print_url:
        url = tailscale_url(args.port)
        if url:
            print(url)
        return

    try:
        server = ThreadingHTTPServer((args.host, args.port), Handler)
    except OSError as exc:
        log(f"ERROR: could not bind {args.host}:{args.port} — {exc}")
        log("Another process may already be using that port; try --port 8080.")
        sys.exit(1)

    if not os.path.isdir(WEB_DIR):
        log("WARNING: web/dist not found — serving the API only. Build with: cd web && npm run build")

    log(f"Sorpple dashboard on http://{args.host}:{args.port}")
    if sorpple_control.read_status() is None:
        log("Sorpple is not running — controls will queue until it starts.")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log("Dashboard stopped. Bye!")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
