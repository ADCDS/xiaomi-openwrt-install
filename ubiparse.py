#!/usr/bin/env python3
"""Offline UBI image parser -- read a raw MTD dump, enumerate its volumes.

Why offline rather than `ubiattach` + `ubinfo` on the device: attaching a UBI
is not a pure read.  The kernel scans every erase block and will rewrite
headers it considers damaged, and the partition in question is the one the
locked stock bootloader has to be able to attach afterwards.  A raw `dd` of
the partition costs a minute over Wi-Fi and cannot possibly change anything,
so the probe dumps and parses here instead.

What we are actually trying to learn: whether stock keeps a *second*, idle
kernel volume in `ubi_kernel`.  If it does, the OpenWrt RAM initramfs can be
written into that one with `ubiupdatevol` -- leaving the running stock kernel
untouched and the bootloader's A/B failure counters as a working fallback --
instead of `ubiformat`ing the whole partition and burning the only bootable
system on the device.

On-disk layout (drivers/mtd/ubi/ubi-media.h):
  each PEB starts with a 64-byte EC header  (magic "UBI#")
  at ec.vid_hdr_offset a 64-byte VID header (magic "UBI!") names the volume
  volume id 0x7fffefff is the layout volume: 128 x 172-byte table records
"""

import hashlib
import struct
import sys

EC_MAGIC = b"UBI#"
VID_MAGIC = b"UBI!"
LAYOUT_VOL_ID = 0x7FFFEFFF
VTBL_RECORD = 172
VTBL_RECORDS = 128

VOL_TYPE = {1: "dynamic", 2: "static"}


class Volume:
    def __init__(self, vol_id, name, reserved_pebs, vol_type, alignment, flags):
        self.vol_id = vol_id
        self.name = name
        self.reserved_pebs = reserved_pebs
        self.vol_type = vol_type
        self.alignment = alignment
        self.flags = flags
        self.lebs = {}      # lnum -> (peb_index, data_offset, data_size)
        self.used_ebs = 0

    @property
    def type_name(self):
        return VOL_TYPE.get(self.vol_type, f"?{self.vol_type}")

    def __repr__(self):
        return (f"<vol {self.vol_id} {self.name!r} {self.type_name} "
                f"reserved={self.reserved_pebs} mapped={len(self.lebs)}>")


