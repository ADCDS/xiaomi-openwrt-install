#!/usr/bin/env python3
"""Update an already-installed OpenWrt build to a new release, without UART.

This is the scripted form of docs/no-uart-reflash.md.  Unlike install.py it
does *not* use the cab_meshd exploit: the box is already OpenWrt and already
answers SSH as root.  The route is the sanctioned one:

    1. preflight  -- confirm SSH root, locate the idle `ubi_kernel` MTD, and
                     measure free memory.
    2. backup     -- `sysupgrade -b` the running config and copy it off-box
                     (optionally dump the current kernel volume as rollback).
    3. write-ram  -- ubiformat the release's initramfs-factory(.ubi) into the
                     unattached `ubi_kernel` MTD, verify it by read-back
                     (prefix md5 + erase-padding tail), set the boot flags the
                     sanctioned path sets, and reboot into the RAM installer.
    4. flash      -- from the RAM system (rootfs is tmpfs now), check memory,
                     upload the sysupgrade image, `sysupgrade -T`, then
                     `sysupgrade -f backup new.bin`.
    5. verify     -- rediscover and prove the permanent system on NAND.

Only the two writes (the ubiformat in 3, the sysupgrade in 4) are risk
windows; both leave the box recoverable over UART.  Everything that must
outlive an SSH session is detached with `setsid` (see the gotchas in the doc).

install.py already carries battle-tested helpers for every network step --
ssh(), scp_to(), wait_and_discover(), wait_for_openwrt(),
verify_permanent_system(), parse_mtd(), detect_transport() -- so this module
imports and reuses them rather than reimplementing them.  Importing install is
side-effect free (everything runs under its __main__ guard).
"""

import argparse
import os
import shlex
import sys
import time

import chain
import devices
import install
import release
from chain import ChainError
from install import Abort, log


# The RAM installer brings up this network on a Wi-Fi update; its address and
# key are fixed and public and the network exists only while it runs.
RAM_HOST = "192.168.1.1"


# ---- detached helpers -------------------------------------------------------

# A plain `cmd &` over dropbear can die with the session, and this BusyBox has
# no nohup; setsid is the documented replacement.  We always write the command
# into a uniquely named script and confirm its effect rather than trusting the
# exit status (see the start-stop-daemon pitfall in the doc).
def _detach(ch_host, script_body, name):
    """Write /tmp/<name>.sh on the box and launch it detached with setsid."""
    path = f"/tmp/{name}.sh"
    heredoc = f"cat > {path} <<'RD03V2_EOF'\n{script_body}\nRD03V2_EOF\n" \
              f"chmod +x {path}"
    install.ssh(ch_host, heredoc)
    install.ssh(ch_host,
                f"setsid {path} </dev/null >/dev/null 2>&1 &",
                check=False)


def scp_from(host, remote, local, passwords=None):
    """Copy remote:path off the box.  install.py only ships scp_to."""
    import shutil
    import subprocess
    src = f"root@[{host}]:{remote}" if ":" in host else f"root@{host}:{remote}"
    last = None
    for pw in (install.SSH_PASSWORDS if passwords is None else passwords):
        cmd = (["sshpass", "-p", pw] if shutil.which("sshpass") else []) + [
            "scp", "-O", "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR",
            "-o", "NumberOfPasswordPrompts=1", src, local]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        if r.returncode == 0:
            return
        last = r.stderr.strip()
    raise Abort(f"scp from {remote} failed: {last}")


def _free_memory_kb(host):
    rc, out = install.ssh(
        host, "grep MemAvailable /proc/meminfo", check=False)
    if rc != 0:
        return None
    for tok in out.split():
        if tok.isdigit():
            return int(tok)
    return None


def _find_ubi_kernel_mtd(host):
    """Return the mtd index of the unattached kernel partition, or raise.

    docs/no-uart-reflash.md: `ubi_kernel` is a separate MTD the running system
    never attaches.  Different builds have labelled it `ubi_kernel` or
    `kernel`; accept either, preferring the exact profile name.
    """
    rc, text = install.ssh(host, "cat /proc/mtd", check=False)
    if rc != 0:
        raise Abort(f"cannot read /proc/mtd on {host}: {text}")
    mtd = install.parse_mtd(text)
    for name in ("ubi_kernel", "kernel"):
        if name in mtd:
            return mtd[name]["index"], name, mtd
    raise Abort(
        "no 'ubi_kernel'/'kernel' MTD on the box; /proc/mtd was:\n" + text)


# ---- stages -----------------------------------------------------------------

