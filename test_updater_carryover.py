"""updater.py's carry-over of extra files and its package reinstall; no router access."""
import os
import shlex
import tempfile
import unittest
from unittest.mock import patch

import install
import updater


class ExtraFileTests(unittest.TestCase):
    # What the scan script prints on the reference router, plus what the
    # Python-side excludes must catch should the remote `case` miss it.
    SCAN = ("AUTOKEEP /etc/aurora/device.key\n"
            "AUTOKEEP /etc/aurora/device.hash\n"
            "AUTOKEEP /etc/apk/keys/eamonxg.pem\n"
            "AUTOKEEP /etc/apk/keys/glass-apk.rsa.pub\n"
            "AUTOKEEP /usr/bin/led-status.sh\n"
            "AUTOKEEP /etc/apk/world\n"
            "AUTOKEEP /etc/board.json\n"
            "AUTOKEEP /lib/firmware/ath11k/IPQ5018/cal-ahb-c000000.wifi.bin\n"
            "AUTOKEEP /etc/config/network.apk-new\n"
            "AUTOKEEP /usr/lib/lua/x.lua\n"
            "AUTOKEEP /etc/aurora/device.key\n"
            "AUTOKEEP_DONE\n")

    def test_reference_router(self):
        self.assertEqual(updater.parse_extra_files(self.SCAN), [
            "/etc/apk/keys/eamonxg.pem", "/etc/apk/keys/glass-apk.rsa.pub",
            "/etc/aurora/device.hash", "/etc/aurora/device.key",
            "/usr/bin/led-status.sh"])

    def test_excludes(self):
        excluded = ["/etc/apk/world", "/etc/urandom.seed", "/etc/board.json",
                    "/etc/board-extra.json", "/etc/hotplug.json", "/etc/hotplug-preinit.json",
                    "/etc/capabilities/ntpd.json", "/etc/banner.openwrt",
                    "/lib/firmware/ath11k/IPQ5018/cal-x.bin", "/.wget-hsts",
                    "/etc/config/dhcp.apk-new", "/tmp/x", "/var/run/x", "/bin/x",
                    "/sbin/x", "/lib/x.so", "/usr/lib/x.so", "/www/luci-static/x.css"]
        kept = ["/etc/aurora/device.key", "/usr/bin/led-status.sh", "/root/.profile",
                "/etc/banner", "/usr/sbin/tool", "/etc/apk/keys/eamonxg.pem"]
        for path in excluded:
            self.assertTrue(updater.is_auto_keep_excluded(path), path)
        for path in kept:
            self.assertFalse(updater.is_auto_keep_excluded(path), path)

    def test_script_is_one_pass_with_every_exclude(self):
        script = updater.auto_keep_script()
        for pattern in updater.AUTO_KEEP_EXCLUDES:
            self.assertIn(pattern, script)
        self.assertIn("apk info --who-owns", script)
        self.assertIn("sysupgrade -l", script)
        with self.assertRaises(ValueError):
            updater.auto_keep_script(("/etc/$(reboot)",))

    def test_detection_is_one_ssh_call(self):
        with patch.object(install, "ssh", return_value=(0, self.SCAN)) as ssh:
            updater.detect_extra_files("192.168.31.1")
        ssh.assert_called_once()

    def test_scan_errors_are_not_an_empty_result(self):
        with self.assertRaisesRegex(install.Abort, "no apk"):
            updater.parse_extra_files("AUTOKEEP_ERROR no apk\n")
        with self.assertRaisesRegex(install.Abort, "did not finish"):
            updater.parse_extra_files("AUTOKEEP /etc/x\n")


class SysupgradeConfTests(unittest.TestCase):
    def test_no_duplicates(self):
        existing = "## comment\n/etc/aurora/\n/usr/bin/led-status.sh\n"
        self.assertEqual(updater.keep_append_text(existing, [
            "/etc/aurora", "/usr/bin/led-status.sh", "/etc/backup/",
            "/etc/backup/", "/etc/apk/keys/eamonxg.pem"]),
            "/etc/backup/\n/etc/apk/keys/eamonxg.pem\n")

    def test_missing_final_newline(self):
        self.assertEqual(updater.keep_append_text("/etc/a", ["/etc/b"]), "\n/etc/b\n")
        self.assertEqual(updater.keep_append_text("", ["/etc/b"]), "/etc/b\n")
        self.assertEqual(updater.keep_append_text("/etc/a", ["/etc/a"]), "")

    def test_a_commented_path_is_not_listed(self):
        self.assertEqual(updater.keep_append_text("# /etc/b\n", ["/etc/b"]), "/etc/b\n")

    def test_append_reads_the_file_as_it_is(self):
        calls = []

        def ssh(host, cmd, **kw):
            calls.append(cmd)
            if cmd.startswith("cat /etc/sysupgrade.conf"):
                return 0, "/etc/a" + updater.KEEP_EOF     # no final newline
            return 0, ""
        with patch.object(install, "ssh", ssh):
            added = updater.append_keep_list("192.168.31.1", ["/etc/a", "/etc/it's here"])
        self.assertEqual(added, ["/etc/it's here"])
        write = calls[-1]
        self.assertTrue(write.startswith("printf %s "))
        self.assertTrue(write.endswith(">> /etc/sysupgrade.conf"))
        self.assertEqual(shlex.split(write)[2], "\n/etc/it's here\n")

    def test_nothing_new_writes_nothing(self):
        calls = []

        def ssh(host, cmd, **kw):
            calls.append(cmd)
            return 0, "/etc/a\n" + updater.KEEP_EOF
        with patch.object(install, "ssh", ssh):
            self.assertEqual(updater.append_keep_list("h", ["/etc/a"]), [])
        self.assertEqual(len(calls), 1)

    def test_extra_keep_must_be_absolute(self):
        with self.assertRaises(install.Abort):
            updater.validate_keep_path("etc/x")
        self.assertEqual(updater.validate_keep_path("/etc/x"), "/etc/x")