class UbiImage:
    def __init__(self, data, peb_size=None):
        self.data = data
        self.peb_size = peb_size or self._guess_peb_size(data)
        self.volumes = {}
        self.image_seq = None
        self.image_seqs = set()
        self.empty_pebs = 0
        self.total_pebs = len(data) // self.peb_size
        self._parse()

    @staticmethod
    def _guess_peb_size(data):
        """Distance between the first two "UBI#" markers that are both at the
        start of a plausible block size."""
        for candidate in (131072, 262144, 65536, 524288):
            if len(data) >= 2 * candidate and data[0:4] == EC_MAGIC \
                    and data[candidate:candidate + 4] in (EC_MAGIC, b"\xff\xff\xff\xff"):
                return candidate
        return 131072

    def _parse(self):
        """Scan every PEB.

        Two things a naive scan gets wrong on a dump taken off live flash
        rather than out of a freshly-built image:

        * **The same LEB can be present twice.** UBI writes a new copy before
          dropping the old one, and the newer is the one with the higher
          sequence number -- not the one that happens to sit later in the
          dump. Picking by physical order reconstructs a mix of old and new.
        * **A dump can span more than one UBI.** MTD partition boundaries are
          whatever the running kernel's DTS says, and two adjacent UBIs get
          merged into nonsense -- including their volume tables, so a volume
          name can appear to have contents it does not have. Differing
          `image_seq` is the tell, so it is recorded and reported.
        """
        vtbl_raw = None
        mapped = []       # (vol_id, lnum, peb, data_off, data_size, used_ebs)
        for i in range(self.total_pebs):
            base = i * self.peb_size
            peb = self.data[base:base + self.peb_size]
            if peb[0:4] != EC_MAGIC:
                if peb[0:4] == b"\xff\xff\xff\xff":
                    self.empty_pebs += 1
                continue
            vid_off, data_off, image_seq = struct.unpack(">III", peb[0x10:0x1C])
            self.image_seqs.add(image_seq)
            if self.image_seq is None:
                self.image_seq = image_seq
            if vid_off + 64 > len(peb) or peb[vid_off:vid_off + 4] != VID_MAGIC:
                continue  # EC header only: an erased-and-counted block
            vid = peb[vid_off:vid_off + 64]
            vol_type = vid[5]
            vol_id, lnum = struct.unpack(">II", vid[0x08:0x10])
            data_size, used_ebs = struct.unpack(">II", vid[0x14:0x1C])
            sqnum = struct.unpack(">Q", vid[0x28:0x30])[0]
            mapped.append((vol_id, lnum, i, data_off, data_size, used_ebs,
                           vol_type, sqnum, image_seq))
            if vol_id == LAYOUT_VOL_ID and vtbl_raw is None:
                vtbl_raw = peb[data_off:data_off + VTBL_RECORD * VTBL_RECORDS]

        if vtbl_raw:
            self._parse_vtbl(vtbl_raw)

        # Newest copy of each LEB wins, by sequence number.
        mapped.sort(key=lambda m: m[7])
        for vol_id, lnum, peb, data_off, data_size, used_ebs, vol_type, _sq, _is in mapped:
            if vol_id == LAYOUT_VOL_ID:
                continue
            vol = self.volumes.get(vol_id)
            if vol is None:
                # A volume with no table entry: unusual, but report it rather
                # than drop it -- it would mean the table and the data
                # disagree, which is exactly the sort of thing worth seeing.
                vol = Volume(vol_id, f"<untabled:{vol_id}>", 0, vol_type, 1, 0)
                self.volumes[vol_id] = vol
            vol.lebs[lnum] = (peb, data_off, data_size)
            vol.used_ebs = max(vol.used_ebs, used_ebs)

    def _parse_vtbl(self, raw):
        for idx in range(VTBL_RECORDS):
            rec = raw[idx * VTBL_RECORD:(idx + 1) * VTBL_RECORD]
            if len(rec) < VTBL_RECORD:
                break
            reserved_pebs, alignment, _data_pad = struct.unpack(">III", rec[0:12])
            vol_type = rec[12]
            name_len = struct.unpack(">H", rec[14:16])[0]
            name = rec[16:16 + name_len].decode("utf-8", "replace")
            flags = rec[0x90]
            if reserved_pebs == 0 and name_len == 0:
                continue
            self.volumes[idx] = Volume(idx, name, reserved_pebs, vol_type,
                                       alignment, flags)

    def leb_size(self):
        for vol in self.volumes.values():
            for _peb, data_off, _sz in vol.lebs.values():
                return self.peb_size - data_off
        return self.peb_size - 4096

    def extract(self, vol_id):
        """Reassemble a volume's contents in LEB order.

        Static volumes carry a per-LEB data_size, so the result is byte-exact
        for images like a kernel FIT.  Dynamic volumes have no notion of used
        length, so every mapped LEB is returned in full.
        """
        vol = self.volumes[vol_id]
        out = bytearray()
        count = vol.used_ebs if (vol.vol_type == 2 and vol.used_ebs) else \
            (max(vol.lebs) + 1 if vol.lebs else 0)
        for lnum in range(count):
            if lnum not in vol.lebs:
                out.extend(b"\x00" * self.leb_size())
                continue
            peb, data_off, data_size = vol.lebs[lnum]
            base = peb * self.peb_size + data_off
            take = data_size if vol.vol_type == 2 else (self.peb_size - data_off)
            out.extend(self.data[base:base + take])
        return bytes(out)

    def report(self):
        lines = [
            f"peb_size   {self.peb_size} ({self.peb_size // 1024}k)",
            f"leb_size   {self.leb_size()}",
            f"total pebs {self.total_pebs}  (erased/unused: {self.empty_pebs})",
            f"image_seq  {self.image_seq}"
            + ("" if len(self.image_seqs) <= 1
               else f"   *** {len(self.image_seqs)} DISTINCT image_seq values "
                    f"{sorted(self.image_seqs)}: this dump spans more than one "
                    "UBI, so volumes and their contents are mixed. Limit it to "
                    "one partition. ***"),
            "",
            f"{'id':>4}  {'name':<20} {'type':<8} {'reserved':>8} {'mapped':>7} {'bytes':>12}",
        ]
        for vol_id in sorted(self.volumes):
            v = self.volumes[vol_id]
            size = len(self.extract(vol_id)) if v.lebs else 0
            lines.append(
                f"{vol_id:>4}  {v.name:<20} {v.type_name:<8} "
                f"{v.reserved_pebs:>8} {len(v.lebs):>7} {size:>12}"
            )
        return "\n".join(lines)


def matches_image(volume, image):
    """Does a volume read back as `image`?

    Not an equality test, and this matters.  The kernel volume that
    `ubinize-kernel` produces is **dynamic**, so UBI records no used length for
    it: a read-back returns whole LEBs, and the tail past the FIT is 0xff
    padding.  The real v1.6 artifact is 13,904,532 bytes of `.itb` inside a
    13,967,360-byte volume -- 110 LEBs of 126,976 -- so an md5 of the whole
    volume against the `.itb` can never match, however good the write was.

    Compare the prefix, and require the remainder to be erased.
    """
    if len(volume) < len(image):
        return False, f"short by {len(image) - len(volume)} B"
    if volume[:len(image)] != image:
        for i, (a, b) in enumerate(zip(volume, image)):
            if a != b:
                return False, f"first difference at offset {i}"
        return False, "prefix differs"
    tail = volume[len(image):]
    if tail.strip(b"\xff"):
        return False, f"{len(tail)} B of trailing data is not erased padding"
    return True, f"image matches; {len(tail)} B of 0xff padding to the LEB boundary"


def main(argv):
    if len(argv) < 2:
        print(f"usage: {argv[0]} <ubi-dump.bin> [--extract NAME|ID outfile]")
        return 2
    data = open(argv[1], "rb").read()
    img = UbiImage(data)
    print(img.report())
    print()
    for vol_id in sorted(img.volumes):
        v = img.volumes[vol_id]
        if not v.lebs:
            continue
        blob = img.extract(vol_id)
        print(f"  vol {vol_id} {v.name!r}: md5={hashlib.md5(blob).hexdigest()} "
              f"head={blob[:4].hex()}")
    if "--extract" in argv:
        i = argv.index("--extract")
        want, out = argv[i + 1], argv[i + 2]
        for vol_id, v in img.volumes.items():
            if v.name == want or str(vol_id) == want:
                open(out, "wb").write(img.extract(vol_id))
                print(f"wrote {out}")
                return 0
        print(f"no volume {want!r}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
