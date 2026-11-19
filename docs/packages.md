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

### When DNS depends on a package you installed

dnsmasq may forward every query to a resolver on the router itself, for
example `127.0.0.1#5053` from `https-dns-proxy`, with `noresolv` set so that it
does not use the WAN's DNS servers. If that resolver comes from a package that
is not in the image, the router cannot resolve names after the update. It then
cannot reach the repositories to reinstall that package.

`updater.py` warns about this before the backup. During the reinstall, if
`apk update` fails because the router cannot resolve the repository's name,
it does this:

1. It checks which of the WAN's DNS servers (from
   `/tmp/resolv.conf.d/resolv.conf.auto`) can resolve the name.
2. It points the router's own resolver, `/tmp/resolv.conf`, at those servers
   until the reinstall ends. dnsmasq, the LAN and the UCI configuration are
   not changed.
3. At the end, also when the reinstall stops early, it restores
   `/tmp/resolv.conf`. If dnsmasq has rewritten the file in the meantime,
   because the reinstalled DNS package restarted it, the new file stays.

`updater.py` never chooses a DNS server itself. If none of the WAN's servers
works, it reports that and changes nothing. Fix the router's DNS, then run
`--reinstall-only`. Clients on the LAN cannot resolve names until the DNS
package is back.

## Configuration files after the update

Your configuration is restored and in use after the update. `updater.py` then
shows you where a newer default exists, in two ways.

**Packages that were reinstalled.** apk never overwrites a configuration file
that you changed. It keeps yours and saves the package's new default next to
it as `*.apk-new`. For each `*.apk-new` that the reinstall created,
`updater.py` shows the difference from your version and asks, as dpkg does:

- `k`: keep your version. The `*.apk-new` file is removed.
- `n`: install the package's version. Yours is saved as `*.apk-old`. Restart
  the service that reads the file, or reboot.
- `d`: decide later. Both files stay as they are.

With `--yes`, or without a terminal, every file is left for later. Ctrl-C
leaves the current file and the rest for later.

**Packages in the image.** sysupgrade restores your version over the image's
default without asking, which is correct when that default did not change.
Before the update, `updater.py` saves the current image defaults (from `/rom`)
of the files that the backup keeps, as `rom-defaults-old.tar.gz`. After the
update, it compares them with the new image's defaults and lists only the
files whose default the new release changed. For each of those, it shows the
upstream change from the old default to the new one, so you can copy what you
need into your version. Nothing on the router is changed in this step.

All differences are saved in `conffiles/` in the output directory. The
decisions are listed in `reinstall-summary.txt`.

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
`AUTO_KEEP_EXCLUDES` in `updater.py`. It includes the service links in
`/etc/rc.d/`. So the scan does not carry over which services are enabled: a
reinstalled package enables its service as the package does by default. If
you disabled one, check it after the update with `service`, and disable it
again with `service NAME disable`.

Use `--extra-keep PATH` to add more paths, and `--no-auto-keep` to turn off
the scan. The paths go into `/etc/sysupgrade.conf` itself, because lists in
`/etc/sysupgrade.conf.d/` were found not to take effect on this firmware.