def preflight(host, profile):
    log("\n=== stage 1: pre-flight (nothing is written) ===")
    rc, board = install.ssh(
        host, ". /lib/functions.sh 2>/dev/null; board_name", check=False)
    if rc == 0 and board.strip():
        log(f"[1] board_name = {board.strip()!r}")
        if board.strip() != profile.openwrt_board:
            raise Abort(
                f"board {board.strip()!r} is not {profile.openwrt_board!r}; "
                "this updater only drives the reviewed profile")
    rc, rel = install.ssh(host, "cat /etc/openwrt_release 2>/dev/null | "
                                "grep DISTRIB_DESCRIPTION", check=False)
    if rc == 0 and rel.strip():
        log(f"[1] running: {rel.strip()}")
    idx, label, _mtd = _find_ubi_kernel_mtd(host)
    log(f"[1] idle kernel partition: mtd{idx} ({label}) -- never attached at "
        "runtime, safe to reformat")
    avail = _free_memory_kb(host)
    if avail is not None:
        log(f"[1] MemAvailable = {avail} KiB")
    log("[1] pre-flight passed")
    return idx


def backup(host, outdir, dump_kernel_mtd=None):
    log("\n=== stage 2: backup ===")
    install.ssh(host, "sysupgrade -b /tmp/config-backup.tar.gz")
    local_cfg = os.path.join(outdir, "config-backup.tar.gz")
    # Copy it off-box; /tmp does not survive the reboot.
    scp_from(host, "/tmp/config-backup.tar.gz", local_cfg)
    log(f"[b] config backup -> {local_cfg}")
    if dump_kernel_mtd is not None:
        # Optional rollback artifact: read the kernel volume back.  Best effort.
        remote = "/tmp/old-kernel.bin"
        rc, _ = install.ssh(
            host,
            f"ubidetach -d 9 >/dev/null 2>&1; "
            f"ubiattach -m {dump_kernel_mtd} -d 9 >/dev/null 2>&1; "
            f"dd if=/dev/ubi9_0 of={remote} bs=128k 2>/dev/null; "
            f"ubidetach -d 9 >/dev/null 2>&1; echo done",
            check=False)
        if rc == 0:
            try:
                scp_from(host, remote,
                                 os.path.join(outdir, "old-kernel.bin"))
                log(f"[b] rollback kernel dump -> {outdir}/old-kernel.bin")
            except Abort:
                log("[b] (kernel rollback dump skipped: copy failed)")
    return local_cfg


BOOT_FLAGS = (
    ("boot_wait", "on"), ("uart_en", "1"), ("flag_boot_rootfs", "0"),
    ("flag_last_success", "1"), ("flag_boot_success", "1"),
    ("flag_try_sys1_failed", "8"), ("flag_try_sys2_failed", "8"),
)


def write_ram_installer(host, mtd_idx, ubi_path, itb_path, assume_yes):
    log("\n=== stage 3: write RAM installer (first risk window) ===")
    remote_ubi = "/tmp/initramfs-factory.ubi"
    log(f"[3] uploading {os.path.basename(ubi_path)} -> {remote_ubi}")
    install.scp_to(host, ubi_path, remote_ubi)

    install.confirm(
        f"ubiformat mtd{mtd_idx} with the RAM installer now? (recoverable over "
        "UART if it tears)", assume_yes)

    # Detach the write so it cannot die with the SSH session.
    script = (
        "#!/bin/sh\n"
        "exec >/tmp/rd03v2-ubiformat.log 2>&1\n"
        "echo running > /tmp/rd03v2-ubiformat.status\n"
        f"ubiformat /dev/mtd{mtd_idx} -f {remote_ubi} -y\n"
        "rc=$?\n"
        "sync\n"
        "if [ $rc = 0 ]; then echo done > /tmp/rd03v2-ubiformat.status\n"
        "else echo fail:$rc > /tmp/rd03v2-ubiformat.status; fi\n"
    )
    _detach(host, script, "rd03v2-ubiformat")
    log("[3] ubiformat launched detached; waiting for it to finish")

    deadline = time.time() + 300
    status = ""
    while time.time() < deadline:
        time.sleep(3)
        rc, status = install.ssh(
            host, "cat /tmp/rd03v2-ubiformat.status 2>/dev/null", check=False)
        status = status.strip()
        if status == "done":
            break
        if status.startswith("fail:"):
            raise Abort(f"ubiformat failed on the box ({status}); the box is "
                        "still bootable from its current kernel")
    if status != "done":
        raise Abort("ubiformat did not report completion within 300s; inspect "
                    "/tmp/rd03v2-ubiformat.log over SSH before retrying")
    log("[3] ubiformat reported done")

    _verify_readback(host, mtd_idx, itb_path)
    _set_boot_flags(host)

    install.confirm("reboot into the RAM installer now?", assume_yes)
    reboot = "#!/bin/sh\nsleep 2\nreboot\n"
    _detach(host, reboot, "rd03v2-reboot")
    log("[3] reboot launched detached; the box is coming up in the RAM "
        "installer (default config: static 192.168.1.1, DHCP, NSS off)")


