# xiaomi-openwrt-install

**English** · [Русский](README.ru.md) · [简体中文](README.zh-CN.md) · [Português (Brasil)](README.pt-BR.md) · [Technical reference](docs/technical.md)

Install OpenWrt on a supported Xiaomi router without UART or soldering, or
return an installed router to an approved stock image.

The installer uses the [documented `cab_meshd` exploit chain](https://github.com/ADCDS/xiaomi-ax3000t-cabmeshd-disclosure#combined-v1--v2-capuci-exploit) to obtain root,
checks the exact board, NAND and partition layout, downloads the OpenWrt images
selected by the device profile, boots the RAM installer, and writes the
permanent image.

> Read [NOTICE](NOTICE) before continuing. Run this only on a router you own.

## Supported device

The Xiaomi AX3000T **RD03v2** (Qualcomm IPQ5018) is the only supported model.
Installation and restoration were tested with stock firmware **2.0.28** and
**2.0.12**. See the [device profile](devices.py).

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

### Verify an installed system with an SSH key

If the web UI and manual key-based SSH login work, retry verification without
flashing again:

```sh
python3 install.py --verify-only /path/to/install/resume.json \
  --verify-interface en0 --verify-host 192.168.1.1 \
  --verify-ssh-key ~/.ssh/id_ed25519
```

Replace the interface and private key path. Use `agent` instead of the path for
ssh-agent or your default OpenSSH configuration. Load encrypted keys with
`ssh-add` first. This mode requires system OpenSSH and a trusted host key for
the exact target address: confirm it with a manual SSH login first. It checks
the board, permanent partitions, release revision and recorded reboot, then
updates the local resume manifest. These options require `--verify-only` and
do not change authentication during flashing. If your `~/.ssh/config` names
the router by an alias, add `--verify-ssh-alias NAME`; that Host must resolve
to `--verify-host` and log in as `root`.

### macOS (experimental)

The wired install and revert have been run end to end on macOS 14 (Sonoma)
against an AX3000T; macOS support is still experimental and the Wi-Fi transport
is untested. Homebrew supports only
Apple Silicon Macs; on Intel Macs use MacPorts. Python 3.9 or newer is needed;
the Xcode Command Line Tools (`xcode-select --install`) provide one.

Apple Silicon (Homebrew):

```sh
brew install python iproute2mac sshpass openssl@3
export PATH="$(brew --prefix openssl@3)/bin:$PATH"
```

Intel (MacPorts):

```sh
sudo port install iproute2mac sshpass openssl3
export PATH="/opt/local/bin:/opt/local/sbin:$PATH"
```

Then list the interfaces:

```sh
python3 install.py --list-interfaces
```

Select the router-facing interface from that list, then run, for example,
`python3 install.py --interface en0`. Do not assume en0 is Ethernet. Launch the
GUI from the same terminal to inherit PATH. Apple's LibreSSL does not support
`openssl passwd -6`. Join Wi-Fi manually in System Settings; Linux `nmcli`
automation is unavailable. This approach does not require paramiko or passlib.

To update an installed system from a Mac with the SSH key in your
`~/.ssh/config`, see [Update with your SSH key](#update-with-your-ssh-key).
Key login needs neither `sshpass` nor OpenSSL 3.

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

The updater and web wizard also support Windows 10/11 and Linux. See
[hardware validation](docs/technical.md#hardware-validation) for tested scenarios.

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
join the permanent SSID. See how to
[supply passwords through environment variables](docs/technical.md#first-boot-configuration).

To keep permanent Wi-Fi disabled, select an Ethernet interface for the final
check and connect it when the installer asks:

```sh
python3 install.py --transport wifi --interface WIFI_INTERFACE \
    --verify-interface ETHERNET_INTERFACE
```

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

Use `--dry-run` to download and verify images without contacting a router.
`--offline` requires an explicit release tag and a verified cache for the
chosen connection type. See [verifying OpenWrt releases](docs/technical.md#verifying-openwrt-releases).

`--attacker ADDRESS` pins the callback address when automatic selection would
pick the wrong one on a multihomed host.

If the connection drops after `sysupgrade` starts, switch to
[read-only verification](docs/technical.md#verify-after-a-disconnect) to avoid
flashing twice.

## Update an installed system

Use `updater.py` to update OpenWrt while keeping its settings. See the
[update guide](docs/maintenance.md#update-an-installed-system) for commands,
release selection, configuration backups, and recovery.

Before the backup, `updater.py` also keeps what `sysupgrade` would drop:

- Files in `/overlay/upper` that no package owns and `sysupgrade -l` does not
  list (for example your own scripts or apk keys) are shown, and after your
  confirmation added to `/etc/sysupgrade.conf`. Generated and program files
  are excluded (`AUTO_KEEP_EXCLUDES` in `updater.py`). Add paths by hand with
  `--extra-keep PATH` (repeatable); turn the detection off with
  `--no-auto-keep`.
- The packages you installed are saved as `/etc/backup/world-old.txt`, which
  goes into the backup. After a verified update, each missing package is
  installed again with `apk add`, and a summary is written to
  `reinstall-summary.txt` in the output directory. Skip this with
  `--no-reinstall`; retry it later with `--reinstall-only`. See
  [docs/packages.md](docs/packages.md) for kernel modules and packages such as
  `dnsmasq-full`, which are not replaced automatically.

The output directory also keeps `world.txt`, `sysupgrade-list.txt` and
`overlay-full.tar.gz` for comparison. The archive is never restored.

### Update with your SSH key

If the router accepts only SSH keys, or you prefer them, log in with your own
OpenSSH setup:

```sh
python3 updater.py --ssh-key-auth --host 192.168.31.1 --interface en0
```

- `ssh` and `scp` use your `~/.ssh/config` and `known_hosts`. No password is
  sent, and a refused key is never followed by a password.
- Log in once by hand (`ssh root@192.168.31.1`) to check and record the
  router's host key. A missing or changed host key stops the update; check
  the key yourself before you edit `known_hosts`.
- Load a key that has a passphrase with `ssh-add` first. Use
  `--ssh-identity PATH` to offer only one key.
- If your config names the router by an alias, add `--ssh-alias NAME`.
  `--host` stays the numeric address that the network checks use; the alias
  must resolve to it and log in as `root`.
- The router may be this computer's default gateway on `--interface`: no
  password is sent, and the host key is checked strictly.
- The settings must be kept (no `--no-keep-settings`), because the update is
  verified by key afterwards.
- The RAM installer route (`--via-ram-installer`, updates from v1.x) still
  logs in to the RAM installer with its empty password, so it needs
  `sshpass`. To continue an interrupted update from the RAM installer, run
  without `--ssh-key-auth`.

## Web wizard

```sh
python3 gui/app.py
```

A local step-by-step wizard over these tools, in English, Russian, Simplified
Chinese and Brazilian Portuguese, with a dry run, a stage tracker, and a live
log. See [gui/README.md](gui/README.md).

## Return to stock

Use `revert.py` from an installed OpenWrt system with a profile-approved stock
image. See the [stock restoration guide](docs/maintenance.md#return-to-stock)
for the image list, command, and connection options.

## Supporting tools

See [diagnostics and tests](docs/technical.md#diagnostics-and-tests) for image
inspection and local checks.

## License

GPL-2.0-only. See [LICENSE](LICENSE).
