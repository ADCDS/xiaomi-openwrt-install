"""Password SSH and legacy SCP for Windows, including Dropbear without SFTP.

Used only on the isolated router link. As with the existing Unix SSH commands,
host keys are not trusted/persisted: board and RAM-system checks are the caller's
responsibility. Never use this transport against an untrusted network.

Originally written by alexsys87 for the native Windows port
(https://github.com/alexsys87/xiaomi-openwrt-install).
"""

import os
import shlex
import socket
import time


class SSHError(Exception):
    pass


def dependencies():
    try:
        import paramiko
    except ImportError as exc:
        raise SSHError("Windows dependencies are missing; run: "
                       "py -3 -m pip install -r requirements-windows.txt") from exc
    return paramiko


def _connect(host, password, timeout):
    paramiko = dependencies()
    sock = socket.create_connection((host.strip("[]"), 22), timeout=min(15, timeout))
    transport = paramiko.Transport(sock)
    try:
        transport.start_client(timeout=min(15, timeout))
        transport.auth_timeout = min(15, timeout)
        if password == "":
            try:
                transport.auth_none("root")
            except paramiko.AuthenticationException:
                transport.auth_password("root", password)
        else:
            transport.auth_password("root", password)
        if not transport.is_authenticated():
            raise paramiko.AuthenticationException("root authentication failed")
        return transport
    except Exception:
        transport.close()
        sock.close()
        raise


def _authenticated(host, passwords, timeout):
    paramiko = dependencies()
    for password in passwords:
        try:
            return _connect(host, password, timeout)
        except paramiko.AuthenticationException:
            continue
        except (OSError, paramiko.SSHException) as exc:
            raise SSHError(f"SSH connection to {host} failed: {exc}") from exc
    raise SSHError("Permission denied: none of the configured root passwords worked")


def _close_session(transport, channel):
    """Best-effort cleanup: a peer may close just after sending exit status.

    Ignore only EOF/socket errors during cleanup, not during SSH/SCP work.
    Always close the transport even if closing its channel fails.
    """
    try:
        if channel is not None:
            try:
                channel.close()
            except (EOFError, OSError):
                pass
    finally:
        try:
            transport.close()
        except (EOFError, OSError):
            pass


def run(host, command, passwords, timeout=120):
    """Drain both streams before collecting status (large output cannot deadlock)."""
    transport = _authenticated(host, passwords, timeout)
    channel = None
    try:
        channel = transport.open_session(timeout=min(15, timeout))
        channel.settimeout(timeout)
        channel.exec_command(command)
        end = time.monotonic() + timeout
        stdout, stderr = bytearray(), bytearray()
        while True:
            while channel.recv_ready():
                stdout.extend(channel.recv(65536))
                if time.monotonic() >= end:
                    raise SSHError(f"SSH command timed out after {timeout}s")
            while channel.recv_stderr_ready():
                stderr.extend(channel.recv_stderr(65536))
                if time.monotonic() >= end:
                    raise SSHError(f"SSH command timed out after {timeout}s")
            if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
                break
            if time.monotonic() >= end:
                raise SSHError(f"SSH command timed out after {timeout}s")
            time.sleep(0.01)
        return channel.recv_exit_status(), (stdout + stderr).decode("utf-8", "replace").strip()
    except SSHError:
        raise
    except Exception as exc:
        raise SSHError(f"SSH command failed: {exc}") from exc
    finally:
        _close_session(transport, channel)


def _ack(channel):
    code = channel.recv(1)
    if code == b"\x00":
        return
    message = bytearray()
    if code in (b"\x01", b"\x02"):
        while len(message) < 4096:
            byte = channel.recv(1)
            if not byte or byte == b"\n":
                break
            message.extend(byte)
    raise SSHError("SCP receiver rejected transfer: " +
                   (message.decode("utf-8", "replace") or "connection closed"))


