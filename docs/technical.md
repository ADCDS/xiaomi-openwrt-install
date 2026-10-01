# `xiaomi-router-install` technical reference

This document preserves the implementation details, recovery commands, flash
layout, and advanced controls. Start with the concise [README](../README.md)
for the supported installation command.

Install OpenWrt over Ethernet or Wi-Fi on supported Xiaomi routers — no serial
adapter or soldering — and put pristine stock back the same way.

## Supported devices

| profile | hardware | implementation status |
|---|---|---|
| `rd03v2` | Xiaomi AX3000T RD03v2 (Qualcomm IPQ5018) | V1 → V2 root, RAM pivot, NAND install, and restore; see the README and approved-image table for validation status |

Only `rd03v2` is enabled. The mesh vulnerabilities exist in firmware shared by
other Xiaomi models, but that does not establish their flash layout, boot
flags, OpenWrt image, or restoration procedure. Every additional model needs
its own reviewed and hardware-tested profile before this tool will accept it.

The port's documented install needs a USB↔UART adapter on the board's pads plus
a TFTP server over Ethernet, because the bootloader is locked. This drives it
instead through a pre-auth root RCE in the stock firmware's mesh daemon, pivots
into the OpenWrt RAM initramfs, and runs the sanctioned `sysupgrade` from there.

