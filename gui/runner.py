#!/usr/bin/env python3
"""Run install.py / updater.py / revert.py as subprocesses and stream them.

The GUI never reimplements any flashing logic: it builds an argv for one of the
command-line tools, runs it with every answer supplied up front, and turns its
line-by-line stdout into events the browser can render -- a live log plus a stage
tracker, the Wi-Fi join banner, and the resume-manifest path for recovery.

There is no terminal behind the browser, so a tool must never be able to ask
anything: stdin is /dev/null, which makes its own "needs an interactive terminal"
checks fire and report what was missing, instead of waiting forever on a prompt
nobody can see.
"""

import os
import re
import shlex
import subprocess
import sys
import threading
import time
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import hostnet          # noqa: E402

# Banner keyword -> canonical stage id for the tracker. Every tool prints
# `=== ... ===` banners; match on words, because their numbering differs.
_STAGE_RULES = [
    ("preflight", re.compile(r"pre-?flight", re.I)),
    ("backup", re.compile(r"\bbackup\b", re.I)),
    ("pivot", re.compile(r"pivot", re.I)),
    ("flash", re.compile(r"\bflash\b|restore stock", re.I)),
    ("verify", re.compile(r"verify|permanent|wait for stock", re.I)),
]

# flash() verifies inside its own stage, so there is no banner for this one.
_VERIFY_LINE_RE = re.compile(r"for permanent OpenWrt|wait for stock", re.I)

_WIFI_RE = re.compile(r"join .*?['\"]([^'\"]+)['\"].*?(?:key|with key) ['\"]([^'\"]+)['\"]", re.I)
_RESUME_RE = re.compile(r"(\S*resume\S*\.json)\b")
_OUTPUT_RE = re.compile(r"output -> (.+)$")

STAGES = ["preflight", "backup", "pivot", "flash", "verify"]

# From the pivot on, stopping the driver helps nothing: the writes are detached
# on the router and keep going, and only the driver knows how to finish the job.
_NO_CANCEL_FROM = ("pivot", "flash", "verify")

_SECRET_FLAGS = ("--root-password", "--wifi-key")


class RunBusy(Exception):
    """Only one tool may drive the router at a time."""


def redact(argv):
    """argv with secret values blanked, for anything that leaves this process."""
    shown = list(argv)
    for index, item in enumerate(shown[:-1]):
        if item in _SECRET_FLAGS:
            shown[index + 1] = "***"
    return shown


def build_argv(mode, opts):
    """Translate wizard options into a safe argv list (never a shell string).

    Validation is strict: unknown modes and malformed values raise ValueError
    rather than being passed through, and every value becomes a single argv
    item, so there is no shell to inject into.
    """
    py = [sys.executable, "-u"]  # -u: unbuffered, so we stream line by line
    dry = bool(opts.get("dry_run"))

    def iface(key="interface"):
        v = (opts.get(key) or "").strip()
        # Windows names adapters in the system's language, with spaces. It is one
        # argv item either way; what must not happen is it reading as a flag.
        bad = (v.startswith("-") or any(ord(c) < 32 for c in v)
               if hostnet.is_windows()
               else not re.fullmatch(r"[A-Za-z0-9_.@:-]+", v))
        if v and bad:
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

    def common(argv):
        if iface():
            argv += ["--interface", iface()]
        if opts.get("root_password"):
            argv += ["--root-password", str(opts["root_password"])]
        return argv + (["--dry-run"] if dry else ["--yes"])

    if mode == "install":
        argv = py + ["install.py", "--release", tag(), "--transport", transport]
        if opts.get("verify_interface"):
            argv += ["--verify-interface", iface("verify_interface")]
        if opts.get("wifi_ssid"):
            argv += ["--wifi-ssid", str(opts["wifi_ssid"])]
        if opts.get("wifi_key"):
            argv += ["--wifi-key", str(opts["wifi_key"])]
        if opts.get("wifi_country"):
            argv += ["--wifi-country", str(opts["wifi_country"])]
        return common(argv)

    if mode == "update":
        argv = py + ["updater.py", "--release", tag(), "--transport", transport]
        if opts.get("verify_interface"):
            argv += ["--verify-interface", iface("verify_interface")]
        # Keep settings like sysupgrade by default; a clean install is opt-in.
        if opts.get("keep_settings") is False:
            argv += ["--no-keep-settings"]
        return common(argv)

    if mode == "revert":
        image = (opts.get("stock_image") or "").strip()
        # revert.py carves and validates the stock image even on a dry-run, and
        # finds the router only through an interface it is told about.
        if not image:
            raise ValueError("revert needs the path to an approved stock image")
        if not os.path.isfile(image):
            raise ValueError(f"stock image not found: {image}")
        if not iface():
            raise ValueError("revert needs the router-facing network interface")
        return common(py + ["revert.py", "--device", "rd03v2", "--release", tag(),
                            "--transport", transport, image])

    raise ValueError(f"unknown mode: {mode!r}")


class Run:
    """One subprocess and the events it has produced so far."""

    def __init__(self, mode, opts):
        self.id = uuid.uuid4().hex[:12]
        self.mode = mode
        self.dry_run = bool(opts.get("dry_run"))
        self.argv = build_argv(mode, opts)
        self.shown_argv = shlex.join(redact(self.argv))
        self.events = []                 # append-only; SSE replays from an index
        self._lock = threading.Lock()
        self.proc = None
        self.returncode = None
        self.stage = None
        self.finished = False
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
                    "argv": self.shown_argv})
        self.proc = subprocess.Popen(
            self.argv, cwd=ROOT, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            encoding="utf-8", errors="replace", bufsize=1,
            # The parsing below reads the tools' English output; the browser
            # does the translating, whatever language this system runs in.
            env={**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8",
                 "XIAOMI_INSTALL_LANG": "en"})
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        for raw in self.proc.stdout:
            line = raw.rstrip("\n")
            self._emit({"type": "log", "line": line})
            self._interpret(line)
        self.returncode = self.proc.wait()
        # In this order: a reader that sees `finished` must find `done` waiting.
        self._emit({"type": "done", "ok": self.returncode == 0,
                    "code": self.returncode})
        self.finished = True

    def _enter_stage(self, stage):
        self.stage = stage
        self._emit({"type": "stage", "stage": stage})
        if stage in _NO_CANCEL_FROM:
            self.cancellable = False

    def _interpret(self, line):
        if "===" in line:
            for stage, rx in _STAGE_RULES:
                if rx.search(line):
                    self._enter_stage(stage)
                    break
        if self.stage == "flash" and _VERIFY_LINE_RE.search(line):
            self._enter_stage("verify")
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
            self._emit({"type": "hint", "code": "ssl"})

    def cancel(self):
        if not self.cancellable:
            return False, "unsafe"
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            return True, "cancelled"
        return False, "not_running"


_RUNS = {}
_RUNS_LOCK = threading.Lock()


def start(mode, opts):
    run = Run(mode, opts)             # validates before anything is claimed
    with _RUNS_LOCK:
        if any(not other.finished for other in _RUNS.values()):
            raise RunBusy("another operation is still running")
        run.start()
        _RUNS[run.id] = run
    return run


def get(run_id):
    with _RUNS_LOCK:
        return _RUNS.get(run_id)
