#!/usr/bin/env python3
"""Supported Xiaomi router profiles.

The mesh exploit is shared, but installing and restoring firmware is always
device-specific.  A profile is the complete safety boundary for those
operations: identity, tested stock firmware, release artifacts, NAND IDs,
partition layout, and accepted stock images all live here.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class DeviceProfile:
    slug: str
    display_name: str
    stock_hardware: tuple[str, ...]
    stock_model: str
    tested_stock_roms: tuple[str, ...]
    nvram_model: str
    openwrt_board: str
    stock_host: str
    openwrt_host: str
    installer_wifi_ssid: str
    installer_wifi_key: str
    release_repo: str
    release_prefix: str
    default_release: str
    image_kinds: dict[str, str]
    nand_min_version: dict[str, tuple[int, int]]
    flash_types: dict[str, str]
    stock_slots: dict[str, int]
    required_stock_partitions: tuple[str, ...]
    backup_partitions: tuple[tuple[str, str], ...]
    stock_images: dict[str, str]
    stock_image_glob: str
    payload_offset: int
    peb_size: int
    stock_slot0: tuple[int, int]
    openwrt_partitions: dict[str, tuple[int, int]]
    firstboot_script: str


RD03V2 = DeviceProfile(
    slug="rd03v2",
    display_name="Xiaomi AX3000T (RD03v2)",
    stock_hardware=("RD03v2",),
    stock_model="xiaomi.router.rd03v2",
    tested_stock_roms=("2.0.28",),
    nvram_model="RD03v2",
    openwrt_board="xiaomi,mi-router-ax3000t-v2",
    stock_host="192.168.31.1",
    openwrt_host="192.168.1.1",
    installer_wifi_ssid="OpenWrt-RD03v2-Installer",
    installer_wifi_key="rd03v2install",
    release_repo="ADCDS/openwrt-xiaomi-ax3000t-rd03v2",
    release_prefix="openwrt-qualcommax-ipq50xx-xiaomi_mi-router-ax3000t-v2",
    default_release="v1.11",
    image_kinds={
        "initramfs_itb": "initramfs-uImage.itb",
        "initramfs_ubi": "initramfs-factory.ubi",
        "sysupgrade": "squashfs-sysupgrade.bin",
    },
    nand_min_version={
        "ESMT F50D1G41LB": (1, 0),
        "ESMT F50D1G41LB (raw id)": (1, 0),
        "Winbond W25N01KW": (1, 7),
        "Winbond W25N01KW (raw id)": (1, 7),
    },
    flash_types={
        "c9": "GigaDevice GD5F1GQ4RE9IH",
        "22": "GigaDevice GD5F2GQ5REYIH",
        "15": "Micron MT29F1G01ABBFDWB-IT",
        "bc": "Winbond W25N01JW",
        "11": "ESMT F50D1G41LB",
        "41": "GigaDevice GD5F1GQ5REYIG",
        "21": "GigaDevice GD5F1GQ5REYIH",
        "bf": "Winbond W25N02JWZEIF",
        "92": "Macronix MX35UF1GE4AC",
        "ba": "Winbond W25N01GWZEIG",
        "81": "GigaDevice GD5F1GM7REYIG",
        "be": "Winbond W25N01KW",
    },
    stock_slots={"rootfs": 0, "rootfs_1": 1},
    required_stock_partitions=(
        "rootfs", "rootfs_1", "overlay", "0:APPSBLENV", "0:ART", "bdata",
        "0:APPSBL",
    ),
    backup_partitions=(
        ("appsblenv", "0:APPSBLENV"),
        ("art", "0:ART"),
        ("bdata", "bdata"),
        ("appsbl", "0:APPSBL"),
    ),
    stock_images={
        "3138342e564c7d7482fde4a90e1778830180f0eac15e1de5f3ad269f9ba9940f":
            "miwifi_rd03v2 2.0.28 (newest, version code 131100)",
        "be7af0e551d440a96757fe885dd775580fd8362addefb594b114f218ccc786c3":
            "miwifi_rd03v2 2.0.12 (version code 131084)",
    },
    stock_image_glob="miwifi_rd03v2_*.bin",
    payload_offset=0x2F4,
    peb_size=131072,
    stock_slot0=(0x0A80000, 0x1E00000),
    openwrt_partitions={
        "ubi_kernel": (0x0A80000, 0x2400000),
        "rootfs": (0x2E80000, 0x5180000),
    },
    firstboot_script="99-rd03v2-firstboot",
)


PROFILES = {RD03V2.slug: RD03V2}


def add_device_argument(parser):
    parser.add_argument(
        "--device", required=True, choices=tuple(sorted(PROFILES)),
        help="device profile; currently only rd03v2 is supported")


def get_profile(slug):
    try:
        return PROFILES[slug]
    except KeyError:
        raise ValueError(
            f"unsupported device {slug!r}; choose one of {', '.join(sorted(PROFILES))}")


def stock_identity_error(profile, info):
    """Return why unauthenticated stock identity is unsupported, or None."""
    hardware = str(info.get("hardware", ""))
    if hardware not in profile.stock_hardware:
        return (f"hardware {hardware!r} does not match the selected "
                f"{profile.display_name} profile")
    model = str(info.get("model", ""))
    if model and model != profile.stock_model:
        return (f"stock model {model!r} does not match {profile.stock_model!r}")
    rom = str(info.get("romversion", ""))
    if rom not in profile.tested_stock_roms:
        return (f"stock ROM {rom!r} is not validated for {profile.slug}; tested: "
                f"{', '.join(profile.tested_stock_roms)}")
    return None