class WorldTests(unittest.TestCase):
    WORLD = ("base-files=1.2-r1\nkernel=6.6.93~abc-r1\nlibc\nkmod-nft-offload=6.6.93-r1\n"
             "luci\ndnsmasq-full\nlibustream-mbedtls\nluci-app-aurora\nluci\n"
             "kmod-wireguard\nfoo<2\nbar>1\n!badpkg\nhtop@custom\n")

    def test_filter(self):
        self.assertEqual(updater.filter_world(self.WORLD),
                         ["dnsmasq-full", "htop@custom", "kmod-wireguard", "luci",
                          "luci-app-aurora"])

    def test_pinned_and_installed_names(self):
        self.assertEqual(updater.pinned_names(self.WORLD),
                         {"base-files", "kernel", "kmod-nft-offload", "foo", "bar"})
        db = "P:luci\nV:1\np:cmd:luci luci-ng=1\nP:dnsmasq\n"
        self.assertEqual(updater.installed_names(db),
                         {"luci", "cmd:luci", "luci-ng", "dnsmasq"})


class ReinstallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for target in (patch.object(updater, "log"), patch.object(updater.time, "sleep")):
            target.start()
            self.addCleanup(target.stop)

    def router(self, world_old, *, installed=(), pinned="", fail=(), update_rc=(0,)):
        added, updates = [], list(update_rc)

        def ssh(host, cmd, **kw):
            self.assertNotIn("passwords", kw)
            if cmd == f"cat {updater.WORLD_OLD}":
                return (0, world_old) if world_old is not None else (1, "No such file")
            if cmd.startswith("grep -E '^[Pp]:'"):
                return 0, "".join(f"P:{name}\n" for name in installed)
            if cmd == "cat /etc/apk/world":
                return 0, pinned
            if cmd == "apk update":
                return (updates.pop(0) if updates else 1), "fetch failed"
            if cmd.startswith("apk add "):
                name = shlex.split(cmd)[2]
                added.append(name)
                if name in fail:
                    return 1, "ERROR: unable to select packages:\n  " + name + " (no such package)"
                return 0, "OK"
            raise AssertionError(cmd)
        return ssh, added

    def test_only_missing_packages_are_added_and_failures_summarised(self):
        ssh, added = self.router(
            "luci\nluci-app-aurora\nkmod-wireguard\ndnsmasq-full\nhtop\nkmod-nft-offload\n",
            installed=("luci",), pinned="kmod-nft-offload=6.6-r1\n",
            fail=("kmod-wireguard",), update_rc=(1, 0))
        with patch.object(install, "ssh", ssh):
            summary = updater.reinstall_packages("192.168.31.1", self.tmp.name)
        self.assertEqual(added, ["htop", "kmod-wireguard", "luci-app-aurora"])
        self.assertEqual(summary["installed"], ["htop", "luci-app-aurora"])
        self.assertEqual(summary["present"], ["luci"])
        self.assertEqual([name for name, _why in summary["failed"]], ["kmod-wireguard"])
        self.assertIn("no such package", summary["failed"][0][1])
        skipped = dict(summary["skipped"])
        self.assertIn("pinned", skipped["kmod-nft-offload"])
        self.assertIn("docs/packages.md", skipped["dnsmasq-full"])
        with open(os.path.join(self.tmp.name, "reinstall-summary.txt"), encoding="utf-8") as f:
            text = f.read()
        self.assertIn("failed: kmod-wireguard -- ", text)
        self.assertIn("installed: htop", text)
        self.assertIn("skipped: dnsmasq-full -- ", text)

    def test_unreachable_repositories_fail_every_missing_package(self):
        ssh, added = self.router("htop\nnano\n", update_rc=())
        with patch.object(install, "ssh", ssh):
            summary = updater.reinstall_packages("h", self.tmp.name, deadline_s=1)
        self.assertEqual(added, [])
        self.assertEqual([name for name, _ in summary["failed"]], ["htop", "nano"])

    def test_local_copy_when_the_router_lost_the_list(self):
        with open(os.path.join(self.tmp.name, "world-old.txt"), "w") as f:
            f.write("htop\n")
        ssh, added = self.router(None)
        with patch.object(install, "ssh", ssh):
            updater.reinstall_packages("h", self.tmp.name)
        self.assertEqual(added, ["htop"])

    def test_nothing_installed_needs_no_repository(self):
        ssh, added = self.router("luci\n", installed=("luci",), update_rc=())
        with patch.object(install, "ssh", ssh):
            summary = updater.reinstall_packages("h", self.tmp.name, deadline_s=1)
        self.assertEqual((added, summary["failed"]), ([], []))

    def test_a_reinstall_failure_does_not_fail_the_update(self):
        args = updater.parse_args([])
        with patch.object(updater, "reinstall_packages",
                          side_effect=install.Abort("lost the router")):
            updater.finish_packages("h", args, self.tmp.name, "cfg.tar.gz")
        args = updater.parse_args(["--no-reinstall"])
        with patch.object(updater, "reinstall_packages") as reinstall:
            updater.finish_packages("h", args, self.tmp.name, "cfg.tar.gz")
            updater.finish_packages("h", updater.parse_args([]), self.tmp.name, None)
        reinstall.assert_not_called()


