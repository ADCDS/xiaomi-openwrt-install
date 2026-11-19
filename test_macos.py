"""macOS compatibility regressions; no router access."""
import unittest
from unittest.mock import patch

import install
import macnet

# Captured from macOS 14.8.9 (QEMU guest) on 2026-11-19. networksetup lists a
# serial port that is no network interface; `ifconfig -l` does not know it.
NETWORKSETUP_SERIAL = (
    '\nHardware Port: Built-in Serial Port (1)\nDevice: serial1\nEthernet Address: N/A\n\n'
    'Hardware Port: Ethernet\nDevice: en0\nEthernet Address: 52:54:00:c9:18:27\n\n'
    'VLAN Configurations\n===================\n')
IFCONFIG_L = 'lo0 gif0 stf0 en0 utun0 utun1 utun2\n'
ARP_AN = ('? (192.168.250.1) at 52:54:0:44:80:38 on en0 ifscope [ethernet]\n'
          '? (224.0.0.251) at 1:0:5e:0:0:fb on en0 ifscope permanent [ethernet]\n')
NDP_AN = ('Neighbor                                Linklayer Address  Netif Expire    St Flgs Prbs\n'
          'fe80::1%lo0                             (incomplete)         lo0 permanent R      \n'
          'fe80::7b:a771:839a:5457%en0             52:54:0:c9:18:27     en0 permanent R      \n')


def fake_query(outputs):
    """macnet.query stand-in answering by command name."""
    def query(command):
        return outputs[command[0].rsplit('/', 1)[-1]]
    return query


class MacTests(unittest.TestCase):
    def test_hardware_port_detection_without_sysfs(self):
        text = ('Hardware Port: Wi-Fi\nDevice: en0\nEthernet Address: aa:bb:cc:dd:ee:ff\n\n'
                'Hardware Port: USB Ethernet\nDevice: en7\nEthernet Address: aa:bb:cc:dd:ee:01\n')
        query = fake_query({'networksetup': text, 'ifconfig': 'lo0 en0 en7\n'})
        with patch.object(install.sys, 'platform', 'darwin'), patch.object(macnet, 'query', query):
            self.assertEqual(install.detect_transport('en0'), 'wifi')
            self.assertEqual(install.detect_transport('en7'), 'wired')
            with self.assertRaises(install.Abort):
                install.detect_transport('en99')

    def test_ports_without_a_network_interface_are_dropped(self):
        query = fake_query({'networksetup': NETWORKSETUP_SERIAL, 'ifconfig': IFCONFIG_L})
        with patch.object(macnet, 'query', query):
            self.assertEqual(macnet.interfaces(), {'en0': 'wired'})
            with self.assertRaises(RuntimeError):
                macnet.transport('serial1')

    def test_arp_is_scoped_and_padded(self):
        text = ('? (192.168.1.1) at aa:bb:cc:dd:ee:ff on en7 ifscope\n'
                '? (192.168.1.1) at 0:1:2:3:4:5 on en0 ifscope\n')
        with patch.object(macnet, 'query', return_value=text):
            self.assertEqual(macnet.neighbor_mac('192.168.1.1', 'en0'), '00:01:02:03:04:05')
            self.assertIsNone(macnet.neighbor_mac('192.168.1.1', 'en8'))

    def test_real_arp_and_ndp_output(self):
        with patch.object(macnet, 'query', return_value=ARP_AN):
            self.assertEqual(macnet.neighbor_mac('192.168.250.1', 'en0'), '52:54:00:44:80:38')
        with patch.object(macnet, 'query', return_value=NDP_AN):
            self.assertEqual(macnet.neighbor_mac('fe80::7b:a771:839a:5457', 'en0'),
                             '52:54:00:c9:18:27')
            self.assertIsNone(macnet.neighbor_mac('fe80::1', 'lo0'))

    def test_ndp_incomplete_and_wrong_interface(self):
        text = ('Neighbor Linklayer Netif Expire St\n'
                'fe80::1%en7 aa:bb:cc:dd:ee:ff en7 permanent R\n'
                'fe80::1%en0 (incomplete) en0 permanent R\n')
        with patch.object(macnet, 'query', return_value=text):
            self.assertIsNone(macnet.neighbor_mac('fe80::1', 'en0'))
            self.assertEqual(macnet.neighbor_mac('fe80::1', 'en7'), 'aa:bb:cc:dd:ee:ff')

    def test_list_does_not_contact_router(self):
        with patch.object(install.sys, 'platform', 'darwin'), \
             patch.object(macnet, 'interfaces', return_value={'en0': 'wifi'}), \
             patch.object(install, 'interface_ipv4_networks', return_value=[]), \
             patch.object(install.chain, 'init_info') as contact, \
             patch.object(install, 'log'):
            self.assertEqual(install.main(['--list-interfaces']), 0)
            contact.assert_not_called()

    def test_list_survives_an_unreadable_interface(self):
        def networks(name):
            if name == 'en5':
                raise install.Abort('cannot inspect IPv4 addresses on en5')
            return [install.ipaddress.ip_interface('192.168.250.63/24')]
        with patch.object(install.sys, 'platform', 'darwin'), \
             patch.object(macnet, 'interfaces', return_value={'en5': 'wired', 'en0': 'wired'}), \
             patch.object(install, 'interface_ipv4_networks', networks), \
             patch.object(install, 'log') as log:
            self.assertEqual(install.main(['--list-interfaces']), 0)
        lines = [call.args[0] for call in log.call_args_list]
        self.assertIn('en5  wired  IPv4 unreadable', lines)
        self.assertIn('en0  wired  192.168.250.63/24', lines)


if __name__ == '__main__':
    unittest.main()
