#!/usr/bin/env python3
"""One command: an RD03v2 running OpenWrt -> pristine stock MiWiFi 2.0.28.

`restore.py` does the actual restoring, but it can only run from the RAM
initramfs -- it erases `rootfs`, which on an installed system is the partition
the running OpenWrt is mounted from. Getting there is a pivot: write the RAM
image into the (runtime-unattached) `ubi_kernel` partition and reboot.

That pivot was a dozen shell commands with a read-back check in the middle,
which is exactly the sort of thing that gets typed wrong once. This does the
whole sequence and refuses to continue at each point where continuing blind
would be a bad idea:

    1. find the box, and prove it is the box (dropbear, board_name)
    2. if it is running from NAND, pivot it into RAM
         upload -> md5 on the device -> ubiformat detached -> read-back
         compared byte-for-byte -> reboot -> wait for tmpfs
    3. restore stock from Xiaomi's own signed image
    4. wait for stock and confirm it came up at factory defaults

Already in RAM? Step 2 is skipped. Interrupted halfway? Re-run it -- every
step re-checks the state it needs rather than assuming the previous run got
there.

Usage:
    python3 revert.py --device rd03v2 miwifi_rd03v2_2.0.28.bin --discover enx0 \\
        --root-password hunter2
    python3 revert.py --device rd03v2 miwifi_rd03v2_2.0.28.bin \\
        --host fe80::...%eth0 --dry-run
"""

import argparse
import glob
import hashlib
import os
import sys
import time

import devices
import install
import restore
from chain import log


def find_ram_images(images_dir, release_tag=devices.RD03V2.default_release):
    """The RAM image to pivot through, and the .itb it wraps for verification.

    Prefers the -wifi variant: it is the one that beacons, so if the cable
    ever comes out mid-revert there is still a way back in.
    """
    roots = (os.path.join(images_dir, release_tag), images_dir)
    ubi = []
    for root in roots:
        for pat in ("*initramfs-factory-wifi.ubi", "*initramfs-factory.ubi"):
            ubi = sorted(glob.glob(os.path.join(root, pat)))
            if ubi:
                break
        if ubi:
            break
    if not ubi:
        raise restore.RestoreError(
            f"no initramfs UBI in {images_dir}/{release_tag}. Fetch it first:\n"
            f"  python3 release.py --device rd03v2 --tag {release_tag} --wifi "
            "--download --dest images")
    itb_pat = ("*initramfs-uImage-wifi.itb" if "-wifi" in os.path.basename(ubi[0])
               else "*initramfs-uImage.itb")
    itb = sorted(glob.glob(os.path.join(root, itb_pat)))
    if not itb:
        raise restore.RestoreError(
            f"no initramfs pair in {images_dir}. Fetch one first:\n"
            f"  python3 release.py --device rd03v2 --tag {release_tag} --wifi "
            "--download --dest images")
    return ubi[0], itb[0]


PIVOT_SCRIPT = """\
#!/bin/sh
[ -e /tmp/pv.lock ] && exit 0
: > /tmp/pv.lock
exec >/tmp/pv.log 2>&1
echo running > /tmp/pv.status
ubiformat /dev/mtd{k} -f /tmp/ini.ubi -y
rc=$?
sync
[ $rc = 0 ] && echo done > /tmp/pv.status || echo "fail:$rc" > /tmp/pv.status
"""


def pivot_to_ram(host, ubi_path, itb_path, iface, dry_run=False):
    """Put the RAM image in `ubi_kernel` and boot it. Returns the new host.

    `ubi_kernel` is not attached while OpenWrt runs -- the kernel was read out
    of it at boot and nothing holds it since -- so overwriting it under the
    running system is safe. What is not safe is rebooting onto a bad write,
    hence the read-back before the reboot.
    """
    mtd = restore.mtd_map(host)
    if "ubi_kernel" not in mtd:
        raise restore.RestoreError("no ubi_kernel partition -- not the OpenWrt layout")
    k = mtd["ubi_kernel"]["index"]

    blob, want_md5 = install.expected_volume(ubi_path, itb_path)
    local_md5 = hashlib.md5(open(ubi_path, "rb").read()).hexdigest()
    log(f"[1] pivot via mtd{k} (ubi_kernel); a correct write reads back "
        f"{len(blob)} B, md5 {want_md5}")
    if dry_run:
        log("[1] [dry-run] stopping before the write")
        return None

    log(f"[1] uploading {os.path.basename(ubi_path)}")
    install.scp_to(host, ubi_path, "/tmp/ini.ubi")
    rc, out = install.ssh(host, "md5sum /tmp/ini.ubi", timeout=300)
    if local_md5 not in out:
        raise restore.RestoreError(f"upload md5 mismatch: {out!r} != {local_md5}")
    log(f"[1] md5 {local_md5} verified on the device")

    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        sh = os.path.join(tmp, "pv.sh")
        with open(sh, "w") as fh:
            fh.write(PIVOT_SCRIPT.format(k=k))
        install.scp_to(host, sh, "/tmp/pv.sh")
    install.ssh(host, "chmod +x /tmp/pv.sh; rm -f /tmp/pv.status /tmp/pv.lock",
                check=False)
    # -x names the script, not /bin/sh: start-stop-daemon -S refuses to start
    # when it matches a running process, and our own session is a /bin/sh.
    rc, out = install.ssh(host, "start-stop-daemon -S -b -x /tmp/pv.sh; echo rc=$?",
                          check=False)
    log(f"[1] launch: {out.strip()}")

    deadline = time.time() + 300
    status = ""
    while time.time() < deadline:
        time.sleep(5)
        _rc, status = install.ssh(host, "cat /tmp/pv.status 2>/dev/null",
                                  check=False)
        if status.startswith(("done", "fail")):
            break
    if not status.startswith("done"):
        _rc, tail = install.ssh(host, "tail -20 /tmp/pv.log", check=False)
        raise restore.RestoreError(f"pivot write did not finish ({status!r}):\n{tail}")
    log("[1] ubiformat done")

    install.ssh(host, "ubidetach -d 9 2>/dev/null; true", check=False)
    rc, out = install.ssh(host, f"ubiattach -m {k} -d 9", timeout=120, check=False)
    if rc != 0:
        raise restore.RestoreError(f"the written UBI will not attach: {out!r}")
    _rc, got = install.ssh(host, "md5sum /dev/ubi9_0", timeout=300)
    install.ssh(host, "ubidetach -d 9", check=False)
    got_md5 = got.split()[0] if got else ""
    log(f"[1] read-back md5 {got_md5}")
    if got_md5 != want_md5:
        raise restore.RestoreError(
            f"read-back {got_md5} != expected {want_md5}. Do NOT reboot: the "
            "boot flags still point at the installed system, so it is still "
            "bootable. Re-run to retry the write.")
    log("[1] read-back matches the image byte for byte")

    install.ssh(host, "start-stop-daemon -S -b -x /sbin/reboot", check=False)
    log("[1] rebooting into the RAM initramfs")
    time.sleep(15)
    return install.wait_and_discover(iface) if iface else host