def _verify_readback(host, mtd_idx, itb_path):
    """Read the kernel volume back and prove the .itb is a byte-exact prefix.

    The artifact's single volume is dynamic: it reads back the FIT image
    followed by 0xff erase padding, so a whole-volume md5 can never match the
    .itb.  Check the prefix md5 and that the tail is nothing but 0xff.
    """
    itb_size = os.path.getsize(itb_path)
    want_md5 = _md5_local(itb_path)
    log(f"[3] verifying read-back (.itb is {itb_size} B, must be an exact "
        "prefix of the kernel volume)")
    # Attach the kernel MTD on a fixed, high device number: the rootfs UBI is
    # already attached as ubi0 while we are still on the installed system, so
    # we must not pick that one up.
    dev = "/dev/ubi9_0"
    cmd = (
        f"ubidetach -d 9 >/dev/null 2>&1; "
        f"ubiattach -m {mtd_idx} -d 9 >/dev/null 2>&1; "
        f"head -c {itb_size} {dev} | md5sum | cut -d' ' -f1; "
        f"tail -c +{itb_size + 1} {dev} | tr -d '\\377' | wc -c; "
        f"ubidetach -d 9 >/dev/null 2>&1; true"
    )
    rc, out = install.ssh(host, cmd, check=False)
    lines = [l.strip() for l in out.splitlines() if l.strip()]
    if rc != 0 or len(lines) < 2:
        raise Abort(f"could not read the kernel volume back for verification: "
                    f"{out}")
    got_md5, tail_nonff = lines[0], lines[-1]
    if got_md5 != want_md5:
        raise Abort(
            "kernel read-back mismatch: the RAM installer was NOT written "
            f"correctly.\n  got md5  {got_md5}\n  want md5 {want_md5}\n"
            "Do not reboot; the box is still bootable from its current kernel.")
    if tail_nonff != "0":
        raise Abort(
            f"kernel read-back has {tail_nonff} non-0xff bytes past the image; "
            "the write is suspect, do not reboot")
    log("[3] read-back verified: prefix md5 matches and the tail is pure erase "
        "padding")


def _md5_local(path):
    import hashlib
    h = hashlib.md5()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _set_boot_flags(host):
    # On an installed OpenWrt the U-Boot environment is written with
    # `fw_setenv` (uboot-envtools), not the stock `nvram` tool.  This is the
    # exact set the community no-UART guide and the sanctioned path use.  If
    # the tool is missing we stop *before* the reboot, while the box is still
    # bootable from its current kernel.
    rc, _ = install.ssh(host, "command -v fw_setenv", check=False)
    if rc != 0:
        raise Abort(
            "fw_setenv is not available on the router (install uboot-envtools: "
            "`opkg update && opkg install uboot-envtools`). Not rebooting; the "
            "box is still bootable from its current kernel.")
    cmd = " && ".join(f"fw_setenv {k} {v}" for k, v in BOOT_FLAGS)
    rc, out = install.ssh(host, cmd, check=False)
    if rc != 0:
        raise Abort(f"could not set the boot flags with fw_setenv ({out}); not "
                    "rebooting")
    log("[3] boot flags set via fw_setenv (boot_wait, flag_last_success, "
        "try-counters, ...)")


