"""updater.py's carry-over of extra files and its package reinstall; no router access."""
import io
import os
import shlex
import tarfile
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
                    "/sbin/x", "/lib/x.so", "/usr/lib/x.so", "/www/luci-static/x.css",
                    # Seen on hardware (v2.1.4): generated, not the operator's.
                    "/etc/rc.d/S99ttyd", "/etc/rc.d/K10collectd", "/run",
                    "/run/x.pid",
                    "/usr/share/nftables.d/ruleset-post/20-https-dns-proxy-notrack.nft"]
        kept = ["/etc/aurora/device.key", "/usr/bin/led-status.sh", "/root/.profile",
                "/etc/banner", "/usr/sbin/tool", "/etc/apk/keys/eamonxg.pem",
                "/etc/rc.local", "/etc/nftables.d/10-custom.nft", "/runner.sh"]
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
            if cmd.startswith("find /etc -name"):
                return 0, ""
            if cmd.startswith("cat /etc/apk/repositories"):
                return 0, ""            # no repository host: no resolver fallback
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
            if "FETCH_OK" in cmd:
                return 0, "FETCH_NONE"
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
        self.assertEqual(uploads[updater.WORLD_OLD], "htop\nluci\n")
        self.assertIn("etc/config/network\n", uploads[updater.REMOTE_LIST])
        self.assertEqual(files, ["sysupgrade-list.txt", "world-old.txt", "world.txt"])
        self.assertFalse(any(cmd.startswith("tar czf") for cmd in commands))

    def test_no_auto_keep(self):
        commands, _uploads, _files = self.run_prepare("--no-auto-keep")
        self.assertTrue(any("FETCH_OK" in cmd for cmd in commands))
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


def tar_bytes(files, extra=()):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, body in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(body)
            archive.addfile(info, io.BytesIO(body))
        for info in extra:
            archive.addfile(info)
    return buffer.getvalue()


class FetchFilesTests(unittest.TestCase):
    def test_archive_names_are_checked(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "a.tar.gz")
            link = tarfile.TarInfo("etc/link")
            link.type = tarfile.SYMTYPE
            with open(path, "wb") as f:
                f.write(tar_bytes({"etc/config/dhcp": b"x", "./etc/b": b"y"}, [link]))
            self.assertEqual(updater.read_files_archive(path),
                             {"/etc/config/dhcp": b"x", "/etc/b": b"y"})
            with open(path, "wb") as f:
                f.write(tar_bytes({"../etc/shadow": b"x"}))
            with self.assertRaises(install.Abort):
                updater.read_files_archive(path)

    def run_fetch(self, answer, archive=None, keep=None):
        commands, lists = [], []

        def ssh(host, cmd, **kw):
            commands.append(cmd)
            return (0, answer) if "FETCH_OK" in cmd else (0, "")

        def scp_to(host, local, remote, passwords=None):
            with open(local, encoding="utf-8") as f:
                lists.append(f.read())

        def scp_from(host, remote, local):
            with open(local, "wb") as f:
                f.write(archive)
        with patch.object(install, "ssh", ssh), patch.object(install, "scp_to", scp_to), \
             patch.object(updater, "scp_from", scp_from) as copied:
            try:
                return updater.fetch_files("h", "/rom", ["/etc/config/dhcp", "/etc/x"],
                                           keep=keep), commands, lists
            finally:
                self.assertTrue(commands[-1].startswith("rm -f "))

    def test_only_requested_regular_files(self):
        archive = tar_bytes({"etc/config/dhcp": b"default\n", "etc/config/._dhcp": b"\0meta"})
        files, commands, lists = self.run_fetch("FETCH_OK", archive)
        self.assertEqual(files, {"/etc/config/dhcp": b"default\n"})
        self.assertEqual(lists, ["etc/config/dhcp\netc/x\n"])
        self.assertIn("cd /rom && ", commands[0])
        self.assertIn("tar czf", commands[0])

    def test_none_present_and_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            keep = os.path.join(tmp, "kept.tar.gz")
            files, _commands, _lists = self.run_fetch("FETCH_NONE", keep=keep)
            self.assertEqual(files, {})
            self.assertFalse(os.path.exists(keep))
        with self.assertRaises(install.Abort):
            self.run_fetch("tar: short write")


