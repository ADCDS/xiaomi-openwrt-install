#!/usr/bin/env python3
"""Update an installed OpenWrt to another release of the port, without UART.

Between v2 releases the update is a plain in-place `sysupgrade` from the
installed system, settings kept, as on any other OpenWrt device:

    1. pre-flight  prove this is the board on its permanent system, that the
                   target release can drive this unit's NAND, and that the
                   image fits in memory. Nothing is written.
    2. backup      `sysupgrade -b`, copied off the box and checked.
    3. flash       upload and verify the image, check it with `sysupgrade -T`,
                   start sysupgrade detached, and verify the permanent system
                   on the new release.

v2.0.0 to v2.1.2 refuse an in-place sysupgrade (a mistake, since corrected).
When that refusal is the only objection `sysupgrade -T` raises, the update
passes -F, once. Anything else it objects to stops the update.

From v1.x, going back to v1.x, or with --via-ram-installer, the update goes
through the RAM installer instead, the route revert.py and install.py take on
real hardware, and reuses their code:

    1. pre-flight  as above, and the RAM installer fits in ubi_kernel.
    2. backup      as above.
    3. pivot       revert.pivot_to_ram: write the RAM installer over `ubi_kernel`,
                   read it back, reboot. Then wait until tmpfs is what answers.
    4. flash       install.flash: re-checks board, tmpfs and NAND, verifies the
                   upload, proves the detached sysupgrade started, records a
                   resume manifest, and verifies the permanent system.

The manual forms are docs/no-uart-reflash.md in the firmware repository,
https://github.com/ADCDS/openwrt-xiaomi-ax3000t-rd03v2.

Neither route has an A/B fallback: there is one installed kernel. On the RAM
route `ubi_kernel` is overwritten at step 3, so from there the way out is
forward: run this again, and it continues from the RAM system. A torn write on
either route needs the stock TFTP recovery or UART.

By default it logs in by password, like the rest of this tool, and a router
configured for SSH keys only (`PasswordAuth off`) is refused at step 1. With
--ssh-key-auth it logs in to the installed system through the system OpenSSH
and ~/.ssh/config instead, by key only, with the host key checked against
known_hosts. The RAM installer still gets the empty password: it has no copy
of the key.

Before the backup it also carries over what sysupgrade would drop: files in
/overlay/upper that no package owns are added to /etc/sysupgrade.conf, and the
packages the operator installed are listed in /etc/backup/world-old.txt.
After a verified update those packages are installed again. See
docs/packages.md.
"""

import argparse
import difflib
import fnmatch
import ipaddress
import os
import secrets
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.parse

import chain
import devices
import hostnet
import i18n
import install
import release
import restore
import revert
import winssh
from chain import ChainError, log
from i18n import t
from install import Abort

REMOTE_BACKUP = "/tmp/config-backup.tar.gz"

# The RAM image is uploaded to tmpfs on a router that is still running every
# service. Refuse below this much headroom rather than risk the OOM killer.
MEMORY_MARGIN_KB = 8 * 1024

# How long the box gets to come up in the RAM installer after the pivot.
RAM_DEADLINE_S = 420

# An in-place update runs sysupgrade's second stage on the same memory the
# uploaded image sits in, so it keeps more headroom than the RAM upload does.
IN_PLACE_MARGIN_KB = 12 * 1024

# What v2.0.0 to v2.1.2 print when they refuse an in-place sysupgrade, and the
# lines sysupgrade -T may print around it. Anything else is a real objection.
IN_PLACE_REFUSAL = "Boot the initramfs image and run sysupgrade from there."
_T_HARMLESS = ("Image check failed.", "verifying sysupgrade tar file integrity")

ROOTFS_TYPE = ". /lib/upgrade/common.sh 2>/dev/null; rootfs_type"

# ---- carry-over: extra files and packages -----------------------------------

SYSUPGRADE_CONF = "/etc/sysupgrade.conf"
BACKUP_DIR = "/etc/backup/"
WORLD_OLD = "/etc/backup/world-old.txt"
REMOTE_OVERLAY = "/tmp/overlay-full.tar.gz"
KEEP_EOF = "RD03V2_KEEP_EOF"
# Where CarryoverUndo keeps what the carry-over is about to change (tmpfs).
UNDO_DIR = "/tmp/rd03v2-carryover-undo"

# Files in /overlay/upper that are never added to sysupgrade.conf on their
# own: generated at boot or by apk, tied to this one image, or programs that a
# package (or the next image) provides. Shell glob patterns, matched against
# the full path; `*` also matches "/". Edit here.
AUTO_KEEP_EXCLUDES = (
    "/etc/apk/world",
    "/etc/urandom.seed",
    "/etc/board*.json",
    "/etc/hotplug*.json",
    "/etc/capabilities/*",
    "/etc/banner.openwrt",
    "/lib/firmware/ath11k/*/cal-*.bin",
    "/.wget-hsts",
    "*.apk-new",
    "/tmp/*",
    "/var/*",
    "/run",
    "/run/*",
    # Generated when a service is enabled; a reinstalled package enables its
    # own, and a kept link would point at an init script not yet installed.
    "/etc/rc.d/*",
    # Includes that services (https-dns-proxy, pbr, ...) write at run time;
    # files a package installs here are owned, so excluded anyway.
    "/usr/share/nftables.d/*",
    "/bin/*",
    "/sbin/*",
    "/lib/*",
    "/usr/lib/*",
    "/www/*",
)

# World entries that the firmware pins (name=version and the like) or that
# are its own base; they come with the image and are never added by name.
BASE_PACKAGES = ("kernel", "libc", "base-files")

# Packages not reinstalled automatically, because adding them replaces a
# package the firmware ships (apk resolves the conflict by swapping them).
# Glob patterns -> why; docs/packages.md says how to do it by hand.
SPECIAL_PACKAGES = {
    "dnsmasq-*": "replaces the firmware's dnsmasq",
    "wpad*": "replaces the firmware's wpad variant",
    "hostapd*": "replaces the firmware's wpad/hostapd variant",
    "odhcpd*": "replaces the firmware's odhcpd variant",
    "libustream-*": "replaces the firmware's TLS backend for uclient/uhttpd",
}

# How long a freshly updated router gets to reach its package repositories.
REPOSITORY_DEADLINE_S = 300

# The router's own resolver, and what netifd learned from the WAN. When the
# restored dnsmasq config forwards to a resolver that a reinstalled package
# provides (https-dns-proxy, stubby, ...), the router cannot resolve the
# repositories until that package is back; see TemporaryResolver.
RESOLV_CONF = "/tmp/resolv.conf"
RESOLV_SAVED = "/tmp/rd03v2-resolv.conf.saved"
WAN_RESOLV = "/tmp/resolv.conf.d/resolv.conf.auto"
RESOLVER_MARK = "# rd03v2-updater: temporary resolver, restored after the reinstall"
REPOSITORY_LISTS = "/etc/apk/repositories /etc/apk/repositories.d/*.list"

# Configuration files after the update (see review_apk_new and
# review_image_defaults). The image defaults are read from /rom, the
# read-only copy of the running image, before and after the update.
REMOTE_LIST = "/tmp/rd03v2-files.list"
REMOTE_FILES = "/tmp/rd03v2-files.tar.gz"
ROM_DEFAULTS_OLD = "rom-defaults-old.tar.gz"
CONFFILE_DIR = "conffiles"
APK_NEW = ".apk-new"
# How much of a diff the log shows; the whole of it is always saved.
DIFF_LOG_LINES = 40


def is_auto_keep_excluded(path, excludes=AUTO_KEEP_EXCLUDES):
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in excludes)


def auto_keep_script(excludes=AUTO_KEEP_EXCLUDES):
    """One remote pass over /overlay/upper; see detect_extra_files.

    Prints `AUTOKEEP <path>` for each regular file or symlink that no package
    owns, sysupgrade -l does not list, and no exclude matches, then
    AUTOKEEP_DONE. Whiteouts (character devices) are not files and are
    skipped by -type, as are names with a newline, which `read` would split.
    """
    for pattern in excludes:
        if not all(c.isalnum() or c in "/._-*" for c in pattern):
            raise ValueError(f"unsafe exclude pattern {pattern!r}")
    case = "|".join(excludes)
    return (
        "cd /overlay/upper 2>/dev/null || { echo 'AUTOKEEP_ERROR no /overlay/upper'; exit 1; }\n"
        "command -v apk >/dev/null 2>&1 || { echo 'AUTOKEEP_ERROR no apk'; exit 1; }\n"
        "keep=/tmp/autokeep.$$\n"
        "sysupgrade -l > \"$keep\" 2>/dev/null || "
        "{ rm -f \"$keep\"; echo 'AUTOKEEP_ERROR sysupgrade -l failed'; exit 1; }\n"
        "find . \\( -type f -o -type l \\) | while IFS= read -r p; do\n"
        "  p=${p#.}\n"
        f"  case \"$p\" in {case}) continue ;; esac\n"
        "  grep -qxF \"$p\" \"$keep\" && continue\n"
        "  apk info --who-owns \"$p\" >/dev/null 2>&1 && continue\n"
        "  echo \"AUTOKEEP $p\"\n"
        "done\n"
        "rm -f \"$keep\"\n"
        "echo AUTOKEEP_DONE\n")


def parse_extra_files(output, excludes=AUTO_KEEP_EXCLUDES):
    """The paths auto_keep_script reported, excludes applied again here."""
    lines = output.splitlines()
    for line in lines:
        if line.startswith("AUTOKEEP_ERROR"):
            raise Abort(f"cannot list the extra files: {line.split(' ', 1)[-1]}")
    if "AUTOKEEP_DONE" not in (line.strip() for line in lines):
        raise Abort(f"the extra-file scan did not finish: {output.strip()[-300:]!r}")
    found = {line[len("AUTOKEEP "):] for line in lines if line.startswith("AUTOKEEP /")}
    return sorted(path for path in found if not is_auto_keep_excluded(path, excludes))


