# xiaomi-router-install

Install OpenWrt on a supported Xiaomi router without UART or soldering, or
return an installed router to an approved stock image.

The installer uses the documented V1 → V2 `cab_meshd` chain to obtain root,
checks the exact board, NAND and partition layout, downloads the OpenWrt images
selected by the device profile, boots the RAM installer, and writes the
permanent image.

> Read [NOTICE](NOTICE) before continuing. Run this only on a router you own.

## Before starting

You need a Linux computer with Python 3.9 or newer:

```sh
sudo apt install sshpass openssl
```

Factory-reset the router and leave it at Xiaomi's setup wizard. **Do not finish
the web wizard.** Connect the computer to a LAN port or to the router's open
factory Wi-Fi network. Keep the router on stable power.

Use an isolated link. If using factory Wi-Fi, ensure no other client is joined.
The temporary root callback uses a per-run token and peer checks, while the
stock firmware cannot provide an authenticated encrypted channel for this
exploit stage.

Clone this repository:

```sh
git clone https://github.com/ADCDS/xiaomi-router-install.git
cd xiaomi-router-install
```

## Install OpenWrt

Choose one image:

```sh
# Standard image (recommended)
python3 install.py standard

# Experimental NSS acceleration
python3 install.py nss
```

The device profile supplies the tested default OpenWrt release. The installer
downloads and verifies the matching RAM and permanent images, detects whether
the router is connected by Ethernet or Wi-Fi, backs up device-specific
partitions, and asks before each irreversible write.

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
python3 install.py standard --transport wifi --interface WIFI_INTERFACE \
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
python3 install.py standard --configure
```

Download and fully verify the profile's default image set without contacting a
router:

```sh
python3 install.py standard --dry-run
```

Use `--release TAG` to select a particular release, or `--release latest` to
follow the release repository's current latest tag instead of the profile's
tested default.

If the connection is lost after `sysupgrade` starts, do not repeat the flash
stage. Use the private manifest printed by the original run to perform only
the final checks, optionally over a different interface:

```sh
python3 install.py --verify-only /path/to/resume.json \
    --verify-interface INTERFACE_NAME
```

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
| `python3 selftest.py` | Run checks that do not require a router. |

See the [technical reference](docs/technical.md) for interrupted runs, manual
stages, stock-image hashes, flash-layout details, and recovery procedures.

## Supported device

| Profile | Hardware | Install source | Return to stock |
|---|---|---|---|
| `rd03v2` | Xiaomi AX3000T RD03v2, Qualcomm IPQ5018 | Stock 2.0.28, hardware-tested | 2.0.28 hardware-tested; 2.0.12 hash-recognized but not hardware-tested |

The exact tested stock ROMs, release source, NAND support, layouts, and accepted
stock-image hashes live in [`devices.py`](devices.py). Other Xiaomi models may
contain the same vulnerabilities, but their flash and boot layouts are not
interchangeable. The installer rejects every model without a reviewed and
hardware-tested profile.

Technical details and the vulnerability disclosure are in
[`xiaomi-ax3000t-cabmeshd-disclosure`](https://github.com/ADCDS/xiaomi-ax3000t-cabmeshd-disclosure).

## License

GPL-2.0-only. See [LICENSE](LICENSE).
