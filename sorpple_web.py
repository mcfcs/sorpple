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
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

import sorpple_archive
import sorpple_control
from prosple_monitor import SCRIPT_DIR, load_dotenv, log

# Description fetches honour INDEED_USE_PROXIES and friends, which live in .env.
load_dotenv(os.path.join(SCRIPT_DIR, ".env"))

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

# Requests bigger than this are refused outright — a control action is a few
# dozen bytes of JSON.  Saving the proxy list is the one legitimately large
# body, so it gets its own ceiling.
MAX_BODY_BYTES = 64 * 1024
MAX_PROXY_BODY_BYTES = 8 * 1024 * 1024


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


# Enough backlog to see the last few poll cycles when the console is opened.
LOG_TAIL_BYTES = 64 * 1024


def read_log(since: int = 0) -> dict:
    """Log lines appended after byte `since`, plus the new offset.

    A first read (since=0) returns the tail rather than the whole file, so
    opening the console does not ship half a megabyte of history.
    """
    path = os.path.join(SCRIPT_DIR, "sorpple.log")
    try:
        size = os.path.getsize(path)
    except OSError:
        return {"lines": [], "offset": 0, "missing": True}

    # The bot trims the log in place when it grows too large, so the file can
    # legitimately shrink.  Treat that as "start again from the tail" rather
    # than reading from an offset that no longer means anything.
    start = since if 0 < since <= size else max(0, size - LOG_TAIL_BYTES)

    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            fh.seek(start)
            if start and since == 0:
                fh.readline()   # drop the partial line a tail-seek lands in
            text = fh.read()
    except OSError as exc:
        return {"lines": [], "offset": since, "error": str(exc)}

    return {
        "lines":  [line for line in text.splitlines() if line.strip()],
        "offset": size,
        "missing": False,
    }


# ── Proxies ───────────────────────────────────────────────────────────────────
# proxies.txt is host:port:user:pass per line, and can hold thousands of entries.
# The API pages through it rather than shipping the lot, and validates a rewrite
# before touching the file the Indeed poller depends on.

PROXY_PAGE = 200
PROXY_MAX_LINES = 50_000


def proxies_path() -> str:
    return os.environ.get(
        "INDEED_PROXIES_FILE", os.path.join(SCRIPT_DIR, "proxies.txt")
    )


def _proxy_lines() -> list[str]:
    path = proxies_path()
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return [line.rstrip("\r\n") for line in fh]


def valid_proxy(line: str) -> bool:
    """host:port:user:pass, with a numeric port — what load_proxies() accepts."""
    parts = line.split(":", 3)
    if len(parts) != 4:
        return False
    host, port, user, password = parts
    return bool(host and user and password) and port.isdigit() and 0 < int(port) < 65536


def read_proxies(offset: int = 0, limit: int = PROXY_PAGE, query: str = "") -> dict:
    """One page of the proxy list, with per-line validity."""
    lines = _proxy_lines()
    kept = [
        (index, text)
        for index, text in enumerate(lines)
        if text.strip() and not text.lstrip().startswith("#")
    ]
    if query:
        needle = query.lower()
        kept = [(i, t) for i, t in kept if needle in t.lower()]

    page = kept[offset: offset + limit]
    return {
        "path":    os.path.basename(proxies_path()),
        "total":   len(kept),
        "offset":  offset,
        "invalid": sum(1 for _, text in kept if not valid_proxy(text.strip())),
        "proxies": [
            {"line": index + 1, "value": text.strip(), "valid": valid_proxy(text.strip())}
            for index, text in page
        ],
    }