def detect_extra_files(host):
    rc, out = install.ssh(host, auto_keep_script(), timeout=600, check=False)
    if rc != 0 and "AUTOKEEP_ERROR" not in out:
        raise Abort(f"cannot list the extra files on {host}: {out.strip()}")
    return parse_extra_files(out)


def _keep_key(line):
    line = line.strip()
    return line.rstrip("/") or line


def keep_append_text(existing, paths):
    """What to append to sysupgrade.conf so that it lists every one of paths.

    Paths already listed (with or without a trailing "/") are not repeated, nor
    is any path twice. A file that does not end in a newline gets one first, so
    the first new path does not run into its last line."""
    listed = {_keep_key(line) for line in existing.splitlines()
              if line.strip() and not line.lstrip().startswith("#")}
    new = []
    for path in paths:
        if _keep_key(path) not in listed:
            listed.add(_keep_key(path))
            new.append(path)
    if not new:
        return ""
    lead = "\n" if existing and not existing.endswith("\n") else ""
    return lead + "".join(path + "\n" for path in new)


def validate_keep_path(path):
    if not path.startswith("/") or "\n" in path or "\r" in path:
        raise Abort(t("--extra-keep needs an absolute path on the router, got {path!r}",
                      path=path))
    return path


def append_keep_list(host, paths):
    """Add paths to the router's sysupgrade.conf; returns those it added."""
    # install.ssh strips its output, which would hide whether the file ends in
    # a newline; a marker after it keeps that last character in place.
    _rc, out = install.ssh(
        host, f"cat {SYSUPGRADE_CONF} 2>/dev/null; echo {KEEP_EOF}", check=False)
    if KEEP_EOF not in out:
        raise Abort(f"cannot read {SYSUPGRADE_CONF}: {out.strip()!r}")
    existing = out.rsplit(KEEP_EOF, 1)[0]
    text = keep_append_text(existing, paths)
    if text:
        install.ssh(host, f"printf %s {shlex.quote(text)} >> {SYSUPGRADE_CONF}")
    return [line for line in text.splitlines() if line]


def filter_world(text):
    """The packages to reinstall, from /etc/apk/world.

    Left out: entries with a version constraint (= < > ~: the firmware pins
    those), the firmware's base (kernel, libc, base-files), and negations
    (!name). Libraries stay: world lists only what was asked for by name, so
    a lib* there was installed on purpose, and dependencies are never in it.
    Sorted, unique."""
    keep = set()
    for entry in text.split():
        if any(mark in entry for mark in "=<>~") or entry.startswith("!"):
            continue
        name = entry.split("@", 1)[0]
        if name in BASE_PACKAGES:
            continue
        keep.add(entry)
    return sorted(keep)


def pinned_names(text):
    """Package names /etc/apk/world pins with a version constraint."""
    names = set()
    for entry in text.split():
        for mark in "=<>~":
            if mark in entry:
                names.add(entry.split(mark, 1)[0].split("@", 1)[0])
                break
    return names


def installed_names(db_text):
    """Names installed or provided, from /lib/apk/db/installed P: and p: lines."""
    names = set()
    for line in db_text.splitlines():
        if line.startswith("P:"):
            names.add(line[2:].strip())
        elif line.startswith("p:"):
            for item in line[2:].split():
                names.add(item.split("=", 1)[0])
    return names


def special_reason(package):
    for pattern, why in SPECIAL_PACKAGES.items():
        if fnmatch.fnmatchcase(package.split("@", 1)[0], pattern):
            return why
    return None


def _write_local(outdir, name, text):
    path = os.path.join(outdir, name)
    with open(path, "w", encoding="utf-8", newline="\n") as output:
        output.write(text if not text or text.endswith("\n") else text + "\n")
    return path


def save_overlay_archive(host, outdir):
    """overlay-full.tar.gz, for comparing by hand later; never restored."""
    _rc, used = install.ssh(host, "du -sk /overlay/upper 2>/dev/null", check=False)
    sizes = [token for token in used.split() if token.isdigit()]
    if not sizes:
        log(f"[c] cannot size /overlay/upper ({used.strip()!r}); overlay archive skipped")
        return None
    # The archive sits in tmpfs while it is copied off, beside a router still
    # running everything, so it gets the same headroom as an uploaded image.
    if int(sizes[0]) + IN_PLACE_MARGIN_KB > available_memory_kb(host):
        log(t("[c] /overlay/upper ({size} KiB) does not fit in the router's free "
              "memory; overlay-full.tar.gz was not saved", size=sizes[0]))
        return None
    local = os.path.join(outdir, "overlay-full.tar.gz")
    try:
        install.ssh(host, f"tar czf {REMOTE_OVERLAY} -C /overlay/upper .", timeout=300)
        scp_from(host, REMOTE_OVERLAY, local)
    finally:
        try:
            install.ssh(host, f"rm -f {REMOTE_OVERLAY}", timeout=30, check=False)
        except Exception:               # noqa: BLE001 -- the first error matters
            pass
    log(f"[c] overlay archive (reference only, never restored) -> {local}")
    return local


def agree(question, assume_yes):
    """install.confirm for a question whose "no" skips one step, not the run.

    Anything but YES is a no; EOF (no terminal) is a no. Ctrl-C still stops
    the whole run."""
    if assume_yes:
        log(t("[?] {question} -- assumed yes (--yes)", question=question))
        return True
    try:
        answer = input(t("[?] {question} [type YES to continue] ", question=question))
    except EOFError:
        return False
    return answer.strip() == "YES"


class CarryoverUndo:
    """What prepare_carryover changed on the router, put back on failure.

    The carry-over writes sysupgrade.conf and /etc/backup/ before the update
    is confirmed, because the backup has to carry them. Until the update
    starts writing the new release (commit()), a decline, a rejected image or
    any other stop puts both back, so "nothing was written" stays true. After
    that, or when it cannot tell, it leaves the router alone."""

    def __init__(self):
        self.host = None
        self.added = []
        self.committed = False

    def snapshot(self, host):
        """Copy what is about to change into /tmp on the router."""
        _rc, out = install.ssh(
            host,
            f"rm -rf {UNDO_DIR} && mkdir -p {UNDO_DIR} && "
            f"if [ -e {SYSUPGRADE_CONF} ]; then cp -p {SYSUPGRADE_CONF} {UNDO_DIR}/conf; fi && "
            f"if [ -e {WORLD_OLD} ]; then cp -p {WORLD_OLD} {UNDO_DIR}/world; fi && "
            f"if [ -d {BACKUP_DIR} ]; then : > {UNDO_DIR}/had-dir; fi && echo UNDO_READY",
            timeout=30, check=False)
        if "UNDO_READY" not in out:
            raise Abort(f"cannot save {SYSUPGRADE_CONF} before changing it; "
                        f"nothing was written: {out.strip()!r}")
        self.host = host

    def commit(self):
        self.committed = True

    def revert(self):
        if self.host is None or self.committed:
            return
        host, self.host = self.host, None
        try:
            _rc, out = install.ssh(
                host,
                f"[ -d {UNDO_DIR} ] || exit 3; "
                f"if [ -e {UNDO_DIR}/conf ]; then cat {UNDO_DIR}/conf > {SYSUPGRADE_CONF}; "
                f"else rm -f {SYSUPGRADE_CONF}; fi && "
                f"if [ -e {UNDO_DIR}/world ]; then cat {UNDO_DIR}/world > {WORLD_OLD}; "
                f"else rm -f {WORLD_OLD}; fi && "
                f"{{ [ -e {UNDO_DIR}/had-dir ] || rmdir {BACKUP_DIR} 2>/dev/null; true; }} && "
                f"rm -rf {UNDO_DIR} && echo UNDONE", timeout=60, check=False)
            if "UNDONE" not in out:
                raise Abort(out.strip() or "no answer")
            log(t("[c] the carry-over changes were undone: {conf} and {world} are as "
                  "they were", conf=SYSUPGRADE_CONF, world=WORLD_OLD))
        except Exception as exc:            # noqa: BLE001 -- the first error matters
            log(t("[!] could not undo the carry-over changes on {host} ({error}). "
                  "By hand, remove these lines from {conf}: {lines}; and remove {world} "
                  "if you did not have one before.", host=host, error=exc,
                  conf=SYSUPGRADE_CONF, lines=", ".join(self.added) or "(none)",
                  world=WORLD_OLD))


def prepare_carryover(host, args, outdir, undo=None):
    """Before the backup: what sysupgrade would drop, made part of it.

    Writes to the router's /etc only (sysupgrade.conf, /etc/backup/), which
    the backup then carries; the image and the partitions are not touched.
    `undo` (a CarryoverUndo) records those writes so that they can be put
    back if the update does not go ahead."""
    log(t("\n=== carry-over: extra files and packages ==="))
    _rc, world = install.ssh(host, "cat /etc/apk/world")
    _write_local(outdir, "world.txt", world)
    _rc, listed = install.ssh(host, "sysupgrade -l", timeout=120)
    _write_local(outdir, "sysupgrade-list.txt", listed)
    save_rom_defaults(host, outdir, listed)

    extras = detect_extra_files(host) if args.auto_keep else []
    if not args.auto_keep:
        log("[c] extra-file scan not run (--auto-keep looks for files no package owns)")
    manual = [validate_keep_path(path) for path in args.extra_keep]
    paths = list(dict.fromkeys(extras + manual))
    if paths:
        log(t("[c] files no package owns and sysupgrade would not keep:"))
        for path in paths:
            log(f"      {path}")
        if not agree(t("add these {count} path(s) to {conf}?",
                       count=len(paths), conf=SYSUPGRADE_CONF), args.yes):
            log(t("[c] not added: the update goes on without these files"))
            paths = []
    elif args.auto_keep:
        log(t("[c] no extra files found that sysupgrade would drop"))
    if undo is not None:
        undo.snapshot(host)
    added = append_keep_list(host, paths + [BACKUP_DIR])
    if undo is not None:
        undo.added = added
    for path in added:
        log(f"[c] {SYSUPGRADE_CONF} += {path}")

    forwarders = local_dns_forwarders(host)
    if forwarders:
        log(t("[c] dnsmasq forwards DNS to a resolver on the router itself ({forwarders}). "
              "If a package you installed provides it, the router cannot resolve "
              "names after the update until that package is reinstalled; the "
              "reinstall then uses the WAN's DNS servers for the router itself.",
              forwarders=", ".join(forwarders)))

    wanted = filter_world(world)
    text = "".join(name + "\n" for name in wanted)
    local = _write_local(outdir, "world-old.txt", text)
    install.ssh(host, f"mkdir -p {BACKUP_DIR}")
    install.scp_to(host, local, WORLD_OLD)
    log(t("[c] {count} package(s) to reinstall after the update -> {path}",
          count=len(wanted), path=WORLD_OLD))
    save_overlay_archive(host, outdir)


