"""Windows 10/11 host networking, using PowerShell 5.1's structured objects.

Queries are fresh on every call. Interface aliases never enter PowerShell source;
selection and validation happen in Python. Discovery only returns candidates,
not a claim that any neighbour is the router (callers must check SSH identity).

Originally written by alexsys87 for the native Windows port
(https://github.com/alexsys87/xiaomi-openwrt-install).
"""

import ipaddress
import json
import math
import ntpath
import os
import re
import socket
import subprocess
import sys
import time


class NetworkError(RuntimeError):
    """Host networking could not be inspected or the selection is unsafe."""


def is_windows():
    return sys.platform == "win32"


_PS_PREFIX = r"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding
"""

_ADAPTERS = r"""
$addresses = @(Get-NetIPAddress)
$records = @(Get-NetAdapter -IncludeHidden | ForEach-Object {
    $a = $_
    $wireless = $false
    $medium = $null
    if ($null -ne $a.NdisPhysicalMedium) {
        try { $medium = [int]$a.NdisPhysicalMedium } catch { }
    }
    if ($null -eq $medium -and $null -ne $a.PhysicalMediaType) {
        try { $medium = [int]$a.PhysicalMediaType } catch {
            # These are invariant enum/display values, not adapter descriptions.
            $wireless = ([string]$a.PhysicalMediaType -in @(
                'Wireless LAN', 'Native 802.11', '802.11'))
        }
    }
    if ($null -ne $medium) { $wireless = ($medium -in @(1, 9)) }
    $own = @($addresses | Where-Object { $_.InterfaceIndex -eq $a.ifIndex })
    [pscustomobject]@{
        index = [int]$a.ifIndex
        name = [string]$a.Name
        mac = [string]$a.MacAddress
        wireless = [bool]$wireless
        ipv4 = @($own | Where-Object { [int]$_.AddressFamily -eq 2 } |
            ForEach-Object { '{0}/{1}' -f $_.IPAddress, $_.PrefixLength })
        ipv6 = @($own | Where-Object { [int]$_.AddressFamily -eq 23 } |
            ForEach-Object { '{0}/{1}' -f $_.IPAddress, $_.PrefixLength })
    }
})
ConvertTo-Json -InputObject @($records) -Depth 5 -Compress
"""

_GATEWAYS = r"""
$records = @(Get-NetRoute | Where-Object {
        [int]$_.AddressFamily -eq 2 -and $_.DestinationPrefix -eq '0.0.0.0/0'
    } |
    ForEach-Object { [pscustomobject]@{
        index = [int]$_.InterfaceIndex
        gateway = [string]$_.NextHop
    } })