class PrepareTests(unittest.TestCase):
    def run_prepare(self, *argv, scan=ExtraFileTests.SCAN):
        args = updater.parse_args(list(argv) + ["--yes"])
        commands, uploads = [], {}

        def ssh(host, cmd, **kw):
            commands.append(cmd)
            if cmd == "cat /etc/apk/world":
                return 0, "base-files=1-r1\nluci\nlibfoo\nhtop\n"
            if cmd == "sysupgrade -l":
                return 0, "/etc/config/network"
            if "AUTOKEEP_DONE" in cmd:
                return 0, scan
            if updater.KEEP_EOF in cmd:
                return 0, "/etc/aurora/device.key\n" + updater.KEEP_EOF
            if cmd.startswith("du -sk"):
                return 0, "900\t/overlay/upper"
            if "MemAvailable" in cmd:
                return 0, "MemAvailable: 100 kB"
            return 0, ""

        def scp_to(host, local, remote, passwords=None):
            with open(local, encoding="utf-8") as f:
                uploads[remote] = f.read()
        with tempfile.TemporaryDirectory() as outdir, \
             patch.object(install, "ssh", ssh), \
             patch.object(install, "scp_to", scp_to), \
             patch.object(updater, "log"), \
             patch.object(install, "log"):
            updater.prepare_carryover("192.168.31.1", args, outdir)
            files = sorted(os.listdir(outdir))
        return commands, uploads, files

    def test_keep_list_and_world_old(self):
        commands, uploads, files = self.run_prepare("--extra-keep", "/root/notes")
        write = [cmd for cmd in commands if cmd.startswith("printf %s ")]
        self.assertEqual(len(write), 1)
        self.assertEqual(shlex.split(write[0])[2],
                         "/etc/apk/keys/eamonxg.pem\n/etc/apk/keys/glass-apk.rsa.pub\n"
                         "/etc/aurora/device.hash\n/usr/bin/led-status.sh\n/root/notes\n"
                         "/etc/backup/\n")
        self.assertEqual(uploads, {updater.WORLD_OLD: "htop\nluci\n"})
        self.assertEqual(files, ["sysupgrade-list.txt", "world-old.txt", "world.txt"])
        self.assertFalse(any(cmd.startswith("tar czf") for cmd in commands))

    def test_no_auto_keep(self):
        commands, _uploads, _files = self.run_prepare("--no-auto-keep")
        self.assertFalse(any("AUTOKEEP_DONE" in cmd for cmd in commands))
        write = [cmd for cmd in commands if cmd.startswith("printf %s ")]
        self.assertEqual(shlex.split(write[0])[2], "/etc/backup/\n")

    def test_declining_writes_nothing(self):
        args = updater.parse_args([])
        with patch.object(install, "ssh",
                          side_effect=lambda h, c, **k: (0, ExtraFileTests.SCAN if "AUTOKEEP_DONE" in c else "")), \
             patch.object(install, "confirm", side_effect=install.Abort("declined")), \
             patch.object(install, "scp_to") as scp, \
             patch.object(updater, "log"), tempfile.TemporaryDirectory() as outdir:
            with self.assertRaises(install.Abort):
                updater.prepare_carryover("h", args, outdir)
        scp.assert_not_called()


if __name__ == "__main__":
    unittest.main()
