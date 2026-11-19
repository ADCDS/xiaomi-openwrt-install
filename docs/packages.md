# Packages after an update

A sysupgrade installs the packages that the new image contains. The packages
you added yourself are not reinstalled, and neither are files that no package
owns unless `/etc/sysupgrade.conf` lists them. `updater.py` handles both. This
page explains what it does, and what you have to do by hand.

## The package list

Before the backup, `updater.py` reads `/etc/apk/world` and writes the packages
you installed to `/etc/backup/world-old.txt`. It also adds `/etc/backup/` to
`/etc/sysupgrade.conf`, so the list goes into the configuration backup.

It leaves out these entries:

- Entries with a version constraint (`=`, `<`, `>`, `~`). The firmware pins
  these to its own build, for example `kernel`, `base-files` and its kernel
  modules.
- `kernel`, `libc` and `base-files`.
- `lib*` packages. Libraries come back as dependencies.

After the update has been verified, `updater.py` waits until `apk update`
works, then checks each listed package. It installs only those that are
missing, one at a time, and writes the result to `reinstall-summary.txt` in
the output directory. It never runs `apk add` with the plain name of a package
that the new firmware pins, because that would remove the pin.

To run only this step on a router that is already updated:

```sh
python3 updater.py --reinstall-only --host 192.168.31.1 --interface en0
```

Add `--ssh-key-auth` if you log in with a key.

## Kernel modules

A `kmod-*` package must be built for the exact kernel that is running. Install
it only from the package repository of the running firmware, as configured on
the router. If a kmod is missing from that repository, it fails and is listed
in the summary. Do not install a kmod built for another kernel or release.

## Packages that replace a firmware package

Some packages replace a package that the firmware installs. `apk add` resolves
that conflict by swapping the two packages, so `updater.py` does not install
them automatically. It lists them in the summary instead. These are the
patterns in `SPECIAL_PACKAGES` in `updater.py`:

| Pattern | Replaces |
| --- | --- |
| `dnsmasq-*` (such as `dnsmasq-full`) | `dnsmasq` |
| `wpad*`, `hostapd*` | the firmware's `wpad` variant |
| `odhcpd*` | the firmware's `odhcpd` variant |

Replace one of these by hand. Download the new package first: once `dnsmasq`
is removed, the router may not resolve the repository's name any more. For
example, for `dnsmasq-full`:

```sh
ssh root@192.168.31.1 'cd /tmp && apk update && apk fetch dnsmasq-full &&
  apk del dnsmasq && apk add ./dnsmasq-full-*.apk'
```

Your DHCP and DNS settings in `/etc/config/dhcp` stay as they are. Check them
with `service dnsmasq restart` and `logread -e dnsmasq` afterwards.

## Files that no package owns

Before the backup, `updater.py` scans `/overlay/upper` once and lists the
files that no package owns (`apk info --who-owns` finds none) and that
`sysupgrade -l` does not list. After you confirm the list, it adds these files
to `/etc/sysupgrade.conf`. Paths that are already listed are not added again.
Generated files and program directories are excluded. The list is
`AUTO_KEEP_EXCLUDES` in `updater.py`.

Use `--extra-keep PATH` to add more paths, and `--no-auto-keep` to turn off
the scan. The paths go into `/etc/sysupgrade.conf` itself, because lists in
`/etc/sysupgrade.conf.d/` were found not to take effect on this firmware.