class ApkNewReviewTests(unittest.TestCase):
    MINE = b"config dnsmasq\n\toption domain 'lan'\n"
    PACKAGE = b"config dnsmasq\n\toption domain 'lan'\n\toption rebind_protection '1'\n"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = patch.object(updater, "log")
        p.start()
        self.addCleanup(p.stop)
        self.commands = []

    def review(self, answers, assume_yes=False, before=()):
        found = ("/etc/config/a.apk-new\n/etc/config/b.apk-new\n"
                 "/etc/config/old.apk-new\n")
        files = {}
        for name in ("a", "b"):
            files[f"/etc/config/{name}"] = self.MINE
            files[f"/etc/config/{name}.apk-new"] = self.PACKAGE

        def ssh(host, cmd, **kw):
            self.commands.append(cmd)
            return (0, found) if cmd.startswith("find /etc") else (0, "")
        with patch.object(install, "ssh", ssh), \
             patch.object(updater, "fetch_files",
                          side_effect=lambda h, root, paths, keep=None:
                          {p: files[p] for p in paths if p in files}), \
             patch("builtins.input", side_effect=answers) as asked:
            result = updater.review_apk_new("h", self.tmp.name,
                                            set(before) | {"/etc/config/old.apk-new"},
                                            assume_yes)
        return result, asked

    def test_keep_and_new(self):
        result, asked = self.review(["x", "k", "n"])
        self.assertEqual(result, [("/etc/config/a", "keep"), ("/etc/config/b", "new")])
        self.assertEqual(asked.call_count, 3)          # "x" was asked again
        self.assertIn("rm -f /etc/config/a.apk-new", self.commands)
        self.assertIn("cp -p /etc/config/b /etc/config/b.apk-old && "
                      "mv -f /etc/config/b.apk-new /etc/config/b", self.commands)
        self.assertFalse(any("old.apk-new" in cmd for cmd in self.commands[1:]))
        with open(os.path.join(self.tmp.name, "conffiles",
                               "etc__config__a.apk-new.diff"), encoding="utf-8") as f:
            diff = f.read()
        self.assertIn("+\toption rebind_protection '1'", diff)
        self.assertIn("/etc/config/a (yours)", diff)

    def test_yes_defers_without_asking_or_writing(self):
        result, asked = self.review([], assume_yes=True)
        self.assertEqual([d for _p, d in result], ["defer", "defer"])
        asked.assert_not_called()
        self.assertEqual([c for c in self.commands if not c.startswith("find")], [])

    def test_no_terminal_and_ctrl_c_defer(self):
        result, _asked = self.review(EOFError())
        self.assertEqual([d for _p, d in result], ["defer", "defer"])
        result, asked = self.review(KeyboardInterrupt())
        self.assertEqual([d for _p, d in result], ["defer", "defer"])
        self.assertEqual(asked.call_count, 1)

    def test_nothing_new_asks_nothing(self):
        result, asked = self.review([], before={"/etc/config/a.apk-new",
                                                 "/etc/config/b.apk-new"})
        self.assertEqual(result, [])
        asked.assert_not_called()

    def test_decisions_reach_the_summary(self):
        with tempfile.TemporaryDirectory() as outdir, patch.object(updater, "log"):
            updater._write_summary(outdir, {
                "installed": ["luci-app-x"], "present": [], "skipped": [], "failed": [],
                "conffiles": [("/etc/config/x", "defer")]})
            with open(os.path.join(outdir, "reinstall-summary.txt"), encoding="utf-8") as f:
                self.assertIn("config defer: /etc/config/x", f.read())


