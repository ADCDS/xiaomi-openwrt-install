#!/usr/bin/env python3
"""Fetch and verify a release of the RD03v2 OpenWrt port.

Two jobs, and the second one is the important one.

Fetching is unremarkable: the GitHub releases API, assets picked by name, a
`sha256sums.txt` that the release notes promise covers every asset, checked
before anything is handed to a flasher.

Gating is not.  The NAND on this board is **second-sourced** -- some units
carry an ESMT F50D1G41LB, others a Winbond W25N01KW -- and the two are not
interchangeable to the kernel.  A build without the Winbond ID entry does not
merely run slower on a Winbond unit: `spi-nand` fails to probe at all, no MTD
device is registered, `/proc/mtd` is empty, UBI cannot attach, and ath11k
cannot read caldata out of `0:ART`, so the radios never come up either.  On a
Wi-Fi-only install that is a device you cannot talk to any more.

The entry landed in commit 4d074a2, one day *after* the v1.6 tag, so **the
latest published release does not have it**.  Downloading "the latest
release" and flashing it onto a Winbond unit is precisely the failure this
module exists to refuse.

The release itself should say which parts it supports rather than having that
encoded here; if an asset named `nand-support.txt` is present it wins, and
the version table below is only the fallback for releases predating it.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request

REPO = "ADCDS/openwrt-xiaomi-ax3000t-rd03v2"
PREFIX = "openwrt-qualcommax-ipq50xx-xiaomi_mi-router-ax3000t-v2"

# Images this installer knows how to use, keyed by role.  mkrelease.sh derives
# the NSS names by inserting "-nss" before the extension.
KINDS = {
    "initramfs_itb": "initramfs-uImage.itb",
    "initramfs_ubi": "initramfs-factory.ubi",
    "sysupgrade": "squashfs-sysupgrade.bin",
}

# Fallback only -- see the module docstring.  Keyed by the part names
# probe.identify_nand() reports.
NAND_MIN_VERSION = {
    "ESMT F50D1G41LB": (1, 0),
    "ESMT F50D1G41LB (raw id)": (1, 0),
    "Winbond W25N01KW": (1, 7),
    "Winbond W25N01KW (raw id)": (1, 7),
}


class ReleaseError(Exception):
    pass


def _api(path):
    """GET an API path.  Anonymous requests get 60/hour and shared NAT eats
    that fast, so fall back to an authenticated `gh` when one is configured
    before giving up."""
    url = f"https://api.github.com/{path}"
    req = urllib.request.Request(url)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "rd03v2-ota-installer")
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        if e.code in (403, 429):
            got = _api_via_gh(path)
            if got is not None:
                return got
            raise ReleaseError(
                "GitHub rate-limited this host (60 requests/hour anonymous). "
                "Set GITHUB_TOKEN, run `gh auth login`, or point the installer "
                "at an image directory you already downloaded."
            )
        raise ReleaseError(f"{url}: HTTP {e.code}")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        got = _api_via_gh(path)
        if got is not None:
            return got
        raise ReleaseError(f"{url}: {type(e).__name__}: {e}")


def _api_via_gh(path):
    if not shutil.which("gh"):
        return None
    try:
        r = subprocess.run(["gh", "api", path], capture_output=True,
                           text=True, timeout=30)
        if r.returncode != 0:
            return None
        print("[*] (used an authenticated `gh api` -- anonymous quota exhausted)")
        return json.loads(r.stdout)
    except Exception:                                            # noqa: BLE001
        return None


def parse_version(tag):
    m = re.match(r"v?(\d+)\.(\d+)", str(tag))
    return (int(m.group(1)), int(m.group(2))) if m else None


class Release:
    def __init__(self, meta):
        self.tag = meta.get("tag_name", "?")
        self.published = meta.get("published_at", "?")
        self.body = meta.get("body") or ""
        self.prerelease = bool(meta.get("prerelease"))
        self.assets = {
            a["name"]: {"url": a["browser_download_url"], "size": a["size"]}
            for a in meta.get("assets", [])
        }
        self.version = parse_version(self.tag)

    def name_for(self, kind, flavour="default", wifi=False):
        """`initramfs_itb` + nss + wifi -> ...-initramfs-uImage-nss-wifi.itb

        v1.7 added a second initramfs per flavour whose `rc.local` brings the
        radios up while it is running from RAM, so an install needs no LAN
        cable.  The flavour suffix goes before the variant suffix, and only
        the initramfs artifacts have a -wifi twin -- there is still exactly
        one sysupgrade image per flavour, built from the radio-silent pass.
        """
        if kind not in KINDS:
            raise ReleaseError(f"unknown image kind {kind!r}")
        base = KINDS[kind]
        stem, _, ext = base.rpartition(".")
        if flavour == "nss":
            stem += "-nss"
        elif flavour != "default":
            raise ReleaseError(f"unknown flavour {flavour!r}")
        if wifi:
            if not kind.startswith("initramfs"):
                raise ReleaseError(f"{kind} has no -wifi variant")
            stem += "-wifi"
        return f"{PREFIX}-{stem}.{ext}"

    def require(self, kind, flavour="default", wifi=False):
        name = self.name_for(kind, flavour, wifi)
        if name not in self.assets:
            raise ReleaseError(
                f"{self.tag} has no asset {name!r}. Assets present: "
                + ", ".join(sorted(self.assets)) or "(none)"
            )
        return name

    def __repr__(self):
        return f"<Release {self.tag} ({len(self.assets)} assets)>"


def latest(repo=REPO):
    return Release(_api(f"repos/{repo}/releases/latest"))


def by_tag(tag, repo=REPO):
    return Release(_api(f"repos/{repo}/releases/tags/{tag}"))


# ---- download + verify ------------------------------------------------------


def download(rel, name, destdir, progress=True):
    """Fetch one asset, resuming nothing and trusting nothing -- the caller
    verifies against sha256sums.txt afterwards."""
    if name not in rel.assets:
        raise ReleaseError(f"{rel.tag}: no asset named {name!r}")
    os.makedirs(destdir, exist_ok=True)
    path = os.path.join(destdir, name)
    want = rel.assets[name]["size"]
    if os.path.exists(path) and os.path.getsize(path) == want:
        return path
    req = urllib.request.Request(rel.assets[name]["url"])
    req.add_header("User-Agent", "rd03v2-ota-installer")
    tmp = path + ".part"
    got = 0
    with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as fh:
        while True:
            chunk = r.read(262144)
            if not chunk:
                break
            fh.write(chunk)
            got += len(chunk)
            if progress and want:
                pct = 100 * got // want
                short = name.rsplit("-v2-", 1)[-1]
                print(f"\r    {short}: {pct:3d}%  {got >> 20}/{want >> 20} MiB",
                      end="", flush=True)
    if progress:
        print()
    if want and got != want:
        os.unlink(tmp)
        raise ReleaseError(f"{name}: got {got} B, release says {want}")
    os.replace(tmp, path)
    return path


def checksums(rel, destdir):
    """Parse sha256sums.txt.  Absent on a release that predates it, in which
    case the caller gets nothing to check against and should say so out loud
    rather than pretend the download was verified."""
    if "sha256sums.txt" not in rel.assets:
        return {}
    path = download(rel, "sha256sums.txt", destdir, progress=False)
    out = {}
    with open(path) as fh:
        for line in fh:
            parts = line.split()
            if len(parts) == 2 and len(parts[0]) == 64:
                out[parts[1].lstrip("*")] = parts[0]
    return out


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def get_images(rel, destdir, flavour="default", kinds=("initramfs_ubi", "sysupgrade"),
               wifi=False):
    """Download the images this install needs and verify every one of them.

    `wifi` selects the beaconing initramfs where one exists; the sysupgrade
    image has no such variant and is fetched unchanged either way.
    """
    sums = checksums(rel, destdir)
    if not sums:
        print(f"[!] {rel.tag} publishes no sha256sums.txt -- downloads are "
              "unverified beyond their length")
    out = {}
    for kind in kinds:
        name = rel.require(kind, flavour, wifi and kind.startswith("initramfs"))
        print(f"[*] {kind}: {name}")
        path = download(rel, name, destdir)
        digest = sha256(path)
        want = sums.get(name)
        if want and digest != want:
            raise ReleaseError(
                f"{name}: sha256 mismatch\n  got  {digest}\n  want {want}")
        if want:
            print(f"    sha256 ok ({digest[:16]}...)")
        out[kind] = {"name": name, "path": path, "sha256": digest,
                     "verified": bool(want)}
    return out


# ---- the NAND gate ----------------------------------------------------------


def nand_support(rel, destdir=None):
    """What this release says it can drive: {flash_type_code: part name}.

    v1.7 onward ships a `nand-support.txt` asset generated from the kernel
    that was actually built, so a release states this for itself instead of an
    installer guessing from version numbers.  Its format is one part per line,
    `#` starts a comment:

        11  c8:11  ESMT F50D1G41LB          # kernel: ESMT F50D1G41LB
        be  ef:be  Winbond W25N01KWZEIG     # kernel: Winbond W25N01KW

    The first column is the device byte the stock bootloader leaves in
    `flash_type`, which is how a unit self-identifies before anything is
    written.  Lines whose first column is `-` are parts the kernel knows but
    the bootloader cannot identify: such a chip cannot be this board's boot
    NAND, since the bootloader could not have read it to boot at all, so they
    are dropped rather than offered as a match.

    Returns None when the release says nothing, which the caller must treat as
    "fall back to the version table", never as "everything is supported".
    """
    if "nand-support.txt" not in rel.assets or destdir is None:
        return None
    path = download(rel, "nand-support.txt", destdir, progress=False)
    parts = {}
    with open(path) as fh:
        for line in fh:
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            cols = line.split(None, 2)
            if len(cols) < 3 or cols[0] == "-":
                continue
            parts[cols[0].lower()] = cols[2].strip()
    return parts or None


def normalise_flash_type(value):
    """`0xBE`, `be`, `b` -> `be`.  The env holds it as bare lowercase hex."""
    return str(value).strip().lower().removeprefix("0x").zfill(2)


def check_nand(rel, part, destdir=None, flash_type=None):
    """(ok, reason) for flashing this unit's NAND with this release.

    `flash_type` is the authoritative key when the release declares its own
    support: it is what the bootloader measured, whereas `part` is a name this
    installer decoded.  Falls back to the name, then to the version table, and
    fails closed at every step.
    """
    if not part and not flash_type:
        return False, ("the NAND was not identified; nothing here can tell you "
                       "whether this release can drive it")

    declared = nand_support(rel, destdir)
    if declared is not None:
        if flash_type:
            code = normalise_flash_type(flash_type)
            if code in declared:
                return True, (f"{rel.tag} declares flash_type 0x{code} "
                              f"({declared[code]}) supported")
            return False, (
                f"{rel.tag} does not list flash_type 0x{code} among the "
                f"{len(declared)} parts it declares. Flashing it would leave a "
                "device whose kernel cannot probe its own flash.")
        for code, name in declared.items():
            if name.lower() in part.lower() or part.lower() in name.lower():
                return True, (f"{rel.tag} declares {name!r} supported "
                              f"(flash_type 0x{code})")
        return False, f"{rel.tag} does not declare support for {part!r}"

    need = NAND_MIN_VERSION.get(part)
    if need is None:
        what = part or f"flash_type 0x{normalise_flash_type(flash_type)}"
        return False, (f"{what} is not a part this installer knows about, and "
                       f"{rel.tag} ships no nand-support.txt to ask; no release "
                       "is known to drive it")
    if rel.version is None:
        return False, f"cannot read a version out of the tag {rel.tag!r}"
    if rel.version >= need:
        return True, (f"{part} needs >= v{need[0]}.{need[1]}, {rel.tag} is "
                      "new enough")
    return False, (
        f"{part} needs >= v{need[0]}.{need[1]} and {rel.tag} predates it. "
        "For the Winbond W25N01KW the spinand ID entry landed after v1.6 "
        "(commit 4d074a2); without it the kernel registers no MTD at all and "
        "the device comes up with no flash and no radios. Build from main, or "
        "wait for the release that carries it."
    )


def main(argv):
    import argparse
    ap = argparse.ArgumentParser(description="Inspect/fetch an RD03v2 OpenWrt release.")
    ap.add_argument("--tag", default=None, help="default: the latest release")
    ap.add_argument("--flavour", default="default", choices=("default", "nss"))
    ap.add_argument("--dest", default="images")
    ap.add_argument("--download", action="store_true")
    ap.add_argument("--nand", default=None,
                    help="part name from the probe, e.g. 'Winbond W25N01KW'")
    ap.add_argument("--flash-type", default=None,
                    help="the unit's `nvram get flash_type`, e.g. 11 or be")
    ap.add_argument("--wifi", action="store_true",
                    help="select the beaconing initramfs (v1.7+)")
    args = ap.parse_args(argv[1:])

    rel = by_tag(args.tag) if args.tag else latest()
    print(f"{rel.tag}  published {rel.published}  ({len(rel.assets)} assets)")
    for n in sorted(rel.assets):
        print(f"  {rel.assets[n]['size']:>12} B  {n}")
    print()
    for kind in KINDS:
        try:
            print(f"  {kind:<14} -> "
                  f"{rel.require(kind, args.flavour, args.wifi and kind.startswith('initramfs'))}")
        except ReleaseError as e:
            print(f"  {kind:<14} -> MISSING ({e})")

    if args.nand or args.flash_type:
        # nand-support.txt has to be fetched to be consulted, so always give
        # check_nand somewhere to put it -- otherwise the gate silently falls
        # back to the version table and reports on the wrong evidence.
        ok, why = check_nand(rel, args.nand, args.dest, args.flash_type)
        print(f"\nNAND gate: {'PASS' if ok else 'REFUSE'} -- {why}")
        if not ok and not args.download:
            return 1
    if args.download:
        print()
        get_images(rel, args.dest, args.flavour, wifi=args.wifi)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except ReleaseError as exc:
        print(f"[-] {exc}", file=sys.stderr)
        sys.exit(1)
