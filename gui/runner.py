#!/usr/bin/env python3
"""Run install.py / updater.py / revert.py as subprocesses and stream them.

The GUI never reimplements any flashing logic: it builds an argv for one of the
proven command-line tools, runs it with every answer supplied up front (so no
interactive prompt can block), and turns its line-by-line stdout into events
the browser can render — a live log plus a stage tracker, the Wi-Fi join
banner, and the resume-manifest path for recovery.
"""

import os
import re
import shlex
import subprocess
import threading
import time
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Banner keyword -> canonical stage id for the tracker.  Both install.py and
# updater.py print `=== ... ===` banners; we match on words, not numbers,
# because the two tools number their stages differently.
_STAGE_RULES = [
    ("preflight", re.compile(r"pre-?flight", re.I)),
    ("backup", re.compile(r"\bbackup\b", re.I)),
    ("pivot", re.compile(r"pivot|RAM initramfs|write RAM installer", re.I)),
    ("flash", re.compile(r"\bflash\b|restore stock", re.I)),
    ("verify", re.compile(r"verify|permanent|wait for stock", re.I)),
]

# install.py has no separate verify banner — it verifies inside the flash
# stage — so advance the tracker to "verify" on this telltale line.
_VERIFY_LINE_RE = re.compile(r"for permanent OpenWrt|wait for stock", re.I)

_WIFI_RE = re.compile(r"join .*?['\"]([^'\"]+)['\"].*?(?:key|with key) ['\"]([^'\"]+)['\"]", re.I)
_RESUME_RE = re.compile(r"(\S*resume\S*\.json)\b")
_OUTPUT_RE = re.compile(r"output -> (\S+)")
_DONE_OK_RE = re.compile(r"\[\+\].*(installed on NAND|update complete|OpenWrt is installed)", re.I)

STAGES = ["preflight", "backup", "pivot", "flash", "verify"]


def build_argv(mode, opts):
    """Translate wizard options into a safe argv list (never a shell string).

    Validation is strict: unknown modes and malformed values raise ValueError
    rather than being passed through, and every value becomes a single argv
    item, so there is no shell to inject into.
    """
    py = ["python3", "-u"]  # -u: unbuffered, so we stream line by line
    dry = bool(opts.get("dry_run"))

    def iface(key="interface"):
        v = (opts.get(key) or "").strip()
        if v and not re.fullmatch(r"[A-Za-z0-9_.@:-]+", v):
            raise ValueError(f"bad interface name: {v!r}")
        return v

    def tag():
        v = (opts.get("release") or "latest").strip()
        if not re.fullmatch(r"[A-Za-z0-9_.+-]+", v):
            raise ValueError(f"bad release tag: {v!r}")
        return v

    transport = opts.get("transport") or "auto"
    if transport not in ("auto", "wired", "wifi"):
        raise ValueError(f"bad transport: {transport!r}")

    if mode == "install":
        argv = py + ["install.py", "--release", tag(), "--transport", transport]
        if iface():
            argv += ["--interface", iface()]
        if opts.get("verify_interface"):
            argv += ["--verify-interface", iface("verify_interface")]
        if opts.get("wifi_ssid"):
            argv += ["--wifi-ssid", str(opts["wifi_ssid"])]
        if opts.get("wifi_key"):
            argv += ["--wifi-key", str(opts["wifi_key"])]
        if opts.get("wifi_country"):
            argv += ["--wifi-country", str(opts["wifi_country"])]
        if opts.get("root_password"):
            argv += ["--root-password", str(opts["root_password"])]
        if dry:
            argv += ["--dry-run"]
        else:
            argv += ["--yes"]
        return argv

    if mode == "update":
        argv = py + ["updater.py", "--release", tag(), "--transport", transport]
        if iface():
            argv += ["--interface", iface()]
        if opts.get("host"):
            host = str(opts["host"]).strip()
            if not re.fullmatch(r"[A-Za-z0-9_.:%-]+", host):
                raise ValueError(f"bad host: {host!r}")
            argv += ["--host", host]
        if opts.get("root_password"):
            argv += ["--root-password", str(opts["root_password"])]
        if opts.get("verify_interface"):
            argv += ["--verify-interface", iface("verify_interface")]
        # Keep settings like sysupgrade by default; a clean install is opt-in.
        if opts.get("keep_settings") is False:
            argv += ["--no-keep-settings"]
        if dry:
            argv += ["--dry-run"]
        else:
            argv += ["--yes"]
        return argv

    if mode == "revert":
        image = (opts.get("stock_image") or "").strip()
        # revert.py carves and validates the stock image even on a dry-run,
        # so it is required in both cases.
        if not image:
            raise ValueError("revert needs the path to an approved stock image")
        if not os.path.isfile(image):
            raise ValueError(f"stock image not found: {image}")
        argv = py + ["revert.py", "--device", "rd03v2", "--release", tag(),
                     "--transport", transport]
        if image:
            argv += [image]
        if iface():
            argv += ["--interface", iface()]
        if opts.get("root_password"):
            argv += ["--root-password", str(opts["root_password"])]
        if dry:
            argv += ["--dry-run"]
        else:
            argv += ["--yes"]
        return argv

    raise ValueError(f"unknown mode: {mode!r}")


