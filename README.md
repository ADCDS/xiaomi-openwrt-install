# xiaomi-router-install

Install OpenWrt on a supported Xiaomi router without UART or soldering. The
current release supports only the **Xiaomi AX3000T RD03v2** running stock
firmware **2.0.28**.

The installer uses the documented V1 → V2 `cab_meshd` chain to obtain root,
checks the exact board, NAND and partition layout, downloads the selected
OpenWrt release, boots its RAM installer, and writes the permanent image.

> Read [NOTICE](NOTICE) before continuing. Run this only on a router you own.

## Before starting

You need a Linux computer with Python 3.9 or newer:

```sh
sudo apt install sshpass openssl
```

Factory-reset the router and leave it at Xiaomi's setup wizard. **Do not finish
the web wizard.** Connect the computer to a LAN port or to the open factory
Wi-Fi network (`minet_rd03_*`). Keep the router on stable power.

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

That is the complete installation command. It defaults to the hardware-tested
[v1.11 release](https://github.com/ADCDS/openwrt-xiaomi-ax3000t-rd03v2/releases/tag/v1.11),
auto-detects whether the router is connected by Ethernet or Wi-Fi, downloads
and verifies the matching RAM and permanent images, backs up device-specific
partitions, and performs the full installation. It asks before each
irreversible write.

Ethernet uses the radio-silent RAM installer. Wi-Fi uses the beaconing RAM
installer shown below. Override detection with `--transport wired` or
`--transport wifi`.

If interface detection fails, append `--interface <name>`; for example,
`--interface enx00e04c125990`.

For a Wi-Fi-only installation, the router temporarily reboots into:

```text
SSID: OpenWrt-RD03v2-Installer
Password: rd03v2install
```

Join that network when the installer asks you to; the running process waits for
the router to reappear.

By default, the installed system uses `192.168.1.1`, has Wi-Fi disabled, and has
no root password. To configure Wi-Fi and a root password interactively during
the install, run:

```sh
python3 install.py standard --configure
```

To download and fully verify the selected v1.11 image pair without contacting
the router:

```sh
python3 install.py standard --dry-run
```

Use `--release latest` only when you deliberately want a newer release than the
pinned, tested default.

## After installation

Connect a cable to a LAN port and open <http://192.168.1.1>, or join the Wi-Fi
network configured with `--configure`. The first boot can take several minutes.

For interrupted runs, manual stages, stock restoration, flash-layout details,
and recovery procedures, see the [RD03v2 technical reference](docs/technical.md).

## Supported device

| Profile | Hardware | Tested stock | Default OpenWrt release |
|---|---|---|---|
| `rd03v2` | Xiaomi AX3000T RD03v2, Qualcomm IPQ5018 | 2.0.28 | v1.11 |

Other Xiaomi models may contain the same vulnerabilities, but their flash and
boot layouts are not interchangeable. The installer rejects every model that
does not have a hardware-tested profile.

Technical details and the vulnerability disclosure are in
[`xiaomi-ax3000t-cabmeshd-disclosure`](https://github.com/ADCDS/xiaomi-ax3000t-cabmeshd-disclosure).

## License

GPL-2.0-only. See [LICENSE](LICENSE).