def write_proxies(text: str) -> dict:
    """Replace proxies.txt with `text`.  Returns a summary, or raises ValueError.

    Written atomically, and only after every line validates: this file is what
    Indeed polls through, and a malformed rewrite would silently drop the bot to
    direct requests and straight into Cloudflare.
    """
    lines = [line.strip() for line in str(text).replace("\r\n", "\n").split("\n")]
    entries = [line for line in lines if line and not line.startswith("#")]

    if len(lines) > PROXY_MAX_LINES:
        raise ValueError(f"too many lines (limit {PROXY_MAX_LINES})")

    bad = [
        {"line": index + 1, "value": line[:80]}
        for index, line in enumerate(lines)
        if line and not line.startswith("#") and not valid_proxy(line)
    ]
    if bad:
        raise ValueError(
            f"{len(bad)} line(s) are not host:port:user:pass — first is line {bad[0]['line']}"
        )

    path = proxies_path()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines).rstrip("\n") + "\n")
    os.replace(tmp, path)

    log(f"[web] proxies.txt rewritten — {len(entries)} proxies.")
    return {"total": len(entries)}


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

    def _body(self, limit: int = MAX_BODY_BYTES) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        if length > limit:
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

        if path == "/api/proxies":
            query = parse_qs(urlparse(self.path).query)

            def _int(name, default):
                try:
                    return max(0, int((query.get(name) or [str(default)])[0]))
                except ValueError:
                    return default

            # ?raw=1 returns the file verbatim, for the console's copy button.
            if (query.get("raw") or [""])[0] in ("1", "true"):
                return self._send_json({"text": "\n".join(_proxy_lines())})

            return self._send_json(read_proxies(
                offset=_int("offset", 0),
                limit=min(_int("limit", PROXY_PAGE), 1000),
                query=(query.get("q") or [""])[0].strip(),
            ))

        if path == "/api/log":
            # ?since=<byte offset> returns only what was appended after it, so
            # the console polls cheaply instead of re-sending the whole file.
            query = parse_qs(urlparse(self.path).query)
            try:
                offset = int((query.get("since") or ["0"])[0])
            except ValueError:
                offset = 0
            return self._send_json(read_log(offset))

        # /api/description/<source>/<id> — fetched from the board on first ask,
        # then cached, so re-opening a listing costs no proxy request.
        if path.startswith("/api/description/"):
            parts = path[len("/api/description/"):].split("/", 1)
            if len(parts) != 2 or parts[0] not in SOURCE_LABELS or not parts[1]:
                return self._error(400, "expected /api/description/<source>/<id>")
            source, listing_id = parts[0], unquote(parts[1])
            text, origin = sorpple_archive.description(source, listing_id)
            if origin == "unknown":
                return self._error(404, "no such listing in the archive")
            return self._send_json({"description": text, "origin": origin})

        if path.startswith("/api/"):
            return self._error(404, f"no such endpoint: {path}")

        return self._serve_static(path)

    def do_POST(self):
        path = urlparse(self.path).path
        if not path.startswith("/api/"):
            return self._error(404, f"no such endpoint: {path}")

        action = path[len("/api/"):].strip("/")

        if action == "proxies":
            try:
                body = self._body(MAX_PROXY_BODY_BYTES)
            except ValueError as exc:
                return self._error(400, str(exc))
            if not isinstance(body.get("text"), str):
                return self._error(400, "expected a 'text' string holding the whole file")
            try:
                result = write_proxies(body["text"])
            except ValueError as exc:
                return self._error(400, str(exc))
            except OSError as exc:
                return self._error(500, f"could not write the proxy file: {exc}")
            return self._send_json({
                "saved": True,
                "total": result["total"],
                # The bot loads proxies once at startup, so a rewrite only takes
                # effect on its next launch.  Say so rather than implying it is live.
                "note": "Restart Sorpple for the new list to take effect.",
            })

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
            # Queued actions expire, so an offline caller is told the action will
            # not happen rather than being promised it applies "when it starts".
            "note":   None if online else
                      "Sorpple is not running, so this had no effect. "
                      "Start it with: python sorpple.py",
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