ConvertTo-Json -InputObject @($records) -Depth 5 -Compress
"""

_NEIGHBORS = r"""
$records = @(Get-NetNeighbor | Where-Object { [int]$_.AddressFamily -eq 23 } |
    ForEach-Object {
    [pscustomobject]@{
        index = [int]$_.InterfaceIndex
        address = [string]$_.IPAddress
        state = [int]$_.State
    }
})
ConvertTo-Json -InputObject @($records) -Depth 5 -Compress
"""

_NEIGHBOR_MACS = r"""
$records = @(Get-NetNeighbor | ForEach-Object {
    [pscustomobject]@{
        index = [int]$_.InterfaceIndex
        address = [string]$_.IPAddress
        state = [int]$_.State
        mac = [string]$_.LinkLayerAddress
    }
})
ConvertTo-Json -InputObject @($records) -Depth 5 -Compress
"""

# MSFT_NetNeighbor.State: Probe, Delay, Stale, Reachable, Permanent. Unreachable
# (0) and Incomplete (1) carry no link-layer address worth believing.
_KNOWN_NEIGHBOR_STATES = (2, 3, 4, 5, 6)
_NO_MAC = "00:00:00:00:00:00"


def _system_executable(*parts):
    root = os.environ.get("SystemRoot", r"C:\Windows")
    if not ntpath.isabs(root):
        raise NetworkError("SystemRoot must be an absolute Windows directory")
    return ntpath.join(root, "System32", *parts)


def _list(value):
    # Also tolerate single-object JSON from older PowerShell wrappers.
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _powershell(script, timeout=20):
    command = [_system_executable("WindowsPowerShell", "v1.0", "powershell.exe"),
               "-NoProfile", "-NonInteractive", "-Command", _PS_PREFIX + script]
    try:
        result = subprocess.run(command, capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise NetworkError("Cannot query Windows networking with PowerShell 5.1; "
                           "check it is installed and the network is ready: "
                           + str(exc)) from exc
    if result.returncode:
        detail = result.stderr.decode("utf-8-sig", errors="replace").strip()
        raise NetworkError("PowerShell networking query failed; check NetTCPIP "
                           "and NetAdapter are available: " + (detail or
                           "exit status {}".format(result.returncode)))
    try:
        data = json.loads(result.stdout.decode("utf-8-sig"))
        records = _list(data)
        if not all(isinstance(row, dict) for row in records):
            raise ValueError("expected an array of network objects")
        return records
    except (UnicodeError, ValueError) as exc:
        raise NetworkError("PowerShell returned invalid UTF-8 networking JSON: "
                           + str(exc)) from exc


def _adapters(timeout=20):
    records = _powershell(_ADAPTERS, timeout)
    try:
        for row in records:
            row["index"] = int(row["index"])
            if row["index"] <= 0 or not isinstance(row["name"], str):
                raise ValueError("invalid interface index or name")
            if not isinstance(row["mac"], str) or not isinstance(row["wireless"], bool):
                raise ValueError("invalid MAC or wireless classification")
            row["mac"] = row["mac"].replace("-", ":").lower()
            for family in ("ipv4", "ipv6"):
                row[family] = _list(row.get(family))
                if not all(isinstance(addr, str) for addr in row[family]):
                    raise ValueError("invalid assigned address")
        return records
    except (KeyError, TypeError, ValueError) as exc:
        raise NetworkError("PowerShell returned invalid adapter data: " + str(exc)) from exc


def adapters():
    """Return current canonical adapter records, including disconnected links."""
    return _adapters()


def _select(interface, records):
    selection = str(interface)
    matches = [row for row in records if row["name"].casefold() == selection.casefold()
               or (selection.isascii() and selection.isdecimal()
                   and row["index"] == int(selection))]
    if len(matches) != 1:
        reason = "ambiguous" if matches else "not found"
        raise NetworkError("Interface {!r} {}; use a unique friendly alias or "
                           "numeric interface index (check Get-NetAdapter)."
                           .format(interface, reason))
    return matches[0]


def adapter(interface):
    """Resolve an alias case-insensitively, or a numeric index, to one record."""
    return _select(interface, adapters())


def route(host):
    """Return the actual kernel-selected IPv4 source and owning adapter alias.

    UDP connect selects a route without sending a datagram. No default gateway
    is guessed; an unassigned or multiply assigned source is a hard failure.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(5)
            sock.connect((host, 9))
            source = ipaddress.IPv4Address(sock.getsockname()[0])
    except (OSError, ValueError, TypeError) as exc:
        raise NetworkError("Cannot determine IPv4 route to {!r}; connect the "
                           "router-facing adapter and check the destination: {}"
                           .format(host, exc)) from exc
    if source.is_unspecified or source.is_loopback or source.is_multicast:
        raise NetworkError("Route to {!r} selected unusable source {}".format(host, source))
    owners = []
    for row in adapters():
        try:
            if any(ipaddress.IPv4Interface(addr).ip == source for addr in row["ipv4"]):
                owners.append(row)
        except ValueError as exc:
            raise NetworkError("Invalid IPv4 assignment on " + row["name"]) from exc
    if len(owners) != 1:
        raise NetworkError("Route source {} for {!r} is {} on current adapters; "
                           "check IP assignments and retry after reconnecting."
                           .format(source, host, "ambiguous" if owners else "not assigned"))
    return str(source), owners[0]["name"]


