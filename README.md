# xiaomi-openwrt-install

**English** · [Русский](README.ru.md) · [简体中文](README.zh-CN.md) · [Português (Brasil)](README.pt-BR.md) · [Technical reference](docs/technical.md)

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

If a run stops partway it prints the command that continues it, once it has
passed pre-flight. A failure before that prints none: no firmware has been
written yet, so restarting is safe if the one-shot trigger was not spent. The
tool cannot tell you which from the log alone; if the trigger was already spent,
the exploit refuses and only a factory reset brings it back.

In a virtual machine, give Windows a bridged or macvtap network card. USB
passthrough of an RTL8153 adapter froze on sustained sends in testing, and the
exploit's download needs those to work.

**How far this has been tested** on a real RD03v2. The port is by
[alexsys87](https://github.com/alexsys87/xiaomi-openwrt-install).

| Scenario | Result |
|---|---|
| Wi-Fi, stock 2.0.28 to v1.14 standard, on Windows 10 | Installed on 2026-10-11 (alexsys87), on older code |
| Cable, stock 2.0.28 to v2.1.2 with a root password, on Windows 11 25H2 in a VM | Installed on 2026-11-07, on this code, including continuing an interrupted run |
| `updater.py` on Linux, restoring a saved config | Ran once on 2026-11-07. Every write stage worked; the final password login could not complete, because the restored config disables it |
| `updater.py` from Windows, or against a router that keeps password login | Automated tests only, not yet run against a real router |
| Wi-Fi with this code, and `revert.py` | Automated tests only, not yet run against a real router |

Keep a Linux machine or a UART adapter within reach, and please report how it went.

`updater.py` and the web wizard run on Windows 10/11 as well as Linux. What is
still unproven on real hardware is listed in the table above.

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
and `--wifi-country`. Secrets may also arrive through `RD03V2_ROOT_PASSWORD` and
`RD03V2_WIFI_KEY`, so that they never appear in a process argument list. A
variable is consumed on startup when the matching flag is absent — with both set,
the flag wins and the variable stays in the environment. The web wizard passes
them this way for exactly that reason. After `sysupgrade` makes the temporary network disappear,
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

Prompts and instructions follow your system language: English, Russian, Simplified
Chinese or Brazilian Portuguese. Choose explicitly with `--lang en`, `ru`, `zh-CN`
or `pt-BR`; low-level diagnostics stay in English.

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

# a Wi-Fi install caches a different image set
python3 install.py --release v2.0.0 --transport wifi --dry-run
python3 install.py --release v2.0.0 --transport wifi --offline
```

If GitHub is unavailable, an explicitly selected tag can also use its verified
cache. Selecting `latest` requires GitHub access. A Wi-Fi install needs
`--transport wifi` for its own cache; the wired run caches only the radio-silent
images.

`--root-password PASS` sets the installed system's root password without the
interactive `--configure` prompts, and `--attacker ADDRESS` pins the callback
address when automatic selection would pick the wrong one on a multihomed host.

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

Useful flags: `--verify-interface IFACE` chooses the interface for the final
check and is required with `--no-keep-settings` only when that check would run
over Wi-Fi — a wired update defaults it to the router-facing interface;
`--wifi-ssid NAME` names the network to rejoin when continuing a run that stopped
in the RAM installer, whose own wireless config is the installer's; `--offline`
uses only the verified cache; `--yes` answers the confirmations up front;
`--device`, `--images` and `--outdir` pick a profile, an image cache and an output
directory; `--recover-stale-lock` clears an interrupted pivot state once you have
inspected it.

There is no A/B fallback here: the installed kernel is overwritten on the way into
RAM, and the recovery net for a torn write is UART. If the run is interrupted after
that point, reach the RAM installer and run the command again with
`--config-backup` and the backup it saved. It logs in by password, so a router set
to SSH keys only (`PasswordAuth off`) is refused before anything is written.

So far this has run on a real router **once**: from Linux over a cable, v2.1.2 with
`--config-backup`, on 2026-11-07. Every write stage worked; the final password
login could not complete, because the restored config turns password login off.
The tool warns about this in advance, and the result was verified with an SSH key
instead. A plain update of a router that keeps password login, and any update from
Windows, have not been run at all. Keep a UART adapter within reach, and please
report how it went.

## Web wizard

```sh
python3 gui/app.py
```

A local step-by-step wizard over these tools, in English, Russian, Simplified
Chinese and Brazilian Portuguese, with a dry run, a stage tracker, and a live
log. See [gui/README.md](gui/README.md).

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
image pair, pivots the router into RAM, restores stock, and waits for the stock system to
answer, reporting its hardware, ROM and whether the setup wizard is waiting. It infers Ethernet or Wi-Fi from the selected interface.
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