def download(host, remote, local, passwords, timeout=300):
    """Fetch a file with legacy SCP, the counterpart of upload."""
    if not remote.startswith("/") or any(c in remote for c in "\r\n\x00"):
        raise SSHError("SCP source must be an absolute remote path")
    transport = _authenticated(host, passwords, timeout)
    channel = None
    partial = local + ".part"
    try:
        channel = transport.open_session(timeout=min(15, timeout))
        channel.settimeout(min(30, timeout))
        channel.exec_command("scp -f " + shlex.quote(remote))
        channel.sendall(b"\x00")
        header = bytearray()
        while not header.endswith(b"\n"):
            byte = channel.recv(1)
            if not byte or len(header) >= 4096:
                raise SSHError("SCP sender closed before naming the file")
            header.extend(byte)
        if header[:1] in (b"\x01", b"\x02"):
            raise SSHError("SCP sender refused: " +
                           header[1:].decode("utf-8", "replace").strip())
        fields = header.decode("utf-8", "replace").split(" ", 2)
        if len(fields) != 3 or not fields[0].startswith("C") or not fields[1].isdigit():
            raise SSHError(f"unexpected SCP header: {header[:80]!r}")
        remaining = int(fields[1])
        channel.sendall(b"\x00")
        end = time.monotonic() + timeout
        with open(partial, "wb") as output:
            while remaining:
                if time.monotonic() >= end:
                    raise SSHError(f"SCP transfer timed out after {timeout}s")
                chunk = channel.recv(min(65536, remaining))
                if not chunk:
                    raise SSHError("SCP sender closed mid-file")
                output.write(chunk)
                remaining -= len(chunk)
        _ack(channel)
        channel.sendall(b"\x00")
        os.replace(partial, local)
    except SSHError:
        raise
    except Exception as exc:
        raise SSHError(f"SCP transfer failed: {exc}") from exc
    finally:
        if os.path.exists(partial):
            os.remove(partial)
        _close_session(transport, channel)


def upload(host, local, remote, passwords, timeout=900):
    """Send legacy SCP; OpenWrt initramfs need not ship an SFTP subsystem."""
    if not remote.startswith("/") or any(c in remote for c in "\r\n\x00"):
        raise SSHError("SCP destination must be an absolute remote path")
    transport = _authenticated(host, passwords, timeout)
    channel = None
    try:
        channel = transport.open_session(timeout=min(15, timeout))
        channel.settimeout(min(30, timeout))
        channel.exec_command("scp -t " + shlex.quote(remote))
        _ack(channel)
        with open(local, "rb") as source:
            size = os.fstat(source.fileno()).st_size
            # The receiver target is an explicit path; a fixed safe filename
            # avoids Windows paths/backslashes or newlines entering SCP headers.
            channel.sendall(f"C0600 {size} upload.bin\n".encode("ascii"))
            _ack(channel)
            remaining = size
            end = time.monotonic() + timeout
            while remaining:
                if time.monotonic() >= end:
                    raise SSHError(f"SCP transfer timed out after {timeout}s")
                chunk = source.read(min(65536, remaining))
                if not chunk:
                    raise SSHError("local file changed during SCP transfer")
                channel.sendall(chunk)
                remaining -= len(chunk)
            channel.sendall(b"\x00")
            _ack(channel)
        channel.shutdown_write()
        while not channel.exit_status_ready():
            if time.monotonic() >= end:
                raise SSHError(f"SCP receiver timed out after {timeout}s")
            # Drain diagnostic output too, rather than blocking exit status.
            if channel.recv_ready():
                channel.recv(65536)
            if channel.recv_stderr_ready():
                channel.recv_stderr(65536)
            time.sleep(0.01)
        status = channel.recv_exit_status()
        if status != 0:
            raise SSHError(f"SCP receiver exited with status {status}")
    except SSHError:
        raise
    except Exception as exc:
        raise SSHError(f"SCP transfer failed: {exc}") from exc
    finally:
        _close_session(transport, channel)