class Run:
    """One subprocess and the events it has produced so far."""

    def __init__(self, mode, opts):
        self.id = uuid.uuid4().hex[:12]
        self.mode = mode
        self.dry_run = bool(opts.get("dry_run"))
        self.argv = build_argv(mode, opts)
        self.events = []                 # append-only; SSE replays from an index
        self._lock = threading.Lock()
        self.proc = None
        self.returncode = None
        self.stage = None
        self.finished = False
        # Never let a flash-stage subprocess be cancelled once sysupgrade is
        # armed; the UI reflects this flag.
        self.cancellable = True

    # -- event helpers --
    def _emit(self, ev):
        ev["t"] = time.time()
        with self._lock:
            self.events.append(ev)

    def events_since(self, index):
        with self._lock:
            return self.events[index:], len(self.events)

    # -- lifecycle --
    def start(self):
        self._emit({"type": "meta", "mode": self.mode, "dry_run": self.dry_run,
                    "argv": shlex.join(self.argv)})
        self.proc = subprocess.Popen(
            self.argv, cwd=ROOT, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, bufsize=1,
            env={**os.environ, "PYTHONUNBUFFERED": "1"})
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        for raw in self.proc.stdout:
            line = raw.rstrip("\n")
            self._emit({"type": "log", "line": line})
            self._interpret(line)
        self.returncode = self.proc.wait()
        self.finished = True
        ok = self.returncode == 0
        self._emit({"type": "done", "ok": ok, "code": self.returncode})

    def _interpret(self, line):
        # Stage banners.
        if "===" in line:
            for stage, rx in _STAGE_RULES:
                if rx.search(line):
                    self.stage = stage
                    self._emit({"type": "stage", "stage": stage})
                    # Once a real flash starts, forbid cancel.
                    if stage == "flash":
                        self.cancellable = False
                    break
        if self.stage == "flash" and _VERIFY_LINE_RE.search(line):
            self.stage = "verify"
            self._emit({"type": "stage", "stage": "verify"})
        m = _WIFI_RE.search(line)
        if m:
            self._emit({"type": "wifi", "ssid": m.group(1), "key": m.group(2)})
        m = _OUTPUT_RE.search(line)
        if m:
            self._emit({"type": "output_dir", "path": m.group(1)})
        m = _RESUME_RE.search(line)
        if m:
            self._emit({"type": "resume", "path": m.group(1)})
        if "sysupgrade launched" in line or "flash-started" in line:
            self.cancellable = False
        if "CERTIFICATE_VERIFY_FAILED" in line or "SSL: CERTIFICATE" in line:
            self._emit({"type": "hint", "text":
                "GitHub не проверился по SSL (частая проблема). Обновите "
                "сертификаты системы, либо выберите конкретный релиз (не "
                "latest) — он возьмётся из проверенного кэша офлайн."})
        if _DONE_OK_RE.search(line):
            self._emit({"type": "success_marker"})

    def cancel(self):
        if not self.cancellable:
            return False, ("The flash has started; cancelling now is unsafe. "
                           "Let it finish, then use the resume manifest to "
                           "verify the result.")
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            return True, "cancelled"
        return False, "not running"


_RUNS = {}
_RUNS_LOCK = threading.Lock()


def start(mode, opts):
    run = Run(mode, opts)
    with _RUNS_LOCK:
        _RUNS[run.id] = run
    run.start()
    return run


def get(run_id):
    with _RUNS_LOCK:
        return _RUNS.get(run_id)
