#!/usr/bin/env python3
"""Return a supported OpenWrt router to an approved stock image.

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

Use `python3 revert.py --help` for profile, image, and connection options.
"""

import argparse
import hashlib
import os
import re
import subprocess
import sys
import time

import devices
import i18n
import install
import release
import restore
from chain import log
from i18n import t


def verified_ram_images(rel, images_dir, flavour="default", wifi=False):
    images = release.get_images(
        rel, images_dir, flavour,
        kinds=("initramfs_ubi", "initramfs_itb"), wifi=wifi)
    if not all(item.get("verified") for item in images.values()):
        raise restore.RestoreError("RAM image pair lacks trusted SHA-256 values")
    return images["initramfs_ubi"]["path"], images["initramfs_itb"]["path"]


PIVOT_SCRIPT = """\
#!/bin/sh
if ! mkdir /tmp/pv.lock 2>/dev/null; then
    echo fail:locked > /tmp/pv.status
    exit 1
fi
echo $$ > /tmp/pv.lock/pid
trap 'rm -rf /tmp/pv.lock' EXIT
exec >/tmp/pv.log 2>&1
echo running > /tmp/pv.status
ubiformat /dev/mtd{k} -f /tmp/ini.ubi -y
rc=$?
sync
[ $rc = 0 ] && echo done > /tmp/pv.status || echo "fail:$rc" > /tmp/pv.status
"""


def remote_job_state(host, status_path, lock_dir):
    _rc, state = install.ssh(
        host,
        f"s=$(cat {status_path} 2>/dev/null); "
        f"p=$(cat {lock_dir}/pid 2>/dev/null); "
        "a=0; [ -n \"$p\" ] && kill -0 \"$p\" 2>/dev/null && a=1; "
        f"printf 'status=%s pid=%s active=%s lock=%s' \"$s\" \"$p\" \"$a\" "
        f"\"$([ -d {lock_dir} ] && echo 1 || echo 0)\"",
        check=False)
    return state


def kernel_volume_id(ubinfo_text):
    """The volume id of the volume named `kernel`, or None.

    `ubinfo -a` emits one block per volume, so each block's own id must be read.
    A lazy span from the FIRST "Volume ID:" to a given Name matches volume 0 for
    every name -- the mistake restore.do_restore records, which silently md5s
    the wrong device and calls a good write bad.
    """
    for chunk in re.split(r"(?=Volume ID:)", ubinfo_text):
        vid = re.search(r"Volume ID:\s+(\d+)", chunk)
        name = re.search(r"Name:\s+(\S+)", chunk)
        if vid and name and name.group(1) == "kernel":
            return vid.group(1)
    return None


def read_back_kernel(host, k, want_md5):
    """Attach the freshly written UBI, md5 its kernel volume, detach.

    Returns the device that was read. Raises RestoreError if the UBI will not
    attach, carries no volume named `kernel`, or reads back a different md5.
    """
    install.ssh(host, "ubidetach -d 9 2>/dev/null; true", check=False)
    rc, out = install.ssh(host, f"ubiattach -m {k} -d 9", timeout=120, check=False)
    if rc != 0:
        raise restore.RestoreError(
            f"the written UBI will not attach: {out!r}. Do NOT reboot: "
            "ubi_kernel held the installed kernel and now holds a write that "
            "could not be verified. The running system is fine until it "
            "reboots. Re-run now to retry the write.")
    _rc, vols = install.ssh(host, "ubinfo -a -d 9", check=False)
    vid = kernel_volume_id(vols)
    if vid is None:
        install.ssh(host, "ubidetach -d 9", check=False)
        raise restore.RestoreError(
            f"no 'kernel' volume in the written UBI:\n{vols}\n"
            "Do NOT reboot: ubi_kernel held the installed kernel and now holds "
            "a write that could not be verified. Re-run now to retry the write.")
    dev = f"/dev/ubi9_{vid}"
    _rc, got = install.ssh(host, f"md5sum {dev}; wc -c < {dev}", timeout=300)
    install.ssh(host, "ubidetach -d 9", check=False)
    got_md5 = got.split()[0] if got else ""
    log(f"[1] read-back {dev}: {got.splitlines()[-1] if got else '?'} B, "
        f"md5 {got_md5}")
    if got_md5 != want_md5:
        raise restore.RestoreError(
            f"read-back {got_md5} != expected {want_md5}. Do NOT reboot: "
            "ubi_kernel held the installed kernel and now holds a bad write, so "
            "nothing bootable is left there. The running system is fine until it "
            "reboots. Re-run now to retry the write.")
    log("[1] read-back matches the image byte for byte")
    return dev


