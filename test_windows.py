"""Windows-path regressions; runnable on Windows and Linux without a router."""
import hashlib
import ipaddress
import json
import os
import socket
import sys
import tempfile
import threading
import unittest
from unittest import mock

import chain
import hostnet
import i18n
import install
import winssh

# Assertions below are on the English text, whatever this machine's language.
os.environ["XIAOMI_INSTALL_LANG"] = "en"


class InstallerWindowsTests(unittest.TestCase):
    def setUp(self):
        self.windows = mock.patch.object(hostnet, "is_windows", return_value=True)
        self.windows.start()
        self.addCleanup(self.windows.stop)
        i18n.set_language("en")

    def test_route_delegation(self):
        with mock.patch.object(hostnet, "route", return_value=("192.168.31.2", "Ethernet")):
            self.assertEqual(chain.local_ip("192.168.31.1"), "192.168.31.2")
            self.assertEqual(chain.local_interface("192.168.31.1"), "Ethernet")

    def test_no_route_reads_as_none_like_linux(self):
        """A rebooting router takes the link's address with it; pollers must survive."""
        with mock.patch.object(hostnet, "route", side_effect=hostnet.NetworkError("no route")):
            self.assertIsNone(chain.local_interface("192.168.31.1"))
            self.assertIsNone(chain.local_ip("192.168.31.1"))
            self.assertEqual(chain.local_mac("192.168.31.1"), "00:00:00:00:00:00")

    def test_rediscovery_survives_link_loss_while_router_reboots(self):
        with mock.patch.object(hostnet, "route", side_effect=hostnet.NetworkError("no route")), \
             mock.patch.object(hostnet, "default_routes", return_value=[]), \
             mock.patch.object(hostnet, "discover_candidates", return_value=["fe80::1%12"]), \
             mock.patch.object(install, "ssh_banner", return_value=("SSH-2.0-dropbear", None)), \
             mock.patch.object(install, "log"):
            self.assertEqual(install.wait_and_discover("Ethernet", deadline_s=30,
                                                       fallback="192.168.1.1"),
                             "fe80::1%12")

    def test_rediscovery_survives_unreadable_route_table(self):
        with mock.patch.object(hostnet, "default_routes", side_effect=hostnet.NetworkError("busy")), \
             mock.patch.object(hostnet, "route", return_value=("192.168.1.2", "Ethernet")), \
             mock.patch.object(hostnet, "discover_candidates", return_value=["fe80::1%12"]), \
             mock.patch.object(install, "ssh_banner", return_value=("SSH-2.0-dropbear", None)) as banner, \
             mock.patch.object(install, "log"):
            self.assertEqual(install.wait_and_discover("Ethernet", deadline_s=30,
                                                       fallback="192.168.1.1"),
                             "fe80::1%12")
            # The IPv4 fallback was skipped, not trusted: only the link-local was probed.
            banner.assert_called_once_with("fe80::1%12", timeout=5)

    def test_transport_and_ipv4(self):
        with mock.patch.object(hostnet, "adapter", return_value={"wireless": True, "ipv4": ["192.168.31.2/24"]}):
            self.assertEqual(install.detect_transport("Wi-Fi"), "wifi")
            self.assertEqual(install.interface_ipv4_networks("Wi-Fi"),
                             [ipaddress.ip_interface("192.168.31.2/24")])

    def test_callback_keeps_wrong_route_guard(self):
        with mock.patch.object(hostnet, "route", return_value=("192.168.31.2", "VPN")):
            with self.assertRaisesRegex(install.Abort, "does not match"):
                install.callback_address("192.168.31.1", "Ethernet")

    def test_callback_keeps_ownership_guard(self):
        with mock.patch.object(hostnet, "route", return_value=("192.168.31.2", "Ethernet")), \
             mock.patch.object(hostnet, "adapter", return_value={"ipv4": ["192.168.31.2/24"]}):
            self.assertEqual(install.callback_address("192.168.31.1", "Ethernet"), "192.168.31.2")
            with self.assertRaisesRegex(install.Abort, "not assigned"):
                install.callback_address("192.168.31.1", "Ethernet", "192.168.31.3")

    def test_manifest_without_posix_calls(self):
        with tempfile.TemporaryDirectory() as directory, \
             mock.patch.object(os, "fchmod", create=True, side_effect=AssertionError("POSIX fchmod")):
            path = os.path.join(directory, "resume.json")
            install._write_resume_data(path, {"phase": "prepared"})
            install._write_resume_data(path, {"phase": "pivot-complete"})
            with open(path) as source:
                self.assertEqual(json.load(source)["phase"], "pivot-complete")
            self.assertFalse(os.path.exists(path + ".tmp"))

    def test_router_scripts_are_utf8_with_unix_newlines(self):
        with tempfile.TemporaryDirectory() as directory:
            script = install.write_tmp(directory, "test.sh", "#!/bin/sh\necho 'Привет'\n")
            with open(script, "rb") as source:
                data = source.read()
            self.assertNotIn(b"\r", data)
            self.assertEqual(data.decode("utf-8"), "#!/bin/sh\necho 'Привет'\n")

    def test_linklocal_requires_unique_dropbear(self):
        with mock.patch.object(hostnet, "discover_candidates", return_value=["fe80::1%12", "fe80::2%12"]), \
             mock.patch.object(install, "ssh_banner", side_effect=[("SSH-2.0-OpenSSH", None), ("SSH-2.0-dropbear", None)]):
            self.assertEqual(install.discover_linklocal("Wi-Fi"), "fe80::2%12")
        with mock.patch.object(hostnet, "discover_candidates", return_value=["fe80::1%12", "fe80::2%12"]), \
             mock.patch.object(install, "ssh_banner", return_value=("SSH-2.0-dropbear", None)):
            with self.assertRaises(install.Abort):
                install.discover_linklocal("Wi-Fi")

    def test_default_gateway_collision_still_blocked(self):
        with mock.patch.object(hostnet, "default_routes", return_value=[("192.168.1.1", "Ethernet")]), \
             mock.patch.object(install, "ssh_banner") as banner:
            with self.assertRaisesRegex(install.Abort, "default gateway"):
                install.check_is_openwrt_ram("192.168.1.1")
            banner.assert_not_called()

    def test_gateway_check_fails_closed_when_routes_are_unreadable(self):
        with mock.patch.object(hostnet, "default_routes", side_effect=hostnet.NetworkError("busy")), \
             mock.patch.object(install, "ssh_banner") as banner:
            with self.assertRaisesRegex(install.Abort, "busy"):
                install.check_is_openwrt_ram("192.168.1.1")
            banner.assert_not_called()

    def test_router_accepted_as_gateway_only_on_its_own_cable(self):
        """Issue #4's rule: the same address via Wi-Fi as well is someone else's router."""
        routes = [("192.168.1.1", "Ethernet")]
        with mock.patch.object(hostnet, "default_routes", return_value=routes), \
             mock.patch.object(hostnet, "route", return_value=("192.168.1.2", "Ethernet")):
            self.assertIsNone(install.gateway_conflict("192.168.1.1", "Ethernet"))
        routes = [("192.168.1.1", "Ethernet"), ("192.168.1.1", "Wi-Fi")]
        with mock.patch.object(hostnet, "default_routes", return_value=routes), \
             mock.patch.object(hostnet, "route", return_value=("192.168.1.2", "Ethernet")):
            self.assertIn("Wi-Fi", install.gateway_conflict("192.168.1.1", "Ethernet"))

    def test_neighbour_mac_uses_scope_or_interface(self):
        with mock.patch.object(hostnet, "neighbor_mac", return_value="aa:bb:cc:dd:ee:ff") as lookup:
            self.assertEqual(install.neighbour_mac("fe80::1%12"), "aa:bb:cc:dd:ee:ff")
            lookup.assert_called_with("fe80::1", "12")
            self.assertEqual(install.neighbour_mac("192.168.1.1", "Ethernet"), "aa:bb:cc:dd:ee:ff")
            lookup.assert_called_with("192.168.1.1", "Ethernet")
        with mock.patch.object(hostnet, "neighbor_mac", side_effect=hostnet.NetworkError("busy")):
            self.assertIsNone(install.neighbour_mac("192.168.1.1", "Ethernet"))

    def test_ram_system_is_offered_only_its_empty_password(self):
        """A configured root password never reaches a merely presumed router."""
        original = install.SSH_PASSWORDS[:]
        self.addCleanup(install.SSH_PASSWORDS.__setitem__, slice(None), original)
        install.SSH_PASSWORDS[:] = ["", "configured-secret"]
        with mock.patch.object(winssh, "run", return_value=(0, "up")) as run:
            install.ssh("192.168.1.1", "echo up", passwords=install.RAM_PASSWORDS)
            self.assertEqual(run.call_args.args[2], ("",))
            install.ssh("192.168.1.1", "echo up")
            self.assertEqual(run.call_args.args[2], ["", "configured-secret"])
        with mock.patch.object(winssh, "upload") as upload:
            install.scp_to("192.168.1.1", "local", "/tmp/x", passwords=install.RAM_PASSWORDS)
            self.assertEqual(upload.call_args.args[3], ("",))

    def test_refused_empty_password_is_recognised(self):
        """wait_for_openwrt turns exactly this message into its explanation."""
        error = winssh.SSHError("Permission denied: none of the configured root passwords worked")
        with mock.patch.object(winssh, "run", side_effect=error):
            rc, out = install.ssh("192.168.1.1", "echo up", check=False, passwords=("",))
        self.assertNotEqual(rc, 0)
        self.assertIn("Permission denied", out)

    def test_canonical_interface_resolves_index_to_alias(self):
        with mock.patch.object(hostnet, "adapter", return_value={"name": "Беспроводная сеть"}):
            self.assertEqual(install.canonical_interface("12"), "Беспроводная сеть")
        self.assertIsNone(install.canonical_interface(None))
        with mock.patch.object(hostnet, "adapter", side_effect=hostnet.NetworkError("not found")):
            with self.assertRaisesRegex(install.Abort, "not found"):
                install.canonical_interface("99")

    def test_updater_copies_files_through_winssh(self):
        import updater
        with mock.patch.object(winssh, "download") as download:
            updater.scp_from("router.test", "/tmp/a", "local")
            self.assertEqual(download.call_args.args[:3], ("router.test", "/tmp/a", "local"))
        with mock.patch.object(winssh, "download", side_effect=winssh.SSHError("gone")):
            with self.assertRaisesRegex(install.Abort, "gone"):
                updater.scp_from("router.test", "/tmp/a", "local")

    def test_list_interfaces_hides_pseudo_adapters(self):
        rows = [{"index": 2, "name": "6to4 Adapter", "wireless": False, "ipv4": [], "hidden": True},
                {"index": 12, "name": "Wi-Fi", "wireless": True, "ipv4": ["192.168.31.2/24"], "hidden": False}]
        shown = []
        with mock.patch.object(hostnet, "adapters", return_value=rows), \
             mock.patch.object(install, "log", side_effect=shown.append):
            self.assertEqual(install.main(["--list-interfaces"]), 0)
        self.assertEqual(len(shown), 1)
        self.assertIn("Wi-Fi", shown[0])

    def test_wizard_lists_real_adapters_and_accepts_their_names(self):
        here = os.path.dirname(os.path.abspath(__file__))
        sys.path.insert(0, os.path.join(here, "gui"))
        self.addCleanup(sys.path.remove, os.path.join(here, "gui"))
        import app
        import prereqs
        import runner
        rows = [{"index": 2, "name": "6to4 Adapter", "wireless": False, "ipv4": [],
                 "hidden": True, "status": "Up"},
                {"index": 12, "name": "Беспроводная сеть", "wireless": True,
                 "ipv4": ["192.168.31.2/24"], "hidden": False, "status": "Up"}]
        with mock.patch.object(hostnet, "adapters", return_value=rows):
            listed = app.list_interfaces()
        self.assertEqual([i["name"] for i in listed], ["Беспроводная сеть"])
        self.assertEqual(listed[0]["transport"], "wifi")
        argv = runner.build_argv("install", {"interface": "Беспроводная сеть", "dry_run": True})
        self.assertEqual(argv[argv.index("--interface") + 1], "Беспроводная сеть")
        for bad in ("-x", "--yes", "a\nb"):
            with self.assertRaises(ValueError):
                runner.build_argv("install", {"interface": bad})
        with mock.patch.object(prereqs.platform, "system", return_value="Windows"), \
             mock.patch.object(prereqs, "_importable", return_value=True), \
             mock.patch.object(prereqs, "_which", return_value=True):
            state = prereqs.collect()
        self.assertTrue(state["ready"])
        self.assertEqual(state["platform"], "windows")
        ids = {check["id"] for check in state["checks"]}
        self.assertIn("paramiko", ids)
        self.assertNotIn("sshpass", ids)
        with mock.patch.object(prereqs.platform, "system", return_value="Windows"), \
             mock.patch.object(prereqs, "_importable", return_value=False), \
             mock.patch.object(prereqs, "_which", return_value=True):
            state = prereqs.collect()
        self.assertFalse(state["ready"])
        self.assertIn("requirements-windows.txt", state["hint_install"])

    def test_next_step_commands_are_for_this_shell(self):
        command = install.rerun_command("--verify-only", r"C:\Users\me\My Files\resume.json")
        self.assertTrue(command.startswith("py -3 "))
        self.assertIn(r'"C:\Users\me\My Files\resume.json"', command)
        self.assertNotIn("python3", install.verification_command("resume.json", "Беспроводная сеть"))
        self.assertIn('"Беспроводная сеть"', install.verification_command("resume.json", "Беспроводная сеть"))

    def test_list_interfaces_read_only(self):
        with mock.patch.object(hostnet, "adapters", return_value=[{"index": 12, "name": "Wi-Fi", "wireless": True, "ipv4": []}]), \
             mock.patch.object(chain, "init_info") as contact, \
             mock.patch.object(install, "log"), \
             mock.patch.object(install.os, "makedirs") as output:
            self.assertEqual(install.main(["--list-interfaces"]), 0)
            contact.assert_not_called()
            output.assert_not_called()

    def test_password_hash_without_openssl(self):
        from passlib.hash import sha512_crypt
        with mock.patch.object(install.subprocess, "run", side_effect=AssertionError("openssl called")):
            result = install.password_hash("test-password")
            self.assertTrue(sha512_crypt.verify("test-password", result))
            self.assertFalse(sha512_crypt.verify("wrong", result))


