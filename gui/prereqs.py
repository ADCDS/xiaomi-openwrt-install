#!/usr/bin/env python3
"""Environment checks for the GUI's first screen.

The wizard refuses to drive a flash until the host can actually run the
installer.  The install engine is Linux-only (it drives `ip`, /sys/class/net,
sshpass, ARP/neigh and network namespaces), so on Windows the honest answer is
"run this under WSL", and that is what we tell the user instead of letting a
flash fail halfway.
"""

import os
import platform
import shutil
import sys


def _which(name):
    return shutil.which(name) is not None


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
    py_ok = sys.version_info >= (3, 9)

    checks = [
        {
            "id": "python",
            "label": "Python 3.9+",
            "ok": py_ok,
            "detail": platform.python_version(),
            "required": True,
        },
        {
            "id": "sshpass",
            "label": "sshpass",
            "ok": _which("sshpass"),
            "detail": "used to log in to the router over SSH",
            "required": True,
        },
        {
            "id": "openssl",
            "label": "openssl",
            "ok": _which("openssl"),
            "detail": "used by the exploit stage",
            "required": True,
        },
        {
            "id": "ssh",
            "label": "ssh / scp",
            "ok": _which("ssh") and _which("scp"),
            "detail": "OpenSSH client",
            "required": True,
        },
        {
            "id": "ip",
            "label": "iproute2 (ip)",
            "ok": _which("ip"),
            "detail": "interface detection and discovery",
            "required": True,
        },
        {
            "id": "nmcli",
            "label": "nmcli (NetworkManager)",
            "ok": _which("nmcli"),
            "detail": "handy for Wi-Fi joins and interface status (optional)",
            "required": False,
        },
    ]

    # Platform verdict.
    if system == "Linux":
        platform_ok = True
        platform_note = ("WSL" if wsl else "Linux") + " — supported"
    else:
        platform_ok = False
        if system == "Windows":
            platform_note = (
                "The install (stock → OpenWrt) path is Linux-only, and WSL2 is "
                "reported to fail at the exploit stage — use a real Linux host "
                "for it. The update path (installed OpenWrt → new build) only "
                "needs an SSH and SCP client and works from Windows; the "
                "community guide does it with PuTTY + WinSCP. To drive it from "
                "this GUI, still run under WSL (wsl --install) with "
                "sshpass/openssl.")
        elif system == "Darwin":
            platform_note = (
                "macOS is untested: the installer relies on Linux networking "
                "(ip, /sys/class/net, network namespaces). Use a Linux host "
                "or a Linux VM.")
        else:
            platform_note = f"{system} is unsupported; use a Linux host."

    required_ok = platform_ok and all(c["ok"] for c in checks if c["required"])

    return {
        "system": system,
        "is_wsl": wsl,
        "platform_ok": platform_ok,
        "platform_note": platform_note,
        "checks": checks,
        "ready": required_ok,
        "hint_install": _install_hint(system),
    }


def _install_hint(system):
    if system == "Linux":
        return "sudo apt install sshpass openssl   # or your distro's equivalent"
    if system == "Windows":
        return "wsl --install   (then inside WSL: sudo apt install sshpass openssl)"
    return ""


if __name__ == "__main__":
    import json
    print(json.dumps(collect(), indent=2))