def port_holder(port: int) -> tuple[int, str] | None:
    """(pid, command line) of whatever is listening on `port`, or None.

    Uses netstat + wmic rather than a library so this keeps the project's
    zero-dependency rule.  Windows-only; on anything else it reports nothing and
    the caller falls back to the plain "port in use" error.
    """
    if os.name != "nt":
        return None
    try:
        out = subprocess.run(
            ["netstat", "-ano", "-p", "TCP"],
            capture_output=True, text=True, timeout=10, check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None

    pid = None
    for line in out.splitlines():
        parts = line.split()
        # "TCP  0.0.0.0:7331  0.0.0.0:0  LISTENING  1234"
        if len(parts) >= 5 and parts[-2].upper() == "LISTENING":
            local = parts[1]
            if local.rsplit(":", 1)[-1] == str(port):
                pid = parts[-1]
                break
    if not pid or not pid.isdigit():
        return None

    command = ""
    try:
        info = subprocess.run(
            ["wmic", "process", "where", f"ProcessId={pid}", "get", "CommandLine", "/value"],
            capture_output=True, text=True, timeout=10,
        ).stdout
        for line in info.splitlines():
            if line.startswith("CommandLine="):
                command = line.split("=", 1)[1].strip()
                break
    except (OSError, subprocess.SubprocessError):
        pass
    return int(pid), command


def free_port(port: int, force: bool = False) -> bool:
    """Stop whatever holds `port`.  Returns True if the port is now free.

    Only a previous Sorpple dashboard is killed by default.  Something else
    listening on this port is far more likely to be a service you care about
    than a stale copy of this script, so that needs --force.
    """
    holder = port_holder(port)
    if holder is None:
        return True   # nothing listening, or we cannot tell — let bind() decide

    pid, command = holder
    if pid == os.getpid():
        return True

    is_ours = "sorpple_web" in command.lower()
    if not is_ours and not force:
        log(f"Port {port} is held by PID {pid}, which is not a Sorpple dashboard:")
        log(f"  {command or '(command line unavailable)'}")
        log("Refusing to kill it.  Use a different --port, or --free-port --force.")
        return False

    log(f"Port {port} is held by PID {pid} ({'previous dashboard' if is_ours else 'forced'}); stopping it.")
    try:
        subprocess.run(["taskkill", "/PID", str(pid), "/F", "/T"],
                       capture_output=True, timeout=10, check=True)
    except (OSError, subprocess.SubprocessError) as exc:
        log(f"Could not stop PID {pid}: {exc}")
        return False

    # taskkill returns before the socket is released.
    for _ in range(20):
        if port_holder(port) is None:
            return True
        time.sleep(0.25)
    log(f"PID {pid} was signalled but port {port} is still held.")
    return False


def main():
    parser = argparse.ArgumentParser(description="Sorpple web dashboard server.")
    parser.add_argument("--port", type=int, default=int(os.environ.get("SORPPLE_WEB_PORT", DEFAULT_PORT)))
    parser.add_argument("--host", default=os.environ.get("SORPPLE_WEB_HOST", DEFAULT_HOST))
    parser.add_argument(
        "--print-url", action="store_true",
        help="print this machine's tailnet URL and exit (used by the launcher)",
    )
    parser.add_argument(
        "--bot-running", action="store_true",
        help="exit 0 if sorpple.py is running, 1 if not (used by the launcher)",
    )
    parser.add_argument(
        "--free-port", action="store_true",
        help="stop whatever is holding the port, so the dashboard can bind it",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="with --free-port, also stop a process that isn't a Sorpple dashboard",
    )
    args = parser.parse_args()

    if args.print_url:
        url = tailscale_url(args.port)
        if url:
            print(url)
        return

    if args.bot_running:
        # The heartbeat is the only reliable signal: a bot process that has died
        # without clearing it still goes stale within STATUS_STALE_SECONDS.
        sys.exit(0 if sorpple_control.read_status() else 1)

    if args.free_port:
        sys.exit(0 if free_port(args.port, force=args.force) else 1)

    try:
        server = ThreadingHTTPServer((args.host, args.port), Handler)
    except OSError:
        # Almost always a dashboard left running from a previous launch.  Clear
        # it out and retry once rather than making the user hunt down the PID.
        log(f"Port {args.port} is busy; trying to free it.")
        if not free_port(args.port, force=args.force):
            sys.exit(1)
        try:
            server = ThreadingHTTPServer((args.host, args.port), Handler)
        except OSError as exc:
            log(f"ERROR: could not bind {args.host}:{args.port} — {exc}")
            log("Try a different port with --port 8080.")
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