def pivot_advice(error):
    """What to add after a pivot failure, or None when there is nothing to add.

    `pivot_to_ram` tags any exception it raises with how far the write got, but
    the tag is only useful if the caller turns it into an instruction, and a
    standalone revert has no updater to do that for it.
    """
    stage = getattr(error, "pivot_stage", None)
    if stage == "write-unconfirmed":
        return ("Do NOT reboot or power-cycle: ubi_kernel held the installed "
                "kernel and now holds a write that could not be verified. The "
                "running system is fine until it reboots. Re-run to retry.")
    if stage == "rebooted":
        return ("The write verified. Reach the RAM installer (allow it about a "
                "minute to start) and re-run to continue.")
    return None


def pivot_to_ram(host, ubi_path, itb_path, iface, dry_run=False, fallback=None,
                 recover_stale_lock=False):
    """Put the RAM image in `ubi_kernel` and boot it. Returns the new host.

    `ubi_kernel` is not attached while OpenWrt runs -- the kernel was read out
    of it at boot and nothing holds it since -- so overwriting it under the
    running system is safe. What is not safe is rebooting onto a bad write,
    hence the read-back before the reboot.

    A failure is tagged with `pivot_stage` -- `not-written`,
    `write-unconfirmed` or `rebooted` -- because the exception class cannot say
    how far it got: the same RestoreError and Abort are raised before the write,
    after it was issued, and once the box is already in the RAM installer, and
    what the operator should do differs completely between those.
    """
    phase = {"written": False, "pivoted": False}
    try:
        return _pivot_to_ram(host, ubi_path, itb_path, iface, dry_run, fallback,
                             recover_stale_lock, phase)
    except Exception as exc:
        exc.pivot_stage = ("rebooted" if phase["pivoted"]
                           else "write-unconfirmed" if phase["written"]
                           else "not-written")
        raise