def local_dns_forwarders(host):
    """The dnsmasq upstreams on this router itself (127.0.0.1#5053 and the like)."""
    _rc, out = install.ssh(host, "uci -q get dhcp.@dnsmasq[0].server", check=False)
    return [item for item in out.split()
            if item.split("#", 1)[0].split("/")[-1] in ("127.0.0.1", "::1")]


def repository_host(host):
    """The host name of the first package repository, or None."""
    _rc, out = install.ssh(host, f"cat {REPOSITORY_LISTS} 2>/dev/null", check=False)
    for line in out.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name = urllib.parse.urlsplit(line.split()[-1]).hostname
        if name:
            return name
    return None


def wan_nameservers(host):
    """The DNS servers netifd learned for the WAN, as addresses only."""
    _rc, out = install.ssh(host, f"cat {WAN_RESOLV} 2>/dev/null", check=False)
    servers = []
    for line in out.splitlines():
        fields = line.split()
        if len(fields) >= 2 and fields[0] == "nameserver":
            try:
                servers.append(str(ipaddress.ip_address(fields[1])))
            except ValueError:
                continue                    # never put anything else in a command
    return list(dict.fromkeys(servers))


def ssh_lost(host, rc, out, doing):
    """Abort when OpenSSH itself failed (exit 255; see install.key_failure).

    That is not the router's DNS or its repositories, and must not be
    reported as either, or answered by changing its resolver."""
    if rc == 255:
        raise Abort(f"lost the SSH connection to {host} while {doing}: "
                    f"{out.strip()[-300:]}")


def resolves(host, name, server=None):
    target = shlex.quote(name) + (f" {shlex.quote(server)}" if server else "")
    rc, out = install.ssh(host, f"nslookup {target} >/dev/null 2>&1 && echo DNS_OK",
                          timeout=30, check=False)
    ssh_lost(host, rc, out, "checking its DNS")
    return "DNS_OK" in out


class TemporaryResolver:
    """Point the router's *own* resolver at the WAN's DNS while packages install.

    The restored config may make dnsmasq forward every query to a resolver
    that a not-yet-reinstalled package provides, and noresolv keeps it off the
    WAN's servers. The router then cannot resolve its repositories, and so
    cannot install the package that would fix that. Only /tmp/resolv.conf,
    which the router itself uses, is changed: dnsmasq and the LAN keep their
    configuration, and nothing is committed. The WAN's own servers are used,
    never a resolver this tool chooses; without any, it only explains.

    restore() puts the saved file back unless dnsmasq has rewritten it in the
    meantime (a reinstalled DNS package restarts it), in which case that
    version is the right one and stays."""

    def __init__(self, host):
        self.host = host
        self.servers = []
        self.reported = False

    @property
    def active(self):
        return bool(self.servers)

    def engage(self, name):
        """Use the WAN's DNS when the system resolver cannot resolve `name`."""
        if resolves(self.host, name):
            return False
        servers = [server for server in wan_nameservers(self.host)
                   if resolves(self.host, name, server)]
        if not servers:
            # Asked again on each retry, since the WAN may still be coming up;
            # said once.
            if not self.reported:
                log(t("[p] the router cannot resolve {name}, and none of the WAN's "
                      "DNS servers can either. Fix its DNS, then run this again "
                      "with --reinstall-only.", name=name))
                self.reported = True
            return False
        self.servers = servers
        self._write()
        log(t("[p] the router cannot resolve {name}. The restored dnsmasq config "
              "forwards to {forwarders}, probably a resolver from a package that is "
              "not reinstalled yet. Until the reinstall ends, the router itself uses "
              "the WAN's DNS servers ({servers}); dnsmasq and the LAN are unchanged.",
              name=name, forwarders=", ".join(local_dns_forwarders(self.host)) or "?",
              servers=", ".join(servers)))
        return True

    def _write(self):
        text = RESOLVER_MARK + "\n" + "".join(f"nameserver {s}\n" for s in self.servers)
        install.ssh(self.host,
                    f"[ -e {RESOLV_SAVED} ] || cp -p {RESOLV_CONF} {RESOLV_SAVED}; "
                    f"printf %s {shlex.quote(text)} > {RESOLV_CONF}")

    def keep(self, name):
        """Before each install: write it again if dnsmasq replaced it and the
        router's own resolver still fails."""
        if self.active and not resolves(self.host, name):
            self._write()

    def restore(self):
        if not self.active:
            return
        try:
            install.ssh(self.host,
                        f"if [ -e {RESOLV_SAVED} ]; then "
                        f"grep -qF {shlex.quote(RESOLVER_MARK)} {RESOLV_CONF} && "
                        f"cat {RESOLV_SAVED} > {RESOLV_CONF}; "
                        f"rm -f {RESOLV_SAVED}; fi", timeout=30)
            log(t("[p] the router's own resolver is back to its configuration"))
        except (Abort, subprocess.TimeoutExpired) as exc:
            log(t("[!] could not restore {conf} on the router ({error}); restart "
                  "dnsmasq there (service dnsmasq restart) to rebuild it",
                  conf=RESOLV_CONF, error=exc))
        self.servers = []


def wait_for_repositories(host, deadline_s=None, resolver=None):
    """Whether `apk update` succeeded before the deadline.

    With a TemporaryResolver, a failure that is the router's own DNS engages
    it once, and the wait goes on."""
    deadline_s = REPOSITORY_DEADLINE_S if deadline_s is None else deadline_s
    log(t("[p] waiting up to {seconds}s for the router to reach its package "
          "repositories", seconds=deadline_s))
    end = time.time() + deadline_s
    out = ""
    name = None
    while True:
        try:
            rc, out = install.ssh(host, "apk update", timeout=120, check=False)
        except subprocess.TimeoutExpired:
            rc, out = 1, "apk update timed out"
        if rc == 0:
            return True
        if rc != 255 and resolver is not None and not resolver.active:
            name = name or repository_host(host)
            if name and resolver.engage(name):
                continue
        if time.time() + 15 >= end:
            ssh_lost(host, rc, out, "waiting for its package repositories")
            log(f"[p] apk update: {out.strip()[-300:]}")
            return False
        time.sleep(15)


def reinstall_packages(host, outdir, deadline_s=None, assume_yes=True):
    """Install what world-old.txt lists and the updated system lacks.

    Each package is checked first and added only when missing; a pinned or
    special one is never added by name (see SPECIAL_PACKAGES). A package
    whose configuration the backup restored leaves the new default beside it
    as *.apk-new; each of those is then reviewed (review_apk_new), asking
    unless assume_yes. Returns the summary as {"installed", "present",
    "skipped", "failed", "conffiles"} and writes it to reinstall-summary.txt."""
    log(t("\n=== packages: reinstall what the update dropped ==="))
    rc, text = install.ssh(host, f"cat {WORLD_OLD}", check=False)
    if rc != 0:
        local = os.path.join(outdir, "world-old.txt")
        if not os.path.exists(local):
            log(t("[p] no package list on the router or in {outdir}; nothing to "
                  "reinstall", outdir=outdir))
            return None
        with open(local, encoding="utf-8") as source:
            text = source.read()
        log(f"[p] {WORLD_OLD} is missing on the router; using {local}")
    wanted = filter_world(text)
    summary = {"installed": [], "present": [], "skipped": [], "failed": [],
               "conffiles": []}
    if not wanted:
        log(t("[p] the package list is empty; nothing to reinstall"))
        return _write_summary(outdir, summary)
    if any(name.startswith("kmod-") for name in wanted):
        log(t("[p] kernel modules (kmod-*) install only from the repository "
              "built for the running firmware; a kmod that it lacks fails and "
              "is listed below"))

    _rc, db = install.ssh(host, "grep -E '^[Pp]:' /lib/apk/db/installed", check=False)
    have = installed_names(db)
    _rc, world_now = install.ssh(host, "cat /etc/apk/world", check=False)
    pinned = pinned_names(world_now)
    missing = []
    for name in wanted:
        bare = name.split("@", 1)[0]
        if bare in have:
            summary["present"].append(name)
        elif bare in pinned:
            summary["skipped"].append((name, "pinned by the firmware"))
        elif special_reason(name):
            summary["skipped"].append((name, special_reason(name)
                                       + "; see docs/packages.md"))
        else:
            missing.append(name)

    resolver = TemporaryResolver(host)
    try:
        if missing and not wait_for_repositories(host, deadline_s, resolver):
            summary["failed"] += [(name, "the package repositories are unreachable")
                                  for name in missing]
            missing = []
        before = list_apk_new(host) if missing else set()
        repository = repository_host(host) if resolver.active else None
        for name in missing:
            if repository:
                resolver.keep(repository)
            try:
                rc, out = install.ssh(host, f"apk add {shlex.quote(name)}",
                                      timeout=600, check=False)
            except subprocess.TimeoutExpired:
                rc, out = 1, "apk add timed out"
            if rc == 0:
                summary["installed"].append(name)
                log(f"[p] installed {name}")
            elif rc == 255:
                # OpenSSH failed, not apk: every later package would fail the
                # same way, and not for any reason of its own.
                reason = "the SSH connection failed: " + (
                    out.strip().splitlines() or ["exit 255"])[-1]
                rest = missing[missing.index(name):]
                summary["failed"] += [(other, reason) for other in rest]
                log(f"[p] {reason}; {len(rest)} package(s) left for --reinstall-only")
                break
            else:
                reason = (out.strip().splitlines() or [f"exit {rc}"])[-1]
                summary["failed"].append((name, reason))
                log(f"[p] could not install {name}: {reason}")
    finally:
        resolver.restore()
    if summary["installed"]:
        summary["conffiles"] = review_apk_new(host, outdir, before, assume_yes)
    return _write_summary(outdir, summary)


