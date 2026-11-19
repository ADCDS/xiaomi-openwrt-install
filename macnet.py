"""Small macOS adaptations for the Homebrew/iproute2mac transport."""
import ipaddress
import os
import re
import subprocess


def query(command):
    result = subprocess.run(command, capture_output=True, text=True, timeout=5,
                            env=dict(os.environ, LC_ALL="C", LANG="C"))
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "command failed: " + command[0])
    return result.stdout


def interfaces():
    """Only hardware ports: exclude VPNs and virtual peer interfaces."""
    text = query(["/usr/sbin/networksetup", "-listallhardwareports"])
    ports = {}
    for block in text.split("\n\n"):
        port = re.search(r"^Hardware Port: (.+)$", block, re.M)
        device = re.search(r"^Device: (\S+)$", block, re.M)
        if port and device:
            label = port[1].lower()
            ports[device[1]] = "wifi" if label in ("wi-fi", "airport") else "wired"
    if not ports:
        raise RuntimeError("Cannot read macOS hardware ports")
    return ports


def transport(interface):
    ports = interfaces()
    if interface not in ports:
        raise RuntimeError("Unknown macOS hardware interface {!r}; available: {}".format(
            interface, ", ".join(ports)))
    return ports[interface]


def normalize_mac(value):
    if not re.fullmatch(r"[0-9a-fA-F]{1,2}(?::[0-9a-fA-F]{1,2}){5}", value):
        return None
    mac = ":".join(part.zfill(2).lower() for part in value.split(":"))
    return None if mac == "00:00:00:00:00:00" else mac


def neighbor_mac(address, interface):
    wanted = ipaddress.ip_address(address)
    if wanted.version == 4:
        text = query(["/usr/sbin/arp", "-an"])
        for line in text.splitlines():
            match = re.search(r"\(([^)]+)\) at (\S+) on (\S+)", line)
            if match and match[1] == str(wanted) and match[3] == interface:
                return normalize_mac(match[2])
    else:
        text = query(["/usr/sbin/ndp", "-an"])
        for line in text.splitlines()[1:]:
            fields = line.split()
            if len(fields) < 3 or fields[2] != interface:
                continue
            if ipaddress.ip_address(fields[0].split("%", 1)[0]) == wanted:
                return normalize_mac(fields[1])
    return None


def local_mac(interface):
    text = query(["/sbin/ifconfig", interface])
    match = re.search(r"\bether\s+(\S+)", text)
    return normalize_mac(match[1]) if match else None


def openssl_ready():
    try:
        result = subprocess.run(["openssl", "passwd", "-6", "-stdin"],
                                input="compatibility-check\n", capture_output=True,
                                text=True, timeout=5)
        return result.returncode == 0 and result.stdout.startswith("$6$")
    except (OSError, subprocess.TimeoutExpired):
        return False