def _pivot_to_ram(host, ubi_path, itb_path, iface, dry_run, fallback,
                  recover_stale_lock, phase):
    mtd = restore.mtd_map(host)
    if "ubi_kernel" not in mtd:
        raise restore.RestoreError("no ubi_kernel partition -- not the OpenWrt layout")
    k = mtd["ubi_kernel"]["index"]

    # ubiformat erases PEBs as it writes, so an image that does not fit is not a
    # clean failure: the installed kernel is already gone by the time it reports
    # "not enough space". install.py and updater.py both check the size first;
    # this is the path that reaches the no-fallback case, so it must too.
    size = os.path.getsize(ubi_path)
    if size > mtd["ubi_kernel"]["size"]:
        raise restore.RestoreError(
            f"{os.path.basename(ubi_path)} is {size} B, ubi_kernel holds "
            f"{mtd['ubi_kernel']['size']} B -- refusing to erase the installed "
            "kernel for an image that cannot fit it")
    log(f"[1] image {size} B fits ubi_kernel ({mtd['ubi_kernel']['size']} B)")

    # A previous pivot only ever leaves device 9 attached through its read-back,
    # and that read-back only runs after a write -- which always leaves
    # /tmp/pv.status or the lock behind. So the same state that drives the
    # decisions below is also the evidence that an attached device 9 on
    # ubi_kernel is this tool's own scratch and not something live.
    state = remote_job_state(host, "/tmp/pv.status", "/tmp/pv.lock")
    reported = {}
    for part in state.split():
        key, _, value = part.partition("=")
        reported[key] = value
    reuse_done = reported.get("status") == "done"
    previous_pivot = bool(reported.get("status")) or reported.get("lock") == "1"

    # The same hard interlock the A/B path uses, for the same reason: mtd{k} is
    # the partition holding the only bootable kernel, so it must not be live.
    # This is a read only -- a dry run must not change kernel state, so a stale
    # device 9 left by an earlier read-back is only recorded here and dropped
    # after the dry-run gate below. Detaching first would also hide a live ubi9
    # from the very check it is meant to satisfy.
    _rc, attached = install.ssh(
        host,
        "for u in /sys/class/ubi/ubi*; do "
        "printf '%s %s\\n' \"${u##*/}\" \"$(cat $u/mtd_num 2>/dev/null)\"; done",
        check=False)
    by_mtd = {}
    for line in attached.splitlines():
        fields = line.split()
        if len(fields) == 2 and fields[1].isdigit():
            by_mtd.setdefault(fields[1], set()).add(fields[0])
    if not by_mtd:
        # Fail closed: reading no attached UBI would make any target look idle.
        raise restore.RestoreError(
            "could not read which MTDs the running UBIs sit on "
            "(/sys/class/ubi/*/mtd_num) -- refusing to pick a target")
    for other_mtd, devices in by_mtd.items():
        if other_mtd != str(k) and "ubi9" in devices:
            raise restore.RestoreError(
                f"device 9 is attached to mtd{other_mtd}, not to ubi_kernel "
                f"(mtd{k}). This tool reserves device 9 for its own read-back "
                "and will not detach a device it does not own. Detach it on the "
                "router (ubidetach -d 9) or reboot, then re-run. Nothing has "
                "been written by this run.")

    on_target = by_mtd.get(str(k), set())
    # Device 9 is this tool's own scratch device, used only for the read-back;
    # it can outlive a run that died there. Anything else on ubi_kernel is live.
    if on_target - {"ubi9"}:
        raise restore.RestoreError(
            f"ubi_kernel (mtd{k}) has {', '.join(sorted(on_target))} attached at "
            "runtime; refusing to overwrite a partition that may be live")
    # Device 9 is this toolchain's reserved scratch number: install.verify_written,
    # restore.do_restore and read_back_kernel are its only users, and ubiattach
    # fails outright when the number is taken. A live device numbered 9 still
    # detaches as cleanly as our own, so "it was not busy" proves nothing; it is
    # ours only when a pivot ran here to leave it.
    stale_scratch = "ubi9" in on_target
    if stale_scratch and not previous_pivot:
        # The attachment is kernel state, so it outlives /tmp: an operator who
        # cleared /tmp, or a box where the status file never landed, reaches
        # this. Say how to get out rather than claiming no pivot ever ran.
        raise restore.RestoreError(
            f"ubi_kernel (mtd{k}) has ubi9 attached, but this box has no record "
            f"of a pivot ({state.strip() or 'empty'}), so it cannot be told "
            "apart from a partition that is live. If you know device 9 is not "
            "in use, detach it on the router (ubidetach -d 9) and re-run. This "
            "run has written nothing, but a previous pivot may have, so do not "
            "reboot until you know what device 9 is.")
    log(f"[1] UBI attached on mtd{sorted(int(item) for item in by_mtd)}; "
        f"ubi_kernel is mtd{k}"
        + (", holding only our stale device 9" if stale_scratch else " and is idle"))

    blob, want_md5 = install.expected_volume(ubi_path, itb_path)
    local_md5 = hashlib.md5(open(ubi_path, "rb").read()).hexdigest()
    log(f"[1] pivot via mtd{k} (ubi_kernel); a correct write reads back "
        f"{len(blob)} B, md5 {want_md5}")
    if dry_run:
        log("[1] [dry-run] stopping before the write")
        return None

    if stale_scratch:
        # Only now, so that a dry run really is read-only.
        rc, out = install.ssh(host, "ubidetach -d 9", check=False)
        if rc != 0:
            # Device 9 sits on ubi_kernel, so an earlier read-back ran -- and
            # that run may already have erased part of it.
            phase["written"] = True
            raise restore.RestoreError(
                f"could not detach the stale read-back device 9: {out!r}. "
                "Do NOT reboot: an earlier pivot may already have erased part "
                "of ubi_kernel. Inspect it, then re-run to retry.")
        log("[1] dropped the stale read-back device 9 that sat on ubi_kernel")

    # Any status at all, or a held lock, means a previous ubiformat was issued
    # and ubi_kernel may already be partly erased -- including in the failed or
    # interrupted state the refusal below is about to report. Tagging that
    # "not-written" would drop the do-not-reboot warning in the one state where
    # clearing /tmp by rebooting is the act that leaves the box unbootable.
    phase["written"] = previous_pivot
    if "active=1" in state:
        raise restore.RestoreError(f"pivot ubiformat is still running ({state})")
    if not reuse_done and ("lock=1" in state or "status=fail" in state
                           or "status=running" in state):
        if not recover_stale_lock:
            raise restore.RestoreError(
                f"stale/failed pivot state requires --recover-stale-lock "
                f"after inspection ({state}). Do not reboot to clear it: an "
                "interrupted ubiformat may already have erased part of "
                f"ubi_kernel. Inspect /tmp/pv.log, then retry with "
                "--recover-stale-lock.")
        install.ssh(host, "rm -rf /tmp/pv.lock; rm -f /tmp/pv.status")
        log(f"[1] explicitly cleared inactive stale pivot state: {state}")

    if reuse_done:
        log("[1] prior pivot write reports done; reusing it for readback")
    else:
        log(f"[1] uploading {os.path.basename(ubi_path)}")
        install.scp_to(host, ubi_path, "/tmp/ini.ubi")
        rc, out = install.ssh(host, "md5sum /tmp/ini.ubi", timeout=300)
        if local_md5 not in out:
            raise restore.RestoreError(f"upload md5 mismatch: {out!r} != {local_md5}")
        log(f"[1] md5 {local_md5} verified on the device")

        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            sh = os.path.join(tmp, "pv.sh")
            with open(sh, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(PIVOT_SCRIPT.format(k=k))
            install.scp_to(host, sh, "/tmp/pv.sh")
        install.ssh(host, "chmod +x /tmp/pv.sh; rm -f /tmp/pv.status",
                    check=True)
        phase["written"] = True         # ubiformat is about to erase PEBs
        rc, out = install.ssh(
            host, "start-stop-daemon -S -b -x /tmp/pv.sh; echo rc=$?",
            check=False)
        log(f"[1] launch: {out.strip()}")
        if "rc=0" not in out:
            # The launcher answered and refused, so no ubiformat started. A
            # transport error above leaves the flag set instead, which is the
            # conservative direction: a write may have begun.
            phase["written"] = False
            raise restore.RestoreError(f"pivot launcher failed: {out!r}")

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
            raise restore.RestoreError(
                f"pivot write did not finish ({status!r}):\n{tail}")
        log("[1] ubiformat done")

    read_back_kernel(host, k, want_md5)
    phase["written"] = False            # the write is confirmed good
    phase["pivoted"] = True             # from here the box boots the RAM image

    install.ssh(host, "start-stop-daemon -S -b -x /sbin/reboot", check=False)
    log(t("[1] rebooting into the RAM initramfs"))
    time.sleep(15)
    return install.wait_and_discover(iface, fallback=fallback) if iface else host


def state_of(host, profile):
    """(board, rootfs_type) -- and refuse anything that is not this board."""
    _rc, board = restore.ssh(host, ". /lib/functions.sh 2>/dev/null; board_name")
    if board.strip() != profile.openwrt_board:
        raise restore.RestoreError(
            f"board_name is {board.strip()!r}, not {profile.openwrt_board} -- "
            f"this is not {profile.display_name} running its supported port")
    _rc, rtype = restore.ssh(host, ". /lib/upgrade/common.sh 2>/dev/null; rootfs_type")
    return board.strip(), rtype.strip()


def require_nand_support(host, rel, profile, images_dir):
    """Refuse a release whose kernel cannot probe this unit's flash."""
    rc, raw_ft = install.ssh(
        host, "fw_printenv -n flash_type 2>/dev/null", check=False)
    if rc != 0 or not raw_ft.strip():
        rc, raw_ft = install.ssh(
            host, "fw_printenv flash_type 2>/dev/null", check=False)
        raw_ft = raw_ft.split("=", 1)[-1] if "=" in raw_ft else ""
    _ft, _part, why = install.require_permanent_nand_support(
        raw_ft, rel, profile, images_dir)
    return why


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    devices.add_device_argument(ap)
    ap.add_argument("image", help="official stock firmware image")
    ap.add_argument("--host", default=None, help="fe80::...%%iface of the box")
    ap.add_argument("--interface", "--discover", dest="discover",
                    metavar="IFACE", default=None,
                    help="find the box on IFACE (and again after the pivot)")
    ap.add_argument("--transport", choices=("auto", "wired", "wifi"),
                    default="auto")
    ap.add_argument("--images", default="images",
                    help="directory holding the initramfs pair")
    ap.add_argument("--release", default=None,
                    help="release tag or 'latest' for the RAM image "
                         "(default: latest published release)")
    ap.add_argument("--flavour", choices=("default", "nss"), default="default")
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--root-password", default=None,
                    help="the installed system's root password, if one is set")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--yes", action="store_true")
    ap.add_argument("--expected-image-sha256", default=None,
                    help="explicit digest for an unlisted development image")
    ap.add_argument("--recover-stale-lock", action="store_true",
                    help="clear an inactive stale writer lock after inspection")
    ap.add_argument("--lang", choices=i18n.LANGUAGES, default="auto",
                    help="language for prompts and instructions (default: system)")
    args = ap.parse_args()
    i18n.set_language(args.lang)
    i18n.tolerate_console_encoding()
    profile = devices.get_profile(args.device)
    args.release = args.release or profile.default_release
    args.discover = install.canonical_interface(args.discover)
    if not args.dry_run:
        install.require_host_tools()
    if args.transport == "auto" and args.discover:
        args.transport = install.detect_transport(args.discover)
    elif args.transport == "auto":
        args.transport = "wired" if args.host and "%" not in args.host else "wifi"
    fallback = profile.openwrt_host if args.transport == "wired" else None
    rel = release.resolve(args.release, profile, args.images,
                          offline=args.offline, log=log)
    ram_wifi = args.transport == "wifi"
    ubi, itb = verified_ram_images(
        rel, args.images, args.flavour, wifi=ram_wifi)

    # Validate the stock image before touching the device: if it is not a
    # restorable image there is no point pivoting anything.
    payload, _digest, known = restore.carve(args.image, profile)
    restore.inspect(payload, profile)
    restore.require_trusted_image(_digest, known, args.expected_image_sha256)

    if args.root_password:
        install.SSH_PASSWORDS.append(args.root_password)
        restore.SSH_PASSWORDS[:] = install.SSH_PASSWORDS

    host = args.host
    if not host and args.discover:
        host = install.wait_and_discover(
            args.discover, deadline_s=120, fallback=fallback)
    if not host:
        raise restore.RestoreError("give --host or --discover")

    install.check_is_openwrt_ram(host)      # refuses this host's own gateway
    board, rtype = state_of(host, profile)
    log(f"[*] {host}: {board}, rootfs_type={rtype}")

    why = require_nand_support(host, rel, profile, args.images)
    log(f"[*] RAM-image NAND gate: PASS -- {why}")

    if rtype != "tmpfs":
        log(t("\n=== step 1: pivot into the RAM initramfs ==="))
        if args.transport == "wifi":
            log(t("[1] when the installed network disappears, join {ssid!r} with "
                  "key {key!r}; this temporary RAM system has a passwordless root "
                  "account, so keep the link isolated",
                  ssid=profile.installer_wifi_ssid,
                  key=profile.installer_wifi_key))
        if not args.yes and not args.dry_run:
            if input(t("[?] write {image} to ubi_kernel and reboot? [type YES] ",
                       image=os.path.basename(ubi))).strip() != "YES":
                raise restore.RestoreError(t("declined"))
        newhost = pivot_to_ram(host, ubi, itb, args.discover, args.dry_run,
                               fallback=fallback,
                               recover_stale_lock=args.recover_stale_lock)
        if args.dry_run:
            log("\n[dry-run] would restore stock next; stopping.")
            return 0
        host = newhost or host
        if not args.discover and not install.wait_for_openwrt(host, deadline_s=420):
            raise restore.RestoreError(
                f"RAM initramfs did not start answering on {host}")
        _board, rtype = state_of(host, profile)
        if rtype != "tmpfs":
            raise restore.RestoreError(
                f"after the pivot the box is still running from {rtype!r}. It "
                "did not boot the RAM image; nothing has been erased.")
        log(f"[1] in RAM at {host}")
    else:
        log("[*] already running from RAM -- skipping the pivot")

    log(t("\n=== step 2: restore stock ==="))
    mtd = restore.check_target(host, profile)
    plan = restore.plan(mtd, profile)
    if args.dry_run:
        log("[dry-run] stopping before the restore write.")
        return 0
    if not args.yes:
        if input(t("[?] erase OpenWrt and write stock? [type RESTORE] ")).strip() \
                != "RESTORE":
            raise restore.RestoreError(t("declined"))
    restore.do_restore(host, payload, restore.img_volumes(payload, profile), plan,
                       profile, recover_stale_lock=args.recover_stale_lock)

    log(t("\n=== step 3: wait for stock ==="))
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
        identity_error = devices.stock_identity_error(profile, info)
        if identity_error:
            log(f"[!] stock answered, but it is not the restored router: "
                f"{identity_error}")
            return 1
        if info.get("inited") == 0:
            log(t("[+] factory defaults -- the setup wizard is waiting"))
        return 0
    log(t("[!] stock did not answer on {host} within 7 minutes. It may still be "
          "booting; check that this host has an address on that subnet.",
          host=profile.stock_host))
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (restore.RestoreError, release.ReleaseError, install.Abort,
            subprocess.TimeoutExpired) as e:
        log(f"[-] {e}")
        advice = pivot_advice(e)
        if advice:
            log(f"[!] {advice}")
        sys.exit(1)
    except Exception as e:                              # noqa: BLE001 -- see below
        # The pivot tags every exception it raises, including ones this list
        # does not name (an OSError from the transport, for one). Show those
        # with their advice instead of a traceback, and leave every untagged
        # exception alone so real bugs still report a stack.
        if not hasattr(e, "pivot_stage"):
            raise
        log(f"[-] {e}")
        advice = pivot_advice(e)
        if advice:
            log(f"[!] {advice}")
        sys.exit(1)
    except KeyboardInterrupt:
        log("\n[*] interrupted")
        sys.exit(130)
