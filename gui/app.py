#!/usr/bin/env python3
"""Local web wizard for the Xiaomi RD03v2 OpenWrt installer.

A tiny stdlib HTTP server (no third-party dependencies, matching the rest of
the project) that serves a static single-page wizard and a small JSON API.
The API never flashes anything itself — it shells out to the proven install.py
/ updater.py / revert.py via runner.py and streams their output to the browser.

Safety:
  * binds 127.0.0.1 only (this process can drive a root reflash);
  * every /api/* call must carry the one-time session token printed at startup;
  * the wizard defaults to the safest choices and pushes a dry-run first.
"""

import http.server
import json
import os
import secrets
import socket
import socketserver
import sys
import urllib.parse
import urllib.request
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
STATIC = os.path.join(HERE, "static")
sys.path.insert(0, ROOT)

import devices          # noqa: E402
import install          # noqa: E402
import release          # noqa: E402
import runner           # noqa: E402
import prereqs          # noqa: E402

TOKEN = os.environ.get("RD03V2_GUI_TOKEN") or secrets.token_urlsafe(16)


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
        except Exception:
            transport = "unknown"
        try:
            nets = install.interface_ipv4_networks(name)
            addrs = [str(n) for n in nets]
        except Exception:
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
                     "User-Agent": "rd03v2-installer-gui"})
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


# ---- HTTP handler -----------------------------------------------------------

class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "rd03v2-gui"

    def log_message(self, *a):          # keep the console clean for the wizard
        pass

    # -- helpers --
    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authed(self, qs):
        tok = qs.get("t", [None])[0] or self.headers.get("X-Token")
        return secrets.compare_digest(tok or "", TOKEN)

    def _serve_static(self, path):
        if path in ("", "/"):
            path = "/index.html"
        safe = os.path.normpath(path).lstrip("/")
        full = os.path.join(STATIC, safe)
        if not full.startswith(STATIC) or not os.path.isfile(full):
            self.send_error(404)
            return
        ctype = {
            ".html": "text/html; charset=utf-8",
            ".js": "text/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".svg": "image/svg+xml",
        }.get(os.path.splitext(full)[1], "application/octet-stream")
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
        path = parsed.path

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
            self._json(list_releases(qs.get("device", ["rd03v2"])[0]))
        elif path.startswith("/api/stream/"):
            self._stream(path.rsplit("/", 1)[-1], int(qs.get("since", [0])[0]))
        else:
            self._json({"error": "not found"}, 404)

    # -- POST --
    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)
        if not self._authed(qs):
            self._json({"error": "bad or missing session token"}, 403)
            return
        length = int(self.headers.get("Content-Length", 0))
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
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
            self._json({"run_id": run.id, "argv": __import__("shlex").join(run.argv)})
        elif path.startswith("/api/cancel/"):
            run = runner.get(path.rsplit("/", 1)[-1])
            if not run:
                self._json({"error": "no such run"}, 404)
                return
            ok, msg = run.cancel()
            self._json({"ok": ok, "message": msg})
        else:
            self._json({"error": "not found"}, 404)

    # -- SSE --
    def _stream(self, run_id, since):
        run = runner.get(run_id)
        if not run:
            self._json({"error": "no such run"}, 404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        idx = since
        import time
        try:
            while True:
                batch, idx = run.events_since(idx)
                for ev in batch:
                    self.wfile.write(
                        f"data: {json.dumps(ev)}\n\n".encode())
                self.wfile.flush()
                if run.finished:
                    batch, idx = run.events_since(idx)
                    for ev in batch:
                        self.wfile.write(f"data: {json.dumps(ev)}\n\n".encode())
                    self.wfile.flush()
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
    print("  Xiaomi RD03v2 OpenWrt installer — web wizard")
    print("=" * 68)
    print(f"\n  Open this URL in your browser:\n\n    {url}\n")
    print("  (keep this terminal open; Ctrl-C stops the server)")
    print("=" * 68)
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