def default_routes():
    """[(IPv4 default gateway, canonical adapter alias)], one per route.

    Every route is kept. Two default routes via one address -- a home router on
    Wi-Fi, ours on the cable -- is exactly the case the gateway checks in
    install.py exist for, and a mapping keyed by gateway would lose one of them.
    """
    routes = _powershell(_GATEWAYS)
    records = adapters()
    result = []
    try:
        for row in routes:
            gateway = ipaddress.IPv4Address(row["gateway"])
            if gateway.is_unspecified or gateway.is_multicast or gateway.is_loopback:
                continue
            owner = _select(str(int(row["index"])), records)
            result.append((str(gateway), owner["name"]))
    except (KeyError, ValueError, TypeError) as exc:
        raise NetworkError("Invalid Windows default route data: " + str(exc)) from exc
    return result


def neighbor_mac(address, interface=None):
    """The MAC Windows has on record for `address`, or None.

    `interface` is an alias or numeric index (a link-local's scope is the
    latter); without it any adapter's entry counts, as `ip neigh` would need a
    device for. Read just after talking to the peer, so the entry is fresh.
    """
    try:
        wanted = ipaddress.ip_address(address.split("%", 1)[0])
        index = _select(interface, adapters())["index"] if interface else None
        for row in _powershell(_NEIGHBOR_MACS):
            if ipaddress.ip_address(str(row["address"]).split("%", 1)[0]) != wanted:
                continue
            if index is not None and int(row["index"]) != index:
                continue
            if int(row["state"]) not in _KNOWN_NEIGHBOR_STATES:
                continue
            mac = str(row["mac"]).replace("-", ":").lower()
            if re.fullmatch(r"(?:[0-9a-f]{2}:){5}[0-9a-f]{2}", mac) and mac != _NO_MAC:
                return mac
    except (KeyError, TypeError, ValueError) as exc:
        raise NetworkError("Invalid neighbour data: " + str(exc)) from exc
    return None


def _ipv6(value):
    return ipaddress.IPv6Address(value.split("/", 1)[0].split("%", 1)[0])


def discover_candidates(interface, timeout=15):
    """Populate neighbours with all-nodes ping and return scoped link-locals.

    A nonzero ping status is normal when multicast echo is filtered; the
    neighbour table remains useful. Probe launch/timeouts and query failures
    are errors. An empty neighbour table is an empty result, not a router guess.
    """
    try:
        budget = float(timeout)
        if not math.isfinite(budget) or budget <= 0:
            raise ValueError("timeout must be positive and finite")
    except (TypeError, ValueError) as exc:
        raise NetworkError("Invalid discovery timeout: " + str(exc)) from exc
    deadline = time.monotonic() + budget

    def remaining():
        seconds = deadline - time.monotonic()
        if seconds <= 0:
            raise NetworkError("IPv6 discovery timed out; check the link or increase timeout")
        return seconds

    before = _select(interface, _adapters(remaining()))
    index = before["index"]
    try:
        ours = {_ipv6(addr) for addr in before["ipv6"]}
        subprocess.run([_system_executable("ping.exe"), "-6", "-n", "2",
                        "-w", "1000", "ff02::1%{}".format(index)],
                       capture_output=True, timeout=remaining())
        after = _select(str(index), _adapters(remaining()))
        if after["name"] != before["name"]:
            raise NetworkError("Adapter changed during discovery; reconnect and retry")
        ours.update(_ipv6(addr) for addr in after["ipv6"])
        neighbors = _powershell(_NEIGHBORS, remaining())
        found = []
        for row in neighbors:
            if int(row["index"]) != index or int(row["state"]) not in (2, 3, 4, 5, 6):
                continue
            address = _ipv6(row["address"])
            candidate = "{}%{}".format(address, index)
            if address.is_link_local and address not in ours and candidate not in found:
                found.append(candidate)
        return found
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise NetworkError("Cannot probe IPv6 on {!r}; check ping.exe and IPv6 "
                           "on the selected link: {}".format(before["name"], exc)) from exc
    except (KeyError, TypeError, ValueError) as exc:
        raise NetworkError("Invalid IPv6 adapter/neighbour data: " + str(exc)) from exc
