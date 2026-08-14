# ax3000t-ota-install

Install OpenWrt on a **Xiaomi AX3000T (RD03v2)** over the air — no serial
adapter, no soldering, and no LAN cable — and put pristine stock back the same
way.

The port's documented install needs a USB↔UART adapter on the board's pads plus
a TFTP server over Ethernet, because the bootloader is locked. This drives it
instead through a pre-auth root RCE in the stock firmware's mesh daemon, pivots
into the OpenWrt RAM initramfs, and runs the sanctioned `sysupgrade` from there.

> **Private, and it should stay that way for now.** `chain.py` is a working
> exploit for a vulnerability under coordinated disclosure with the vendor.
> Publishing this repo is the publication event for that chain — a disclosure
> decision, not just an engineering one.

Verified end to end on hardware: factory unit → configured OpenWrt on NAND, and
back to factory 2.0.28, repeatedly, in both directions.

## Two commands

```sh
# stock -> OpenWrt on NAND, with WiFi and a root password already set
python3 install.py --host 192.168.31.1 --stage all --discover <iface> \
    --wifi-ssid '<SSID>' --wifi-key '<passphrase>' \
    --wifi-country <CC> --root-password '<password>'

# OpenWrt -> pristine stock 2.0.28
python3 revert.py miwifi_rd03v2_2.0.28.bin --discover <iface> \
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
| `install.py` | the installer: pre-flight → pivot into the idle A/B slot → `sysupgrade` from the RAM system, with optional first-boot config |
| `revert.py` | one command back to stock: pivot into RAM if needed, then restore |
| `restore.py` | the restore itself, from Xiaomi's own signed image. Runs only from RAM |
| `chain.py` | stock 2.0.28 → root: wizard completion, V1 admin takeover, the `encryption` plant, the `cap_init` trigger |
| `channel.py` | operator side: stager delivery over HTTP, a durable root command channel, a bulk file sink |
| `release.py` | fetch + sha256-verify a release, and refuse one whose kernel cannot drive this unit's NAND |
| `ubiparse.py` | offline UBI parser — turns a raw MTD dump into a volume table |
| `probe.py` | read-only fact-finding run against a stock unit; how the layout below was established |
| `attach.py` | re-attach to a stager still dialling in, after a driver crash — the trigger is one-shot, so this saves a factory reset |
| `selftest.py` | everything testable without the router (178 checks) |
| `installer-wifi.rc.local.patch` | the port change that makes the RAM initramfs beacon (shipped in v1.7) |

`chain.py` re-implements the exploit rather than importing the disclosure
package, so this tree carries no vendor-only material.

```sh
python3 selftest.py     # run this first; needs no hardware
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
python3 release.py                                # what the latest release ships
python3 release.py --flash-type be --tag v1.6     # REFUSE: Winbond needs >= v1.7
python3 release.py --flash-type be --tag v1.7     # PASS
python3 release.py --download --wifi --dest images
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
