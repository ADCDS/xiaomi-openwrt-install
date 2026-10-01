# `xiaomi-router-install` RD03v2 technical reference

This document preserves the implementation details, recovery commands, flash
layout, and advanced controls. Start with the concise [README](../README.md)
for the supported installation command.

Install OpenWrt over the air on supported Xiaomi routers — no serial adapter,
no soldering, and no LAN cable — and put pristine stock back the same way.

## Supported devices

| profile | hardware | tested stock | install | return to stock |
|---|---|---|---|---|
| `rd03v2` | Xiaomi AX3000T RD03v2 (Qualcomm IPQ5018) | 2.0.28 | Hardware-validated V1 → V2 root, RAM pivot, and NAND install | Xiaomi 2.0.28 hardware-validated; signed 2.0.12 image also recognized |

Only `rd03v2` is enabled. The mesh vulnerabilities exist in firmware shared by
other Xiaomi models, but that does not establish their flash layout, boot
flags, OpenWrt image, or restoration procedure. Every additional model needs
its own reviewed and hardware-tested profile before this tool will accept it.

The port's documented install needs a USB↔UART adapter on the board's pads plus
a TFTP server over Ethernet, because the bootloader is locked. This drives it
instead through a pre-auth root RCE in the stock firmware's mesh daemon, pivots
into the OpenWrt RAM initramfs, and runs the sanctioned `sysupgrade` from there.

