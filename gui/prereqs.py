#!/usr/bin/env python3
"""Environment checks for the GUI's first screen.

The wizard refuses to drive a flash until the host can actually run the
installer. On Linux the engine drives `ip`, sshpass and OpenSSH; on Windows,
PowerShell and the Python packages in requirements-windows.txt. The browser turns
the ids here into text in the operator's language (`prereq.<id>` and
`prereq.platform.<platform>` in static/strings.json).
"""

import importlib.util
import os
import platform
import shutil
import sys


def _which(name):
    return shutil.which(name) is not None


def _importable(name):
    return importlib.util.find_spec(name) is not None


def _is_wsl():
    if platform.system() != "Linux":
        return False
    try:
        with open("/proc/version") as f:
            return "microsoft" in f.read().lower()
    except OSError:
        return False


def collect():
    """Return a JSON-serialisable snapshot of the host environment."""
    system = platform.system()          # Linux / Windows / Darwin
    wsl = _is_wsl()

    python = {"id": "python", "label": "Python 3.9+",
              "ok": sys.version_info >= (3, 9),
              "version": platform.python_version(), "required": True}
    checks = [
        python,
        {"id": "sshpass", "label": "sshpass",
         "ok": _which("sshpass"), "required": True},
        {"id": "openssl", "label": "openssl",
         "ok": _which("openssl"), "required": True},
        {"id": "ssh", "label": "ssh / scp",
         "ok": _which("ssh") and _which("scp"), "required": True},
        {"id": "ip", "label": "iproute2 (ip)",
         "ok": _which("ip"), "required": True},
        {"id": "nmcli", "label": "nmcli (NetworkManager)",
         "ok": _which("nmcli"), "required": False},
    ]
    if system == "Windows":
        checks = [
            python,
            {"id": "paramiko", "label": "paramiko",
             "ok": _importable("paramiko"), "required": True},
            {"id": "passlib", "label": "passlib",
             "ok": _importable("passlib"), "required": True},
            {"id": "powershell", "label": "PowerShell",
             "ok": _which("powershell"), "required": True},
        ]

    host = {"Linux": "wsl" if wsl else "linux", "Windows": "windows",
            "Darwin": "macos"}.get(system, "other")
    platform_ok = system in ("Linux", "Windows")

    return {
        "system": system,
        "is_wsl": wsl,
        "platform": host,
        "platform_ok": platform_ok,
        "checks": checks,
        "ready": platform_ok and all(c["ok"] for c in checks if c["required"]),
        "hint_install": {
            "Linux": "sudo apt install sshpass openssl",
            "Windows": "py -3 -m pip install -r requirements-windows.txt",
        }.get(system, ""),
    }


if __name__ == "__main__":
    import json
    print(json.dumps(collect(), indent=2))