class ImageDefaultsTests(unittest.TestCase):
    OLD = {"etc/config/dhcp": b"old dhcp\n", "etc/config/system": b"same\n",
           "etc/config/gone": b"x\n", "etc/config/firewall": b"old fw\n"}
    NEW = {"/etc/config/dhcp": b"old dhcp\nnew option\n", "/etc/config/system": b"same\n",
           "/etc/config/firewall": b"new fw\n"}
    MINE = {"/etc/config/dhcp": b"my dhcp\n", "/etc/config/firewall": b"new fw\n"}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = patch.object(updater, "log")
        p.start()
        self.addCleanup(p.stop)

    def fetch(self, host, root, paths, keep=None):
        source = self.NEW if root == "/rom" else self.MINE
        return {path: source[path] for path in paths if path in source}

    def test_only_upstream_changes_are_reported(self):
        with open(os.path.join(self.tmp.name, updater.ROM_DEFAULTS_OLD), "wb") as f:
            f.write(tar_bytes(self.OLD))
        with patch.object(updater, "fetch_files", side_effect=self.fetch) as fetch, \
             patch.object(install, "ssh", side_effect=AssertionError("writes nothing")):
            result = dict(updater.review_image_defaults("h", self.tmp.name))
        # system did not change upstream; firewall is already the new default.
        self.assertEqual(set(result), {"/etc/config/dhcp", "/etc/config/gone",
                                       "/etc/config/firewall"})
        self.assertIsNone(result["/etc/config/gone"])
        self.assertIsNone(result["/etc/config/firewall"])
        with open(result["/etc/config/dhcp"], encoding="utf-8") as f:
            diff = f.read()
        self.assertIn("+new option", diff)
        self.assertIn("(old default)", diff)
        self.assertEqual(fetch.call_args_list[1].args[1], "/")

    def test_without_old_defaults_nothing_is_read(self):
        with patch.object(updater, "fetch_files") as fetch:
            self.assertEqual(updater.review_image_defaults("h", self.tmp.name), [])
        fetch.assert_not_called()

    def test_saving_old_defaults_never_stops_the_update(self):
        listed = "/etc/config/dhcp\n/etc/sysupgrade.conf\n"
        with patch.object(updater, "fetch_files", return_value={"/etc/config/dhcp": b"x"}) as fetch:
            self.assertEqual(updater.save_rom_defaults("h", self.tmp.name, listed), 1)
        self.assertEqual(fetch.call_args.args[1:3],
                         ("/rom", ["/etc/config/dhcp", "/etc/sysupgrade.conf"]))
        self.assertEqual(fetch.call_args.kwargs["keep"],
                         os.path.join(self.tmp.name, updater.ROM_DEFAULTS_OLD))
        with patch.object(updater, "fetch_files", side_effect=install.Abort("no tar")):
            self.assertEqual(updater.save_rom_defaults("h", self.tmp.name, listed), 0)

    def test_finish_reviews_then_reinstalls_with_the_yes_flag(self):
        args = updater.parse_args(["--yes"])
        with patch.object(updater, "review_image_defaults",
                          side_effect=install.Abort("lost")) as review, \
             patch.object(updater, "reinstall_packages") as reinstall:
            updater.finish_packages("h", args, self.tmp.name, "cfg.tar.gz")
        review.assert_called_once()
        self.assertTrue(reinstall.call_args.kwargs["assume_yes"])


