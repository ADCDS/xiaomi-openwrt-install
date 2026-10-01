#!/usr/bin/env python3
"""Restore a profile-approved stock image from an OpenWrt RAM system.

For the enabled profile, Xiaomi's own firmware image is already the artifact
needed for restoration: a 756-byte `HDR1` header, then a **complete raw UBI
image**, then a 272-byte RSA signature. That UBI holds exactly the two volumes
stock boots from:

    kernel      dynamic   29 LEBs   3682304 B   FIT (d00dfeed)
    ubi_rootfs  dynamic  149 LEBs  18919424 B   squashfs (hsqs)

For the profile's stock image marked hardware-tested, both volumes are
byte-identical to what was physically on a live unit's stock slot. So no backup
is needed for that validated restore path: the official download *is* the
backup, and it is signed and hash-published.

The signature is only checked by U-Boot's TFTP recovery path.  There is no
secure boot on the kernel, which is precisely why writing the payload straight
into the partition works and needs no cable.

What has to be true afterwards
------------------------------
The stock bootloader survives an OpenWrt install untouched (`0:APPSBL` is far
below anything OpenWrt writes), and it picks its system from
`flag_last_success` -- so restoring is: put stock's UBI back where slot 0 is,
give stock a blank flash everywhere else it expects to own, and point the
chooser at slot 0.

The partition boundaries do not line up, and that is the whole difficulty:

    stock   rootfs   0x0a80000..0x2880000  (30M)  <- slot 0, what we restore
            rootfs_1 0x2880000..0x4680000  (30M)  <- slot 1
            overlay  0x4680000..0x8000000  (57M)  <- cfg / user / plugin
    OpenWrt ubi_kernel 0x0a80000..0x2e80000 (36M)
            rootfs     0x2e80000..0x8000000 (81M)

Stock reads its own layout from MIBIB, which OpenWrt never touches, so once
stock boots it sees the original three partitions again.  But under OpenWrt we
can only address its two, and stock's boundaries fall in their middles.  Hence:
write the stock UBI at the start of `ubi_kernel` (which is exactly stock's
slot 0), then **erase** everything above stock's slot 0.  Erased flash is what
a factory unit's spare slot and data area look like before first boot, and
stock formats its data volumes itself from that state -- which is a state it
is guaranteed to handle, unlike a partition full of another OS's UBI.
"""

import argparse
import hashlib
import os
import shutil
import struct
import subprocess
import sys
import time

import devices
import ubiparse

HDR1_MAGIC = b"HDR1"


class RestoreError(Exception):
    pass


def require_trusted_image(digest, known, expected_digest=None):
    """Require either a profile-approved image or an exact explicit digest."""
    if known:
        return
    expected = (expected_digest or "").strip().lower()
    if expected != digest:
        raise RestoreError(
            f"stock image SHA-256 {digest} is not approved for this profile. "
            "Use a published image, or pass its exact digest with "
            "--expected-image-sha256 for deliberate development testing.")
    print("[!] accepting an unlisted stock image only because its exact "
          "SHA-256 was supplied explicitly")


def carve(path, profile):
    """HDR1 -> the raw UBI payload, checked rather than assumed."""
    d = open(path, "rb").read()
    digest = hashlib.sha256(d).hexdigest()
    known = profile.stock_images.get(digest)
    print(f"[*] {os.path.basename(path)}  {len(d)} B")
    print(f"    sha256 {digest}")
    print(f"    {known if known else '*** NOT a published image hash ***'}")
    if d[:4] != HDR1_MAGIC:
        raise RestoreError(f"not an HDR1 image (starts {d[:4]!r})")
    total = struct.unpack_from("<I", d, 4)[0]
    if total > len(d):
        raise RestoreError(f"HDR1 says {total} B but the file is {len(d)} B")
    payload = d[profile.payload_offset:total]
    if len(payload) % profile.peb_size:
        raise RestoreError(f"payload {len(payload)} B is not a whole number of "
                           f"{profile.peb_size}-byte blocks")
    print(f"    payload {len(payload)} B = {len(payload)//profile.peb_size} PEBs, "
          f"signature trailer {len(d)-total} B")
    return payload, digest, known