def _write_summary(outdir, summary):
    lines = [f"installed: {name}" for name in summary["installed"]]
    lines += [f"already present: {name}" for name in summary["present"]]
    lines += [f"skipped: {name} -- {why}" for name, why in summary["skipped"]]
    lines += [f"failed: {name} -- {why}" for name, why in summary["failed"]]
    lines += [f"config {decision}: {path}" for path, decision in summary.get("conffiles", [])]
    path = _write_local(outdir, "reinstall-summary.txt", "\n".join(lines))
    log(t("[p] packages: {installed} installed, {present} already present, "
          "{skipped} skipped, {failed} failed -> {path}",
          installed=len(summary["installed"]), present=len(summary["present"]),
          skipped=len(summary["skipped"]), failed=len(summary["failed"]),
          path=path))
    for name, why in summary["skipped"]:
        log(t("[p] not reinstalled, do it by hand: {name} ({why})", name=name, why=why))
    for name, why in summary["failed"]:
        log(t("[p] failed: {name} ({why})", name=name, why=why))
    if any(name.startswith("kmod-") for name, _why in summary["failed"]):
        log(t("[p] a kmod that failed is not in the repository of the running "
              "firmware; do not install one built for another kernel"))
    return summary


def read_files_archive(path):
    """{"/absolute/path": bytes} for the regular files in a fetch_files archive.

    Names are checked rather than trusted: nothing absolute, no "..", so an
    odd archive cannot name a path outside the tree it claims to be."""
    files = {}
    with tarfile.open(path, "r:gz") as archive:
        for member in archive.getmembers():
            if not member.isfile():
                continue
            name = member.name[2:] if member.name.startswith("./") else member.name
            if name.startswith("/") or ".." in name.split("/"):
                raise Abort(f"{path} names {member.name!r}, outside its tree")
            files["/" + name] = archive.extractfile(member).read()
    return files


def fetch_files(host, root, paths, keep=None):
    """{path: bytes} for those of `paths` that are regular files under `root`.

    One tar on the router, copied off with scp, so the bytes are exact (ssh
    output is stripped). `paths` are absolute, as seen from `root`: /rom for
    the image's defaults, / for what is in use. keep: also save the archive
    there; it is not written when none of the paths exists."""
    relative = [path.lstrip("/") for path in paths
                if path.startswith("/") and "\n" not in path]
    if not relative:
        return {}
    with tempfile.TemporaryDirectory() as tmp:
        listing = os.path.join(tmp, "files.list")
        with open(listing, "w", encoding="utf-8", newline="\n") as output:
            output.write("".join(path + "\n" for path in relative))
        have = REMOTE_LIST + ".have"
        try:
            install.scp_to(host, listing, REMOTE_LIST)
            _rc, out = install.ssh(
                host,
                f"cd {shlex.quote(root)} && "
                "while IFS= read -r p; do [ -f \"$p\" ] && [ ! -L \"$p\" ] && echo \"$p\"; "
                f"done < {REMOTE_LIST} > {have} && "
                f"if [ -s {have} ]; then tar czf {REMOTE_FILES} -T {have} && echo FETCH_OK; "
                "else echo FETCH_NONE; fi",
                timeout=120, check=False)
            if "FETCH_NONE" in out:
                return {}
            if "FETCH_OK" not in out:
                raise Abort(f"cannot read files under {root} on {host}: {out.strip()!r}")
            local = keep or os.path.join(tmp, "files.tar.gz")
            scp_from(host, REMOTE_FILES, local)
        finally:
            try:
                install.ssh(host, f"rm -f {REMOTE_LIST} {have} {REMOTE_FILES}",
                            timeout=30, check=False)
            except Exception:               # noqa: BLE001 -- the first error matters
                pass
        # Only what was asked for: a tar may add entries of its own (macOS's
        # adds AppleDouble ._ files), and those are not the router's files.
        wanted = {"/" + path for path in relative}
        return {path: data for path, data in read_files_archive(local).items()
                if path in wanted}


def _text(data):
    return data.decode("utf-8", "replace").splitlines(keepends=True)


def unified(old, new, old_name, new_name):
    """A unified diff of two file contents, or a note when either is binary."""
    if b"\0" in old or b"\0" in new:
        return f"Binary files {old_name} and {new_name} differ\n"
    lines = difflib.unified_diff(_text(old), _text(new), old_name, new_name)
    return "".join(line if line.endswith("\n") else line + "\n" for line in lines)


def save_diff(outdir, path, suffix, text):
    folder = os.path.join(outdir, CONFFILE_DIR)
    os.makedirs(folder, mode=0o700, exist_ok=True)
    local = os.path.join(folder, path.strip("/").replace("/", "__") + suffix)
    with open(local, "w", encoding="utf-8", newline="\n") as output:
        output.write(text)
    return local


def diff_size(text):
    """(added, removed) lines of a unified diff, headers left out."""
    lines = text.splitlines()
    added = sum(1 for line in lines if line.startswith("+") and not line.startswith("+++"))
    removed = sum(1 for line in lines if line.startswith("-") and not line.startswith("---"))
    return added, removed


def show_private_diff(path, text, saved, interactive):
    """A diff against the operator's own file, which may hold secrets.

    Passwords, keys and tokens live in /etc/config, and transcripts get
    pasted into public issues. So the log (console, transcript.log, the GUI)
    only says how big the diff is and where it is saved; an operator who is
    about to answer for that file sees it on the terminal, outside the log."""
    added, removed = diff_size(text)
    log(f"[p] {path}: +{added} -{removed} line(s) against the package's version; "
        f"diff (may hold your secrets) in {saved}")
    if interactive and sys.stdout.isatty():
        sys.stdout.write("".join("      " + line + "\n" for line in text.splitlines()))
        sys.stdout.flush()


def log_diff(text, saved):
    """For diffs between two image defaults only: public firmware content."""
    lines = text.splitlines()
    for line in lines[:DIFF_LOG_LINES]:
        log("      " + line)
    if len(lines) > DIFF_LOG_LINES:
        log(f"      ... {len(lines) - DIFF_LOG_LINES} more lines in {saved}")


# ---- configuration files of reinstalled packages (as dpkg asks) -------------

def list_apk_new(host):
    _rc, out = install.ssh(host, f"find /etc -name '*{APK_NEW}' 2>/dev/null",
                           check=False)
    return {line.strip() for line in out.splitlines()
            if line.strip().startswith("/") and line.strip().endswith(APK_NEW)}


def ask_conffile(path, assume_yes):
    """The decision for one configuration file: keep, new or defer."""
    if assume_yes:
        return "defer"
    choices = {"k": "keep", "n": "new", "d": "defer"}
    while True:
        try:
            answer = input(t("[?] {path}: keep your version (k), install the "
                             "package's new one (n), or decide later (d)? "
                             "[k/n/d] ", path=path)).strip().lower()
        except EOFError:
            return "defer"
        if answer in choices:
            return choices[answer]


def review_apk_new(host, outdir, before, assume_yes):
    """Decide each *.apk-new that the reinstall left, as dpkg asks on upgrade.

    apk never overwrites a configuration file you changed: it keeps yours and
    writes the package's new default beside it as *.apk-new. Only those that
    this reinstall created (not in `before`) are offered, with the diff from
    yours to the package's on the terminal but never in the log (see
    show_private_diff), and for each one:
      keep   your version stays and the *.apk-new is removed;
      new    the package's version is installed, yours is kept as *.apk-old;
      defer  both stay as they are (always so with --yes).
    Every diff is saved under conffiles/ in the output directory.
    Returns [(path, decision)]."""
    created = sorted(list_apk_new(host) - set(before))
    if not created:
        return []
    log(t("\n=== configuration files: new package versions ==="))
    log(t("[p] {count} reinstalled package(s) brought a new version of a "
          "configuration file you changed; yours is in use", count=len(created)))
    pairs = [(new[:-len(APK_NEW)], new) for new in created]
    files = fetch_files(host, "/", [path for pair in pairs for path in pair])
    decisions = []
    for path, new in pairs:
        if path not in files or new not in files:
            log(f"[p] cannot read {path} or {new}; left as it is")
            decisions.append((path, "defer"))
            continue
        text = unified(files[path], files[new], f"{path} (yours)", f"{new} (package)")
        saved = save_diff(outdir, path, ".apk-new.diff", text)
        show_private_diff(path, text, saved, interactive=not assume_yes)
        try:
            decision = ask_conffile(path, assume_yes)
        except KeyboardInterrupt:
            # The update itself is done; Ctrl-C only stops the questions.
            log("[p] interrupted: this and the remaining files are left for later")
            assume_yes, decision = True, "defer"
        quoted, quoted_new = shlex.quote(path), shlex.quote(new)
        if decision == "keep":
            install.ssh(host, f"rm -f {quoted_new}")
            log(t("[p] kept your {path}; the package's version was removed",
                  path=path))
        elif decision == "new":
            old = path + ".apk-old"
            install.ssh(host, f"cp -p {quoted} {shlex.quote(old)} && "
                              f"mv -f {quoted_new} {quoted}")
            log(t("[p] installed the package's {path}; yours is in {old}. Restart "
                  "the service that reads it, or reboot.", path=path, old=old))
        else:
            log(t("[p] left for later: {path} and {new}; the difference is in "
                  "{diff}", path=path, new=new, diff=saved))
        decisions.append((path, decision))
    return decisions


