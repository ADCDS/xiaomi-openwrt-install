"""macOS compatibility regressions; no router access."""
import unittest
from unittest.mock import patch

import install
import macnet


class MacTests(unittest.TestCase):
    def test_hardware_port_detection_without_sysfs(self):
        text = ('Hardware Port: Wi-Fi\nDevice: en0\nEthernet Address: aa:bb:cc:dd:ee:ff\n\n'
                'Hardware Port: USB Ethernet\nDevice: en7\nEthernet Address: aa:bb:cc:dd:ee:01\n')
        with patch.object(install.sys, 'platform', 'darwin'), patch.object(macnet, 'query', return_value=text):
            self.assertEqual(install.detect_transport('en0'), 'wifi')
            self.assertEqual(install.detect_transport('en7'), 'wired')
            with self.assertRaises(install.Abort):
                install.detect_transport('en99')

    def test_arp_is_scoped_and_padded(self):
        text = ('? (192.168.1.1) at aa:bb:cc:dd:ee:ff on en7 ifscope\n'
                '? (192.168.1.1) at 0:1:2:3:4:5 on en0 ifscope\n')
        with patch.object(macnet, 'query', return_value=text):
            self.assertEqual(macnet.neighbor_mac('192.168.1.1', 'en0'), '00:01:02:03:04:05')
            self.assertIsNone(macnet.neighbor_mac('192.168.1.1', 'en8'))

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


if __name__ == '__main__':
    unittest.main()
