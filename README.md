# xiaomi-openwrt-install

**English** · [Русский](README.ru.md)

Install OpenWrt on a supported Xiaomi router without UART or soldering, or
return an installed router to an approved stock image.

The installer uses the [documented `cab_meshd` exploit chain](https://github.com/ADCDS/xiaomi-ax3000t-cabmeshd-disclosure#combined-v1--v2-capuci-exploit) to obtain root,
checks the exact board, NAND and partition layout, downloads the OpenWrt images
selected by the device profile, boots the RAM installer, and writes the
permanent image.

> Read [NOTICE](NOTICE) before continuing. Run this only on a router you own.

## Before starting

You need a Linux or Windows 10/11 computer with Python 3.9 or newer. On Linux:

```sh
sudo apt install sshpass openssl
```

On Windows, see [Windows 10/11](#windows-1011) below.

Factory-reset the router and leave it at Xiaomi's setup wizard. **Do not finish
the web wizard.** Connect the computer to a LAN port or to the router's open
factory Wi-Fi network. Keep the router on stable power.

Use an isolated link. If using factory Wi-Fi, ensure no other client is joined.
The temporary root callback uses a per-run token and peer checks, while the
stock firmware cannot provide an authenticated encrypted channel for this
exploit stage.

Clone this repository:

```sh
git clone https://github.com/ADCDS/xiaomi-openwrt-install.git
cd xiaomi-openwrt-install
```

### Windows 10/11

Native support, no WSL. Install Python from
[python.org](https://www.python.org/downloads/windows/), including the `py`
launcher, then open PowerShell in the repository folder:

```powershell
py -3 -m pip install -r requirements-windows.txt
py -3 install.py --list-interfaces
py -3 install.py --interface "Ethernet"
```

Use `py -3` wherever this document says `python3`. Pass the adapter name from
`--list-interfaces`, or its numeric index, which is easier for names with spaces
or Cyrillic. Install the dependencies while you still have Internet access: the
factory network may not have any. `install.cmd` is an equivalent launcher.

`sshpass`, `ip`, `nmcli` and OpenSSL are not needed; PowerShell 5.1 (included
with Windows) reports the network state, and the Python dependencies provide
password SSH, legacy SCP and root-password hashing. Keep IPv6 enabled on the
selected adapter: rediscovery after a reboot uses link-local addresses. Join the
temporary and permanent Wi-Fi networks by hand when prompted.

If the router's callback never arrives (`no root shell`), first check that you
are on a **LAN** port or the factory Wi-Fi with a `192.168.31.x` address and that
no VPN or other network competes for the route. Then allow `python.exe` through
Windows Defender Firewall for inbound TCP 8000, 4444 and 4445 from the router, in
an elevated PowerShell. Factory links may be classified as Public, so a
Private-only exception may not work. Do not disable the firewall globally:

```powershell
$python = py -3 -c "import sys; print(sys.executable)"
New-NetFirewallRule -DisplayName "Xiaomi OpenWrt installer" -Direction Inbound -Action Allow -Program $python -Protocol TCP -LocalPort 8000,4444,4445 -RemoteAddress 192.168.31.1 -Profile Any
# Remove it when finished:
Get-NetFirewallRule -DisplayName "Xiaomi OpenWrt installer" | Remove-NetFirewallRule
```

Keep the run output somewhere private. Backups, configuration archives and
`resume.json` hold device data and session secrets, and on Windows they inherit
the folder's NTFS permissions rather than getting POSIX modes.

**How far this has been tested.** The port is by
[alexsys87](https://github.com/alexsys87/xiaomi-openwrt-install), who confirmed a
complete native install on a physical RD03v2: Windows 10, Wi-Fi, stock 2.0.28 to
release v1.14 standard, on 2026-10-11. This repository carries that port on newer
installer code. The current code, v2.x releases, wired installs and `revert.py`
have automated coverage on Windows but have not yet been run from Windows against
a real router. Keep a Linux machine or a UART adapter within reach, and please
report how it went.

## Install OpenWrt

```sh
python3 install.py
```

This installs the latest release of the
[RD03v2 port](https://github.com/ADCDS/openwrt-xiaomi-ax3000t-rd03v2), with NSS hardware
offload. For an image without NSS offload, install the port's last v1.x standard
build instead: `python3 install.py standard --release v1.14`.

By default, the installer selects the latest published release from the device
profile's firmware repository, excluding drafts and prereleases. It downloads
and verifies the matching RAM and permanent images, detects whether the router
is connected by Ethernet or Wi-Fi, backs up device-specific partitions, and asks
before each irreversible write. New firmware releases become the default
automatically.

Override connection detection with `--transport wired` or `--transport wifi`.
If route-based interface detection fails, append `--interface INTERFACE_NAME`.

During a Wi-Fi install, the router temporarily reboots into a RAM installer
network. `install.py` prints its SSID and password before the reboot and waits
while you join it. By default, final verification uses that same Wi-Fi
interface, so before doing anything to the router the installer requires the
SSID, passphrase, and country code for the permanent system. An interactive run
prompts for missing values; automation must pass `--wifi-ssid`, `--wifi-key`,
and `--wifi-country`. After `sysupgrade` makes the temporary network disappear,
join that permanent SSID. The installer never prints its passphrase.

To keep permanent Wi-Fi disabled, select an Ethernet interface for the final
check and connect it when the installer asks:

```sh
python3 install.py --transport wifi --interface WIFI_INTERFACE \
    --verify-interface ETHERNET_INTERFACE
```

Both paths rediscover the router and verify the board, persistent overlay, and
partition sizes.

The temporary credentials are fixed and public, and root has no password in
the RAM system. Keep the link isolated until the permanent system has booted.

When final verification is over Ethernet, omitting configuration arguments
leaves OpenWrt Wi-Fi disabled and the root password unset. Configure both
interactively during installation with:

```sh
python3 install.py --configure
```

Prompts and instructions appear in Russian on a Russian system. Choose explicitly
with `--lang ru` or `--lang en`; low-level diagnostics stay in English.

Download and fully verify the latest release's image set without contacting a
router:

```sh
python3 install.py --dry-run
```

Use `--release TAG` to select a particular release. `--release latest` explicitly
selects the default behavior. Each installation records the resolved tag and
image hashes in its private resume manifest, so continuing an interrupted run
keeps the original release even if a newer one has been published.

Offline installs require an explicit tag and its verified cached images:

```sh
python3 install.py --release v2.0.0 --dry-run
python3 install.py --release v2.0.0 --offline
```

If GitHub is unavailable, an explicitly selected tag can also use its verified
cache. Selecting `latest` requires GitHub access.

If the connection is lost after `sysupgrade` starts, do not repeat the flash
stage. Use the private manifest printed by the original run to perform only
the final checks, optionally over a different interface:

```sh
python3 install.py --verify-only /path/to/resume.json \
    --verify-interface INTERFACE_NAME
```

## Update an installed system

```sh
python3 updater.py --root-password 'YOUR_OPENWRT_PASSWORD'
```

This updates an installed OpenWrt to the latest release of the port, keeping its
settings like `sysupgrade` does. Use `--release TAG` for a specific one,
`--no-keep-settings` for a clean install, and `--dry-run` to only download and
verify the images. An in-place `sysupgrade` bricks this board, so the update goes
through the RAM installer, as
[the firmware's manual procedure](https://github.com/ADCDS/openwrt-xiaomi-ax3000t-rd03v2/blob/main/docs/no-uart-reflash.md)
does. It saves a config backup in a private `update-*` folder before writing.

There is no A/B fallback here: the installed kernel is overwritten on the way into
RAM, and the recovery net for a torn write is UART. If the run is interrupted after
that point, reach the RAM installer and run the command again with
`--config-backup` and the backup it saved. It logs in by password, so a router set
to SSH keys only (`PasswordAuth off`) is refused before anything is written.

The update reuses the code the install and revert paths run on real hardware, but
the update as a whole **has not yet been run on a real router**. Keep a UART adapter
within reach, and please report how it went.

## Web wizard

```sh
python3 gui/app.py
```

A local step-by-step wizard over these tools, in English and Russian, with a dry
run, a stage tracker, and a live log. See [gui/README.md](gui/README.md).

## Return to stock

Download an official stock image whose SHA-256 is approved by the selected
device profile. The [technical reference](docs/technical.md#getting-the-images)
lists the approved images for the enabled profiles.

From an installed OpenWrt system, run:

```sh
nmcli device status
IFACE=YOUR_ROUTER_INTERFACE
python3 revert.py --device rd03v2 /path/to/approved-stock-image.bin \
    --interface "$IFACE"
```

`revert.py` verifies the stock image, downloads and verifies the profile's RAM
image pair, pivots the router into RAM, restores stock, and confirms that the
setup wizard returns. It infers Ethernet or Wi-Fi from the selected interface.
The RAM images default to the latest published release; use `--release TAG` to
select a specific release, and add `--offline` to use its verified cache.
During a Wi-Fi revert, join the temporary RAM network when `revert.py` prints
its SSID and password. The useful overrides are:

```sh
python3 revert.py --device rd03v2 /path/to/approved-stock-image.bin \
    --transport wired --interface "$IFACE"

python3 revert.py --device rd03v2 /path/to/approved-stock-image.bin \
    --interface "$IFACE" --root-password 'YOUR_OPENWRT_PASSWORD'

python3 revert.py --device rd03v2 /path/to/approved-stock-image.bin \
    --interface "$IFACE" --dry-run
```

Use `revert.py` for normal restoration. `restore.py` is the lower-level writer
and refuses to run unless the router is already booted from a RAM initramfs.

## Supporting tools

| Command | Purpose |
|---|---|
| `python3 release.py --device rd03v2` | Inspect the latest release and its required assets. |
| `python3 release.py --device rd03v2 --download --wifi --dest images` | Download and verify a release image set. |
| `python3 probe.py --device rd03v2` | Collect hardware and flash-layout evidence without raw firmware-partition writes. It initializes or reboots stock as needed, modifies stock control state, and consumes the one-shot trigger. |
| `python3 attach.py --bind OPERATOR_ADDRESS --peer ROUTER_ADDRESS --session-token TOKEN 'id'` | Reattach to the authenticated callback from an interrupted run. |
| `python3 restore.py --device rd03v2 /path/to/approved-stock-image.bin --discover INTERFACE_NAME` | Run the low-level stock writer from an already-booted RAM system. |
| `python3 ubiparse.py /path/to/ubi-dump.bin` | Inspect a raw UBI dump offline. |
| `python3 selftest.py` | Run checks that do not require a router (Linux). |
| `python3 updater.py --dry-run` | Download and verify an update's images without contacting a router. |
| `python3 -m unittest test_hostnet test_windows test_i18n` | Run the Windows and language checks; needs `requirements-windows.txt`. |

`probe.py` has not been tested on Windows, and its automatic Wi-Fi reconnect is
Linux-only.

See the [technical reference](docs/technical.md) for interrupted runs, manual
stages, stock-image hashes, flash-layout details, and recovery procedures.

## Supported device

| Profile | Hardware | Install source | Return to stock |
|---|---|---|---|
| `rd03v2` | Xiaomi AX3000T RD03v2, Qualcomm IPQ5018 | Stock 2.0.28 and 2.0.12, hardware-tested | 2.0.28 and 2.0.12 hardware-tested |

The exact tested stock ROMs, release source, NAND support, layouts, and accepted
stock-image hashes live in [`devices.py`](devices.py). Other Xiaomi models may
contain the same vulnerabilities, but their flash and boot layouts are not
interchangeable. The installer rejects every model without a reviewed and
hardware-tested profile.

Technical details and the vulnerability disclosure are in
[`xiaomi-ax3000t-cabmeshd-disclosure`](https://github.com/ADCDS/xiaomi-ax3000t-cabmeshd-disclosure).

## License

GPL-2.0-only. See [LICENSE](LICENSE).
