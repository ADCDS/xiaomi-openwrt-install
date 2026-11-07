#!/usr/bin/env python3
"""Local web wizard for the Xiaomi RD03v2 OpenWrt installer.

A tiny stdlib HTTP server (no third-party dependencies, matching the rest of
the project) that serves a static single-page wizard and a small JSON API.
The API never flashes anything itself -- it shells out to install.py /
updater.py / revert.py via runner.py and streams their output to the browser.

Safety:
  * binds 127.0.0.1 only (this process can drive a root reflash);
  * every /api/* call must carry the session token printed at startup;
  * one operation at a time, so a double click cannot start two flashes;
  * the wizard defaults to the safest choices and offers a dry run first.
"""

import http.server
import json
import os
import secrets
import socketserver
import sys
import time
import urllib.parse
import urllib.request
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
STATIC = os.path.join(HERE, "static")
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import devices          # noqa: E402
import install          # noqa: E402
import runner           # noqa: E402
import prereqs          # noqa: E402

TOKEN = os.environ.get("RD03V2_GUI_TOKEN") or secrets.token_urlsafe(16)

MAX_BODY = 1 << 20

_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
}

# The session token sits in this page's URL, and the page can drive a flash.
_SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; frame-ancestors 'none'; "
                               "base-uri 'none'; form-action 'none'",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "no-store",
}


# ---- data endpoints ---------------------------------------------------------

def list_interfaces():
    out = []
    base = "/sys/class/net"
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return out
    for name in names:
        if name == "lo":
            continue
        try:
            transport = install.detect_transport(name)
        except Exception:                                       # noqa: BLE001
            transport = "unknown"
        try:
            addrs = [str(n) for n in install.interface_ipv4_networks(name)]
        except Exception:                                       # noqa: BLE001
            addrs = []
        try:
            with open(f"{base}/{name}/operstate") as f:
                state = f.read().strip()
        except OSError:
            state = "?"
        out.append({"name": name, "transport": transport,
                    "ipv4": addrs, "state": state})
    return out


def list_releases(device):
    profile = devices.get_profile(device)
    data = {"default": profile.default_release, "releases": [], "error": None}
    try:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{profile.release_repo}/releases?per_page=15",
            headers={"Accept": "application/vnd.github+json",
                     "User-Agent": "xiaomi-openwrt-install-gui"})
        with urllib.request.urlopen(req, timeout=10) as r:
            meta = json.loads(r.read().decode())
        for rel in meta:
            if rel.get("draft"):
                continue
            data["releases"].append({
                "tag": rel.get("tag_name"),
                "published": (rel.get("published_at") or "")[:10],
                "prerelease": bool(rel.get("prerelease")),
            })
    except Exception as exc:                                    # noqa: BLE001
        data["error"] = f"{type(exc).__name__}: {exc}"
    return data


def static_path(path):
    """The file under STATIC a request path names, or None.

    Compared after resolving, so neither `..`, a symlink, nor a backslash on
    Windows can step outside the directory.
    """
    relative = (path or "/").lstrip("/") or "index.html"
    if "\\" in relative or "\0" in relative:
        return None
    root = os.path.realpath(STATIC)
    full = os.path.realpath(os.path.join(root, relative))
    if not full.startswith(root + os.sep) or not os.path.isfile(full):
        return None
    return full


# ---- HTTP handler -----------------------------------------------------------