> ### Published 2026-09-28
>
> `chain.py` is a working exploit for a vulnerability that is **unpatched** as of
> this date. This repository is the owner-facing half of
> [`xiaomi-ax3000t-cabmeshd-disclosure`](https://github.com/ADCDS/xiaomi-ax3000t-cabmeshd-disclosure):
> the stock firmware offers no supported path off itself, so getting OpenWrt onto
> the device *is* the exploit chain, and the installer and the exploit cannot be
> separated. The advisory, the PoC and the timeline are there.
>
> Read [`NOTICE`](NOTICE) before running anything, and note that this install is
> effectively one-way — see [Going back to stock](#going-back-to-stock).

Verified end to end on hardware: factory unit → configured OpenWrt on NAND, and
back to factory 2.0.28, repeatedly, in both directions.

## Prerequisites

A Linux laptop, Python **3.9+**, and:

```sh
sudo apt install sshpass openssl        # or your distro's equivalent
```

`sshpass` is not optional. Without it the scripts fall back to an interactive
`ssh` password prompt that fails as `Permission denied` — which looks exactly
like a wrong password rather than a missing package. `openssl` is used to hash
the root password. `ping6`, `ip`, `ssh`, `scp` and `tar` are assumed present.

**The laptop needs an address on the router's subnet.** Stock serves DHCP on
`192.168.31.0/24`; the OpenWrt RAM system is reached by IPv6 link-local, so it
does not care. Either:

```sh
# over Wi-Fi: join the factory SSID (open, named minet_rd03_* or similar)
nmcli dev wifi connect '<factory SSID>'

# or over a cable, into any LAN port
nmcli con add type ethernet ifname <iface> con-name rd03v2 \
    ipv4.method auto ipv6.method link-local autoconnect no
nmcli con up rd03v2
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
| **2.0.28** (newest) | [`miwifi_rd03v2_firmware_31bf9_2.0.28.bin`](https://cdn.cnbj1.fds.api.mi-img.com/xiaoqiang/rom/rd03v2/miwifi_rd03v2_firmware_31bf9_2.0.28.bin) | `3138342e564c7d7482fde4a90e1778830180f0eac15e1de5f3ad269f9ba9940f` |
| 2.0.12 | [`miwifi_rd03v2_firmware_69eec_2.0.12.bin`](https://cdn.cnbj1.fds.api.mi-img.com/xiaoqiang/rom/rd03v2/miwifi_rd03v2_firmware_69eec_2.0.12.bin) | `be7af0e551d440a96757fe885dd775580fd8362addefb594b114f218ccc786c3` |

Genuine, Xiaomi-signed, served from Xiaomi's own CDN. `restore.py` refuses any
image whose hash is not one of these unless you pass `--yes`, so check it:

```sh
sha256sum miwifi_rd03v2_firmware_31bf9_2.0.28.bin
```

Take 2.0.28 unless you have a reason not to: the bootloader's anti-rollback
refuses only images *older* than the version the unit last ran.

## Two commands

```sh
# stock -> OpenWrt on NAND, with WiFi and a root password already set
python3 install.py --device rd03v2 --host 192.168.31.1 --stage all --discover <iface> \
    --wifi-ssid '<SSID>' --wifi-key '<passphrase>' \
    --wifi-country <CC> --root-password '<password>'

# OpenWrt -> pristine stock 2.0.28
python3 revert.py --device rd03v2 miwifi_rd03v2_2.0.28.bin --discover <iface> \
    --root-password '<the installed system's password>'
```

Both are re-runnable: every step re-checks the state it needs instead of
assuming the previous run got there. Add `--yes` to skip the prompts at the
irreversible points, `--dry-run` (revert) to see the plan without touching
anything.

`<iface>` is the interface facing the router — a USB Ethernet adapter, or your
Wi-Fi interface joined to the installer SSID. Discovery finds the box by its
IPv6 link-local, which sidesteps the fact that OpenWrt's `192.168.1.1` is also
a very common gateway address.

## What is here

| file | role |
|---|---|
| `devices.py` | device-profile registry and the complete RD03v2 identity, release, NAND, partition and restore safety boundary |
| `install.py` | the installer: pre-flight → pivot into the idle A/B slot → `sysupgrade` from the RAM system, with optional first-boot config |
| `revert.py` | one command back to stock: pivot into RAM if needed, then restore |
| `restore.py` | the restore itself, from Xiaomi's own signed image. Runs only from RAM |
| `chain.py` | stock 2.0.28 → root: wizard completion, V1 admin takeover, the `encryption` plant, the `cap_init` trigger |
| `channel.py` | operator side: stager delivery over HTTP, a durable root command channel, a bulk file sink |
| `release.py` | fetch + sha256-verify a release, and refuse one whose kernel cannot drive this unit's NAND |
| `ubiparse.py` | offline UBI parser — turns a raw MTD dump into a volume table |
| `probe.py` | read-only fact-finding run against a stock unit; how the layout below was established |
| `attach.py` | re-attach to a stager still dialling in, after a driver crash — the trigger is one-shot, so this saves a factory reset |
| `selftest.py` | everything testable without the router (179 checks, 182 once you have a release artifact, 183 with its matching `.itb`) |
| `installer-wifi.rc.local.patch` | the port change that makes the RAM initramfs beacon (shipped in v1.7 and later) |
| `LICENSE` | GPL-2.0-only |
| `NOTICE` | authorised-use, one-way-install and no-warranty terms — **read first** |

`chain.py` re-implements the exploit rather than importing the disclosure
package, so this tree stands on its own; the two repositories are independent
implementations of the same chain, which is also what makes one a useful check
on the other.

```sh
python3 selftest.py                          # 179 checks, no hardware, no network

# three more run against a real release artifact, if you have one:
python3 release.py --device rd03v2 --download --wifi --dest images
RD03V2_IMAGES=images/v1.11 python3 selftest.py     # 182

# a fourth check compares the kernel volume against the .itb it wraps, so it
# needs that file too -- release.py fetches the .ubi and the sysupgrade only:
RD03V2_IMAGES=images/v1.11 python3 selftest.py     # 183
```

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

Without these arguments the installed system comes up as OpenWrt normally does:
radios off, no root password, reachable over Ethernet only.

## Reaching the installed system

**The installed system's LAN address is `192.168.1.1`, and so is a great many
people's own gateway.** If yours is one of them, that address is ambiguous on
your machine and your existing route almost certainly wins on metric — so you
will silently talk to your own gateway and draw conclusions about the router
from it. This is not hypothetical: a reviewer handed only this repo did exactly
that, found `dropbear` there offering only `publickey`, and concluded the root
password this tool had just set was broken. It was not; they were logged into
something else.

The scripts avoid the question by addressing the box on its IPv6 link-local,
and refuse outright to talk to this host's default gateway. Do the same by
hand:

```sh
# find it -- this returns only a neighbour that answers as dropbear
python3 -c "import install; print(install.discover_linklocal('<iface>'))"

ssh root@fe80::xxxx:xxxx:xxxx:xxxx%<iface>          # password: what you set
```

Sanity-check what answered before believing anything it tells you:

```sh
. /lib/functions.sh; board_name          # xiaomi,mi-router-ax3000t-v2
. /lib/upgrade/common.sh; rootfs_type    # overlay = NAND, tmpfs = RAM initramfs
```

Over Wi-Fi you can also just join the SSID you configured; the same ambiguity
applies to `192.168.1.1` from there, so prefer the link-local either way.

## Going back to stock

No backup is needed. `miwifi_rd03v2_*_2.0.28.bin` is a 756-byte `HDR1` header,
a **complete raw UBI image**, and a 272-byte RSA signature — and that UBI holds
exactly the two volumes stock boots from. Verified against a live unit: both
byte-identical to what was physically on its stock slot, so **the official
signed download is the backup**.

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

## Not done

- `--wifi-country` is optional and should be mandatory whenever the radios are
  enabled: without a regulatory domain the installer AP runs under the world
  domain, which is the objection upstream raises against enabled-by-default
  radios independently of security.
- The installer beacon should drop to 2.4 GHz only, for the same reason.
- The placeholder-shaped arguments in the docs have been bitten once already;
  keep them unmistakable.

## License

GPL-2.0-only. See [`LICENSE`](LICENSE).