@unittest.skipIf(os.name == "nt", "runs the resolver commands through sh")
class TemporaryResolverTests(unittest.TestCase):
    """The restored dnsmasq config forwards to https-dns-proxy, which only the
    reinstall brings back: the case seen on hardware."""
    ORIGINAL = "search lan\nnameserver 127.0.0.1\nnameserver ::1\n"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.conf = os.path.join(self.tmp.name, "resolv.conf")
        self.saved = os.path.join(self.tmp.name, "saved")
        with open(self.conf, "w") as f:
            f.write(self.ORIGINAL)
        for target in (patch.object(updater, "RESOLV_CONF", self.conf),
                       patch.object(updater, "RESOLV_SAVED", self.saved),
                       patch.object(updater, "log"),
                       patch.object(updater.time, "sleep")):
            target.start()
            self.addCleanup(target.stop)
        self.proxy_up = False          # https-dns-proxy serves 127.0.0.1#5053
        self.wan = "# Interface wan\nnameserver 1.1.1.2\nnameserver 1.0.0.2;reboot\n"
        self.wan_works = True
        self.apk_broken = False
        self.added = []
        self.restart_dnsmasq_on = "https-dns-proxy"

    def conf_text(self):
        with open(self.conf) as f:
            return f.read()

    def system_resolves(self):
        return self.proxy_up or "nameserver 1.1.1.2" in self.conf_text()

    def ssh(self, host, cmd, **kw):
        import subprocess
        if cmd.startswith("uci -q get dhcp.@dnsmasq[0].server"):
            return 0, "127.0.0.1#5053 /router.example/"
        if cmd.startswith("cat /etc/apk/repositories"):
            return 0, "# feeds\nhttps://downloads.openwrt.org/snapshots/packages.adb\n"
        if cmd.startswith(f"cat {updater.WAN_RESOLV}"):
            return 0, self.wan
        if cmd.startswith("nslookup "):
            words = shlex.split(cmd.split(">")[0])
            ok = (self.wan_works and words[2] == "1.1.1.2") if len(words) > 2 \
                else self.system_resolves()
            return 0, "DNS_OK" if ok else ""
        if cmd == "apk update":
            return (0, "OK") if self.system_resolves() and not self.apk_broken \
                else (1, "ERROR: DNS lookup error")
        if cmd.startswith("apk add "):
            name = shlex.split(cmd)[2]
            if not self.system_resolves():
                return 1, "ERROR: unable to select packages"
            self.added.append(name)
            if name == self.restart_dnsmasq_on:
                # The package restarts dnsmasq, which rewrites resolv.conf.
                self.proxy_up = name == "https-dns-proxy"
                with open(self.conf, "w") as f:
                    f.write("search lan\nnameserver 127.0.0.1\n")
            return 0, "OK"
        if cmd == f"cat {updater.WORLD_OLD}":
            return 0, "https-dns-proxy\nhtop\nnano\n"
        if cmd.startswith(("grep -E", "find /etc")) or cmd == "cat /etc/apk/world":
            return 0, ""
        result = subprocess.run(["sh", "-c", cmd], capture_output=True, text=True)
        return result.returncode, (result.stdout + result.stderr).strip()

    def reinstall(self):
        with patch.object(install, "ssh", self.ssh):
            return updater.reinstall_packages("h", self.tmp.name, deadline_s=1)

    def test_wan_dns_carries_the_reinstall_then_dnsmasq_wins(self):
        summary = self.reinstall()
        self.assertEqual(summary["installed"], ["htop", "https-dns-proxy", "nano"])
        self.assertEqual(summary["failed"], [])
        # dnsmasq rewrote the file when https-dns-proxy came back: that stays.
        self.assertEqual(self.conf_text(), "search lan\nnameserver 127.0.0.1\n")
        self.assertFalse(os.path.exists(self.saved))

    def test_restored_when_nothing_rewrote_it(self):
        self.restart_dnsmasq_on = None
        summary = self.reinstall()
        self.assertEqual(len(summary["installed"]), 3)
        self.assertEqual(self.conf_text(), self.ORIGINAL)
        self.assertFalse(os.path.exists(self.saved))

    def test_only_the_wan_addresses_are_written(self):
        written = []
        real = self.ssh

        def spy(host, cmd, **kw):
            if cmd.startswith(f"[ -e {self.saved} ]"):
                written.append(cmd)
            return real(host, cmd, **kw)
        self.restart_dnsmasq_on = None
        with patch.object(install, "ssh", spy):
            updater.reinstall_packages("h", self.tmp.name, deadline_s=1)
        text = shlex.split(written[0].split(";", 1)[1])[2]
        self.assertEqual(text, updater.RESOLVER_MARK + "\nnameserver 1.1.1.2\n")
        self.assertNotIn("reboot", written[0])

    def test_rewritten_mid_way_is_written_again(self):
        # Another package restarts dnsmasq before the DNS package is back.
        self.restart_dnsmasq_on = "htop"
        summary = self.reinstall()
        self.assertEqual(summary["installed"], ["htop", "https-dns-proxy", "nano"])

    def test_working_dns_is_left_alone(self):
        self.proxy_up, self.apk_broken = True, True
        summary = self.reinstall()
        self.assertEqual(self.conf_text(), self.ORIGINAL)
        self.assertEqual([name for name, _ in summary["failed"]],
                         ["htop", "https-dns-proxy", "nano"])

    def test_no_usable_wan_dns_changes_nothing(self):
        self.wan_works = False
        summary = self.reinstall()
        self.assertEqual(self.conf_text(), self.ORIGINAL)
        self.assertFalse(os.path.exists(self.saved))
        self.assertEqual(len(summary["failed"]), 3)

    def test_restored_even_when_the_reinstall_stops(self):
        real = self.ssh

        def stop(host, cmd, **kw):
            if cmd.startswith("apk add "):
                raise install.Abort("host key changed")
            return real(host, cmd, **kw)
        self.restart_dnsmasq_on = None
        with patch.object(install, "ssh", stop):
            with self.assertRaises(install.Abort):
                updater.reinstall_packages("h", self.tmp.name, deadline_s=1)
        self.assertEqual(self.conf_text(), self.ORIGINAL)

    def test_local_forwarders(self):
        with patch.object(install, "ssh", return_value=(
                0, "127.0.0.1#5053 /router.example/ /lan/::1#5054 8.8.8.8 /x/192.168.1.5")):
            self.assertEqual(updater.local_dns_forwarders("h"),
                             ["127.0.0.1#5053", "/lan/::1#5054"])


if __name__ == "__main__":
    unittest.main()