`chain.py` implements the exploit documented in
[`xiaomi-ax3000t-cabmeshd-disclosure`](https://github.com/ADCDS/xiaomi-ax3000t-cabmeshd-disclosure).
The advisory contains the PoC and disclosure timeline. Read
[`NOTICE`](../NOTICE) before running anything, and understand the recovery path
in [Going back to stock](#going-back-to-stock).

Verified end to end on hardware: factory state → configured OpenWrt on NAND,
and back to the stock image marked hardware-tested below, repeatedly, in both
directions.

## Prerequisites

A Linux laptop, Python **3.9+**, and:

```sh
sudo apt install sshpass openssl        # or your distro's equivalent
```

`sshpass` is not optional. Without it the scripts fall back to an interactive
`ssh` password prompt that fails as `Permission denied` — which looks exactly
like a wrong password rather than a missing package. `openssl` is used to hash
the root password. `ping6`, `ip`, `ssh`, `scp` and `tar` are assumed present.

**The laptop needs an address on the router's stock setup network.** Stock
serves DHCP there; the OpenWrt RAM system is reached by IPv6 link-local, so its
IPv4 subnet does not matter. Either:

```sh
# over Wi-Fi: join the router's open factory SSID
FACTORY_SSID='YOUR_FACTORY_SSID'
nmcli dev wifi connect "$FACTORY_SSID"

# or over a cable, into any LAN port
nmcli device status
IFACE=YOUR_ROUTER_INTERFACE
nmcli con add type ethernet ifname "$IFACE" con-name xiaomi-router \
    ipv4.method auto ipv6.method link-local autoconnect no
nmcli con up xiaomi-router
```

Over Wi-Fi the laptop's address gets baked into the exploit payload, so it
must not change mid-run. A cable is steadier; neither needs serial.

> Do **not** put the router on a network that already has a `192.168.1.1` —
> once OpenWrt is installed that is its LAN address, and it will serve DHCP.
> The scripts refuse to talk to your own default gateway, but a second DHCP
> server on your LAN is your problem, not theirs.

## Getting the images

The OpenWrt images are downloaded and sha256-verified automatically by
`install.py`. The **stock image is not** — you supply it, and you only need it
to revert:

| version | download | SHA-256 |
|---|---|---|
| 2.0.28 (hardware-tested) | [`miwifi_rd03v2_firmware_31bf9_2.0.28.bin`](https://cdn.cnbj1.fds.api.mi-img.com/xiaoqiang/rom/rd03v2/miwifi_rd03v2_firmware_31bf9_2.0.28.bin) | `3138342e564c7d7482fde4a90e1778830180f0eac15e1de5f3ad269f9ba9940f` |
| 2.0.12 (recognized) | [`miwifi_rd03v2_firmware_69eec_2.0.12.bin`](https://cdn.cnbj1.fds.api.mi-img.com/xiaoqiang/rom/rd03v2/miwifi_rd03v2_firmware_69eec_2.0.12.bin) | `be7af0e551d440a96757fe885dd775580fd8362addefb594b114f218ccc786c3` |

These are Xiaomi-signed images served from Xiaomi's CDN. In normal use,
`restore.py` and `revert.py` refuse any image whose full hash is not approved
by the selected device profile; `--yes` only skips confirmation prompts. The
development-only `--expected-image-sha256` override proves that the supplied
file matches an operator-provided digest. It does not establish vendor
provenance, device compatibility, or hardware validation.

```sh
sha256sum miwifi_rd03v2_firmware_31bf9_2.0.28.bin
```

Choose the newest profile-approved stock image that is not older than the
version the unit last ran. The bootloader's anti-rollback rejects older images.

## Two commands

```sh
nmcli device status
IFACE=YOUR_ROUTER_INTERFACE

# stock -> OpenWrt on NAND, with optional first-boot configuration
python3 install.py standard --interface "$IFACE" --configure

# OpenWrt -> a profile-approved stock image
python3 revert.py --device rd03v2 /path/to/approved-stock-image.bin \
    --interface "$IFACE" \
    --root-password 'YOUR_OPENWRT_PASSWORD'
```

`revert.py` rechecks its state and can be rerun after interruption. Once the
install exploit fires, its V2 trigger is spent until factory reset; continue an
interrupted install with the exact command and private `resume.json` printed by
`install.py`. Add `--yes` to skip prompts at irreversible points, or `--dry-run`
to validate images and show the revert plan without writing.

`$IFACE` is the interface facing the router — a USB Ethernet adapter, or your
Wi-Fi interface joined to the installer SSID. Discovery finds the box by its
IPv6 link-local, which sidesteps the fact that OpenWrt's `192.168.1.1` is also
a very common gateway address.

The beaconing RAM image uses fixed public Wi-Fi credentials and has a
passwordless root account. Keep that link isolated. During a Wi-Fi install or
revert, the driver prints the temporary SSID and key before the pivot and waits
while the operator reconnects. An install over Wi-Fi also requires permanent
Wi-Fi settings before the exploit runs when final verification uses Wi-Fi.
Before `sysupgrade`, the driver prints the permanent SSID without its
passphrase; after the RAM network disappears, join that SSID so final
verification can rediscover the installed system. To leave permanent Wi-Fi
disabled, pass a wired `--verify-interface` and connect that link when asked.

## What is here

| file | role |
|---|---|
| `devices.py` | device-profile registry and each device's identity, release, NAND, partition and restore safety boundary |
| `install.py` | the installer: pre-flight → pivot into the idle A/B slot → `sysupgrade` from the RAM system, with optional first-boot config |
| `revert.py` | one command back to stock: pivot into RAM if needed, then restore |
| `restore.py` | the restore itself, from Xiaomi's own signed image. Runs only from RAM |
| `chain.py` | supported stock firmware → root: wizard completion, V1 admin takeover, the `encryption` plant, the `cap_init` trigger |
| `channel.py` | operator side: stager delivery over HTTP, a durable root command channel, a bulk file sink |
| `release.py` | fetch + sha256-verify a release, and refuse one whose kernel cannot drive this unit's NAND |
| `ubiparse.py` | offline UBI parser — turns a raw MTD dump into a volume table |
| `probe.py` | fact-finding run used to establish the layout below; avoids raw firmware-partition writes but changes stock configuration and mesh state |
| `attach.py` | re-attach to a stager still dialling in, after a driver crash — the trigger is one-shot, so this saves a factory reset |
| `selftest.py` | checks that run without a router, with additional coverage when matching release artifacts are available |
| `installer-wifi.rc.local.patch` | the port change that makes the RAM initramfs beacon (shipped in v1.7 and later) |
| `LICENSE` | GPL-2.0-only |
| `NOTICE` | authorised-use, one-way-install and no-warranty terms — **read first** |

`chain.py` re-implements the exploit rather than importing the disclosure
package, so this tree stands on its own; the two repositories are independent
implementations of the same chain, which is also what makes one a useful check
on the other.

```sh
python3 selftest.py                          # no hardware or network

# Add checks against the selected profile's default release artifacts:
DEFAULT_RELEASE=$(python3 -c \
    'import devices; print(devices.get_profile("rd03v2").default_release)')
python3 release.py --device rd03v2 --tag "$DEFAULT_RELEASE" \
    --download --wifi --dest images
RD03V2_IMAGES="images/$DEFAULT_RELEASE" python3 selftest.py

# Optionally add stock-image carving and layout checks:
XIAOMI_STOCK_IMAGE=/path/to/approved-stock-image.bin python3 selftest.py
```

## Interrupted runs

Each run writes a mode-`0600` `resume.json` inside its mode-`0700` output
directory. It records the device profile, release, flavor, transport, exact
image hashes, configuration archive, callback address, permanent SSID (never
its passphrase), and callback token. When a manual stage is needed, use the
continuation command printed by `install.py`; the flash stage refuses selection
drift. `attach.py` likewise requires the bind address and token from this
manifest.

As soon as the RAM system reports that the detached sysupgrade launcher has
started, the host atomically records `flash-started` before allowing the write
to proceed. A disconnect after that point must never launch sysupgrade again.
Reconnect to the permanent network and run the read-only verifier instead:

```sh
python3 install.py --verify-only /path/to/resume.json \
    --verify-interface INTERFACE_NAME
```

`--verify-interface` may name a different interface from the original install,
such as an Ethernet adapter after a Wi-Fi interruption. Verification performs
no write: it rediscovers the router and requires the profile's exact board,
`rootfs_type=overlay`, and permanent partition sizes before recording
`flash-complete`. It also accepts an older `pivot-complete` manifest so a run
whose sysupgrade launch was not recorded can be inspected without risking a
second flash.

Detached NAND writers use atomic directory locks. A live writer is never
restarted. An inactive stale lock is preserved for inspection unless the
operator deliberately passes `--recover-stale-lock`.

## How the device is laid out

Established by `probe.py` against a live unit, and by reversing
`miwifi_config_env` out of a dump of `0:APPSBL`.

```
stock    rootfs   0x0a80000..0x2880000  30M   slot 0: kernel + ubi_rootfs
         rootfs_1 0x2880000..0x4680000  30M   slot 1: the idle one
         overlay  0x4680000..0x8000000  57M   cfg / user / plugin
OpenWrt  ubi_kernel 0x0a80000..0x2e80000 36M
         rootfs     0x2e80000..0x8000000 81M
```

Stock keeps a real A/B pair and **exactly one slot is attached at runtime**, so
the other is writable while the system runs. That is what makes the risky step
reversible: the installer writes the RAM initramfs into the *idle* slot, leaving
stock byte-for-byte intact in the slot it booted from. Only the final
`sysupgrade` is irreversible.

Four things that cost real time to learn, all of which the code depends on:

- **`flag_last_success` selects the slot, not `flag_boot_rootfs`.** The chooser
  at `0x4a922404` reads `flag_last_success` as `os_idx`, overrides it only when
  that slot's own failure counter has passed 5, and the caller writes
  `flag_boot_rootfs` back afterwards to *record* what it chose. Setting
  `flag_boot_rootfs` alone is a no-op the loader overwrites.
- **The `flag_try_sys{1,2}_failed` counters are the fallback budget**, and the
  loader increments the chosen slot's counter *before* each attempt. The pivot
  zeroes them, so the RAM system gets six tries before the loader returns to
  the slot stock is still in. (OpenWrt's `platform.sh` sets both to 8 at
  `sysupgrade` time — correct once OpenWrt is the only system, but it would
  disarm the safety net here.)
- **`fw_setenv` does not exist on stock**; `nvram` *is* the U-Boot environment
  (`0:APPSBLENV` decodes to exactly what `nvram show` prints).
- **`nvram flash_type` is the NAND device ID the bootloader measured** — `0x11`
  ESMT F50D1G41LB, `0xbe` Winbond W25N01KW, per the ID table in `0:APPSBL`.
  That is the NAND pre-flight: `dmesg` on a unit that has been up a while has
  already wrapped past the probe lines, and the two parts are identical in
  geometry.

`kexec` is absent from the stock kernel, so there is no zero-write rehearsal.
The idle slot is the substitute.

## The NAND gate

The flash is second-sourced, and a build without the right spinand entry does
not merely run slower — `spi-nand` fails to probe, `/proc/mtd` is empty, UBI
cannot attach, and ath11k gets no caldata. On a Wi-Fi-only install that is a
device you cannot talk to any more.

v1.7 ships `nand-support.txt`, generated from the kernel that was actually
built and keyed on the same `flash_type` byte the installer reads off the
device (`<flash_type>  <mfr:dev>  <part>`). `release.py` prefers it over its own
version table, so the gate is answered by the release rather than a hardcoded
assumption. It fails closed on anything it cannot identify.

```sh
python3 release.py --device rd03v2                                # latest release
python3 release.py --device rd03v2 --flash-type be --tag v1.6     # REFUSE
python3 release.py --device rd03v2 --flash-type be --tag v1.7     # PASS
python3 release.py --device rd03v2 --download --wifi --dest images
```

## First-boot configuration

The Wi-Fi and password arguments build a `sysupgrade -f` config tarball
containing one `uci-defaults` script that runs on the installed system's first
boot and then deletes itself.

It ships a script rather than a ready-made `/etc/config/wireless` because the
generated wireless config carries a `path` per radio describing where the phy
actually sits; a hand-written file that gets it wrong leaves the radios down
with no way in. The script edits whatever the board generated for itself, and
waits for the radios to register rather than assuming they already have.

Without these arguments an Ethernet install comes up as OpenWrt normally does:
radios off, no root password, reachable over Ethernet only. An install whose
final verification interface is Wi-Fi refuses to begin until it has a valid
permanent SSID, WPA2 passphrase, and country code; noninteractive callers must
supply all three explicitly. Selecting a wired final verification interface
keeps those settings optional.

## Reaching the installed system

**The installed system's LAN address is `192.168.1.1`, and so is a great many
people's own gateway.** If yours is one of them, that address is ambiguous on
your machine and the existing route may win on metric, causing commands to
reach the wrong device.

The scripts avoid the question by addressing the box on its IPv6 link-local,
and refuse outright to talk to this host's default gateway. Do the same by
hand:

```sh
# find it -- this returns only a neighbour that answers as dropbear
export IFACE=YOUR_ROUTER_INTERFACE
HOST=$(python3 -c \
    'import install, os; print(install.discover_linklocal(os.environ["IFACE"]))')

ssh "root@$HOST"                              # password: what you set
```

Sanity-check what answered before believing anything it tells you:

```sh
. /lib/functions.sh; board_name          # xiaomi,mi-router-ax3000t-v2
. /lib/upgrade/common.sh; rootfs_type    # overlay = NAND, tmpfs = RAM initramfs
```

Over Wi-Fi you can also just join the SSID you configured; the same ambiguity
applies to `192.168.1.1` from there, so prefer the link-local either way.

## Going back to stock

Each approved official stock image contains a 756-byte `HDR1` header, a
**complete raw UBI image**, and a 272-byte RSA signature. For the image marked
hardware-tested above, its two boot volumes were verified byte-for-byte against
a live unit's stock slot, so **the official signed download is the backup** for
that validated restore path.

The signature is only checked by U-Boot's TFTP recovery path; there is no secure
boot on the kernel, which is why writing the payload straight in works and needs
no cable.

Stock reads its own layout from MIBIB, which OpenWrt never touches, so once it
boots it sees the original three partitions again. The restore writes the stock
UBI at the start of `ubi_kernel` (exactly stock's slot 0) and **erases**
everything above it — erased flash is the state a factory unit's spare slot and
data area are in before first boot, which stock formats for itself.

It must run from the RAM initramfs: it erases `rootfs`, which on an installed
OpenWrt is the partition the running system is mounted from. `restore.py`
refuses unless `rootfs_type` is `tmpfs`; `revert.py` does the pivot for you.

## Safety properties

- **Nothing is written until every gate passes.** Model, `flash_type` against
  the release's own declaration, partition map, which slot is live cross-checked
  between `/proc/cmdline` and two nvram flags, and a hard interlock that refuses
  to target a partition with a UBI attached to it.
- **Every write is verified by read-back before it is trusted.** The kernel
  volume is compared byte-for-byte against the image — as a prefix plus `0xff`
  padding, because the volume is dynamic and reads back LEB-aligned.
- **Every detached command proves it started.** `start-stop-daemon -S` refuses
  to launch when it matches a running process, and it matches on the `-x`
  binary — pointing it at `/bin/sh` finds the command channel's own shell and
  exits 1 while looking like success.
- **The trigger is one-shot.** `cap_init` persists `NETMODE=whc_cap`, gating the
  sink until a factory reset. A reset re-arms it and leaves nothing else behind.
  `attach.py` exists so a driver bug does not cost one.
- **Discovery identifies before it authenticates.** A reply to a multicast ping
  only proves something is on the link; candidates must answer as dropbear, and
  the host's own default gateway is refused outright.

Run only against a device you own.

## License

GPL-2.0-only. See [`LICENSE`](../LICENSE).
