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

It logs in by password, like the rest of this tool. A router configured for
SSH keys only (`PasswordAuth off`) is refused at step 1, before any write.
"""

import argparse
import os
import secrets
import shlex
import shutil
import subprocess
import sys
import tarfile
import time

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


def scp_from(host, remote, local):
    """Copy remote:path off the box. install.py only ever sends."""
    if hostnet.is_windows():
        try:
            return winssh.download(host, remote, local, install.SSH_PASSWORDS)
        except winssh.SSHError as exc:
            raise Abort(str(exc)) from exc
    src = f"root@[{host}]:{remote}" if ":" in host else f"root@{host}:{remote}"
    last = None
    for password in install.SSH_PASSWORDS:
        argv = ["scp", "-O", "-o", "StrictHostKeyChecking=no",
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
    # Refuses this host's own gateway; see install.gateway_conflict.
    install.check_is_openwrt_ram(host, router_iface=router_iface)
    rc, out = install.ssh(host, "echo up", timeout=30, check=False)
    if rc != 0:
        if "Permission denied" in out:
            raise Abort(t(
                "{host} refused the root password offered. Pass the current one "
                "with --root-password. A router set to SSH keys only "
                "(PasswordAuth off) is not supported: nothing was written.",
                host=host))
        raise Abort(f"cannot log in to {host}: {out}")

    board, rootfs = revert.state_of(host, profile)   # refuses any other board
    log(f"[1] {host}: {board}, rootfs_type={rootfs}")
    why = revert.require_nand_support(host, rel, profile, images_dir)
    log(f"[1] NAND gate: PASS -- {why}")
    if rootfs == "tmpfs":
        return rootfs, "ram", ""
    if rootfs != "overlay":
        raise Abort(f"rootfs_type is {rootfs!r}: neither the installed system "
                    "nor the RAM installer, so nothing here applies")

    _rc, mtd_text = install.ssh(host, "cat /proc/mtd")
    if not install.permanent_system_matches(profile, board, rootfs, mtd_text):
        raise Abort("the partition map is not this profile's OpenWrt layout: "
                    f"expected {sorted(profile.openwrt_partitions)}\n{mtd_text}")
    mtd = install.parse_mtd(mtd_text)
    require_idle_kernel_partition(host, mtd)

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
    return rootfs, ("in-place" if in_place else "ram"), installed


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
                   installed):
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
    gateway_iface = args.discover if args.transport == "wired" else None
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
        log("    " + install.verification_command(manifest, verify_interface))
        raise
    install.update_resume_phase(manifest, "flash-complete")


def run(args):
    os.umask(0o077)
    i18n.set_language(args.lang)
    i18n.tolerate_console_encoding()
    profile = devices.get_profile(args.device)
    args.image = {"default": "standard", "nss": "nss"}[args.flavour]

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

    outdir = os.path.abspath(
        args.outdir or f"update-{time.strftime('%Y%m%d-%H%M%S')}")
    os.makedirs(outdir, mode=0o700, exist_ok=True)
    os.chmod(outdir, 0o700)
    chain.set_log_sink(open(f"{outdir}/transcript.log", "w", encoding="utf-8"))
    log(f"[*] output -> {outdir}/")

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

    install.require_host_tools(password=bool(args.root_password))
    if args.root_password:
        install.SSH_PASSWORDS.append(args.root_password)
        restore.SSH_PASSWORDS[:] = install.SSH_PASSWORDS

    # On a cable the router's own DHCP makes it this host's gateway. That is
    # accepted only while all we would offer it is the empty password, the same
    # rule as restore.locate; a configured one goes only to a link-local peer.
    fallback = profile.openwrt_host if args.transport == "wired" else None
    gateway_ok = args.transport == "wired" and not any(install.SSH_PASSWORDS)
    host = args.host or install.wait_and_discover(
        args.discover, deadline_s=120, fallback=fallback,
        router_gateway_ok=gateway_ok)

    rootfs, route, installed = preflight(host, profile, rel, images, args.images,
                              router_iface=args.discover if gateway_ok else None,
                              allow_in_place=not args.via_ram_installer)
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
            backup_config(host, outdir)      # still take one of what is there now
        else:
            config_tar = backup_config(host, outdir)
        if not args.keep_settings:
            config_tar = None
            log("[*] clean install: settings will be reset to defaults; a backup "
                f"of the current ones is in {outdir}")

    if config_tar and turns_off_password_login(config_tar):
        log(t("[!] this config turns off password login, and the final check logs "
              "in by password. It will report that it could not verify the router "
              "even when the update worked: when it starts waiting, check with your "
              "SSH key instead."))

    manifest = os.path.join(outdir, "resume.json")
    install.write_resume_manifest(manifest, args, profile, rel, images,
                                  secrets.token_hex(16), config_tar)

    if route == "in-place":
        flash_in_place(host, rel, images, args, profile, manifest,
                       verify_transport, installed)
        log(t("[+] update complete: your settings were restored." if config_tar
              else "[+] update complete: the new release is running with default settings."))
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
        except Exception as exc:            # noqa: BLE001 -- tagged by the pivot
            # What to say depends on how far the write got, and the exception
            # class cannot express that: pivot_to_ram tags the stage instead.
            # Catching Exception rather than (Abort, RestoreError) keeps a
            # transport timeout raised mid-write from escaping with no advice.
            stage = getattr(exc, "pivot_stage", None)
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

    install.flash(
        host, rel, images, args.yes, profile, args.images, config_tar, manifest,
        args.transport, args.verify_interface, args.wifi_ssid,
        profile.openwrt_host if verify_transport == "wired" else None,
        verify_transport,
        router_iface=args.discover if args.transport == "wired" else None,
        expected_revision=release.revision(rel, args.images))
    log(t("[+] update complete: your settings were restored." if config_tar
          else "[+] update complete: the new release is running with default settings."))
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