class FlashOverWindowsTransportTests(unittest.TestCase):
    """The real flash() and verification, with only the wire replaced."""

    BOARD = "xiaomi,mi-router-ax3000t-v2"
    MTD = ('dev:    size   erasesize  name\n'
           'mtd5: 02400000 00020000 "ubi_kernel"\n'
           'mtd6: 05180000 00020000 "rootfs"\n')

    def setUp(self):
        i18n.set_language("en")
        windows = mock.patch.object(hostnet, "is_windows", return_value=True)
        windows.start()
        self.addCleanup(windows.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.calls, self.uploads = [], {}
        self.running = "ram"           # ram -> permanent once acknowledged

    def fake_run(self, host, command, passwords, timeout=120):
        self.calls.append((self.running, command, tuple(passwords)))
        if self.running == "permanent" and "configured-secret" not in passwords:
            return 1, "Permission denied"
        if "board_name" in command:
            return 0, self.BOARD
        if "rootfs_type" in command:
            return 0, "tmpfs" if self.running == "ram" else "overlay"
        if "/proc/mtd" in command:
            return 0, self.MTD
        if "flash_type" in command:
            return 0, "be"
        if "sha256sum" in command:
            return 0, self.sha + "  /tmp/fw.bin"
        if "start-stop-daemon" in command:
            return 0, "rc=0 status=started"
        if "xiaomi-sysupgrade.ack" in command and "touch" in command:
            self.running = "permanent"
            return 0, "ack-sent"
        if "pgrep" in command:
            return 1, ""
        if "echo up" in command:
            return 0, "up"
        return 0, ""

    def fake_upload(self, host, local, remote, passwords, timeout=900):
        with open(local, "rb") as source:
            self.uploads[remote] = source.read()
        self.calls.append((self.running, "UPLOAD " + remote, tuple(passwords)))

    def test_flash_and_verify(self):
        image = os.path.join(self.tmp.name, "sysupgrade.bin")
        with open(image, "wb") as output:
            output.write(b"firmware")
        self.sha = hashlib.sha256(b"firmware").hexdigest()
        manifest = os.path.join(self.tmp.name, "resume.json")
        install._write_resume_data(manifest, {"version": 1, "phase": "pivot-complete"})

        original = install.SSH_PASSWORDS[:]
        self.addCleanup(install.SSH_PASSWORDS.__setitem__, slice(None), original)
        install.SSH_PASSWORDS[:] = ["", "configured-secret"]

        rel = mock.Mock()
        images = {"sysupgrade": {"name": "sysupgrade.bin", "path": image, "sha256": self.sha}}
        with mock.patch.object(winssh, "run", side_effect=self.fake_run), \
             mock.patch.object(winssh, "upload", side_effect=self.fake_upload), \
             mock.patch.object(install, "ssh_banner", return_value=("SSH-2.0-dropbear", None)), \
             mock.patch.object(install.release, "check_nand", return_value=(True, "ok")), \
             mock.patch.object(hostnet, "default_routes", return_value=[("192.168.1.1", "Ethernet")]), \
             mock.patch.object(hostnet, "route", return_value=("192.168.1.2", "Ethernet")), \
             mock.patch.object(hostnet, "neighbor_mac", return_value="aa:bb:cc:dd:ee:ff"), \
             mock.patch.object(install.time, "sleep"), \
             mock.patch.object(install, "log"):
            install.flash("192.168.1.1", rel, images, True, install.devices.RD03V2, "images",
                          manifest_path=manifest, transport="wired",
                          verify_interface="Ethernet", verify_fallback="192.168.1.1",
                          verify_transport="wired", router_iface="Ethernet")

        before = [c for c in self.calls if c[0] == "ram"]
        after = [c for c in self.calls if c[0] == "permanent"]
        self.assertTrue(after, "verification never reached the installed system")
        # The RAM system never sees the configured password...
        self.assertEqual({c[2] for c in before}, {("",)})
        # ...and only the verified MAC gets it afterwards: recorded in the manifest.
        with open(manifest) as source:
            recorded = json.load(source)
        self.assertEqual(recorded["router_mac"], "aa:bb:cc:dd:ee:ff")
        self.assertEqual(recorded["phase"], "flash-complete")
        self.assertIn("configured-secret", after[-1][2])
        # What runs under the router's /bin/sh must not gain a carriage return.
        self.assertEqual(self.uploads["/tmp/fw.bin"], b"firmware")
        self.assertNotIn(b"\r", self.uploads["/tmp/run-sysupgrade.sh"])
        self.assertTrue(self.uploads["/tmp/run-sysupgrade.sh"].startswith(b"#!/bin/sh\n"))

    def test_refuses_to_flash_anything_but_the_ram_system(self):
        """install.py's own guard: an in-place sysupgrade bricks this board."""
        self.running = "nand"
        image_sha = "0" * 64
        rel = mock.Mock()
        images = {"sysupgrade": {"name": "s.bin", "path": os.devnull, "sha256": image_sha}}

        def on_nand(host, command, passwords, timeout=120):
            self.calls.append(command)
            if "rootfs_type" in command:
                return 0, "overlay"
            if "board_name" in command:
                return 0, self.BOARD
            return 0, "up"

        with mock.patch.object(winssh, "run", side_effect=on_nand), \
             mock.patch.object(winssh, "upload") as upload, \
             mock.patch.object(install, "ssh_banner", return_value=("SSH-2.0-dropbear", None)), \
             mock.patch.object(hostnet, "default_routes", return_value=[]), \
             mock.patch.object(install, "log"):
            with self.assertRaisesRegex(install.Abort, "not tmpfs"):
                install.flash("192.168.1.1", rel, images, True, install.devices.RD03V2, "images",
                              manifest_path="unused", verify_interface="Ethernet")
            upload.assert_not_called()


class SSHCleanupTests(unittest.TestCase):
    """Force the disconnect race; real loopback timing is nondeterministic."""

    def session(self, error):
        channel = mock.Mock()
        channel.recv_ready.return_value = False
        channel.recv_stderr_ready.return_value = False
        channel.exit_status_ready.return_value = True
        channel.recv_exit_status.return_value = 0
        channel.close.side_effect = error
        transport = mock.Mock()
        transport.open_session.return_value = channel
        return transport, channel

    def test_run_preserves_result_and_exit_status(self):
        for error in (EOFError(), ConnectionResetError("peer closed")):
            with self.subTest(error=type(error).__name__):
                transport, channel = self.session(error)
                channel.recv_ready.side_effect = [True, False, False]
                channel.recv.return_value = b"command output\n"
                channel.recv_exit_status.return_value = 7
                with mock.patch.object(winssh, "_authenticated", return_value=transport):
                    self.assertEqual(winssh.run("router.test", "test", [""]),
                                     (7, "command output"))
                channel.close.assert_called_once_with()
                transport.close.assert_called_once_with()

    def test_run_preserves_command_error(self):
        transport, channel = self.session(EOFError())
        channel.exec_command.side_effect = OSError("command connection lost")
        with mock.patch.object(winssh, "_authenticated", return_value=transport):
            with self.assertRaisesRegex(winssh.SSHError, "command connection lost"):
                winssh.run("router.test", "test", [""])
        transport.close.assert_called_once_with()

    def test_upload_preserves_success(self):
        for error in (EOFError(), BrokenPipeError("peer closed")):
            with self.subTest(error=type(error).__name__):
                transport, channel = self.session(error)
                channel.recv.return_value = b"\x00"
                with tempfile.TemporaryDirectory() as directory:
                    local = os.path.join(directory, "image.bin")
                    with open(local, "wb") as output:
                        output.write(b"firmware")
                    with mock.patch.object(winssh, "_authenticated", return_value=transport):
                        self.assertIsNone(winssh.upload("router.test", local, "/tmp/image.bin", [""]))
                channel.close.assert_called_once_with()
                transport.close.assert_called_once_with()

    def test_upload_preserves_receiver_error(self):
        transport, channel = self.session(EOFError())
        channel.recv.side_effect = [b"\x01", b"n", b"o", b"\n"]
        with mock.patch.object(winssh, "_authenticated", return_value=transport):
            with self.assertRaisesRegex(winssh.SSHError, "receiver rejected transfer: no"):
                winssh.upload("router.test", "unused", "/tmp/image.bin", [""])
        transport.close.assert_called_once_with()

    def test_open_session_error_still_closes_transport(self):
        transport, _ = self.session(None)
        transport.open_session.side_effect = EOFError("session connection lost")
        transport.close.side_effect = EOFError("already closed")
        with mock.patch.object(winssh, "_authenticated", return_value=transport):
            with self.assertRaisesRegex(winssh.SSHError, "session connection lost"):
                winssh.run("router.test", "test", [""])
        transport.close.assert_called_once_with()

    def test_unexpected_cleanup_error_is_not_silenced(self):
        transport, channel = self.session(ValueError("cleanup bug"))
        with self.assertRaisesRegex(ValueError, "cleanup bug"):
            winssh._close_session(transport, channel)
        transport.close.assert_called_once_with()


class SSHTransportTests(unittest.TestCase):
    """Real loopback SSH/SCP exchange, not just mocked transport calls."""
    @classmethod
    def setUpClass(cls):
        cls.paramiko = winssh.dependencies()
        cls.key = cls.paramiko.RSAKey.generate(2048)

    def server(self, handler, none_auth=False):
        paramiko = self.paramiko
        command_ready = threading.Event()
        commands, errors = [], []

        class Server(paramiko.ServerInterface):
            def check_auth_none(self, username):
                return paramiko.AUTH_SUCCESSFUL if none_auth else paramiko.AUTH_FAILED

            def get_allowed_auths(self, username):
                return "none" if none_auth else "password"

            def check_auth_password(self, username, password):
                return paramiko.AUTH_SUCCESSFUL if username == "root" and password == "secret" else paramiko.AUTH_FAILED

            def check_channel_request(self, kind, chanid):
                return paramiko.OPEN_SUCCEEDED if kind == "session" else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

            def check_channel_exec_request(self, channel, command):
                commands.append(command.decode())
                command_ready.set()
                return True

        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(5)
        listener.settimeout(10)
        port = listener.getsockname()[1]

        def serve():
            transport = None
            try:
                # Wrong-password clients close without opening a channel.
                for _ in range(3):
                    connection, _ = listener.accept()
                    transport = paramiko.Transport(connection)
                    transport.add_server_key(self.key)
                    transport.start_server(server=Server())
                    channel = transport.accept(5)
                    if channel is None:
                        transport.close()
                        continue
                    channel.settimeout(10)
                    if not command_ready.wait(5):
                        raise AssertionError("no exec request")
                    handler(channel, commands[-1])
                    try:
                        channel.close()
                    except (EOFError, OSError):
                        # The client can drop the connection the moment it has
                        # the last byte; closing a channel nobody is listening
                        # on then fails at the packet layer, and that is not a
                        # server-side error worth failing the test for.
                        pass
                    break
            except Exception as exc:
                errors.append(exc)
            finally:
                if transport:
                    transport.close()
                listener.close()

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        original_connect = socket.create_connection

        def connect(address, **kwargs):
            self.assertEqual(address, ("router.test", 22))
            return original_connect(("127.0.0.1", port), **kwargs)

        return thread, errors, mock.patch.object(winssh.socket, "create_connection", side_effect=connect)

    def finish(self, thread, errors):
        thread.join(15)
        self.assertFalse(thread.is_alive(), "SSH server did not finish")
        self.assertEqual(errors, [])

    def test_password_retry_and_large_streams(self):
        def handler(channel, command):
            self.assertEqual(command, "read-only-test")
            channel.sendall(b"A" * 180000)
            channel.sendall_stderr(b"B" * 180000)
            channel.send_exit_status(7)
        thread, errors, connection = self.server(handler)
        with connection:
            code, output = winssh.run("router.test", "read-only-test", ["wrong", "secret"], timeout=15)
        self.assertEqual(code, 7)
        self.assertEqual(output, "A" * 180000 + "B" * 180000)
        self.finish(thread, errors)

    def test_passwordless_ram_auth(self):
        def handler(channel, command):
            channel.sendall(b"up\n")
            channel.send_exit_status(0)
        thread, errors, connection = self.server(handler, none_auth=True)
        with connection:
            self.assertEqual(winssh.run("router.test", "echo up", [""]), (0, "up"))
        self.finish(thread, errors)

    def test_legacy_scp_binary_transfer(self):
        payload = os.urandom(200001)
        received = []

        def exact(channel, count):
            data = bytearray()
            while len(data) < count:
                chunk = channel.recv(count - len(data))
                if not chunk:
                    raise AssertionError("unexpected EOF")
                data.extend(chunk)
            return bytes(data)

        def handler(channel, command):
            self.assertEqual(command, "scp -t '/tmp/image with spaces.bin'")
            channel.sendall(b"\x00")
            header = bytearray()
            while not header.endswith(b"\n"):
                header.extend(exact(channel, 1))
            self.assertEqual(header, f"C0600 {len(payload)} upload.bin\n".encode())
            channel.sendall(b"\x00")
            received.append(exact(channel, len(payload)))
            self.assertEqual(exact(channel, 1), b"\x00")
            channel.sendall(b"\x00")
            self.assertEqual(channel.recv(1), b"")
            channel.send_exit_status(0)

        thread, errors, connection = self.server(handler)
        with tempfile.TemporaryDirectory() as directory:
            local = os.path.join(directory, "firmware.bin")
            with open(local, "wb") as output:
                output.write(payload)
            with connection:
                winssh.upload("router.test", local, "/tmp/image with spaces.bin", ["secret"])
        self.finish(thread, errors)
        self.assertEqual(hashlib.sha256(received[0]).digest(), hashlib.sha256(payload).digest())

    def test_legacy_scp_download(self):
        payload = os.urandom(150001)

        def exact(channel, count):
            data = bytearray()
            while len(data) < count:
                chunk = channel.recv(count - len(data))
                if not chunk:
                    raise AssertionError("unexpected EOF")
                data.extend(chunk)
            return bytes(data)

        def handler(channel, command):
            self.assertEqual(command, "scp -f '/tmp/config backup.tar.gz'")
            self.assertEqual(exact(channel, 1), b"\x00")
            channel.sendall(f"C0600 {len(payload)} config backup.tar.gz\n".encode())
            self.assertEqual(exact(channel, 1), b"\x00")
            channel.sendall(payload)
            channel.sendall(b"\x00")
            self.assertEqual(exact(channel, 1), b"\x00")
            channel.send_exit_status(0)

        thread, errors, connection = self.server(handler)
        with tempfile.TemporaryDirectory() as directory:
            local = os.path.join(directory, "backup.tar.gz")
            with connection:
                winssh.download("router.test", "/tmp/config backup.tar.gz", local, ["secret"])
            with open(local, "rb") as source:
                self.assertEqual(source.read(), payload)
            self.assertFalse(os.path.exists(local + ".part"))
        self.finish(thread, errors)

    def test_download_leaves_nothing_when_refused(self):
        def handler(channel, command):
            self.assertEqual(channel.recv(1), b"\x00")
            channel.sendall(b"\x01scp: no such file\n")

        thread, errors, connection = self.server(handler)
        with tempfile.TemporaryDirectory() as directory:
            local = os.path.join(directory, "backup.tar.gz")
            with connection:
                with self.assertRaisesRegex(winssh.SSHError, "no such file"):
                    winssh.download("router.test", "/tmp/missing", local, ["secret"])
            self.assertEqual(os.listdir(directory), [])
        self.finish(thread, errors)

    def test_download_refuses_bad_source_before_connecting(self):
        with mock.patch.object(winssh, "_authenticated") as connect:
            for remote in ("relative", "/tmp/evil\nname"):
                with self.assertRaises(winssh.SSHError):
                    winssh.download("router.test", remote, "unused", [""])
            connect.assert_not_called()

    def test_receiver_error(self):
        channel = mock.Mock()
        channel.recv.side_effect = [b"\x01", b"n", b"o", b"\n"]
        with self.assertRaisesRegex(winssh.SSHError, "no"):
            winssh._ack(channel)

    def test_invalid_destination_before_connect(self):
        with mock.patch.object(winssh, "_authenticated") as connect:
            for remote in ("relative", "/tmp/evil\nname", "/tmp/evil\x00name"):
                with self.assertRaises(winssh.SSHError):
                    winssh.upload("router.test", "unused", remote, [""])
            connect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