# ---- configuration files of the image's own packages ------------------------

def save_rom_defaults(host, outdir, listed):
    """Before the update: the image defaults of the files the backup keeps.

    `listed` is `sysupgrade -l`. For each of those files that the running
    image ships (it is in /rom), its default is saved as
    rom-defaults-old.tar.gz. With the new image's default read after the
    update, that gives the three versions a real comparison needs. Never
    stops the update: this is only for the review afterwards."""
    paths = [line.strip() for line in listed.splitlines() if line.strip().startswith("/")]
    keep = os.path.join(outdir, ROM_DEFAULTS_OLD)
    try:
        found = fetch_files(host, "/rom", paths, keep=keep)
    except (Abort, OSError, tarfile.TarError) as exc:
        log(f"[c] could not save the image defaults ({exc}); the review of "
            "changed defaults after the update is skipped")
        return 0
    log(f"[c] {len(found)} image default(s) of kept files -> {keep}" if found
        else "[c] none of the kept files has an image default")
    return len(found)


def review_image_defaults(host, outdir):
    """After the update: which kept files' image defaults the new release changed.

    Your version was restored and is in use, which is right for a file whose
    default did not change; those are not mentioned. For one whose default
    did change, the upstream change (old default to new) is saved and shown,
    so you can carry it into yours. Nothing on the router is changed.
    Returns [(path, saved diff or None)]."""
    archive = os.path.join(outdir, ROM_DEFAULTS_OLD)
    if not os.path.exists(archive):
        return []
    log(t("\n=== configuration files: image defaults that changed upstream ==="))
    old = read_files_archive(archive)
    new = fetch_files(host, "/rom", sorted(old))
    changed = sorted(path for path in old if new.get(path) != old[path])
    if not changed:
        log(t("[r] the new release changed none of the defaults of the files you "
              "changed; your versions stay as they are"))
        return []
    mine = fetch_files(host, "/", [path for path in changed if path in new])
    results = []
    log(t("[r] the new release changed the default of {count} file(s) that you "
          "changed. Yours were restored and are in use; carry over what you "
          "need from the upstream change:", count=len(changed)))
    for path in changed:
        if path not in new:
            log(t("[r] the new release no longer ships {path}", path=path))
            results.append((path, None))
            continue
        if mine.get(path) == new[path]:
            results.append((path, None))
            continue
        text = unified(old[path], new[path], f"{path} (old default)",
                       f"{path} (new default)")
        saved = save_diff(outdir, path, ".upstream.diff", text)
        log(t("[r] {path}: upstream change in {diff}", path=path, diff=saved))
        log_diff(text, saved)
        results.append((path, saved))
    return results


def scp_from(host, remote, local):
    """Copy remote:path off the box. install.py only ever sends."""
    if install.key_auth() is not None:
        return install.key_scp(host, local, remote, upload=False, timeout=300)
    if hostnet.is_windows():
        try:
            return winssh.download(host, remote, local, install.SSH_PASSWORDS)
        except winssh.SSHError as exc:
            raise Abort(str(exc)) from exc
    src = f"root@[{host}]:{remote}" if ":" in host else f"root@{host}:{remote}"
    last = None
    for password in install.SSH_PASSWORDS:
        argv = ["scp", *hostnet.scp_legacy_flag(), "-o", "StrictHostKeyChecking=no",
                "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR",
                "-o", "NumberOfPasswordPrompts=1", src, local]
        env = None
        if shutil.which("sshpass"):
            argv = ["sshpass", "-e"] + argv
            env = install.password_env(password)
        result = subprocess.run(argv, capture_output=True, text=True,
                                timeout=300, env=env)
        if result.returncode == 0:
            return
        last = result.stderr.strip()
    raise Abort(f"scp from {remote} failed: {last}")


def check_config_backup(path):
    """Refuse an archive `sysupgrade -f` could not restore your config from."""
    try:
        with tarfile.open(path, "r:gz") as archive:
            names = archive.getnames()
    except (OSError, tarfile.TarError) as exc:
        raise Abort(f"{path} is not a readable config backup: {exc}")
    if not any(name.lstrip("./").startswith("etc/config/") for name in names):
        raise Abort(f"{path} holds no etc/config/: it is not a config backup")


def turns_off_password_login(path):
    """Whether restoring this archive leaves the router refusing passwords."""
    try:
        with tarfile.open(path, "r:gz") as archive:
            member = archive.extractfile("etc/config/dropbear")
            text = member.read().decode("utf-8", "replace") if member else ""
    except (OSError, KeyError, tarfile.TarError):
        return False
    return any(line.split()[:3] in (["option", "PasswordAuth", "'off'"],
                                    ["option", "RootPasswordAuth", "'off'"])
               for line in text.splitlines())


def installed_release(host):
    """The release tag the installed system was built as, or '' if it has none.

    Only a stamp that is plainly absent means ''. A read that fails is an
    error, not a reason to pick the RAM route without saying so.
    """
    rc, out = install.ssh(
        host, "if [ -e /etc/rd03v2-release ]; then cat /etc/rd03v2-release; "
              "else echo RD03V2_NO_STAMP; fi", check=False)
    if rc != 0:
        raise Abort(f"cannot read /etc/rd03v2-release on {host}: {out!r}; "
                    "nothing was written")
    if out.strip() == "RD03V2_NO_STAMP":
        return ""
    for line in out.splitlines():
        if line.startswith("RD03V2_TAG="):
            return line.split("=", 1)[1].strip().strip("'\"")
    return ""


def is_v2_release(tag):
    """A published v2.x tag; DEV builds and v1.x are not."""
    parts = (tag or "").split(".")
    return len(parts) >= 2 and parts[0] == "v2" and parts[1].isdigit()


def carries_the_refusal(tag):
    """v2.0.0 to v2.1.2 are the releases that refuse an in-place sysupgrade."""
    try:
        version = tuple(int(part) for part in tag.lstrip("v").split("."))
    except ValueError:
        return False
    return (2, 0, 0) <= version <= (2, 1, 2)


def only_the_in_place_refusal(output):
    """Whether sysupgrade -T objected to nothing but running in place."""
    lines = []
    for line in output.splitlines():
        line = line.strip()
        if " upgrade: " in line:            # "Wed Nov 11 ... 2026 upgrade: ..."
            line = line.split(" upgrade: ", 1)[1].strip()
        if line:
            lines.append(line)
    return (IN_PLACE_REFUSAL in lines
            and all(line in (IN_PLACE_REFUSAL,) + _T_HARMLESS for line in lines))


def available_memory_kb(host):
    rc, out = install.ssh(host, "grep MemAvailable /proc/meminfo", check=False)
    values = [token for token in out.split() if token.isdigit()]
    if rc != 0 or not values:
        raise Abort(f"cannot read the free memory on {host}: {out!r}")
    return int(values[0])


def require_idle_kernel_partition(host, mtd):
    """The hard interlock: never write a partition a UBI is attached to."""
    _rc, attached = install.ssh(
        host,
        "for u in /sys/class/ubi/ubi[0-9]; do cat $u/mtd_num 2>/dev/null; done",
        check=False)
    live = {int(item) for item in attached.split() if item.isdigit()}
    if not live:
        # Fail closed: reading nothing would make any target look idle.
        raise Abort("could not read which MTDs the running UBIs sit on "
                    "(/sys/class/ubi/*/mtd_num); refusing to pick a target")
    if mtd["ubi_kernel"]["index"] in live:
        raise Abort(f"ubi_kernel (mtd{mtd['ubi_kernel']['index']}) has a UBI "
                    "attached; reboot the router before updating")


def preflight(host, profile, rel, images, images_dir, router_iface=None,
              allow_in_place=True):
    """Everything that must hold before a byte is written.

    Returns (rootfs type, route, installed release). The rootfs type is 'overlay' for the installed
    system, or 'tmpfs' when an earlier run already reached the RAM installer.
    The route is 'in-place' for an installed v2 release going to a v2 release,
    and 'ram' otherwise.
    """
    log(t("\n=== stage 1: pre-flight (nothing is written) ==="))
    board, rootfs = identify(host, profile, router_iface)
    log(f"[1] {host}: {board}, rootfs_type={rootfs}")
    why = revert.require_nand_support(host, rel, profile, images_dir)
    log(f"[1] NAND gate: PASS -- {why}")
    if rootfs == "tmpfs":
        return rootfs, "ram", ""
    if rootfs != "overlay":
        raise Abort(f"rootfs_type is {rootfs!r}: neither the installed system "
                    "nor the RAM installer, so nothing here applies")

    mtd = require_permanent_layout(host, profile, board, rootfs)
    require_idle_kernel_partition(host, mtd)
    return _choose_route(host, rel, images, mtd, allow_in_place)


def identify(host, profile, router_iface=None):
    """Log in and return (board, rootfs type); refuses any other board."""
    # Refuses this host's own gateway; see install.gateway_conflict.
    install.check_is_openwrt_ram(host, router_iface=router_iface)
    rc, out = install.ssh(host, "echo up", timeout=30, check=False)
    if rc != 0:
        if install.key_auth() is not None:
            # No password is tried after a refused key, not even the RAM
            # installer's empty one: see KeyAuth.
            raise Abort(t(
                "{host} did not accept the SSH key login, and no password is "
                "tried in its place: {output}\nCheck the key, ssh-agent (ssh-add) "
                "and ~/.ssh/config with a manual `ssh` first. If the router is in "
                "the RAM installer (an interrupted update), it has no copy of your "
                "key: run this again without --ssh-key-auth. Nothing was written.",
                host=host, output=out))
        if "Permission denied" in out:
            raise Abort(t(
                "{host} refused the root password offered. Pass the current one "
                "with --root-password. For a router set to SSH keys only "
                "(PasswordAuth off), use --ssh-key-auth. Nothing was written.",
                host=host))
        raise Abort(f"cannot log in to {host}: {out}")
    return revert.state_of(host, profile)