class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "xiaomi-openwrt-install-gui"
    sys_version = ""

    def log_message(self, *a):          # keep the console clean for the wizard
        pass

    def end_headers(self):
        for name, value in _SECURITY_HEADERS.items():
            self.send_header(name, value)
        super().end_headers()

    # -- helpers --
    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authed(self, qs):
        tok = qs.get("t", [None])[0] or self.headers.get("X-Token") or ""
        # As bytes: compare_digest raises on a non-ASCII str.
        return secrets.compare_digest(tok.encode("utf-8", "replace"), TOKEN.encode())

    def _serve_static(self, path):
        full = static_path(path)
        if not full:
            self.send_error(404)
            return
        ctype = _CONTENT_TYPES.get(os.path.splitext(full)[1],
                                   "application/octet-stream")
        with open(full, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # -- GET --
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)
        path = urllib.parse.unquote(parsed.path)

        if path == "/favicon.ico":      # browsers ask regardless; there is none
            self.send_response(204)
            self.end_headers()
            return
        if not path.startswith("/api/"):
            self._serve_static(path)
            return

        if not self._authed(qs):
            self._json({"error": "bad or missing session token"}, 403)
            return

        if path == "/api/prereqs":
            self._json(prereqs.collect())
        elif path == "/api/interfaces":
            self._json({"interfaces": list_interfaces()})
        elif path == "/api/releases":
            device = qs.get("device", ["rd03v2"])[0]
            if device not in devices.PROFILES:
                self._json({"error": "unknown device"}, 400)
                return
            self._json(list_releases(device))
        elif path.startswith("/api/stream/"):
            self._stream(path.rsplit("/", 1)[-1])
        else:
            self._json({"error": "not found"}, 404)

    # -- POST --
    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)
        if not self._authed(qs):
            self._json({"error": "bad or missing session token"}, 403)
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            if not 0 <= length <= MAX_BODY:
                raise ValueError("bad length")
            payload = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(payload, dict):
                raise ValueError("not an object")
        except ValueError:
            self._json({"error": "bad JSON"}, 400)
            return
        path = parsed.path

        if path == "/api/run":
            try:
                run = runner.start(payload.get("mode"),
                                   payload.get("options") or {})
            except ValueError as exc:
                self._json({"error": str(exc)}, 400)
                return
            except runner.RunBusy:
                self._json({"error": "busy", "code": "busy"}, 409)
                return
            self._json({"run_id": run.id, "argv": run.shown_argv})
        elif path.startswith("/api/cancel/"):
            run = runner.get(path.rsplit("/", 1)[-1])
            if not run:
                self._json({"error": "no such run"}, 404)
                return
            ok, code = run.cancel()
            self._json({"ok": ok, "code": code})
        else:
            self._json({"error": "not found"}, 404)

    # -- SSE --
    def _stream(self, run_id):
        run = runner.get(run_id)
        if not run:
            self._json({"error": "no such run"}, 404)
            return
        try:
            # A reconnecting EventSource says where it got to, so a dropped
            # connection cannot replay the whole log into the page.
            idx = max(0, int(self.headers.get("Last-Event-ID") or 0))
        except ValueError:
            idx = 0
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

        def send(batch, first):
            for offset, ev in enumerate(batch):
                self.wfile.write(
                    f"id: {first + offset + 1}\ndata: {json.dumps(ev)}\n\n".encode())
            self.wfile.flush()

        try:
            while True:
                finished = run.finished       # read before draining; see _pump
                batch, end = run.events_since(idx)
                send(batch, idx)
                idx = end
                if finished:
                    break
                time.sleep(0.4)
        except (BrokenPipeError, ConnectionResetError):
            pass


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    port = int(os.environ.get("RD03V2_GUI_PORT", "0"))
    httpd = Server(("127.0.0.1", port), Handler)
    actual = httpd.server_address[1]
    url = f"http://127.0.0.1:{actual}/?t={TOKEN}"
    print("=" * 68)
    print("  Xiaomi RD03v2 OpenWrt installer -- web wizard")
    print("=" * 68)
    print(f"\n  Open this URL in your browser:\n\n    {url}\n")
    print("  (keep this terminal open; Ctrl-C stops the server)")
    print("=" * 68, flush=True)
    if os.environ.get("RD03V2_GUI_NO_BROWSER") != "1":
        try:
            webbrowser.open(url)
        except Exception:                                       # noqa: BLE001
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[*] stopped")


if __name__ == "__main__":
    main()
