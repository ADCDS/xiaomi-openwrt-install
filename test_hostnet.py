"""Windows host-network tests; all OS and network operations are mocked."""
import copy
import json
import socket
import subprocess
import unittest
from unittest.mock import patch

import hostnet


def response(data, code=0, stderr=b""):
    return subprocess.CompletedProcess([], code,
                                       json.dumps(data, ensure_ascii=False).encode("utf-8"),
                                       stderr)


def record(index=12, name="Беспроводная сеть", wireless=True,
           ipv4=None, ipv6=None):
    return {"index": index, "name": name, "mac": "AA-BB-CC-DD-EE-FF",
            "wireless": wireless,
            "ipv4": ["192.168.31.20/24"] if ipv4 is None else ipv4,
            "ipv6": ["fe80::1/64"] if ipv6 is None else ipv6}


class HostnetTests(unittest.TestCase):
    def test_platform(self):
        with patch.object(hostnet.sys, "platform", "win32"):
            self.assertTrue(hostnet.is_windows())
        with patch.object(hostnet.sys, "platform", "linux"):
            self.assertFalse(hostnet.is_windows())

    @patch("hostnet.subprocess.run")
    def test_utf8_alias_and_powershell_command(self, run):
        run.return_value = response([record()])
        item = hostnet.adapters()[0]
        self.assertEqual(item["name"], "Беспроводная сеть")
        self.assertEqual(item["mac"], "aa:bb:cc:dd:ee:ff")
        self.assertIs(item["wireless"], True)
        command = run.call_args.args[0]
        self.assertTrue(command[0].endswith(r"System32\WindowsPowerShell\v1.0\powershell.exe"))
        self.assertEqual(command[1:4], ["-NoProfile", "-NonInteractive", "-Command"])
        self.assertIn("UTF8Encoding", command[4])
        self.assertIn("ConvertTo-Json -InputObject @($records)", command[4])
        self.assertNotIn("text", run.call_args.kwargs)
        self.assertNotIn("shell", run.call_args.kwargs)

    @patch("hostnet.subprocess.run")
    def test_single_empty_and_single_address_normalization(self, run):
        item = record(ipv4="192.168.31.20/24", ipv6=None)
        item["ipv6"] = None
        for data in ([], None):
            run.return_value = response(data)
            self.assertEqual(hostnet.adapters(), [])
        run.return_value = response(item)
        self.assertEqual(hostnet.adapters()[0]["ipv4"], ["192.168.31.20/24"])
        self.assertEqual(hostnet.adapters()[0]["ipv6"], [])
        run.return_value = response(item)
        run.return_value.stdout = b"\xef\xbb\xbf" + run.return_value.stdout
        self.assertEqual(hostnet.adapters()[0]["name"], item["name"])

    @patch("hostnet.subprocess.run")
    def test_resolution_wifi_and_current_data(self, run):
        run.return_value = response([record(), record(8, "Ethernet", False)])
        self.assertEqual(hostnet.adapter("беспроводная СЕТЬ")["index"], 12)
        self.assertTrue(hostnet.adapter("12")["wireless"])
        self.assertFalse(hostnet.adapter("ethernet")["wireless"])
        run.return_value = response([record(19)])
        self.assertEqual(hostnet.adapter("Беспроводная сеть")["index"], 19)
        with self.assertRaisesRegex(hostnet.NetworkError, "not found"):
            hostnet.adapter("12")

    @patch("hostnet.subprocess.run")
    def test_missing_ambiguous_and_no_alias_interpolation(self, run):
        alias = "bad'; Stop-Computer; #"
        run.return_value = response([record(name=alias)])
        self.assertEqual(hostnet.adapter(alias)["name"], alias)
        self.assertNotIn(alias, run.call_args.args[0][-1])
        run.return_value = response([record(name="WIFI"), record(8, "wifi")])
        with self.assertRaisesRegex(hostnet.NetworkError, "ambiguous"):
            hostnet.adapter("wifi")
        run.return_value = response([record(name="8"), record(8, "Ethernet")])
        with self.assertRaisesRegex(hostnet.NetworkError, "ambiguous"):
            hostnet.adapter("8")

    def test_wireless_classification_script_uses_physical_medium(self):
        self.assertIn("$a.NdisPhysicalMedium", hostnet._ADAPTERS)
        self.assertIn("$medium -in @(1, 9)", hostnet._ADAPTERS)
        self.assertNotIn("InterfaceDescription", hostnet._ADAPTERS)

    @patch("hostnet.subprocess.run")
    def test_json_and_command_failures(self, run):
        for result in (subprocess.CompletedProcess([], 0, b"not JSON", b""),
                       subprocess.CompletedProcess([], 0, b"\xff", b""),
                       response(["not an object"]),
                       response([], 1, "Ошибка NetTCPIP".encode("utf-8"))):
            run.return_value = result
            with self.assertRaises(hostnet.NetworkError):
                hostnet.adapters()
        for error in (FileNotFoundError("powershell.exe"),
                      subprocess.TimeoutExpired("powershell.exe", 10)):
            run.side_effect = error
            with self.assertRaisesRegex(hostnet.NetworkError, "PowerShell 5.1"):
                hostnet.adapters()

    @patch("hostnet.adapters")
    @patch("hostnet.socket.socket")
    def test_route_maps_actual_source_not_default(self, factory, adapters):
        sock = factory.return_value.__enter__.return_value
        sock.getsockname.return_value = ("192.168.31.20", 33333)
        adapters.return_value = [record(8, "Default", ipv4=["10.0.0.2/24"]), record()]
        self.assertEqual(hostnet.route("192.168.31.1"),
                         ("192.168.31.20", "Беспроводная сеть"))
        factory.assert_called_once_with(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect.assert_called_once_with(("192.168.31.1", 9))
        sock.send.assert_not_called()
        sock.sendto.assert_not_called()

    @patch("hostnet.adapters")
    @patch("hostnet.socket.socket")
    def test_route_failures(self, factory, adapters):
        sock = factory.return_value.__enter__.return_value
        for source, rows in (("0.0.0.0", [record()]),
                             ("127.0.0.1", [record()]),
                             ("10.0.0.20", [record()]),
                             ("192.168.31.20", [record(), record(8, "Other")])):
            sock.getsockname.return_value = (source, 1)
            adapters.return_value = rows
            with self.assertRaises(hostnet.NetworkError):
                hostnet.route("192.168.31.1")
        sock.connect.side_effect = OSError("network unreachable")
        with self.assertRaisesRegex(hostnet.NetworkError, "router-facing"):
            hostnet.route("192.168.31.1")

    @patch("hostnet.subprocess.run")
    def test_default_routes(self, run):
        run.side_effect = [response([{"index": 12, "gateway": "192.168.31.1"},
                                    {"index": 12, "gateway": "0.0.0.0"}]),
                           response([record()])]
        self.assertEqual(hostnet.default_routes(),
                         [("192.168.31.1", "Беспроводная сеть")])
        run.side_effect = [response([]), response([])]
        self.assertEqual(hostnet.default_routes(), [])

    @patch("hostnet.subprocess.run")
    def test_default_routes_keep_one_gateway_on_two_adapters(self, run):
        run.side_effect = [response([{"index": 12, "gateway": "192.168.1.1"},
                                    {"index": 8, "gateway": "192.168.1.1"}]),
                           response([record(), record(8, "Ethernet", False)])]
        self.assertEqual(hostnet.default_routes(),
                         [("192.168.1.1", "Беспроводная сеть"),
                          ("192.168.1.1", "Ethernet")])

    @patch("hostnet.subprocess.run")
    def test_neighbor_mac(self, run):
        rows = [{"index": 8, "address": "192.168.1.1", "state": 5, "mac": "11-11-11-11-11-11"},
                {"index": 12, "address": "192.168.1.1", "state": 4, "mac": "AA-BB-CC-DD-EE-FF"},
                {"index": 12, "address": "192.168.1.9", "state": 0, "mac": "22-22-22-22-22-22"},
                {"index": 12, "address": "192.168.1.8", "state": 5, "mac": "00-00-00-00-00-00"},
                {"index": 12, "address": "fe80::1%12", "state": 5, "mac": "33-33-33-33-33-33"}]
        adapters = [record(), record(8, "Ethernet", False)]

        def lookup(*args):
            run.side_effect = [response(adapters), response(rows)]
            return hostnet.neighbor_mac(*args)

        self.assertEqual(lookup("192.168.1.1", "Беспроводная сеть"), "aa:bb:cc:dd:ee:ff")
        self.assertEqual(lookup("192.168.1.1", "8"), "11:11:11:11:11:11")
        self.assertEqual(lookup("fe80::1", "12"), "33:33:33:33:33:33")
        self.assertIsNone(lookup("192.168.1.9", "12"), "an unreachable entry is not a MAC")
        self.assertIsNone(lookup("192.168.1.8", "12"), "the all-zero MAC is not a MAC")
        self.assertIsNone(lookup("192.168.1.7", "12"))
        run.side_effect = [response(rows)]
        self.assertEqual(hostnet.neighbor_mac("192.168.1.1"), "11:11:11:11:11:11")

    @patch("hostnet.subprocess.run")
    def test_neighbor_mac_bad_data(self, run):
        run.side_effect = [response([record()]),
                           response([{"index": 12, "address": "not-an-address",
                                      "state": 5, "mac": "AA-BB-CC-DD-EE-FF"}])]
        with self.assertRaisesRegex(hostnet.NetworkError, "neighbour"):
            hostnet.neighbor_mac("192.168.1.1", "12")

    @patch("hostnet.subprocess.run")
    def test_discovery_filters_scopes_and_refreshes_self(self, run):
        before = record(name="evil'; #", ipv6=["fe80::1/64"])
        after = copy.deepcopy(before)
        after["ipv6"] = ["fe80::2%12/64"]
        neighbors = [{"index": index, "address": addr, "state": state}
                     for index, addr, state in (
                         (12, "fe80::1", 5), (12, "fe80::2", 4),
                         (12, "fe80::3%12", 5), (12, "FE80::3", 4),
                         (12, "fe80::4", 0), (12, "fe80::5", 1),
                         (8, "fe80::6", 5), (12, "2001:db8::1", 5),
                         (12, "ff02::1", 5), (12, "fe80::7", 6))]
        run.side_effect = [response(before), response(None, code=1),
                           response(after), response(neighbors)]
        self.assertEqual(hostnet.discover_candidates(before["name"]),
                         ["fe80::3%12", "fe80::7%12"])
        ping = run.call_args_list[1].args[0]
        self.assertTrue(ping[0].endswith(r"System32\ping.exe"))
        self.assertEqual(ping[1:], ["-6", "-n", "2", "-w", "1000", "ff02::1%12"])
        for call in run.call_args_list:
            self.assertNotIn(before["name"], " ".join(call.args[0]))

    @patch("hostnet.subprocess.run")
    def test_discovery_empty_and_failed_probe(self, run):
        run.side_effect = [response(record()), response(None),
                           response(record()), response([])]
        self.assertEqual(hostnet.discover_candidates("12"), [])
        run.side_effect = [response(record()), subprocess.TimeoutExpired("ping", 1)]
        with self.assertRaisesRegex(hostnet.NetworkError, "Cannot probe IPv6"):
            hostnet.discover_candidates("12")
        for timeout in (0, -1, float("nan"), "bad"):
            with self.assertRaisesRegex(hostnet.NetworkError, "timeout"):
                hostnet.discover_candidates("12", timeout)


if __name__ == "__main__":
    unittest.main()