def require_permanent_layout(host, profile, board, rootfs):
    _rc, mtd_text = install.ssh(host, "cat /proc/mtd")
    if not install.permanent_system_matches(profile, board, rootfs, mtd_text):
        raise Abort("the partition map is not this profile's OpenWrt layout: "
                    f"expected {sorted(profile.openwrt_partitions)}\n{mtd_text}")
    return install.parse_mtd(mtd_text)


def _choose_route(host, rel, images, mtd, allow_in_place):
    installed = installed_release(host)
    in_place = (allow_in_place and is_v2_release(installed)
                and is_v2_release(rel.tag))
    log(f"[1] installed release {installed or 'unknown'}, target {rel.tag}: "
        + ("in-place sysupgrade" if in_place else "through the RAM installer"))
    if in_place:
        image = images["sysupgrade"]
        needed = os.path.getsize(image["path"]) // 1024 + IN_PLACE_MARGIN_KB
    else:
        image = images["initramfs_ubi"]
        size = os.path.getsize(image["path"])
        if size > mtd["ubi_kernel"]["size"]:
            raise Abort(f"{image['name']} is {size} B, ubi_kernel holds "
                        f"{mtd['ubi_kernel']['size']} B")
        needed = size // 1024 + MEMORY_MARGIN_KB
    available = available_memory_kb(host)
    log(f"[1] {available} KiB available, {image['name']} needs {needed} KiB")
    if available < needed:
        raise Abort(t("only {available} KiB of memory is available and {image} "
                      "needs {needed} KiB. Free some first, for example by "
                      "stopping services you can spare, then run this again.",
                      available=available, image=image["name"], needed=needed))
    return "overlay", ("in-place" if in_place else "ram"), installed


def permanent_ssid(host, args, verify_transport, rootfs, profile):
    """The network final verification waits on, known before anything is written.

    install.flash refuses without one, but only after the pivot, when the
    installed kernel is already gone. Where the answer comes from matters: on
    the resume path `host` is the RAM installer, whose wireless config is the
    installer's own, so asking it would strand the operator on a network that
    disappears. The config archive, when there is one, is what sysupgrade -f
    will apply and therefore what says which network the box returns on.
    """
    if verify_transport != "wifi":
        return args.wifi_ssid
    if not args.keep_settings:
        raise Abort(t("a clean install leaves Wi-Fi disabled, so it cannot be "
                      "verified over Wi-Fi. Connect a cable and pass "
                      "--verify-interface, or keep the settings."))
    if args.wifi_ssid:
        if args.config_backup:
            install.validate_wifi_config_archive(args.config_backup, profile,
                                                 args.wifi_ssid)
        return args.wifi_ssid
    if args.config_backup:
        archived = install.ssid_from_config_archive(args.config_backup, profile)
        if not archived:
            raise Abort(t("the config archive does not enable a Wi-Fi network, so "
                          "the update cannot be verified over Wi-Fi. Pass "
                          "--verify-interface to verify over a cable."))
        return archived
    if rootfs == "tmpfs":
        raise Abort(t("the router is in the RAM installer, whose wireless config "
                      "is the installer's, not yours. Pass --wifi-ssid, or "
                      "--config-backup with the archive the earlier run saved."))
    rc, out = install.ssh(host, "uci -q get wireless.@wifi-iface[0].ssid",
                          check=False)
    ssid = out.strip()
    if rc != 0 or not ssid:
        raise Abort(t("cannot tell which SSID to rejoin after the update; pass it "
                      "with --wifi-ssid, or verify over a cable with "
                      "--verify-interface."))
    return ssid


def backup_config(host, outdir):
    log(t("\n=== backup ==="))
    install.ssh(host, f"sysupgrade -b {REMOTE_BACKUP}", timeout=120)
    local = os.path.join(outdir, "config-backup.tar.gz")
    scp_from(host, REMOTE_BACKUP, local)
    _rc, remote_sum = install.ssh(host, f"sha256sum {REMOTE_BACKUP}", timeout=60)
    if release.sha256(local) not in remote_sum:
        raise Abort("the config backup did not survive the copy off the box; "
                    "nothing was written")
    check_config_backup(local)
    log(f"[b] config backup -> {local}")
    return local


def wait_for_ram_system(host, interface, fallback, deadline_s=None):
    """The RAM system's address, once that is provably what answers.

    The installed system keeps answering for a while after `reboot`, and it may
    also have an empty root password, so answering proves nothing. Only tmpfs
    does. The check offers nothing but the RAM system's empty password: a
    refusal just means it is not up yet, and a wrong device learns nothing.
    """
    deadline_s = RAM_DEADLINE_S if deadline_s is None else deadline_s
    log(f"[2] waiting up to {deadline_s}s for the RAM installer")
    end = time.time() + deadline_s
    last = note = None
    while time.time() < end:
        try:
            rc, out = install.ssh(host, ROOTFS_TYPE, timeout=30, check=False,
                                  passwords=install.RAM_PASSWORDS)
        except subprocess.TimeoutExpired:
            rc, out = 1, "ssh timed out"
        if rc == 0 and out.strip() == "tmpfs":
            log(f"[2] the RAM installer is up at {host}")
            return host
        note = f"{host}: {out.strip() or f'no answer (rc={rc})'}"
        if note != last:
            log(f"[2] not the RAM installer yet -- {note}")
            last = note
        time.sleep(5)
        if interface:
            try:
                host = install.wait_and_discover(
                    interface, deadline_s=min(30, max(1, int(end - time.time()))),
                    fallback=fallback, router_gateway_ok=True)
            except Abort as exc:
                note = str(exc)
    raise Abort(f"the RAM installer was not seen within {deadline_s}s, so nothing "
                f"more was written. Last: {note}")


def _hint_path(path):
    """A path as the operator's own shell needs it, inside a pasted hint."""
    return subprocess.list2cmdline([path]) if hostnet.is_windows() else shlex.quote(path)


def _keep_flag(config_tar):
    return (f"--config-backup {_hint_path(config_tar)}" if config_tar
            else "--no-keep-settings")


def resume_hint(config_tar):
    return t("The installed kernel has been replaced by the RAM installer, so the "
             "router boots into that now. Reach it, then run this again with "
             "{keep} to continue; do not skip this step.",
             keep=_keep_flag(config_tar))


def retry_hint(config_tar):
    return t("The pivot did not finish. ubi_kernel held the installed kernel, so "
             "nothing bootable is left there: do NOT reboot or power-cycle. Re-run "
             "the same command with {keep} to retry the write.",
             keep=_keep_flag(config_tar))


def _remove_upload(host):
    """Free the router's RAM after an update that was not launched.

    Best effort: it runs while another error is on its way out, which must not
    be replaced by a second one from a router that has stopped answering.
    """
    try:
        install.ssh(host, "rm -f /tmp/fw.bin /tmp/cfg.tar.gz /tmp/run-sysupgrade.sh",
                    timeout=30, check=False)
    except Exception:                   # noqa: BLE001 -- the first error matters
        pass


def flash_in_place(host, rel, images, args, profile, manifest, verify_transport,
                   installed, undo=None):
    """Update the installed system with sysupgrade from itself, and verify it."""
    log(t("\n=== stage 3: flash (sysupgrade from the installed system) ==="))
    image = images["sysupgrade"]
    install.confirm(t("update {host} in place to {tag} with {image}?",
                      host=host, tag=rel.tag, image=image["name"]), args.yes)

    # Read while the router, often this host's way out, still answers.
    expected_revision = release.revision(rel, args.images)
    _rc, boot_id = install.ssh(host, "cat /proc/sys/kernel/random/boot_id",
                               check=False)
    boot_id = boot_id.strip()
    if not boot_id:
        raise Abort(f"cannot read {host}'s boot id; nothing was written")
    # A kept configuration keeps the router's address, so verification goes
    # back to this host. A clean one comes up on the profile's default address.
    same_host = bool(args.config_backup or args.keep_settings)
    verify_interface = None if same_host else args.verify_interface
    # On a cable, the router may be this host's gateway (its own DHCP):
    # pre-flight accepted that, and verification has to as well.
    # Under key auth nothing secret is offered and the host key identifies the
    # router, so it is accepted as the gateway on its interface, Wi-Fi too.
    gateway_iface = (args.discover if args.transport == "wired" or args.ssh_key_auth
                     else None)
    router_mac = install.neighbour_mac(host, gateway_iface) if gateway_iface else None

    try:
        install.scp_to(host, image["path"], "/tmp/fw.bin")
        _rc, out = install.ssh(host, "sha256sum /tmp/fw.bin", timeout=300)
        if image["sha256"] not in out:
            raise Abort(f"uploaded image sha256 mismatch: {out!r}")
        log(f"[3] uploaded and verified: {image['sha256'][:16]}...")

        rc, out = install.ssh(host, "sysupgrade -T /tmp/fw.bin", timeout=300,
                              check=False)
        force = False
        if rc != 0:
            if not (only_the_in_place_refusal(out) and carries_the_refusal(installed)):
                raise Abort(f"sysupgrade -T rejected {image['name']}; nothing was "
                            f"written:\n{out}")
            rc, forced = install.ssh(host, "sysupgrade -T -F /tmp/fw.bin",
                                     timeout=300, check=False)
            if rc != 0:
                raise Abort(f"sysupgrade -T -F rejected {image['name']}; nothing "
                            f"was written:\n{forced}")
            force = True
            log(f"[3] sysupgrade -T said: {out.strip()}")
            log(t("[3] the running release refuses an in-place sysupgrade, as v2.0.0 "
                  "to v2.1.2 do. That is its only objection to this image, so this "
                  "update passes -F."))
        else:
            log("[3] sysupgrade -T accepted the image")

        if args.config_backup:
            install.scp_to(host, args.config_backup, "/tmp/cfg.tar.gz")
            rc, out = install.ssh(host, "gzip -t /tmp/cfg.tar.gz && tar tzf "
                                  "/tmp/cfg.tar.gz", timeout=60, check=False)
            if rc != 0:
                raise Abort(f"the config tarball did not survive the upload: {out}")
            opts = "-f /tmp/cfg.tar.gz "
        elif args.keep_settings:
            # sysupgrade keeps the configuration by default. The launcher reads an
            # empty option list as -n, so name a harmless option instead.
            opts = "-v "
        else:
            opts = "-n "
        if force:
            opts = "-F " + opts
        install.update_resume_fields(
            manifest, verify_host=host if same_host else None,
            verify_host_iface=gateway_iface, router_mac=router_mac,
            expected_revision=expected_revision, boot_id=boot_id)
    except BaseException:
        # Anything before the launch, a timeout or Ctrl-C included: nothing was
        # written, so take the 16 MB back from a router that is short of RAM.
        _remove_upload(host)
        raise

    # From here the new release is being written: the carry-over is part of it.
    if undo is not None:
        undo.commit()
    install.launch_sysupgrade(host, opts, manifest, verify_interface,
                              install.ssh, install.scp_to, where=host)
    log(t("[3] do not touch the power"))
    log(t("[3] sysupgrade writes the new release in place and reboots"))
    time.sleep(60)
    try:
        found = install.verify_permanent_system(
            profile, interface=verify_interface,
            fallback=(profile.openwrt_host
                      if not same_host and verify_transport == "wired" else None),
            host=host if same_host else None,
            router_mac=router_mac, host_iface=gateway_iface if same_host else None,
            expected_revision=expected_revision)
        install.require_new_boot(found, boot_id)
    except Abort:
        log(t("[!] sysupgrade was launched; do not run the flash stage again"))
        log(t("[!] verification-only continuation:"))
        log("    " + install.verification_command(
            manifest, verify_interface, host=host if same_host else None))
        raise
    install.update_resume_phase(manifest, "flash-complete")
    return found