def state_of(host, profile):
    """(board, rootfs_type) -- and refuse anything that is not this board."""
    _rc, board = restore.ssh(host, ". /lib/functions.sh 2>/dev/null; board_name")
    if board.strip() != profile.openwrt_board:
        raise restore.RestoreError(
            f"board_name is {board.strip()!r}, not {profile.openwrt_board} -- "
            f"this is not {profile.display_name} running its supported port")
    _rc, rtype = restore.ssh(host, ". /lib/upgrade/common.sh 2>/dev/null; rootfs_type")
    return board.strip(), rtype.strip()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    devices.add_device_argument(ap)
    ap.add_argument("image", help="official stock firmware image")
    ap.add_argument("--host", default=None, help="fe80::...%%iface of the box")
    ap.add_argument("--discover", metavar="IFACE", default=None,
                    help="find the box on IFACE (and again after the pivot)")
    ap.add_argument("--images", default="images",
                    help="directory holding the initramfs pair")
    ap.add_argument("--release", default=devices.RD03V2.default_release,
                    help="release tag used for the cached RAM image")
    ap.add_argument("--root-password", default=None,
                    help="the installed system's root password, if one is set")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--yes", action="store_true")
    args = ap.parse_args()
    profile = devices.get_profile(args.device)

    # Validate the stock image before touching the device: if it is not a
    # restorable image there is no point pivoting anything.
    payload, _digest, known = restore.carve(args.image, profile)
    restore.inspect(payload, profile)
    if not known:
        log("[!] this image's hash is not one the port publishes")
        if not args.yes:
            raise restore.RestoreError("refusing an unrecognised image without --yes")

    if args.root_password:
        install.SSH_PASSWORDS.append(args.root_password)
        restore.SSH_PASSWORDS[:] = install.SSH_PASSWORDS

    host = args.host
    if args.discover:
        host = install.wait_and_discover(args.discover, deadline_s=120)
    if not host:
        raise restore.RestoreError("give --host or --discover")

    install.check_is_openwrt_ram(host)      # refuses this host's own gateway
    board, rtype = state_of(host, profile)
    log(f"[*] {host}: {board}, rootfs_type={rtype}")

    if rtype != "tmpfs":
        log("\n=== step 1: pivot into the RAM initramfs ===")
        ubi, itb = find_ram_images(args.images, args.release)
        if not args.yes and not args.dry_run:
            if input(f"[?] write {os.path.basename(ubi)} to ubi_kernel and "
                     "reboot? [type YES] ").strip() != "YES":
                raise restore.RestoreError("declined")
        newhost = pivot_to_ram(host, ubi, itb, args.discover, args.dry_run)
        if args.dry_run:
            log("\n[dry-run] would restore stock next; stopping.")
            return 0
        host = newhost or host
        _board, rtype = state_of(host, profile)
        if rtype != "tmpfs":
            raise restore.RestoreError(
                f"after the pivot the box is still running from {rtype!r}. It "
                "did not boot the RAM image; nothing has been erased.")
        log(f"[1] in RAM at {host}")
    else:
        log("[*] already running from RAM -- skipping the pivot")

    log("\n=== step 2: restore stock ===")
    mtd = restore.check_target(host, profile)
    plan = restore.plan(mtd, profile)
    if args.dry_run:
        log("[dry-run] stopping before the restore write.")
        return 0
    if not args.yes:
        if input("[?] erase OpenWrt and write stock? [type RESTORE] ").strip() \
                != "RESTORE":
            raise restore.RestoreError("declined")
    restore.do_restore(host, payload, restore.img_volumes(payload, profile), plan,
                       profile)

    log("\n=== step 3: wait for stock ===")
    import chain
    deadline = time.time() + 420
    while time.time() < deadline:
        time.sleep(8)
        try:
            info = chain.init_info(profile.stock_host)
        except Exception:                                        # noqa: BLE001
            continue
        log(f"[+] stock is up: hardware={info.get('hardware')} "
            f"rom={info.get('romversion')} inited={info.get('inited')}")
        if info.get("inited") == 0:
            log("[+] factory defaults -- the setup wizard is waiting")
        return 0
    log(f"[!] stock did not answer on {profile.stock_host} within 7 minutes. It may "
        "still be booting; check that this host has an address on that subnet.")
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except restore.RestoreError as e:
        log(f"[-] {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        log("\n[*] interrupted")
        sys.exit(130)