def flash(host, sysupgrade_path, config_tar, assume_yes):
    log("\n=== stage 4: flash from the RAM installer (second risk window) ===")
    avail = _free_memory_kb(host)
    if avail is not None and avail < 30000:
        log(f"[4] only {avail} KiB free; stopping non-essential services to "
            "make room for the sysupgrade image")
        install.ssh(
            host,
            "for s in rd03v2-watchdog odhcpd sysntpd uhttpd; do "
            "/etc/init.d/$s stop 2>/dev/null; done; "
            "sync; echo 3 > /proc/sys/vm/drop_caches", check=False)

    remote_img = "/tmp/new.bin"
    log(f"[4] uploading {os.path.basename(sysupgrade_path)} -> {remote_img}")
    install.scp_to(host, sysupgrade_path, remote_img)
    # Confirm the upload byte-for-byte before the write.
    want = _sha256_local(sysupgrade_path)
    rc, got = install.ssh(host, f"sha256sum {remote_img} | cut -d' ' -f1",
                          check=False)
    if rc != 0 or got.strip() != want:
        raise Abort("uploaded sysupgrade image failed its checksum on the box; "
                    "nothing was flashed")
    log("[4] upload sha256 verified on the box")

    # Keep settings exactly like a normal sysupgrade: restore the config
    # backup made in stage 2 (`sysupgrade -f backup`).  When the operator asked
    # for a clean install, reset to defaults instead (`sysupgrade -n`).
    if config_tar is not None:
        remote_cfg = "/tmp/config-backup.tar.gz"
        install.scp_to(host, config_tar, remote_cfg)
        sysupgrade = f"sysupgrade -f {remote_cfg} {remote_img}"
        log("[4] settings will be kept (restoring the stage-2 config backup)")
    else:
        sysupgrade = f"sysupgrade -n {remote_img}"
        log("[4] clean install requested: settings will be reset to defaults "
            "(a config backup was still saved to your computer in stage 2)")

    rc, out = install.ssh(host, f"sysupgrade -T {remote_img}", check=False)
    log(f"[4] sysupgrade -T: {out.strip() or 'ok'}")
    if rc != 0:
        raise Abort("sysupgrade refused the image (platform_check_image); "
                    "nothing was flashed")

    install.confirm(
        "run sysupgrade now? THIS OVERWRITES THE SYSTEM (irreversible from "
        "here; recoverable only over UART)", assume_yes)

    # Detach sysupgrade: it reboots the box, which would kill our session.
    script = (
        "#!/bin/sh\n"
        "sleep 2\n"
        f"{sysupgrade}\n"
    )
    _detach(host, script, "rd03v2-sysupgrade")
    log("[4] sysupgrade launched detached; the box will reformat both UBIs, "
        "write the new kernel and rootfs, restore the config, and reboot")


# ---- driver -----------------------------------------------------------------