def check_key_options(args):
    """The --ssh-key-auth combinations that can work, checked before any work."""
    if not args.ssh_key_auth:
        if args.ssh_identity or args.ssh_alias:
            raise Abort(t("--ssh-identity and --ssh-alias require --ssh-key-auth"))
        return
    if args.root_password:
        raise Abort(t("--ssh-key-auth sends no password; drop --root-password"))
    if not args.keep_settings:
        raise Abort(t("--ssh-key-auth needs the settings kept: a clean install "
                      "removes your public key and the router's host key, so the "
                      "update could not be verified by key"))
    if args.ssh_identity:
        args.ssh_identity = os.path.abspath(os.path.expanduser(args.ssh_identity))
        if not os.path.isfile(args.ssh_identity):
            raise Abort(t("SSH private key file does not exist"))
    if args.ssh_alias:
        try:
            ipaddress.ip_address((args.host or "").split("%", 1)[0])
        except ValueError as exc:
            raise Abort(t("--ssh-alias requires --host with the router's numeric "
                          "address, which the network checks use")) from exc


def enable_key_auth(args):
    """Send every installed-system ssh/scp through OpenSSH with keys only."""
    if args.ssh_alias:
        install.require_alias_matches(args.ssh_alias, args.host)
    install.KEY_AUTH = install.KeyAuth(args.ssh_identity, alias=args.ssh_alias,
                                       alias_for=args.host)
    log(t("[*] SSH: key login to the installed system through OpenSSH and "
          "~/.ssh/config; no password is sent, and known_hosts must already "
          "hold the router's host key"))


def require_tools_and_login(args):
    install.require_host_tools(password=bool(args.root_password),
                               key=args.ssh_key_auth)
    if args.ssh_key_auth:
        enable_key_auth(args)
    if args.root_password:
        install.SSH_PASSWORDS.append(args.root_password)
        restore.SSH_PASSWORDS[:] = install.SSH_PASSWORDS


def gateway_allowed(args):
    """Whether the router may be this host's default gateway on args.discover.

    On a cable the router's own DHCP makes it this host's gateway. That is
    accepted while all we would offer it is the empty password, the same rule
    as restore.locate; a configured one goes only to a link-local peer. Under
    --ssh-key-auth it is accepted as well, on Wi-Fi too: nothing secret is
    sent (the key never leaves this host), and known_hosts is checked
    strictly, so a device that only shares the router's address fails the
    host key check before anything runs on it."""
    if args.ssh_key_auth:
        return True
    return args.transport == "wired" and not any(install.SSH_PASSWORDS)


def open_outdir(args, prefix):
    outdir = os.path.abspath(args.outdir or f"{prefix}-{time.strftime('%Y%m%d-%H%M%S')}")
    os.makedirs(outdir, mode=0o700, exist_ok=True)
    os.chmod(outdir, 0o700)
    chain.set_log_sink(open(f"{outdir}/transcript.log", "w", encoding="utf-8"))
    log(f"[*] output -> {outdir}/")
    return outdir


def finish_packages(host, args, outdir, config_tar):
    """After a verified update: review the configuration files, reinstall.

    Neither ever turns that update into a failure."""
    if not config_tar or not host:
        return
    try:
        review_image_defaults(host, outdir)
    except (Abort, restore.RestoreError, subprocess.TimeoutExpired, OSError,
            tarfile.TarError) as exc:
        log(f"[r] the review of changed image defaults stopped: {exc}")
    if args.no_reinstall:
        log("[p] package reinstall skipped (--no-reinstall)")
        return
    try:
        reinstall_packages(host, outdir, assume_yes=args.yes)
    except (Abort, restore.RestoreError, subprocess.TimeoutExpired, OSError,
            tarfile.TarError) as exc:
        log(t("[!] the update is complete, but reinstalling packages stopped: "
              "{error}. Run this again with --reinstall-only to retry.", error=exc))


def run_reinstall_only(args, profile):
    """--reinstall-only: on an already updated router, only reinstall packages."""
    args.discover = (install.canonical_interface(args.discover)
                     or chain.local_interface(args.host or profile.openwrt_host))
    if args.transport == "auto" and args.discover:
        args.transport = install.detect_transport(args.discover)
    outdir = open_outdir(args, "reinstall")
    require_tools_and_login(args)
    gateway_ok = gateway_allowed(args) and bool(args.discover)
    host = args.host or install.wait_and_discover(
        args.discover, deadline_s=120,
        fallback=profile.openwrt_host if args.transport == "wired" else None,
        router_gateway_ok=gateway_ok)
    board, rootfs = identify(host, profile, args.discover if gateway_ok else None)
    if rootfs != "overlay":
        raise Abort(f"rootfs_type is {rootfs!r}, not the installed system; "
                    "--reinstall-only works on an installed, updated router")
    require_permanent_layout(host, profile, board, rootfs)
    log(f"[*] {host}: {board}, the installed system")
    reinstall_packages(host, outdir, assume_yes=args.yes)
    return 0


def run(args):
    undo = CarryoverUndo()
    try:
        return _run(args, undo)
    except BaseException:
        # A decline, a rejected image, Ctrl-C: whatever stopped the update
        # before it wrote the new release also takes the carry-over back.
        undo.revert()
        raise