def inspect(payload, profile):
    """Confirm the payload really is a stock system before offering to write it."""
    img = ubiparse.UbiImage(payload, peb_size=profile.peb_size)
    if len(img.image_seqs) > 1:
        raise RestoreError("payload spans more than one UBI -- refusing")
    names = {v.name: v for v in img.volumes.values() if v.lebs}
    print(f"    volumes: " + ", ".join(f"{n} ({len(v.lebs)} LEBs)"
                                       for n, v in sorted(names.items())))
    for want, magic, what in (("kernel", bytes.fromhex("d00dfeed"), "a FIT"),
                              ("ubi_rootfs", b"hsqs", "a squashfs")):
        if want not in names:
            raise RestoreError(f"payload has no {want!r} volume -- this is not a "
                               "stock system image")
        head = img.extract(names[want].vol_id)[:4]
        if head != magic:
            raise RestoreError(f"{want} does not start with {what} ({head.hex()})")
    need = len(payload)
    if need > profile.stock_slot0[1]:
        raise RestoreError(f"payload {need} B does not fit stock's slot 0 "
                           f"({profile.stock_slot0[1]} B)")
    print(f"    fits stock slot 0: {need} B of {profile.stock_slot0[1]} B "
          f"({profile.stock_slot0[1]//profile.peb_size} PEBs)")
    return img


# ---- device side ------------------------------------------------------------


# The RAM initramfs has no root password, but an installed system reached on
# the way here may. revert.py points this at install.SSH_PASSWORDS.
SSH_PASSWORDS = [""]


def ssh(host, cmd, timeout=180, check=True):
    last = (1, "")
    for pw in SSH_PASSWORDS:
        base = ["sshpass", "-p", pw] if _have("sshpass") else []
        base += ["ssh", "-o", "StrictHostKeyChecking=no",
                 "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR",
                 "-o", "ConnectTimeout=15", "-o", "PubkeyAuthentication=no",
                 "-o", "NumberOfPasswordPrompts=1", f"root@{host}", cmd]
        r = subprocess.run(base, capture_output=True, text=True, timeout=timeout)
        last = (r.returncode, (r.stdout + r.stderr).strip())
        if r.returncode == 0:
            return last
        if "Permission denied" not in last[1]:
            break
    if check and last[0] != 0:
        raise RestoreError(f"ssh {cmd[:60]!r}: {last[1]}")
    return last


def _have(x):
    # shutil.which, not `command -v` through subprocess: with shell=False that
    # execs bash with "-v" as a *flag*, so it only ever worked by falling
    # through to the /usr/bin check -- and missed sshpass installed anywhere
    # else on PATH.
    return shutil.which(x) is not None


def mtd_map(host):
    _rc, out = ssh(host, "cat /proc/mtd")
    m = {}
    for line in out.splitlines():
        p = line.split()
        if len(p) == 4 and p[0].startswith("mtd"):
            m[p[3].strip('"')] = {"index": int(p[0][3:].rstrip(":")),
                                  "size": int(p[1], 16)}
    return m


def check_target(host, profile):
    """Require the selected board running OpenWrt *from RAM*.

    This has to run from the initramfs, for the same reason the install does:
    it erases `rootfs`, and on an installed system that is the partition the
    running OpenWrt is mounted from. Erasing it under a live root does not
    fail cleanly -- it takes the machine down mid-restore, before the boot
    flags have been moved, which is the worst possible moment.

    Getting there from an installed OpenWrt is the documented no-UART pivot:
    write `initramfs-factory-wifi.ubi` into `ubi_kernel` and reboot. With the
    -wifi image that step needs no cable either.
    """
    _rc, board = ssh(host, ". /lib/functions.sh 2>/dev/null; board_name")
    if board.strip() != profile.openwrt_board:
        raise RestoreError(
            f"board_name is {board.strip()!r}, not {profile.openwrt_board}")
    _rc, rtype = ssh(host, ". /lib/upgrade/common.sh 2>/dev/null; rootfs_type")
    if rtype.strip() != "tmpfs":
        raise RestoreError(
            f"rootfs_type is {rtype.strip()!r}, not tmpfs. This restore erases "
            "the rootfs partition, so it must run from the RAM initramfs -- "
            "running it from the installed system would kill the machine "
            "mid-write. Use revert.py to perform the verified RAM pivot, then "
            "re-run only if you are deliberately driving restore.py directly.")
    mtd = mtd_map(host)
    for name, (_start, size) in profile.openwrt_partitions.items():
        if name not in mtd:
            raise RestoreError(f"no {name!r} partition -- not the OpenWrt layout")
        if mtd[name]["size"] != size:
            raise RestoreError(f"{name} is {mtd[name]['size']} B, expected {size}")
    print(f"[*] target is {board.strip()}, "
          f"ubi_kernel=mtd{mtd['ubi_kernel']['index']} "
          f"rootfs=mtd{mtd['rootfs']['index']}")
    return mtd


