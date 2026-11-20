"""updater.py --ssh-key-auth and the shared OpenSSH key path; no real network access."""
import itertools
import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import devices
import hostnet
import install
import restore
import updater


def done(rc=0, out="", err=""):
    return subprocess.CompletedProcess([], rc, out, err)


class KeyAuthCase(unittest.TestCase):
    def setUp(self):
        language = patch.dict(install.os.environ, {"XIAOMI_INSTALL_LANG": "en"})
        language.start()
        self.addCleanup(language.stop)
        install.i18n.set_language("en")
        saved = install.KEY_AUTH, install.VERIFY_SSH_KEY
        self.addCleanup(lambda: setattr(install, "KEY_AUTH", saved[0]))
        self.addCleanup(lambda: setattr(install, "VERIFY_SSH_KEY", saved[1]))
        install.VERIFY_SSH_KEY = None
        install.KEY_AUTH = install.KeyAuth()
        flag =patch.object(hostnet, "scp_legacy_flag", return_value=("-O",))
        flag.start()
        self.addCleanup(flag.stop)
        # sshpass being installed must change nothing under key auth.
        which = patch.object(install.shutil, "which", return_value="/usr/bin/sshpass")
        which.start()
        self.addCleanup(which.stop)


class CommandTests(KeyAuthCase):
    FORBIDDEN = ("StrictHostKeyChecking=no", "UserKnownHostsFile=/dev/null",
                 "PubkeyAuthentication=no", "-i", "sshpass", "IdentitiesOnly=yes")

    def test_ssh_respects_the_ssh_config(self):
        with patch.object(install.subprocess, "run", return_value=done(0, "up")) as run:
            self.assertEqual(install.ssh("192.168.31.1", "echo up"), (0, "up"))
        argv = run.call_args.args[0]
        self.assertEqual(argv[0], "ssh")
        for option in ("BatchMode=yes", "ConnectTimeout=10", "PasswordAuthentication=no",
                       "KbdInteractiveAuthentication=no", "StrictHostKeyChecking=yes"):
            self.assertIn(option, argv)
        for option in self.FORBIDDEN:
            self.assertNotIn(option, argv)
        self.assertEqual(argv[-2:], ["root@192.168.31.1", "echo up"])
        self.assertIsNone(run.call_args.kwargs.get("env"))

    def test_output_stays_clean_and_nothing_is_forwarded(self):
        # ~/.ssh/config is honoured, except where it could put client chatter
        # into parsed output, reuse a master verified some other way, or hand
        # this host's agent/X11/ports to the router.
        for argv in (install.KeyAuth().options(), install.KeyAuth("/k").options()):
            for option in ("LogLevel=ERROR", "RequestTTY=no", "PermitLocalCommand=no",
                           "RemoteCommand=none", "ControlMaster=no", "ControlPath=none",
                           "ForwardAgent=no", "ForwardX11=no", "ClearAllForwardings=yes"):
                self.assertIn(option, argv)
                self.assertEqual(argv[argv.index(option) - 1], "-o")

    def test_ssh_never_reads_the_terminal(self):
        with patch.object(install.subprocess, "run", return_value=done(0, "up")) as run:
            install.ssh("192.168.31.1", "echo up")
        self.assertIs(run.call_args.kwargs.get("stdin"), subprocess.DEVNULL)
        install.KEY_AUTH = None
        with patch.object(install.subprocess, "run", return_value=done(0, "up")) as run:
            install.ssh("192.168.31.1", "echo up")
        self.assertIs(run.call_args.kwargs.get("stdin"), subprocess.DEVNULL)

    def test_a_scope_id_must_name_an_interface(self):
        for good in ("192.168.31.1", "fe80::1%en0", "fe80::1%12", "fe80::1%br-lan.1"):
            install.numeric_address(good)
        for bad in ("fe80::1%;reboot", "fe80::1%$(id)", "192.168.31.1%en0", "fe80::1%",
                    "router.lan"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                install.numeric_address(bad)

    def test_host_key_failure_warns_about_the_ram_installer(self):
        with patch.object(install.subprocess, "run",
                          return_value=done(255, "", "Host key verification failed.")):
            with self.assertRaisesRegex(install.Abort, "RAM installer.*do not add"):
                install.ssh("192.168.31.1", "echo up", check=False)

    def test_identity_is_the_only_key_offered(self):
        install.KEY_AUTH = install.KeyAuth("/keys/router key")
        with patch.object(install.subprocess, "run", return_value=done()) as run:
            install.ssh("192.168.31.1", "true")
        argv = run.call_args.args[0]
        self.assertEqual(argv[argv.index("-i") + 1], "/keys/router key")
        self.assertIn("IdentitiesOnly=yes", argv)

    def test_alias_replaces_only_the_numeric_host(self):
        install.KEY_AUTH = install.KeyAuth(alias="ax3000t", alias_for="192.168.31.1")
        with patch.object(install.subprocess, "run", return_value=done()) as run:
            install.ssh("192.168.31.1", "true")
            self.assertEqual(run.call_args.args[0][-2], "ax3000t")
            install.ssh("fe80::1%en0", "true")
            self.assertEqual(run.call_args.args[0][-2], "root@fe80::1%en0")

    def test_scp_uses_legacy_protocol_and_brackets(self):
        with patch.object(install.subprocess, "run", return_value=done()) as run:
            install.scp_to("fe80::1%en0", "/local/fw.bin", "/tmp/fw.bin")
            argv = run.call_args.args[0]
            self.assertEqual(argv[:2], ["scp", "-O"])
            self.assertEqual(argv[-2:], ["/local/fw.bin", "root@[fe80::1%en0]:/tmp/fw.bin"])
            for option in self.FORBIDDEN:
                self.assertNotIn(option, argv)
            updater.scp_from("192.168.31.1", "/tmp/b.tar.gz", "/local/b.tar.gz")
            argv = run.call_args.args[0]
            self.assertEqual(argv[:2], ["scp", "-O"])
            self.assertEqual(argv[-2:], ["root@192.168.31.1:/tmp/b.tar.gz", "/local/b.tar.gz"])

    def test_scp_to_an_alias(self):
        install.KEY_AUTH = install.KeyAuth(alias="ax3000t", alias_for="192.168.31.1")
        with patch.object(install.subprocess, "run", return_value=done()) as run:
            install.scp_to("192.168.31.1", "/l", "/tmp/r")
        self.assertEqual(run.call_args.args[0][-1], "ax3000t:/tmp/r")

    def test_scp_follows_an_old_client(self):
        with patch.object(hostnet, "scp_legacy_flag", return_value=()), \
             patch.object(install.subprocess, "run", return_value=done()) as run:
            install.scp_to("192.168.31.1", "/l", "/r")
        self.assertNotIn("-O", run.call_args.args[0])

    def test_scp_failure_does_not_retry_with_a_password(self):
        with patch.object(install.subprocess, "run",
                          return_value=done(255, "", "Permission denied (publickey).")) as run:
            with self.assertRaisesRegex(install.Abort, "scp with the SSH key"):
                install.scp_to("192.168.31.1", "/l", "/r")
        run.assert_called_once()

    def test_refused_key_is_not_followed_by_a_password(self):
        with patch.object(install.subprocess, "run",
                          return_value=done(255, "", "root@192.168.31.1: Permission denied (publickey).")) as run:
            rc, out = install.ssh("192.168.31.1", "echo up", check=False)
            self.assertEqual(rc, 255)
            with self.assertRaisesRegex(install.Abort, "SSH key login to 192.168.31.1 failed"):
                install.ssh("192.168.31.1", "echo up")
        self.assertEqual(run.call_count, 2)
        for call in run.call_args_list:
            self.assertNotIn("sshpass", call.args[0])

    def test_host_key_mismatch_always_stops(self):
        err = ("@@@ WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED! @@@\n"
               "Host key verification failed.")
        with patch.object(install.subprocess, "run", return_value=done(255, "", err)):
            with self.assertRaisesRegex(install.Abort, "known_hosts"):
                install.ssh("192.168.31.1", "true", check=False)
            with self.assertRaisesRegex(install.Abort, "known_hosts"):
                install.scp_to("192.168.31.1", "/l", "/r")

    def test_a_failing_command_is_not_an_ssh_failure(self):
        with patch.object(install.subprocess, "run", return_value=done(1, "", "no such file")):
            self.assertEqual(install.ssh("192.168.31.1", "cat /x", check=False), (1, "no such file"))
            with self.assertRaises(install.Abort) as caught:
                install.ssh("192.168.31.1", "cat /x")
        self.assertNotIn("SSH key login", str(caught.exception))
        self.assertIsNone(install.key_failure(1, "Permission denied"))
        self.assertEqual(install.key_failure(255, "Connection refused"), "connection")

    def test_ram_installer_calls_keep_the_empty_password(self):
        with patch.object(hostnet, "is_windows", return_value=False), \
             patch.object(install.subprocess, "run", return_value=done(0, "tmpfs")) as run:
            install.ssh("192.168.1.1", "rootfs_type", passwords=install.RAM_PASSWORDS)
            argv = run.call_args.args[0]
            self.assertEqual(argv[:2], ["sshpass", "-e"])
            self.assertIn("PubkeyAuthentication=no", argv)
            install.scp_to("192.168.1.1", "/l", "/r", passwords=install.RAM_PASSWORDS)
            self.assertEqual(run.call_args.args[0][:2], ["sshpass", "-e"])

    def test_restore_ssh_shares_the_key_path(self):
        with patch.object(install.subprocess, "run", return_value=done(0, "xiaomi,mi-router-ax3000t")) as run:
            self.assertEqual(restore.ssh("192.168.31.1", "board_name"),
                             (0, "xiaomi,mi-router-ax3000t"))
        self.assertIn("BatchMode=yes", run.call_args.args[0])
        with patch.object(install.subprocess, "run", return_value=done(255, "", "Permission denied")):
            with self.assertRaises(restore.RestoreError):
                restore.ssh("192.168.31.1", "board_name")


class VerificationTests(KeyAuthCase):
    def verify(self, answer):
        clock = itertools.chain([0.0, 0.0, 0.5], itertools.repeat(10.0))
        with patch.object(install, "check_is_openwrt_ram"), \
             patch.object(install, "ssh", side_effect=answer) as ssh, \
             patch.object(install.time, "time", side_effect=lambda: next(clock)), \
             patch.object(install.time, "sleep"), \
             patch.object(install, "log"):
            try:
                install.verify_permanent_system(devices.RD03V2, host="192.168.31.1",
                                                host_iface="en0", deadline_s=1)
            except install.Abort as exc:
                return str(exc), ssh
        return None, ssh

    def test_command_failure_is_retried_not_reported_as_a_key_failure(self):
        error, _ssh = self.verify(lambda *a, **k: (1, "sh: board_name: not found"))
        self.assertIn("permanent OpenWrt was not verified", error)
        self.assertNotIn("Key verification failed", error)

    def test_dropped_connection_is_retried(self):
        error, _ssh = self.verify(lambda *a, **k: (255, "Connection refused"))
        self.assertIn("permanent OpenWrt was not verified", error)

    def test_refused_key_stops_at_once(self):
        error, ssh = self.verify(lambda *a, **k: (255, "Permission denied (publickey)"))
        self.assertIn("Key verification failed", error)
        self.assertEqual(ssh.call_count, 3)

    def test_gateway_is_accepted_on_the_router_interface(self):
        with patch.object(install, "check_is_openwrt_ram") as check, \
             patch.object(install, "ssh", return_value=(255, "Permission denied")), \
             patch.object(install, "log"):
            with self.assertRaises(install.Abort):
                install.verify_permanent_system(devices.RD03V2, host="192.168.31.1",
                                                host_iface="en0")
        self.assertEqual(check.call_args.kwargs["router_iface"], "en0")
        self.assertIsNone(check.call_args.kwargs["router_mac"])

    def test_continuation_verifies_by_key(self):
        install.KEY_AUTH = install.KeyAuth("/k/id", alias="ax", alias_for="192.168.31.1")
        command = install.verification_command("/out/resume.json", "en0", host="192.168.31.1")
        for part in ("--verify-host 192.168.31.1", "--verify-ssh-key /k/id",
                     "--verify-ssh-alias ax"):
            self.assertIn(part, command)


class GatewayTests(unittest.TestCase):
    def args(self, **kw):
        values = dict(ssh_key_auth=False, transport="wired")
        values.update(kw)
        return type("Args", (), values)()

    def test_gateway_exemption_only_with_keys_or_the_empty_password(self):
        with patch.object(install, "SSH_PASSWORDS", [""]):
            self.assertTrue(updater.gateway_allowed(self.args()))
            self.assertFalse(updater.gateway_allowed(self.args(transport="wifi")))
            self.assertTrue(updater.gateway_allowed(self.args(ssh_key_auth=True, transport="wifi")))
        with patch.object(install, "SSH_PASSWORDS", ["", "secret"]):
            self.assertFalse(updater.gateway_allowed(self.args()))
            self.assertTrue(updater.gateway_allowed(self.args(ssh_key_auth=True)))

    def test_password_mode_still_refuses_the_gateway(self):
        # The rule the exemption leaves alone: without router_iface any
        # gateway is refused before anything is offered.
        with patch.object(install, "default_routes", return_value=[("192.168.31.1", "en0")]), \
             patch.object(install, "ssh_banner") as banner:
            with self.assertRaisesRegex(install.Abort, "default gateway"):
                install.check_is_openwrt_ram("192.168.31.1")
        banner.assert_not_called()


class OptionTests(unittest.TestCase):
    def setUp(self):
        install.i18n.set_language("en")
        saved = install.KEY_AUTH
        self.addCleanup(lambda: setattr(install, "KEY_AUTH", saved))

    def parse(self, *argv):
        return updater.parse_args(list(argv))

    def test_invalid_combinations(self):
        cases = [
            (["--ssh-identity", "/x"], "require --ssh-key-auth"),
            (["--ssh-key-auth", "--root-password", "pw"], "drop --root-password"),
            (["--ssh-key-auth", "--no-keep-settings"], "settings kept"),
            (["--ssh-key-auth", "--ssh-alias", "ax"], "numeric"),
            (["--ssh-key-auth", "--ssh-alias", "ax", "--host", "router.lan"], "numeric"),
            (["--ssh-key-auth", "--ssh-identity", "/does/not/exist"], "does not exist"),
        ]
        for argv, message in cases:
            with self.subTest(argv=argv), patch.dict(os.environ, {}, clear=False):
                os.environ.pop("RD03V2_ROOT_PASSWORD", None)
                with self.assertRaisesRegex(install.Abort, message):
                    updater.check_key_options(self.parse(*argv))

    def test_alias_must_resolve_to_host_and_root(self):
        def ssh_g(hostname, user):
            return done(0, f"user {user}\nhostname {hostname}\nport 22\n")
        with patch.object(install.subprocess, "run", return_value=ssh_g("192.168.31.1", "root")):
            install.require_alias_matches("ax", "192.168.31.1")
        with patch.object(install.subprocess, "run", return_value=ssh_g("10.0.0.1", "root")):
            with self.assertRaisesRegex(install.Abort, "connects to 10.0.0.1"):
                install.require_alias_matches("ax", "192.168.31.1")
        with patch.object(install.subprocess, "run", return_value=ssh_g("192.168.31.1", "lucas")):
            with self.assertRaisesRegex(install.Abort, "logs in as lucas"):
                install.require_alias_matches("ax", "192.168.31.1")

    def test_key_mode_needs_neither_sshpass_nor_openssl(self):
        present = {"ssh", "scp", "ip"}
        with patch.object(hostnet, "is_windows", return_value=False), \
             patch.object(install.shutil, "which", side_effect=lambda t: t if t in present else None):
            install.require_host_tools(key=True)
            with self.assertRaisesRegex(install.Abort, "sshpass"):
                install.require_host_tools()

    def test_refused_key_at_preflight_names_the_ram_installer(self):
        install.KEY_AUTH = install.KeyAuth()
        with patch.object(install, "check_is_openwrt_ram") as check, \
             patch.object(install, "ssh", return_value=(255, "Permission denied (publickey)")) as ssh:
            with self.assertRaisesRegex(install.Abort, "without --ssh-key-auth"):
                updater.identify("192.168.31.1", devices.RD03V2, router_iface="en0")
        ssh.assert_called_once()
        self.assertNotIn("passwords", ssh.call_args.kwargs)
        self.assertEqual(check.call_args.kwargs["router_iface"], "en0")

    def test_verify_only_accepts_an_alias(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(install.subprocess, "run",
                              return_value=done(0, "user root\nhostname 10.9.9.9\n")), \
                 patch.object(install.shutil, "which", return_value="/usr/bin/ssh"):
                with self.assertRaisesRegex(install.Abort, "connects to 10.9.9.9"):
                    install.main(["--verify-only", os.path.join(tmp, "resume.json"),
                                  "--verify-ssh-key", "agent", "--verify-host",
                                  "192.168.31.1", "--verify-ssh-alias", "ax"])
        self.assertIsNone(install.KEY_AUTH)


if __name__ == "__main__":
    unittest.main()
