# Update or restore a router running OpenWrt

**English** · [Русский](maintenance.ru.md) · [简体中文](maintenance.zh-CN.md) · [Português (Brasil)](maintenance.pt-BR.md)

These steps are for the Xiaomi AX3000T RD03v2 running the
[OpenWrt port](https://github.com/ADCDS/openwrt-xiaomi-ax3000t-rd03v2).
See the [README](../README.md#before-starting) for host requirements and
[hardware validation](technical.md#hardware-validation) for tested scenarios.
Run the commands on your computer from the repository directory. On Windows,
use `py -3` in place of `python3`.

## Update an installed system

```sh
python3 updater.py --root-password 'YOUR_OPENWRT_PASSWORD'
```

The updater selects the latest published release and keeps the current settings
by default. It saves a configuration backup to a private `update-*` folder
before writing. It logs in over SSH with a password and stops before writing
if the router accepts SSH keys only.

Updates between v2 releases use an in-place `sysupgrade`. Releases v2.0.0
through v2.1.2 can incorrectly refuse an in-place update. When that check is
the only reason `sysupgrade -T` fails, the updater passes `-F`. Other failures
stop the update. Updates from or to a v1.x release use the RAM installer,
as in the [manual reflash procedure](https://github.com/ADCDS/openwrt-xiaomi-ax3000t-rd03v2/blob/main/docs/no-uart-reflash.md).
`--via-ram-installer` selects that route between v2 releases too and reformats
both UBI partitions.

| Option | Use |
|---|---|
| `--release TAG` | Select a specific release. |
| `--dry-run` | Download and verify the images without contacting the router. |
| `--offline` | Use verified cached images for an explicit release tag. |
| `--verify-interface IFACE` | Select the interface for the final check. |
| `--no-keep-settings` | Start with a clean configuration. Supply a wired `--verify-interface IFACE` if the final check would otherwise use Wi-Fi. A wired update defaults to the router-facing interface. |
| `--wifi-ssid NAME` | Name the network to rejoin when continuing from the RAM installer and verifying over Wi-Fi. |
| `--device`, `--images`, `--outdir` | Select a device profile, image cache, or output directory. |
| `--yes` | Answer the confirmation prompts up front. |

The router has one installed kernel, with no A/B fallback. A torn write may
require UART or stock TFTP recovery. On the RAM-installer route, the tool
overwrites the installed kernel before booting into RAM. If a run is
interrupted at that point and the RAM installer is reachable, run the updater
again with `--config-backup` and the archive it saved. Use
`--recover-stale-lock` only after inspecting an inactive writer lock.

## Return to stock

Download an [approved Xiaomi stock image](technical.md#getting-the-images).
Choose the newest approved image that is at least as recent as the last stock
firmware the router ran.

Replace `YOUR_ROUTER_INTERFACE` with the name of the network interface facing
the router. On Linux, `nmcli device status` lists the interfaces.

```sh
python3 revert.py --device rd03v2 /path/to/approved-stock-image.bin --interface "YOUR_ROUTER_INTERFACE"
```

`revert.py` verifies the stock image and the RAM image pair, boots the router
into RAM, restores Xiaomi firmware, and waits for the stock system to report
its hardware, ROM version, and setup wizard status.

The selected interface determines whether the tool uses Ethernet or Wi-Fi.
Use `--transport wired` to override detection. Over Wi-Fi, join the temporary
RAM network when the tool prints its SSID and password. Keep this link isolated
until the stock firmware boots.

The RAM images default to the latest published release. Use `--release TAG`
to select a specific release and `--offline` to use its verified cache. Add
`--root-password 'YOUR_OPENWRT_PASSWORD'` if the installed system has a root
password. Use `--dry-run` to check the images and the router without writing the
firmware.

Use `revert.py` for normal restoration. The lower-level `restore.py` requires
a router already booted from a RAM initramfs.