def plan(mtd, profile):
    """What gets written and what gets erased, in absolute offsets."""
    k, r = mtd["ubi_kernel"]["index"], mtd["rootfs"]["index"]
    # Everything above stock's slot 0 must look erased to stock: the tail of
    # ubi_kernel (stock's slot 1 head) and the whole of OpenWrt's rootfs
    # (stock's slot 1 tail + all of overlay).
    ubi_kernel = profile.openwrt_partitions["ubi_kernel"]
    rootfs = profile.openwrt_partitions["rootfs"]
    tail_off = profile.stock_slot0[0] + profile.stock_slot0[1] - ubi_kernel[0]
    tail_len = ubi_kernel[1] - tail_off
    return {
        "write": (k, "ubi_kernel"),
        "erase_tail": (k, tail_off, tail_len // profile.peb_size),
        "erase_rootfs": (r, 0, rootfs[1] // profile.peb_size),
    }


RESTORE_SCRIPT = """\
#!/bin/sh
if ! mkdir /tmp/restore.lock 2>/dev/null; then
    echo fail:locked > /tmp/restore.status
    exit 1
fi
echo $$ > /tmp/restore.lock/pid
trap 'rm -rf /tmp/restore.lock' EXIT
exec >/tmp/restore.log 2>&1
set -x
echo running > /tmp/restore.status
ubiformat /dev/mtd{k} -f /tmp/stock.ubi -y || {{ echo "fail:ubiformat" > /tmp/restore.status; exit 1; }}
# Stock's data area goes back to erased -- the state a factory unit is in
# before its first boot, which stock formats for itself.
#
# `flash_erase` is absent from both the installed and the RAM OpenWrt images
# here, and `mtd` has no offset/length option, so this erases whole devices
# only. That leaves ubiformat's EC headers sitting in the first 48 PEBs of
# stock's slot 1 (the tail of ubi_kernel). Harmless: slot 1 is only ever
# consulted if slot 0's failure counter passes 5, and we reset both to 0.
mtd erase /dev/mtd{r} || {{ echo "fail:erase-rootfs" > /tmp/restore.status; exit 1; }}
sync
echo done > /tmp/restore.status
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    devices.add_device_argument(ap)
    ap.add_argument("image", help="official stock firmware image")
    ap.add_argument("--host", default=None,
                    help="the OpenWrt system; prefer fe80::...%%iface")
    ap.add_argument("--discover", metavar="IFACE", default=None)
    ap.add_argument("--dry-run", action="store_true",
                    help="carve, verify and print the plan; touch nothing")
    ap.add_argument("--yes", action="store_true")
    ap.add_argument("--expected-image-sha256", default=None,
                    help="explicit digest for an unlisted development image")
    ap.add_argument("--recover-stale-lock", action="store_true",
                    help="clear an inactive stale writer lock after inspection")
    args = ap.parse_args()
    profile = devices.get_profile(args.device)

    payload, digest, known = carve(args.image, profile)
    inspect(payload, profile)
    require_trusted_image(digest, known, args.expected_image_sha256)

    if args.dry_run and not (args.host or args.discover):
        print("\n[dry-run] image is valid and restorable. Re-run with --host or "
              "--discover to see the device-side plan.")
        return 0

    host = args.host
    if args.discover:
        import install
        host = install.discover_linklocal(args.discover)
        print(f"[*] discovered {host}")
    if not host:
        raise RestoreError("give --host or --discover")

    import install
    install.check_is_openwrt_ram(host)      # refuses this host's own gateway
    mtd = check_target(host, profile)
    p = plan(mtd, profile)

    print("\n=== plan ===")
    print(f"  write  /dev/mtd{p['write'][0]} ({p['write'][1]}) <- stock UBI, "
          f"{len(payload)//profile.peb_size} PEBs at its start = stock slot 0")
    print(f"  erase  /dev/mtd{p['erase_rootfs'][0]} entirely "
          f"({p['erase_rootfs'][2]} blocks) = stock slot 1 tail + all of overlay")
    print(f"  leave  /dev/mtd{p['erase_tail'][0]} tail ({p['erase_tail'][2]} blocks at "
          f"0x{p['erase_tail'][1]:x}) carrying ubiformat's EC headers: no "
          "offset-erase tool exists on this image, and stock never consults "
          "slot 1 while slot 0's counter is 0")
    print("  nvram  flag_last_success=0 flag_boot_rootfs=0 "
          "flag_try_sys{1,2}_failed=0")
    print("\n  This erases OpenWrt. Afterwards the only ways back are this "
          "installer again, or TFTP recovery with a cable.")
    if args.dry_run:
        print("\n[dry-run] stopping here.")
        return 0
    if not args.yes:
        if input("\ntype RESTORE to continue: ").strip() != "RESTORE":
            print("declined")
            return 1
    do_restore(host, payload, img_volumes(payload, profile), p, profile,
               recover_stale_lock=args.recover_stale_lock)
    return 0


def img_volumes(payload, profile):
    """{name: md5 of the volume as UBI will present it back}."""
    img = ubiparse.UbiImage(payload, peb_size=profile.peb_size)
    return {v.name: hashlib.md5(img.extract(v.vol_id)).hexdigest()
            for v in img.volumes.values() if v.lebs}


def parse_env_values(text, keys):
    values = {}
    for line in text.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key in values:
            raise RestoreError(f"duplicate boot environment value for {key}")
        values[key] = value
    missing = [key for key in keys if key not in values]
    if missing:
        raise RestoreError(f"boot environment readback is missing {missing}")
    return values


def set_bootenv_verified(host, wanted):
    for key, value in wanted.items():
        ssh(host, f"fw_setenv {key} {value}", check=True)
    keys = tuple(wanted)
    _rc, output = ssh(host, "fw_printenv " + " ".join(keys), check=True)
    got = parse_env_values(output, keys)
    mismatched = {key: (str(wanted[key]), got[key]) for key in keys
                  if got[key] != str(wanted[key])}
    if mismatched:
        raise RestoreError(f"boot environment readback mismatch: {mismatched}")
    return got


def remote_job_state(host, status_path, lock_dir):
    _rc, state = ssh(
        host,
        f"s=$(cat {status_path} 2>/dev/null); "
        f"p=$(cat {lock_dir}/pid 2>/dev/null); "
        "a=0; [ -n \"$p\" ] && kill -0 \"$p\" 2>/dev/null && a=1; "
        f"printf 'status=%s pid=%s active=%s lock=%s' \"$s\" \"$p\" \"$a\" "
        f"\"$([ -d {lock_dir} ] && echo 1 || echo 0)\"",
        check=False)
    return state


def do_restore(host, payload, want_md5, p, profile, recover_stale_lock=False):
    import tempfile
    k, _ = p["write"]
    _kd, tail_off, tail_cnt = p["erase_tail"]
    rd, _, rootfs_cnt = p["erase_rootfs"]

    state = remote_job_state(host, "/tmp/restore.status", "/tmp/restore.lock")
    reuse_done = "status=done" in state
    reuse_active = "active=1" in state
    if not (reuse_done or reuse_active) and (
            "lock=1" in state or "status=fail" in state or "status=running" in state):
        if not recover_stale_lock:
            raise RestoreError(
                f"stale/failed restore state requires --recover-stale-lock "
                f"after inspection ({state})")
        ssh(host, "rm -rf /tmp/restore.lock; rm -f /tmp/restore.status")
        print(f"[1] explicitly cleared inactive stale restore state: {state}")

    with tempfile.TemporaryDirectory() as tmp:
        ubi = os.path.join(tmp, "stock.ubi")
        with open(ubi, "wb") as fh:
            fh.write(payload)
        local_md5 = hashlib.md5(payload).hexdigest()

        _rc, out = ssh(host, "df -k /tmp | tail -1")
        avail = int(out.split()[-3]) * 1024 if len(out.split()) >= 3 else 0
        if avail < len(payload) + 2 * 1024 * 1024:
            raise RestoreError(f"/tmp has {avail} B free, need {len(payload)}")

        if not (reuse_done or reuse_active):
            print(f"\n[1] uploading {len(payload)} B ...")
            scp(host, ubi, "/tmp/stock.ubi")
            _rc, out = ssh(host, "md5sum /tmp/stock.ubi", timeout=300)
            if local_md5 not in out:
                raise RestoreError(f"upload md5 mismatch: {out!r} != {local_md5}")
            print(f"[1] md5 {local_md5} verified on the device")

            script = os.path.join(tmp, "restore.sh")
            with open(script, "w") as fh:
                fh.write(RESTORE_SCRIPT.format(k=k, r=rd, tail_off=hex(tail_off),
                                               tail_cnt=tail_cnt,
                                               rootfs_cnt=rootfs_cnt))
            scp(host, script, "/tmp/restore.sh")
            ssh(host, "chmod +x /tmp/restore.sh; rm -f /tmp/restore.status")

    if reuse_done:
        print("[2] prior restore reports done; reusing it for readback")
    elif reuse_active:
        print("[2] existing restore writer is active; waiting for it")
    else:
        # Detached, and proven started: `start-stop-daemon -S` matches on the
        # -x binary, so it must name the script, not the shell.
        print("[2] writing, detached")
        _rc, launch = ssh(
            host, "start-stop-daemon -S -b -x /tmp/restore.sh; echo rc=$?",
            check=False)
        if "rc=0" not in launch:
            raise RestoreError(f"restore launcher failed: {launch!r}")
    deadline = time.time() + 900
    status = "done" if reuse_done else ""
    while time.time() < deadline:
        time.sleep(5)
        _rc, status = ssh(host, "cat /tmp/restore.status 2>/dev/null", check=False)
        if status.startswith(("done", "fail")):
            break
        print(f"    ... {status or 'starting'}")
    if not status.startswith("done"):
        _rc, tail = ssh(host, "tail -20 /tmp/restore.log", check=False)
        raise RestoreError(f"restore did not finish ({status!r}):\n{tail}")
    print("[2] ubiformat + erases reported done")

    print("[3] reading the volumes back")
    ssh(host, "ubidetach -d 9 2>/dev/null; true", check=False)
    rc, out = ssh(host, f"ubiattach -m {k} -d 9", timeout=180, check=False)
    if rc != 0:
        raise RestoreError(f"the restored UBI will not attach: {out!r}")
    _rc, vols = ssh(host, "ubinfo -a -d 9")
    # ubinfo -a emits one block per volume. Anchoring a lazy span from the
    # FIRST "Volume ID:" to a given Name matches volume 0 for every name --
    # which silently md5s the same device twice and reports a mismatch on a
    # perfectly good write. Split into blocks and read each one's own id.
    import re as _re
    blocks = {}
    for chunk in _re.split(r"(?=Volume ID:)", vols):
        mid = _re.search(r"Volume ID:\s+(\d+)", chunk)
        mnm = _re.search(r"Name:\s+(\S+)", chunk)
        if mid and mnm:
            blocks[mnm.group(1)] = mid.group(1)
    got = {}
    for name, md5want in want_md5.items():
        m = _re.match(r"(\d+)$", blocks.get(name, ""))
        if not m:
            ssh(host, "ubidetach -d 9", check=False)
            raise RestoreError(f"volume {name!r} missing after restore:\n{vols}")
        _rc, o = ssh(host, f"md5sum /dev/ubi9_{m.group(1)}", timeout=300)
        got[name] = o.split()[0] if o else ""
        print(f"    {name:<12} {got[name]}  {'OK' if got[name] == md5want else 'MISMATCH'}")
    ssh(host, "ubidetach -d 9", check=False)
    bad = [n for n, v in got.items() if v != want_md5[n]]
    if bad:
        raise RestoreError(
            f"{bad} did not read back as the image. Do NOT reboot -- the boot "
            "flags still point at OpenWrt's slot, so power-cycling now returns "
            "you to the initramfs and you can retry.")

    print("[4] pointing the bootloader back at stock's slot")
    wanted_env = {
        "flag_last_success": 0,
        "flag_boot_rootfs": 0,
        "flag_try_sys1_failed": 0,
        "flag_try_sys2_failed": 0,
        "flag_boot_success": 1,
        "flag_ota_reboot": 0,
    }
    got_env = set_bootenv_verified(host, wanted_env)
    print("    " + " ".join(f"{key}={got_env[key]}" for key in wanted_env))

    print("[5] rebooting into stock")
    ssh(host, "start-stop-daemon -S -b -x /sbin/reboot", check=False)
    print("\n[+] restore written and verified. The unit should come back as "
          f"stock {profile.display_name} on its own SSID, with the setup wizard.")


def scp(host, local, remote):
    base = ["sshpass", "-p", ""] if _have("sshpass") else []
    base += ["scp", "-O", "-o", "StrictHostKeyChecking=no",
             "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR",
             local, f"root@[{host}]:{remote}" if ":" in host
             else f"root@{host}:{remote}"]
    subprocess.run(base, check=True, timeout=900)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RestoreError as e:
        print(f"[-] {e}", file=sys.stderr)
        sys.exit(1)