def _sha256_local(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def resolve_host(args, profile):
    """Pick the address to talk to the running OpenWrt box on."""
    if args.host:
        return args.host
    if args.transport == "wired" or (
            args.discover and install.detect_transport(args.discover) == "wired"):
        return profile.openwrt_host
    if args.discover:
        # Over Wi-Fi the box is on the user's own network; discover its
        # link-local as a last resort.
        try:
            return install.discover_linklocal(args.discover, timeout=15)
        except Abort:
            pass
    return profile.openwrt_host


def run(args):
    profile = devices.get_profile(args.device)
    wifi = (args.transport == "wifi"
            or (args.discover and not args.no_wifi_initramfs
                and args.transport == "auto"
                and install.detect_transport(args.discover) == "wifi"))

    outdir = os.path.abspath(
        args.outdir or f"update-{time.strftime('%Y%m%d-%H%M%S')}")
    os.makedirs(outdir, mode=0o700, exist_ok=True)
    chain.set_log_sink(open(f"{outdir}/transcript.log", "w"))
    log(f"[*] output -> {outdir}/")

    # Resolve and verify the target release's image set (no router contact).
    log(f"[*] resolving release {args.tag!r} for {profile.slug}")
    rel = release.resolve(args.tag, profile, args.images, offline=args.offline)
    log(f"[*] release {rel.tag} (published {rel.published})")
    kinds = ("initramfs_ubi", "initramfs_itb", "sysupgrade")
    images = release.get_images(rel, args.images, args.flavour, kinds, wifi=wifi)
    ubi = images["initramfs_ubi"]["path"]
    itb = images["initramfs_itb"]["path"]
    sysimg = images["sysupgrade"]["path"]
    log(f"[*] images verified: RAM={os.path.basename(ubi)} "
        f"sys={os.path.basename(sysimg)} ({'Wi-Fi' if wifi else 'wired'} RAM "
        "installer)")

    if args.dry_run:
        log("[+] dry-run: release resolved, images downloaded and verified; "
            "the router was not contacted")
        return 0

    if args.root_password:
        install.SSH_PASSWORDS.append(args.root_password)

    host = resolve_host(args, profile)
    log(f"[*] talking to the running OpenWrt box at {host}")
    rc, _ = install.ssh(host, "echo up", check=False)
    if rc != 0:
        raise Abort(f"cannot SSH into the running OpenWrt box at {host}; pass "
                    "--host or --root-password, or check the cable/network")

    mtd_idx = preflight(host, profile)
    config_tar = backup(host, outdir,
                        dump_kernel_mtd=mtd_idx if args.dump_kernel else None)
    write_ram_installer(host, mtd_idx, ubi, itb, args.yes)

    # Wait for the RAM installer to come up.  Over Wi-Fi the user joins
    # the installer network now; wired keeps the cable.
    if wifi:
        log(f"[*] join the RAM installer network {profile.installer_wifi_ssid!r} "
            f"(key {profile.installer_wifi_key!r}); it appears only while the "
            "RAM installer runs")
    ram_host = RAM_HOST
    if args.discover and not wifi:
        ram_host = install.wait_and_discover(
            args.discover, fallback=RAM_HOST, router_gateway_ok=True) or RAM_HOST
    if not install.wait_for_openwrt(ram_host, router_iface=args.discover,
                                    passwords=install.RAM_PASSWORDS):
        raise Abort("the RAM installer did not come up; the box falls back to "
                    "its current kernel on repeated boot failure")
    log("[*] RAM installer is up (rootfs is tmpfs; sysupgrade is now allowed)")

    # The RAM system has no password; flash() talks to it with RAM_PASSWORDS.
    saved = install.SSH_PASSWORDS[:]
    install.SSH_PASSWORDS[:] = list(install.RAM_PASSWORDS)
    try:
        flash(ram_host, sysimg,
              config_tar if args.keep_settings else None, args.yes)
    finally:
        install.SSH_PASSWORDS[:] = saved

    verify_iface = args.verify_interface or args.discover
    fallback = profile.openwrt_host if not wifi else None
    install.verify_permanent_system(
        profile, interface=verify_iface,
        fallback=fallback,
        host=None if verify_iface else profile.openwrt_host)
    log("[+] update complete: the new OpenWrt release is on NAND and your "
        "configuration was migrated.")
    return 0


def parse_args(argv=None):
    ap = argparse.ArgumentParser(
        description="Update an installed OpenWrt build to a new release over "
                    "SSH, without UART (scripted docs/no-uart-reflash.md).")
    ap.add_argument("--release", "--tag", dest="tag", default=None,
                    help="target release tag or 'latest' (default: latest)")
    ap.add_argument("--interface", "--discover", dest="discover", metavar="IFACE",
                    help="router-facing interface (for rediscovery after reboot)")
    ap.add_argument("--transport", choices=("auto", "wired", "wifi"),
                    default="auto", help="update transport (default: detect)")
    ap.add_argument("--host", default=None,
                    help="running OpenWrt address (default: profile LAN address)")
    ap.add_argument("--root-password", default=None,
                    help="current root password of the running OpenWrt box")
    ap.add_argument("--flavour", choices=("default", "nss"), default="default",
                    help="image flavour for v1.x releases (v2 ships one image)")
    ap.add_argument("--verify-interface", metavar="IFACE",
                    help="interface for permanent-system verification")
    ap.add_argument("--dry-run", action="store_true",
                    help="resolve and verify the image set; do not contact the "
                         "router")
    ap.add_argument("--yes", action="store_true",
                    help="skip the write/reboot confirmation prompts")
    ap.add_argument("--no-keep-settings", dest="keep_settings",
                    action="store_false", default=True,
                    help="do a clean install (sysupgrade -n) instead of "
                         "restoring the config backup; a backup is still saved "
                         "to your computer")
    ap.add_argument("--dump-kernel", action="store_true",
                    help="also dump the current kernel volume as a rollback "
                         "artifact during backup")
    ap.add_argument("--offline", action="store_true",
                    help="use the verified image cache; do not contact GitHub")
    ap.add_argument("--no-wifi-initramfs", action="store_true",
                    help="use the radio-silent RAM installer (wired only)")
    ap.add_argument("--device", default="rd03v2",
                    choices=tuple(devices.PROFILES),
                    help="device profile")
    ap.add_argument("--images", default="images", help="image cache directory")
    ap.add_argument("--outdir", default=None, help="run output directory")
    args = ap.parse_args(argv)
    args.tag = args.tag or devices.get_profile(args.device).default_release
    if args.no_wifi_initramfs and args.transport == "wifi":
        ap.error("--transport wifi conflicts with --no-wifi-initramfs")
    return args


def main(argv=None):
    os.umask(0o077)
    return run(parse_args(argv))


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (Abort, ChainError, release.ReleaseError) as e:
        log(f"[-] {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        log("\n[*] interrupted")
        sys.exit(130)