def _run(args, undo):
    os.umask(0o077)
    i18n.set_language(args.lang)
    i18n.tolerate_console_encoding()
    profile = devices.get_profile(args.device)
    args.image = {"default": "standard", "nss": "nss"}[args.flavour]
    check_key_options(args)
    if args.reinstall_only:
        return run_reinstall_only(args, profile)

    if args.config_backup:
        args.config_backup = os.path.abspath(args.config_backup)
        check_config_backup(args.config_backup)
    if args.config_backup and not args.keep_settings:
        raise Abort("--config-backup conflicts with --no-keep-settings")

    # Which RAM installer to write depends on the link: the wired one has no
    # radios and would strand a Wi-Fi laptop. So neither may be guessed.
    args.discover = (install.canonical_interface(args.discover)
                     or chain.local_interface(args.host or profile.openwrt_host))
    args.verify_interface = install.canonical_interface(args.verify_interface)
    if args.transport == "auto":
        if args.discover:
            args.transport = install.detect_transport(args.discover)
        elif args.dry_run:
            args.transport = "wired"
            log("[*] no route to the router during dry-run; verifying wired images")
    if not args.dry_run and not (args.discover and args.transport != "auto"):
        raise Abort(t("cannot determine the router-facing interface; pass "
                      "--interface IFACE"))
    args.verify_interface = args.verify_interface or args.discover
    wifi = args.transport == "wifi"
    log(f"[*] transport: {args.transport}"
        + (f" via {args.discover}" if args.discover else ""))

    outdir = open_outdir(args, "update")

    rel = release.resolve(args.tag, profile, args.images, offline=args.offline,
                          log=log)
    log(f"[*] target release {rel.tag} ({rel.published})")
    if wifi and "-wifi" not in " ".join(rel.assets):
        raise Abort(f"{rel.tag} ships no Wi-Fi RAM installer; use a release with "
                    "one, or connect a cable")
    images = release.get_images(
        rel, args.images, args.flavour,
        ("initramfs_ubi", "initramfs_itb", "sysupgrade"), wifi=wifi)
    if not all(item.get("verified") for item in images.values()):
        raise Abort("the image set lacks trusted SHA-256 values")
    ubi, itb = images["initramfs_ubi"]["path"], images["initramfs_itb"]["path"]
    blob, _md5 = install.expected_volume(ubi, itb)
    log(f"[*] images verified; RAM installer {len(blob)} B "
        f"({'with' if wifi else 'without'} radios)")

    if args.dry_run:
        log(t("[dry-run] router was not contacted"))
        return 0

    require_tools_and_login(args)
    fallback = profile.openwrt_host if args.transport == "wired" else None
    gateway_ok = gateway_allowed(args)
    host = args.host or install.wait_and_discover(
        args.discover, deadline_s=120, fallback=fallback,
        router_gateway_ok=gateway_ok)

    rootfs, route, installed = preflight(host, profile, rel, images, args.images,
                              router_iface=args.discover if gateway_ok else None,
                              allow_in_place=not args.via_ram_installer)
    installed_host = host
    if args.ssh_key_auth and route == "ram":
        log(t("[!] this update goes through the RAM installer, which has no copy "
              "of your SSH key. It is reached with its empty root password, as "
              "without --ssh-key-auth (so sshpass is needed); the final check "
              "logs in to {host} by key again.", host=installed_host))
        install.require_host_tools()
    verify_transport = install.detect_transport(args.verify_interface)
    args.wifi_ssid = permanent_ssid(host, args, verify_transport, rootfs,
                                    profile)

    if rootfs == "tmpfs":
        # The config of a RAM system is the installer's defaults. Backing that up
        # and restoring it would silently wipe the owner's settings.
        log("[*] already in the RAM installer -- continuing an interrupted update")
        if args.keep_settings and not args.config_backup:
            raise Abort(t(
                "this is the RAM installer, so its config is not yours. Pass "
                "--config-backup with the config-backup.tar.gz the earlier run "
                "saved, or --no-keep-settings to install clean."))
        config_tar = args.config_backup if args.keep_settings else None
    else:
        if args.config_backup:
            config_tar = args.config_backup
            log(f"[*] restoring the given config backup: {config_tar}")
            log("[c] the extra files and package list are whatever that archive "
                "holds; nothing is added to it")
            backup_config(host, outdir)      # still take one of what is there now
        else:
            if args.keep_settings:
                prepare_carryover(host, args, outdir, undo)
            config_tar = backup_config(host, outdir)
        if not args.keep_settings:
            config_tar = None
            log("[*] clean install: settings will be reset to defaults; a backup "
                f"of the current ones is in {outdir}")

    # Under --ssh-key-auth the final check logs in by key, so this does not apply.
    if config_tar and not args.ssh_key_auth and turns_off_password_login(config_tar):
        log(t("[!] this config turns off password login, and the final check logs "
              "in by password. It will report that it could not verify the router "
              "even when the update worked: when it starts waiting, check with your "
              "SSH key instead."))

    manifest = os.path.join(outdir, "resume.json")
    install.write_resume_manifest(manifest, args, profile, rel, images,
                                  secrets.token_hex(16), config_tar)

    if route == "in-place":
        found = flash_in_place(host, rel, images, args, profile, manifest,
                               verify_transport, installed, undo=undo)
        log(t("[+] update complete: your settings were restored." if config_tar
              else "[+] update complete: the new release is running with default settings."))
        finish_packages(found or host, args, outdir, config_tar)
        return 0

    if rootfs != "tmpfs":
        log(t("\n=== stage 2: pivot into the RAM installer ==="))
        log(t("[2] there is no A/B fallback on an installed system: this overwrites "
              "the installed kernel. Leave the power alone from here."))
        if wifi:
            log(t("[2] when this network disappears, join {ssid!r} with key {key!r}; "
                  "the RAM system has a passwordless root account, so keep the link "
                  "isolated",
                  ssid=profile.installer_wifi_ssid, key=profile.installer_wifi_key))
        install.confirm(t(
            "overwrite ubi_kernel with {image} and reboot? From here the only way "
            "out is finishing the update, or UART.",
            image=os.path.basename(ubi)), args.yes)
        try:
            host = revert.pivot_to_ram(
                host, ubi, itb, args.discover, fallback=fallback,
                recover_stale_lock=args.recover_stale_lock) or host
            undo.commit()
        except Exception as exc:            # noqa: BLE001 -- tagged by the pivot
            # What to say depends on how far the write got, and the exception
            # class cannot express that: pivot_to_ram tags the stage instead.
            # Catching Exception rather than (Abort, RestoreError) keeps a
            # transport timeout raised mid-write from escaping with no advice.
            stage = getattr(exc, "pivot_stage", None)
            if stage in (None, "write-unconfirmed", "rebooted"):
                # Written, or not known: the carry-over is left as it is.
                undo.commit()
            if stage is None:
                raise
            if stage == "write-unconfirmed":
                # ubiformat was issued and its result is not known: the
                # partition that held the installed kernel is not trustworthy,
                # so the router must not be rebooted and the write retried now.
                raise Abort(f"{exc}\n{retry_hint(config_tar)}") from exc
            if stage == "rebooted":
                # The write verified and the box rebooted; it is reached over
                # the network from the RAM installer instead.
                raise Abort(f"{exc}\n{resume_hint(config_tar)}") from exc
            # Nothing was written. The router is still the installed system and
            # still bootable, so neither hint applies and saying either would
            # be a false claim about the state of ubi_kernel.
            raise Abort(str(exc)) from exc
        try:
            host = wait_for_ram_system(host, args.discover, fallback)
        except Abort as exc:
            raise Abort(f"{exc}\n{resume_hint(config_tar)}") from exc
    install.update_resume_phase(manifest, "pivot-complete")

    # Under key auth the permanent system is verified where it was before:
    # known_hosts vouches for that address, not for one discovery turns up.
    # The kept configuration keeps that address, and its host key.
    found = install.flash(
        host, rel, images, args.yes, profile, args.images, config_tar, manifest,
        args.transport, args.verify_interface, args.wifi_ssid,
        profile.openwrt_host if verify_transport == "wired" else None,
        verify_transport,
        router_iface=args.discover if args.transport == "wired" else None,
        expected_revision=release.revision(rel, args.images),
        verify_host=installed_host if args.ssh_key_auth else None,
        verify_host_iface=args.discover if args.ssh_key_auth else None)
    log(t("[+] update complete: your settings were restored." if config_tar
          else "[+] update complete: the new release is running with default settings."))
    finish_packages(found, args, outdir, config_tar)
    return 0


def parse_args(argv=None):
    ap = argparse.ArgumentParser(
        description="Update an installed OpenWrt to another release over SSH, "
                    "without UART.")
    ap.add_argument("--release", "--tag", dest="tag", default=None,
                    help="target release tag or 'latest' (default: latest)")
    ap.add_argument("--interface", "--discover", dest="discover", metavar="IFACE",
                    help="router-facing interface (normally detected)")
    ap.add_argument("--transport", choices=("auto", "wired", "wifi"),
                    default="auto", help="connection type (default: detect)")
    ap.add_argument("--host", default=None,
                    help="the router, preferably fe80::...%%iface (default: "
                         "find it on the interface)")
    ap.add_argument("--root-password", default=None,
                    help="current root password, if one is set")
    ap.add_argument("--verify-interface", metavar="IFACE",
                    help="interface for the final check (default: --interface)")
    ap.add_argument("--wifi-ssid", default=None,
                    help="network to rejoin after the update, when the final "
                         "check is over Wi-Fi (default: read from the router)")
    ap.add_argument("--no-keep-settings", dest="keep_settings",
                    action="store_false", default=True,
                    help="install clean instead of restoring the config; a "
                         "backup of it is still saved here")
    ap.add_argument("--config-backup", metavar="TAR_GZ", default=None,
                    help="restore this sysupgrade -b archive; needed to continue "
                         "from the RAM installer")
    ap.add_argument("--dry-run", action="store_true",
                    help="download and verify the images; do not contact the "
                         "router")
    ap.add_argument("--yes", action="store_true",
                    help="skip the confirmation prompts")
    ap.add_argument("--offline", action="store_true",
                    help="use the verified image cache; needs an explicit tag")
    ap.add_argument("--flavour", choices=("default", "nss"), default="default",
                    help="image flavour for v1.x releases (v2 ships one image)")
    ap.add_argument("--via-ram-installer", action="store_true",
                    help="update through the RAM installer, which reformats both "
                         "UBI partitions (always used from v1.x)")
    ap.add_argument("--ssh-key-auth", action="store_true",
                    help="log in to the installed system with your OpenSSH keys "
                         "and ~/.ssh/config; never a password. The host key must "
                         "already be in known_hosts")
    ap.add_argument("--ssh-identity", metavar="PATH", default=None,
                    help="with --ssh-key-auth: use only this private key "
                         "(-i PATH, IdentitiesOnly=yes)")
    ap.add_argument("--ssh-alias", metavar="NAME", default=None,
                    help="with --ssh-key-auth: connect ssh/scp to this ~/.ssh/config "
                         "Host; --host stays the numeric address for the network "
                         "checks")
    ap.add_argument("--extra-keep", metavar="PATH", action="append", default=[],
                    help="also add PATH to /etc/sysupgrade.conf before the backup "
                         "(repeatable)")
    ap.add_argument("--auto-keep", action="store_true",
                    help="also look for files that no package owns and sysupgrade "
                         "would drop, and offer to keep them (slow on a big overlay)")
    ap.add_argument("--no-reinstall", action="store_true",
                    help="do not reinstall your packages after the update")
    ap.add_argument("--reinstall-only", action="store_true",
                    help="only reinstall the packages listed in "
                         "/etc/backup/world-old.txt on an already updated router")
    ap.add_argument("--recover-stale-lock", action="store_true",
                    help="clear an inactive stale writer lock after inspection")
    ap.add_argument("--device", default="rd03v2", choices=tuple(devices.PROFILES),
                    help="device profile")
    ap.add_argument("--images", default="images", help="image cache directory")
    ap.add_argument("--outdir", default=None, help="run output directory")
    ap.add_argument("--lang", choices=i18n.LANGUAGES, default="auto",
                    help="language for prompts and instructions (default: system)")
    args = install.absorb_secret_env(ap.parse_args(argv))
    args.tag = args.tag or devices.get_profile(args.device).default_release
    return args


def main(argv=None):
    return run(parse_args(argv))


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (Abort, ChainError, release.ReleaseError, restore.RestoreError,
            subprocess.TimeoutExpired) as e:
        log(f"[-] {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        log("\n[*] interrupted")
        sys.exit(130)
