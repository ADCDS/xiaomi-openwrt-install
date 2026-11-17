"""Read a flattened device tree, and the device trees a release's images carry.

Only what the installer needs: whether a release forces the NAND ECC strength
in its device tree, or leaves it to the driver, which then follows the chip.
Pure Python, so it runs where dtc does not (Windows).
"""
import struct
import tarfile

FDT_MAGIC = 0xd00dfeed
_BEGIN_NODE, _END_NODE, _PROP, _NOP, _END = 1, 2, 3, 4, 9


class DtbError(ValueError):
    """The blob is not a device tree this module can read."""


def parse(blob):
    """{node path: {property name: raw value}} for every node."""
    try:
        return _parse(blob)
    except (struct.error, IndexError, UnicodeError) as exc:
        raise DtbError(f"malformed device tree: {exc}")


def _parse(blob):
    if len(blob) < 40:
        raise DtbError("too short for a device tree")
    (magic, total, off_struct, off_strings, _rsv, version, _last,
     _cpu, size_strings, size_struct) = struct.unpack(">10I", blob[:40])
    if magic != FDT_MAGIC:
        raise DtbError("not a device tree")
    if total > len(blob):
        raise DtbError("device tree is truncated")
    end = off_struct + size_struct if version >= 17 else total
    if not (40 <= off_struct <= end <= total and off_strings + size_strings <= total):
        raise DtbError("device tree blocks lie outside it")
    strings = blob[off_strings:off_strings + size_strings]
    nodes, stack, pos = {}, [], off_struct
    while pos + 4 <= end:
        (token,) = struct.unpack(">I", blob[pos:pos + 4])
        pos += 4
        if token == _BEGIN_NODE:
            nul = blob.find(b"\0", pos)
            if nul < 0:
                raise DtbError("unterminated node name")
            name = blob[pos:nul].decode("ascii", "replace")
            pos = (nul + 4) & ~3
            parent = stack[-1] if stack else None
            path = "/" if parent is None else (
                "/" + name if parent == "/" else f"{parent}/{name}")
            stack.append(path)
            nodes[path] = {}
        elif token == _PROP:
            if not stack:
                raise DtbError("property outside a node")
            length, nameoff = struct.unpack(">II", blob[pos:pos + 8])
            pos += 8
            nul = strings.find(b"\0", nameoff)
            if nul < 0:
                raise DtbError("bad property name offset")
            nodes[stack[-1]][strings[nameoff:nul].decode("ascii", "replace")] = \
                blob[pos:pos + length]
            pos = (pos + length + 3) & ~3
        elif token == _END_NODE:
            if not stack:
                raise DtbError("unbalanced node end")
            stack.pop()
        elif token == _NOP:
            continue
        elif token == _END:
            return nodes
        else:
            raise DtbError(f"unknown token {token:#x}")
    raise DtbError("device tree has no end token")


def _string(value):
    return value.split(b"\0", 1)[0].decode("ascii", "replace")


def fit_dtbs(itb):
    """The device trees embedded in a FIT image, in node order."""
    out = []
    for path, props in parse(itb).items():
        if (path.startswith("/images/") and path.count("/") == 2
                and _string(props.get("type", b"")) == "flat_dt"):
            compression = _string(props.get("compression", b"none"))
            if compression != "none":
                raise DtbError(f"{path} is {compression}-compressed")
            if "data" not in props:
                raise DtbError(f"{path} carries no data")
            out.append(props["data"])
    if not out:
        raise DtbError("the FIT image carries no device tree")
    return out


def spi_nand_ecc(dtb):
    """The ECC strength a device tree forces on its SPI-NAND, or None when it
    leaves the strength to the driver."""
    tree = parse(dtb)
    nand = [p for p, props in tree.items()
            if b"spi-nand" in props.get("compatible", b"").split(b"\0")]
    if len(nand) != 1:
        raise DtbError(f"expected one spi-nand node, found {len(nand)}")
    value = tree[nand[0]].get("nand-ecc-strength")
    if value is None:
        return None
    if len(value) != 4:
        raise DtbError("nand-ecc-strength is not one cell")
    return struct.unpack(">I", value)[0]


def sysupgrade_kernel(path):
    """The kernel FIT inside a NAND sysupgrade image (a tar)."""
    try:
        with tarfile.open(path) as tar:
            for member in tar.getmembers():
                if member.isfile() and member.name.endswith("/kernel"):
                    return tar.extractfile(member).read()
    except (tarfile.TarError, OSError) as exc:
        raise DtbError(f"cannot read {path} as a sysupgrade image: {exc}")
    raise DtbError(f"{path} has no kernel")


def release_nand_ecc(images):
    """The NAND ECC strength a release forces, or None when it follows the chip.

    Reads every device tree of the RAM installer (initramfs_itb) and of the
    installed kernel (sysupgrade), whichever the selection has; they must agree.
    Raises DtbError when there is nothing to read or they disagree.
    """
    found = {}
    if "initramfs_itb" in images:
        with open(images["initramfs_itb"]["path"], "rb") as fh:
            found["initramfs_itb"] = {spi_nand_ecc(d) for d in fit_dtbs(fh.read())}
    if "sysupgrade" in images:
        kernel = sysupgrade_kernel(images["sysupgrade"]["path"])
        found["sysupgrade"] = {spi_nand_ecc(d) for d in fit_dtbs(kernel)}
    if not found:
        raise DtbError("no image of the release to read a device tree from")
    values = set().union(*found.values())
    if len(values) != 1:
        raise DtbError(f"the release's device trees disagree: {found}")
    return values.pop()
